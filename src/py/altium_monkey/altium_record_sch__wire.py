"""Schematic record model for SchRecordType.WIRE."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchPointMils,
    SchRecordType,
    color_to_hex,
    rgb_to_win32_color,
)
from ._sch_managed_defaults import WIRE_COLOR
from .altium_serializer import (
    AltiumSerializer,
    CaseMode,
    FieldDef,
    Fields,
    read_dynamic_string_field,
    write_dynamic_string_field,
)
from .altium_sch_record_helpers import (
    _validate_schematic_vertex_counts,
    _validate_schematic_vertex_total,
    read_indexed_coord,
    validate_indexed_coord,
)


def wire_like_junction_geometry_ops(
    geometry_points: list[tuple[float, float]],
    *,
    source_points: list[tuple[int, int]],
    connection_points: set[tuple[int, int]],
    suppressed_points: set[tuple[int, int]] | None = None,
    units_per_px: int,
    size_px: float,
    color_raw: int,
) -> list[Any]:
    """
    Build oracle-style rounded-rectangle junction ops for matching path vertices.
    """
    from .altium_sch_geometry_oracle import (
        SchGeometryOp,
        _geometry_item_length,
        make_pen,
        make_solid_brush,
    )

    radius_units = _geometry_item_length(size_px / 2.0, units_per_px=units_per_px)
    junction_brush = make_solid_brush(color_raw)
    junction_pen = make_pen(color_raw)
    operations = []
    suppressed_points = suppressed_points or set()
    for (geometry_x, geometry_y), source_point in zip(
        geometry_points, source_points, strict=False
    ):
        if source_point not in connection_points:
            continue
        if source_point in suppressed_points:
            continue
        operations.append(
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=geometry_x,
                center_y=geometry_y,
                half_width=radius_units,
                half_height=radius_units,
                corner_x_radius=radius_units,
                corner_y_radius=radius_units,
                brush=junction_brush,
            )
        )
        operations.append(
            SchGeometryOp.rounded_rectangle_from_item(
                center_x=geometry_x,
                center_y=geometry_y,
                half_width=radius_units,
                half_height=radius_units,
                corner_x_radius=radius_units,
                corner_y_radius=radius_units,
                pen=junction_pen,
            )
        )
    return operations


class AltiumSchWire(SchGraphicalObject):
    """
    Wire/connection record.

    Electrical connection between pins and nets.
    """

    def __init__(self) -> None:
        super().__init__()
        self._init_family_dynamic_unique_id()
        self.points: list[CoordPoint] = []
        self.line_width: LineWidth = LineWidth.SMALL
        self.color = WIRE_COLOR
        self._apply_nonpersisted_area_color_default()
        # Note: Wire does NOT support LineStyle, IsSolid, Transparent (per native file format implementation)
        # Wire-specific fields (per native file format implementation)
        self.underline_color: int = 0
        self.assigned_interface: str = ""
        self.assigned_interface_signal: str = ""
        # Detached SchDoc records serialize in PascalCase; parsed records preserve
        # the original source style in parse_from_record().
        self._use_pascal_case: bool = True
        self._has_line_width: bool = False
        self._has_underline_color: bool = False
        self._has_assigned_interface: bool = False
        self._used_utf8_assigned_interface: bool = False
        self._has_assigned_interface_signal: bool = False
        self._used_utf8_assigned_interface_signal: bool = False
        self._source_points: tuple[CoordPoint, ...] = ()
        self._source_line_width: LineWidth = self.line_width
        self._source_underline_color: int = self.underline_color
        self._source_assigned_interface: str = self.assigned_interface
        self._source_assigned_interface_signal: str = self.assigned_interface_signal
        self._capture_graphical_source_state()

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.WIRE

    def add_point(self, x_mils: float, y_mils: float) -> None:
        """
        Add a point to the wire in mils.
        """
        _validate_schematic_vertex_total(len(self.points) + 1)
        self.points.append(CoordPoint.from_mils(x_mils, y_mils))

    @property
    def points_mils(self) -> list[SchPointMils]:
        """
        Public wire-like path points expressed in mils.
        """
        return [
            SchPointMils.from_mils(point.x_mils, point.y_mils) for point in self.points
        ]

    @points_mils.setter
    def points_mils(self, value: list[SchPointMils]) -> None:
        if not isinstance(value, list):
            raise TypeError("points_mils must be a list of SchPointMils values")
        _validate_schematic_vertex_total(len(value))
        converted: list[CoordPoint] = []
        for point in value:
            if not isinstance(point, SchPointMils):
                raise TypeError("points_mils must contain only SchPointMils values")
            converted.append(point.to_coord_point())
        self.points = converted

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse from a record.
        """
        super().parse_from_record(record, font_manager)

        # Detect if this is PascalCase (native JSON) or UPPERCASE (Altium binary)
        self._use_pascal_case = "LocationCount" in record or "LineWidth" in record

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()
        # Read through the indexed case-insensitive copy captured by the base
        # parse; case-insensitive lookups on the raw dict degrade to key scans.
        r = self._record

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            r, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        # Note: Wire does NOT support LineStyle, IsSolid, Transparent - ignore if present

        # Wire-specific fields (per native file format implementation)
        underline_val, self._has_underline_color = s.read_color(
            r, Fields.UNDERLINE_COLOR, default=0
        )
        self.underline_color = int(underline_val or 0)
        (
            self.assigned_interface,
            self._has_assigned_interface,
            self._used_utf8_assigned_interface,
        ) = read_dynamic_string_field(
            s, record, r, Fields.ASSIGNED_INTERFACE, default=""
        )
        (
            self.assigned_interface_signal,
            self._has_assigned_interface_signal,
            self._used_utf8_assigned_interface_signal,
        ) = read_dynamic_string_field(
            s, record, r, Fields.ASSIGNED_INTERFACE_SIGNAL, default=""
        )
        self._parse_family_dynamic_unique_id(s, r)

        # Parse points
        point_count, _ = s.read_int(r, Fields.LOCATION_COUNT, default=0)
        extra_point_count, _ = s.read_int(r, "EXTRALOCATIONCOUNT", default=0)
        _validate_schematic_vertex_counts(point_count, extra_point_count)
        self.points = []

        if point_count > 0 or extra_point_count > 0:
            # Managed SchDataVertices accepts extended points even when the
            # primary count is zero.
            for i in range(point_count):
                x, x_frac = read_indexed_coord(r, f"X{i + 1}")
                y, y_frac = read_indexed_coord(r, f"Y{i + 1}")
                self.points.append(CoordPoint(x, y, x_frac, y_frac))

            for i in range(point_count + 1, point_count + extra_point_count + 1):
                x, x_frac = read_indexed_coord(r, f"EX{i}")
                y, y_frac = read_indexed_coord(r, f"EY{i}")
                self.points.append(CoordPoint(x, y, x_frac, y_frac))
        else:
            # No LocationCount - count X/Y fields manually
            # (membership stays exact-case on the raw record, as before)
            i = 1
            while f"X{i}" in record or f"Y{i}" in record:
                _validate_schematic_vertex_total(i)
                x, x_frac = read_indexed_coord(r, f"X{i}")
                y, y_frac = read_indexed_coord(r, f"Y{i}")
                self.points.append(CoordPoint(x, y, x_frac, y_frac))
                i += 1

        self._source_points = tuple(self.points)
        self._source_line_width = self.line_width
        self._source_underline_color = self.underline_color
        self._source_assigned_interface = self.assigned_interface
        self._source_assigned_interface_signal = self.assigned_interface_signal
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()
        self._remove_non_wire_geometry(record)
        s = AltiumSerializer(
            CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE
        )
        points_changed = (
            self._raw_record is None or tuple(self.points) != self._source_points
        )
        _validate_schematic_vertex_total(len(self.points))
        self._write_point_counts(record, s, points_changed)
        self._write_wire_fields(record, s)
        if points_changed:
            self._write_points(record, points_changed)
            self._remove_stale_points(record)
        self._write_wire_colors(record, s)
        return self._order_authored_graphical_fields(record, self._family_order())

    def _remove_non_wire_geometry(self, record: dict[str, object]) -> None:
        for loc_key in [
            "Location.X",
            "Location.Y",
            "LOCATION.X",
            "LOCATION.Y",
            "Location.X_Frac",
            "Location.Y_Frac",
            "LOCATION.X_FRAC",
            "LOCATION.Y_FRAC",
        ]:
            record.pop(loc_key, None)
        if self._raw_record is None:
            for field in (
                Fields.LINE_STYLE,
                Fields.LINE_STYLE_EXT,
                Fields.IS_SOLID,
                Fields.TRANSPARENT,
            ):
                self._remove_field(record, [field.pascal, field.upper])

    def _write_point_counts(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        points_changed: bool,
    ) -> None:
        main_point_count = min(len(self.points), 50)
        extra_point_count = max(len(self.points) - main_point_count, 0)
        serializer.write_int(
            record,
            Fields.LOCATION_COUNT,
            main_point_count,
            self._raw_record,
            force=points_changed,
        )
        if main_point_count == 0 and (self._raw_record is None or points_changed):
            serializer.remove_field(record, Fields.LOCATION_COUNT)
        if extra_point_count > 0:
            self._update_field(
                record,
                "EXTRALOCATIONCOUNT",
                extra_point_count,
                ["EXTRALOCATIONCOUNT"],
                force=points_changed,
            )
        else:
            self._remove_field(record, ["EXTRALOCATIONCOUNT"])

    def _write_wire_fields(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        self._serialize_managed_family_int(
            record, serializer, Fields.LINE_WIDTH.canonical, self.line_width.value
        )
        self._serialize_managed_family_color(
            record,
            serializer,
            Fields.UNDERLINE_COLOR.canonical,
            self.underline_color or 0,
        )
        self._write_wire_dynamic(
            record,
            serializer,
            Fields.ASSIGNED_INTERFACE,
            self.assigned_interface,
            self._source_assigned_interface,
            self._has_assigned_interface,
            self._used_utf8_assigned_interface,
        )
        self._write_wire_dynamic(
            record,
            serializer,
            Fields.ASSIGNED_INTERFACE_SIGNAL,
            self.assigned_interface_signal,
            self._source_assigned_interface_signal,
            self._has_assigned_interface_signal,
            self._used_utf8_assigned_interface_signal,
        )
        self._serialize_family_dynamic_unique_id(record, serializer)

    def _write_points(self, record: dict[str, object], points_changed: bool) -> None:
        for i, point in enumerate(self.points, 1):
            x_key, y_key = self._point_keys(i)
            validate_indexed_coord(point, x_key, y_key)
            self._update_field(record, x_key, point.x, [x_key], force=points_changed)
            self._update_field(record, y_key, point.y, [y_key], force=points_changed)
            self._write_point_fraction(record, x_key, point.x_frac)
            self._write_point_fraction(record, y_key, point.y_frac)

    @staticmethod
    def _point_keys(index: int) -> tuple[str, str]:
        prefix = "" if index <= 50 else "E"
        return f"{prefix}X{index}", f"{prefix}Y{index}"

    def _write_point_fraction(
        self, record: dict[str, object], key: str, fraction: int
    ) -> None:
        spellings = [f"{key}_Frac", f"{key}_FRAC"]
        if fraction:
            self._update_field(record, spellings[0], fraction, spellings, force=True)
        else:
            self._remove_field(record, spellings)

    def _remove_stale_points(self, record: dict[str, object]) -> None:
        if self._raw_record is None:
            return
        stale_total = int(
            self._raw_record.get(
                "LocationCount", self._raw_record.get("LOCATIONCOUNT", 0)
            )
        ) + int(self._raw_record.get("EXTRALOCATIONCOUNT", 0))
        for i in range(len(self.points) + 1, stale_total + 1):
            x_key, y_key = self._point_keys(i)
            self._remove_field(
                record,
                [
                    x_key,
                    y_key,
                    f"{x_key}_Frac",
                    f"{x_key}_FRAC",
                    f"{y_key}_Frac",
                    f"{y_key}_FRAC",
                ],
            )

    def _write_wire_colors(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        if self._raw_record is None or self.color != self._source_color:
            self._remove_fields_case_insensitively(record, ["Color"])
            if self.color not in (None, 0):
                serializer.write_color(
                    record, Fields.COLOR, self.color, None, force=True
                )
        if self._raw_record is None or self._area_color_dirty:
            self._remove_fields_case_insensitively(record, ["AreaColor"])

    def _family_order(self) -> tuple[str, ...]:
        if self.record_type is SchRecordType.BUS:
            return (
                "LineWidth",
                "Color",
                "UnderlineColor",
                "__Vertices__",
                "UniqueID",
                "AssignedInterface",
                "AssignedInterfaceSignal",
            )
        return (
            "LineWidth",
            "Color",
            "UnderlineColor",
            "UniqueID",
            "AssignedInterface",
            "AssignedInterfaceSignal",
            "__Vertices__",
        )

    def _write_wire_dynamic(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: FieldDef | str,
        value: str,
        source_value: str,
        was_present: bool,
        used_utf8_sidecar: bool,
    ) -> None:
        if self._raw_record is not None and value == source_value:
            return
        field_def = serializer._get_field_def(field)
        if not value:
            self._remove_fields_case_insensitively(
                record, [field_def.pascal, f"%UTF8%{field_def.pascal}"]
            )
            return
        write_dynamic_string_field(
            serializer,
            record,
            field_def,
            value,
            raw_record=self._raw_record,
            used_utf8_sidecar=used_utf8_sidecar,
            was_present=was_present,
            force=True,
        )

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
        kind: str = "wire",
        object_id: str = "eWire",
        default_color_raw: int = 0,
        stroke_width_mils_override: float | None = None,
        junction_color_raw: int = 0x000000,
        junction_size_px: float = 4.0,
    ) -> Any:
        """
        Build an oracle-aligned geometry record for a wire-like path.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            _geometry_item_length,
            make_pen,
            svg_coord_to_geometry,
            wrap_record_operations,
        )
        from .altium_sch_svg_renderer import DEFAULT_LINE_WIDTH, LINE_WIDTH_MILS

        if len(self.points) < 2:
            return None

        raw_points = [(float(point.x), float(point.y)) for point in self.points]
        svg_points = [
            tuple(float(v) for v in ctx.transform_coord_precise(point))
            for point in self.points
        ]
        geometry_points = [
            svg_coord_to_geometry(
                x,
                y,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for x, y in svg_points
        ]

        stroke_width_mils = (
            float(stroke_width_mils_override)
            if stroke_width_mils_override is not None
            else float(LINE_WIDTH_MILS.get(self.line_width, DEFAULT_LINE_WIDTH))
        )
        inflate = max(stroke_width_mils, 1.0)
        min_x = min(point[0] for point in raw_points) - inflate
        max_x = max(point[0] for point in raw_points) + inflate
        min_y = min(point[1] for point in raw_points) - inflate
        max_y = max(point[1] for point in raw_points) + inflate

        color_raw = (
            int(self.color) if self.color is not None else int(default_color_raw)
        )
        if all(
            ctx.is_segment_fully_under_compile_mask(start.x, start.y, end.x, end.y)
            for start, end in zip(self.points, self.points[1:], strict=False)
        ):
            masked_hex = ctx.apply_compile_mask_color(color_to_hex(color_raw), True)
            color_raw = rgb_to_win32_color(
                int(masked_hex[1:3], 16),
                int(masked_hex[3:5], 16),
                int(masked_hex[5:7], 16),
            )
        pen = make_pen(
            color_raw,
            width=_geometry_item_length(stroke_width_mils, units_per_px=units_per_px),
        )

        operations = [SchGeometryOp.lines(geometry_points, pen=pen)]
        operations.extend(
            wire_like_junction_geometry_ops(
                geometry_points,
                source_points=[(point.x, point.y) for point in self.points],
                connection_points=ctx.connection_points,
                suppressed_points=(
                    ctx.explicit_junction_points
                    if getattr(ctx, "native_svg_export", False)
                    else set()
                ),
                units_per_px=units_per_px,
                size_px=junction_size_px,
                color_raw=junction_color_raw,
            )
        )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind=kind,
            object_id=object_id,
            bounds=SchGeometryBounds(
                left=int(round(min_x * 100000)),
                top=int(round(max_y * 100000)),
                right=int(round(max_x * 100000)),
                bottom=int(round(min_y * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )
