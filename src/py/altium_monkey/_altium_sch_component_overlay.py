"""Bounded component overlay drawing calls in signed internal units."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from ._altium_record_sch__harness_layout import (
    _f32,
    _float_to_i32,
    _harness_geometry_point,
    _harness_drawing_color,
    _trunc_i32_div,
    _unchecked_i32,
    _utf16_code_unit_prefix,
)
from .altium_sch_geometry_oracle import SchGeometryOp, make_pen
from .altium_sch_paint_order import _ManagedPaintRectangle

if TYPE_CHECKING:
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_sch_svg_renderer import SchSvgRenderContext


_HATCH_STEP = 500_000


@dataclass(frozen=True, slots=True)
class _ComponentOverlayDrawCall:
    kind: Literal["line", "rectangle"]
    coordinates: tuple[int, int, int, int]
    pen: Literal["missing", "cross", "gray_box"]


def _overlay_line(
    x1: int, y1: int, x2: int, y2: int, pen: Literal["missing", "cross", "gray_box"]
) -> _ComponentOverlayDrawCall:
    return _ComponentOverlayDrawCall(
        "line",
        (
            _unchecked_i32(x1),
            _unchecked_i32(y1),
            _unchecked_i32(x2),
            _unchecked_i32(y2),
        ),
        pen,
    )


def _hatch_45(
    edges: tuple[int, int, int, int], pen: Literal["missing", "cross", "gray_box"]
) -> Iterator[_ComponentOverlayDrawCall]:
    left, top, right, bottom = edges
    x, y = _unchecked_i32(left + _HATCH_STEP), _unchecked_i32(top + _HATCH_STEP)
    while x < right and y < bottom:
        yield _overlay_line(x, top, left, y, pen)
        x, y = _unchecked_i32(x + _HATCH_STEP), _unchecked_i32(y + _HATCH_STEP)
    residual = _unchecked_i32(y - bottom)
    while x < right:
        yield _overlay_line(x, top, left + residual, bottom, pen)
        x, residual = (
            _unchecked_i32(x + _HATCH_STEP),
            _unchecked_i32(residual + _HATCH_STEP),
        )
    residual = _unchecked_i32(x - right)
    while y < bottom:
        yield _overlay_line(left, y, right, top + residual, pen)
        residual, y = (
            _unchecked_i32(residual + _HATCH_STEP),
            _unchecked_i32(y + _HATCH_STEP),
        )
    dx, dy = _unchecked_i32(x - right), _unchecked_i32(y - bottom)
    x, y = _unchecked_i32(x - _HATCH_STEP), _unchecked_i32(y - _HATCH_STEP)
    while x > left and y > top:
        yield _overlay_line(x - dx, bottom, right, y - dy, pen)
        x, y = _unchecked_i32(x - _HATCH_STEP), _unchecked_i32(y - _HATCH_STEP)


def _hatch_135(
    edges: tuple[int, int, int, int], pen: Literal["missing", "cross", "gray_box"]
) -> Iterator[_ComponentOverlayDrawCall]:
    left, top, right, bottom = edges
    x, y = _unchecked_i32(left + _HATCH_STEP), _unchecked_i32(bottom - _HATCH_STEP)
    while x < right and y > top:
        yield _overlay_line(x, bottom, left, y, pen)
        x, y = _unchecked_i32(x + _HATCH_STEP), _unchecked_i32(y - _HATCH_STEP)
    residual = _unchecked_i32(top - y)
    while x < right:
        yield _overlay_line(left + residual, top, x, bottom, pen)
        x, residual = (
            _unchecked_i32(x + _HATCH_STEP),
            _unchecked_i32(residual + _HATCH_STEP),
        )
    residual = _unchecked_i32(x - right)
    while y > top:
        yield _overlay_line(right, bottom - residual, left, y, pen)
        residual, y = (
            _unchecked_i32(residual + _HATCH_STEP),
            _unchecked_i32(y - _HATCH_STEP),
        )
    dx, dy = _unchecked_i32(x - right), _unchecked_i32(y - top)
    x, y = _unchecked_i32(x - _HATCH_STEP), _unchecked_i32(y + _HATCH_STEP)
    while x > left and y < bottom:
        yield _overlay_line(x - dx, top, right, y - dy, pen)
        x, y = _unchecked_i32(x - _HATCH_STEP), _unchecked_i32(y + _HATCH_STEP)


def _overlay_calls(
    edges: tuple[int, int, int, int],
    missing: bool,
    graphics: Literal["cross", "gray_box"] | None,
) -> Iterator[_ComponentOverlayDrawCall]:
    if missing:
        yield from _hatch_45(edges, "missing")
    if graphics is None:
        return
    left, top, right, bottom = edges
    if graphics == "cross":
        yield _overlay_line(left, top, right, bottom, "cross")
        yield _overlay_line(left, bottom, right, top, "cross")
        return
    yield _ComponentOverlayDrawCall("rectangle", edges, "gray_box")
    yield from _hatch_45(edges, "gray_box")
    yield from _hatch_135(edges, "gray_box")


def _component_overlay_draw_calls(
    bounds: _ManagedPaintRectangle,
    *,
    missing: bool = False,
    graphics: Literal["cross", "gray_box"] | None = None,
    max_calls: int = 100_000,
) -> tuple[_ComponentOverlayDrawCall, ...]:
    """Prepare ordered raw calls; callers still own pens, transforms and emission."""
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError("component overlay call limit must be a nonnegative integer")
    if graphics not in (None, "cross", "gray_box"):
        raise ValueError("unknown component overlay graphics")
    if any(
        type(value) is not int or value != _unchecked_i32(value)
        for value in (bounds.x, bounds.y, bounds.width, bounds.height)
    ):
        raise ValueError("component overlay rectangle must contain signed int32 values")
    edges = (
        bounds.x,
        bounds.y,
        _unchecked_i32(bounds.x + bounds.width),
        _unchecked_i32(bounds.y + bounds.height),
    )
    calls: list[_ComponentOverlayDrawCall] = []
    # Every managed loop iteration yields once. A shared cap bounds even wrapped
    # lattice cycles and both overlay phases; no partial calls escape on failure.
    for call in _overlay_calls(edges, missing, graphics):
        if len(calls) >= max_calls:
            raise ValueError("component overlay call limit exceeded")
        calls.append(call)
    return tuple(calls)


@dataclass(frozen=True, slots=True)
class _ComponentOverlayColors:
    """Already-resolved object/painter colors, not inferred editor settings."""

    missing: int
    cross: int
    gray_box: int


@dataclass(frozen=True, slots=True)
class _ComponentDrawingColorState:
    """Captured DrawObjectInfo flags, not inferred from persisted properties."""

    owner_document_present: bool
    _draw_disabled: bool
    _draw_dimmed: bool
    _draw_compilation_masked: bool
    _draw_editable_in_current_view: bool


def _component_drawing_color(
    ctx: SchSvgRenderContext, state: _ComponentDrawingColorState, color: int
) -> int:
    if not state.owner_document_present:
        return color
    return _harness_drawing_color(state, color, ctx)


def _component_overlay_palette(
    ctx: SchSvgRenderContext, state: _ComponentDrawingColorState
) -> tuple[_ComponentOverlayColors, int]:
    from .altium_sch_svg_renderer import modify_color

    # ColorManager.SheetArea's ARGB -459521 becomes Win32 BGR 0xFFFCF8.
    background = ctx.sheet_area_color if state.owner_document_present else 0xFFFCF8
    masked = modify_color(50, 0x808080, background)
    gray = _component_drawing_color(ctx, state, 0xA9A9A9)
    cross = _component_drawing_color(ctx, state, 0x0000FF)
    return _ComponentOverlayColors(gray, cross, gray), _component_drawing_color(
        ctx, state, masked
    )


@dataclass(frozen=True, slots=True)
class _ComponentOverlayText:
    """Prepared text, transformed font/brush and unzoomed internal-unit metrics.

    Font registration, measurement backend and object/monochrome color policy
    are resolved by the caller, not inferred from persisted component records.
    """

    text: str
    font: Mapping[str, object]
    brush: Mapping[str, object]
    measure: Callable[[str], tuple[float, float]]


@dataclass(frozen=True, slots=True)
class _ComponentOverlayCapture:
    """One component's prepared overlay branch, not an alternate-symbol loader."""

    source: AltiumSchComponent
    missing: bool
    variant_option_present: bool
    graphics: Literal["cross", "gray_box"] | None
    use_text: bool
    bounds: _ManagedPaintRectangle | None
    colors: _ComponentOverlayColors
    text: _ComponentOverlayText | None = None
    max_operations: int = 100_000
    max_text_characters: int = 1_000_000
    primitive_color: int | None = None


def _component_override_color(color: int | None) -> int:
    if type(color) is not int or not 0 <= color <= 0xFFFFFFFF:
        raise ValueError("component override color must be a uint32 integer")
    return color & 0xFFFFFF


def _component_primitive_context(
    ctx: SchSvgRenderContext,
    source: AltiumSchComponent,
    overlay: _ComponentOverlayCapture | None,
) -> SchSvgRenderContext:
    if source.override_colors:
        return ctx.with_color_overrides(
            area_color=_component_override_color(source.area_color),
            line_color=_component_override_color(source.color),
            pin_color=_component_override_color(source.pin_color),
        )
    if overlay is None or not overlay.variant_option_present:
        return ctx.with_color_overrides()
    color = overlay.primitive_color
    if color is None:
        raise NotImplementedError("variant primitives require a prepared masked color")
    if type(color) is not int or not 0 <= color <= 0xFFFFFF:
        raise ValueError("variant primitive color must be a 24-bit Win32 BGR integer")
    return ctx.with_color_overrides(area_color=color, line_color=color, pin_color=color)


def _component_paint_contexts(
    ctx: SchSvgRenderContext, source: AltiumSchComponent
) -> tuple[SchSvgRenderContext, SchSvgRenderContext]:
    overlay = ctx._component_overlay_capture
    if overlay is None and ctx._component_variant_capture is None:
        return ctx, ctx
    if overlay is not None and overlay.source is not source:
        raise ValueError("component overlay capture has a different source")
    # The component resets overrides before overlays and the outer junction
    # pass. Neither those phases nor nested components inherit its captures.
    outer = ctx.with_color_overrides()
    outer._component_overlay_capture = None
    outer._component_variant_capture = None
    return _component_primitive_context(outer, source, overlay), outer


def _component_overlay_group_operations(
    ctx: SchSvgRenderContext,
    capture: _ComponentOverlayCapture,
    *,
    units_per_px: int,
) -> list[SchGeometryOp]:
    _validate_overlay_emission(
        None, capture.max_operations, capture.max_text_characters
    )
    if not capture.missing and not capture.variant_option_present:
        return []
    if capture.max_operations < 2:
        raise ValueError("component overlay group operation limit exceeded")
    operations = _component_overlay_group_content(ctx, capture, units_per_px)
    return [
        SchGeometryOp.begin_group("VarComponentGroup"),
        *operations,
        SchGeometryOp.end_group(),
    ]


def _component_overlay_group_content(
    ctx: SchSvgRenderContext, capture: _ComponentOverlayCapture, units_per_px: int
) -> list[SchGeometryOp]:
    graphics = capture.graphics if capture.variant_option_present else None
    text = None
    if capture.variant_option_present and capture.use_text:
        if capture.text is None:
            raise NotImplementedError("component overlay text requires prepared state")
        text = capture.text if capture.text.text else None
    if not capture.missing and graphics is None and text is None:
        return []
    if capture.bounds is None:
        raise NotImplementedError(
            "component overlay requires prepared component bounds"
        )
    return _component_overlay_geometry_operations(
        ctx,
        capture.bounds,
        capture.colors,
        missing=capture.missing,
        graphics=graphics,
        text=text,
        units_per_px=units_per_px,
        max_operations=capture.max_operations - 2,
        max_text_characters=capture.max_text_characters,
    )


def _overlay_text_operation(
    ctx: SchSvgRenderContext,
    bounds: _ManagedPaintRectangle,
    prepared: _ComponentOverlayText,
    units_per_px: int,
) -> SchGeometryOp:
    measured_text = _utf16_code_unit_prefix(prepared.text, 8192)
    _, height = prepared.measure(measured_text)
    height_i32 = _float_to_i32(_f32(height), rounded=False)
    right = _unchecked_i32(bounds.x + bounds.width)
    bottom = _unchecked_i32(bounds.y + bounds.height)
    x = _trunc_i32_div(_unchecked_i32(bounds.x + right), 2)
    y = _trunc_i32_div(_unchecked_i32(bounds.y + bottom + height_i32), 2)
    width, _ = prepared.measure(measured_text)
    # MeasureString casts height to int; ApplyTextAlignment instead halves the
    # second RawVector2 width as a single before subtracting it from a double.
    half_width = _f32(_f32(width) / 2)
    aligned_x = x - half_width
    origin_x = aligned_x + half_width
    # ApplyTextAlignment installs even its zero-degree rotation. Do not fold
    # away the origin round-trip: infinite widths produce NaN, not infinity.
    aligned_x = origin_x + (aligned_x - origin_x)
    gx, gy = _harness_geometry_point(ctx, aligned_x, y, units_per_px=units_per_px)
    return SchGeometryOp.string(
        x=_f32(gx),
        y=_f32(gy),
        text=prepared.text,
        font=dict(prepared.font),
        brush=dict(prepared.brush),
    )


def _overlay_rectangle_operation(
    ctx: SchSvgRenderContext,
    coordinates: tuple[int, int, int, int],
    pen: dict[str, object],
    units_per_px: int,
) -> SchGeometryOp:
    from .altium_sch_geometry_oracle import _geometry_item_length

    x1, y1, x2, y2 = coordinates
    center = _harness_geometry_point(
        ctx, (x1 + x2) / 2, (y1 + y2) / 2, units_per_px=units_per_px
    )
    # RoundedRectangleGeometryItem transforms its center and zooms positive
    # half-extents independently, then stores and combines them as singles.
    half_width = _geometry_item_length(
        abs(x2 - x1) / 200_000 * ctx.scale,
        units_per_px=units_per_px,
    )
    half_height = _geometry_item_length(
        abs(y2 - y1) / 200_000 * ctx.scale,
        units_per_px=units_per_px,
    )
    x, y = (_f32(value) for value in center)
    return SchGeometryOp.rounded_rectangle_from_item(
        center_x=x,
        center_y=y,
        half_width=half_width,
        half_height=half_height,
        pen=pen,
    )


def _validate_overlay_emission(
    text: _ComponentOverlayText | None, max_operations: int, max_text_characters: int
) -> bool:
    if any(
        type(value) is not int or value < 0
        for value in (max_operations, max_text_characters)
    ):
        raise ValueError("component overlay limits must be nonnegative integers")
    if text is None or not text.text:
        return False
    if len(text.text) > max_text_characters // 2:
        raise ValueError("component overlay text character limit exceeded")
    if max_operations == 0:
        raise ValueError("component overlay call limit exceeded")
    return True


def _component_overlay_geometry_operations(
    ctx: SchSvgRenderContext,
    bounds: _ManagedPaintRectangle,
    colors: _ComponentOverlayColors,
    *,
    missing: bool = False,
    graphics: Literal["cross", "gray_box"] | None = None,
    units_per_px: int = 64,
    max_operations: int = 100_000,
    text: _ComponentOverlayText | None = None,
    max_text_characters: int = 1_000_000,
) -> list[SchGeometryOp]:
    """Emit bounded prepared graphics then text, without creating a group."""
    has_text = _validate_overlay_emission(text, max_operations, max_text_characters)
    calls = _component_overlay_draw_calls(
        bounds, missing=missing, graphics=graphics, max_calls=max_operations - has_text
    )
    operations: list[SchGeometryOp] = []
    from .altium_sch_geometry_oracle import _geometry_item_length

    for call in calls:
        width = (
            _geometry_item_length(3 * ctx.get_stroke_scale(), units_per_px=units_per_px)
            if call.pen == "cross"
            else 0
        )
        pen = make_pen(getattr(colors, call.pen), width=width)
        if call.kind == "rectangle":
            operations.append(
                _overlay_rectangle_operation(ctx, call.coordinates, pen, units_per_px)
            )
            continue
        x1, y1, x2, y2 = call.coordinates
        points = [
            tuple(
                _f32(value)
                for value in _harness_geometry_point(
                    ctx, x, y, units_per_px=units_per_px
                )
            )
            for x, y in ((x1, y1), (x2, y2))
        ]
        operations.append(SchGeometryOp.lines(points, pen=pen))
    if has_text and text is not None:
        operations.append(_overlay_text_operation(ctx, bounds, text, units_per_px))
    return operations
