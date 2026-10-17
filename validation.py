"""Gabaritos independentes das conexões e conjuntos de casos reproduzíveis."""

import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import math
import random

from numerics import f32, fma, float_bits, round_fraction_f32, ulp_distance

REFERENCES = {
    "mathematical": {
        "title": "Matemático · uma rodada FP32",
        "description": "Soma exata de peso × ativação × escala; arredonda somente a saída para FP32.",
    },
    "sequential": {
        "title": "FP32 sequencial",
        "description": "Cada produto e cada soma são arredondados para FP32, em ordem de índice.",
    },
    "main_sse": {
        "title": "main · oito vias SSE",
        "description": "Produtos separados, oito acumuladores e a redução do núcleo SSE histórico.",
    },
    "dev_f32": {
        "title": "dev · FP32 com FMA",
        "description": "Oito acumuladores com fmaf, escala por via e a árvore de redução da dev.",
    },
    "dev_i8": {
        "title": "dev · int8 por linha",
        "description": "Quantização por linha, oito somas inteiras e a correção de viés após a escala.",
    },
    "dev_a32": {
        "title": "dev · ativações por 32",
        "description": "Dois produtos inteiros assinados e a ordem de fmaf da referência A32.",
    },
}


def _tree8(lanes):
    a = [f32(lanes[i] + lanes[i + 4]) for i in range(4)]
    return f32(f32(a[0] + a[2]) + f32(a[1] + a[3]))


def _quantized(values, group):
    codes, scales = [], []
    for start in range(0, 64, group):
        block = values[start:start + group]
        maximum = max(map(abs, block))
        scale = f32(maximum / 127) if maximum else (1.0 if group == 64 else 0.0)
        if maximum and not scale:
            raise ValueError("A escala do gabarito sofreu subfluxo em FP32.")
        inverse = (f32(1 / scale) if group == 64 else f32(127 / maximum)) if maximum else 0.0
        codes.extend(max(-127, min(127, round(f32(v * inverse)))) for v in block)
        scales.append(scale)
    return codes, scales


def oracle(inputs):
    """A saída depende somente das entradas e da regra escolhida, nunca do grafo."""
    weights, x = inputs["weights"], inputs["activations"]
    scale = inputs["weightScale"]
    mode = inputs.get("referenceMode", "mathematical")
    if mode not in REFERENCES:
        raise ValueError("Gabarito desconhecido.")
    exact = sum((Fraction(w) * Fraction(v) for w, v in zip(weights, x)), Fraction()) * Fraction(scale)
    with localcontext() as context:
        context.prec = 500
        exact_decimal = format(Decimal(exact.numerator) / Decimal(exact.denominator), "f")
    mathematical = round_fraction_f32(exact)
    products = [f32(w * v) for w, v in zip(weights, x)]
    if mode == "mathematical":
        expected = mathematical
    elif mode == "sequential":
        total = 0.0
        for product in products:
            total = f32(total + product)
        expected = f32(total * scale)
    elif mode in ("main_sse", "dev_f32"):
        lanes = [0.0] * 8
        for i in range(64):
            lanes[i & 7] = fma(float(weights[i]), x[i], lanes[i & 7]) if mode == "dev_f32" else f32(lanes[i & 7] + products[i])
        expected = _tree8([fma(v, scale) for v in lanes]) if mode == "dev_f32" else fma(_tree8(lanes), scale)
    elif mode == "dev_i8":
        q, scales = _quantized(x, 64)
        partials = [sum((weights[k] + 8) * q[k] + (weights[k + 32] + 8) * q[k + 32] for k in range(4 * lane, 4 * lane + 4)) for lane in range(8)]
        lanes = [fma(float(value), scale) for value in partials]
        correction = fma(scale, float(sum(q)))
        expected = f32(f32(_tree8(lanes) - f32(8 * correction)) * scales[0])
    else:
        q, scales = _quantized(x, 32)
        p = [sum(weights[k] * q[k] for k in range(start, start + 32)) for start in (0, 32)]
        expected = fma(fma(float(p[1]), scales[1], f32(p[0] * scales[0])), scale)
    return {
        "mode": mode, **REFERENCES[mode], "expected": expected,
        "mathematical": mathematical, "exactDecimal": exact_decimal,
        "products": products, "expectedBits": f"{float_bits(expected):032b}",
    }


def compare(actual, expected, inputs):
    absolute = abs(actual - expected)
    tolerance = inputs["atol"] + inputs["rtol"] * abs(expected)
    bit_equal = float_bits(actual) == float_bits(expected)
    policy = inputs.get("validationPolicy", "tolerance")
    return {
        "actual": actual, "expected": expected, "absolute": absolute,
        "relative": absolute / abs(expected) if expected else None,
        "ulp": ulp_distance(actual, expected), "bitEqual": bit_equal,
        "tolerance": tolerance, "policy": policy,
        "passed": bit_equal if policy == "bits" else absolute <= tolerance,
    }


def test_cases(inputs, random_count=12, seed=2696):
    if type(random_count) is not int or not 0 <= random_count <= 32:
        raise ValueError("Use de 0 a 32 casos aleatórios.")
    if type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF:
        raise ValueError("A semente deve ser um inteiro de 0 a 4294967295.")
    cases = []

    def add(name, weights, activations, scale=1.0):
        case = copy.deepcopy(inputs)
        case.update(weights=weights, activations=list(map(f32, activations)), weightScale=f32(scale))
        cases.append({"name": name, "inputs": case})

    cases.append({"name": "Entrada atual", "inputs": copy.deepcopy(inputs)})
    add("Tudo zero", [0] * 64, [0.0] * 64)
    add("Pesos mínimos", [-8] * 64, [127.0] * 64)
    add("Pesos máximos", [7] * 64, [-127.0] * 64)
    add("Sinais alternados", [-8, 7] * 32, [127.0, -127.0] * 32)
    add("Cancelamento", [1, -1] * 32, [1000000.0] * 64)
    add("Entrada unitária", [(i % 16) - 8 for i in range(64)], [1.0] + [0.0] * 63)
    add("Frações e escala", [(i % 16) - 8 for i in range(64)], [math.sin(i * .73) * 13.71 for i in range(64)], .01875)
    add("Magnitudes diferentes", [-8, 7, -1, 1] * 16, [1e-4, -1000, 0.125, 127] * 16, .0625)
    rng = random.Random(seed)
    for i in range(random_count):
        add(f"Aleatório {i + 1}", [rng.randrange(-8, 8) for _ in range(64)], [rng.uniform(-100, 100) for _ in range(64)], rng.uniform(.001, .5))
    return cases
