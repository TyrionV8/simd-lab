"""Verifica representação, ordem numérica, validação de grafos e execução nativa."""
import copy
import math
import random
import struct
import unittest

from engine import analyze, clean_graph, f32, fma, pack
from presets import PRESETS, make_preset
from server import CPU, run_native


class CircuitTests(unittest.TestCase):
    def test_all_presets_produce_expected_integer_example(self):
        for key,*_ in PRESETS:
            with self.subTest(key=key):
                report=analyze(make_preset(key),CPU)
                self.assertTrue(report["valid"],report["message"])
                self.assertTrue(report["passed"],(report["actual"],report["reference"]))
                self.assertIn("int main(int argc",report["code"])

    def test_signed_and_planar_weights_reconstruct_same_integers(self):
        graph=make_preset("main"); report=analyze(graph,CPU)
        node=next(n for n in graph["nodes"] if n["op"]=="sign4")
        self.assertEqual(report["nodes"][node["id"]]["outputs"]["value"]["values"],graph["inputs"]["weights"])
        graph=make_preset("avx_int4"); report=analyze(graph,CPU)
        node=next(n for n in graph["nodes"] if n["op"]=="center")
        self.assertEqual(report["nodes"][node["id"]]["outputs"]["value"]["values"],graph["inputs"]["weights"])
        self.assertNotEqual(pack(graph["inputs"]["weights"],"pairs"),pack(graph["inputs"]["weights"],"planar"))

    def test_ssse3_preserves_dev_i8_bits_with_fractional_inputs(self):
        rng=random.Random(2696)
        for _ in range(30):
            a=make_preset("dev_i8");b=make_preset("ssse3_exact")
            a["inputs"]["weights"]=[rng.randrange(-8,8) for _ in range(64)]
            a["inputs"]["activations"]=[f32(rng.uniform(-100,100)) for _ in range(64)]
            a["inputs"]["weightScale"]=f32(rng.uniform(.0001,.4)); b["inputs"]=copy.deepcopy(a["inputs"])
            ar=analyze(a,CPU);br=analyze(b,CPU)
            self.assertEqual(struct.pack("<f",ar["actual"]),struct.pack("<f",br["actual"]))

    def test_partial_products_and_origins(self):
        graph=make_preset("ssse3_exact"); report=analyze(graph,CPU)
        byop={n["op"]:report["nodes"][n["id"]]["outputs"]["value"] for n in graph["nodes"]}
        self.assertEqual(byop["maddubs"]["count"],32)
        self.assertEqual(byop["maddubs"]["bytes"],64)
        self.assertEqual(byop["maddwd"]["count"],16)
        self.assertEqual(byop["merge"]["origins"][0],[0,1,2,3,32,33,34,35])

    def test_quantization_difference_is_visible(self):
        graph=make_preset("dev_i8")
        graph["inputs"]["activations"]=[f32(math.sin(i*.73)*13.71) for i in range(64)]
        graph["inputs"].update(atol=0,rtol=0)
        report=analyze(graph,CPU)
        self.assertTrue(report["valid"])
        self.assertGreater(report["delta"],0)
        self.assertFalse(report["passed"])

    def test_quantization_scale_underflow_is_reported(self):
        graph=make_preset("dev_i8")
        graph["inputs"]["activations"]=[1e-45]*64
        report=analyze(graph,CPU)
        self.assertFalse(report["valid"])
        self.assertIn("escala de quantização",report["message"])

    def test_fma_is_not_approximated_by_two_roundings(self):
        a=f32(1+2**-23); b=f32(1-2**-23)
        self.assertEqual(f32(f32(a*b)-1),0)
        self.assertEqual(fma(a,b,-1),-2**-46)

    def test_type_mismatch_and_cycle_are_rejected(self):
        graph=make_preset("main")
        cast=next(n for n in graph["nodes"] if n["op"]=="float")
        activation=next(n for n in graph["nodes"] if n["op"]=="activations")
        edge=next(e for e in graph["edges"] if e["to"]==cast["id"])
        edge["from"]=activation["id"]
        report=analyze(graph,CPU)
        self.assertFalse(report["valid"])
        self.assertIn("recebido f32",report["nodes"][cast["id"]]["error"])
        graph=make_preset("main")
        final=next(n for n in graph["nodes"] if n["op"]=="output")
        edge=next(e for e in graph["edges"] if e["to"]==final["id"])
        edge["from"]=final["id"]
        self.assertFalse(analyze(graph,CPU)["valid"])

    def test_missing_input_does_not_generate_runnable_code(self):
        graph=make_preset("main");graph["edges"].pop()
        report=analyze(graph,CPU)
        self.assertFalse(report["valid"])
        self.assertNotIn("int main(int argc",report["code"])

    def test_invalid_identifiers_parameters_and_numbers(self):
        for edit in (lambda g:g["nodes"][0].update(id='x;system("bad")'),
                     lambda g:g["inputs"]["weights"].__setitem__(0,8),
                     lambda g:g["inputs"]["activations"].__setitem__(0,float("nan"))):
            graph=make_preset("main"); edit(graph)
            with self.assertRaises(ValueError): clean_graph(graph)

    def test_unsupported_instruction_is_simulated_but_not_executed(self):
        graph=make_preset("dev_avx2_f32")
        report=analyze(graph,{"flags":[]})
        self.assertTrue(report["valid"])
        self.assertIn("avx2",report["missing"])
        self.assertIn("fma",report["missing"])
        if "avx2" not in CPU["flags"]:
            with self.assertRaisesRegex(ValueError,"Execução bloqueada"):run_native(graph)

    @unittest.skipUnless(CPU["compiler"],"gcc não disponível")
    def test_native_preserves_negative_zero_in_trace(self):
        graph=make_preset("main")
        graph["inputs"]["weights"]=[0]*64
        graph["inputs"]["activations"]=[-1.0]*64
        native=run_native(graph)
        mul=next(n for n in graph["nodes"] if n["op"]=="mul")
        self.assertEqual(struct.pack("<f",native["values"][mul["id"]+".value"][0]),struct.pack("<f",-0.0))
        self.assertTrue(native["matched"],native["differences"])

    @unittest.skipUnless(CPU["compiler"],"gcc não disponível")
    def test_native_all_supported_paths_every_intermediate(self):
        rng=random.Random(86)
        for key,*_ in PRESETS:
            with self.subTest(key=key):
                graph=make_preset(key)
                graph["inputs"]["weights"]=[rng.randrange(-8,8) for _ in range(64)]
                graph["inputs"]["activations"]=[f32(rng.uniform(-30,30)) for _ in range(64)]
                graph["inputs"]["weightScale"]=.01875
                if analyze(graph,CPU)["missing"]:continue
                native=run_native(graph)
                self.assertTrue(native["matched"],native["differences"])


if __name__=="__main__": unittest.main(verbosity=2)
