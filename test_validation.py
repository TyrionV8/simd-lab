import copy
from fractions import Fraction
import random
import struct
import threading
import unittest

from engine import analyze
from inspection import functions_in, type_description
from numerics import f32, float_bits, round_fraction_f32, ulp_distance
from presets import make_preset
from runner import NativeRunner, check_options
from server import CPU
from validation import oracle, test_cases


class OracleTests(unittest.TestCase):
    def test_exact_rounding_ties_and_subnormal(self):
        self.assertEqual(round_fraction_f32(Fraction(1) + Fraction(1, 1 << 24)), 1.0)
        self.assertEqual(float_bits(round_fraction_f32(Fraction(1) + Fraction(3, 1 << 24))), 0x3F800002)
        self.assertEqual(float_bits(round_fraction_f32(Fraction(1, 1 << 150))), 0)
        self.assertEqual(float_bits(round_fraction_f32(-Fraction(1, 1 << 150))), 0x80000000)
        self.assertEqual(float_bits(round_fraction_f32(Fraction(3, 1 << 150))), 2)

    def test_exact_rounding_reconstructs_random_fp32(self):
        rng = random.Random(32)
        for _ in range(3000):
            bits = rng.randrange(0xFF000000)
            if (bits >> 23) & 255 == 255:
                continue
            value = struct.unpack('<f', struct.pack('<I', bits))[0]
            self.assertEqual(float_bits(round_fraction_f32(Fraction(value))), bits)

    def test_ulp_distance_handles_negative_and_zero(self):
        self.assertEqual(ulp_distance(-0.0, 0.0), 0)
        self.assertEqual(ulp_distance(1.0, struct.unpack('<f', struct.pack('<I', 0x3F800001))[0]), 1)
        self.assertEqual(ulp_distance(-1.0, struct.unpack('<f', struct.pack('<I', 0xBF800001))[0]), 1)

    def test_each_contract_matches_its_independent_reference(self):
        rng = random.Random(21)
        for preset, mode in [('main', 'main_sse'), ('dev_f32', 'dev_f32'), ('dev_i8', 'dev_i8'), ('dev_a32', 'dev_a32'), ('ssse3_exact', 'dev_i8')]:
            for _ in range(10):
                graph = make_preset(preset)
                graph['inputs'].update(weights=[rng.randrange(-8, 8) for _ in range(64)], activations=[f32(rng.uniform(-30, 30)) for _ in range(64)], weightScale=f32(.01875), referenceMode=mode, validationPolicy='bits')
                result = analyze(graph, CPU, include_code=False)
                self.assertTrue(result['passed'], (preset, result['comparison']))

    def test_wrong_graph_cannot_change_the_oracle(self):
        graph = make_preset('main')
        expected = oracle(analyze(graph, CPU, include_code=False)['graph']['inputs'])['expected']
        output = next(n for n in graph['nodes'] if n['op'] == 'output')
        graph['nodes'].append(dict(id='wrong', op='constant', params={'value': 0}, x=0, y=0))
        graph['edges'] = [e for e in graph['edges'] if e['to'] != output['id']]
        graph['edges'].append(dict(**{'from': 'wrong'}, out='value', to=output['id'], input='value'))
        result = analyze(graph, CPU, include_code=False)
        self.assertEqual(result['reference'], expected)
        self.assertFalse(result['passed'])

    def test_cases_are_reproducible_and_do_not_mutate_inputs(self):
        inputs = make_preset('main')['inputs']
        before = copy.deepcopy(inputs)
        self.assertEqual(test_cases(inputs, 3, 4), test_cases(inputs, 3, 4))
        self.assertNotEqual(test_cases(inputs, 3, 4), test_cases(inputs, 3, 5))
        self.assertEqual(len(test_cases(inputs, 3, 4)), 12)
        self.assertEqual(inputs, before)

    def test_clock_model_respects_dependencies_and_frequency(self):
        graph = make_preset('ssse3_exact')
        graph['simulation'] = {'clockGHz': 2, 'cyclesPerBatch': 3}
        result = analyze(graph, CPU)
        times = {n['id']: n for n in result['clock']['timeline']}
        for edge in graph['edges']:
            if edge['to'] in times and edge['from'] in times:
                self.assertGreaterEqual(times[edge['to']]['start'], times[edge['from']]['finish'])
        self.assertEqual(result['clock']['criticalNs'], result['clock']['criticalCycles'] / 2)

    def test_c_functions_and_per_node_code_are_inspectable(self):
        result = analyze(make_preset('ssse3_exact'), CPU)
        self.assertIn('circuit', result['functions'])
        self.assertIn('maddubs_ssse3', result['functions'])
        node = next(n for n in result['graph']['nodes'] if n['op'] == 'maddubs')
        implementation = result['nodes'][node['id']]['implementation']
        self.assertIn('maddubs_ssse3', implementation['snippet'])
        self.assertIn('_mm_maddubs_epi16', implementation['functions']['maddubs_ssse3'])
        self.assertEqual(type_description('f32', 4)['generic'], '4 números de 32 bits · ponto flutuante')

    def test_invalid_native_options_are_rejected(self):
        for value in [{'samples': 0}, {'core': 100000}, {'repetitions': 0}, {'suite': 'yes'}]:
            with self.assertRaises(ValueError):
                check_options(value, CPU)

    def test_tolerances_are_not_quantized_to_fp32(self):
        graph = make_preset('main')
        graph['inputs'].update(atol=1e-5, rtol=1e-5)
        normalized = analyze(graph, CPU, include_code=False)['graph']['inputs']
        self.assertEqual(normalized['atol'], 1e-5)
        self.assertEqual(normalized['rtol'], 1e-5)


@unittest.skipUnless(CPU['compiler'], 'GCC indisponível')
class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = NativeRunner(CPU)

    @classmethod
    def tearDownClass(cls):
        cls.runner.close()

    def test_repeated_measurements_and_all_cases_execute_real_c(self):
        graph = make_preset('ssse3_exact')
        graph['inputs'].update(referenceMode='dev_i8', validationPolicy='bits')
        result = self.runner.run(graph, {'repetitions': 1000, 'samples': 3, 'warmup': 10, 'randomCount': 2})
        self.assertTrue(result['matched'])
        self.assertEqual(result['suite']['passed'], result['suite']['total'])
        self.assertTrue(all(case['nativeMatched'] for case in result['suite']['cases']))
        self.assertEqual(len(result['benchmark']['samples']), 3)
        self.assertGreater(result['benchmark']['medianNs'], 0)
        self.assertIn('pmaddubsw', result['assembly'])
        if result['benchmark']['perfErrno']:
            self.assertIsNone(result['benchmark']['coreCyclesPerBlock'])

    def test_compile_cache_and_cancel_are_real(self):
        graph = make_preset('ssse3_exact')
        first = self.runner.run(graph, {'benchmark': False, 'suite': False})
        second = self.runner.run(graph, {'benchmark': False, 'suite': False})
        self.assertTrue(second['cached'])
        self.assertTrue(first['matched'])
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            self.runner.run(make_preset('main'), {'benchmark': False, 'suite': False}, cancel=cancelled)


if __name__ == '__main__':
    unittest.main(verbosity=2)
