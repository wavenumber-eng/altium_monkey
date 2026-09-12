"""Managed own-bounds adapters for component painter inputs (internal units)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessBundle,
    AltiumSchHarnessSplice,
    _BUS_LINE_WIDTH_INTERNAL,
    _SYMBOL_LINE_WIDTH_INTERNAL,
    _float_to_i32,
    _harness_internal_location,
    _harness_splice_bounds,
    _round_away_from_zero,
    _trunc_i32_div,
    _unchecked_i32,
    _utf16_code_unit_prefix,
)
from ._altium_record_sch__physical_model import (
    _AltiumSchHarnessCavity,
    _AltiumSchHarnessCavityComponent,
    _AltiumSchLineView,
)
from .altium_record_sch__arc import AltiumSchArc
from .altium_record_sch__bezier import AltiumSchBezier
from .altium_record_sch__blanket import AltiumSchBlanket
from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__bus_entry import AltiumSchBusEntry
from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__compile_mask import AltiumSchCompileMask
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc
from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
from .altium_record_sch__line import AltiumSchLine
from .altium_record_sch__junction import AltiumSchJunction
from .altium_record_sch__image import AltiumSchImage
from .altium_record_sch__ieee_symbol import AltiumSchIeeeSymbol, _IEEE_SYMBOL_SHAPES
from .altium_record_sch__note import AltiumSchNote
from .altium_record_sch__no_erc import AltiumSchNoErc, NoErcSymbol
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__piechart import AltiumSchPieChart
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__polyline import AltiumSchPolyline
from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle
from .altium_record_sch__signal_harness import AltiumSchSignalHarness
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol, _repeat_name_int32
from .altium_record_sch__wire import AltiumSchWire
from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_types import CoordPoint, LineShape, LineWidth
from .altium_sch_enums import IeeeSymbol, ParameterSetStyle, Rotation90
from .altium_sch_geometry_oracle import SchGeometryBounds

if TYPE_CHECKING:
    from .altium_sch_svg_renderer import SchSvgRenderContext


def _portable_string_bounds(
    ctx: SchSvgRenderContext,
    location: tuple[int, int],
    text: str,
    font_id: int,
) -> SchGeometryBounds:
    """Use the Python portable metric oracle in unzoomed internal coordinates."""
    from .altium_text_metrics import measure_gdi_typographic_bounds

    name, _, bold, italic, _ = ctx.get_font_info(font_id)
    width, height = measure_gdi_typographic_bounds(
        _utf16_code_unit_prefix(text, 8192),
        ctx.get_font_size_for_width(font_id),
        name,
        bold=bold,
        italic=italic,
    )
    x, y = location
    return _bounds(
        x,
        y,
        x + _float_to_i32(width * 100_000, rounded=True),
        y + _float_to_i32(height * 100_000, rounded=True),
    )


def _bounds(left: int, bottom: int, right: int, top: int) -> SchGeometryBounds:
    return SchGeometryBounds(
        left=_unchecked_i32(left),
        bottom=_unchecked_i32(bottom),
        right=_unchecked_i32(right),
        top=_unchecked_i32(top),
    )


def _corner_bounds(first: CoordPoint, second: CoordPoint) -> SchGeometryBounds:
    x1, y1 = _harness_internal_location(first)
    x2, y2 = _harness_internal_location(second)
    return _bounds(x1, y1, x2, y2)


def _inflate(bounds: SchGeometryBounds, amount: int) -> SchGeometryBounds:
    return _bounds(
        bounds.left - amount,
        bounds.bottom - amount,
        bounds.right + amount,
        bounds.top + amount,
    )


def _rectangle_bounds(
    record: AltiumSchRectangle
    | AltiumSchRoundedRectangle
    | AltiumSchImage
    | AltiumSchTextFrame,
) -> SchGeometryBounds:
    bounds = _corner_bounds(record.location, record.corner)
    collapsed = False
    if isinstance(record, AltiumSchNote):
        collapsed = record.collapsed
    elif isinstance(record, AltiumSchCompileMask):
        collapsed = record.is_collapsed
    if not collapsed:
        return bounds
    left, top = min(bounds.left, bounds.right), max(bounds.bottom, bounds.top)
    return _bounds(left, top - 1_000_000, left + 1_000_000, top)


def _sheet_symbol_bounds(record: AltiumSchSheetSymbol) -> SchGeometryBounds:
    x, y = _harness_internal_location(record.location)
    width = _unchecked_i32(record.x_size * 100_000 + record.x_size_frac)
    height = _unchecked_i32(record.y_size * 100_000 + record.y_size_frac)
    offset = 400_000 if record.is_multichannel() else 0
    return _bounds(x, y - height - offset, x + width + offset, y)


def _basic_entry_measurement_text(
    text: str,
    *,
    prefix_style: bool,
    override_display_string: str | None = None,
) -> str:
    """Prepare already-budgeted entry text for managed bounds measurement."""
    display = override_display_string or text
    if prefix_style:
        display = _basic_entry_bus_prefix(display)
    # Overbars are removed after numeric bus recognition, never before it.
    return display.replace("\\", "")


def _basic_entry_bus_prefix(text: str) -> str:
    opening, separator, closing = text.find("["), text.find(".."), text.find("]")
    if opening < 0 or not opening < separator < closing:
        return text
    if opening == 0:
        return ""
    first = _repeat_name_int32(text[opening + 1 : separator])
    last = _repeat_name_int32(text[separator + 2 : closing])
    if first is None or last is None or first < 0 or last < 0:
        return ""
    return text[:opening]


def _basic_entry_location_from_owner(
    owner_location: tuple[int, int],
    owner_size: tuple[int, int],
    distance: int,
    *,
    side: int,
    vertical: bool,
) -> tuple[int, int]:
    """Project a prepared direct rectangular owner's entry location locally."""
    x, y = owner_location
    width, height = owner_size
    if vertical:
        return (
            _unchecked_i32(x + distance),
            y if side == 2 else _unchecked_i32(y - height),
        )
    return (
        x if side == 0 else _unchecked_i32(x + width),
        _unchecked_i32(y - distance),
    )


def _basic_entry_bounds_from_size(
    location: tuple[int, int],
    measured_size: tuple[int, int],
    *,
    symbol_width: int,
    side: int,
    vertical: bool,
) -> SchGeometryBounds:
    """Use prepared engine location and unrotated internal-unit text metrics."""
    x, y = location
    width, height = measured_size
    text_width = _unchecked_i32(width + 500_000)
    half_height = _trunc_i32_div(max(800_001, height), 2)
    extent = _unchecked_i32(symbol_width + text_width)
    if vertical:
        if side == 2:
            return _bounds(x - half_height, y - extent, x + half_height, y)
        return _bounds(x - half_height, y, x + half_height, y + extent)
    # Horizontal entries intentionally have inverted vertical edges.
    if side == 0:
        return _bounds(x, y + half_height, x + extent, y - half_height)
    return _bounds(x - extent, y + half_height, x, y - half_height)


def _parameter_set_symbol_bounds(
    location: tuple[int, int], orientation: int, *, differential_pair: bool, style: int
) -> SchGeometryBounds:
    x, y = location
    if not 0 <= orientation <= 3:
        return _bounds(0, 0, 0, 0)
    if differential_pair:
        offsets = (
            (0, 0, 2_250_000, 1_000_000),
            (-1_000_000, 0, 0, 2_150_000),
            (-2_150_000, -1_100_000, 0, 0),
            (0, -2_250_000, 1_100_000, 0),
        )
    else:
        half, length = (
            (600_000, 2_400_000)
            if style == ParameterSetStyle.LARGE
            else (200_000, 800_000)
        )
        offsets = (
            (0, -half, length, half),
            (-half, 0, half, length),
            (-length, -half, 0, half),
            (-half, -length, half, 0),
        )
    left, bottom, right, top = offsets[orientation]
    return _bounds(x + left, y + bottom, x + right, y + top)


def _parameter_set_text_location(
    location: tuple[int, int], orientation: int, initial: SchGeometryBounds
) -> tuple[int, int]:
    x, y = location
    width = _unchecked_i32(initial.right - initial.left)
    height = _unchecked_i32(initial.top - initial.bottom)
    positions = (
        (x + 2_000_000, y - _trunc_i32_div(height, 2)),
        (x - _trunc_i32_div(width, 2), y + 2_200_000),
        (x - 2_000_000 - width, y - _trunc_i32_div(height, 2)),
        (x - _trunc_i32_div(width, 2), y - 1_800_000 - height),
    )
    if not 0 <= orientation <= 3:
        return (0, 0)
    px, py = positions[orientation]
    return _unchecked_i32(px), _unchecked_i32(py)


def _parameter_set_bounds_from_state(
    location: tuple[int, int],
    orientation: int,
    style: int,
    *,
    has_effective_document: bool,
    differential_pair: bool,
    display_string: str = "",
    measure: Callable[[tuple[int, int], str, int], SchGeometryBounds] | None = None,
    max_text_characters: int = 1_000_000,
) -> SchGeometryBounds:
    """Use captured effective-owner/classifier state and a prepared text provider.

    The owner flag must include managed original-owner fallback. The provider
    measures the same already-resolved display string at both requested origins;
    this adapter does not infer either input from the final ownership tree.
    """
    x, y = location
    if not has_effective_document:
        return _bounds(x - 5, y - 5, x + 5, y + 5)
    if not differential_pair and style not in (
        ParameterSetStyle.LARGE,
        ParameterSetStyle.TINY,
    ):
        return _bounds(0, 0, 0, 0)
    symbol = _parameter_set_symbol_bounds(
        location, orientation, differential_pair=differential_pair, style=style
    )
    if differential_pair or style == ParameterSetStyle.TINY:
        return symbol
    placed = _measure_parameter_set_text_bounds(
        location, orientation, display_string, measure, max_text_characters
    )
    if placed == symbol:
        return symbol
    # EnclosingRectangle normalizes its first operand, not both inputs. The
    # final normalization matters when symbol arithmetic wraps signed int32.
    left = min(placed.left, placed.right, symbol.left)
    bottom = min(placed.bottom, placed.top, symbol.bottom)
    right = max(placed.left, placed.right, symbol.right)
    top = max(placed.bottom, placed.top, symbol.top)
    return _bounds(
        min(left, right), min(bottom, top), max(left, right), max(bottom, top)
    )


def _measure_parameter_set_text_bounds(
    location: tuple[int, int],
    orientation: int,
    display_string: str,
    measure: Callable[[tuple[int, int], str, int], SchGeometryBounds] | None,
    max_text_characters: int,
) -> SchGeometryBounds:
    if type(max_text_characters) is not int or max_text_characters < 0:
        raise ValueError(
            "parameter-set text character limit must be a nonnegative integer"
        )
    if len(display_string) > max_text_characters // 2:
        raise ValueError("parameter-set text measurement character limit exceeded")
    if measure is None:
        raise NotImplementedError("parameter-set large bounds require a text provider")
    initial = measure(location, display_string, 1)
    return measure(
        _parameter_set_text_location(location, orientation, initial), display_string, 1
    )


def _line_bounds(record: AltiumSchLine) -> SchGeometryBounds:
    raw = _corner_bounds(record.location, record.corner)
    positive = _bounds(
        min(raw.left, raw.right),
        min(raw.bottom, raw.top),
        max(raw.left, raw.right),
        max(raw.bottom, raw.top),
    )
    return _inflate(positive, _SYMBOL_LINE_WIDTH_INTERNAL[record.line_width])


def _rotate_bounds_around_location(
    bounds: SchGeometryBounds, location: tuple[int, int], orientation: int
) -> SchGeometryBounds:
    x, y = location
    left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top
    match orientation:
        case Rotation90.DEG_90:
            return _bounds(
                x - (top - y), y + (left - x), x - (bottom - y), y + (right - x)
            )
        case Rotation90.DEG_180:
            return _bounds(
                x - (right - x), y - (top - y), x - (left - x), y - (bottom - y)
            )
        case Rotation90.DEG_270:
            return _bounds(
                x + (bottom - y), y - (right - x), x + (top - y), y - (left - x)
            )
    return bounds


_NO_ERC_HALF_LENGTHS = {
    NoErcSymbol.CROSS_THIN: 400_000,
    NoErcSymbol.CROSS: 400_000,
    NoErcSymbol.CROSS_SMALL: 200_000,
    NoErcSymbol.CHECKBOX: 400_000,
    NoErcSymbol.TRIANGLE: 266_666,
}


def _no_erc_bounds(record: AltiumSchNoErc) -> SchGeometryBounds:
    x, y = _harness_internal_location(record.location)
    half = _NO_ERC_HALF_LENGTHS[record.symbol]
    offset_x = (
        half if record.symbol in (NoErcSymbol.CHECKBOX, NoErcSymbol.TRIANGLE) else 0
    )
    offset_y = half if record.symbol == NoErcSymbol.CHECKBOX else 0
    extent = half + 100_000
    bounds = _bounds(
        x - extent + offset_x,
        y - extent + offset_y,
        x + extent + offset_x,
        y + extent + offset_y,
    )
    return _rotate_bounds_around_location(bounds, (x, y), record.orientation)


def _vertex_bounds(
    vertices: Sequence[CoordPoint], max_vertices: int
) -> SchGeometryBounds:
    if len(vertices) > max_vertices:
        raise ValueError("component own-bounds vertex limit exceeded")
    # DefaultMaxMinRect is not empty, including for an empty vertex array.
    left = bottom = 2_147_483_647
    right = top = -2_147_483_647
    for vertex in vertices:
        x, y = _harness_internal_location(vertex)
        left, bottom = min(left, x), min(bottom, y)
        right, top = max(right, x), max(top, y)
    return _bounds(left, bottom, right, top)


def _polygon_bounds(
    record: AltiumSchPolygon | AltiumSchBezier, max_vertices: int
) -> SchGeometryBounds:
    bounds = _vertex_bounds(record.vertices, max_vertices)
    if len(record.vertices) > 1:
        return _inflate(bounds, _SYMBOL_LINE_WIDTH_INTERNAL[record.line_width])
    return bounds


def _bounds_ieee_divide(numerator: float, denominator: float) -> float:
    if denominator != 0.0:
        return numerator / denominator
    if numerator == 0.0:
        return math.nan
    return math.copysign(math.inf, numerator * math.copysign(1.0, denominator))


def _blanket_ray_parameters(
    location: tuple[int, int], first: tuple[int, int], second: tuple[int, int]
) -> tuple[float, float]:
    # Managed subtraction wraps before promotion to double. Keep both products
    # in each expression, including the horizontal ray's zero Y delta.
    num = float(_unchecked_i32(location[1] - first[1]))
    num2 = float(_unchecked_i32(second[1] - first[1]))
    num3 = 0.0
    num4 = float(_unchecked_i32(location[0] - first[0]))
    num5 = float(_unchecked_i32(second[0] - first[0]))
    num6 = float(_unchecked_i32(_unchecked_i32(location[0] + 1) - location[0]))
    denominator = num6 * num2 - num3 * num5
    return (
        _bounds_ieee_divide(num * num5 - num4 * num2, denominator),
        _bounds_ieee_divide(num * num6 - num4 * num3, denominator),
    )


def _blanket_vertex_crossing(
    y: int, first_y: int, vertices: Sequence[CoordPoint], index: int
) -> bool:
    third_y = _harness_internal_location(vertices[(index + 2) % len(vertices)])[1]
    if min(first_y, third_y) < y < max(first_y, third_y):
        return True
    if third_y != y:
        return False
    fourth_y = _harness_internal_location(vertices[(index + 3) % len(vertices)])[1]
    return min(first_y, fourth_y) < y < max(first_y, fourth_y)


def _blanket_location_inside(
    location: tuple[int, int], vertices: Sequence[CoordPoint]
) -> bool:
    inside = False
    for index, vertex in enumerate(vertices):
        first = _harness_internal_location(vertex)
        second = _harness_internal_location(vertices[(index + 1) % len(vertices)])
        param_a, param_b = _blanket_ray_parameters(location, first, second)
        if not param_a >= 0.0:
            continue
        if second[1] == location[1] and second[0] >= location[0]:
            crossing = _blanket_vertex_crossing(location[1], first[1], vertices, index)
        else:
            crossing = 0.0 < param_b < 1.0
        if crossing:
            inside = not inside
    return inside


def _blanket_button_bounds(vertices: Sequence[CoordPoint]) -> SchGeometryBounds:
    selected: SchGeometryBounds | None = None
    for vertex in vertices:
        x, y = _harness_internal_location(vertex)
        # The last two candidates are identical in the managed implementation.
        # Preserve raw inverted edges; normalizing them changes winner ordering.
        candidates = (
            _bounds(x + 100_000, y - 900_000, x + 900_000, y - 100_000),
            _bounds(x - 100_000, y - 900_000, x - 900_000, y - 100_000),
            _bounds(x - 100_000, y + 900_000, x - 900_000, y + 100_000),
            _bounds(x - 100_000, y + 900_000, x - 900_000, y + 100_000),
        )
        for candidate in candidates:
            center = (
                _trunc_i32_div(_unchecked_i32(candidate.left + candidate.right), 2),
                _trunc_i32_div(_unchecked_i32(candidate.top + candidate.bottom), 2),
            )
            if not _blanket_location_inside(center, vertices):
                continue
            if selected is None or (
                selected.bottom <= candidate.bottom
                and (
                    selected.bottom != candidate.bottom
                    or selected.left >= candidate.left
                )
            ):
                selected = candidate
    return selected if selected is not None else _bounds(0, 0, 0, 0)


def _blanket_own_bounds(
    record: AltiumSchBlanket, *, max_vertices: int, max_edge_tests: int
) -> SchGeometryBounds:
    if type(max_edge_tests) is not int or max_edge_tests < 0:
        raise ValueError("blanket edge-test limit must be a nonnegative integer")
    count = len(record.vertices)
    if count > max_vertices:
        raise ValueError("component own-bounds vertex limit exceeded")
    if not record.is_collapsed:
        # SchStraightPolygon overrides SchPolygon: no stroke inflation.
        return _vertex_bounds(record.vertices, max_vertices)
    if 4 * count * count > max_edge_tests:
        raise ValueError("blanket edge-test limit exceeded")
    return _inflate(_blanket_button_bounds(record.vertices), 100_000)


def _line_shape_angle(center: tuple[int, int], point: tuple[int, int]) -> float:
    if center == point:
        return 0.0
    if center[0] == point[0]:
        return 270.0 if point[1] <= center[1] else 90.0
    if center[1] == point[1]:
        return 180.0 if point[0] <= center[0] else 0.0
    dx = _unchecked_i32(point[0] - center[0])
    dy = _unchecked_i32(point[1] - center[1])
    angle = math.atan(float(dy) / float(dx)) * 180.0 / math.pi
    # The managed implementation compares point.Y to center.X here, not Y.
    if point[0] > center[0] and point[1] < center[0]:
        angle += 360.0
    elif point[0] < center[0]:
        angle += 180.0
    return angle


def _rotate_line_shape_point(x: int, y: int, angle: float) -> tuple[int, int]:
    match angle:
        case 0.0:
            return x, y
        case 90.0:
            return _unchecked_i32(-y), x
        case 180.0:
            return _unchecked_i32(-x), _unchecked_i32(-y)
        case 270.0:
            return y, _unchecked_i32(-x)
    radians = angle * math.pi / 180.0
    cosine, sine = math.cos(radians), math.sin(radians)
    return (
        _round_away_from_zero(x * cosine - y * sine),
        _round_away_from_zero(y * cosine + x * sine),
    )


def _line_shape_offsets(
    shape: LineShape, line_base: int
) -> tuple[tuple[int, int], ...]:
    arrow_base = 2 * line_base
    match shape:
        case LineShape.ARROW | LineShape.SOLID_ARROW:
            return (
                (-2 * arrow_base, _trunc_i32_div(2 * arrow_base, 3)),
                (-2 * arrow_base, _trunc_i32_div(-2 * arrow_base, 3)),
            )
        case LineShape.TAIL | LineShape.SOLID_TAIL:
            return (
                (arrow_base, arrow_base // 2),
                (arrow_base, -arrow_base // 2),
                (0, arrow_base // 2),
                (0, -arrow_base // 2),
            )
        case LineShape.CIRCLE | LineShape.SQUARE:
            return (
                (line_base, line_base),
                (-line_base, line_base),
                (-line_base, -line_base),
                (line_base, -line_base),
                (line_base, line_base),
            )
        case _:
            return ()


def _add_line_shape_bounds(
    bounds: SchGeometryBounds,
    location: tuple[int, int],
    angle: float,
    shape: LineShape,
    line_base: int,
) -> SchGeometryBounds:
    left, bottom, right, top = bounds.left, bounds.bottom, bounds.right, bounds.top
    for dx, dy in _line_shape_offsets(shape, line_base):
        rx, ry = _rotate_line_shape_point(dx, dy, angle)
        x, y = _unchecked_i32(location[0] + rx), _unchecked_i32(location[1] + ry)
        left, bottom = min(left, x), min(bottom, y)
        right, top = max(right, x), max(top, y)
    return _bounds(left, bottom, right, top)


_LINE_SHAPE_SIZE_COEFFICIENT = {
    LineWidth.SMALLEST: 1,
    LineWidth.SMALL: 2,
    LineWidth.MEDIUM: 3,
    LineWidth.LARGE: 4,
}


def _polyline_bounds(record: AltiumSchPolyline, max_vertices: int) -> SchGeometryBounds:
    bounds = _vertex_bounds(record.vertices, max_vertices)
    count = len(record.vertices)
    if count < 2:
        return bounds
    width = _SYMBOL_LINE_WIDTH_INTERNAL[record.line_width]
    line_base = (width or _SYMBOL_LINE_WIDTH_INTERNAL[LineWidth.SMALL]) * (
        _LINE_SHAPE_SIZE_COEFFICIENT[record.line_shape_size]
    )
    first, second = (_harness_internal_location(p) for p in record.vertices[:2])
    bounds = _add_line_shape_bounds(
        bounds,
        first,
        _line_shape_angle(second, first),
        record.start_line_shape,
        line_base,
    )
    previous, last = (_harness_internal_location(p) for p in record.vertices[-2:])
    location = last
    if previous == last:
        # GetState_Vertex(0) returns this sentinel for a two-point duplicate.
        previous = (
            _harness_internal_location(record.vertices[-3])
            if count > 2
            else (2_147_483_647, 2_147_483_647)
        )
    bounds = _add_line_shape_bounds(
        bounds,
        location,
        _line_shape_angle(previous, last),
        record.end_line_shape,
        line_base,
    )
    return _inflate(bounds, width)


def _ellipse_bounds(record: AltiumSchEllipse) -> SchGeometryBounds:
    x, y = _harness_internal_location(record.location)
    radius_x = _unchecked_i32(record.radius * 100_000 + record.radius_frac)
    radius_y = _unchecked_i32(
        record.secondary_radius * 100_000 + record.secondary_radius_frac
    )
    return _bounds(x - radius_x, y - radius_y, x + radius_x, y + radius_y)


def _wire_bounds(record: AltiumSchWire, max_vertices: int) -> SchGeometryBounds:
    bounds = _vertex_bounds(record.points, max_vertices)
    if len(record.points) < 2:
        return bounds
    # Bus removes the inherited line inflation before adding bus inflation.
    # Unchecked edge arithmetic composes to the selected width directly.
    widths = (
        _BUS_LINE_WIDTH_INTERNAL
        if isinstance(record, AltiumSchBus)
        else _SYMBOL_LINE_WIDTH_INTERNAL
    )
    return _inflate(bounds, widths[record.line_width])


_JUNCTION_SIZE_INTERNAL = {0: 200_000, 1: 300_000, 2: 500_000, 3: 1_000_000}


def _junction_bounds(record: AltiumSchJunction) -> SchGeometryBounds:
    x, y = _harness_internal_location(record.location)
    size = _JUNCTION_SIZE_INTERNAL[record.size] + 3
    return _bounds(x - size, y - size, x + size, y + size)


def _pin_bounds(record: AltiumSchPin) -> SchGeometryBounds:
    x, y = _harness_internal_location(record.location)
    length = _unchecked_i32(record.length * 100_000 + (record.pin_length_frac or 0))
    width = (
        300_000 if "[" in record.designator and "]" in record.designator else 100_000
    )
    side = width + 200_000
    match record.orientation:
        case Rotation90.DEG_0:
            return _bounds(x - 200_000, y - side, x + length + 200_000, y + side)
        case Rotation90.DEG_90:
            return _bounds(x - side, y - 200_000, x + side, y + length + 200_000)
        case Rotation90.DEG_180:
            return _bounds(x - length - 200_000, y - side, x + 200_000, y + side)
        case Rotation90.DEG_270:
            return _bounds(x - side, y - length - 200_000, x + side, y + 200_000)
        case _:
            raise ValueError("invalid pin orientation for component own bounds")


def _label_own_bounds_from_size(
    location: tuple[int, int],
    width: int,
    height: int,
    justification: int,
    orientation: int,
) -> SchGeometryBounds:
    """Apply managed label layout to cached signed internal xSize/ySize."""
    if not 0 <= justification <= 8:
        return _bounds(0, 0, 0, 0)
    half_width, half_height = _trunc_i32_div(width, 2), _trunc_i32_div(height, 2)
    horizontal, vertical = justification % 3, justification // 3
    left, right = (
        (0, -half_width, -width)[horizontal],
        (width, half_width, 0)[horizontal],
    )
    bottom, top = (
        (0, -half_height, -height)[vertical],
        (height, half_height, 0)[vertical],
    )
    match orientation:
        case 1:
            left, bottom, right, top = -top, left, -bottom, right
        case 2:
            left, bottom, right, top = -right, -top, -left, -bottom
        case 3:
            left, bottom, right, top = bottom, -right, top, -left
    x, y = location
    # Do not normalize after overflow. Managed label methods assign these
    # edges directly; ToRectangle performs its separate signed conversion.
    return _bounds(x + left, y + bottom, x + right, y + top)


class _BoundsAngleStepLimitExceeded(ValueError):
    def __init__(self, steps: int) -> None:
        super().__init__("component arc own-bounds angle step limit exceeded")
        self.steps = steps


def _normalize_bounds_angle(angle: float, remaining: int) -> tuple[float, int]:
    # Repeated subtraction preserves managed floating-point behavior and 360.
    # The shared budget also bounds hostile angles for which subtraction stalls.
    initial = remaining
    while angle > 360.0 or angle < 0.0:
        if remaining == 0:
            raise _BoundsAngleStepLimitExceeded(initial)
        angle += -360.0 if angle > 360.0 else 360.0
        remaining -= 1
    return angle, remaining


def _bounds_quadrant(angle: float) -> int:
    return 4 if abs(angle - 360.0) < 0.001 else int(angle / 90.0) + 1


# ArcRectangle's twelve different-quadrant branches: L, B, R, T full extrema.
_ARC_QUADRANT_EXTREMA = {
    (1, 2): (False, False, False, True),
    (1, 3): (True, False, False, True),
    (1, 4): (True, True, False, True),
    (2, 1): (True, True, True, False),
    (2, 3): (True, False, False, False),
    (2, 4): (True, True, False, False),
    (3, 1): (False, True, True, False),
    (3, 2): (False, True, True, True),
    (3, 4): (False, True, False, False),
    (4, 1): (False, False, True, False),
    (4, 2): (False, False, True, True),
    (4, 3): (True, False, True, True),
}


def _arc_extrema_flags(
    start: float, end: float, start_x: float, end_x: float
) -> tuple[bool, bool, bool, bool]:
    first, last = _bounds_quadrant(start), _bounds_quadrant(end)
    if first != last:
        return _ARC_QUADRANT_EXTREMA[first, last]
    full = start > end and (
        (first < 3 and start_x <= end_x) or (first >= 3 and end_x <= start_x)
    )
    return full, full, full, full


def _circular_arc_bounds(
    location: tuple[int, int],
    radius: int,
    angles: tuple[float, float],
    max_angle_steps: int,
) -> SchGeometryBounds:
    return _circular_arc_bounds_with_work(location, radius, angles, max_angle_steps)[0]


def _circular_arc_bounds_with_work(
    location: tuple[int, int],
    radius: int,
    angles: tuple[float, float],
    max_angle_steps: int,
) -> tuple[SchGeometryBounds, int]:
    try:
        start, remaining = _normalize_bounds_angle(angles[0], max_angle_steps)
        end, remaining = _normalize_bounds_angle(angles[1], remaining)
    except _BoundsAngleStepLimitExceeded:
        # Either failure has exhausted the shared budget across both angles.
        raise _BoundsAngleStepLimitExceeded(max_angle_steps) from None
    return _normalized_circular_arc_bounds(location, radius, start, end), (
        max_angle_steps - remaining
    )


def _normalized_circular_arc_bounds(
    location: tuple[int, int], radius: int, start: float, end: float
) -> SchGeometryBounds:
    x, y = location
    start_rad, end_rad = start * math.pi / 180.0, end * math.pi / 180.0
    sx, sy = math.cos(start_rad) * radius + x, math.sin(start_rad) * radius + y
    ex, ey = math.cos(end_rad) * radius + x, math.sin(end_rad) * radius + y
    if abs(start - end) < 0.001:
        return _bounds(
            _round_away_from_zero(ex),
            _round_away_from_zero(ey),
            _round_away_from_zero(ex),
            _round_away_from_zero(ey),
        )
    left, bottom, right, top = _arc_extrema_flags(start, end, sx, ex)
    return _bounds(
        _round_away_from_zero(x - radius if left else min(sx, ex)),
        _round_away_from_zero(y - radius if bottom else min(sy, ey)),
        _round_away_from_zero(x + radius if right else max(sx, ex)),
        _round_away_from_zero(y + radius if top else max(sy, ey)),
    )


def _elliptical_arc_quadrants(
    start: float,
    end: float,
) -> tuple[bool, bool, bool, bool]:
    if start > end:
        return True, start < 180.0 or end > 90.0, start < 270.0 or end > 180.0, True
    return (
        start < 90.0,
        start < 180.0 and end > 90.0,
        start < 270.0 and end > 180.0,
        end > 270.0,
    )


def _elliptical_arc_bounds(
    location: tuple[int, int],
    radius: int,
    secondary_radius: int,
    angles: tuple[float, float],
) -> SchGeometryBounds:
    first, second, third, fourth = _elliptical_arc_quadrants(*angles)
    x, y = location
    return _bounds(
        x - radius if second or third else x,
        y - secondary_radius if third or fourth else y,
        x + radius if first or fourth else x,
        y + secondary_radius if first or second else y,
    )


def _arc_own_bounds(
    record: AltiumSchArc,
    angles: tuple[float, float],
    *,
    max_angle_steps: int = 4096,
) -> tuple[SchGeometryBounds, tuple[float, float]]:
    """Return bounds and next render-local angles, without writing the record."""
    bounds, next_angles, _ = _arc_own_bounds_with_work(
        record, angles, max_angle_steps=max_angle_steps
    )
    return bounds, next_angles


def _arc_own_bounds_with_work(
    record: AltiumSchArc,
    angles: tuple[float, float],
    *,
    max_angle_steps: int,
) -> tuple[SchGeometryBounds, tuple[float, float], int]:
    if type(record) not in (AltiumSchArc, AltiumSchEllipticalArc, AltiumSchPieChart):
        raise NotImplementedError(
            f"component arc own bounds for {type(record).__name__}"
        )
    if max_angle_steps < 0:
        raise ValueError("component arc own-bounds angle step limit cannot be negative")
    if not all(math.isfinite(angle) for angle in angles):
        raise ValueError("component arc own-bounds angles must be finite")
    next_angles = (0.0, 360.0) if abs(angles[0] - angles[1]) < 0.001 else angles
    location = _harness_internal_location(record.location)
    radius = _unchecked_i32(record.radius * 100_000 + record.radius_frac)
    if isinstance(record, AltiumSchEllipticalArc):
        secondary = _unchecked_i32(
            record.secondary_radius * 100_000 + record.secondary_radius_frac
        )
        # This override reads captured angles even after updating the live state.
        return (
            _elliptical_arc_bounds(location, radius, secondary, angles),
            next_angles,
            0,
        )
    bounds, steps = _circular_arc_bounds_with_work(
        location, radius, next_angles, max_angle_steps
    )
    return bounds, next_angles, steps


def _ieee_point(
    point: tuple[float, float],
    location: tuple[int, int],
    orientation: Rotation90,
    mirrored: bool,
    scale: float,
) -> tuple[int, int]:
    x, y = int(point[0]), int(point[1])
    match orientation:
        case Rotation90.DEG_90:
            x, y = -y, x
        case Rotation90.DEG_180:
            x, y = -x, -y
        case Rotation90.DEG_270:
            x, y = y, -x
    if mirrored:
        x = -x
    return (
        _unchecked_i32(location[0] + _round_away_from_zero(_unchecked_i32(x) * scale)),
        _unchecked_i32(location[1] + _round_away_from_zero(_unchecked_i32(y) * scale)),
    )


def _ieee_arc_angles(
    start: float, end: float, orientation: Rotation90, mirrored: bool
) -> tuple[float, float]:
    rotation = int(orientation) * 90 if orientation in (1, 2, 3) else 0
    angles = [start + rotation, end + rotation]
    for index, angle in enumerate(angles):
        # Authored symbol definitions have fixed bounded angles.
        angles[index] = angle - 360 if angle > 360 else angle
    if mirrored:
        angles = [180 - angle if angle <= 180 else 540 - angle for angle in angles]
        angles.reverse()
    start, end = angles
    return (0.0, 360.0) if start == end else (start, end)


def _ieee_own_bounds(
    record: AltiumSchIeeeSymbol, max_vertices: int
) -> SchGeometryBounds:
    location = _harness_internal_location(record.location)
    if record.symbol == IeeeSymbol.NONE:
        x, y = location
        return _bounds(x, y, x + 10, y + 10)
    polylines, polygons, arcs = _IEEE_SYMBOL_SHAPES.get(record.symbol, ([], [], []))
    if sum(map(len, polylines)) + sum(map(len, polygons)) > max_vertices:
        raise ValueError("component own-bounds vertex limit exceeded")
    scale = (
        _unchecked_i32(record.scale_factor * 100_000 + record.scale_factor_frac) / 10.0
    )
    bounds = _bounds(2_147_483_647, 2_147_483_647, -2_147_483_647, -2_147_483_647)
    for x, y, radius, start, end in arcs:
        center = _ieee_point(
            (x, y), location, record.orientation, record.is_mirrored, scale
        )
        scaled_radius = _round_away_from_zero(radius * scale)
        angles = _ieee_arc_angles(start, end, record.orientation, record.is_mirrored)
        # BaseSymbolData assigns each arc, then unions all line/polygon points.
        bounds = _circular_arc_bounds(center, scaled_radius, angles, 16)
    for path in (*polylines, *polygons):
        for point in path:
            x, y = _ieee_point(
                point, location, record.orientation, record.is_mirrored, scale
            )
            bounds = _bounds(
                min(bounds.left, x),
                min(bounds.bottom, y),
                max(bounds.right, x),
                max(bounds.top, y),
            )
    return bounds


def _line_view_own_bounds(
    record: _AltiumSchLineView, max_vertices: int
) -> SchGeometryBounds:
    if len(record.lines) > max_vertices // 2:
        raise ValueError("component own-bounds vertex limit exceeded")
    bounds = record.own_bounds_internal()
    if bounds is not None:
        return bounds
    # No lines still has an engine own rectangle. The image-model renderer's
    # separate None/fallback convention must not leak into this bounds path.
    return _bounds(2_147_483_647, 2_147_483_647, -2_147_483_647, -2_147_483_647)


_BASIC_BOUNDS_TYPES = frozenset(
    {
        AltiumSchComponent,
        _AltiumSchHarnessCavityComponent,
        _AltiumSchHarnessCavity,
        _AltiumSchLineView,
        AltiumSchHarnessSplice,
        AltiumSchHarnessConnector,
        AltiumSchSheetSymbol,
        AltiumSchRectangle,
        AltiumSchRoundedRectangle,
        AltiumSchImage,
        AltiumSchTextFrame,
        AltiumSchNote,
        AltiumSchCompileMask,
        AltiumSchLine,
        AltiumSchBusEntry,
        AltiumSchPolygon,
        AltiumSchBlanket,
        AltiumSchPolyline,
        AltiumSchBezier,
        AltiumSchEllipse,
        AltiumSchPin,
        AltiumSchWire,
        AltiumSchBus,
        AltiumSchSignalHarness,
        AltiumSchHarnessBundle,
        AltiumSchJunction,
        AltiumSchNoErc,
        AltiumSchIeeeSymbol,
    }
)


def _primitive_own_bounds(
    record: object,
    *,
    max_vertices: int = 1_000_000,
    max_blanket_edge_tests: int = 1_000_000,
) -> SchGeometryBounds | None:
    """Return audited own bounds; reject classes whose adapter has not landed."""
    if max_vertices < 0:
        raise ValueError("component own-bounds vertex limit cannot be negative")
    # Derived model classes can override managed bounds or HasOwn. Do not let
    # isinstance silently treat an unaudited subclass as its base primitive.
    if type(record) not in _BASIC_BOUNDS_TYPES:
        raise NotImplementedError(f"component own bounds for {type(record).__name__}")
    if isinstance(record, AltiumSchBlanket):
        return _blanket_own_bounds(
            record, max_vertices=max_vertices, max_edge_tests=max_blanket_edge_tests
        )
    if isinstance(record, (AltiumSchPolygon, AltiumSchBezier)):
        return _polygon_bounds(record, max_vertices)
    if isinstance(record, AltiumSchPolyline):
        return _polyline_bounds(record, max_vertices)
    if isinstance(record, AltiumSchWire):
        return _wire_bounds(record, max_vertices)
    if isinstance(record, AltiumSchIeeeSymbol):
        return _ieee_own_bounds(record, max_vertices)
    if isinstance(record, _AltiumSchLineView):
        return _line_view_own_bounds(record, max_vertices)
    return _non_vertex_own_bounds(record)


def _non_vertex_own_bounds(record: object) -> SchGeometryBounds | None:
    if isinstance(record, AltiumSchComponent):
        return None
    if isinstance(record, AltiumSchSheetSymbol):
        return _sheet_symbol_bounds(record)
    if isinstance(
        record,
        (
            AltiumSchRectangle,
            AltiumSchRoundedRectangle,
            AltiumSchImage,
            AltiumSchTextFrame,
        ),
    ):
        return _rectangle_bounds(record)
    if isinstance(record, AltiumSchLine):
        return _line_bounds(record)
    if isinstance(record, AltiumSchEllipse):
        return _ellipse_bounds(record)
    if isinstance(record, AltiumSchPin):
        return _pin_bounds(record)
    if isinstance(record, AltiumSchJunction):
        return _junction_bounds(record)
    if isinstance(record, AltiumSchNoErc):
        return _no_erc_bounds(record)
    return _harness_non_vertex_own_bounds(record)


def _harness_non_vertex_own_bounds(record: object) -> SchGeometryBounds:
    if isinstance(record, AltiumSchHarnessConnector):
        x, y = _harness_internal_location(record.location)
        width = _unchecked_i32(record.xsize * 100_000 + record.xsize_frac)
        height = _unchecked_i32(record.ysize * 100_000 + record.ysize_frac)
        return _bounds(x, y - height, x + width, y)
    if isinstance(record, AltiumSchHarnessSplice):
        x, y = _harness_internal_location(record.location)
        return _harness_splice_bounds(record, x=x, y=y)
    if isinstance(record, _AltiumSchHarnessCavity):
        return record.own_bounds_internal()
    raise AssertionError("registered component bounds type has no adapter")
