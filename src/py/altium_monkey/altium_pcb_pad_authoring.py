"""Shared PCB pad authoring helpers."""

from __future__ import annotations

from .altium_pcb_enums import PadShape
from .altium_record_pcb__pad import AltiumPcbPad

ROUNDED_RECTANGLE_ALT_SHAPE = 9
ROUNDED_RECTANGLE_FULL_STACK_LAYER_CODE = 4
ROUNDED_RECTANGLE_FULL_STACK_MODE_FLAGS = 0x0180
ROUNDED_RECTANGLE_FULL_STACK_ENABLED = 9
DEFAULT_ROUNDED_RECTANGLE_CORNER_RADIUS_PERCENT = 50
SLOT_HOLE_SHAPE = 2


def validate_non_negative(value: float, name: str) -> None:
    if float(value) < 0.0:
        raise ValueError(f"{name} must be non-negative")


def apply_authored_pad_shape(
    pad: AltiumPcbPad,
    *,
    shape: int | PadShape,
    width_iu: int,
    height_iu: int,
    corner_radius_percent: int | None,
) -> None:
    """
    Apply public semantic pad shape to native fields.

    Altium does not persist authored rounded rectangles as base shape `4`.
    It uses a round base shape plus SubRecord 6 alternate-shape and radius
    data. Keeping that encoding here prevents public API callers from writing
    files that reopen with invalid pad-shape state.
    """
    shape_id = int(shape)
    if shape_id != int(PadShape.ROUNDED_RECTANGLE):
        pad.shape = shape_id
        pad.top_shape = shape_id
        pad.mid_shape = shape_id
        pad.bot_shape = shape_id
        return

    corner_pct = _normalize_corner_radius_percent(corner_radius_percent)
    base_shape = int(PadShape.CIRCLE)
    pad.shape = base_shape
    pad.top_shape = base_shape
    pad.mid_shape = base_shape
    pad.bot_shape = base_shape
    pad.inner_size_x = [width_iu] * 29
    pad.inner_size_y = [height_iu] * 29
    pad.inner_shape = [base_shape] * 29
    pad.alt_shape = [ROUNDED_RECTANGLE_ALT_SHAPE] * 32
    pad.corner_radius = [corner_pct] * 32
    pad.full_stack_layer_entries = [
        (
            ROUNDED_RECTANGLE_FULL_STACK_LAYER_CODE,
            ROUNDED_RECTANGLE_FULL_STACK_MODE_FLAGS,
            ROUNDED_RECTANGLE_FULL_STACK_ENABLED,
            width_iu,
            height_iu,
            corner_pct,
        )
    ]


def _normalize_corner_radius_percent(value: int | None) -> int:
    percent = (
        DEFAULT_ROUNDED_RECTANGLE_CORNER_RADIUS_PERCENT if value is None else int(value)
    )
    if not 0 <= percent <= 100:
        raise ValueError("corner_radius_percent must be between 0 and 100")
    return percent
