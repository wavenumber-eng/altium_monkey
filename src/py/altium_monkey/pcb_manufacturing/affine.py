"""Deterministic decimal-fixed affine operations."""

from __future__ import annotations

import math

from .generated import PcbDecimalAffine2d, Point2d
from .units import _round_ratio_ties_even

AFFINE_COEFFICIENT_SCALE = 10**15
_MIN_INT64 = -(1 << 63)
_MAX_INT64 = (1 << 63) - 1
_MAX_UINT32 = (1 << 32) - 1


def identity_affine() -> PcbDecimalAffine2d:
    """Return the exact a0 identity transform."""

    return PcbDecimalAffine2d(
        type="pcb.manufacturing.affine2d.decimal_e15",
        a_e15=AFFINE_COEFFICIENT_SCALE,
        b_e15=0,
        c_e15=0,
        d_e15=AFFINE_COEFFICIENT_SCALE,
        tx_nm=0,
        ty_nm=0,
        composition_depth=0,
    )


def rotation_affine_degrees(angle_degrees: float) -> PcbDecimalAffine2d:
    """Return a deterministic local rotation in the decimal-e15 contract."""

    if not math.isfinite(angle_degrees):
        raise ValueError("manufacturing rotation angle must be finite")
    radians = math.radians(angle_degrees % 360.0)
    cosine = round(math.cos(radians) * AFFINE_COEFFICIENT_SCALE)
    sine = round(math.sin(radians) * AFFINE_COEFFICIENT_SCALE)
    return PcbDecimalAffine2d(
        type="pcb.manufacturing.affine2d.decimal_e15",
        a_e15=cosine,
        b_e15=sine,
        c_e15=-sine,
        d_e15=cosine,
        tx_nm=0,
        ty_nm=0,
        composition_depth=0,
    )


def compose_affines(
    parent: PcbDecimalAffine2d,
    child: PcbDecimalAffine2d,
) -> PcbDecimalAffine2d:
    """Compose `parent * child` with exact widened integer intermediates."""

    depths = (parent.composition_depth, child.composition_depth)
    if any(not 0 <= depth <= _MAX_UINT32 for depth in depths):
        raise ValueError("manufacturing affine composition depth is outside uint32")
    composition_depth = parent.composition_depth + child.composition_depth + 1
    if composition_depth > _MAX_UINT32:
        raise OverflowError("composed manufacturing affine depth exceeds uint32")
    scale = AFFINE_COEFFICIENT_SCALE
    a_e15 = _round_ratio_ties_even(
        parent.a_e15 * child.a_e15 + parent.c_e15 * child.b_e15,
        scale,
    )
    b_e15 = _round_ratio_ties_even(
        parent.b_e15 * child.a_e15 + parent.d_e15 * child.b_e15,
        scale,
    )
    c_e15 = _round_ratio_ties_even(
        parent.a_e15 * child.c_e15 + parent.c_e15 * child.d_e15,
        scale,
    )
    d_e15 = _round_ratio_ties_even(
        parent.b_e15 * child.c_e15 + parent.d_e15 * child.d_e15,
        scale,
    )
    tx_nm = (
        _round_ratio_ties_even(
            parent.a_e15 * child.tx_nm + parent.c_e15 * child.ty_nm,
            scale,
        )
        + parent.tx_nm
    )
    ty_nm = (
        _round_ratio_ties_even(
            parent.b_e15 * child.tx_nm + parent.d_e15 * child.ty_nm,
            scale,
        )
        + parent.ty_nm
    )
    values = (a_e15, b_e15, c_e15, d_e15, tx_nm, ty_nm)
    if any(not _MIN_INT64 <= value <= _MAX_INT64 for value in values):
        raise OverflowError("composed manufacturing affine exceeds signed int64")
    return PcbDecimalAffine2d(
        type="pcb.manufacturing.affine2d.decimal_e15",
        a_e15=a_e15,
        b_e15=b_e15,
        c_e15=c_e15,
        d_e15=d_e15,
        tx_nm=tx_nm,
        ty_nm=ty_nm,
        composition_depth=composition_depth,
    )


def apply_affine(affine: PcbDecimalAffine2d, point: Point2d) -> Point2d:
    """Apply an affine to one integer-nanometer point with ties-to-even rounding."""

    scale = AFFINE_COEFFICIENT_SCALE
    x_nm = (
        _round_ratio_ties_even(
            affine.a_e15 * point.x_nm + affine.c_e15 * point.y_nm,
            scale,
        )
        + affine.tx_nm
    )
    y_nm = (
        _round_ratio_ties_even(
            affine.b_e15 * point.x_nm + affine.d_e15 * point.y_nm,
            scale,
        )
        + affine.ty_nm
    )
    if not _MIN_INT64 <= x_nm <= _MAX_INT64 or not _MIN_INT64 <= y_nm <= _MAX_INT64:
        raise OverflowError("transformed manufacturing point exceeds signed int64")
    return Point2d(x_nm=x_nm, y_nm=y_nm)
