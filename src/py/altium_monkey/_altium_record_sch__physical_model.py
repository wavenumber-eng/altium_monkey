"""Private schematic physical-model record adapters."""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING

from .altium_record_sch__component import AltiumSchComponent
from ._sch_source_admission import _SourceAdmission
from .altium_record_types import (
    CoordPoint,
    IntField,
    LineWidth,
    SchGraphicalObject,
    SchRecordType,
)
from .altium_sch_binding import SingleFontBindableRecordMixin
from .altium_sch_enums import Rotation90
from .altium_sch_record_helpers import (
    _validate_schematic_vertex_counts,
    detect_case_mode_method_from_dotted_uppercase_fields,
    read_indexed_coord,
    validate_indexed_coord,
)
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryOp,
        SchGeometryRecord,
    )
    from .altium_sch_svg_renderer import SchSvgRenderContext


LineViewSegment = tuple[CoordPoint, CoordPoint]


class _HarnessCavityShape(IntEnum):
    CIRCLE = 0
    RECTANGLE = 1


class _HarnessSealingType(IntEnum):
    SEALED = 0
    UNSEALED = 1


def _f32_harness_svg_point(
    ctx: SchSvgRenderContext,
    x: int | float,
    y: int | float,
) -> tuple[float, float]:
    from ._altium_record_sch__harness_layout import _f32

    svg_x, svg_y = ctx.transform_point(float(x) / 100_000, float(y) / 100_000)
    return _f32(svg_x), _f32(svg_y)


def _f32_svg_geometry_point(
    ctx: SchSvgRenderContext,
    point: tuple[float, float],
    *,
    units_per_px: int,
) -> tuple[float, float]:
    from .altium_sch_geometry_oracle import svg_coord_to_geometry

    return svg_coord_to_geometry(
        point[0],
        point[1],
        sheet_height_px=float(ctx.sheet_height or 0.0),
        units_per_px=units_per_px,
    )


def _f32_harness_geometry_point(
    ctx: SchSvgRenderContext,
    x: int | float,
    y: int | float,
    *,
    units_per_px: int,
) -> tuple[float, float]:
    return _f32_svg_geometry_point(
        ctx,
        _f32_harness_svg_point(ctx, x, y),
        units_per_px=units_per_px,
    )


class _AltiumSchLineView(SchGraphicalObject):
    """Managed RECORD 126 line projection used by harness physical models."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[LineViewSegment] = []
        self.orientation: Rotation90 | int = Rotation90.DEG_0
        self.rotation_angle = 0.0
        self._source_lines: tuple[LineViewSegment, ...] = ()
        self._source_location = CoordPoint()
        self._source_orientation = self.orientation
        self._source_rotation_angle = self.rotation_angle

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.LINE_VIEW

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        count, _ = serializer.read_int(record, Fields.LOCATION_COUNT, default=0)
        _validate_schematic_vertex_counts(count, 0)
        self.lines = []
        for index in range(count):
            first = self._read_endpoint(record, index, endpoint=1)
            second = self._read_endpoint(record, index, endpoint=2)
            self.lines.append((first, second))
        orientation, _ = serializer.read_int(record, Fields.ORIENTATION, default=0)
        if not 0 <= orientation <= 0xFF:
            raise ValueError("Orientation must fit an unsigned byte")
        self.orientation = Rotation90(orientation) if orientation <= 3 else orientation
        self.rotation_angle, _ = serializer.read_double(
            record, "RotationAngle", default=0.0
        )
        self._source_lines = tuple(self.lines)
        self._source_location = CoordPoint(
            self.location.x,
            self.location.y,
            self.location.x_frac,
            self.location.y_frac,
        )
        self._source_orientation = self.orientation
        self._source_rotation_angle = self.rotation_angle

    @staticmethod
    def _read_endpoint(
        record: dict[str, object], index: int, *, endpoint: int
    ) -> CoordPoint:
        x, x_frac = read_indexed_coord(record, f"X{endpoint}_{index}")
        y, y_frac = read_indexed_coord(record, f"Y{endpoint}_{index}")
        return CoordPoint(x, y, x_frac, y_frac)

    def serialize_to_record(self) -> dict[str, object]:
        self._validate_lines()
        specialized_changed = self._specialized_state_changed()
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        self._remove_fields_case_insensitively(record, ["UniqueID"])
        if self._location_changed():
            self._serialize_normalized_location(record, serializer)
        if specialized_changed:
            self._remove_line_fields(record)
            self._remove_fields_case_insensitively(
                record,
                ["Orientation", "RotationAngle"],
            )
            self._serialize_lines(record, serializer)
            self._serialize_orientation(record, serializer)
            self._serialize_rotation_angle(record, serializer)
            record = self._order_specialized_fields(record)
        elif not self.lines:
            serializer.remove_field(record, Fields.LOCATION_COUNT)
        return record

    def _specialized_state_changed(self) -> bool:
        return (
            self._raw_record is None
            or tuple(self.lines) != self._source_lines
            or self._location_changed()
            or self.orientation != self._source_orientation
            or self.rotation_angle != self._source_rotation_angle
        )

    def _location_changed(self) -> bool:
        return self._raw_record is None or self.location != self._source_location

    def _serialize_normalized_location(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
    ) -> None:
        self._remove_fields_case_insensitively(
            record,
            [
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
            ],
        )
        location = self._normalized_point(self.location)
        serializer.write_coord(
            record,
            "Location",
            "X",
            location.x,
            location.x_frac,
            force=True,
        )
        serializer.write_coord(
            record,
            "Location",
            "Y",
            location.y,
            location.y_frac,
            force=True,
        )

    def _validate_lines(self) -> None:
        _validate_schematic_vertex_counts(len(self.lines), 0)
        for index, (first, second) in enumerate(self.lines):
            validate_indexed_coord(
                self._normalized_point(first),
                f"X1_{index}",
                f"Y1_{index}",
            )
            validate_indexed_coord(
                self._normalized_point(second),
                f"X2_{index}",
                f"Y2_{index}",
            )

    def _serialize_lines(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        serializer.write_int(
            record,
            Fields.LOCATION_COUNT,
            len(self.lines),
            skip_if_default=True,
            force=True,
        )
        for index, segment in enumerate(self.lines):
            self._write_segment(record, serializer, index, segment)

    def _serialize_orientation(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        orientation = int(self.orientation)
        if not 0 <= orientation <= 0xFF:
            raise ValueError("Orientation must fit an unsigned byte")
        serializer.write_int(
            record,
            Fields.ORIENTATION,
            orientation,
            skip_if_default=True,
            force=True,
        )

    def _serialize_rotation_angle(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        serializer.write_double(
            record,
            "RotationAngle",
            self.rotation_angle,
            skip_if_zero=True,
        )

    @staticmethod
    def _remove_line_fields(record: dict[str, object]) -> None:
        for key in tuple(record):
            folded = key.casefold()
            if folded == "locationcount" or (
                folded.startswith(("x1_", "y1_", "x2_", "y2_"))
                and folded.removesuffix("_frac").split("_", 1)[1].isdigit()
            ):
                record.pop(key)

    @staticmethod
    def _write_segment(
        record: dict[str, object],
        serializer: AltiumSerializer,
        index: int,
        segment: LineViewSegment,
    ) -> None:
        first, second = map(_AltiumSchLineView._normalized_point, segment)
        for name, value in (
            (f"X1_{index}", (first.x, first.x_frac)),
            (f"Y1_{index}", (first.y, first.y_frac)),
            (f"X2_{index}", (second.x, second.x_frac)),
            (f"Y2_{index}", (second.y, second.y_frac)),
        ):
            serializer.write_coord(record, name, "", *value, force=True)

    @staticmethod
    def _normalized_point(point: CoordPoint) -> CoordPoint:
        x, x_frac = _AltiumSchLineView._normalized_axis(point.x, point.x_frac)
        y, y_frac = _AltiumSchLineView._normalized_axis(point.y, point.y_frac)
        return CoordPoint(x, y, x_frac, y_frac)

    @staticmethod
    def _normalized_axis(whole: int, fraction: int) -> tuple[int, int]:
        total = (whole * 100_000 + fraction) & 0xFFFF_FFFF
        if total >= 0x8000_0000:
            total -= 0x1_0000_0000
        normalized_whole = abs(total) // 100_000
        if total < 0:
            normalized_whole = -normalized_whole
        return normalized_whole, total - normalized_whole * 100_000

    def _order_specialized_fields(self, record: dict[str, object]) -> dict[str, object]:
        ordered = (
            "Location.X",
            "Location.X_Frac",
            "Location.Y",
            "Location.Y_Frac",
            "LocationCount",
            *(
                name
                for index in range(len(self.lines))
                for name in (
                    f"X1_{index}",
                    f"X1_{index}_Frac",
                    f"Y1_{index}",
                    f"Y1_{index}_Frac",
                    f"X2_{index}",
                    f"X2_{index}_Frac",
                    f"Y2_{index}",
                    f"Y2_{index}_Frac",
                )
            ),
            "Orientation",
            "RotationAngle",
        )
        return self._order_authored_graphical_fields(record, ordered)

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> SchGeometryRecord:
        from ._altium_record_sch__harness_layout import (
            _f32,
            _connection_pen_width,
            _harness_geometry_point,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            wrap_record_operations,
        )

        internal_lines = self._internal_lines()
        operations = [
            SchGeometryOp.lines(
                [
                    tuple(
                        map(
                            _f32,
                            _harness_geometry_point(
                                ctx,
                                *first,
                                units_per_px=units_per_px,
                            ),
                        )
                    ),
                    tuple(
                        map(
                            _f32,
                            _harness_geometry_point(
                                ctx,
                                *second,
                                units_per_px=units_per_px,
                            ),
                        )
                    ),
                ],
                pen=make_pen(
                    0,
                    width=_f32(_connection_pen_width(ctx, 25_000, units_per_px)),
                ),
            )
            for first, second in internal_lines
        ]
        bounds = self.own_bounds_internal()
        unique_id = str(self.unique_id or "")
        render_group_id = ctx.render_group_id(self)
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="line_view",
            object_id="eLineView",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
                render_group_id=render_group_id or None,
                render_group_identity=ctx.render_group_identity(self),
                render_source_id=id(self),
            ),
        )

    def _internal_lines(
        self,
    ) -> tuple[tuple[tuple[int, int], tuple[int, int]], ...]:
        from ._altium_record_sch__harness_layout import _harness_internal_location

        return tuple(
            (
                _harness_internal_location(first),
                _harness_internal_location(second),
            )
            for first, second in self.lines
        )

    def own_bounds_internal(self) -> SchGeometryBounds | None:
        """Return managed own bounds, excluding only DefaultMaxMinRect lines."""
        from ._altium_record_sch__harness_layout import _harness_internal_location
        from .altium_sch_geometry_oracle import SchGeometryBounds

        sentinel = (
            (2_147_483_647, 2_147_483_647),
            (-2_147_483_647, -2_147_483_647),
        )
        left = bottom = 2_147_483_647
        right = top = -2_147_483_647
        found = False
        for first, second in self.lines:
            start = _harness_internal_location(first)
            end = _harness_internal_location(second)
            if (start, end) == sentinel:
                continue
            found = True
            # Keep the managed sentinel seed: INT_MIN is one unit below its
            # initial right/top, so a point there is not a zero-area rectangle.
            left, bottom = min(left, start[0], end[0]), min(bottom, start[1], end[1])
            right, top = max(right, start[0], end[0]), max(top, start[1], end[1])
        if not found:
            return None
        return SchGeometryBounds(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
        )

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields


class _AltiumSchHarnessCavity(SingleFontBindableRecordMixin, SchGraphicalObject):
    """Managed RECORD 140 cavity child used by cavity-component models."""

    font_id = IntField(default=1)

    def __init__(self) -> None:
        super().__init__()
        self._init_family_dynamic_unique_id()
        self._init_single_font_binding()
        self.text = "1"
        self.font_id = 1
        self._custom_font = False
        self._cavity_shape: _HarnessCavityShape | int = _HarnessCavityShape.CIRCLE
        self.width = 1_574_803
        self.height = 1_574_803
        self.keep_aspect = False
        self.corner_radius = 0
        self.line_width = LineWidth.SMALLEST
        self.insertion_length = 0.0
        self.custom_name_position = False
        self.name_vertical_margin = 0
        self.name_horizontal_margin = 0
        self.runtime_is_connected = False
        self.runtime_connected_wire_color: int | None = None
        self._has_text = False
        self._used_utf8_text = False
        self._capture_cavity_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_CAVITY

    @property
    def custom_font(self) -> bool:
        return self._custom_font

    @custom_font.setter
    def custom_font(self, value: bool) -> None:
        self._custom_font = bool(value)
        if not self._custom_font:
            self.font_id = 1

    @property
    def cavity_shape(self) -> _HarnessCavityShape | int:
        return self._cavity_shape

    @cavity_shape.setter
    def cavity_shape(self, value: _HarnessCavityShape | int) -> None:
        numeric = int(value)
        normalized: _HarnessCavityShape | int = (
            _HarnessCavityShape(numeric) if 0 <= numeric <= 1 else numeric
        )
        if self._cavity_shape != normalized:
            self._cavity_shape = normalized
            if normalized == _HarnessCavityShape.CIRCLE:
                self.height = self.width

    def _capture_cavity_source_state(self) -> None:
        self._cavity_source_state = {
            name: getattr(self, name)
            for name in (
                "text",
                "font_id",
                "custom_font",
                "cavity_shape",
                "width",
                "height",
                "keep_aspect",
                "corner_radius",
                "line_width",
                "insertion_length",
                "custom_name_position",
                "name_vertical_margin",
                "name_horizontal_margin",
            )
        }
        self._cavity_source_state["location"] = (
            self.location.x,
            self.location.y,
            self.location.x_frac,
            self.location.y_frac,
        )
        self._cavity_source_state["color"] = self.color
        self._cavity_source_state["unique_id"] = self.unique_id

    def _cavity_state_changed(self) -> bool:
        current = {
            name: getattr(self, name)
            for name in (
                "text",
                "font_id",
                "custom_font",
                "cavity_shape",
                "width",
                "height",
                "keep_aspect",
                "corner_radius",
                "line_width",
                "insertion_length",
                "custom_name_position",
                "name_vertical_margin",
                "name_horizontal_margin",
            )
        }
        current["location"] = (
            self.location.x,
            self.location.y,
            self.location.x_frac,
            self.location.y_frac,
        )
        current["color"] = self.color
        current["unique_id"] = self.unique_id
        return current != self._cavity_source_state

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self._font_manager = font_manager
        self._public_font_spec = None
        serializer = AltiumSerializer()
        self._parse_family_dynamic_unique_id(serializer, record)
        self.text, self._has_text, self._used_utf8_text = read_dynamic_string_field(
            serializer,
            record,
            self._record,
            "Name",
            default="",
        )
        self.font_id, _ = serializer.read_font_id(
            record, "FontID", font_manager, default=1
        )
        self.custom_font = serializer.read_bool(record, "CustomFont", default=False)[0]
        shape = serializer.read_int(record, "CavityShape", default=0)[0]
        self._validate_byte(shape, "CavityShape")
        self.cavity_shape = shape
        self.width = serializer.read_int(record, "Width", default=0)[0]
        self.height = serializer.read_int(record, "Height", default=0)[0]
        self.keep_aspect = serializer.read_bool(record, "KeepAspect", default=True)[0]
        self.corner_radius = serializer.read_int(record, "CornerXRadius", default=0)[0]
        line_width = serializer.read_int(record, "LineWidth", default=0)[0]
        self._validate_byte(line_width, "LineWidth")
        self.line_width = LineWidth(line_width) if line_width <= 3 else line_width
        self.insertion_length = serializer.read_double(
            record, "InsertionLength", default=0.0
        )[0]
        self.custom_name_position = serializer.read_bool(
            record, "NameCustomPosition", default=False
        )[0]
        self.name_vertical_margin = serializer.read_int(
            record, "Name_CustomPosition_VerticalMargin", default=0
        )[0]
        self.name_horizontal_margin = serializer.read_int(
            record, "Name_CustomPosition_Margin", default=0
        )[0]
        self._apply_imported_color_defaults(area_color=False)
        self._capture_cavity_source_state()

    @staticmethod
    def _validate_byte(value: int, field: str) -> None:
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{field} must fit an unsigned byte")

    def serialize_to_record(self) -> dict[str, object]:
        self._ensure_bound_public_font_ready()
        self._validate_byte(int(self.cavity_shape), "CavityShape")
        self._validate_byte(int(self.line_width), "LineWidth")
        normalized_location = _AltiumSchLineView._normalized_point(self.location)
        source_location = self.location
        self.location = normalized_location
        try:
            record = super().serialize_to_record()
        finally:
            self.location = source_location
        serializer = AltiumSerializer(self._detect_case_mode())
        self._serialize_family_dynamic_unique_id(record, serializer)
        self._serialize_cavity_text(record, serializer)
        self._serialize_cavity_fields(record, serializer)
        return self._order_changed_graphical_family_fields(
            record,
            (
                "Location.X",
                "Location.X_Frac",
                "Location.Y",
                "Location.Y_Frac",
                "Color",
                "UniqueID",
                "Name",
                "FontID",
                "CustomFont",
                "CavityShape",
                "Width",
                "Height",
                "KeepAspect",
                "CornerXRadius",
                "LineWidth",
                "InsertionLength",
                "NameCustomPosition",
                "Name_CustomPosition_VerticalMargin",
                "Name_CustomPosition_Margin",
            ),
            changed=self._cavity_state_changed(),
        )

    def _serialize_cavity_text(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        source = str(self._cavity_source_state["text"])
        if self._raw_record is not None and self.text == source:
            return
        write_dynamic_string_field(
            serializer,
            record,
            "Name",
            self.text,
            raw_record=self._raw_record,
            used_utf8_sidecar=self._used_utf8_text,
            was_present=self._has_text,
            force=self.text != source,
        )

    def _serialize_cavity_fields(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        self._serialize_managed_font_id(
            record,
            serializer,
            "FontID",
            int(self.font_id),
            self._get_fallback_font_manager(),
        )
        for field, attribute in (
            ("CustomFont", "custom_font"),
            ("KeepAspect", "keep_aspect"),
            ("NameCustomPosition", "custom_name_position"),
        ):
            self._write_source_bool(record, serializer, field, attribute)
        for field, attribute in (
            ("CavityShape", "cavity_shape"),
            ("Width", "width"),
            ("Height", "height"),
            ("CornerXRadius", "corner_radius"),
            ("LineWidth", "line_width"),
            ("Name_CustomPosition_VerticalMargin", "name_vertical_margin"),
            ("Name_CustomPosition_Margin", "name_horizontal_margin"),
        ):
            self._write_source_int(record, serializer, field, attribute)
        self._write_source_double(
            record, serializer, "InsertionLength", "insertion_length"
        )

    def _write_source_bool(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: str,
        attribute: str,
    ) -> None:
        value = bool(getattr(self, attribute))
        if (
            self._raw_record is not None
            and value == self._cavity_source_state[attribute]
        ):
            return
        serializer.remove_field(record, field)
        if value:
            serializer.write_bool(record, field, True, None, force=True)

    def _write_source_int(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: str,
        attribute: str,
    ) -> None:
        value = int(getattr(self, attribute))
        if (
            self._raw_record is not None
            and value == self._cavity_source_state[attribute]
        ):
            return
        serializer.remove_field(record, field)
        if value:
            serializer.write_int(record, field, value, None, force=True)

    def _write_source_double(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: str,
        attribute: str,
    ) -> None:
        value = float(getattr(self, attribute))
        if (
            self._raw_record is not None
            and value == self._cavity_source_state[attribute]
        ):
            return
        serializer.remove_field(record, field)
        serializer.write_double(record, field, value, None, skip_if_zero=True)

    def own_bounds_internal(self) -> SchGeometryBounds:
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _unchecked_i32_offset,
        )
        from .altium_sch_geometry_oracle import SchGeometryBounds

        x, y = _harness_internal_location(self.location)
        return SchGeometryBounds(
            left=x,
            top=_unchecked_i32_offset(y, self.height),
            right=_unchecked_i32_offset(x, self.width),
            bottom=y,
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> SchGeometryRecord:
        from ._altium_record_sch__harness_layout import (
            _SYMBOL_LINE_WIDTH_INTERNAL,
            _connection_pen_width,
            _f32,
            _harness_internal_location,
            _trunc_i32_div,
            _unchecked_i32_offset,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            make_solid_brush,
            wrap_record_operations,
        )

        unique_id = str(self.unique_id or "")
        render_group_id = ctx.render_group_id(self)
        x, y = _harness_internal_location(self.location)
        right = _unchecked_i32_offset(x, self.width)
        top = _unchecked_i32_offset(y, self.height)
        corner_x = (
            _trunc_i32_div(self.width, 2)
            if self.cavity_shape == _HarnessCavityShape.CIRCLE
            else self.corner_radius
        )
        corner_y = (
            _trunc_i32_div(self.height, 2)
            if self.cavity_shape == _HarnessCavityShape.CIRCLE
            else self.corner_radius
        )
        center_x = (x + right) / 2.0
        center_y = (y + top) / 2.0
        transformed_center = ctx.transform_point(
            float(center_x) / 100_000,
            float(center_y) / 100_000,
        )
        half_width_px = abs(float(ctx.scale)) * (abs(float(right - x)) / 2.0) / 100_000
        half_height_px = abs(float(ctx.scale)) * (abs(float(top - y)) / 2.0) / 100_000
        radius_x_px = (
            abs(float(ctx.scale))
            * min(abs(float(right - x)) / 2.0, abs(float(corner_x)))
            / 100_000
        )
        radius_y_px = (
            abs(float(ctx.scale))
            * min(abs(float(top - y)) / 2.0, abs(float(corner_y)))
            / 100_000
        )
        stored_center = _f32_svg_geometry_point(
            ctx,
            transformed_center,
            units_per_px=units_per_px,
        )
        radius_x = _geometry_item_length(radius_x_px, units_per_px=units_per_px)
        radius_y = _geometry_item_length(radius_y_px, units_per_px=units_per_px)
        line_width = LineWidth(int(self.line_width))
        connectivity = self._cavity_view_type(ctx._source_admission) == "eConnectivity"
        fill_color = int(ctx.sheet_area_color)
        if connectivity and self.runtime_connected_wire_color is not None:
            fill_color = int(self.runtime_connected_wire_color)
        operations = [
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=stored_center[0],
                center_y=stored_center[1],
                half_width=_geometry_item_length(
                    half_width_px, units_per_px=units_per_px
                ),
                half_height=_geometry_item_length(
                    half_height_px, units_per_px=units_per_px
                ),
                corner_x_radius=radius_x,
                corner_y_radius=radius_y,
                brush=make_solid_brush(fill_color),
            ),
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=stored_center[0],
                center_y=stored_center[1],
                half_width=_geometry_item_length(
                    half_width_px, units_per_px=units_per_px
                ),
                half_height=_geometry_item_length(
                    half_height_px, units_per_px=units_per_px
                ),
                corner_x_radius=radius_x,
                corner_y_radius=radius_y,
                pen=make_pen(
                    int(self.color or 0),
                    width=_f32(
                        _connection_pen_width(
                            ctx,
                            _SYMBOL_LINE_WIDTH_INTERNAL[line_width],
                            units_per_px,
                        )
                    ),
                ),
            ),
        ]
        if connectivity and not self.runtime_is_connected:
            operations.extend(
                self._not_connected_operations(ctx, x=x, y=y, units_per_px=units_per_px)
            )
        else:
            name_operation = self._name_operation(
                ctx, x=x, y=y, units_per_px=units_per_px
            )
            if name_operation is not None:
                operations.append(name_operation)
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_cavity",
            object_id="eHarnessCavity",
            bounds=self.own_bounds_internal(),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
                render_group_id=render_group_id or None,
                render_group_identity=ctx.render_group_identity(self),
                render_source_id=id(self),
            ),
        )

    def _not_connected_operations(
        self,
        ctx: SchSvgRenderContext,
        *,
        x: int,
        y: int,
        units_per_px: int,
    ) -> list[SchGeometryOp]:
        from ._altium_record_sch__harness_layout import (
            _connection_line_operation,
            _trunc_i32_div,
            _unchecked_i32_offset,
        )

        quarter = _trunc_i32_div(min(self.width, self.height), 4)
        center_x = _unchecked_i32_offset(x, _trunc_i32_div(self.width, 2))
        center_y = _unchecked_i32_offset(y, _trunc_i32_div(self.height, 2))
        offsets = (
            (-quarter, -quarter, quarter, quarter),
            (-quarter, quarter, quarter, -quarter),
        )
        return [
            _connection_line_operation(
                ctx,
                point1=(
                    _unchecked_i32_offset(center_x, x1),
                    _unchecked_i32_offset(center_y, y1),
                ),
                point2=(
                    _unchecked_i32_offset(center_x, x2),
                    _unchecked_i32_offset(center_y, y2),
                ),
                color=255,
                width=150_000,
                units_per_px=units_per_px,
            )
            for x1, y1, x2, y2 in offsets
        ]

    def _name_operation(
        self,
        ctx: SchSvgRenderContext,
        *,
        x: int,
        y: int,
        units_per_px: int,
    ) -> SchGeometryOp | None:
        from ._altium_record_sch__harness_layout import (
            _f32,
            _float_to_i32,
            _trunc_i32_div,
            _unchecked_i32,
            _unchecked_i32_offset,
            _utf16_code_unit_prefix,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_solid_brush,
        )
        from .altium_text_metrics import measure_gdi_typographic_bounds

        if not self.text:
            return None
        font = ctx.get_font_info(int(self.font_id))
        font_name, font_size_px, is_bold, is_italic, is_underline = font
        width_px, height_px = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(self.text, 8192),
            ctx.get_font_size_for_width(int(self.font_id)),
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        measured_width = _f32(width_px * 100_000)
        measured_height = _f32(height_px * 100_000)
        text_x = _unchecked_i32(
            x
            + _trunc_i32_div(self.width, 2)
            - _float_to_i32(_f32(measured_width / _f32(2.0)), rounded=False)
        )
        text_y = _unchecked_i32(
            y
            + _trunc_i32_div(self.height, 2)
            + _float_to_i32(_f32(measured_height / _f32(2.0)), rounded=False)
        )
        if self.custom_name_position:
            text_x = _unchecked_i32_offset(text_x, self.name_horizontal_margin)
            text_y = _unchecked_i32_offset(text_y, self.name_vertical_margin)
        text_point = _f32_harness_geometry_point(
            ctx, text_x, text_y, units_per_px=units_per_px
        )
        return SchGeometryOp.string(
            x=text_point[0],
            y=text_point[1],
            text=self.text,
            font=make_font_payload(
                name=font_name,
                size_px=font_size_px,
                units_per_px=units_per_px,
                underline=is_underline,
                italic=is_italic,
                bold=is_bold,
            ),
            brush=make_solid_brush(int(self.color or 0)),
        )

    def _cavity_view_type(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> str:
        current = source_admission.parent(self)
        seen = {id(self)}
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if type(current).__name__ == "AltiumSchImageParameter":
                return str(getattr(current, "cavity_view_type", ""))
            current = source_admission.parent(current)
        return ""

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields


class _AltiumSchHarnessCavityComponent(AltiumSchComponent):
    """Managed RECORD 141 component container for physical cavity models."""

    def __init__(self) -> None:
        super().__init__()
        self.sealing_type: _HarnessSealingType | int = _HarnessSealingType.SEALED
        self.built_in_crimp = False
        self._source_sealing_type = self.sealing_type
        self._source_built_in_crimp = self.built_in_crimp

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.HARNESS_CAVITY_COMPONENT

    def parse_from_record(
        self,
        record: dict[str, object],
        font_manager: FontIDManager | None = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        serializer = AltiumSerializer()
        sealing_type = serializer.read_int(record, "HarnessSealingType", default=0)[0]
        _AltiumSchHarnessCavity._validate_byte(sealing_type, "HarnessSealingType")
        self.sealing_type = (
            _HarnessSealingType(sealing_type) if sealing_type <= 1 else sealing_type
        )
        self.built_in_crimp = serializer.read_bool(
            record, "HarnessBuiltInCrimp", default=False
        )[0]
        self._source_sealing_type = self.sealing_type
        self._source_built_in_crimp = self.built_in_crimp

    def serialize_to_record(self) -> dict[str, object]:
        sealing_type = int(self.sealing_type)
        _AltiumSchHarnessCavity._validate_byte(sealing_type, "HarnessSealingType")
        record = super().serialize_to_record()
        serializer = AltiumSerializer(self._detect_case_mode())
        if self._raw_record is None or self.sealing_type != self._source_sealing_type:
            serializer.remove_field(record, "HarnessSealingType")
            if sealing_type:
                serializer.write_int(
                    record, "HarnessSealingType", sealing_type, None, force=True
                )
        if (
            self._raw_record is None
            or self.built_in_crimp != self._source_built_in_crimp
        ):
            serializer.remove_field(record, "HarnessBuiltInCrimp")
            if self.built_in_crimp:
                serializer.write_bool(
                    record, "HarnessBuiltInCrimp", True, None, force=True
                )
        return self._order_changed_graphical_family_fields(
            record,
            ("HarnessSealingType", "HarnessBuiltInCrimp"),
            changed=(
                self.sealing_type != self._source_sealing_type
                or self.built_in_crimp != self._source_built_in_crimp
            ),
        )

    def _geometry_graphics(self) -> list[object]:
        graphics = super()._geometry_graphics()
        graphic_ids = {id(child) for child in graphics}
        graphics.extend(
            child
            for child in self.children
            if isinstance(
                child, (_AltiumSchHarnessCavity, _AltiumSchHarnessCavityComponent)
            )
            and id(child) not in graphic_ids
        )
        return graphics

    def _ordered_geometry_children(
        self,
        *,
        sort_transparency: bool = True,
        record_filter: frozenset[SchRecordType] | None = None,
        exclude_records: frozenset[SchRecordType] = frozenset(),
        max_sort_work: int | None = None,
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> list[tuple[str, object]]:
        del sort_transparency, record_filter, exclude_records, max_sort_work
        source_children, child_kinds = self._geometry_source_children(source_admission)
        return [
            (child_kinds[id(child)], child)
            for child in source_admission.admitted(source_children)
        ]

    def own_bounds_internal(
        self, *, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> SchGeometryBounds:
        from ._altium_record_sch__harness_layout import (
            _harness_internal_location,
            _unchecked_i32_offset,
        )
        from .altium_sch_geometry_oracle import SchGeometryBounds

        bounds: SchGeometryBounds | None = None
        stack = list(reversed(tuple(source_admission.children(self, self.children))))
        seen = {id(self)}
        while stack:
            child = stack.pop()
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            if not source_admission.admits(child) or not self._bound_child_is_visible(
                child, source_admission=source_admission
            ):
                continue
            stack.extend(
                reversed(
                    tuple(
                        source_admission.children(child, getattr(child, "children", ()))
                    )
                )
            )
            if isinstance(child, _AltiumSchHarnessCavityComponent):
                continue
            get_bounds = getattr(child, "own_bounds_internal", None)
            child_bounds = get_bounds() if callable(get_bounds) else None
            if not isinstance(child_bounds, SchGeometryBounds):
                continue
            bounds = (
                child_bounds
                if bounds is None
                else SchGeometryBounds(
                    left=min(bounds.left, child_bounds.left),
                    top=max(bounds.top, child_bounds.top),
                    right=max(bounds.right, child_bounds.right),
                    bottom=min(bounds.bottom, child_bounds.bottom),
                )
            )
        if bounds is not None:
            return bounds
        x, y = _harness_internal_location(self.location)
        return SchGeometryBounds(
            left=_unchecked_i32_offset(x, -500_000),
            top=_unchecked_i32_offset(y, 500_000),
            right=_unchecked_i32_offset(x, 500_000),
            bottom=_unchecked_i32_offset(y, -500_000),
        )

    def _bound_child_is_visible(
        self, child: object, *, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> bool:
        parent = (
            source_admission.parent_by_source_id.get(id(child))
            if source_admission.parent_by_source_id is not None
            else getattr(child, "parent", None)
        )
        if type(parent).__name__ == "AltiumSchImageParameter":
            return False
        if not isinstance(parent, _AltiumSchHarnessCavityComponent):
            return not bool(getattr(child, "is_hidden", False))
        child_type = type(child).__name__
        is_hidden = bool(getattr(child, "is_hidden", False))
        if is_hidden:
            return False
        if child_type == "AltiumSchPin":
            return self._bound_pin_is_current(child, parent)
        if child_type in ("AltiumSchDesignator", "AltiumSchParameter"):
            return True
        if child_type == "AltiumSchImageParameter":
            return True
        return self._bound_generic_child_is_current(child, parent)

    @staticmethod
    def _bound_pin_is_current(
        child: object, parent: _AltiumSchHarnessCavityComponent
    ) -> bool:
        if int(getattr(child, "owner_part_display_mode", 0) or 0) != int(
            parent.display_mode
        ):
            return False
        owner_part = int(getattr(child, "owner_part_id", 0) or 0)
        return owner_part in (0, parent.current_part_id) or parent.current_part_id == -1

    @staticmethod
    def _bound_generic_child_is_current(
        child: object, parent: _AltiumSchHarnessCavityComponent
    ) -> bool:
        display_mode = int(getattr(child, "owner_part_display_mode", 0) or 0)
        owner_part = int(getattr(child, "owner_part_id", 0) or 0)
        return display_mode == int(parent.display_mode) and (
            owner_part == parent.current_part_id or parent.current_part_id == -1
        )

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> SchGeometryRecord:
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        unique_id = str(self.unique_id or "")
        render_group_id = ctx.render_group_id(self)
        child_operations = self._iterative_child_geometry_operations(
            ctx, document_id=document_id, units_per_px=units_per_px
        )
        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="harness_cavity_component",
            object_id="eHarnessCavityComponent",
            bounds=self.own_bounds_internal(source_admission=ctx._source_admission),
            operations=wrap_record_operations(
                unique_id,
                child_operations,
                units_per_px=units_per_px,
                render_group_id=render_group_id or None,
                render_group_identity=ctx.render_group_identity(self),
                render_source_id=id(self),
            ),
        )

    def _iterative_child_geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> list[SchGeometryOp]:
        from .altium_sch_geometry_oracle import SchGeometryOp

        operations: list[SchGeometryOp] = []
        stack: list[tuple[str, _AltiumSchHarnessCavityComponent, str, object]] = []
        seen = {id(self)}
        self._push_component_children(stack, self, ctx._source_admission)
        while stack:
            action, owner, child_kind, child = stack.pop()
            if action == "end":
                operations.append(SchGeometryOp.end_group())
                continue
            if isinstance(child, _AltiumSchHarnessCavityComponent):
                child_id = id(child)
                if child_id in seen:
                    continue
                seen.add(child_id)
                operations.append(
                    SchGeometryOp.begin_group(
                        str(child.unique_id or ""),
                        render_group_id=ctx.render_group_id(child) or None,
                        render_group_identity=ctx.render_group_identity(child),
                        render_source_id=id(child),
                    )
                )
                stack.append(("end", owner, child_kind, child))
                self._push_component_children(stack, child, ctx._source_admission)
                continue
            child_geometry = self._child_geometry_operations(
                child_kind,
                child,
                ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if not child_geometry:
                continue
            if (
                child_kind == "pin"
                and owner.part_count > 1
                and getattr(ctx, "native_svg_export", False)
            ):
                operations.extend(
                    owner._native_export_component_junction_ops(
                        child,
                        child_geometry,
                        ctx,
                        units_per_px=units_per_px,
                    )
                )
            operations.append(
                SchGeometryOp.begin_group(
                    str(getattr(child, "unique_id", "") or ""),
                    render_group_id=ctx.render_group_id(child) or None,
                    render_group_identity=ctx.render_group_identity(child),
                    render_source_id=id(child),
                )
            )
            operations.extend(child_geometry)
            operations.append(SchGeometryOp.end_group())
        return operations

    @staticmethod
    def _push_component_children(
        stack: list[tuple[str, _AltiumSchHarnessCavityComponent, str, object]],
        component: _AltiumSchHarnessCavityComponent,
        source_admission: _SourceAdmission,
    ) -> None:
        for child_kind, child in reversed(
            component._ordered_geometry_children(source_admission=source_admission)
        ):
            stack.append(("child", component, child_kind, child))
