"""Private V5 record adapters for managed harness-layout objects."""

from __future__ import annotations

import colorsys
import math
import unicodedata
from collections import Counter, deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, cast

from .altium_common_enums import ComponentKind
from ._sch_managed_numeric import (
    managed_f32 as _f32,
    split_coord_toward_zero as _split_coord_toward_zero,
    unchecked_i32 as _unchecked_i32,
)
from ._sch_source_admission import _SourceAdmission
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_record_sch__component import AltiumSchHarnessComponent
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__label import AltiumSchLabel
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_sch__wire import AltiumSchWire
from .altium_record_sch__text_frame import (
    _decode_altium_multiline_text,
    _encode_altium_multiline_text,
)
from .altium_record_types import (
    MAX_INDEXED_ITEMS_PER_RECORD,
    CoordPoint,
    LineStyle,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
    TextOrientation,
)
from .altium_sch_enums import SchHorizontalAlign
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_uppercase_fields,
    validate_record_enum_value,
)
from .altium_serializer import (
    AltiumSerializer,
    FieldDef,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)

type _CoveringCommentYSize = int | Callable[[], int]

_DOTNET_WHITESPACE_CATEGORIES = frozenset({"Zs", "Zl", "Zp"})
_SYMBOL_LINE_WIDTH_INTERNAL = {
    LineWidth.SMALLEST: 0,
    LineWidth.SMALL: 100_000,
    LineWidth.MEDIUM: 300_000,
    LineWidth.LARGE: 500_000,
}
_BUS_LINE_WIDTH_INTERNAL = {
    LineWidth.SMALLEST: 200_000,
    LineWidth.SMALL: 300_000,
    LineWidth.MEDIUM: 500_000,
    LineWidth.LARGE: 700_000,
}
_HARNESS_LINE_DASH_STYLE = {
    LineStyle.SOLID: "pdsSolid",
    LineStyle.DASHED: "pdsDash",
    LineStyle.DOTTED: "pdsDot",
    LineStyle.DASH_DOT: "pdsDashDot",
}
_MAX_HARNESS_CONNECTION_BUNDLES = 50_000
_MAX_HARNESS_COMPILE_MASKS = 50_000
_MAX_HARNESS_COMPILE_MASK_CANDIDATE_VISITS = 10_000_000
_MAX_HARNESS_COVERING_TOPOLOGY_OBJECTS = 50_000
_MAX_HARNESS_COVERING_SEGMENTS = 200_000
_MAX_HARNESS_COVERING_POINTS = 800_000
_MAX_HARNESS_COVERING_TOPOLOGY_WORK = 1_000_000
_HARNESS_CONSTRAINED_LOOK_AHEAD_MODES = frozenset(
    {"eLine90Start", "eLine90End", "eLine45Start", "eLine45End"}
)
_HARNESS_LOOK_AHEAD_MODES = _HARNESS_CONSTRAINED_LOOK_AHEAD_MODES | {"eLineAnyAngle"}

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryOp,
        SchGeometryRecord,
    )
    from .altium_sch_svg_renderer import SchSvgRenderContext


class HarnessSpliceStyle(IntEnum):
    CIRCLE = 0
    INLINE = 1


class HarnessLayoutConnectionPointStyle(IntEnum):
    CIRCLE = 0
    SQUARE = 1
    INSULATOR = 2


class HarnessBrush(IntEnum):
    NONE = 0
    BLACK_WEAVE = 1
    YELLOW_WEAVE = 2
    RED_WEAVE = 3


class HarnessCoveringType(IntEnum):
    HARNESS_BRUSH = 0
    TUBING = 1
    CORRUGATED_TUBING = 2
    SPIRAL_WRAP = 3
    TAPE = 4
    BRAIDING = 5


class HarnessCoveringClosureType(IntEnum):
    STANDARD = 0
    SLIT = 1
    SHRINKABLE = 2


@dataclass(slots=True)
class HarnessLayoutConnectionPointConnector:
    """One managed connector reference attached to a layout connection point."""

    connector_id: str
    pins: list[str]
    is_auto_assigned: bool = False

    def __init__(
        self,
        connector_id: str,
        pins: Iterable[str] = (),
        *,
        is_auto_assigned: bool = False,
    ) -> None:
        if not isinstance(connector_id, str):
            raise TypeError("connector_id must be a string")
        self.connector_id = connector_id
        self.pins = []
        self.is_auto_assigned = bool(is_auto_assigned)
        for pin_id in pins:
            self.add_pin(pin_id)

    def add_pin(self, pin_id: str) -> None:
        """Append a pin ID unless the connector already contains it."""
        if not isinstance(pin_id, str):
            raise TypeError("pin_id must be a string")
        if pin_id not in self.pins:
            self.pins.append(pin_id)

    def remove_pin(self, pin_id: str) -> None:
        """Remove the first matching pin ID when present."""
        if pin_id in self.pins:
            self.pins.remove(pin_id)


def _field_name(field: FieldDef | str) -> str:
    return field.canonical if isinstance(field, FieldDef) else field


def _read_dynamic(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    default: str = "",
) -> tuple[str, bool, bool]:
    return read_dynamic_string_field(
        serializer,
        record,
        owner._record,
        field,
        default=default,
    )


def _write_dynamic(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    value: str,
    source: tuple[str, bool, bool],
) -> None:
    source_value, was_present, used_utf8 = source
    name = _field_name(field)
    if value != source_value and not value:
        owner._remove_fields_case_insensitively(record, [name, f"%UTF8%{name}"])
        return
    write_dynamic_string_field(
        serializer,
        record,
        field,
        value,
        raw_record=owner._raw_record,
        used_utf8_sidecar=used_utf8,
        was_present=was_present,
        force=value != source_value,
    )


def _read_indexed_strings(
    serializer: AltiumSerializer,
    record: dict[str, object],
    count_field: str,
    item_prefix: str,
) -> tuple[str, ...]:
    count, _ = serializer.read_int(record, count_field, default=0)
    if count < 0:
        raise ValueError(f"{count_field} cannot be negative")
    if count > MAX_INDEXED_ITEMS_PER_RECORD:
        raise ValueError(
            f"{count_field} exceeds {MAX_INDEXED_ITEMS_PER_RECORD} entries"
        )
    return tuple(
        serializer.read_str(record, f"{item_prefix}{index}", default="")[0]
        for index in range(1, count + 1)
    )


def _is_indexed_field(field: str, prefix: str) -> bool:
    folded = field.casefold()
    prefix_folded = prefix.casefold()
    return folded.startswith(prefix_folded) and folded[len(prefix_folded) :].isdigit()


def _write_indexed_strings(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    count_field: str,
    item_prefix: str,
    values: tuple[str, ...],
    source: tuple[str, ...],
) -> None:
    if owner._raw_record is not None and values == source:
        return
    if len(values) > MAX_INDEXED_ITEMS_PER_RECORD:
        raise ValueError(
            f"{count_field} exceeds {MAX_INDEXED_ITEMS_PER_RECORD} entries"
        )
    serializer.remove_field(record, count_field)
    for key in tuple(record):
        if _is_indexed_field(key, item_prefix):
            record.pop(key)
    ordered = sorted(values)
    if not ordered:
        return
    serializer.write_int(record, count_field, len(ordered), None, force=True)
    for index, value in enumerate(ordered, start=1):
        serializer.write_str(
            record,
            f"{item_prefix}{index}",
            value,
            None,
            force=True,
        )


def _write_sparse_int(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    value: int,
    source: int,
) -> None:
    if owner._raw_record is not None and value == source:
        return
    serializer.remove_field(record, field)
    if value:
        serializer.write_int(record, field, value, None, force=True)


def _write_sparse_bool(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    value: bool,
    source: bool,
) -> None:
    if owner._raw_record is not None and value == source:
        return
    serializer.remove_field(record, field)
    if value:
        serializer.write_bool(record, field, True, None, force=True)


def _write_sparse_long(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    value: int,
    source: int,
) -> None:
    if owner._raw_record is not None and value == source:
        return
    serializer.remove_field(record, field)
    if value:
        serializer.write_long(record, field, value, None)


def _write_sparse_color(
    owner: SchGraphicalObject,
    serializer: AltiumSerializer,
    record: dict[str, object],
    field: FieldDef | str,
    value: int,
    source: int | None,
) -> None:
    if owner._raw_record is not None and value == source:
        return
    serializer.remove_field(record, field)
    if value:
        serializer.write_color(record, field, value, None, force=True)


class _HarnessNamedPoint(AltiumSchLabel):
    _style_type: type[IntEnum]
    _style_maximum: int
    _connected_count_field: str
    _connected_item_prefix: str
    _import_text_default: str

    def __init__(self) -> None:
        super().__init__()
        self.style: IntEnum = self._style_type(0)
        self.connected_unique_ids: tuple[str, ...] = ()
        self.show_name = True
        self.border_color = 12_632_256
        self.designator_locked = False
        self.orientation = TextOrientation.DEGREES_0
        self._source_connected_unique_ids: tuple[str, ...] = ()
        self._capture_named_point_source_state()

    def _capture_named_point_source_state(self) -> None:
        self._source_style = self.style
        self._source_connected_unique_ids = self.connected_unique_ids
        self._source_show_name = self.show_name
        self._source_border_color = self.border_color
        self._source_designator_locked = self.designator_locked

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        style, _ = serializer.read_int(record, "Style", default=0)
        validate_record_enum_value("Style", style, self._style_maximum)
        self.style = self._style_type(style)
        self.connected_unique_ids = _read_indexed_strings(
            serializer,
            record,
            self._connected_count_field,
            self._connected_item_prefix,
        )
        self.show_name = serializer.read_bool(record, Fields.SHOW_NAME, default=True)[0]
        self.border_color = serializer.read_color(record, "BorderColor", default=0)[0]
        self.designator_locked = serializer.read_bool(
            record, Fields.DESIGNATOR_LOCKED, default=True
        )[0]
        if not self._has_font_id:
            self.font_id = 0
        if not self._has_text:
            self.text = self._import_text_default
        area_color, _ = serializer.read_color(record, Fields.AREA_COLOR, default=0)
        self.area_color = area_color
        self.justification = self.justification.__class__.BOTTOM_LEFT
        self.is_mirrored = False
        self.url = ""
        self._capture_graphical_source_state()
        self._capture_label_source_state()
        self._capture_named_point_source_state()

    def serialize_to_record(self) -> dict[str, object]:
        validate_record_enum_value("Style", self.style.value, self._style_maximum)
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        self._serialize_managed_family_int(
            record, serializer, "Style", self.style.value
        )
        _write_indexed_strings(
            self,
            serializer,
            record,
            self._connected_count_field,
            self._connected_item_prefix,
            self.connected_unique_ids,
            self._source_connected_unique_ids,
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.SHOW_NAME,
            self.show_name,
            self._source_show_name,
        )
        self._serialize_managed_family_color(
            record, serializer, "BorderColor", int(self.border_color or 0)
        )
        _write_sparse_color(
            self,
            serializer,
            record,
            Fields.AREA_COLOR.canonical,
            int(self.area_color or 0),
            self._source_area_color,
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.DESIGNATOR_LOCKED,
            self.designator_locked,
            self._source_designator_locked,
        )
        for field in ("Justification", "IsMirrored", "URL", "%UTF8%URL"):
            serializer.remove_field(record, field)
        return self._order_authored_graphical_fields(
            record,
            self._authored_named_point_family_order(),
        )

    def _authored_named_point_family_order(self) -> tuple[str, ...]:
        connected_fields = tuple(
            f"{self._connected_item_prefix}{index}"
            for index in range(1, len(self.connected_unique_ids) + 1)
        )
        return (
            "Style",
            *(
                ("ShowName",)
                if self.record_type is SchRecordType.HARNESS_SPLICE
                else ()
            ),
            self._connected_count_field,
            *connected_fields,
            *(
                ("ConnectedObjectUniqueId",)
                if self.record_type is SchRecordType.HARNESS_SPLICE
                else ()
            ),
            "Location.X",
            "Location.X_Frac",
            "Location.Y",
            "Location.Y_Frac",
            "Orientation",
            "Color",
            "AreaColor",
            "BorderColor",
            "FontID",
            "Text",
            "UniqueID",
            *(
                ("ShowName",)
                if self.record_type is SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT
                else ()
            ),
            "DesignatorLocked",
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields


def _harness_internal_location(location: CoordPoint) -> tuple[int, int]:
    return (
        _unchecked_i32(location.x * 100_000 + location.x_frac),
        _unchecked_i32(location.y * 100_000 + location.y_frac),
    )


def _unchecked_i32_offset(value: int, offset: int) -> int:
    return _unchecked_i32(value + offset)


def _harness_geometry_point(
    ctx: SchSvgRenderContext,
    x: int | float,
    y: int | float,
    *,
    units_per_px: int,
) -> tuple[float, float]:
    from .altium_sch_geometry_oracle import svg_coord_to_geometry

    svg_x, svg_y = ctx.transform_point(float(x) / 100_000, float(y) / 100_000)
    return svg_coord_to_geometry(
        svg_x,
        svg_y,
        sheet_height_px=float(ctx.sheet_height or 0.0),
        units_per_px=units_per_px,
    )


def _layout_label_text_anchor(
    bounds: SchGeometryBounds,
    orientation: TextOrientation,
) -> tuple[int, int]:
    if orientation is TextOrientation.DEGREES_90:
        return bounds.left, bounds.bottom
    if orientation is TextOrientation.DEGREES_180:
        return bounds.right, bounds.bottom
    if orientation is TextOrientation.DEGREES_270:
        return bounds.right, bounds.top
    return bounds.left, bounds.top


def _trunc_i32_div(value: int, divisor: int) -> int:
    quotient = abs(value) // abs(divisor)
    return quotient if (value < 0) == (divisor < 0) else -quotient


def _layout_label_start_x(
    alignment: SchHorizontalAlign,
    *,
    rect_x: int,
    rect_width: int,
    line_width: int,
) -> int:
    if alignment is SchHorizontalAlign.CENTER:
        numerator = _unchecked_i32(_unchecked_i32(rect_x * 2) + rect_width - line_width)
        return _trunc_i32_div(numerator, 2)
    if alignment is SchHorizontalAlign.RIGHT:
        return _unchecked_i32(rect_x + rect_width - line_width)
    return rect_x


def _layout_label_lines(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return tuple(lines)


def _utf16_code_unit_prefix(text: str, limit: int) -> str:
    prefix: list[str] = []
    units = 0
    for character in text:
        code_point = ord(character)
        character_units = 2 if code_point > 0xFFFF else 1
        if units + character_units <= limit:
            prefix.append(character)
            units += character_units
            continue
        if character_units == 2 and units < limit:
            scalar = code_point - 0x10000
            prefix.append(chr(0xD800 + (scalar >> 10)))
        break
    return "".join(prefix)


def _is_dotnet_whitespace(text: str) -> bool:
    return not text or all(
        character in "\t\n\v\f\r\x85"
        or unicodedata.category(character) in _DOTNET_WHITESPACE_CATEGORIES
        for character in text
    )


def _next_tab_stop(offset: int, tab_width: int) -> int:
    if tab_width == 0 or offset % tab_width == 0:
        return _unchecked_i32(offset + tab_width)
    return _unchecked_i32(math.ceil(offset / tab_width) * tab_width)


def _layout_label_words(text: str) -> tuple[tuple[str, bool], ...]:
    words: list[tuple[str, bool]] = []
    remaining = text
    while remaining:
        tab_index = remaining.find("\t")
        if tab_index < 0:
            words.append((remaining, False))
            remaining = ""
        else:
            words.append((remaining[:tab_index], True))
            remaining = remaining[tab_index + 1 :]
    return tuple(words)


def _layout_label_tabbed_width(
    words: tuple[tuple[int, bool], ...], tab_width: int
) -> int:
    width = 0
    for word_width, has_tab in words:
        width = _unchecked_i32(width + word_width)
        if has_tab:
            width = _next_tab_stop(width, tab_width)
    return width


def _rotate_layout_label_point(
    point: tuple[int, int],
    *,
    anchor: tuple[int, int],
    orientation: TextOrientation,
) -> tuple[int, int]:
    dx = _unchecked_i32(point[0] - anchor[0])
    dy = _unchecked_i32(point[1] - anchor[1])
    if orientation is TextOrientation.DEGREES_90:
        return _unchecked_i32(anchor[0] - dy), _unchecked_i32(anchor[1] + dx)
    if orientation is TextOrientation.DEGREES_180:
        return _unchecked_i32(anchor[0] - dx), _unchecked_i32(anchor[1] - dy)
    if orientation is TextOrientation.DEGREES_270:
        return _unchecked_i32(anchor[0] + dy), _unchecked_i32(anchor[1] - dx)
    return point


def _harness_circle_operation(
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    radius: int,
    units_per_px: int,
    brush: dict[str, object] | None = None,
    pen: dict[str, object] | None = None,
) -> SchGeometryOp:
    from .altium_sch_geometry_oracle import SchGeometryOp, _geometry_item_length

    center_x, center_y = _harness_geometry_point(
        ctx,
        x,
        y,
        units_per_px=units_per_px,
    )
    radius_units = _geometry_item_length(
        abs(float(ctx.scale)) * radius / 100_000,
        units_per_px=units_per_px,
    )
    return SchGeometryOp.rounded_rectangle_from_item(
        center_x=center_x,
        center_y=center_y,
        half_width=radius_units,
        half_height=radius_units,
        corner_x_radius=radius_units,
        corner_y_radius=radius_units,
        brush=brush,
        pen=pen,
    )


def _inline_splice_segments(
    orientation: TextOrientation,
    *,
    x: int,
    y: int,
) -> tuple[tuple[tuple[int | float, int | float], ...], ...]:
    size = 1_000_000
    half_size = size // 2
    size_and_half = size * 1.5
    if orientation is TextOrientation.DEGREES_180:
        return (
            (
                (_unchecked_i32_offset(x, -half_size), _unchecked_i32_offset(y, size)),
                (_unchecked_i32_offset(x, -half_size), _unchecked_i32_offset(y, -size)),
            ),
            (
                (x - size_and_half, _unchecked_i32_offset(y, size)),
                (x - size_and_half, _unchecked_i32_offset(y, -size)),
            ),
            ((x, y), (_unchecked_i32_offset(x, -half_size), y)),
        )
    if orientation is TextOrientation.DEGREES_270:
        return (
            (
                (_unchecked_i32_offset(x, -size), _unchecked_i32_offset(y, -half_size)),
                (_unchecked_i32_offset(x, size), _unchecked_i32_offset(y, -half_size)),
            ),
            (
                (_unchecked_i32_offset(x, -size), y - size_and_half),
                (_unchecked_i32_offset(x, size), y - size_and_half),
            ),
            ((x, y), (x, _unchecked_i32_offset(y, -half_size))),
        )
    if orientation is TextOrientation.DEGREES_90:
        return (
            (
                (_unchecked_i32_offset(x, -size), _unchecked_i32_offset(y, half_size)),
                (_unchecked_i32_offset(x, size), _unchecked_i32_offset(y, half_size)),
            ),
            (
                (_unchecked_i32_offset(x, -size), y + size_and_half),
                (_unchecked_i32_offset(x, size), y + size_and_half),
            ),
            ((x, y), (x, _unchecked_i32_offset(y, half_size))),
        )
    return (
        (
            (_unchecked_i32_offset(x, half_size), _unchecked_i32_offset(y, size)),
            (_unchecked_i32_offset(x, half_size), _unchecked_i32_offset(y, -size)),
        ),
        (
            (x + size_and_half, _unchecked_i32_offset(y, size)),
            (x + size_and_half, _unchecked_i32_offset(y, -size)),
        ),
        ((x, y), (_unchecked_i32_offset(x, half_size), y)),
    )


def _harness_splice_operations(
    splice: AltiumSchHarnessSplice,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import (
        SchGeometryOp,
        _geometry_item_length,
        make_pen,
        make_solid_brush,
    )

    if splice.style is HarnessSpliceStyle.CIRCLE:
        operations = [
            _harness_circle_operation(
                ctx,
                x=x,
                y=y,
                radius=400_000,
                units_per_px=units_per_px,
                brush=make_solid_brush(int(splice.area_color or 0)),
            )
        ]
        if splice.area_color != splice.border_color:
            operations.append(
                _harness_circle_operation(
                    ctx,
                    x=x,
                    y=y,
                    radius=400_000,
                    units_per_px=units_per_px,
                    pen=make_pen(
                        int(splice.border_color or 0),
                        width=_geometry_item_length(
                            ctx.get_stroke_scale(), units_per_px=units_per_px
                        ),
                    ),
                )
            )
        return operations

    pen = make_pen(
        int(splice.color or 0),
        width=_geometry_item_length(ctx.get_stroke_scale(), units_per_px=units_per_px),
    )
    return [
        SchGeometryOp.lines(
            [
                _harness_geometry_point(ctx, *point, units_per_px=units_per_px)
                for point in segment
            ],
            pen=pen,
        )
        for segment in _inline_splice_segments(splice.orientation, x=x, y=y)
    ]


def _harness_splice_bounds(
    splice: AltiumSchHarnessSplice,
    *,
    x: int,
    y: int,
) -> SchGeometryBounds:
    from .altium_sch_geometry_oracle import SchGeometryBounds

    if splice.style is HarnessSpliceStyle.CIRCLE:
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -400_000),
            top=_unchecked_i32_offset(y, 400_000),
            right=_unchecked_i32_offset(x, 400_000),
            bottom=_unchecked_i32_offset(y, -400_000),
        )
    if splice.orientation is TextOrientation.DEGREES_180:
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -1_500_000),
            top=_unchecked_i32_offset(y, 1_000_000),
            right=max(_unchecked_i32_offset(x, -500_000), x),
            bottom=_unchecked_i32_offset(y, -1_000_000),
        )
    if splice.orientation is TextOrientation.DEGREES_270:
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -1_000_000),
            top=max(_unchecked_i32_offset(y, -500_000), y),
            right=_unchecked_i32_offset(x, 1_000_000),
            bottom=_unchecked_i32_offset(y, -1_500_000),
        )
    if splice.orientation is TextOrientation.DEGREES_90:
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -1_000_000),
            top=_unchecked_i32_offset(y, 1_500_000),
            right=_unchecked_i32_offset(x, 1_000_000),
            bottom=min(_unchecked_i32_offset(y, 500_000), y),
        )
    return SchGeometryBounds(
        left=min(_unchecked_i32_offset(x, 500_000), x),
        top=_unchecked_i32_offset(y, 1_000_000),
        right=_unchecked_i32_offset(x, 1_500_000),
        bottom=_unchecked_i32_offset(y, -1_000_000),
    )


class AltiumSchHarnessSplice(_HarnessNamedPoint):
    _style_type = HarnessSpliceStyle
    _style_maximum = 1
    _connected_count_field = "ConnectedWiresUniqueIdsCount"
    _connected_item_prefix = "ConnectedWireUniqueId"
    _import_text_default = "SPL"

    def __init__(self) -> None:
        super().__init__()
        self.text = "SPL1"
        self.area_color = 16_777_215
        self.connected_inline_wire_unique_id = ""
        self._source_connected_inline_wire_unique_id = ("", False, False)
        self._capture_graphical_source_state()
        self._capture_label_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_SPLICE

    @property
    def connected_wires_unique_ids(self) -> tuple[str, ...]:
        return self.connected_unique_ids

    @connected_wires_unique_ids.setter
    def connected_wires_unique_ids(self, value: tuple[str, ...]) -> None:
        self.connected_unique_ids = tuple(value)

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        self.connected_inline_wire_unique_id, present, used_utf8 = _read_dynamic(
            self, serializer, record, "ConnectedObjectUniqueId"
        )
        self._source_connected_inline_wire_unique_id = (
            self.connected_inline_wire_unique_id,
            present,
            used_utf8,
        )

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        _write_dynamic(
            self,
            AltiumSerializer(self._detect_case_mode()),
            record,
            "ConnectedObjectUniqueId",
            self.connected_inline_wire_unique_id,
            self._source_connected_inline_wire_unique_id,
        )
        return self._order_authored_graphical_fields(
            record,
            self._authored_named_point_family_order(),
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
        child_records: Sequence[SchGeometryRecord] = (),
    ) -> SchGeometryRecord:
        """Build the managed circle or inline splice geometry."""
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        x, y = _harness_internal_location(self.location)
        operations = _harness_splice_operations(
            self,
            ctx,
            x=x,
            y=y,
            units_per_px=units_per_px,
        )
        operations.extend(_harness_bundle_child_operations(child_records))
        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_splice",
            object_id="eHarnessSplice",
            bounds=_harness_splice_bounds(self, x=x, y=y),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )


class AltiumSchHarnessLayoutConnectionPoint(_HarnessNamedPoint):
    _style_type = HarnessLayoutConnectionPointStyle
    _style_maximum = 2
    _connected_count_field = "ConnectedBundlesUniqueIdsCount"
    _connected_item_prefix = "ConnectedBundleUniqueId"
    _import_text_default = "CNT"

    def __init__(self) -> None:
        super().__init__()
        self.text = "CP1"
        self.show_name = True
        self.designator_locked = False
        self.connectors: list[HarnessLayoutConnectionPointConnector] = []
        self._capture_label_source_state()
        self._capture_named_point_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT

    @property
    def connected_bundles_unique_ids(self) -> tuple[str, ...]:
        return self.connected_unique_ids

    @connected_bundles_unique_ids.setter
    def connected_bundles_unique_ids(self, value: tuple[str, ...]) -> None:
        self.connected_unique_ids = tuple(value)

    def add_connector(
        self,
        connector_id: str,
        *,
        is_auto_assigned: bool = False,
    ) -> HarnessLayoutConnectionPointConnector:
        """Return the existing connector ID or append a new connector."""
        for connector in self.connectors:
            if connector.connector_id == connector_id:
                return connector
        connector = HarnessLayoutConnectionPointConnector(
            connector_id,
            is_auto_assigned=is_auto_assigned,
        )
        self.connectors.append(connector)
        return connector

    def remove_connector(self, connector_id: str) -> None:
        """Remove the first connector with the requested ID when present."""
        for index, connector in enumerate(self.connectors):
            if connector.connector_id == connector_id:
                del self.connectors[index]
                return

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
        document_bundles: (
            Iterable[AltiumSchHarnessBundle] | _HarnessConnectionBundleIndex
        ) = (),
        document_components: _HarnessComponentIndex | None = None,
        physical_model_active: bool = False,
        physical_model_record: SchGeometryRecord | None = None,
        physical_model_unique_id: str = "",
        physical_model_bounds: SchGeometryBounds | None = None,
        child_records: Sequence[SchGeometryRecord] = (),
    ) -> SchGeometryRecord:
        """Build the managed root connection-point geometry."""
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        x, y = _harness_internal_location(self.location)
        resolved_bundles: tuple[AltiumSchHarnessBundle, ...] = ()
        if (
            self.style is HarnessLayoutConnectionPointStyle.INSULATOR
            and self.connected_bundles_unique_ids
        ):
            bundle_index = (
                document_bundles
                if isinstance(document_bundles, _HarnessConnectionBundleIndex)
                else _HarnessConnectionBundleIndex(document_bundles)
            )
            resolved_bundles = bundle_index.resolve(self.connected_bundles_unique_ids)
        if not physical_model_active:
            operations = _harness_connection_point_operations(
                self,
                ctx,
                x=x,
                y=y,
                resolved_bundles=resolved_bundles,
                units_per_px=units_per_px,
            )
            bounds = _harness_connection_point_bounds(
                self,
                x=x,
                y=y,
                resolved_bundles=resolved_bundles,
            )
        else:
            from .altium_sch_geometry_oracle import SchGeometryOp

            model_operations = (
                _harness_bundle_child_operations((physical_model_record,))
                if physical_model_record is not None
                else []
            )
            operations = [SchGeometryOp.begin_group(physical_model_unique_id)]
            operations.extend(model_operations)
            operations.append(SchGeometryOp.end_group())
            bounds = physical_model_bounds or SchGeometryBounds(
                left=x - 5,
                top=y + 5,
                right=x + 5,
                bottom=y - 5,
            )
        operations.extend(
            _harness_connector_signal_line_operations(
                self,
                ctx,
                x=x,
                y=y,
                components=document_components,
                units_per_px=units_per_px,
            )
        )
        operations.extend(_harness_bundle_child_operations(child_records))
        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_layout_connection_point",
            object_id="eHarnessLayoutConnectionPoint",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )


def _float_to_i32(value: float, *, rounded: bool) -> int:
    if rounded:
        if not math.isfinite(value):
            return -(1 << 31)
        value = float(round(value))
    return _dotnet_conv_i4(value)


def _dotnet_conv_i4(value: float) -> int:
    if not math.isfinite(value):
        return -(1 << 31)
    converted = int(value)
    if converted < -(1 << 31) or converted > (1 << 31) - 1:
        return -(1 << 31)
    return converted


def _dotnet_conv_i8(value: float) -> int:
    if not math.isfinite(value):
        return -(1 << 63)
    converted = int(value)
    if converted < -(1 << 63) or converted > (1 << 63) - 1:
        return -(1 << 63)
    return converted


def _unchecked_i64(value: int) -> int:
    return ((int(value) + (1 << 63)) % (1 << 64)) - (1 << 63)


def _round_away_from_zero(value: float) -> int:
    if not math.isfinite(value):
        return _dotnet_conv_i4(value)
    # Adding 0.5 first can round a representable value just below a midpoint
    # up to that midpoint, unlike Math.Round(AwayFromZero).
    fraction, integral = math.modf(value)
    if abs(fraction) >= 0.5:
        integral += math.copysign(1.0, value)
    return _dotnet_conv_i4(integral)


def _abs_safe_i32(value: int) -> int:
    return (1 << 31) - 1 if value == -(1 << 31) else abs(value)


class AltiumSchHarnessBundle(AltiumSchWire):
    """Private V5 adapter for the record-111 harness-layout bundle."""

    def __init__(self) -> None:
        super().__init__()
        self.length = 0
        # Managed V5 initializes this inherited runtime state to solid but the
        # painter honors later editor mutations. Import/export never persists it.
        self.line_style = LineStyle.SOLID
        self.drawn_length = 0
        self._is_length_set_manually = False
        self.show_break_symbol = False
        self.old_length_value = 0
        self.end_vertex1_connected_connection_point_unique_id = ""
        self.end_vertex2_connected_connection_point_unique_id = ""
        self.designator_locked = False
        self.auto_wire = False
        self.editing_end_point = False
        self.is_highlighted = False
        self.use_custom_highlight_color = False
        self.custom_highlight_color = 0
        self._draw_disabled = False
        self._draw_dimmed = False
        self._draw_compilation_masked = False
        self._draw_editable_in_current_view = True
        self._compilation_masked_segments: dict[int, bool] = {}
        self._segment_crossovers: dict[int, tuple[tuple[int, int], ...]] = {}
        self._source_end_vertex1 = ("", False, False)
        self._source_end_vertex2 = ("", False, False)
        self._capture_bundle_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_BUNDLE

    @property
    def is_length_set_manually(self) -> bool:
        return self._is_length_set_manually

    @is_length_set_manually.setter
    def is_length_set_manually(self, value: bool) -> None:
        normalized = bool(value)
        if normalized != self._is_length_set_manually:
            self._is_length_set_manually = normalized
            self.show_break_symbol = normalized

    def is_end_vertex1_connected_to_any_connection_point(self) -> bool:
        return bool(self.end_vertex1_connected_connection_point_unique_id)

    def is_end_vertex2_connected_to_any_connection_point(self) -> bool:
        return bool(self.end_vertex2_connected_connection_point_unique_id)

    def highlight_bundle(self, highlight_color_bgr: int) -> None:
        """Apply the transient managed custom-highlight state."""
        self.is_highlighted = True
        self.custom_highlight_color = int(highlight_color_bgr) & 0xFFFFFF
        self.use_custom_highlight_color = True

    def remove_highlight(self) -> None:
        """Clear the transient managed highlight state."""
        self.use_custom_highlight_color = False
        self.is_highlighted = False

    def _set_compilation_masked_segment(self, index: int, value: bool) -> None:
        if index < 1:
            return
        if value:
            self._compilation_masked_segments[index] = True
        else:
            self._compilation_masked_segments.pop(index, None)

    def _get_compilation_masked_segment(self, index: int) -> bool:
        return self._compilation_masked_segments.get(index, False)

    def _set_segment_crossovers(
        self,
        segment_index: int,
        points: Iterable[tuple[int, int]],
    ) -> None:
        if segment_index < 1:
            return
        normalized = tuple(
            (_unchecked_i32(int(point[0])), _unchecked_i32(int(point[1])))
            for point in points
        )
        if normalized:
            self._segment_crossovers[segment_index] = normalized
        else:
            self._segment_crossovers.pop(segment_index, None)

    def _update_compilation_masked_segments(
        self,
        compile_mask_index: _HarnessCompileMaskIndex,
    ) -> None:
        state_index = 0
        for point1, point2 in zip(self.points, self.points[1:], strict=False):
            internal_point1 = _harness_internal_location(point1)
            internal_point2 = _harness_internal_location(point2)
            if internal_point1 == internal_point2:
                continue
            state_index += 1
            self._set_compilation_masked_segment(
                state_index,
                compile_mask_index.contains_segment(internal_point1, internal_point2),
            )

    def _capture_bundle_source_state(self) -> None:
        self._source_length = self.length
        self._source_drawn_length = self.drawn_length
        self._source_is_length_set_manually = self.is_length_set_manually
        self._source_show_break_symbol = self.show_break_symbol
        self._source_designator_locked = self.designator_locked

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self._compilation_masked_segments.clear()
        serializer = AltiumSerializer()
        self.is_length_set_manually = serializer.read_bool(
            record, "IsLengthSetManually", default=False
        )[0]
        self.show_break_symbol = serializer.read_bool(
            record, "ShowBreakSymbol", default=False
        )[0]
        whole, fraction, _ = serializer.read_coord(record, "Length")
        legacy_length = _unchecked_i32(whole * 100_000 + fraction)
        self.length = legacy_length
        self.old_length_value = legacy_length
        length_long, _ = serializer.read_long(record, "LengthLong", default=0)
        if length_long:
            self.length = length_long
        self.drawn_length = serializer.read_long(record, "DrawnLength", default=0)[0]
        self.end_vertex1_connected_connection_point_unique_id, present, used_utf8 = (
            _read_dynamic(self, serializer, record, "EndVertex1ConnectedObjectUniqueID")
        )
        self._source_end_vertex1 = (
            self.end_vertex1_connected_connection_point_unique_id,
            present,
            used_utf8,
        )
        self.end_vertex2_connected_connection_point_unique_id, present, used_utf8 = (
            _read_dynamic(self, serializer, record, "EndVertex2ConnectedObjectUniqueID")
        )
        self._source_end_vertex2 = (
            self.end_vertex2_connected_connection_point_unique_id,
            present,
            used_utf8,
        )
        self.designator_locked = serializer.read_bool(
            record, Fields.DESIGNATOR_LOCKED, default=False
        )[0]
        self._capture_bundle_source_state()

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        if self._raw_record is None or self.length != self._source_length:
            backward_length = (
                0 if self.length > 2_147_483_647 else _unchecked_i32(self.length)
            )
            whole, fraction = _split_coord_toward_zero(backward_length)
            self._serialize_managed_family_coord(
                record, serializer, "Length", "", whole, fraction
            )
        _write_sparse_bool(
            self,
            serializer,
            record,
            "IsLengthSetManually",
            self.is_length_set_manually,
            self._source_is_length_set_manually,
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            "ShowBreakSymbol",
            self.show_break_symbol,
            self._source_show_break_symbol,
        )
        _write_sparse_long(
            self,
            serializer,
            record,
            "LengthLong",
            self.length,
            self._source_length,
        )
        _write_sparse_long(
            self,
            serializer,
            record,
            "DrawnLength",
            self.drawn_length,
            self._source_drawn_length,
        )
        _write_dynamic(
            self,
            serializer,
            record,
            "EndVertex1ConnectedObjectUniqueID",
            self.end_vertex1_connected_connection_point_unique_id,
            self._source_end_vertex1,
        )
        _write_dynamic(
            self,
            serializer,
            record,
            "EndVertex2ConnectedObjectUniqueID",
            self.end_vertex2_connected_connection_point_unique_id,
            self._source_end_vertex2,
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.DESIGNATOR_LOCKED,
            self.designator_locked,
            self._source_designator_locked,
        )
        return self._order_authored_graphical_fields(
            record,
            (
                *self._family_order(),
                "Length",
                "Length_Frac",
                "IsLengthSetManually",
                "ShowBreakSymbol",
                "LengthLong",
                "DrawnLength",
                "EndVertex1ConnectedObjectUniqueID",
                "EndVertex2ConnectedObjectUniqueID",
                "DesignatorLocked",
            ),
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
        kind: str = "harness_bundle",
        object_id: str = "eHarnessBundle",
        default_color_raw: int = 0,
        stroke_width_mils_override: float | None = None,
        junction_color_raw: int = 0x000000,
        junction_size_px: float = 4.0,
        compile_mask_index: _HarnessCompileMaskIndex | None = None,
        is_metafile: bool = False,
        wire_placement_mode: str = "eLine90Start",
        child_records: Sequence[SchGeometryRecord] = (),
        emit_empty: bool = False,
    ) -> SchGeometryRecord | None:
        """Build the managed bundle root and first-level spatial children."""
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        vertices = tuple(_harness_internal_location(point) for point in self.points)
        root_operations, bounds = _harness_bundle_root_geometry(
            self,
            ctx,
            vertices=vertices,
            units_per_px=units_per_px,
            default_color_raw=default_color_raw,
            stroke_width_mils_override=stroke_width_mils_override,
            compile_mask_index=compile_mask_index,
            is_metafile=is_metafile,
            wire_placement_mode=wire_placement_mode,
        )
        del junction_color_raw, junction_size_px
        root_operations.extend(_harness_bundle_child_operations(child_records))
        if not vertices and not root_operations and not emit_empty:
            return None
        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind=kind,
            object_id=object_id,
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                root_operations,
                units_per_px=units_per_px,
            ),
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields


def _harness_bundle_root_geometry(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    units_per_px: int,
    default_color_raw: int,
    stroke_width_mils_override: float | None,
    compile_mask_index: _HarnessCompileMaskIndex | None,
    is_metafile: bool,
    wire_placement_mode: str,
) -> tuple[list[SchGeometryOp], SchGeometryBounds]:
    from .altium_sch_geometry_oracle import SchGeometryBounds

    if not vertices:
        if bundle.auto_wire:
            raise ValueError("auto-wire harness bundle requires at least one vertex")
        return [], SchGeometryBounds(left=0, top=0, right=0, bottom=0)
    if compile_mask_index is not None:
        bundle._update_compilation_masked_segments(compile_mask_index)
    geometry_points = [
        _harness_geometry_point(ctx, *point, units_per_px=units_per_px)
        for point in vertices
    ]
    pen_width = _harness_bundle_pen_width(
        bundle,
        ctx,
        units_per_px=units_per_px,
        stroke_width_mils_override=stroke_width_mils_override,
    )
    color_raw = int(bundle.color) if bundle.color is not None else default_color_raw
    color_raw = _harness_drawing_color(bundle, color_raw, ctx)
    operations = _harness_bundle_root_operations(
        bundle,
        ctx,
        vertices=vertices,
        geometry_points=geometry_points,
        color_raw=color_raw,
        pen_width=pen_width,
        units_per_px=units_per_px,
        is_metafile=is_metafile,
        wire_placement_mode=wire_placement_mode,
    )
    operations.extend(
        _harness_bundle_underline_operations(
            bundle,
            ctx,
            vertices=vertices,
            geometry_points=geometry_points,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
        )
    )
    return operations, _harness_bundle_bounds(bundle, vertices)


def _harness_bundle_pen_width(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    units_per_px: int,
    stroke_width_mils_override: float | None,
) -> float:
    from .altium_sch_geometry_oracle import _geometry_item_length

    if stroke_width_mils_override is not None:
        return _geometry_item_length(
            float(stroke_width_mils_override) * ctx.get_stroke_scale(),
            units_per_px=units_per_px,
        )
    return _connection_pen_width(
        ctx,
        _BUS_LINE_WIDTH_INTERNAL[bundle.line_width],
        units_per_px,
    )


def _harness_bundle_child_operations(
    child_records: Sequence[SchGeometryRecord],
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, unwrap_record_operations

    operations: list[SchGeometryOp] = []
    for child_record in child_records:
        child_operations = unwrap_record_operations(
            child_record,
            unique_id=child_record.unique_id,
        )
        child_operations = [
            operation
            for operation in child_operations
            if operation.payload.get("transparent_back") is not True
        ]
        operations.append(
            SchGeometryOp.begin_group(
                child_record.unique_id,
                render_group_id=child_record.render_group_id,
                render_group_identity=child_record.render_group_identity,
                render_source_id=child_record.render_source_id,
            )
        )
        operations.extend(child_operations)
        operations.append(SchGeometryOp.end_group())
    return operations


def _harness_bundle_bounds(
    bundle: AltiumSchHarnessBundle,
    vertices: tuple[tuple[int, int], ...],
) -> SchGeometryBounds:
    from .altium_sch_geometry_oracle import SchGeometryBounds

    left = min(point[0] for point in vertices)
    top = max(point[1] for point in vertices)
    right = max(point[0] for point in vertices)
    bottom = min(point[1] for point in vertices)
    if len(vertices) > 1:
        inflate = _SYMBOL_LINE_WIDTH_INTERNAL[bundle.line_width]
        left = _unchecked_i32_offset(left, -inflate)
        top = _unchecked_i32_offset(top, inflate)
        right = _unchecked_i32_offset(right, inflate)
        bottom = _unchecked_i32_offset(bottom, -inflate)
    return SchGeometryBounds(left=left, top=top, right=right, bottom=bottom)


@dataclass(frozen=True, slots=True)
class _HarnessCompileMaskNode:
    bounds: tuple[int, int, int, int]
    masks: tuple[tuple[int, int, int, int], ...] = ()
    left: _HarnessCompileMaskNode | None = None
    right: _HarnessCompileMaskNode | None = None


class _HarnessCompileMaskIndex:
    """Bounded BVH for managed strict segment-containment state updates."""

    _LEAF_SIZE = 8

    def __init__(
        self,
        compile_masks: Iterable[tuple[int, int, int, int]],
        *,
        max_masks: int = _MAX_HARNESS_COMPILE_MASKS,
        max_candidate_visits: int = _MAX_HARNESS_COMPILE_MASK_CANDIDATE_VISITS,
    ) -> None:
        if max_masks < 0 or max_candidate_visits < 0:
            raise ValueError("harness compile-mask limits cannot be negative")
        masks: list[tuple[int, int, int, int]] = []
        for bounds in compile_masks:
            if len(masks) >= max_masks:
                raise ValueError(
                    "harness compile-mask state exceeds the document limit "
                    f"of {max_masks} masks"
                )
            x1, y1, x2, y2 = bounds
            masks.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
        self.mask_count = len(masks)
        self.max_candidate_visits = max_candidate_visits
        self.candidate_visit_count = 0
        self.last_candidate_visit_count = 0
        self._remaining_candidate_visits = max_candidate_visits
        self._root = self._build_node(tuple(masks))

    @classmethod
    def _build_node(
        cls,
        masks: tuple[tuple[int, int, int, int], ...],
    ) -> _HarnessCompileMaskNode | None:
        if not masks:
            return None
        bounds = (
            min(mask[0] for mask in masks),
            min(mask[1] for mask in masks),
            max(mask[2] for mask in masks),
            max(mask[3] for mask in masks),
        )
        if len(masks) <= cls._LEAF_SIZE:
            return _HarnessCompileMaskNode(bounds=bounds, masks=masks)
        axis = 0 if bounds[2] - bounds[0] >= bounds[3] - bounds[1] else 1
        ordered = tuple(
            sorted(masks, key=lambda mask: (mask[axis] + mask[axis + 2], mask))
        )
        middle = len(ordered) // 2
        return _HarnessCompileMaskNode(
            bounds=bounds,
            left=cls._build_node(ordered[:middle]),
            right=cls._build_node(ordered[middle:]),
        )

    @staticmethod
    def _strictly_contains(
        container: tuple[int, int, int, int],
        contained: tuple[int, int, int, int],
    ) -> bool:
        return (
            container[0] < contained[0]
            and container[1] < contained[1]
            and container[2] > contained[2]
            and container[3] > contained[3]
        )

    def _charge_candidate(self) -> None:
        self.last_candidate_visit_count += 1
        self.candidate_visit_count += 1
        self._remaining_candidate_visits -= 1
        if self._remaining_candidate_visits < 0:
            raise ValueError(
                "harness compile-mask state exceeds the document limit of "
                f"{self.max_candidate_visits} candidate-mask visits"
            )

    def contains_segment(
        self,
        point1: tuple[int, int],
        point2: tuple[int, int],
    ) -> bool:
        segment_bounds = (
            min(point1[0], point2[0]),
            min(point1[1], point2[1]),
            max(point1[0], point2[0]),
            max(point1[1], point2[1]),
        )
        self.last_candidate_visit_count = 0
        pending = [self._root] if self._root is not None else []
        while pending:
            node = pending.pop()
            if not self._strictly_contains(node.bounds, segment_bounds):
                continue
            for mask in node.masks:
                self._charge_candidate()
                if self._strictly_contains(mask, segment_bounds):
                    return True
            if node.left is not None:
                pending.append(node.left)
            if node.right is not None:
                pending.append(node.right)
        return False


def _harness_bundle_pen(
    color_raw: int,
    pen_width: float,
    *,
    masked: bool,
    ctx: SchSvgRenderContext,
    dash_style: str = "pdsSolid",
    alpha: int = 0xFF,
) -> dict[str, object]:
    from .altium_sch_geometry_oracle import make_pen

    color_raw = _harness_bundle_color(color_raw, masked=masked, ctx=ctx)
    pen = make_pen(color_raw, width=pen_width, dash_style=dash_style)
    if alpha != 0xFF:
        pen["color_raw"] = _unchecked_i32(
            ((alpha & 0xFF) << 24) | (int(pen["color_raw"]) & 0xFFFFFF)
        )
    return pen


def _harness_bundle_color(
    color_raw: int,
    *,
    masked: bool,
    ctx: SchSvgRenderContext,
) -> int:
    from .altium_sch_svg_renderer import modify_color

    if masked:
        return modify_color(50, 0x808080, ctx.sheet_area_color)
    return color_raw


class _DrawingColorObject(Protocol):
    @property
    def _draw_disabled(self) -> bool: ...

    @property
    def _draw_dimmed(self) -> bool: ...

    @property
    def _draw_compilation_masked(self) -> bool: ...

    @property
    def _draw_editable_in_current_view(self) -> bool: ...


def _harness_drawing_color(
    graphical_object: _DrawingColorObject,
    color_raw: int,
    ctx: SchSvgRenderContext,
) -> int:
    from .altium_sch_svg_renderer import modify_color

    blend_level = -1
    if graphical_object._draw_disabled:
        blend_level = ctx.filtered_objects_blend
    elif graphical_object._draw_dimmed:
        blend_level = ctx.af_dim_level

    result = color_raw
    if blend_level > 0:
        result = modify_color(blend_level, result, ctx.sheet_area_color)
    elif graphical_object._draw_compilation_masked:
        result = modify_color(50, 0x808080, ctx.sheet_area_color)

    if graphical_object._draw_editable_in_current_view:
        return result
    if ctx.document_is_dimmed or ctx.document_is_masked:
        return result
    if ctx.physical_view_dim_level > 0:
        result = _harness_state_gray_level(result)
    return modify_color(
        ctx.physical_view_dim_level,
        result,
        ctx.sheet_area_color,
    )


def _harness_state_gray_level(color_raw: int) -> int:
    red = color_raw & 0xFF
    green = (color_raw >> 8) & 0xFF
    blue = (color_raw >> 16) & 0xFF
    level = round(red * 0.3) + round(green * 0.59) + round(blue * 0.11)
    return (level << 16) | (level << 8) | level


def _harness_bundle_highlight_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    geometry_points: list[tuple[float, float]],
    pen_width: float,
    dash_style: str,
    units_per_px: int,
    is_metafile: bool,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    if not bundle.is_highlighted or is_metafile:
        return []
    outer_color = (
        bundle.custom_highlight_color if bundle.use_custom_highlight_color else 0x00FF00
    )
    outer_width = pen_width + _connection_pen_width(
        ctx,
        _SYMBOL_LINE_WIDTH_INTERNAL[LineWidth.SMALL] * 3,
        units_per_px,
    )
    inner_width = pen_width + _connection_pen_width(
        ctx,
        _SYMBOL_LINE_WIDTH_INTERNAL[LineWidth.SMALL] * 3 // 2,
        units_per_px,
    )
    inner_pen = make_pen(0xFFFFFF, width=inner_width, dash_style=dash_style)
    inner_pen["color_raw"] = _unchecked_i32(
        (200 << 24) | (int(inner_pen["color_raw"]) & 0xFFFFFF)
    )
    return [
        SchGeometryOp.lines(
            geometry_points,
            pen=make_pen(outer_color, width=outer_width, dash_style=dash_style),
        ),
        SchGeometryOp.lines(geometry_points, pen=inner_pen),
    ]


@dataclass(slots=True)
class _HarnessLongestSegmentCache:
    resolved: bool = False
    value: tuple[int, int] | None = None

    def get(
        self,
        vertices: tuple[tuple[int, int], ...],
    ) -> tuple[int, int] | None:
        if not self.resolved:
            self.value = _harness_longest_segment(vertices)
            self.resolved = True
        return self.value


def _harness_bundle_normal_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    pen_width: float,
    units_per_px: int,
    is_metafile: bool,
) -> list[SchGeometryOp]:
    if len(bundle.points) < 2:
        return []
    operations: list[SchGeometryOp] = []
    dash_style = _HARNESS_LINE_DASH_STYLE[bundle.line_style]
    longest_segment_cache = _HarnessLongestSegmentCache()
    run_start = 0
    run_masked = bundle._get_compilation_masked_segment(1)
    for segment_index in range(1, len(bundle.points) - 1):
        segment_masked = bundle._get_compilation_masked_segment(segment_index + 1)
        if segment_masked == run_masked:
            continue
        operations.extend(
            _harness_bundle_run_operations(
                bundle,
                ctx,
                vertices=vertices,
                run_vertices=vertices[run_start : segment_index + 1],
                geometry_points=geometry_points[run_start : segment_index + 1],
                first_segment_index=run_start + 1,
                color_raw=color_raw,
                pen_width=pen_width,
                masked=run_masked,
                units_per_px=units_per_px,
                is_metafile=is_metafile,
                longest_segment_cache=longest_segment_cache,
                dash_style=dash_style,
            )
        )
        run_start = segment_index
        run_masked = segment_masked
    final_internal_points = vertices if run_start == 0 else vertices[run_start:]
    final_geometry_points = (
        geometry_points if run_start == 0 else geometry_points[run_start:]
    )
    operations.extend(
        _harness_bundle_run_operations(
            bundle,
            ctx,
            vertices=vertices,
            run_vertices=final_internal_points,
            geometry_points=final_geometry_points,
            first_segment_index=run_start + 1,
            color_raw=color_raw,
            pen_width=pen_width,
            masked=run_masked,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
            longest_segment_cache=longest_segment_cache,
            dash_style=dash_style,
        )
    )
    return operations


def _harness_bundle_auto_wire_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    pen_width: float,
    units_per_px: int,
    is_metafile: bool,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp

    vertex_count = len(geometry_points)
    if vertex_count == 1:
        return []
    dotted_pen = _harness_bundle_pen(
        color_raw,
        pen_width,
        masked=False,
        ctx=ctx,
        dash_style="pdsDot",
    )
    if vertex_count == 2:
        if is_metafile:
            return _harness_metafile_line_operations(
                ctx,
                point1=vertices[0],
                point2=vertices[1],
                color_raw=color_raw,
                line_width_internal=_BUS_LINE_WIDTH_INTERNAL[bundle.line_width],
                line_style=LineStyle.DOTTED,
                units_per_px=units_per_px,
            )
        return [SchGeometryOp.lines(geometry_points, pen=dotted_pen)]
    solid_points = geometry_points[: vertex_count - 2]
    operations: list[SchGeometryOp] = []
    line_dash_style = _HARNESS_LINE_DASH_STYLE[bundle.line_style]
    if len(solid_points) > 1:
        operations.extend(
            _harness_bundle_highlight_operations(
                bundle,
                ctx,
                geometry_points=solid_points,
                pen_width=pen_width,
                dash_style=line_dash_style,
                units_per_px=units_per_px,
                is_metafile=is_metafile,
            )
        )
        operations.append(
            SchGeometryOp.lines(
                solid_points,
                pen=_harness_bundle_pen(
                    color_raw,
                    pen_width,
                    masked=False,
                    ctx=ctx,
                    dash_style=line_dash_style,
                ),
            )
        )
    dotted_points = geometry_points[vertex_count - 3 : vertex_count - 1]
    operations.extend(
        _harness_bundle_highlight_operations(
            bundle,
            ctx,
            geometry_points=dotted_points,
            pen_width=pen_width,
            dash_style="pdsDot",
            units_per_px=units_per_px,
            is_metafile=is_metafile,
        )
    )
    operations.append(SchGeometryOp.lines(dotted_points, pen=dotted_pen))
    return operations


def _harness_bundle_basic_polyline_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    units_per_px: int,
    is_metafile: bool,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    if is_metafile and bundle.line_style is not LineStyle.SOLID:
        operations: list[SchGeometryOp] = []
        for point1, point2 in zip(vertices, vertices[1:], strict=False):
            operations.extend(
                _harness_metafile_line_operations(
                    ctx,
                    point1=point1,
                    point2=point2,
                    color_raw=color_raw,
                    line_width_internal=_SYMBOL_LINE_WIDTH_INTERNAL[bundle.line_width],
                    line_style=bundle.line_style,
                    units_per_px=units_per_px,
                )
            )
        return operations

    geometry_points = [(_f32(x), _f32(y)) for x, y in geometry_points]
    pen = make_pen(
        color_raw,
        width=_f32(
            _connection_pen_width(
                ctx,
                _SYMBOL_LINE_WIDTH_INTERNAL[bundle.line_width],
                units_per_px,
            )
        ),
        dash_style=_HARNESS_LINE_DASH_STYLE[bundle.line_style],
    )
    return [
        SchGeometryOp.lines([point1, point2], pen=pen)
        for point1, point2 in zip(
            geometry_points,
            geometry_points[1:],
            strict=False,
        )
    ]


def _harness_metafile_dash_operations(
    ctx: SchSvgRenderContext,
    *,
    point1: tuple[int, int],
    point2: tuple[int, int],
    color_raw: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    return _harness_metafile_line_operations(
        ctx,
        point1=point1,
        point2=point2,
        color_raw=color_raw,
        line_width_internal=1,
        line_style=LineStyle.DASHED,
        units_per_px=units_per_px,
    )


def _harness_metafile_line_operations(
    ctx: SchSvgRenderContext,
    *,
    point1: tuple[int, int],
    point2: tuple[int, int],
    color_raw: int,
    line_width_internal: int,
    line_style: LineStyle,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    delta_x = float(point2[0]) - float(point1[0])
    delta_y = float(point2[1]) - float(point1[1])
    length = math.sqrt(delta_x * delta_x + delta_y * delta_y)
    period = float(line_width_internal) if line_width_internal > 1 else 100_000.0
    divisor = {
        LineStyle.DASHED: 5.0,
        LineStyle.DOTTED: 2.0,
        LineStyle.DASH_DOT: 7.0,
    }.get(line_style)
    if divisor is None:
        raise ValueError(f"unsupported metafile line style {line_style!r}")
    dash_count = int(length / period / divisor)
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    current_x = float(point1[0])
    current_y = float(point1[1])
    if dash_count > 0:
        step_x = delta_x / dash_count
        step_y = delta_y / dash_count
        if line_style is LineStyle.DASHED:
            for _ in range(dash_count):
                segments.append(
                    (
                        (current_x, current_y),
                        (current_x + step_x / 1.6, current_y + step_y / 1.6),
                    )
                )
                current_x += step_x
                current_y += step_y
        elif line_style is LineStyle.DOTTED:
            for _ in range(dash_count):
                segments.append(
                    (
                        (current_x, current_y),
                        (current_x + step_x / 100.0, current_y + step_y / 100.0),
                    )
                )
                current_x += step_x
                current_y += step_y
        else:
            for _ in range(dash_count):
                segments.append(
                    (
                        (current_x, current_y),
                        (current_x + step_x / 2.0, current_y + step_y / 2.0),
                    )
                )
                dot_x = current_x + step_x / 2.0 + step_x / 4.0
                dot_y = current_y + step_y / 2.0 + step_y / 4.0
                segments.append(
                    (
                        (dot_x, dot_y),
                        (dot_x + step_x / 100.0, dot_y + step_y / 100.0),
                    )
                )
                current_x += step_x
                current_y += step_y
    if (
        line_style is not LineStyle.DOTTED
        and current_x <= point2[0]
        and current_y <= point2[1]
    ):
        segments.append(((current_x, current_y), (float(point2[0]), float(point2[1]))))

    pen = make_pen(
        color_raw,
        width=_f32(_connection_pen_width(ctx, line_width_internal, units_per_px)),
    )
    return [
        SchGeometryOp.lines(
            [
                tuple(
                    _f32(value)
                    for value in _harness_geometry_point(
                        ctx,
                        *segment_point,
                        units_per_px=units_per_px,
                    )
                )
                for segment_point in segment
            ],
            pen=pen,
        )
        for segment in segments
    ]


def _harness_bundle_look_ahead_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    units_per_px: int,
    is_metafile: bool,
    wire_placement_mode: str,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    if len(vertices) <= 2 or wire_placement_mode not in _HARNESS_LOOK_AHEAD_MODES:
        return _harness_bundle_basic_polyline_operations(
            bundle,
            ctx,
            vertices=vertices,
            geometry_points=geometry_points,
            color_raw=color_raw,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
        )

    geometry_points = [(_f32(x), _f32(y)) for x, y in geometry_points]
    solid_internal_points = vertices[: len(vertices) - 2]
    solid_geometry_points = geometry_points[: len(vertices) - 2]
    ordinary_width = _f32(
        _connection_pen_width(
            ctx,
            _SYMBOL_LINE_WIDTH_INTERNAL[bundle.line_width],
            units_per_px,
        )
    )
    operations: list[SchGeometryOp] = []
    if len(solid_geometry_points) > 1:
        operations.extend(
            _harness_bundle_polyline_operations(
                bundle,
                ctx,
                vertices=vertices,
                internal_points=solid_internal_points,
                geometry_points=solid_geometry_points,
                color_raw=color_raw,
                pen_width=ordinary_width,
                masked=False,
                units_per_px=units_per_px,
                is_metafile=is_metafile,
                longest_segment_cache=_HarnessLongestSegmentCache(),
                dash_style=_HARNESS_LINE_DASH_STYLE[bundle.line_style],
            )
        )

    join_points = geometry_points[len(vertices) - 3 : len(vertices) - 1]
    operations.append(
        SchGeometryOp.lines(
            join_points,
            pen=make_pen(color_raw),
        )
    )
    if wire_placement_mode in _HARNESS_CONSTRAINED_LOOK_AHEAD_MODES:
        if is_metafile:
            operations.extend(
                _harness_metafile_dash_operations(
                    ctx,
                    point1=vertices[-2],
                    point2=vertices[-1],
                    color_raw=color_raw,
                    units_per_px=units_per_px,
                )
            )
        else:
            operations.append(
                SchGeometryOp.lines(
                    geometry_points[len(vertices) - 2 :],
                    pen=make_pen(
                        color_raw,
                        width=_f32(_connection_pen_width(ctx, 1, units_per_px)),
                        dash_style="pdsDash",
                    ),
                )
            )
    return operations


def _harness_bundle_root_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    pen_width: float,
    units_per_px: int,
    is_metafile: bool,
    wire_placement_mode: str,
) -> list[SchGeometryOp]:
    if bundle.auto_wire:
        return _harness_bundle_auto_wire_operations(
            bundle,
            ctx,
            vertices=vertices,
            geometry_points=geometry_points,
            color_raw=color_raw,
            pen_width=pen_width,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
        )
    if len(vertices) != 2 and bundle.editing_end_point:
        return _harness_bundle_look_ahead_operations(
            bundle,
            ctx,
            vertices=vertices,
            geometry_points=geometry_points,
            color_raw=color_raw,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
            wire_placement_mode=wire_placement_mode,
        )
    return _harness_bundle_normal_operations(
        bundle,
        ctx,
        vertices=vertices,
        geometry_points=geometry_points,
        color_raw=color_raw,
        pen_width=pen_width,
        units_per_px=units_per_px,
        is_metafile=is_metafile,
    )


@dataclass(frozen=True, slots=True)
class _HarnessLineEquation:
    a: float
    b: float
    c: float


def _harness_longest_segment(
    vertices: tuple[tuple[int, int], ...],
) -> tuple[int, int] | None:
    longest_index = 0
    longest_squared = 0.0
    for index, (point1, point2) in enumerate(
        zip(vertices, vertices[1:], strict=False),
        start=1,
    ):
        delta_x = _unchecked_i32(point2[0] - point1[0])
        delta_y = _unchecked_i32(point2[1] - point1[1])
        squared = float(delta_x) * float(delta_x) + float(delta_y) * float(delta_y)
        if squared > longest_squared:
            longest_index = index
            longest_squared = squared
    if longest_index == 0:
        return None
    return longest_index, _round_away_from_zero(math.sqrt(longest_squared))


def _harness_line_equation(
    point1: tuple[int, int],
    point2: tuple[int, int],
) -> _HarnessLineEquation:
    x1, y1 = point1
    x2, y2 = point2
    if x1 == x2:
        return _HarnessLineEquation(1.0, 0.0, float(_unchecked_i32(-x1)))
    if y1 == y2:
        return _HarnessLineEquation(0.0, 1.0, float(_unchecked_i32(-y1)))
    slope = float(_unchecked_i32(y1 - y2)) / float(_unchecked_i32(x1 - x2))
    return _HarnessLineEquation(slope, -1.0, float(y1) - slope * float(x1))


def _harness_parallel_line(
    point: tuple[int, int],
    line: _HarnessLineEquation,
) -> _HarnessLineEquation:
    return _HarnessLineEquation(
        line.a,
        line.b,
        -(line.a * float(point[0]) + line.b * float(point[1])),
    )


def _harness_perpendicular_line(
    point: tuple[int, int],
    line: _HarnessLineEquation,
) -> _HarnessLineEquation:
    if line.a == 0.0:
        return _HarnessLineEquation(
            1.0,
            0.0,
            float(_unchecked_i32(-point[0])),
        )
    if line.b == 0.0:
        return _HarnessLineEquation(
            0.0,
            1.0,
            float(_unchecked_i32(-point[1])),
        )
    slope = -1.0 / line.a
    return _HarnessLineEquation(
        slope,
        -1.0,
        float(point[1]) - slope * float(point[0]),
    )


def _harness_points_moved_from_center(
    center: tuple[int, int],
    line: _HarnessLineEquation,
    vector_length: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    x, y = center
    if line.a == 0.0:
        return (
            (_unchecked_i32(x - vector_length), y),
            (_unchecked_i32(x + vector_length), y),
        )
    if line.b == 0.0:
        return (
            (x, _unchecked_i32(y - vector_length)),
            (x, _unchecked_i32(y + vector_length)),
        )
    quadratic_a = 1.0 + line.a**2
    quadratic_b = 2.0 * (line.a * (line.c - float(y)) - float(x))
    quadratic_c = float(x) ** 2 + (line.c - float(y)) ** 2 - vector_length**2
    discriminant = quadratic_b**2 - 4.0 * quadratic_a * quadratic_c
    root = math.sqrt(discriminant) if discriminant >= 0.0 else math.nan
    x1 = _round_away_from_zero(-(quadratic_b + root) / (2.0 * quadratic_a))
    x2 = _round_away_from_zero(-(quadratic_b - root) / (2.0 * quadratic_a))
    y1 = _round_away_from_zero(line.a * float(x1) + line.c)
    y2 = _round_away_from_zero(line.a * float(x2) + line.c)
    return (
        (_unchecked_i32(x1), _unchecked_i32(y1)),
        (_unchecked_i32(x2), _unchecked_i32(y2)),
    )


def _harness_arc_start_angle(
    vector_x: int,
    vector_y: int,
    line: _HarnessLineEquation,
) -> float:
    if line.a == 0.0:
        return 270.0 if vector_x >= 0 else 90.0
    if line.b == 0.0:
        return 180.0 if vector_y >= 0 else 0.0
    angle = math.degrees(math.atan(line.a))
    return 270.0 - angle if vector_x >= 0 else 90.0 - angle


def _harness_arc_points(
    center: tuple[int, int],
    radius_x: int,
    radius_y: int,
    start_angle: float,
    sweep_angle: float,
) -> tuple[tuple[int, int], ...]:
    step_count = int(round(0.0001 + 72.0 * sweep_angle * 0.5 / math.pi)) + 1
    angle = math.radians(start_angle)
    angle_step = math.radians(sweep_angle / float(step_count))
    points: list[tuple[int, int]] = []
    for _ in range(step_count + 1):
        points.append(
            (
                _unchecked_i32(
                    _round_away_from_zero(
                        float(center[0]) + math.cos(angle) * float(radius_x)
                    )
                ),
                _unchecked_i32(
                    _round_away_from_zero(
                        float(center[1]) - math.sin(angle) * float(radius_y)
                    )
                ),
            )
        )
        angle += angle_step
    return tuple(points)


def _harness_closest_point_index_to_line(
    points: tuple[tuple[int, int], ...],
    line: _HarnessLineEquation,
) -> int:
    divisor = math.sqrt(line.a**2 + line.b**2)

    def distance(point: tuple[int, int]) -> float:
        return abs(line.a * point[0] + line.b * point[1] + line.c) / divisor

    return min(range(len(points)), key=lambda index: distance(points[index]))


def _harness_distance(point1: tuple[int, int], point2: tuple[int, int]) -> int:
    delta_x = _f32(_unchecked_i32(point1[0] - point2[0]))
    delta_y = _f32(_unchecked_i32(point1[1] - point2[1]))
    squared = _f32(_f32(delta_x * delta_x) + _f32(delta_y * delta_y))
    distance = math.sqrt(squared)
    return _dotnet_conv_i4(float(round(distance)))


def _harness_manual_half_edge_points(
    break_point: tuple[int, int],
    break_indicator_point: tuple[int, int],
    line: _HarnessLineEquation,
    indicator_half_height: int,
    arc_start_angle: float,
) -> tuple[tuple[int, int], ...]:
    arc_points = _harness_arc_points(
        break_indicator_point,
        indicator_half_height,
        indicator_half_height,
        arc_start_angle,
        180.0,
    )
    closest_index = _harness_closest_point_index_to_line(arc_points, line)
    closest_point = arc_points[closest_index]
    trim = min(closest_index, len(arc_points) - closest_index - 1)
    offset_x = _unchecked_i32(break_point[0] - closest_point[0])
    offset_y = _unchecked_i32(break_point[1] - closest_point[1])
    return tuple(
        (
            _unchecked_i32(point[0] + offset_x),
            _unchecked_i32(point[1] + offset_y),
        )
        for point in arc_points[trim : len(arc_points) - trim]
    )


def _harness_manual_edge_points(
    break_point: tuple[int, int],
    break_indicator_point1: tuple[int, int],
    break_indicator_point2: tuple[int, int],
    line: _HarnessLineEquation,
    indicator_half_height: int,
    arc_start_angle1: float,
    arc_start_angle2: float,
    *,
    invert_indicator: bool,
) -> tuple[tuple[int, int], ...]:
    if invert_indicator:
        arc_start_angle1, arc_start_angle2 = arc_start_angle2, arc_start_angle1
    first = _harness_manual_half_edge_points(
        break_point,
        break_indicator_point1,
        line,
        indicator_half_height,
        arc_start_angle1,
    )
    second = _harness_manual_half_edge_points(
        break_point,
        break_indicator_point2,
        line,
        indicator_half_height,
        arc_start_angle2,
    )
    if first[0] == second[0]:
        first = tuple(reversed(first))
    if first[-1] == second[-1]:
        second = tuple(reversed(second))
    if first[-1] != second[0]:
        raise ValueError("managed harness break indicator edges do not join")
    return first + second[1:]


def _harness_manual_segment_end_points(
    break_margin_point1: tuple[int, int],
    break_margin_point2: tuple[int, int],
    line: _HarnessLineEquation,
    indicator_edge_points: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    index1 = _harness_closest_point_index_to_line(
        indicator_edge_points,
        _harness_parallel_line(break_margin_point1, line),
    )
    index2 = _harness_closest_point_index_to_line(
        indicator_edge_points,
        _harness_parallel_line(break_margin_point2, line),
    )
    start = min(index1, index2)
    stop = max(index1, index2) + 1
    return indicator_edge_points[start:stop]


def _harness_manual_segment_part(
    segment_vertex: tuple[int, int],
    segment_break_point: tuple[int, int],
    line: _HarnessLineEquation,
    indicator_edge_points: tuple[tuple[int, int], ...],
    segment_half_width: int,
    arc_start_angle: float,
    segment_margin_vector: tuple[int, int],
) -> tuple[tuple[int, int], ...]:
    margin_point1 = (
        _unchecked_i32(segment_break_point[0] - segment_margin_vector[0]),
        _unchecked_i32(segment_break_point[1] - segment_margin_vector[1]),
    )
    margin_point2 = (
        _unchecked_i32(segment_break_point[0] + segment_margin_vector[0]),
        _unchecked_i32(segment_break_point[1] + segment_margin_vector[1]),
    )
    segment_cap = _harness_arc_points(
        segment_vertex,
        segment_half_width,
        segment_half_width,
        arc_start_angle,
        180.0,
    )
    segment_end = _harness_manual_segment_end_points(
        margin_point1,
        margin_point2,
        line,
        indicator_edge_points,
    )
    cap_delta = (
        _unchecked_i32(segment_cap[0][0] - segment_cap[-1][0]),
        _unchecked_i32(segment_cap[0][1] - segment_cap[-1][1]),
    )
    margin_delta = (
        _unchecked_i32(margin_point1[0] - margin_point2[0]),
        _unchecked_i32(margin_point1[1] - margin_point2[1]),
    )
    target = margin_point2
    if (
        abs(cap_delta[0] - margin_delta[0]) > 100
        or abs(cap_delta[1] - margin_delta[1]) > 100
    ):
        target = margin_point1
    if _harness_distance(segment_end[0], target) > _harness_distance(
        segment_end[-1], target
    ):
        segment_end = tuple(reversed(segment_end))
    return segment_cap + segment_end


def _harness_manual_wave(
    indicator_edge_points: tuple[tuple[int, int], ...],
    break_point: tuple[int, int],
    reference_break_point: tuple[int, int],
    line: _HarnessLineEquation,
    indicator_wave_width: int,
    *,
    invert_vectors: bool,
) -> tuple[tuple[int, int], ...]:
    vector_points = _harness_points_moved_from_center(
        break_point,
        line,
        indicator_wave_width,
    )
    nearer = (
        vector_points[0]
        if _harness_distance(reference_break_point, vector_points[0])
        < _harness_distance(reference_break_point, vector_points[1])
        else vector_points[1]
    )
    vector_x = _unchecked_i32(break_point[0] - nearer[0])
    vector_y = _unchecked_i32(break_point[1] - nearer[1])
    if invert_vectors:
        vector_x = _unchecked_i32(-vector_x)
        vector_y = _unchecked_i32(-vector_y)
    shifted = tuple(
        (
            _unchecked_i32(point[0] + vector_x),
            _unchecked_i32(point[1] + vector_y),
        )
        for point in indicator_edge_points
    )
    perpendicular_points = _harness_points_moved_from_center(
        indicator_edge_points[0],
        _harness_perpendicular_line(indicator_edge_points[0], line),
        indicator_wave_width // 2,
    )
    reference = (
        perpendicular_points[0]
        if _harness_distance(break_point, perpendicular_points[0])
        < _harness_distance(break_point, perpendicular_points[1])
        else perpendicular_points[1]
    )
    count = _harness_closest_point_index_to_line(
        indicator_edge_points,
        _harness_parallel_line(reference, line),
    )
    return indicator_edge_points[count:] + tuple(reversed(shifted))[count:]


def _harness_geometry_polygon(
    ctx: SchSvgRenderContext,
    points: tuple[tuple[int, int], ...],
    *,
    units_per_px: int,
) -> list[tuple[float, float]]:
    return [
        _harness_geometry_point(ctx, *point, units_per_px=units_per_px)
        for point in points
    ]


def _harness_manual_indicator_operations(
    ctx: SchSvgRenderContext,
    *,
    segment_vertex1: tuple[int, int],
    segment_vertex2: tuple[int, int],
    line_width: LineWidth,
    indicator_width: int,
    segment_color_raw: int,
    units_per_px: int,
    segment_alpha: int = 0xFF,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import (
        SchGeometryOp,
        make_solid_brush,
    )

    delta_x = _unchecked_i32(segment_vertex1[0] - segment_vertex2[0])
    delta_y = _unchecked_i32(segment_vertex1[1] - segment_vertex2[1])
    if delta_x == -(1 << 31) or delta_y == -(1 << 31):
        raise OverflowError("managed harness break indicator midpoint overflow")
    center = (
        _unchecked_i32(min(segment_vertex1[0], segment_vertex2[0]) + abs(delta_x) // 2),
        _unchecked_i32(min(segment_vertex1[1], segment_vertex2[1]) + abs(delta_y) // 2),
    )
    line = _harness_line_equation(segment_vertex1, segment_vertex2)
    break_point1, break_point2 = _harness_points_moved_from_center(
        center,
        line,
        indicator_width // 2,
    )
    if _harness_distance(segment_vertex1, break_point1) > _harness_distance(
        segment_vertex1,
        break_point2,
    ):
        break_point1, break_point2 = break_point2, break_point1
    arc_start1 = _harness_arc_start_angle(
        _unchecked_i32(segment_vertex1[0] - break_point1[0]),
        _unchecked_i32(segment_vertex1[1] - break_point1[1]),
        line,
    )
    arc_start2 = _harness_arc_start_angle(
        _unchecked_i32(segment_vertex2[0] - break_point2[0]),
        _unchecked_i32(segment_vertex2[1] - break_point2[1]),
        line,
    )
    segment_half_width = _BUS_LINE_WIDTH_INTERNAL[line_width] // 2
    perpendicular = _harness_perpendicular_line(center, line)
    margin_points = _harness_points_moved_from_center(
        center,
        perpendicular,
        segment_half_width,
    )
    margin_vector = (
        _unchecked_i32(center[0] - margin_points[0][0]),
        _unchecked_i32(center[1] - margin_points[0][1]),
    )
    indicator_half_height = (
        _round_away_from_zero(_BUS_LINE_WIDTH_INTERNAL[line_width] * 1.5) // 2
    )
    vector_points = _harness_points_moved_from_center(
        center,
        perpendicular,
        indicator_half_height // 2,
    )
    indicator_vector = (
        _unchecked_i32(center[0] - vector_points[0][0]),
        _unchecked_i32(center[1] - vector_points[0][1]),
    )
    invert = indicator_vector[1] > 0 or (
        indicator_vector[1] == 0 and indicator_vector[0] < 0
    )
    edge1 = _harness_manual_edge_points(
        break_point1,
        (
            _unchecked_i32(break_point1[0] - indicator_vector[0]),
            _unchecked_i32(break_point1[1] - indicator_vector[1]),
        ),
        (
            _unchecked_i32(break_point1[0] + indicator_vector[0]),
            _unchecked_i32(break_point1[1] + indicator_vector[1]),
        ),
        line,
        indicator_half_height,
        arc_start1,
        arc_start2,
        invert_indicator=invert,
    )
    edge2 = _harness_manual_edge_points(
        break_point2,
        (
            _unchecked_i32(break_point2[0] - indicator_vector[0]),
            _unchecked_i32(break_point2[1] - indicator_vector[1]),
        ),
        (
            _unchecked_i32(break_point2[0] + indicator_vector[0]),
            _unchecked_i32(break_point2[1] + indicator_vector[1]),
        ),
        line,
        indicator_half_height,
        arc_start1,
        arc_start2,
        invert_indicator=invert,
    )
    segment1 = _harness_manual_segment_part(
        segment_vertex1,
        break_point1,
        line,
        edge1,
        segment_half_width,
        arc_start1,
        margin_vector,
    )
    segment2 = _harness_manual_segment_part(
        segment_vertex2,
        break_point2,
        line,
        edge2,
        segment_half_width,
        arc_start2,
        margin_vector,
    )
    wave_width = _round_away_from_zero(_BUS_LINE_WIDTH_INTERNAL[line_width] * 0.1)
    wave1 = _harness_manual_wave(
        edge1,
        break_point1,
        break_point2,
        line,
        wave_width,
        invert_vectors=invert,
    )
    wave2 = _harness_manual_wave(
        edge2,
        break_point2,
        break_point1,
        line,
        wave_width,
        invert_vectors=not invert,
    )
    segment_brush = make_solid_brush(segment_color_raw, alpha=segment_alpha)
    black_brush = make_solid_brush(0)
    return [
        SchGeometryOp.polygons(
            [_harness_geometry_polygon(ctx, segment1, units_per_px=units_per_px)],
            brush=segment_brush,
        ),
        SchGeometryOp.polygons(
            [_harness_geometry_polygon(ctx, segment2, units_per_px=units_per_px)],
            brush=segment_brush,
        ),
        SchGeometryOp.polygons(
            [_harness_geometry_polygon(ctx, wave1, units_per_px=units_per_px)],
            brush=black_brush,
        ),
        SchGeometryOp.polygons(
            [_harness_geometry_polygon(ctx, wave2, units_per_px=units_per_px)],
            brush=black_brush,
        ),
    ]


def _harness_crossover_gap_points(
    center: tuple[int, int],
    vertex1: tuple[int, int],
    vertex2: tuple[int, int],
    *,
    offset: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    if vertex1[0] == vertex2[0]:
        delta = _unchecked_i32(vertex2[1] - vertex1[1])
        direction = 1 if delta > 0 else -1 if delta < 0 else 0
        return (
            (vertex1[0], _unchecked_i32(center[1] - offset * direction)),
            (vertex1[0], _unchecked_i32(center[1] + offset * direction)),
        )
    if vertex1[1] == vertex2[1]:
        delta = _unchecked_i32(vertex2[0] - vertex1[0])
        direction = 1 if delta > 0 else -1 if delta < 0 else 0
        return (
            (_unchecked_i32(center[0] - offset * direction), vertex1[1]),
            (_unchecked_i32(center[0] + offset * direction), vertex1[1]),
        )
    return vertex1, vertex2


def _harness_crossover_arc_operation(
    ctx: SchSvgRenderContext,
    *,
    center: tuple[int, int],
    gap_point1: tuple[int, int],
    gap_point2: tuple[int, int],
    color_raw: int,
    line_width: LineWidth,
    units_per_px: int,
) -> SchGeometryOp | None:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    if gap_point1[0] == gap_point2[0]:
        start_angle, end_angle = -270.0, -90.0
    elif gap_point1[1] == gap_point2[1]:
        start_angle, end_angle = 0.0, -180.0
    else:
        return None
    transformed_center_x, transformed_center_y = _harness_geometry_point(
        ctx,
        *center,
        units_per_px=units_per_px,
    )
    center_x = _f32(transformed_center_x)
    center_y = _f32(transformed_center_y)
    diameter = _f32(_connection_pen_width(ctx, 600_000, units_per_px))
    return SchGeometryOp.arc(
        center_x=center_x,
        center_y=center_y,
        width=diameter,
        height=diameter,
        start_angle=start_angle,
        end_angle=end_angle,
        pen=make_pen(
            color_raw,
            width=_f32(
                _connection_pen_width(
                    ctx,
                    _SYMBOL_LINE_WIDTH_INTERNAL[line_width],
                    units_per_px,
                )
            ),
        ),
    )


def _harness_bundle_run_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    run_vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    first_segment_index: int,
    color_raw: int,
    pen_width: float,
    masked: bool,
    units_per_px: int,
    is_metafile: bool,
    longest_segment_cache: _HarnessLongestSegmentCache,
    dash_style: str,
) -> list[SchGeometryOp]:
    last_segment_index = first_segment_index + len(run_vertices) - 2
    if not any(
        bundle._segment_crossovers.get(segment_index)
        for segment_index in range(first_segment_index, last_segment_index + 1)
    ):
        return _harness_bundle_polyline_operations(
            bundle,
            ctx,
            vertices=vertices,
            internal_points=run_vertices,
            geometry_points=geometry_points,
            color_raw=color_raw,
            pen_width=pen_width,
            masked=masked,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
            longest_segment_cache=longest_segment_cache,
            dash_style=dash_style,
        )

    operations: list[SchGeometryOp] = []
    pending: list[tuple[int, int]] = []
    segment_color = _harness_bundle_color(color_raw, masked=masked, ctx=ctx)
    for offset, (vertex1, vertex2) in enumerate(
        zip(run_vertices, run_vertices[1:], strict=False),
    ):
        pending.append(vertex1)
        segment_index = first_segment_index + offset
        for center in bundle._segment_crossovers.get(segment_index, ()):
            gap_point1, gap_point2 = _harness_crossover_gap_points(
                center,
                vertex1,
                vertex2,
                offset=300_000,
            )
            pending.append(gap_point1)
            fragment = tuple(pending)
            operations.extend(
                _harness_bundle_polyline_operations(
                    bundle,
                    ctx,
                    vertices=vertices,
                    internal_points=fragment,
                    geometry_points=_harness_geometry_polygon(
                        ctx,
                        fragment,
                        units_per_px=units_per_px,
                    ),
                    color_raw=color_raw,
                    pen_width=pen_width,
                    masked=masked,
                    units_per_px=units_per_px,
                    is_metafile=is_metafile,
                    longest_segment_cache=longest_segment_cache,
                    dash_style=dash_style,
                )
            )
            arc = _harness_crossover_arc_operation(
                ctx,
                center=center,
                gap_point1=gap_point1,
                gap_point2=gap_point2,
                color_raw=segment_color,
                line_width=bundle.line_width,
                units_per_px=units_per_px,
            )
            if arc is not None:
                operations.append(arc)
            pending = [gap_point2]
    pending.append(run_vertices[-1])
    fragment = tuple(pending)
    operations.extend(
        _harness_bundle_polyline_operations(
            bundle,
            ctx,
            vertices=vertices,
            internal_points=fragment,
            geometry_points=_harness_geometry_polygon(
                ctx,
                fragment,
                units_per_px=units_per_px,
            ),
            color_raw=color_raw,
            pen_width=pen_width,
            masked=masked,
            units_per_px=units_per_px,
            is_metafile=is_metafile,
            longest_segment_cache=longest_segment_cache,
            dash_style=dash_style,
        )
    )
    return operations


def _harness_bundle_polyline_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    internal_points: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    color_raw: int,
    pen_width: float,
    masked: bool,
    units_per_px: int,
    is_metafile: bool,
    longest_segment_cache: _HarnessLongestSegmentCache,
    dash_style: str,
    stroke_alpha: int = 0xFF,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp

    pen = _harness_bundle_pen(
        color_raw,
        pen_width,
        masked=masked,
        ctx=ctx,
        alpha=stroke_alpha,
        dash_style=dash_style,
    )
    operations = _harness_bundle_highlight_operations(
        bundle,
        ctx,
        geometry_points=geometry_points,
        pen_width=pen_width,
        dash_style=dash_style,
        units_per_px=units_per_px,
        is_metafile=is_metafile,
    )
    if not bundle.show_break_symbol or len(internal_points) != len(vertices):
        operations.append(SchGeometryOp.lines(geometry_points, pen=pen))
        return operations
    longest = longest_segment_cache.get(vertices)
    if longest is None:
        operations.append(SchGeometryOp.lines(geometry_points, pen=pen))
        return operations
    segment_index, segment_length = longest
    indicator_width = _round_away_from_zero(
        _BUS_LINE_WIDTH_INTERNAL[bundle.line_width] * 0.8
    )
    if indicator_width > segment_length:
        operations.append(SchGeometryOp.lines(geometry_points, pen=pen))
        return operations
    if (
        internal_points[segment_index - 1] != vertices[segment_index - 1]
        or internal_points[segment_index] != vertices[segment_index]
    ):
        operations.append(SchGeometryOp.lines(geometry_points, pen=pen))
        return operations
    if segment_index != 1:
        operations.append(SchGeometryOp.lines(geometry_points[:segment_index], pen=pen))
    operations.extend(
        _harness_manual_indicator_operations(
            ctx,
            segment_vertex1=vertices[segment_index - 1],
            segment_vertex2=vertices[segment_index],
            line_width=bundle.line_width,
            indicator_width=indicator_width,
            segment_color_raw=_harness_bundle_color(
                color_raw,
                masked=masked,
                ctx=ctx,
            ),
            units_per_px=units_per_px,
            segment_alpha=stroke_alpha,
        )
    )
    suffix = geometry_points[segment_index:]
    if len(suffix) > 1:
        operations.append(SchGeometryOp.lines(suffix, pen=pen))
    return operations


def _harness_bundle_underline_operations(
    bundle: AltiumSchHarnessBundle,
    ctx: SchSvgRenderContext,
    *,
    vertices: tuple[tuple[int, int], ...],
    geometry_points: list[tuple[float, float]],
    units_per_px: int,
    is_metafile: bool,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp

    underline_color = int(bundle.underline_color or 0)
    if ctx.net_color_override_enabled:
        underline_color = int(
            ctx.net_color_overrides.get(str(bundle.unique_id or ""), underline_color)
        )
    if underline_color == 0 or len(vertices) <= 1:
        return []
    underline_color = _harness_drawing_color(bundle, underline_color, ctx)

    thickness = max(
        500_000,
        _BUS_LINE_WIDTH_INTERNAL[bundle.line_width] + 233_333,
    )
    operations = _harness_bundle_polyline_operations(
        bundle,
        ctx,
        vertices=vertices,
        internal_points=vertices,
        geometry_points=geometry_points,
        color_raw=underline_color,
        pen_width=_connection_pen_width(ctx, thickness, units_per_px),
        masked=False,
        units_per_px=units_per_px,
        is_metafile=is_metafile,
        longest_segment_cache=_HarnessLongestSegmentCache(),
        dash_style="pdsSolid",
        stroke_alpha=0xFF if is_metafile else 125,
    )
    return [
        SchGeometryOp(
            kind=operation.kind,
            payload={**operation.payload, "transparent_back": True},
        )
        for operation in operations
    ]


class _HarnessConnectionBundleIndex:
    """Resolve connection-point bundle IDs without rescanning the document."""

    __slots__ = ("_by_uid",)

    def __init__(
        self,
        document_bundles: Iterable[AltiumSchHarnessBundle],
    ) -> None:
        by_uid: dict[str, list[tuple[int, AltiumSchHarnessBundle]]] = {}
        for source_order, bundle in enumerate(document_bundles):
            if source_order >= _MAX_HARNESS_CONNECTION_BUNDLES:
                raise ValueError(
                    "harness connection-point geometry exceeds the document "
                    f"limit of {_MAX_HARNESS_CONNECTION_BUNDLES} bundles"
                )
            state_uid = str(bundle.unique_id or "")
            by_uid.setdefault(state_uid, []).append((source_order, bundle))
        self._by_uid = {
            state_uid: tuple(entries) for state_uid, entries in by_uid.items()
        }

    def resolve(
        self,
        connected_unique_ids: tuple[str, ...],
        *,
        max_resolved_bundles: int = _MAX_HARNESS_CONNECTION_BUNDLES,
    ) -> tuple[AltiumSchHarnessBundle, ...]:
        if max_resolved_bundles < 0:
            raise ValueError("resolved harness bundle limit cannot be negative")
        selected: list[tuple[int, AltiumSchHarnessBundle]] = []
        for state_uid in set(connected_unique_ids):
            entries = self._by_uid.get(state_uid, ())
            if len(entries) > max_resolved_bundles - len(selected):
                raise ValueError("resolved harness bundle limit exceeded")
            selected.extend(entries)
        selected.sort(key=lambda entry: entry[0])
        return tuple(bundle for _, bundle in selected)


class _HarnessComponentIndex:
    """Managed first-level harness-component lookup built once per document."""

    def __init__(
        self,
        components: Iterable[AltiumSchHarnessComponent],
        source_admission: _SourceAdmission | None = None,
    ) -> None:
        self._source_admission = source_admission
        self._components: dict[str, AltiumSchHarnessComponent] = {}
        self._centers: dict[str, tuple[int, int]] = {}
        for component in components:
            unique_id = str(component.unique_id or "")
            if unique_id in self._components:
                raise ValueError(f"duplicate harness component UniqueID {unique_id!r}")
            self._components[unique_id] = component

    def get_center(self, unique_id: str) -> tuple[int, int] | None:
        component = self._components.get(unique_id)
        if component is None:
            return None
        center = self._centers.get(unique_id)
        if center is None:
            center = _component_bounds_center_internal(
                component, self._source_admission
            )
            self._centers[unique_id] = center
        return center


def _component_bounds_center_internal(
    component: AltiumSchHarnessComponent,
    source_admission: _SourceAdmission | None = None,
) -> tuple[int, int]:
    bounds = (
        component.non_accessible_children_bounds_mils()
        if source_admission is None
        else component._source_display_body_bounds_mils(source_admission)
    )
    if bounds is None:
        location_x, location_y = _harness_internal_location(component.location)
        return (
            _unchecked_i32_offset(location_x, -250_000),
            _unchecked_i32_offset(location_y, 750_000),
        )
    normalized = bounds.normalized()
    left = _dotnet_conv_i4(normalized.x1_mils * 10_000.0)
    right = _dotnet_conv_i4(normalized.x2_mils * 10_000.0)
    top = _dotnet_conv_i4(normalized.y1_mils * 10_000.0)
    bottom = _dotnet_conv_i4(normalized.y2_mils * 10_000.0)
    width = _abs_safe_i32(_unchecked_i32(right - left))
    height = _abs_safe_i32(_unchecked_i32(bottom - top))
    return (
        _unchecked_i32_offset(left, _trunc_i32_div(width, 2)),
        _unchecked_i32_offset(top, _trunc_i32_div(height, 2)),
    )


def _harness_connector_signal_line_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    components: _HarnessComponentIndex | None,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    if not connection_point.connectors or components is None:
        return []
    start = _harness_geometry_point(ctx, x, y, units_per_px=units_per_px)
    pen = make_pen(
        int(connection_point.color or 0),
        width=_connection_pen_width(ctx, 100_000, units_per_px),
        dash_style="pdsDashDot",
    )
    operations: list[SchGeometryOp] = []
    for connector in connection_point.connectors:
        center = components.get_center(connector.connector_id)
        if center is None:
            continue
        operations.append(
            SchGeometryOp.lines(
                [
                    start,
                    _harness_geometry_point(
                        ctx,
                        *center,
                        units_per_px=units_per_px,
                    ),
                ],
                pen=pen,
            )
        )
    return operations


def _connection_bundle_vertices(
    bundle: AltiumSchHarnessBundle,
) -> tuple[tuple[int, int], ...]:
    return tuple(_harness_internal_location(point) for point in bundle.points)


def _connection_directions(
    vertices: tuple[tuple[int, int], ...],
    connection_point: tuple[int, int],
) -> Iterator[tuple[float, float]]:
    if len(vertices) < 2:
        return
    # Only vertex zero searches forward. Later coincident vertices share the
    # nearest preceding different point; repeated backward scans are quadratic.
    if vertices[0] == connection_point:
        for point in vertices:
            if point != connection_point:
                direction = _connection_unit_direction(point, connection_point)
                if direction is not None:
                    yield direction
                break
    previous = connection_point
    for point in vertices:
        if point != connection_point:
            previous = point
        elif previous != connection_point:
            direction = _connection_unit_direction(previous, connection_point)
            if direction is not None:
                yield direction


def _connection_unit_direction(
    point: tuple[int, int], connection_point: tuple[int, int]
) -> tuple[float, float] | None:
    delta_x = _f32(_unchecked_i32(point[0] - connection_point[0]))
    delta_y = _f32(_unchecked_i32(point[1] - connection_point[1]))
    if delta_x == 0.0 and delta_y == 0.0:
        return None
    length_squared = _f32(_f32(delta_x * delta_x) + _f32(delta_y * delta_y))
    length = _f32(math.sqrt(length_squared))
    return _f32(delta_x / length), _f32(delta_y / length)


def _connection_pen_width(
    ctx: SchSvgRenderContext,
    internal_width: int,
    units_per_px: int,
) -> float:
    from .altium_sch_geometry_oracle import _geometry_item_length

    return _geometry_item_length(
        internal_width * ctx.get_stroke_scale() / 100_000,
        units_per_px=units_per_px,
    )


def _connection_line_operation(
    ctx: SchSvgRenderContext,
    *,
    point1: tuple[int | float, int | float],
    point2: tuple[int | float, int | float],
    color: int,
    width: int,
    units_per_px: int,
) -> SchGeometryOp:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    return SchGeometryOp.lines(
        [
            _harness_geometry_point(ctx, *point1, units_per_px=units_per_px),
            _harness_geometry_point(ctx, *point2, units_per_px=units_per_px),
        ],
        pen=make_pen(
            color,
            width=_connection_pen_width(ctx, width, units_per_px),
        ),
    )


def _connection_insulator_operations(
    ctx: SchSvgRenderContext,
    *,
    connection_point: tuple[int, int],
    bundles: tuple[AltiumSchHarnessBundle, ...],
    color: int,
    extra_width: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import make_solid_brush

    operations: list[SchGeometryOp] = []
    for bundle in bundles:
        vertices = _connection_bundle_vertices(bundle)
        line_width = _SYMBOL_LINE_WIDTH_INTERNAL[bundle.line_width]
        for direction in _connection_directions(vertices, connection_point):
            if not bool(getattr(bundle, "enable_draw", True)):
                operations.append(
                    _harness_circle_operation(
                        ctx,
                        x=connection_point[0],
                        y=connection_point[1],
                        radius=line_width * 2 + extra_width,
                        units_per_px=units_per_px,
                        brush=make_solid_brush(color),
                    )
                )
                continue
            end_point = (
                _f32(_f32(connection_point[0]) + _f32(_f32(700_000) * direction[0])),
                _f32(_f32(connection_point[1]) + _f32(_f32(700_000) * direction[1])),
            )
            operations.append(
                _connection_line_operation(
                    ctx,
                    point1=connection_point,
                    point2=end_point,
                    color=color,
                    width=line_width + 300_000 + extra_width,
                    units_per_px=units_per_px,
                )
            )
    return operations


def _connection_square_operation(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    units_per_px: int,
    fill: bool,
) -> SchGeometryOp:
    from .altium_sch_geometry_oracle import (
        SchGeometryOp,
        _geometry_item_length,
        make_pen,
        make_solid_brush,
    )

    center = _harness_geometry_point(ctx, x, y, units_per_px=units_per_px)
    half_extent = _geometry_item_length(
        abs(float(ctx.scale)) * 4.0, units_per_px=units_per_px
    )
    if fill:
        return SchGeometryOp.rounded_rectangle_from_item(
            center_x=center[0],
            center_y=center[1],
            half_width=half_extent,
            half_height=half_extent,
            brush=make_solid_brush(int(connection_point.area_color or 0)),
        )
    return SchGeometryOp.rounded_rectangle_from_item(
        center_x=center[0],
        center_y=center[1],
        half_width=half_extent,
        half_height=half_extent,
        pen=make_pen(
            int(connection_point.border_color or 0),
            width=_connection_pen_width(ctx, 100_000, units_per_px),
        ),
    )


def _connection_circle_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import make_pen, make_solid_brush

    area_color = int(connection_point.area_color or 0)
    border_color = int(connection_point.border_color or 0)
    operations = [
        _harness_circle_operation(
            ctx,
            x=x,
            y=y,
            radius=400_000,
            units_per_px=units_per_px,
            brush=make_solid_brush(area_color),
        )
    ]
    if border_color != area_color:
        operations.append(
            _harness_circle_operation(
                ctx,
                x=x,
                y=y,
                radius=400_000,
                units_per_px=units_per_px,
                pen=make_pen(
                    border_color,
                    width=_connection_pen_width(ctx, 100_000, units_per_px),
                ),
            )
        )
    return operations


def _connection_square_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    operations = [
        _connection_square_operation(
            connection_point,
            ctx,
            x=x,
            y=y,
            units_per_px=units_per_px,
            fill=True,
        )
    ]
    if int(connection_point.border_color or 0) != int(connection_point.area_color or 0):
        operations.append(
            _connection_square_operation(
                connection_point,
                ctx,
                x=x,
                y=y,
                units_per_px=units_per_px,
                fill=False,
            )
        )
    return operations


def _connection_connected_insulator_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    resolved_bundles: tuple[AltiumSchHarnessBundle, ...],
    units_per_px: int,
) -> list[SchGeometryOp]:
    area_color = int(connection_point.area_color or 0)
    border_color = int(connection_point.border_color or 0)
    border_extra = 100_000 if border_color != area_color else 0
    operations: list[SchGeometryOp] = []
    if border_extra:
        operations.extend(
            _connection_insulator_operations(
                ctx,
                connection_point=(x, y),
                bundles=resolved_bundles,
                color=border_color,
                extra_width=border_extra,
                units_per_px=units_per_px,
            )
        )
    operations.extend(
        _connection_insulator_operations(
            ctx,
            connection_point=(x, y),
            bundles=resolved_bundles,
            color=area_color,
            extra_width=-border_extra,
            units_per_px=units_per_px,
        )
    )
    return operations


def _connection_unconnected_insulator_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    units_per_px: int,
) -> list[SchGeometryOp]:
    area_color = int(connection_point.area_color or 0)
    border_color = int(connection_point.border_color or 0)
    width = _SYMBOL_LINE_WIDTH_INTERNAL[LineWidth.MEDIUM] + 300_000
    end_point = (_unchecked_i32_offset(x, 700_000), y)
    operations: list[SchGeometryOp] = []
    if border_color != area_color:
        operations.append(
            _connection_line_operation(
                ctx,
                point1=(x, y),
                point2=end_point,
                color=border_color,
                width=width,
                units_per_px=units_per_px,
            )
        )
        width -= 200_000
    operations.append(
        _connection_line_operation(
            ctx,
            point1=(x, y),
            point2=end_point,
            color=area_color,
            width=width,
            units_per_px=units_per_px,
        )
    )
    return operations


def _harness_connection_point_operations(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    ctx: SchSvgRenderContext,
    *,
    x: int,
    y: int,
    resolved_bundles: tuple[AltiumSchHarnessBundle, ...],
    units_per_px: int,
) -> list[SchGeometryOp]:
    if connection_point.style is HarnessLayoutConnectionPointStyle.CIRCLE:
        return _connection_circle_operations(
            connection_point,
            ctx,
            x=x,
            y=y,
            units_per_px=units_per_px,
        )
    if connection_point.style is HarnessLayoutConnectionPointStyle.SQUARE:
        return _connection_square_operations(
            connection_point,
            ctx,
            x=x,
            y=y,
            units_per_px=units_per_px,
        )
    if connection_point.connected_bundles_unique_ids:
        return _connection_connected_insulator_operations(
            connection_point,
            ctx,
            x=x,
            y=y,
            resolved_bundles=resolved_bundles,
            units_per_px=units_per_px,
        )
    return _connection_unconnected_insulator_operations(
        connection_point,
        ctx,
        x=x,
        y=y,
        units_per_px=units_per_px,
    )


def _expand_connection_insulator_bounds(
    bounds: SchGeometryBounds,
    *,
    location: tuple[int, int],
    direction: tuple[float, float],
    line_width: LineWidth,
) -> SchGeometryBounds:
    from .altium_sch_geometry_oracle import SchGeometryBounds

    direction_x, direction_y = direction
    start_x = _unchecked_i32_offset(
        location[0],
        _float_to_i32(_f32(_f32(-200_000) * direction_x), rounded=False),
    )
    start_y = _unchecked_i32_offset(
        location[1],
        _float_to_i32(_f32(_f32(-200_000) * direction_y), rounded=False),
    )
    end_x = _float_to_i32(
        _f32(_f32(start_x) + _f32(_f32(1_100_000) * direction_x)),
        rounded=False,
    )
    end_y = _float_to_i32(
        _f32(_f32(start_y) + _f32(_f32(1_100_000) * direction_y)),
        rounded=False,
    )
    width = _f32(_SYMBOL_LINE_WIDTH_INTERNAL[line_width] + 300_000)
    perpendicular_x = _f32(_f32(_f32(0.5) * width) * _f32(-direction_y))
    perpendicular_y = _f32(_f32(_f32(0.5) * width) * direction_x)
    points = tuple(
        (
            _float_to_i32(_f32(_f32(point_x) + offset_x), rounded=False),
            _float_to_i32(_f32(_f32(point_y) + offset_y), rounded=False),
        )
        for point_x, point_y, offset_x, offset_y in (
            (start_x, start_y, perpendicular_x, perpendicular_y),
            (end_x, end_y, perpendicular_x, perpendicular_y),
            (end_x, end_y, -perpendicular_x, -perpendicular_y),
            (start_x, start_y, -perpendicular_x, -perpendicular_y),
        )
    )
    return SchGeometryBounds(
        left=min(bounds.left, *(point[0] for point in points)),
        top=max(bounds.top, *(point[1] for point in points)),
        right=max(bounds.right, *(point[0] for point in points)),
        bottom=min(bounds.bottom, *(point[1] for point in points)),
    )


def _harness_connection_point_bounds(
    connection_point: AltiumSchHarnessLayoutConnectionPoint,
    *,
    x: int,
    y: int,
    resolved_bundles: tuple[AltiumSchHarnessBundle, ...],
) -> SchGeometryBounds:
    from .altium_sch_geometry_oracle import SchGeometryBounds

    if connection_point.style is not HarnessLayoutConnectionPointStyle.INSULATOR:
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -400_000),
            top=_unchecked_i32_offset(y, 400_000),
            right=_unchecked_i32_offset(x, 400_000),
            bottom=_unchecked_i32_offset(y, -400_000),
        )
    bounds = SchGeometryBounds(left=x, top=y, right=x, bottom=y)
    bounds = _expand_connection_insulator_bounds(
        bounds,
        location=(x, y),
        direction=(1.0, 0.0),
        line_width=LineWidth.MEDIUM,
    )
    if not connection_point.connected_bundles_unique_ids:
        return bounds
    for bundle in resolved_bundles:
        vertices = _connection_bundle_vertices(bundle)
        for direction in _connection_directions(vertices, (x, y)):
            bounds = _expand_connection_insulator_bounds(
                bounds,
                location=(x, y),
                direction=direction,
                line_width=bundle.line_width,
            )
    return bounds


_LIBRARY_DYNAMIC_FIELDS: tuple[tuple[str, FieldDef | str], ...] = (
    ("vault_guid", Fields.VAULT_GUID),
    ("item_guid", Fields.ITEM_GUID),
    ("revision_guid", Fields.REVISION_GUID),
    ("design_item_id", Fields.DESIGN_ITEM_ID),
    ("source_library_name", Fields.SOURCE_LIBRARY_NAME),
    ("library_path", Fields.LIBRARY_PATH),
    ("lib_reference", Fields.LIB_REFERENCE),
    ("database_table_name", Fields.DATABASE_TABLE_NAME),
)


class _HarnessLibraryComponentMixin:
    def _init_library_component(self) -> None:
        self.vault_guid = ""
        self.item_guid = ""
        self.revision_guid = ""
        self.design_item_id = ""
        self.source_library_name = "*"
        self.library_path = "*"
        self.lib_reference = "*"
        self.database_table_name = ""
        self.use_library_name = True
        self.component_kind = ComponentKind.STANDARD
        self._library_dynamic_source: dict[str, tuple[str, bool, bool]] = {
            attribute: (str(getattr(self, attribute)), False, False)
            for attribute, _ in _LIBRARY_DYNAMIC_FIELDS
        }
        self._source_use_library_name = self.use_library_name
        self._source_component_kind = self.component_kind

    def _parse_library_component(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        owner = cast(SchGraphicalObject, self)
        self._library_dynamic_source = {}
        for attribute, field in _LIBRARY_DYNAMIC_FIELDS:
            value, present, used_utf8 = _read_dynamic(owner, serializer, record, field)
            setattr(self, attribute, value)
            self._library_dynamic_source[attribute] = (value, present, used_utf8)
        not_use_library, _ = serializer.read_bool(
            record, "NotUseLibraryName", default=False
        )
        self.use_library_name = not not_use_library
        component_kind, _ = serializer.read_int(
            record, Fields.COMPONENT_KIND, default=0
        )
        validate_record_enum_value("ComponentKind", component_kind, 6)
        self.component_kind = ComponentKind(component_kind)
        self._source_use_library_name = self.use_library_name
        self._source_component_kind = self.component_kind

    def _serialize_library_component(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        owner = cast(SchGraphicalObject, self)
        for attribute, field in _LIBRARY_DYNAMIC_FIELDS:
            _write_dynamic(
                owner,
                serializer,
                record,
                field,
                str(getattr(self, attribute)),
                self._library_dynamic_source[attribute],
            )
        owner._serialize_managed_family_bool(
            record,
            serializer,
            "NotUseLibraryName",
            not self.use_library_name,
        )
        validate_record_enum_value("ComponentKind", self.component_kind.value, 6)
        owner._serialize_managed_family_int(
            record,
            serializer,
            Fields.COMPONENT_KIND.canonical,
            self.component_kind.value,
        )


class AltiumSchHarnessLayoutLabel(_HarnessLibraryComponentMixin, AltiumSchLabel):
    def __init__(self) -> None:
        super().__init__()
        self.text = "Harness Layout Label"
        self.text_color = 16_711_680
        self.alignment = SchHorizontalAlign.LEFT
        self.show_only_first_line = False
        self.designator_locked = False
        self.area_color = 58_879
        self._init_library_component()
        self._source_encoded_text = _encode_altium_multiline_text(self.text)
        self._source_text_color = self.text_color
        self._source_alignment = self.alignment
        self._source_show_only_first_line = self.show_only_first_line
        self._source_designator_locked = self.designator_locked
        self._capture_graphical_source_state()
        self._capture_label_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_LAYOUT_LABEL

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        encoded_text, self._has_text, self._used_utf8_text = _read_dynamic(
            self, serializer, record, Fields.TEXT, "Text"
        )
        self.text = _decode_altium_multiline_text(encoded_text)
        self._source_encoded_text = encoded_text
        area_color, _ = serializer.read_color(
            record, Fields.AREA_COLOR, default=16_777_215
        )
        self.area_color = area_color
        self.text_color, _ = serializer.read_color(record, Fields.TEXT_COLOR, default=0)
        alignment, _ = serializer.read_int(record, Fields.ALIGNMENT, default=1)
        validate_record_enum_value("Alignment", alignment, 2)
        self.alignment = SchHorizontalAlign(alignment)
        self.show_only_first_line = serializer.read_bool(
            record, "ShowOnlyFirstLine", default=False
        )[0]
        self.designator_locked = serializer.read_bool(
            record, Fields.DESIGNATOR_LOCKED, default=False
        )[0]
        self._parse_library_component(serializer, record)
        self._source_text_color = self.text_color
        self._source_alignment = self.alignment
        self._source_show_only_first_line = self.show_only_first_line
        self._source_designator_locked = self.designator_locked
        self._capture_graphical_source_state()
        self._capture_label_source_state()

    def _serialize_label_text(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        raw_record: dict[str, object] | None,
    ) -> None:
        encoded = _encode_altium_multiline_text(self.text)
        _write_dynamic(
            self,
            serializer,
            record,
            Fields.TEXT,
            encoded,
            (self._source_encoded_text, self._has_text, self._used_utf8_text),
        )

    def serialize_to_record(self) -> dict[str, object]:
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        _write_sparse_int(
            self,
            serializer,
            record,
            Fields.ALIGNMENT,
            self.alignment.value,
            self._source_alignment.value,
        )
        _write_sparse_color(
            self,
            serializer,
            record,
            Fields.AREA_COLOR.canonical,
            int(self.area_color or 0),
            self._source_area_color,
        )
        self._serialize_managed_family_color(
            record,
            serializer,
            Fields.TEXT_COLOR.canonical,
            int(self.text_color or 0),
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            "ShowOnlyFirstLine",
            self.show_only_first_line,
            self._source_show_only_first_line,
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.DESIGNATOR_LOCKED,
            self.designator_locked,
            self._source_designator_locked,
        )
        self._serialize_library_component(serializer, record)
        return self._order_authored_graphical_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Orientation",
                "Justification",
                "Color",
                "FontID",
                "IsMirrored",
                "URL",
                "UniqueID",
                "Alignment",
                "AreaColor",
                "TextColor",
                "ShowOnlyFirstLine",
                "Text",
                "DesignatorLocked",
                *(_field_name(field) for _, field in _LIBRARY_DYNAMIC_FIELDS[:-1]),
                "NotUseLibraryName",
                "DatabaseTableName",
                "ComponentKind",
            ),
        )

    def _display_text(self, ctx: SchSvgRenderContext) -> str:
        display_text = ctx.substitute_parameters(self.text)
        if not self.show_only_first_line:
            return display_text
        first_break = display_text.find("\r\n")
        if first_break < 0:
            first_break = display_text.find("\n")
        if first_break < 0:
            return display_text
        if first_break == 0:
            return "..."
        return f"{display_text[:first_break]}..."

    def _text_layout(
        self,
        ctx: SchSvgRenderContext,
    ) -> tuple[str, tuple[str, ...], int, int, tuple[str, float, bool, bool, bool]]:
        from .altium_text_metrics import measure_gdi_typographic_bounds

        display_text = self._display_text(ctx)
        lines = _layout_label_lines(display_text)
        font = ctx.get_font_info(int(self.font_id or 0))
        font_name, font_size_px, is_bold, is_italic, _ = font
        font_size_for_width = ctx.get_font_size_for_width(int(self.font_id or 0))
        width_px, height_px = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(display_text, 8192),
            font_size_for_width,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        return (
            display_text,
            lines,
            _abs_safe_i32(_float_to_i32(width_px * 100_000, rounded=True)),
            _abs_safe_i32(_float_to_i32(height_px * 100_000, rounded=True)),
            font,
        )

    def _own_geometry_bounds(
        self,
        ctx: SchSvgRenderContext,
    ) -> SchGeometryBounds:
        from .altium_sch_geometry_oracle import SchGeometryBounds

        _, _, x_size, y_size, _ = self._text_layout(ctx)
        x, y = _harness_internal_location(self.location)
        if self.orientation is TextOrientation.DEGREES_90:
            bounds = (x, y + x_size, x + y_size, y)
        elif self.orientation is TextOrientation.DEGREES_180:
            bounds = (x - x_size, y + y_size, x, y)
        elif self.orientation is TextOrientation.DEGREES_270:
            bounds = (x - y_size, y, x, y - x_size)
        else:
            bounds = (x, y, x + x_size, y - y_size)
        return SchGeometryBounds(
            left=_unchecked_i32_offset(bounds[0], -100_000),
            top=_unchecked_i32_offset(bounds[1], 100_000),
            right=_unchecked_i32_offset(bounds[2], 100_000),
            bottom=_unchecked_i32_offset(bounds[3], -100_000),
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
        child_records: Sequence[SchGeometryRecord] = (),
    ) -> SchGeometryRecord:
        """Build the managed layout-label background, border, and text geometry."""
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            wrap_record_operations,
        )

        display_text, lines, _, _, font = self._text_layout(ctx)
        own_bounds = self._own_geometry_bounds(ctx)
        background = (
            _unchecked_i32_offset(own_bounds.left, -100_000),
            _unchecked_i32_offset(own_bounds.top, 200_000),
            _unchecked_i32_offset(own_bounds.right, 100_000),
            _unchecked_i32_offset(own_bounds.bottom, -200_000),
        )
        center = _harness_geometry_point(
            ctx,
            (background[0] + background[2]) / 2.0,
            (background[1] + background[3]) / 2.0,
            units_per_px=units_per_px,
        )
        from .altium_sch_geometry_oracle import _geometry_item_length

        half_width = _geometry_item_length(
            abs(float(ctx.scale)) * abs(background[2] - background[0]) / 200_000.0,
            units_per_px=units_per_px,
        )
        half_height = _geometry_item_length(
            abs(float(ctx.scale)) * abs(background[3] - background[1]) / 200_000.0,
            units_per_px=units_per_px,
        )

        operations = [
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=center[0],
                center_y=center[1],
                half_width=half_width,
                half_height=half_height,
                brush=make_solid_brush(int(self.area_color or 0)),
            ),
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=center[0],
                center_y=center[1],
                half_width=half_width,
                half_height=half_height,
                pen=make_pen(
                    int(self.color or 0),
                    width=_geometry_item_length(
                        ctx.get_stroke_scale(), units_per_px=units_per_px
                    ),
                ),
            ),
        ]
        if not _is_dotnet_whitespace(display_text):
            operations.extend(
                self._text_geometry_operations(
                    ctx,
                    lines=lines,
                    own_bounds=own_bounds,
                    font=font,
                    units_per_px=units_per_px,
                    brush=make_solid_brush(int(self.text_color or 0)),
                )
            )
        operations.extend(_harness_bundle_child_operations(child_records))
        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_layout_label",
            object_id="eHarnessLayoutLabel",
            bounds=own_bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _text_geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        *,
        lines: tuple[str, ...],
        own_bounds: SchGeometryBounds,
        font: tuple[str, float, bool, bool, bool],
        units_per_px: int,
        brush: dict[str, object],
    ) -> list[SchGeometryOp]:
        from .altium_sch_geometry_oracle import SchGeometryOp, make_font_payload

        font_name, font_size_px, is_bold, is_italic, is_underline = font
        font_size_for_width = ctx.get_font_size_for_width(int(self.font_id or 0))
        anchor = _layout_label_text_anchor(own_bounds, self.orientation)
        rect_width = _abs_safe_i32(_unchecked_i32(own_bounds.right - own_bounds.left))
        line_step = (
            max(int(ctx.get_font_line_height(int(self.font_id or 0))), 0) * 100_000
        )
        rotation = self.orientation.value * 90
        payload = make_font_payload(
            name=font_name,
            size_px=font_size_px,
            units_per_px=units_per_px,
            rotation=-float(rotation),
            underline=is_underline,
            italic=is_italic,
            bold=is_bold,
        )
        tab_width = _trunc_i32_div(
            _unchecked_i32(
                self._line_width_internal(
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
                    font_size_for_width=font_size_for_width,
                    font_name=font_name,
                    is_bold=is_bold,
                    is_italic=is_italic,
                )
                * 8
            ),
            52,
        )
        width_tab_stop = tab_width or 4_000_000
        operations: list[SchGeometryOp] = []
        for line_index, raw_line in enumerate(lines):
            line = raw_line
            words = _layout_label_words(line)
            if not words:
                continue
            measured_words = tuple(
                (
                    word,
                    has_tab,
                    self._line_width_internal(
                        word,
                        font_size_for_width=font_size_for_width,
                        font_name=font_name,
                        is_bold=is_bold,
                        is_italic=is_italic,
                    ),
                )
                for word, has_tab in words
            )
            line_width = _layout_label_tabbed_width(
                tuple(
                    (word_width, has_tab) for _, has_tab, word_width in measured_words
                ),
                width_tab_stop,
            )
            start_x = _layout_label_start_x(
                self.alignment,
                rect_x=anchor[0],
                rect_width=rect_width,
                line_width=line_width,
            )
            cursor_offset = 0
            for word, has_tab, word_width in measured_words:
                unrotated = (
                    _unchecked_i32(start_x + cursor_offset),
                    _unchecked_i32(anchor[1] - line_index * line_step),
                )
                text_point = _rotate_layout_label_point(
                    unrotated,
                    anchor=anchor,
                    orientation=self.orientation,
                )
                geometry_x, geometry_y = _harness_geometry_point(
                    ctx,
                    text_point[0],
                    text_point[1],
                    units_per_px=units_per_px,
                )
                operations.append(
                    SchGeometryOp.string(
                        x=geometry_x,
                        y=geometry_y,
                        text=word,
                        font=payload,
                        brush=brush,
                    )
                )
                if has_tab:
                    cursor_offset = _next_tab_stop(
                        _unchecked_i32(cursor_offset + word_width),
                        tab_width,
                    )
        return operations

    @staticmethod
    def _line_width_internal(
        text: str,
        *,
        font_size_for_width: float,
        font_name: str,
        is_bold: bool,
        is_italic: bool,
    ) -> int:
        from .altium_text_metrics import measure_gdi_typographic_bounds

        width_px, _ = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(text, 8192),
            font_size_for_width,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        return _float_to_i32(
            width_px * 100_000,
            rounded=False,
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields


@dataclass(frozen=True, slots=True)
class _HarnessCoveringLine:
    point1: tuple[int | float, int | float]
    point2: tuple[int | float, int | float]
    thickness: LineWidth

    @property
    def length(self) -> int:
        point1 = (float(self.point1[0]), float(self.point1[1]))
        point2 = (float(self.point2[0]), float(self.point2[1]))
        is_big = all(
            abs(value) > 100_000.0 for point in (point1, point2) for value in point
        )
        if is_big:
            point1 = (point1[0] / 100_000.0, point1[1] / 100_000.0)
            point2 = (point2[0] / 100_000.0, point2[1] / 100_000.0)
        delta_x = point2[0] - point1[0]
        delta_y = point2[1] - point1[1]
        scale = 100_000 if is_big else 1
        return _dotnet_conv_i4(math.sqrt(delta_x * delta_x + delta_y * delta_y) * scale)

    def reversed(self) -> _HarnessCoveringLine:
        return _HarnessCoveringLine(self.point2, self.point1, self.thickness)


_covering_uid_key = dotnet_ordinal_ignore_case_key


@dataclass(frozen=True, slots=True)
class _HarnessCoveringNode:
    graphical_object: object
    parent_key: str | None
    depth: int
    location: tuple[int, int]
    unique_id: str
    start_id: str
    end_id: str
    lines: tuple[_HarnessCoveringLine, ...]
    has_break_symbol: bool
    break_symbol_segment: int
    break_symbol_point: tuple[float, float]
    reversed_state: bool = False

    @property
    def key(self) -> str:
        return _covering_uid_key(self.unique_id)

    @property
    def lines_length(self) -> int:
        total = sum(line.length for line in self.lines)
        if total > (1 << 31) - 1:
            raise OverflowError("harness covering path length exceeds Int32")
        return total

    @property
    def is_bundle(self) -> bool:
        return type(self.graphical_object) is AltiumSchHarnessBundle

    def reversed(self) -> _HarnessCoveringNode:
        return _HarnessCoveringNode(
            graphical_object=self.graphical_object,
            parent_key=self.parent_key,
            depth=self.depth,
            location=self.location,
            unique_id=self.unique_id,
            start_id=self.end_id,
            end_id=self.start_id,
            lines=tuple(line.reversed() for line in reversed(self.lines)),
            has_break_symbol=self.has_break_symbol,
            break_symbol_segment=len(self.lines) - 1 - self.break_symbol_segment,
            break_symbol_point=self.break_symbol_point,
            reversed_state=not self.reversed_state,
        )


@dataclass(slots=True)
class _HarnessCoveringTree:
    nodes: dict[str, _HarnessCoveringNode]

    def contains(self, graphical_object: object) -> bool:
        return (
            _covering_uid_key(str(getattr(graphical_object, "unique_id", "") or ""))
            in self.nodes
        )

    def path_through(
        self,
        graphical_objects: Sequence[object],
    ) -> tuple[_HarnessCoveringNode, ...] | None:
        present = _covering_present_nodes(self.nodes, graphical_objects)
        if present is None:
            return None
        first, second = self._path_endpoints(present)
        return self._path(first, second)

    def _path_endpoints(
        self,
        present: Sequence[_HarnessCoveringNode],
    ) -> tuple[_HarnessCoveringNode, _HarnessCoveringNode]:
        first = present[0]
        last = first
        first_ancestors = self._ancestor_keys(first)
        for candidate in present[1:]:
            last = candidate
            if candidate.depth == first.depth or candidate.key not in first_ancestors:
                return first, candidate
        return first, last

    def _ancestor_keys(self, node: _HarnessCoveringNode) -> set[str]:
        keys = {node.key}
        current = node
        while current.parent_key is not None:
            parent = self.nodes.get(current.parent_key)
            if parent is None:
                break
            keys.add(parent.key)
            current = parent
        return keys

    def _path(
        self,
        node1: _HarnessCoveringNode,
        node2: _HarnessCoveringNode,
    ) -> tuple[_HarnessCoveringNode, ...] | None:
        node1 = _covering_forward_node(node1)
        node2 = _covering_forward_node(node2)
        if _covering_node_is_greater(node1, node2):
            node1, node2 = node2, node1
        common = self._lowest_common_ancestor(node1, node2)
        if common is None:
            return None
        left = _skip_until_bundle(self._ancestry(node1, common))
        right = tuple(reversed(_skip_until_bundle(self._ancestry(node2, common))))
        path = _covering_join_path(left, common, right)
        return _covering_canonical_path(path)

    def _ancestry(
        self,
        node: _HarnessCoveringNode,
        stop: _HarnessCoveringNode,
    ) -> tuple[_HarnessCoveringNode, ...]:
        result: list[_HarnessCoveringNode] = []
        current = node
        while current.key != stop.key and current.parent_key is not None:
            result.append(current)
            parent = self.nodes.get(current.parent_key)
            if parent is None:
                break
            current = parent
        return tuple(result)

    def _lowest_common_ancestor(
        self,
        node1: _HarnessCoveringNode,
        node2: _HarnessCoveringNode,
    ) -> _HarnessCoveringNode | None:
        left = node1
        right = node2
        left = _covering_ancestor_at_depth(self.nodes, left, right.depth)
        right = _covering_ancestor_at_depth(self.nodes, right, left.depth)
        while left.key != right.key:
            parents = _covering_parents(self.nodes, left, right)
            if parents is None:
                return None
            left, right = parents
        return left


def _covering_present_nodes(
    nodes: dict[str, _HarnessCoveringNode],
    graphical_objects: Sequence[object],
) -> tuple[_HarnessCoveringNode, ...] | None:
    present = [
        nodes.get(_covering_uid_key(str(getattr(item, "unique_id", "") or "")))
        for item in graphical_objects
    ]
    if not present or any(node is None for node in present):
        return None
    return tuple(
        sorted(
            (cast(_HarnessCoveringNode, node) for node in present),
            key=lambda node: node.depth,
            reverse=True,
        )
    )


def _covering_forward_node(node: _HarnessCoveringNode) -> _HarnessCoveringNode:
    return node.reversed() if node.reversed_state else node


def _covering_join_path(
    left: Sequence[_HarnessCoveringNode],
    common: _HarnessCoveringNode,
    right: Sequence[_HarnessCoveringNode],
) -> list[_HarnessCoveringNode]:
    path = list(left)
    last_left = path[-1] if path else None
    if last_left is None and right:
        common = common if common.end_id == right[0].unique_id else common.reversed()
    elif common.end_id == (last_left.unique_id if last_left else None):
        common = common.reversed()
    path.append(common)
    path.extend(node.reversed() for node in right)
    while path and not path[-1].is_bundle:
        path.pop()
    return path


def _covering_canonical_path(
    path: Sequence[_HarnessCoveringNode],
) -> tuple[_HarnessCoveringNode, ...] | None:
    if not path or not path[0].lines or not path[-1].lines:
        return None
    start = path[0].lines[0].point1
    end = path[-1].lines[-1].point2
    if end[0] < start[0] or (end[0] == start[0] and end[1] > start[1]):
        return tuple(node.reversed() for node in reversed(path))
    return tuple(path)


def _covering_ancestor_at_depth(
    nodes: dict[str, _HarnessCoveringNode],
    node: _HarnessCoveringNode,
    depth: int,
) -> _HarnessCoveringNode:
    current = node
    while current.depth > depth and current.parent_key is not None:
        parent = nodes.get(current.parent_key)
        if parent is None:
            break
        current = parent
    return current


def _covering_parents(
    nodes: dict[str, _HarnessCoveringNode],
    left: _HarnessCoveringNode,
    right: _HarnessCoveringNode,
) -> tuple[_HarnessCoveringNode, _HarnessCoveringNode] | None:
    if left.parent_key is None or right.parent_key is None:
        return None
    left_parent = nodes.get(left.parent_key)
    right_parent = nodes.get(right.parent_key)
    if left_parent is None or right_parent is None:
        return None
    return left_parent, right_parent


def _covering_node_is_greater(
    left: _HarnessCoveringNode,
    right: _HarnessCoveringNode,
) -> bool:
    return left.location[0] > right.location[0] or (
        left.location[0] == right.location[0] and left.location[1] < right.location[1]
    )


def _skip_until_bundle(
    nodes: Sequence[_HarnessCoveringNode],
) -> tuple[_HarnessCoveringNode, ...]:
    for index, node in enumerate(nodes):
        if node.is_bundle:
            return tuple(nodes[index:])
    return ()


def _covering_first_level(source_objects: Iterable[object]) -> tuple[object, ...]:
    return tuple(
        source_object
        for source_object in source_objects
        if getattr(source_object, "parent", None) is None
    )


def _covering_topology_objects(
    first_level: Sequence[object],
) -> tuple[
    tuple[AltiumSchHarnessBundle, ...],
    tuple[AltiumSchHarnessLayoutConnectionPoint, ...],
]:
    bundles = tuple(
        item for item in first_level if type(item) is AltiumSchHarnessBundle
    )
    connection_points = tuple(
        item
        for item in first_level
        if type(item) is AltiumSchHarnessLayoutConnectionPoint
    )
    return bundles, connection_points


def _validate_covering_topology_size(
    bundles: Sequence[AltiumSchHarnessBundle],
    connection_points: Sequence[AltiumSchHarnessLayoutConnectionPoint],
) -> None:
    topology_size = len(bundles) + len(connection_points)
    if topology_size > _MAX_HARNESS_COVERING_TOPOLOGY_OBJECTS:
        raise ValueError(
            "harness covering topology exceeds the document limit of "
            f"{_MAX_HARNESS_COVERING_TOPOLOGY_OBJECTS} objects"
        )
    segment_count = sum(max(len(bundle.points) - 1, 0) for bundle in bundles)
    if segment_count > _MAX_HARNESS_COVERING_SEGMENTS:
        raise ValueError(
            "harness covering topology exceeds the document limit of "
            f"{_MAX_HARNESS_COVERING_SEGMENTS} segments"
        )


class _HarnessCoveringTopologyIndex:
    """Document-scoped covering topology with bounded, indexed adjacency."""

    def __init__(
        self,
        source_objects: Iterable[object],
        *,
        max_path_work: int = 16 * _MAX_HARNESS_COVERING_TOPOLOGY_WORK,
        max_geometry_work: int = 32 * _MAX_HARNESS_COVERING_SEGMENTS
        + _MAX_HARNESS_COVERING_TOPOLOGY_OBJECTS,
        max_output_work: int = _MAX_HARNESS_COVERING_POINTS,
    ) -> None:
        if any(
            type(limit) is not int or limit < 0
            for limit in (max_path_work, max_geometry_work, max_output_work)
        ):
            raise ValueError("covering work limits must be nonnegative integers")
        self._max_path_work = max_path_work
        self._path_work = 0
        self._path_failed = False
        self._max_geometry_work = max_geometry_work
        self._geometry_work = 0
        self._geometry_failed = False
        self._max_output_work = max_output_work
        self._output_work = 0
        first_level = _covering_first_level(source_objects)
        self._bundles, self._connection_points = _covering_topology_objects(first_level)
        _validate_covering_topology_size(self._bundles, self._connection_points)
        self._bundle_by_uid = _covering_unique_id_map(self._bundles, "bundle")
        self._connection_by_uid = _covering_unique_id_map(
            self._connection_points,
            "connection point",
        )
        coverable_objects = tuple(
            item
            for item in first_level
            if type(item)
            in {
                AltiumSchHarnessBundle,
                AltiumSchHarnessLayoutConnectionPoint,
                AltiumSchHarnessComponent,
            }
        )
        self._object_by_uid = _covering_unique_id_map(
            coverable_objects,
            "coverable object",
        )
        self._topology_work = 0
        self._bundles_by_connection = self._build_endpoint_index()
        self._trees = self._build_trees()
        self._tree_by_key = {
            key: tree for tree in reversed(self._trees) for key in tree.nodes
        }

    def _check_geometry_work(self) -> None:
        if self._geometry_failed:
            raise RuntimeError(
                "covering geometry request is unusable after a work-limit failure"
            )

    def _reserve_geometry_work(self, path: Sequence[_HarnessCoveringNode]) -> None:
        self._check_geometry_work()
        # A break splits at most once per source segment. Multi-polygon borders
        # and patterns need at most 16 slots per resulting segment, hence 32 per
        # source segment. This is conservative capacity, not an output count.
        reservation = 32 * sum(len(node.lines) for node in path) + len(path)
        if reservation > self._max_geometry_work - self._geometry_work:
            self._geometry_failed = True
            raise ValueError("covering geometry allocation work limit exceeded")
        self._geometry_work += reservation

    def _reserve_output_work(self, projection: _HarnessCoveringProjection) -> None:
        self._check_geometry_work()
        points = sum(len(polygon) for polygon in projection.border_polygons) + sum(
            len(pattern.path) for pattern in projection.pattern_polygons
        )
        if points > self._max_output_work - self._output_work:
            self._geometry_failed = True
            raise ValueError("covering renderer output work limit exceeded")
        self._output_work += points

    def _build_endpoint_index(self) -> dict[str, tuple[AltiumSchHarnessBundle, ...]]:
        indexed: dict[str, list[AltiumSchHarnessBundle]] = {}
        for bundle in self._bundles:
            bundle_keys: set[str] = set()
            for unique_id in (
                bundle.end_vertex1_connected_connection_point_unique_id,
                bundle.end_vertex2_connected_connection_point_unique_id,
            ):
                key = _covering_uid_key(str(unique_id or ""))
                if key and key not in bundle_keys:
                    indexed.setdefault(key, []).append(bundle)
                    bundle_keys.add(key)
        return {key: tuple(value) for key, value in indexed.items()}

    def _build_trees(self) -> tuple[_HarnessCoveringTree, ...]:
        trees: list[_HarnessCoveringTree] = []
        visited: set[int] = set()
        for root in self._bundles:
            if id(root) in visited:
                continue
            tree = _HarnessCoveringTree(nodes={})
            queue: deque[tuple[object, str | None, int, str | None]] = deque()
            self._add_tree_node(tree, root, None, 0, None)
            visited.add(id(root))
            self._enqueue_bundle_connections(
                queue,
                root,
                str(root.unique_id or ""),
                1,
                visited,
            )
            while queue:
                item, parent_key, depth, first_point_id = queue.popleft()
                self._add_tree_node(tree, item, parent_key, depth, first_point_id)
                visited.add(id(item))
                if type(item) is AltiumSchHarnessBundle:
                    assert parent_key is not None
                    self._enqueue_bundle_connections(
                        queue,
                        item,
                        str(item.unique_id or ""),
                        depth + 1,
                        visited,
                    )
                else:
                    self._enqueue_connection_bundles(
                        queue,
                        cast(AltiumSchHarnessLayoutConnectionPoint, item),
                        str(getattr(item, "unique_id", "") or ""),
                        depth + 1,
                        visited,
                    )
            trees.append(tree)
        return tuple(trees)

    def _enqueue_bundle_connections(
        self,
        queue: deque[tuple[object, str | None, int, str | None]],
        bundle: AltiumSchHarnessBundle,
        parent_key: str,
        depth: int,
        visited: set[int],
    ) -> None:
        for unique_id in (
            bundle.end_vertex1_connected_connection_point_unique_id,
            bundle.end_vertex2_connected_connection_point_unique_id,
        ):
            key = str(unique_id or "")
            if not key:
                continue
            point = self._connection_by_uid.get(key)
            if point is not None and id(point) not in visited:
                self._charge_topology_work()
                queue.append((point, _covering_uid_key(parent_key), depth, None))

    def _enqueue_connection_bundles(
        self,
        queue: deque[tuple[object, str | None, int, str | None]],
        point: AltiumSchHarnessLayoutConnectionPoint,
        parent_key: str,
        depth: int,
        visited: set[int],
    ) -> None:
        for bundle in self._bundles_by_connection.get(
            _covering_uid_key(str(point.unique_id or "")),
            (),
        ):
            if id(bundle) not in visited:
                self._charge_topology_work()
                queue.append(
                    (
                        bundle,
                        _covering_uid_key(parent_key),
                        depth,
                        bundle.end_vertex1_connected_connection_point_unique_id,
                    )
                )

    def _charge_topology_work(self) -> None:
        self._topology_work += 1
        if self._topology_work > _MAX_HARNESS_COVERING_TOPOLOGY_WORK:
            raise ValueError(
                "harness covering topology exceeds the document work limit of "
                f"{_MAX_HARNESS_COVERING_TOPOLOGY_WORK} queued objects"
            )

    @staticmethod
    def _add_tree_node(
        tree: _HarnessCoveringTree,
        graphical_object: object,
        parent_key: str | None,
        depth: int,
        first_point_id: str | None,
    ) -> None:
        node = _covering_node(
            graphical_object,
            parent_key=parent_key,
            depth=depth,
            first_point_id=first_point_id,
            parent=tree.nodes.get(parent_key or ""),
        )
        tree.nodes[node.key] = node

    def covering_objects(
        self,
        covering: AltiumSchHarnessLayoutCovering,
    ) -> tuple[object, ...] | None:
        self._reserve_path_work(len(covering.covered_items) + 1)
        objects: list[object] = []
        seen: set[int] = set()
        for item in covering.covered_items:
            graphical_object = self._covered_item_object(item)
            if graphical_object is None:
                return None
            if id(graphical_object) in seen:
                continue
            seen.add(id(graphical_object))
            objects.append(graphical_object)
        return tuple(objects)

    def _reserve_path_work(self, reservation: int) -> None:
        if self._path_failed:
            raise RuntimeError(
                "covering path index is unusable after a work-limit failure"
            )
        if reservation > self._max_path_work - self._path_work:
            self._path_failed = True
            raise ValueError("covering path lookup work limit exceeded")
        self._path_work += reservation

    def _covered_item_object(self, item: HarnessCoveredItem) -> object | None:
        return self._object_by_uid.get(item.unique_id)

    def path_for(
        self,
        covering: AltiumSchHarnessLayoutCovering,
    ) -> tuple[_HarnessCoveringNode, ...] | None:
        objects = self.covering_objects(covering)
        if not objects:
            return None
        first_key = _covering_uid_key(str(getattr(objects[0], "unique_id", "") or ""))
        last_key = _covering_uid_key(str(getattr(objects[-1], "unique_id", "") or ""))
        tree = self._tree_by_key.get(first_key) or self._tree_by_key.get(last_key)
        if tree is None:
            return None
        reservation = _covering_path_work_reservation(tree, objects)
        self._reserve_path_work(reservation)
        return tree.path_through(objects)


def _covering_path_work_reservation(
    tree: _HarnessCoveringTree,
    objects: Sequence[object],
) -> int:
    """Conservatively bound sorting and ancestry walks before path lookup."""
    nodes = tuple(
        tree.nodes.get(_covering_uid_key(str(getattr(item, "unique_id", "") or "")))
        for item in objects
    )
    max_depth = max((node.depth for node in nodes if node is not None), default=0)
    item_count = len(objects)
    sort_work = item_count * (max(item_count.bit_length(), 1) + 3)
    ancestry_work = 8 * (max_depth + 1)
    return sort_work + ancestry_work


def _covering_unique_id_map(
    objects: Sequence[object],
    kind: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in objects:
        unique_id = str(getattr(item, "unique_id", "") or "")
        if unique_id in result:
            raise ValueError(f"duplicate harness {kind} UniqueID {unique_id!r}")
        result[unique_id] = item
    return result


def _covering_node(
    graphical_object: object,
    *,
    parent_key: str | None,
    depth: int,
    first_point_id: str | None,
    parent: _HarnessCoveringNode | None,
) -> _HarnessCoveringNode:
    if type(graphical_object) is AltiumSchHarnessLayoutConnectionPoint:
        return _covering_connection_node(
            graphical_object,
            parent_key=parent_key,
            depth=depth,
        )
    bundle = cast(AltiumSchHarnessBundle, graphical_object)
    return _covering_bundle_node(
        bundle,
        parent_key=parent_key,
        depth=depth,
        first_point_id=first_point_id,
        parent=parent,
    )


def _covering_connection_node(
    point: AltiumSchHarnessLayoutConnectionPoint,
    *,
    parent_key: str | None,
    depth: int,
) -> _HarnessCoveringNode:
    return _HarnessCoveringNode(
        graphical_object=point,
        parent_key=parent_key,
        depth=depth,
        location=_harness_internal_location(point.location),
        unique_id=str(point.unique_id or ""),
        start_id="",
        end_id="",
        lines=(),
        has_break_symbol=False,
        break_symbol_segment=0,
        break_symbol_point=(0.0, 0.0),
    )


def _covering_bundle_node(
    bundle: AltiumSchHarnessBundle,
    *,
    parent_key: str | None,
    depth: int,
    first_point_id: str | None,
    parent: _HarnessCoveringNode | None,
) -> _HarnessCoveringNode:
    vertices = tuple(_harness_internal_location(point) for point in bundle.points)
    if len(vertices) < 2:
        raise ValueError("harness covering bundle requires at least two vertices")
    reverse = parent is None or (
        _covering_uid_key(parent.unique_id)
        == _covering_uid_key(str(first_point_id or ""))
    )
    directed = tuple(reversed(vertices)) if reverse else vertices
    lines = _covering_bundle_lines(directed, bundle.line_width)
    start_id = str(bundle.end_vertex1_connected_connection_point_unique_id or "")
    end_id = str(bundle.end_vertex2_connected_connection_point_unique_id or "")
    if reverse:
        start_id, end_id = end_id, start_id
    break_segment, break_point = _covering_break_point(lines)
    location = min(vertices, key=lambda point: (point[0], -point[1]))
    return _HarnessCoveringNode(
        graphical_object=bundle,
        parent_key=parent_key,
        depth=depth,
        location=location,
        unique_id=str(bundle.unique_id or ""),
        start_id=start_id,
        end_id=end_id,
        lines=lines,
        has_break_symbol=bool(bundle.show_break_symbol),
        break_symbol_segment=break_segment,
        break_symbol_point=break_point,
    )


def _covering_bundle_lines(
    vertices: Sequence[tuple[int, int]],
    thickness: LineWidth,
) -> tuple[_HarnessCoveringLine, ...]:
    return tuple(
        _HarnessCoveringLine(point1, point2, thickness)
        for point1, point2 in zip(vertices, vertices[1:], strict=False)
    )


def _covering_break_point(
    lines: Sequence[_HarnessCoveringLine],
) -> tuple[int, tuple[float, float]]:
    segment = max(range(len(lines)), key=lambda index: lines[index].length)
    line = lines[segment]
    return segment, (
        (line.point1[0] + line.point2[0]) / 2.0,
        (line.point1[1] + line.point2[1]) / 2.0,
    )


@dataclass(frozen=True, slots=True)
class HarnessCoveredItem:
    object_type: int
    unique_id: str
    first_pin: str = ""
    last_pin: str = ""


@dataclass(frozen=True, slots=True)
class _HarnessCoveringPathData:
    path: tuple[tuple[int, int], ...]
    height: int
    slope: float
    length: int
    start: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _HarnessCoveringProjection:
    border_polygons: tuple[tuple[tuple[int, int], ...], ...]
    pattern_polygons: tuple[_HarnessCoveringPathData, ...]
    lines: tuple[_HarnessCoveringLine, ...]
    path: tuple[_HarnessCoveringNode, ...]


def _covering_projection(
    covering: AltiumSchHarnessLayoutCovering,
    topology: _HarnessCoveringTopologyIndex,
) -> _HarnessCoveringProjection | None:
    topology._check_geometry_work()
    path = topology.path_for(covering)
    if path is None:
        return None
    covered_length = _checked_covering_sum(node.lines_length for node in path)
    if covered_length == 0:
        return None
    start_distance, end_distance = _covering_active_offsets(covering)
    offset_sum = _unchecked_i32(start_distance + end_distance)
    covering.length = max(_unchecked_i32(covered_length - offset_sum), 0)
    _recalculate_covering_offsets(
        covering,
        topology,
        covered_length,
    )
    _update_covering_path_state(covering, path)
    start_distance, end_distance = _covering_active_offsets(covering)
    topology._reserve_geometry_work(path)
    borders, patterns, lines = _covering_polygons(
        path,
        start_distance=start_distance,
        end_distance=end_distance,
        covering_thickness=covering.thickness,
    )
    if not _covering_should_draw(covering, topology):
        return None
    return _covering_projection_result(borders, patterns, lines, path)


def _update_covering_path_state(
    covering: AltiumSchHarnessLayoutCovering,
    path: tuple[_HarnessCoveringNode, ...],
) -> None:
    old_path = covering._current_covering_path
    covering._current_covering_path = path
    if not _covering_path_was_reversed(old_path, path):
        return
    (
        covering._visual_start_point_distance,
        covering._visual_end_point_distance,
    ) = (
        covering._visual_end_point_distance,
        covering._visual_start_point_distance,
    )
    (
        covering.physical_start_point_distance,
        covering.physical_end_point_distance,
    ) = (
        covering.physical_end_point_distance,
        covering.physical_start_point_distance,
    )


def _covering_path_was_reversed(
    old_path: Sequence[_HarnessCoveringNode] | None,
    new_path: Sequence[_HarnessCoveringNode] | None,
) -> bool:
    if not old_path or not new_path:
        return False
    new_indexes: dict[str, int] = {}
    for index, node in enumerate(new_path):
        new_indexes.setdefault(node.key, index)
    old_indexes = {node.key: index for index, node in enumerate(old_path)}
    for old_index, old_node in enumerate(old_path):
        new_index = new_indexes.get(old_node.key)
        if new_index is None:
            continue
        new_node = new_path[new_index]
        if not new_node.lines:
            continue
        line_result = _covering_line_reversal(old_node.lines[0], new_node.lines[0])
        if line_result is not None:
            return line_result
        adjacency_result = _covering_adjacency_reversal(
            old_indexes,
            new_path,
            old_index,
            new_index,
        )
        if adjacency_result is not None:
            return adjacency_result
        return new_node.reversed_state != old_node.reversed_state
    return False


def _covering_line_reversal(
    old_line: _HarnessCoveringLine,
    new_line: _HarnessCoveringLine,
) -> bool | None:
    if new_line.point1 == old_line.point1 and new_line.point2 == old_line.point2:
        return False
    if new_line.point1 == old_line.point2 and new_line.point2 == old_line.point1:
        return True
    return None


def _covering_adjacency_reversal(
    old_indexes: dict[str, int],
    new_path: Sequence[_HarnessCoveringNode],
    old_index: int,
    new_index: int,
) -> bool | None:
    if new_index + 1 < len(new_path):
        adjacent = old_indexes.get(new_path[new_index + 1].key)
        if adjacent is not None:
            return adjacent < old_index
    if new_index > 0:
        adjacent = old_indexes.get(new_path[new_index - 1].key)
        if adjacent is not None:
            return adjacent >= old_index
    return None


def _covering_should_draw(
    covering: AltiumSchHarnessLayoutCovering,
    topology: _HarnessCoveringTopologyIndex,
) -> bool:
    covered_objects = topology.covering_objects(covering)
    if not covered_objects:
        return False
    return all(bool(getattr(item, "enable_draw", True)) for item in covered_objects)


def _covering_projection_result(
    borders: tuple[tuple[tuple[int, int], ...], ...],
    patterns: tuple[_HarnessCoveringPathData, ...],
    lines: tuple[_HarnessCoveringLine, ...],
    path: tuple[_HarnessCoveringNode, ...],
) -> _HarnessCoveringProjection | None:
    if not borders or not borders[0]:
        return None
    point_count = sum(len(border) for border in borders) + sum(
        len(pattern.path) for pattern in patterns
    )
    if point_count > _MAX_HARNESS_COVERING_POINTS:
        raise ValueError(
            "harness covering geometry exceeds the document limit of "
            f"{_MAX_HARNESS_COVERING_POINTS} points"
        )
    return _HarnessCoveringProjection(borders, patterns, lines, path)


def _autoposition_covering_fields(
    covering: AltiumSchHarnessLayoutCovering,
    projection: _HarnessCoveringProjection,
    designator: AltiumSchDesignator | None,
    comment: AltiumSchParameter | None,
    *,
    comment_y_size: _CoveringCommentYSize,
) -> tuple[AltiumSchDesignator | AltiumSchParameter, ...]:
    active_fields = _active_covering_fields(designator, comment)
    if not active_fields:
        return ()
    location = _covering_designator_location(
        projection.path,
        *_covering_active_offsets(covering),
    )
    if location is None:
        return ()
    default_location = _harness_internal_location(covering.default_designator_position)
    if default_location == (-(1 << 31), -(1 << 31)):
        return _reset_covering_field_positions(
            covering,
            location,
            designator if designator in active_fields else None,
            comment if comment in active_fields else None,
            comment_y_size=comment_y_size,
        )
    delta = (
        _unchecked_i32(location[0] - default_location[0]),
        _unchecked_i32(location[1] - default_location[1]),
    )
    covering.default_designator_position = _covering_coord_point(location)
    _move_covering_field_positions(active_fields, delta)
    return active_fields


def _active_covering_fields(
    designator: AltiumSchDesignator | None,
    comment: AltiumSchParameter | None,
) -> tuple[AltiumSchDesignator | AltiumSchParameter, ...]:
    return tuple(
        field
        for field in (designator, comment)
        if field is not None and field.auto_position
    )


def _move_covering_field_positions(
    fields: tuple[AltiumSchDesignator | AltiumSchParameter, ...],
    delta: tuple[int, int],
) -> None:
    for field in fields:
        field_location = _harness_internal_location(field.location)
        field.location = _covering_coord_point(
            (
                _unchecked_i32_offset(field_location[0], delta[0]),
                _unchecked_i32_offset(field_location[1], delta[1]),
            )
        )
        field.orientation = TextOrientation.DEGREES_0


def _reset_covering_field_positions(
    covering: AltiumSchHarnessLayoutCovering,
    location: tuple[int, int],
    designator: AltiumSchDesignator | None,
    comment: AltiumSchParameter | None,
    *,
    comment_y_size: _CoveringCommentYSize,
) -> tuple[AltiumSchDesignator | AltiumSchParameter, ...]:
    covering.default_designator_position = _covering_coord_point(location)
    moved: list[AltiumSchDesignator | AltiumSchParameter] = []
    if designator is not None:
        designator.location = _covering_coord_point(location)
        designator.orientation = TextOrientation.DEGREES_0
        moved.append(designator)
    if comment is not None:
        resolved_y_size = (
            comment_y_size() if callable(comment_y_size) else comment_y_size
        )
        comment.location = _covering_coord_point(
            (location[0], _unchecked_i32(location[1] - resolved_y_size))
        )
        comment.orientation = TextOrientation.DEGREES_0
        moved.append(comment)
    return tuple(moved)


def _covering_coord_point(location: tuple[int, int]) -> CoordPoint:
    x, x_frac = _split_coord_toward_zero(location[0])
    y, y_frac = _split_coord_toward_zero(location[1])
    return CoordPoint(x, y, x_frac, y_frac)


def _covering_designator_location(
    path: Sequence[_HarnessCoveringNode],
    start_distance: int,
    end_distance: int,
) -> tuple[int, int] | None:
    first_index, start_distance = _covering_node_index(
        path,
        start_distance,
        from_end=False,
    )
    last_index, end_distance = _covering_node_index(
        path,
        end_distance,
        from_end=True,
    )
    longest: _HarnessCoveringLine | None = None
    longest_length = 0
    for index in range(first_index, last_index + 1):
        lines = path[index].lines
        if index == first_index:
            lines = _cut_covering_line_start(lines, start_distance)
        if index == last_index:
            lines = _cut_covering_line_end(lines, end_distance)
        for line in lines:
            if line.length > longest_length:
                longest_length = line.length
                longest = line
    if longest is None:
        return None
    point1 = _covering_to_location(longest.point1)
    point2 = _covering_to_location(longest.point2)
    return (
        _unchecked_i32_offset(
            point1[0],
            _trunc_i32_div(_unchecked_i32(point2[0] - point1[0]), 2),
        ),
        _unchecked_i32_offset(
            point1[1],
            _trunc_i32_div(_unchecked_i32(point2[1] - point1[1]), 2),
        ),
    )


def _checked_covering_sum(values: Iterable[int]) -> int:
    total = 0
    for value in values:
        total += value
        if total > (1 << 31) - 1 or total < -(1 << 31):
            raise OverflowError("harness covering aggregate exceeds Int32")
    return total


def _covering_active_offsets(
    covering: AltiumSchHarnessLayoutCovering,
) -> tuple[int, int]:
    if covering._covering_use_physical_offsets:
        return (
            covering._covering_calculated_start_distance,
            covering._covering_calculated_end_distance,
        )
    return (
        covering.visual_start_point_distance,
        covering.visual_end_point_distance,
    )


def _recalculate_covering_offsets(
    covering: AltiumSchHarnessLayoutCovering,
    topology: _HarnessCoveringTopologyIndex,
    graphical_length: int,
) -> None:
    topology._reserve_path_work(len(covering.covered_items) + 1)
    physical_bundle_length = 0
    for item in covering.covered_items:
        graphical_object = topology._covered_item_object(item)
        if type(graphical_object) is AltiumSchHarnessBundle:
            physical_bundle_length = _unchecked_i64(
                physical_bundle_length + int(graphical_object.length)
            )
    if physical_bundle_length == 0:
        covering.physical_length = 0
        covering._covering_use_physical_offsets = False
        covering._covering_force_recalculate_offsets = False
    elif physical_bundle_length > 0:
        _update_covering_physical_offsets(
            covering,
            graphical_length,
            physical_bundle_length,
        )
    covering._covering_recalculate_offsets = False
    covering._covering_force_recalculate_offsets = False


def _update_covering_physical_offsets(
    covering: AltiumSchHarnessLayoutCovering,
    graphical_length: int,
    physical_bundle_length: int,
) -> None:
    if covering._covering_force_recalculate_offsets or (
        covering._covering_recalculate_offsets and covering.physical_length != 0
    ):
        physical_scale = _f32_ieee_divide(
            _f32(physical_bundle_length),
            _f32(graphical_length),
        )
        covering.physical_start_point_distance = _dotnet_conv_i8(
            _f32(
                _f32(_unchecked_i32(-covering.visual_start_point_distance))
                * physical_scale
            )
        )
        covering.physical_end_point_distance = _dotnet_conv_i8(
            _f32(
                _f32(_unchecked_i32(-covering.visual_end_point_distance))
                * physical_scale
            )
        )
    covering.physical_length = _unchecked_i64(
        physical_bundle_length
        + covering.physical_start_point_distance
        + covering.physical_end_point_distance
    )
    graphical_scale = _f32_ieee_divide(
        _f32(graphical_length),
        _f32(physical_bundle_length),
    )
    covering._covering_calculated_start_distance = _dotnet_conv_i4(
        _f32(
            _f32(_unchecked_i64(-covering.physical_start_point_distance))
            * graphical_scale
        )
    )
    covering._covering_calculated_end_distance = _dotnet_conv_i4(
        _f32(
            _f32(_unchecked_i64(-covering.physical_end_point_distance))
            * graphical_scale
        )
    )
    covering._covering_use_physical_offsets = True


def _covering_polygons(
    path: Sequence[_HarnessCoveringNode],
    *,
    start_distance: int,
    end_distance: int,
    covering_thickness: int,
) -> tuple[
    tuple[tuple[tuple[int, int], ...], ...],
    tuple[_HarnessCoveringPathData, ...],
    tuple[_HarnessCoveringLine, ...],
]:
    borders, patterns, lines, _ = _covering_polygon_geometry(
        path,
        start_distance=start_distance,
        end_distance=end_distance,
        covering_thickness=covering_thickness,
    )
    return borders, patterns, lines


def _covering_polygon_geometry(
    path: Sequence[_HarnessCoveringNode],
    *,
    start_distance: int,
    end_distance: int,
    covering_thickness: int,
) -> tuple[
    tuple[tuple[tuple[int, int], ...], ...],
    tuple[_HarnessCoveringPathData, ...],
    tuple[_HarnessCoveringLine, ...],
    tuple[_HarnessCoveringLine, ...],
]:
    """Return paint lines plus the unsplit strip used by logical endpoints."""
    total_length = _checked_covering_sum(node.lines_length for node in path)
    if _unchecked_i32(start_distance + end_distance) > total_length:
        original_start = start_distance
        start_distance = _unchecked_i32(total_length - end_distance)
        end_distance = _unchecked_i32(total_length - original_start)
    start_distance, end_distance = _correct_covering_offsets(
        start_distance,
        end_distance,
        total_length,
    )
    first_index, start_distance = _covering_node_index(
        path,
        start_distance,
        from_end=False,
    )
    last_index, end_distance = _covering_node_index(
        path,
        end_distance,
        from_end=True,
    )
    retained_path = tuple(path[first_index : last_index + 1])
    logical_lines = _cut_covering_lines(
        tuple(line for node in retained_path for line in node.lines),
        start_distance,
        end_distance,
    )
    if any(node.has_break_symbol for node in retained_path):
        borders, patterns, lines = _covering_multi_polygons(
            retained_path,
            start_distance=start_distance,
            end_distance=end_distance,
            covering_thickness=covering_thickness,
        )
        return borders, patterns, lines, logical_lines
    border, patterns = _covering_simple_polygon(logical_lines, covering_thickness)
    return (border,), patterns, logical_lines, logical_lines


def _correct_covering_offsets(
    start_distance: int,
    end_distance: int,
    available_length: int,
) -> tuple[int, int]:
    if available_length == 0:
        return 0, 0
    excess = _unchecked_i32(
        _unchecked_i32(start_distance + end_distance) - available_length
    )
    if excess <= 0:
        return start_distance, end_distance
    if end_distance == 0:
        return _unchecked_i32(start_distance - excess), end_distance
    end_reduction = float(excess) / (float(start_distance) / end_distance + 1.0)
    start_reduction = float(excess) - end_reduction
    return (
        _unchecked_i32(start_distance - _dotnet_conv_i4(start_reduction)),
        _unchecked_i32(end_distance - _dotnet_conv_i4(end_reduction)),
    )


def _covering_node_index(
    path: Sequence[_HarnessCoveringNode],
    distance: int,
    *,
    from_end: bool,
) -> tuple[int, int]:
    indexes = range(len(path) - 1, -1, -1) if from_end else range(len(path))
    last = -1 if from_end else len(path)
    for index in indexes:
        if path[index].lines_length >= distance:
            return index, distance
        distance -= path[index].lines_length
        last = index - 1 if from_end else index + 1
    return last, distance


def _cut_covering_lines(
    lines: Sequence[_HarnessCoveringLine],
    start_distance: int,
    end_distance: int,
) -> tuple[_HarnessCoveringLine, ...]:
    return _cut_covering_line_end(
        _cut_covering_line_start(lines, start_distance),
        end_distance,
    )


def _cut_covering_line_start(
    lines: Sequence[_HarnessCoveringLine],
    distance: int,
) -> tuple[_HarnessCoveringLine, ...]:
    retained: list[_HarnessCoveringLine] = []
    found = False
    for line in lines:
        if not found and line.length <= distance:
            distance -= line.length
            continue
        if not found:
            found = True
            retained.append(
                _HarnessCoveringLine(
                    _move_covering_point(line.point1, line.point2, distance),
                    line.point2,
                    line.thickness,
                )
            )
        else:
            retained.append(line)
    return tuple(retained)


def _cut_covering_line_end(
    lines: Sequence[_HarnessCoveringLine],
    distance: int,
) -> tuple[_HarnessCoveringLine, ...]:
    reversed_lines = tuple(line.reversed() for line in reversed(lines))
    retained = _cut_covering_line_start(reversed_lines, distance)
    return tuple(line.reversed() for line in reversed(retained))


def _move_covering_point(
    point1: tuple[int | float, int | float],
    point2: tuple[int | float, int | float],
    move_by: int,
) -> tuple[int, int]:
    point1 = _covering_to_location(point1)
    point2 = _covering_to_location(point2)
    if move_by == 0:
        return point1
    if point1[0] == point2[0]:
        direction = -move_by if point1[1] >= point2[1] else move_by
        return point1[0], _unchecked_i32_offset(point1[1], direction)
    if point1[1] == point2[1]:
        direction = -move_by if point1[0] >= point2[0] else move_by
        return _unchecked_i32_offset(point1[0], direction), point1[1]
    delta_x = _unchecked_i32(point2[0] - point1[0])
    delta_y = _unchecked_i32(point2[1] - point1[1])
    length = math.sqrt(float(delta_x) * delta_x + float(delta_y) * delta_y)
    if delta_x == -(1 << 31) or delta_y == -(1 << 31):
        raise OverflowError("managed diagonal component distance overflows Int32")
    move_x = _dotnet_conv_i4(move_by * abs(delta_x) / length)
    move_y = _dotnet_conv_i4(move_by * abs(delta_y) / length)
    return (
        _unchecked_i32_offset(point1[0], move_x if point1[0] < point2[0] else -move_x),
        _unchecked_i32_offset(point1[1], move_y if point1[1] < point2[1] else -move_y),
    )


def _covering_simple_polygon(
    lines: Sequence[_HarnessCoveringLine],
    covering_thickness: int,
    *,
    optimize: bool = True,
) -> tuple[tuple[tuple[int, int], ...], tuple[_HarnessCoveringPathData, ...]]:
    rectangles: list[tuple[tuple[int, int], ...]] = []
    patterns: list[_HarnessCoveringPathData] = []
    for line in lines:
        if line.point1 == line.point2:
            continue
        width = _covering_width(line.thickness, covering_thickness)
        rectangle, pattern = _covering_rectangle(line, width)
        rectangles.append(rectangle)
        patterns.append(pattern)
    border = _linear_covering_border(rectangles)
    if optimize:
        border = _optimize_covering_polygon(border)
        patterns = _optimize_covering_patterns(patterns)
    return border, tuple(patterns)


def _covering_width(line_width: LineWidth, covering_thickness: int) -> int:
    multiplier = {0: 140, 1: 170, 2: 230, 3: 300}.get(
        int(covering_thickness),
        230,
    )
    return _BUS_LINE_WIDTH_INTERNAL[line_width] * multiplier // 100


def _covering_rectangle(
    line: _HarnessCoveringLine,
    width: int,
) -> tuple[tuple[tuple[int, int], ...], _HarnessCoveringPathData]:
    point1 = _covering_to_location(line.point1)
    point2 = _covering_to_location(line.point2)
    point3, point4 = _covering_perpendicular(point1, point2, width)
    point5, point6 = _covering_perpendicular(point2, point1, width)
    points = [point3, point5, point6, point4]
    slope = math.pi - math.atan2(
        _unchecked_i32(point5[1] - point3[1]),
        _unchecked_i32(point5[0] - point3[0]),
    )
    start = _covering_pattern_start(points, slope)
    if _covering_signed_area(points) < 0.0:
        points.reverse()
    path = tuple(points)
    return path, _HarnessCoveringPathData(path, width, slope, line.length, start)


def _covering_perpendicular(
    intersection: tuple[int, int],
    second: tuple[int, int],
    distance: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    half = _trunc_i32_div(distance, 2)
    delta_x = _unchecked_i32(second[0] - intersection[0])
    if delta_x == 0:
        return (
            (_unchecked_i32_offset(intersection[0], -half), intersection[1]),
            (_unchecked_i32_offset(intersection[0], half), intersection[1]),
        )
    delta_y = _unchecked_i32(second[1] - intersection[1])
    if delta_y == 0:
        return (
            (intersection[0], _unchecked_i32_offset(intersection[1], half)),
            (intersection[0], _unchecked_i32_offset(intersection[1], -half)),
        )
    point_x = intersection[0] / 100_000.0
    point_y = intersection[1] / 100_000.0
    radius = half / 100_000.0
    perpendicular_slope = -1.0 / (delta_y / delta_x)
    intercept = point_y - perpendicular_slope * point_x
    a = 1.0 + perpendicular_slope * perpendicular_slope
    b = (
        -2.0 * point_x
        + 2.0 * perpendicular_slope * intercept
        - 2.0 * perpendicular_slope * point_y
    )
    c = (
        point_x * point_x
        + intercept * intercept
        - 2.0 * intercept * point_y
        + point_y * point_y
        - radius * radius
    )
    discriminant = b * b - 4.0 * a * c
    root = math.sqrt(discriminant) if discriminant >= 0.0 else math.nan
    x1 = (-b - root) / (2.0 * a)
    x2 = (-b + root) / (2.0 * a)
    return (
        (
            _dotnet_conv_i4(x1 * 100_000.0),
            _dotnet_conv_i4((perpendicular_slope * x1 + intercept) * 100_000.0),
        ),
        (
            _dotnet_conv_i4(x2 * 100_000.0),
            _dotnet_conv_i4((perpendicular_slope * x2 + intercept) * 100_000.0),
        ),
    )


def _covering_pattern_start(
    points: Sequence[tuple[int, int]],
    slope: float,
) -> tuple[int, int]:
    degrees = slope * 180.0 / math.pi
    max_y = max(point[1] for point in points)
    if 90.0 <= degrees < 180.0 or 270.0 <= degrees < 360.0:
        return min(
            (point for point in points if point[1] == max_y), key=lambda point: point[0]
        )
    return min(point[0] for point in points), max_y


def _covering_signed_area(points: Sequence[tuple[int, int]]) -> float:
    normalized = [(point[0] / 100_000.0, point[1] / 100_000.0) for point in points]
    closed = normalized + normalized[:1]
    return sum(
        (closed[index + 1][0] - closed[index][0])
        * (closed[index + 1][1] + closed[index][1])
        for index in range(len(normalized))
    )


def _linear_covering_border(
    rectangles: Sequence[Sequence[tuple[int, int]]],
) -> tuple[tuple[int, int], ...]:
    first_side = [point for rectangle in rectangles for point in rectangle[:2]]
    second_side = [
        point for rectangle in reversed(rectangles) for point in rectangle[2:]
    ]
    return tuple(first_side + second_side)


def _optimize_covering_polygon(
    polygon: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    remaining = deque(polygon)
    optimized: list[tuple[int, int]] = []
    while len(remaining) > 3:
        optimized.append(remaining.popleft())
        intersection = _covering_segment_intersection(
            optimized[-1],
            remaining[0],
            remaining[1],
            remaining[2],
        )
        if intersection is not None and intersection != (0, 0):
            remaining.popleft()
            remaining.popleft()
            remaining.appendleft(intersection)
    optimized.extend(remaining)
    return tuple(optimized)


def _optimize_covering_patterns(
    patterns: Sequence[_HarnessCoveringPathData],
) -> list[_HarnessCoveringPathData]:
    result = list(patterns)
    for index in range(len(result) - 1):
        first = result[index]
        second = result[index + 1]
        intersection = _covering_segment_intersection(
            first.path[0], first.path[1], second.path[0], second.path[1]
        )
        if intersection is not None and intersection != (0, 0):
            center = _covering_center(first.path[2], second.path[3])
            first_path = [*first.path]
            second_path = [*second.path]
            first_path[1:2] = [intersection, center]
            second_path[0] = intersection
            second_path.append(center)
            result[index] = _replace_covering_path(first, first_path)
            result[index + 1] = _replace_covering_path(second, second_path)
            continue
        intersection = _covering_segment_intersection(
            first.path[2], first.path[3], second.path[2], second.path[3]
        )
        if intersection is None or intersection == (0, 0):
            continue
        center = _covering_center(first.path[1], second.path[0])
        first_path = [*first.path]
        second_path = [*second.path]
        first_path[2:3] = [center, intersection]
        second_path[3] = intersection
        second_path.append(center)
        result[index] = _replace_covering_path(first, first_path)
        result[index + 1] = _replace_covering_path(second, second_path)
    return result


def _replace_covering_path(
    source: _HarnessCoveringPathData,
    path: Sequence[tuple[int, int]],
) -> _HarnessCoveringPathData:
    return _HarnessCoveringPathData(
        tuple(path), source.height, source.slope, source.length, source.start
    )


def _covering_center(
    point1: tuple[int, int],
    point2: tuple[int, int],
) -> tuple[int, int]:
    return (
        _trunc_i32_div(_unchecked_i32(point1[0] + point2[0]), 2),
        _trunc_i32_div(_unchecked_i32(point1[1] + point2[1]), 2),
    )


def _covering_segment_intersection(
    point1: tuple[int, int],
    point2: tuple[int, int],
    point3: tuple[int, int],
    point4: tuple[int, int],
) -> tuple[int, int] | None:
    line1 = (point1, point2)
    line2 = (point3, point4)
    if not _covering_lines_intersect(line1, line2):
        return None
    return _covering_line_intersection(line1, line2)


def _covering_big_line(
    line: tuple[tuple[int, int], tuple[int, int]],
) -> tuple[bool, tuple[tuple[float, float], tuple[float, float]]]:
    is_big = all(abs(value) > 100_000.0 for point in line for value in point)
    if not is_big:
        return False, _covering_float_line(line)
    return True, (
        (line[0][0] / 100_000.0, line[0][1] / 100_000.0),
        (line[1][0] / 100_000.0, line[1][1] / 100_000.0),
    )


def _covering_float_line(
    line: tuple[tuple[int, int], tuple[int, int]],
) -> tuple[tuple[float, float], tuple[float, float]]:
    return (
        (float(line[0][0]), float(line[0][1])),
        (float(line[1][0]), float(line[1][1])),
    )


def _covering_line_direction(
    point1: tuple[float, float],
    point2: tuple[float, float],
    point3: tuple[float, float],
) -> int:
    value = (point2[1] - point1[1]) * (point3[0] - point2[0]) - (
        point2[0] - point1[0]
    ) * (point3[1] - point2[1])
    if abs(value) < 0.001:
        return 0
    return 1 if value >= 0.0 else 2


def _covering_point_on_line(
    line: tuple[tuple[float, float], tuple[float, float]],
    point: tuple[float, float],
    tolerance: float = 0.0001,
) -> bool:
    point1, point2 = line
    if not (
        min(point1[0], point2[0]) <= point[0] <= max(point1[0], point2[0])
        and min(point1[1], point2[1]) <= point[1] <= max(point1[1], point2[1])
    ):
        return False
    delta_x = point2[0] - point1[0]
    if delta_x == 0.0:
        return abs(point[0] - point1[0]) < 0.001
    delta_y = point2[1] - point1[1]
    if delta_y == 0.0:
        return abs(point[1] - point1[1]) < 0.001
    slope = delta_y / delta_x
    intercept = point1[1] - slope * point1[0]
    return abs(point[1] - (slope * point[0] + intercept)) < tolerance


def _covering_lines_intersect(
    line1: tuple[tuple[int, int], tuple[int, int]],
    line2: tuple[tuple[int, int], tuple[int, int]],
) -> bool:
    big1, scaled1 = _covering_big_line(line1)
    big2, scaled2 = _covering_big_line(line2)
    if not (big1 and big2):
        scaled1, scaled2 = _covering_float_line(line1), _covering_float_line(line2)
    direction1 = _covering_line_direction(*scaled1, scaled2[0])
    direction2 = _covering_line_direction(*scaled1, scaled2[1])
    direction3 = _covering_line_direction(*scaled2, scaled1[0])
    direction4 = _covering_line_direction(*scaled2, scaled1[1])
    if direction1 != direction2 and direction3 != direction4:
        return True
    return _covering_collinear_intersection(
        scaled1,
        scaled2,
        (direction1, direction2, direction3, direction4),
    )


def _covering_collinear_intersection(
    line1: tuple[tuple[float, float], tuple[float, float]],
    line2: tuple[tuple[float, float], tuple[float, float]],
    directions: tuple[int, int, int, int],
) -> bool:
    direction1, direction2, direction3, direction4 = directions
    return (
        (direction1 == 0 and _covering_point_on_line(line1, line2[0]))
        or (direction2 == 0 and _covering_point_on_line(line1, line2[1]))
        or (direction3 == 0 and _covering_point_on_line(line2, line1[0]))
        or (direction4 == 0 and _covering_point_on_line(line2, line1[1]))
    )


def _covering_line_intersection(
    line1: tuple[tuple[int, int], tuple[int, int]],
    line2: tuple[tuple[int, int], tuple[int, int]],
) -> tuple[int, int]:
    big1, scaled1 = _covering_big_line(line1)
    big2, scaled2 = _covering_big_line(line2)
    if not (big1 and big2):
        scaled1, scaled2 = _covering_float_line(line1), _covering_float_line(line2)
    point1, point2 = scaled1
    point3, point4 = scaled2
    a1 = point2[1] - point1[1]
    b1 = point1[0] - point2[0]
    c1 = a1 * point1[0] + b1 * point1[1]
    a2 = point4[1] - point3[1]
    b2 = point3[0] - point4[0]
    c2 = a2 * point3[0] + b2 * point3[1]
    determinant = a1 * b2 - a2 * b1
    if abs(determinant) < 0.001:
        return 0, 0
    scale = 100_000 if big1 and big2 else 1
    x = (b2 * c1 - b1 * c2) / determinant
    y = (a1 * c2 - a2 * c1) / determinant
    return _dotnet_conv_i4(x * scale), _dotnet_conv_i4(y * scale)


def _covering_multi_polygons(
    path: Sequence[_HarnessCoveringNode],
    *,
    start_distance: int,
    end_distance: int,
    covering_thickness: int,
) -> tuple[
    tuple[tuple[tuple[int, int], ...], ...],
    tuple[_HarnessCoveringPathData, ...],
    tuple[_HarnessCoveringLine, ...],
]:
    chunks = _covering_break_chunks(path)
    chunks, start_distance, end_distance = _select_covering_break_chunks(
        chunks,
        start_distance,
        end_distance,
    )
    chunks = _trim_covering_break_chunks(
        chunks,
        start_distance,
        end_distance,
    )
    borders: list[tuple[tuple[int, int], ...]] = []
    patterns: list[_HarnessCoveringPathData] = []
    retained_lines: list[_HarnessCoveringLine] = []
    for index, chunk in enumerate(chunks):
        retained_lines.extend(chunk)
        border, chunk_patterns = _covering_simple_polygon(
            chunk,
            covering_thickness,
            optimize=False,
        )
        mutable_border = list(border)
        mutable_patterns = _optimize_covering_patterns(chunk_patterns)
        if not mutable_border or not mutable_patterns:
            continue
        if index > 0:
            _insert_covering_wave_at_start(mutable_border)
            mutable_patterns[0] = _covering_path_with_wave(
                mutable_patterns[0],
                at_start=True,
            )
        if index < len(chunks) - 1:
            _insert_covering_wave_at_end(mutable_border)
            mutable_patterns[-1] = _covering_path_with_wave(
                mutable_patterns[-1],
                at_start=False,
            )
        borders.append(_optimize_covering_polygon(mutable_border))
        patterns.extend(mutable_patterns)
    return tuple(borders or [()]), tuple(patterns), tuple(retained_lines)


def _covering_break_chunks(
    path: Sequence[_HarnessCoveringNode],
) -> list[tuple[_HarnessCoveringLine, ...]]:
    chunks: list[list[_HarnessCoveringLine]] = [[]]
    for node in path:
        if not node.has_break_symbol:
            chunks[-1].extend(node.lines)
            continue
        break_line = node.lines[node.break_symbol_segment]
        break_point = node.break_symbol_point
        chunks[-1].extend(node.lines[: node.break_symbol_segment])
        chunks[-1].append(
            _HarnessCoveringLine(
                break_line.point1,
                break_point,
                break_line.thickness,
            )
        )
        chunks.append(
            [
                _HarnessCoveringLine(
                    break_point,
                    break_line.point2,
                    break_line.thickness,
                ),
                *node.lines[node.break_symbol_segment + 1 :],
            ]
        )
    return [tuple(chunk) for chunk in chunks]


def _covering_to_location(
    point: tuple[int | float, int | float],
) -> tuple[int, int]:
    return int(point[0]), int(point[1])


def _select_covering_break_chunks(
    chunks: Sequence[tuple[_HarnessCoveringLine, ...]],
    start_distance: int,
    end_distance: int,
) -> tuple[list[tuple[_HarnessCoveringLine, ...]], int, int]:
    start_index = 0
    while start_index < len(chunks):
        chunk_length = _checked_covering_sum(
            line.length for line in chunks[start_index]
        )
        if start_distance <= chunk_length:
            break
        difference = start_distance - chunk_length
        if -400_000 <= difference <= 400_000:
            break
        start_distance -= chunk_length
        start_index += 1
    removed_from_end = 0
    while removed_from_end < len(chunks) - start_index:
        chunk = chunks[len(chunks) - 1 - removed_from_end]
        chunk_length = _checked_covering_sum(line.length for line in chunk)
        if end_distance <= chunk_length:
            break
        difference = end_distance - chunk_length
        if -400_000 <= difference <= 400_000:
            break
        end_distance -= chunk_length
        removed_from_end += 1
    stop = len(chunks) - removed_from_end
    return list(chunks[start_index:stop]), start_distance, end_distance


def _trim_covering_break_chunks(
    chunks: Sequence[tuple[_HarnessCoveringLine, ...]],
    start_distance: int,
    end_distance: int,
) -> list[tuple[_HarnessCoveringLine, ...]]:
    if len(chunks) <= 1:
        return (
            [_cut_covering_lines(chunks[0], start_distance, end_distance)]
            if chunks
            else []
        )
    trimmed = list(chunks)
    first = trimmed[0]
    trimmed[0] = _cut_covering_lines(first, start_distance, 400_000)
    if _checked_covering_sum(line.length for line in trimmed[0]) < 400_000:
        adjusted = _checked_covering_sum(line.length for line in first) - 800_000
        trimmed[0] = _cut_covering_lines(first, adjusted, 400_000)
    for index in range(1, len(trimmed) - 1):
        trimmed[index] = _cut_covering_lines(trimmed[index], 400_000, 400_000)
    last = trimmed[-1]
    trimmed[-1] = _cut_covering_lines(last, 400_000, end_distance)
    if _checked_covering_sum(line.length for line in trimmed[-1]) < 400_000:
        adjusted = _checked_covering_sum(line.length for line in last) - 800_000
        trimmed[-1] = _cut_covering_lines(last, 400_000, adjusted)
    return trimmed


def _covering_wave_vector(
    point: tuple[int, int],
    point2: tuple[int, int],
) -> tuple[float, float]:
    vector_x = _f32(_unchecked_i32(point[1] - point2[1]))
    vector_y = _f32(_unchecked_i32(point2[0] - point[0]))
    length = _f32(
        math.sqrt(_f32(_f32(vector_x * vector_x) + _f32(vector_y * vector_y)))
    )
    if length == 0.0:
        return 0.0, 0.0
    return _f32(vector_x / length), _f32(vector_y / length)


def _covering_quarter_points(
    point: tuple[int, int],
    point2: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    return (
        (
            _trunc_i32_div(_unchecked_i32(3 * point[0] + point2[0]), 4),
            _trunc_i32_div(_unchecked_i32(3 * point[1] + point2[1]), 4),
        ),
        (
            _trunc_i32_div(_unchecked_i32(point[0] + 3 * point2[0]), 4),
            _trunc_i32_div(_unchecked_i32(point[1] + 3 * point2[1]), 4),
        ),
    )


def _covering_wave_points(
    point: tuple[int, int],
    point2: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    vector_x, vector_y = _covering_wave_vector(point, point2)
    quarter1, quarter2 = _covering_quarter_points(point, point2)
    offset_x = _dotnet_conv_i4(_f32(vector_x * _f32(100_000)))
    offset_y = _dotnet_conv_i4(_f32(vector_y * _f32(100_000)))
    return (
        (
            _unchecked_i32_offset(quarter1[0], -offset_x),
            _unchecked_i32_offset(quarter1[1], -offset_y),
        ),
        (
            _unchecked_i32_offset(quarter2[0], offset_x),
            _unchecked_i32_offset(quarter2[1], offset_y),
        ),
    )


def _insert_covering_wave_at_end(polygon: list[tuple[int, int]]) -> None:
    middle = len(polygon) // 2
    first, second = _covering_wave_points(polygon[middle], polygon[middle - 1])
    polygon.insert(middle, first)
    polygon.insert(middle, second)


def _insert_covering_wave_at_start(polygon: list[tuple[int, int]]) -> None:
    first, second = _covering_wave_points(polygon[-1], polygon[0])
    polygon.extend((first, second))


def _covering_path_with_wave(
    path_data: _HarnessCoveringPathData,
    *,
    at_start: bool,
) -> _HarnessCoveringPathData:
    path = list(path_data.path)
    if at_start:
        _insert_covering_wave_at_start(path)
    else:
        _insert_covering_wave_at_end(path)
    return _replace_covering_path(path_data, path)


@dataclass(frozen=True)
class _HarnessCoveringPaintState:
    area_color_raw: int
    border_color_raw: int
    opacity: float


def _covering_paint_state(
    covering: AltiumSchHarnessLayoutCovering,
    ctx: SchSvgRenderContext,
) -> _HarnessCoveringPaintState:
    return _HarnessCoveringPaintState(
        area_color_raw=_harness_drawing_color(
            covering,
            int(covering.area_color or 0),
            ctx,
        ),
        border_color_raw=_harness_drawing_color(
            covering,
            int(covering.color or 0),
            ctx,
        ),
        opacity=25.0 / 51.0 if covering.transparent else 1.0,
    )


def _covering_pattern_brush(
    path_data: _HarnessCoveringPathData,
    ctx: SchSvgRenderContext,
    *,
    covering_type: HarnessCoveringType,
    paint_state: _HarnessCoveringPaintState,
    units_per_px: int,
) -> tuple[dict[str, object], dict[str, object]]:
    from .altium_sch_geometry_oracle import make_solid_brush
    from .altium_sch_harness_patterns import harness_covering_pattern_for_type

    resource = harness_covering_pattern_for_type(int(covering_type))
    source_width = float(resource.source_width)
    source_height = float(resource.source_height)
    height = _dotnet_conv_i4(
        float(round(_covering_apply_zoom(ctx, path_data.height, units_per_px)))
    )
    scale = _f32(_f32(height) / _f32(max(source_height, 1.0)))
    tile_width = _f32(_f32(source_width) * scale)
    rendered_length = _dotnet_conv_i4(
        float(round(_covering_apply_zoom(ctx, path_data.length, units_per_px)))
    )
    repeat_count = _f32(
        float(round(_f32(_f32(rendered_length) / _f32(max(tile_width, 1.0)))))
    )
    tile_width = _f32_ieee_divide(_f32(rendered_length), repeat_count)
    export_from_x, export_from_y = _harness_geometry_point(
        ctx,
        path_data.start[0],
        path_data.start[1],
        units_per_px=units_per_px,
    )
    origin_x, origin_y = _harness_geometry_point(
        ctx,
        path_data.path[0][0],
        path_data.path[0][1],
        units_per_px=units_per_px,
    )
    brush = _export_covering_pattern_brush(
        tile_width,
        float(height),
        path_data.slope,
        from_x=_f32(export_from_x),
        from_y=_f32(export_from_y),
    )
    solid = make_solid_brush(paint_state.area_color_raw)
    portable_pattern: dict[str, object] = {
        "schema": "altium.harness_covering_pattern.v1",
        "resource_id": resource.resource_id,
        "origin_x": _f32(origin_x),
        "origin_y": _f32(origin_y),
        "tile_width": tile_width,
        "tile_height": _f32(height),
        "slope_radians": _f32(path_data.slope),
        "opacity": _f32(paint_state.opacity),
        "color_raw": int(solid["color_raw"]),
        "color_hex": str(solid["color_hex"]),
    }
    return brush, portable_pattern


def _covering_apply_zoom(
    ctx: SchSvgRenderContext,
    internal_length: int,
    units_per_px: int,
) -> float:
    from .altium_sch_geometry_oracle import _geometry_item_length

    return _geometry_item_length(
        internal_length * ctx.scale / 100_000,
        units_per_px=units_per_px,
    )


def _f32_ieee_divide(numerator: float, denominator: float) -> float:
    if denominator != 0.0:
        return _f32(numerator / denominator)
    if numerator == 0.0:
        return math.nan
    return math.copysign(
        math.inf, numerator * denominator if denominator else numerator
    )


def _export_covering_pattern_brush(
    width: float,
    height: float,
    slope: float,
    *,
    from_x: float,
    from_y: float,
) -> dict[str, object]:
    cosine = abs(math.cos(slope))
    sine = abs(math.sin(slope))
    pattern_width = _dotnet_conv_i4(width * cosine + height * sine)
    pattern_height = _dotnet_conv_i4(width * sine + height * cosine)
    degrees = slope * 180.0 / math.pi
    if degrees in {0.0, 180.0}:
        to_x, to_y = width, 0.0
    elif degrees in {90.0, 270.0}:
        to_x, to_y = 0.0, float(pattern_height)
    else:
        to_x = width * math.cos(slope)
        to_y = width * math.sin(slope)
    from_x, from_y, to_x, to_y = _orient_covering_pattern_vector(
        degrees,
        from_x,
        from_y,
        to_x,
        to_y,
    )
    return {
        "brush_type": "gbtPattern",
        "color_raw": -16_777_216,
        "color_hex": "#000000",
        "color_to_raw": 0,
        "color_to_hex": "#000000",
        "from_x": from_x,
        "from_y": from_y,
        "to_x": to_x,
        "to_y": to_y,
        "pattern_width": pattern_width,
        "pattern_height": pattern_height,
    }


def _orient_covering_pattern_vector(
    degrees: float,
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
) -> tuple[float, float, float, float]:
    if degrees == 270.0:
        return from_x - to_x, from_y - to_y, to_x, to_y
    if 0.0 <= degrees < 90.0:
        return from_x - to_x, from_y - to_y, to_x, to_y
    if 90.0 <= degrees < 180.0:
        return from_x, from_y - to_y, to_x, to_y
    if degrees == 180.0:
        return from_x - to_x, from_y - to_y, to_x, -to_y
    if 180.0 < degrees < 270.0:
        to_x, to_y = -to_x, -to_y
        return from_x - to_x, from_y - to_y, to_x, to_y
    if 270.0 < degrees < 360.0:
        to_x, to_y = -to_x, -to_y
        return from_x, from_y - to_y, to_x, to_y
    return from_x, from_y, to_x, to_y


def _covering_geometry_points(
    ctx: SchSvgRenderContext,
    points: Sequence[tuple[int | float, int | float]],
    *,
    units_per_px: int,
) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        x, y = _harness_geometry_point(
            ctx,
            point[0],
            point[1],
            units_per_px=units_per_px,
        )
        result.append((_f32(x), _f32(y)))
    return result


def _covering_geometry_operations(
    covering: AltiumSchHarnessLayoutCovering,
    projection: _HarnessCoveringProjection,
    ctx: SchSvgRenderContext,
    *,
    units_per_px: int,
) -> list[SchGeometryOp]:
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen

    paint_state = _covering_paint_state(covering, ctx)
    operations: list[SchGeometryOp] = []
    for pattern in projection.pattern_polygons:
        brush, portable_pattern = _covering_pattern_brush(
            pattern,
            ctx,
            covering_type=covering.covering_type,
            paint_state=paint_state,
            units_per_px=units_per_px,
        )
        operation = SchGeometryOp.polygons(
            [
                _covering_geometry_points(
                    ctx,
                    pattern.path,
                    units_per_px=units_per_px,
                )
            ],
            brush=brush,
        )
        operation.payload["portable_pattern"] = portable_pattern
        operations.append(operation)
    if covering.draw_border:
        pen = make_pen(
            paint_state.border_color_raw,
            width=_f32(
                _connection_pen_width(
                    ctx,
                    _SYMBOL_LINE_WIDTH_INTERNAL[covering.border],
                    units_per_px,
                )
            ),
        )
        operations.extend(
            SchGeometryOp.polygons(
                [
                    _covering_geometry_points(
                        ctx,
                        border,
                        units_per_px=units_per_px,
                    )
                ],
                pen=pen,
            )
            for border in projection.border_polygons
        )
    if (
        covering.covering_type is not HarnessCoveringType.HARNESS_BRUSH
        and covering.covering_closure_type is HarnessCoveringClosureType.SLIT
    ):
        slit_pen = make_pen(
            0xFFFFFF,
            width=_f32(_connection_pen_width(ctx, 20_000, units_per_px)),
        )
        operations.extend(
            SchGeometryOp.lines(
                _covering_geometry_points(
                    ctx,
                    (
                        _covering_to_location(line.point1),
                        _covering_to_location(line.point2),
                    ),
                    units_per_px=units_per_px,
                ),
                pen=slit_pen,
            )
            for line in projection.lines
        )
    return operations


class AltiumSchHarnessLayoutCovering(
    _HarnessLibraryComponentMixin,
    SchGraphicalObject,
):
    def __init__(self) -> None:
        super().__init__()
        self._reset_covering_transient_state()
        self.color = 0
        self.area_color = 8_421_504
        self.border = LineWidth.SMALL
        self.transparent = True
        self.thickness = 2
        self._visual_start_point_distance = 0
        self._visual_end_point_distance = 0
        self.length = 0
        self.brush = HarnessBrush.NONE
        self.covering_type = HarnessCoveringType.TUBING
        self.covering_closure_type = HarnessCoveringClosureType.STANDARD
        self.designator_locked = False
        self.default_designator_position = CoordPoint(
            -21_474,
            -21_474,
            -83_648,
            -83_648,
        )
        self.physical_start_point_distance = 0
        self.physical_end_point_distance = 0
        self.physical_length = 0
        self._covered_items: tuple[HarnessCoveredItem, ...] = ()
        self.draw_border = True
        self._init_library_component()
        self._source_unique_id = (str(self.unique_id or ""), False, False)
        self._capture_covering_source_state()
        self._capture_graphical_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_LAYOUT_COVERING

    @property
    def visual_start_point_distance(self) -> int:
        return self._visual_start_point_distance

    @visual_start_point_distance.setter
    def visual_start_point_distance(self, value: int) -> None:
        self._visual_start_point_distance = int(value)
        self._covering_recalculate_offsets = True

    @property
    def visual_end_point_distance(self) -> int:
        return self._visual_end_point_distance

    @visual_end_point_distance.setter
    def visual_end_point_distance(self, value: int) -> None:
        self._visual_end_point_distance = int(value)
        self._covering_recalculate_offsets = True

    @property
    def covered_items(self) -> tuple[HarnessCoveredItem, ...]:
        return self._covered_items

    @covered_items.setter
    def covered_items(self, value: Iterable[HarnessCoveredItem]) -> None:
        normalized = tuple(value)
        if normalized != self._covered_items:
            old_counts = Counter(self._covered_items)
            self._covered_items = normalized
            if any(
                item.object_type == 111 and count > old_counts[item]
                for item, count in Counter(normalized).items()
            ):
                self._covering_force_recalculate_offsets = True

    def _reset_covering_transient_state(self) -> None:
        self._covering_calculated_start_distance = 0
        self._covering_calculated_end_distance = 0
        self._covering_use_physical_offsets = False
        self._covering_recalculate_offsets = False
        self._covering_force_recalculate_offsets = False
        self._covering_update_count = 0
        self._current_covering_path: tuple[_HarnessCoveringNode, ...] | None = None
        self._draw_disabled = False
        self._draw_dimmed = False
        self._draw_compilation_masked = False
        self._draw_editable_in_current_view = True

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        topology: _HarnessCoveringTopologyIndex,
        projection: _HarnessCoveringProjection | None = None,
        units_per_px: int = 64,
        child_records: Sequence[SchGeometryRecord] = (),
    ) -> SchGeometryRecord | None:
        """Build the managed covering pattern, border, slit, and children."""
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryRecord,
            wrap_record_operations,
        )

        if not bool(getattr(self, "enable_draw", True)):
            return None
        if projection is None:
            projection = _covering_projection(self, topology)
        if projection is None:
            return None
        operations = _covering_geometry_operations(
            self,
            projection,
            ctx,
            units_per_px=units_per_px,
        )
        operations.extend(_harness_bundle_child_operations(child_records))
        points = [point for polygon in projection.border_polygons for point in polygon]
        bounds = SchGeometryBounds(
            left=min(point[0] for point in points),
            top=max(point[1] for point in points),
            right=max(point[0] for point in points),
            bottom=min(point[1] for point in points),
        )
        unique_id = str(self.unique_id or "")
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_layout_covering",
            object_id="eHarnessCovering",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _capture_covering_source_state(self) -> None:
        self._source_covered_items = self.covered_items
        self._source_border = self.border
        self._source_transparent = self.transparent
        self._source_thickness = self.thickness
        self._source_covering_fields = (
            self.visual_start_point_distance,
            self.visual_end_point_distance,
            self.length,
            self.brush,
            self.covering_type,
            self.covering_closure_type,
            self.designator_locked,
        )

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        self._reset_covering_transient_state()
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        self.color = serializer.read_color(record, Fields.COLOR, default=0)[0]
        self.area_color = serializer.read_color(record, Fields.AREA_COLOR, default=0)[0]
        border, _ = serializer.read_int(record, Fields.BORDER_WIDTH, default=1)
        validate_record_enum_value("BorderWidth", border, 3)
        self.border = LineWidth(border)
        self.transparent = serializer.read_bool(
            record, Fields.TRANSPARENT, default=True
        )[0]
        imported_thickness = serializer.read_int(record, "Thickness", default=2)[0]
        if not 0 <= imported_thickness <= 255:
            raise ValueError("Thickness must fit a managed byte")
        self.thickness = min(imported_thickness, 3)
        self._visual_start_point_distance = serializer.read_int(
            record, "StartPointDistance", default=0
        )[0]
        self._visual_end_point_distance = serializer.read_int(
            record, "EndPointDistance", default=0
        )[0]
        self.length = serializer.read_int(record, "Length", default=0)[0]
        brush = serializer.read_int(record, "HarnessLayoutBraidBrush", default=0)[0]
        validate_record_enum_value("HarnessLayoutBraidBrush", brush, 3)
        self.brush = HarnessBrush(brush)
        covering_type, has_covering_type = serializer.read_int(
            record, "HarnessLayoutCoveringType", default=0
        )
        validate_record_enum_value("HarnessLayoutCoveringType", covering_type, 5)
        self.covering_type = HarnessCoveringType(covering_type)
        closure_type = serializer.read_int(
            record, "HarnessLayoutCoveringClosureType", default=0
        )[0]
        validate_record_enum_value("HarnessLayoutCoveringClosureType", closure_type, 2)
        self.covering_closure_type = HarnessCoveringClosureType(closure_type)
        self.designator_locked = serializer.read_bool(
            record, Fields.DESIGNATOR_LOCKED, default=False
        )[0]
        unique_id, present, used_utf8 = _read_dynamic(
            self, serializer, record, "UniqueID"
        )
        self.unique_id = unique_id or None
        self._source_unique_id = (unique_id, present, used_utf8)
        self._parse_library_component(serializer, record)
        dx, dx_frac, _ = serializer.read_coord(record, "DefaultDesignatorPosition", "X")
        dy, dy_frac, _ = serializer.read_coord(record, "DefaultDesignatorPosition", "Y")
        self.default_designator_position = CoordPoint(dx, dy, dx_frac, dy_frac)
        self.physical_start_point_distance = serializer.read_long(
            record, "PhysicalStartDistance", default=0
        )[0]
        self.physical_end_point_distance = serializer.read_long(
            record, "PhysicalEndDistance", default=0
        )[0]
        self.physical_length = serializer.read_long(
            record, "PhysicalLength", default=0
        )[0]
        self._covered_items = self._parse_covered_items(serializer, record)
        if not has_covering_type:
            self._apply_legacy_brush_type()
        self._capture_covering_source_state()
        self._source_thickness = imported_thickness
        self._capture_graphical_source_state()

    @staticmethod
    def _parse_covered_items(
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> tuple[HarnessCoveredItem, ...]:
        count, _ = serializer.read_int(record, "CoveredItemsCount", default=0)
        if count < 0:
            return ()
        if count > MAX_INDEXED_ITEMS_PER_RECORD:
            raise ValueError(
                f"CoveredItemsCount exceeds {MAX_INDEXED_ITEMS_PER_RECORD} entries"
            )
        object_type = serializer.read_int(record, "CoveredItemType", default=0)[0]
        items: list[HarnessCoveredItem] = []
        for index in range(count):
            unique_id = serializer.read_str(
                record, f"CoveredItemId{index}", default=""
            )[0]
            if object_type == 106:
                items.append(
                    HarnessCoveredItem(
                        object_type,
                        unique_id,
                        serializer.read_str(
                            record, f"CoveredItemFirstPin{index}", default=""
                        )[0],
                        serializer.read_str(
                            record, f"CoveredItemLastPin{index}", default=""
                        )[0],
                    )
                )
            elif object_type == 111:
                items.append(HarnessCoveredItem(object_type, unique_id))
        return tuple(items)

    def _apply_legacy_brush_type(self) -> None:
        self.covering_closure_type = HarnessCoveringClosureType.STANDARD
        if self.brush is HarnessBrush.NONE:
            self.covering_type = HarnessCoveringType.TUBING
        else:
            self.covering_type = HarnessCoveringType.BRAIDING
            self.area_color = {
                HarnessBrush.BLACK_WEAVE: 2_236_962,
                HarnessBrush.YELLOW_WEAVE: 4_764_377,
                HarnessBrush.RED_WEAVE: 192,
            }[self.brush]

    def serialize_to_record(self) -> dict[str, object]:
        self._set_harness_brush_from_covering_type()
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        self._serialize_covering_style(serializer, record)
        self._serialize_covering_identity(serializer, record)
        self._serialize_physical_distances(serializer, record)
        self._serialize_covered_items(serializer, record)
        return self._order_authored_graphical_fields(
            record,
            self._authored_covering_family_order(),
        )

    def _serialize_covering_style(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        _write_sparse_int(
            self,
            serializer,
            record,
            Fields.BORDER_WIDTH,
            self.border.value,
            self._source_border.value,
        )
        self._serialize_managed_family_color(
            record, serializer, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._serialize_managed_family_color(
            record,
            serializer,
            Fields.AREA_COLOR.canonical,
            int(self.area_color or 0),
        )
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.TRANSPARENT,
            self.transparent,
            self._source_transparent,
        )
        _write_sparse_int(
            self,
            serializer,
            record,
            "Thickness",
            self.thickness,
            self._source_thickness,
        )
        self._serialize_covering_kind_fields(serializer, record)

    def _serialize_covering_kind_fields(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        source_fields = self._source_covering_fields
        for field, value, source in (
            (
                "StartPointDistance",
                self.visual_start_point_distance,
                source_fields[0],
            ),
            ("EndPointDistance", self.visual_end_point_distance, source_fields[1]),
            ("Length", self.length, source_fields[2]),
            ("HarnessLayoutBraidBrush", self.brush.value, source_fields[3].value),
            (
                "HarnessLayoutCoveringType",
                self.covering_type.value,
                source_fields[4].value,
            ),
            (
                "HarnessLayoutCoveringClosureType",
                self.covering_closure_type.value,
                source_fields[5].value,
            ),
        ):
            _write_sparse_int(self, serializer, record, field, value, source)
        _write_sparse_bool(
            self,
            serializer,
            record,
            Fields.DESIGNATOR_LOCKED,
            self.designator_locked,
            source_fields[6],
        )

    def _serialize_covering_identity(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        _write_dynamic(
            self,
            serializer,
            record,
            "UniqueID",
            str(self.unique_id or ""),
            self._source_unique_id,
        )
        self._serialize_library_component(serializer, record)
        self._serialize_managed_family_coord(
            record,
            serializer,
            "DefaultDesignatorPosition",
            "X",
            self.default_designator_position.x,
            self.default_designator_position.x_frac,
        )
        self._serialize_managed_family_coord(
            record,
            serializer,
            "DefaultDesignatorPosition",
            "Y",
            self.default_designator_position.y,
            self.default_designator_position.y_frac,
        )

    def _serialize_physical_distances(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        for field, value in (
            ("PhysicalStartDistance", self.physical_start_point_distance),
            ("PhysicalEndDistance", self.physical_end_point_distance),
            ("PhysicalLength", self.physical_length),
        ):
            source = serializer.read_long(self._raw_record or {}, field, default=0)[0]
            if self._raw_record is None or value != source:
                serializer.remove_field(record, field)
                if value:
                    serializer.write_long(record, field, value)

    def _authored_covering_family_order(self) -> tuple[str, ...]:
        covered_fields: list[str] = []
        for index, item in enumerate(self.covered_items):
            covered_fields.append(f"CoveredItemId{index}")
            if item.object_type == 106:
                covered_fields.extend(
                    (f"CoveredItemFirstPin{index}", f"CoveredItemLastPin{index}")
                )
        return (
            "BorderWidth",
            "Color",
            "AreaColor",
            "Transparent",
            "Thickness",
            "StartPointDistance",
            "EndPointDistance",
            "Length",
            "HarnessLayoutBraidBrush",
            "HarnessLayoutCoveringType",
            "HarnessLayoutCoveringClosureType",
            "DesignatorLocked",
            *(_field_name(field) for _, field in _LIBRARY_DYNAMIC_FIELDS[:-1]),
            "NotUseLibraryName",
            "DatabaseTableName",
            "DefaultDesignatorPosition.X",
            "DefaultDesignatorPosition.X_Frac",
            "DefaultDesignatorPosition.Y",
            "DefaultDesignatorPosition.Y_Frac",
            "UniqueID",
            "ComponentKind",
            "PhysicalStartDistance",
            "PhysicalEndDistance",
            "PhysicalLength",
            "CoveredItemsCount",
            "CoveredItemType",
            *covered_fields,
        )

    def _set_harness_brush_from_covering_type(self) -> None:
        if self.covering_type is not HarnessCoveringType.BRAIDING:
            self.brush = HarnessBrush.NONE
            return
        area_color = int(self.area_color or 0)
        red = area_color & 0xFF
        green = (area_color >> 8) & 0xFF
        blue = (area_color >> 16) & 0xFF
        hue = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)[0] * 360
        reference_hues = tuple(
            colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)[0] * 360
            for r, g, b in ((34, 34, 34), (217, 182, 72), (192, 0, 0))
        )
        distances = tuple(
            min(abs(reference - hue), 360 - abs(reference - hue))
            for reference in reference_hues
        )
        self.brush = HarnessBrush(distances.index(min(distances)) + 1)

    def _serialize_covered_items(
        self,
        serializer: AltiumSerializer,
        record: dict[str, object],
    ) -> None:
        if (
            self._raw_record is not None
            and self.covered_items == self._source_covered_items
        ):
            return
        if len(self.covered_items) > MAX_INDEXED_ITEMS_PER_RECORD:
            raise ValueError(
                f"CoveredItemsCount exceeds {MAX_INDEXED_ITEMS_PER_RECORD} entries"
            )
        self._remove_covered_item_fields(record)
        if not self.covered_items:
            return
        if any(item.object_type not in {106, 110, 111} for item in self.covered_items):
            raise ValueError("covered item type must be managed type 106, 110, or 111")
        serializer.write_int(
            record, "CoveredItemsCount", len(self.covered_items), None, force=True
        )
        for index, item in enumerate(self.covered_items):
            self._write_covered_item(serializer, record, index, item)

    @staticmethod
    def _remove_covered_item_fields(record: dict[str, object]) -> None:
        indexed_prefixes = (
            "CoveredItemId",
            "CoveredItemFirstPin",
            "CoveredItemLastPin",
        )
        for key in tuple(record):
            scalar = key.casefold() in {"covereditemscount", "covereditemtype"}
            indexed = any(_is_indexed_field(key, prefix) for prefix in indexed_prefixes)
            if scalar or indexed:
                record.pop(key)

    @staticmethod
    def _write_covered_item(
        serializer: AltiumSerializer,
        record: dict[str, object],
        index: int,
        item: HarnessCoveredItem,
    ) -> None:
        if index == 0:
            serializer.write_int(
                record,
                "CoveredItemType",
                item.object_type,
                None,
                force=True,
            )
        serializer.write_str(
            record,
            f"CoveredItemId{index}",
            item.unique_id,
            None,
            force=True,
        )
        if item.object_type != 106:
            return
        serializer.write_str(
            record,
            f"CoveredItemFirstPin{index}",
            item.first_pin,
            None,
            force=True,
        )
        serializer.write_str(
            record,
            f"CoveredItemLastPin{index}",
            item.last_pin,
            None,
            force=True,
        )

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields


__all__ = [
    "AltiumSchHarnessBundle",
    "AltiumSchHarnessLayoutConnectionPoint",
    "AltiumSchHarnessLayoutCovering",
    "AltiumSchHarnessLayoutLabel",
    "AltiumSchHarnessSplice",
    "HarnessBrush",
    "HarnessCoveredItem",
    "HarnessCoveringClosureType",
    "HarnessCoveringType",
    "HarnessLayoutConnectionPointStyle",
    "HarnessLayoutConnectionPointConnector",
    "HarnessSpliceStyle",
]
