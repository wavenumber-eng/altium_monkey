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
from .altium_serializer import AltiumSerializer, CaseMode, Fields


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
    from .altium_sch_geometry_oracle import SchGeometryOp, make_pen, make_solid_brush

    radius_units = size_px * units_per_px / 2.0
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
            SchGeometryOp.rounded_rectangle(
                x1=geometry_x - radius_units,
                y1=geometry_y - radius_units,
                x2=geometry_x + radius_units,
                y2=geometry_y + radius_units,
                corner_x_radius=radius_units,
                corner_y_radius=radius_units,
                brush=junction_brush,
            )
        )
        operations.append(
            SchGeometryOp.rounded_rectangle(
                x1=geometry_x - radius_units,
                y1=geometry_y - radius_units,
                x2=geometry_x + radius_units,
                y2=geometry_y + radius_units,
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
        self.points: list[CoordPoint] = []
        self.line_width: LineWidth = LineWidth.SMALLEST
        # Note: Wire does NOT support LineStyle, IsSolid, Transparent (per native file format implementation)
        # Wire-specific fields (per native file format implementation)
        self.underline_color: int | None = None
        self.assigned_interface: str = ""
        self.assigned_interface_signal: str = ""
        # Detached SchDoc records serialize in PascalCase; parsed records preserve
        # the original source style in parse_from_record().
        self._use_pascal_case: bool = True
        self._has_line_width: bool = False
        self._has_underline_color: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.WIRE

    def add_point(self, x_mils: float, y_mils: float) -> None:
        """
        Add a point to the wire in mils.
        """
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

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        # Note: Wire does NOT support LineStyle, IsSolid, Transparent - ignore if present

        # Wire-specific fields (per native file format implementation)
        underline_val, self._has_underline_color = s.read_int(
            record, Fields.UNDERLINE_COLOR, default=0
        )
        self.underline_color = underline_val if self._has_underline_color else None
        self.assigned_interface, _ = s.read_str(
            record, Fields.ASSIGNED_INTERFACE, default=""
        )
        self.assigned_interface_signal, _ = s.read_str(
            record, Fields.ASSIGNED_INTERFACE_SIGNAL, default=""
        )

        # Parse points
        point_count, _ = s.read_int(record, Fields.LOCATION_COUNT, default=0)
        extra_point_count, _ = s.read_int(record, "EXTRALOCATIONCOUNT", default=0)
        self.points = []

        if point_count > 0:
            # Has explicit LocationCount
            for i in range(point_count):
                x = int(record.get(f"X{i + 1}", 0))
                y = int(record.get(f"Y{i + 1}", 0))
                x_frac = int(
                    record.get(f"X{i + 1}_FRAC", record.get(f"X{i + 1}_Frac", 0))
                )
                y_frac = int(
                    record.get(f"Y{i + 1}_FRAC", record.get(f"Y{i + 1}_Frac", 0))
                )
                self.points.append(CoordPoint(x, y, x_frac, y_frac))

            for i in range(point_count + 1, point_count + extra_point_count + 1):
                x = int(record.get(f"EX{i}", 0))
                y = int(record.get(f"EY{i}", 0))
                x_frac = int(record.get(f"EX{i}_FRAC", record.get(f"EX{i}_Frac", 0)))
                y_frac = int(record.get(f"EY{i}_FRAC", record.get(f"EY{i}_Frac", 0)))
                self.points.append(CoordPoint(x, y, x_frac, y_frac))
        else:
            # No LocationCount - count X/Y fields manually
            i = 1
            while f"X{i}" in record or f"Y{i}" in record:
                x = int(record.get(f"X{i}", 0))
                y = int(record.get(f"Y{i}", 0))
                x_frac = int(record.get(f"X{i}_FRAC", record.get(f"X{i}_Frac", 0)))
                y_frac = int(record.get(f"Y{i}_FRAC", record.get(f"Y{i}_Frac", 0)))
                self.points.append(CoordPoint(x, y, x_frac, y_frac))
                i += 1

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

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

        # Determine case mode
        mode = (
            CaseMode.PASCALCASE
            if getattr(self, "_use_pascal_case", False)
            else CaseMode.UPPERCASE
        )
        s = AltiumSerializer(mode)
        raw = self._raw_record

        # Write location count and line width
        main_point_count = min(len(self.points), 50)
        extra_point_count = max(len(self.points) - main_point_count, 0)
        s.write_int(record, Fields.LOCATION_COUNT, main_point_count, raw)
        if extra_point_count > 0:
            self._update_field(
                record, "EXTRALOCATIONCOUNT", extra_point_count, ["EXTRALOCATIONCOUNT"]
            )
        else:
            self._remove_field(record, ["EXTRALOCATIONCOUNT"])

        if self._has_line_width or self.line_width != LineWidth.SMALLEST:
            s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)
        # Note: Wire does NOT serialize LineStyle, IsSolid, Transparent (per native file format implementation)
        self._remove_field(record, [Fields.LINE_STYLE.pascal, Fields.LINE_STYLE.upper])
        self._remove_field(
            record, [Fields.LINE_STYLE_EXT.pascal, Fields.LINE_STYLE_EXT.upper]
        )
        self._remove_field(record, [Fields.IS_SOLID.pascal, Fields.IS_SOLID.upper])
        self._remove_field(
            record, [Fields.TRANSPARENT.pascal, Fields.TRANSPARENT.upper]
        )

        # Wire-specific fields
        if self.underline_color not in (None, 0):
            s.write_int(
                record,
                Fields.UNDERLINE_COLOR,
                self.underline_color,
                raw,
                force=True,
            )
        else:
            self._remove_field(
                record,
                [Fields.UNDERLINE_COLOR.pascal, Fields.UNDERLINE_COLOR.upper],
            )
        if self.assigned_interface:
            s.write_str(record, Fields.ASSIGNED_INTERFACE, self.assigned_interface, raw)
        else:
            self._remove_field(
                record,
                [
                    Fields.ASSIGNED_INTERFACE.pascal,
                    Fields.ASSIGNED_INTERFACE.upper,
                ],
            )
        if self.assigned_interface_signal:
            s.write_str(
                record,
                Fields.ASSIGNED_INTERFACE_SIGNAL,
                self.assigned_interface_signal,
                raw,
            )
        else:
            self._remove_field(
                record,
                [
                    Fields.ASSIGNED_INTERFACE_SIGNAL.pascal,
                    Fields.ASSIGNED_INTERFACE_SIGNAL.upper,
                ],
            )

        # Write points - indexed field names: X1, Y1, X2, Y2, etc.
        for i, point in enumerate(self.points, 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"

            self._update_field(record, x_key, point.x, [x_key])
            self._update_field(record, y_key, point.y, [y_key])

            if point.x_frac:
                self._update_field(
                    record,
                    f"{x_key}_Frac",
                    point.x_frac,
                    [f"{x_key}_Frac", f"{x_key}_FRAC"],
                )
            else:
                self._remove_field(record, [f"{x_key}_Frac", f"{x_key}_FRAC"])

            if point.y_frac:
                self._update_field(
                    record,
                    f"{y_key}_Frac",
                    point.y_frac,
                    [f"{y_key}_Frac", f"{y_key}_FRAC"],
                )
            else:
                self._remove_field(record, [f"{y_key}_Frac", f"{y_key}_FRAC"])

        stale_total = 0
        if raw is not None:
            stale_total = int(raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)))
            stale_total += int(raw.get("EXTRALOCATIONCOUNT", 0))

        for i in range(len(self.points) + 1, stale_total + 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"

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

        return record

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
        pen = make_pen(color_raw, width=int(round(stroke_width_mils * units_per_px)))

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
