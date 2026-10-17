"""Primitivas de arredondamento explícito e representação IEEE 754."""

import ctypes
from fractions import Fraction
import struct

_libm = ctypes.CDLL("libm.so.6")
_libm.fmaf.argtypes = [ctypes.c_float] * 3
_libm.fmaf.restype = ctypes.c_float


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def fma(a, b, c=0.0):
    return float(_libm.fmaf(a, b, c))


def float_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def ulp_distance(a, b):
    """Distância entre FP32 adjacentes; +0 e -0 têm distância numérica zero."""
    if a == b:
        return 0

    def ordered(value):
        bits = float_bits(value)
        return 0x80000000 - (bits & 0x7FFFFFFF) if bits >> 31 else 0x80000000 + bits

    return abs(ordered(a) - ordered(b))


def round_fraction_f32(value):
    """Arredonda uma fração exata diretamente para FP32, com empate para par."""
    value = Fraction(value)
    sign = 0x80000000 if value < 0 else 0
    numerator, denominator = abs(value.numerator), value.denominator
    if not numerator:
        return 0.0
    exponent = numerator.bit_length() - denominator.bit_length()
    if exponent >= 0:
        exponent -= numerator < denominator << exponent
    else:
        exponent -= numerator << -exponent < denominator

    shift = 149 if exponent < -126 else 23 - exponent
    if shift >= 0:
        numerator <<= shift
    else:
        denominator <<= -shift
    rounded, remainder = divmod(numerator, denominator)
    if 2 * remainder > denominator or (2 * remainder == denominator and rounded & 1):
        rounded += 1
    if exponent < -126:
        bits = sign | rounded
    else:
        if rounded == 1 << 24:
            rounded >>= 1
            exponent += 1
        if exponent > 127:
            raise ValueError("O gabarito excedeu a faixa de FP32.")
        bits = sign | ((exponent + 127) << 23) | (rounded - (1 << 23))
    return struct.unpack("<f", struct.pack("<I", bits))[0]
