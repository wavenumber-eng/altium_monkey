"""Managed integer and GDI+ numeric staging helpers."""

from __future__ import annotations

import ctypes
import math
import struct
import sys
from collections.abc import Callable
from functools import cache
from typing import cast

_DEGREES_TO_RADIANS = struct.unpack("<f", struct.pack("<I", 0x3C8EFA35))[0]
_MCW_RC = 0x300
_RC_DOWN = 0x100
type _UnaryFloatFunction = Callable[[float], float]
type _ControlFloatFunction = Callable[[object, int, int], int]


def managed_f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def unchecked_i32(value: int) -> int:
    return ((value + (1 << 31)) % (1 << 32)) - (1 << 31)


def split_coord_toward_zero(value: int) -> tuple[int, int]:
    """Split a 1/100000 coordinate without flooring negative values."""
    whole = abs(value) // 100_000
    if value < 0:
        whole = -whole
    return whole, value - whole * 100_000


def managed_internal_coord(whole: int, fraction: int) -> int:
    """Rebuild a split coordinate with unchecked managed Int32 arithmetic."""
    return unchecked_i32(whole * 100_000 + fraction)


def managed_gdi_rotate_translate(
    point: tuple[int, int],
    origin: tuple[int, int],
    angle_degrees: float,
) -> tuple[int, int]:
    """Mirror GDI+ Matrix float staging for Altium endpoint markers."""
    angle = managed_f32(angle_degrees)
    radians = _down_multiply(angle, _DEGREES_TO_RADIANS)
    sine, cosine = _downward_sin_cos(radians)
    local_x = _down_f32(float(point[0]))
    local_y = _down_f32(float(point[1]))
    translation_x = managed_f32(float(origin[0]))
    translation_y = managed_f32(float(origin[1]))
    x = _down_add(
        _down_add(
            _down_multiply(local_x, cosine),
            _down_multiply(local_y, -sine),
        ),
        translation_x,
    )
    y = _down_add(
        _down_add(
            _down_multiply(local_x, sine),
            _down_multiply(local_y, cosine),
        ),
        translation_y,
    )
    return _gdi_integer(x), _gdi_integer(y)


def _downward_sin_cos(radians: float) -> tuple[float, float]:
    if sys.platform != "win32":
        return _down_f32(math.sin(radians)), _down_f32(math.cos(radians))

    sine_function, cosine_function, control_function = _windows_float_functions()
    saved = ctypes.c_uint()
    if control_function(ctypes.byref(saved), 0, 0) != 0:
        raise RuntimeError("failed to read the native floating-point control word")
    changed = ctypes.c_uint()
    if control_function(ctypes.byref(changed), _RC_DOWN, _MCW_RC) != 0:
        raise RuntimeError("failed to enter the GDI+ floating-point rounding mode")
    try:
        return sine_function(radians), cosine_function(radians)
    finally:
        if control_function(ctypes.byref(changed), saved.value, _MCW_RC) != 0:
            raise RuntimeError("failed to restore the floating-point control word")


@cache
def _windows_float_functions() -> tuple[
    _UnaryFloatFunction,
    _UnaryFloatFunction,
    _ControlFloatFunction,
]:
    unary_float_type = ctypes.CFUNCTYPE(ctypes.c_float, ctypes.c_float)
    control_float_type = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint),
        ctypes.c_uint,
        ctypes.c_uint,
    )
    runtime = ctypes.CDLL("ucrtbase.dll")
    return (
        cast(_UnaryFloatFunction, unary_float_type(("sinf", runtime))),
        cast(_UnaryFloatFunction, unary_float_type(("cosf", runtime))),
        cast(_ControlFloatFunction, control_float_type(("_controlfp_s", runtime))),
    )


def _down_multiply(first: float, second: float) -> float:
    return _down_f32(first * second)


def _down_add(first: float, second: float) -> float:
    return _down_f32(first + second)


def _down_f32(value: float) -> float:
    nearest = managed_f32(value)
    return _next_down_f32(nearest) if nearest > value else nearest


def _next_down_f32(value: float) -> float:
    if math.isnan(value) or value == -math.inf:
        return value
    if value == 0.0:
        return struct.unpack("<f", struct.pack("<I", 0x80000001))[0]
    bits = struct.unpack("<I", struct.pack("<f", value))[0]
    bits = bits - 1 if value > 0.0 else bits + 1
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _gdi_integer(value: float) -> int:
    rounded = math.floor(value + 0.5)
    if rounded < -(1 << 31) or rounded > (1 << 31) - 1:
        return -(1 << 31)
    return rounded
