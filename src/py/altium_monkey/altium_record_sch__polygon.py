"""Schematic record model for SchRecordType.POLYGON."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchPointMils,
    SchRecordType,
)
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    detect_case_mode_method_from_dotted_uppercase_fields,
)
from .altium_sch_svg_renderer import (
    LINE_WIDTH_MILS,
    SEMI_TRANSPARENT_ALPHA,
    SchSvgRenderContext,
)


class AltiumSchPolygon(SchGraphicalObject):
    """
    Polygon record.

    Closed polygon graphic.

    Public code should use ``points_mils`` for normal polygon mutation and
    inspection, plus ``line_width``, ``is_solid``, and ``transparent`` for
    stroke/fill state. Raw vertex coords remain internal serializer-facing
    storage.
    """

    def __init__(self) -> None:
        super().__init__()
        self.vertices: list[CoordPoint] = []
        self.line_width: LineWidth = LineWidth.SMALLEST
        # Note: Polygon does NOT support LineStyle (per native file format implementation)
        self.is_solid: bool = (
            False  # New-object defaults are applied via the public factory.
        )
        self.transparent: bool = False
        # Track which fields were present
        self._has_line_width: bool = False
        self._has_is_solid: bool = False
        self._has_transparent: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.POLYGON

    @property
    def points_mils(self) -> list[SchPointMils]:
        """
        Public polygon vertex list expressed in mils.
        """
        return [
            SchPointMils.from_mils(vertex.x_mils, vertex.y_mils)
            for vertex in self.vertices
        ]

    @points_mils.setter
    def points_mils(self, value: list[SchPointMils]) -> None:
        if not isinstance(value, list):
            raise TypeError("points_mils must be a list of SchPointMils values")
        for point in value:
            if not isinstance(point, SchPointMils):
                raise TypeError("points_mils must contain only SchPointMils values")
        self.vertices = [point.to_coord_point() for point in value]

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        """
        Parse from a record.
        """
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading (case-insensitive)
        s = AltiumSerializer()

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        # Note: Polygon does NOT support LineStyle - ignore if present in file

        # Parse boolean fields using the native V5 import defaults.
        self.is_solid, self._has_is_solid = s.read_bool(
            record, Fields.IS_SOLID, default=False
        )
        self.transparent, self._has_transparent = s.read_bool(
            record, Fields.TRANSPARENT, default=False
        )

        # CRITICAL: Handle missing AreaColor field
        # Altium omits AreaColor when transparent=True and fill is black (default)
        # Don't set default AreaColor here - leave it as parsed by parent class
        # If it wasn't in the original, it stays None

        # Parse vertices
        vertex_count, _ = s.read_int(record, Fields.LOCATION_COUNT, default=0)
        extra_vertex_count, _ = s.read_int(record, "EXTRALOCATIONCOUNT", default=0)
        self.vertices = []

        for i in range(vertex_count):
            # Vertices use indexed field names: X1, Y1, X2, Y2, etc.
            x = int(record.get(f"X{i + 1}", 0))
            y = int(record.get(f"Y{i + 1}", 0))
            x_frac = int(record.get(f"X{i + 1}_FRAC", record.get(f"X{i + 1}_Frac", 0)))
            y_frac = int(record.get(f"Y{i + 1}_FRAC", record.get(f"Y{i + 1}_Frac", 0)))
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))

        for i in range(vertex_count + 1, vertex_count + extra_vertex_count + 1):
            x = int(record.get(f"EX{i}", 0))
            y = int(record.get(f"EY{i}", 0))
            x_frac = int(record.get(f"EX{i}_FRAC", record.get(f"EX{i}_Frac", 0)))
            y_frac = int(record.get(f"EY{i}_FRAC", record.get(f"EY{i}_Frac", 0)))
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Polygons use X1/Y1/X2/Y2 for vertices, NOT Location.X/Y
        # Remove Location fields that SchGraphicalObject adds
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

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        main_vertex_count = min(len(self.vertices), 50)
        extra_vertex_count = max(len(self.vertices) - main_vertex_count, 0)

        s.write_int(record, Fields.LOCATION_COUNT, main_vertex_count, raw)

        if extra_vertex_count > 0:
            self._update_field(
                record, "EXTRALOCATIONCOUNT", extra_vertex_count, ["EXTRALOCATIONCOUNT"]
            )
        else:
            self._remove_field(record, ["EXTRALOCATIONCOUNT"])

        # LineWidth - skip if default (0 = SMALLEST)
        # Altium's Library Splitter omits LineWidth=0
        s.write_int(
            record,
            Fields.LINE_WIDTH,
            self.line_width.value,
            raw,
            skip_if_default=True,
            default=0,
        )
        # Note: Polygon does NOT serialize LineStyle (per native file format implementation)
        self._remove_field(record, [Fields.LINE_STYLE.pascal, Fields.LINE_STYLE.upper])
        self._remove_field(
            record, [Fields.LINE_STYLE_EXT.pascal, Fields.LINE_STYLE_EXT.upper]
        )

        # Always export IsSolid - matches Altium's serialization behavior
        s.write_bool(record, Fields.IS_SOLID, self.is_solid, raw)

        # Only export Transparent if True - Altium's Library Splitter omits Transparent=F
        if self.transparent:
            s.write_bool(record, Fields.TRANSPARENT, self.transparent, raw)

        # Write vertices - Xn/Yn for the first 50, EXn/EYn for the remainder.
        for i, vertex in enumerate(self.vertices, 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"

            # Altium omits zero-value vertex coordinates
            if vertex.x != 0:
                self._update_field(record, x_key, vertex.x, [x_key])
            else:
                self._remove_field(record, [x_key])
            if vertex.y != 0:
                self._update_field(record, y_key, vertex.y, [y_key])
            else:
                self._remove_field(record, [y_key])

            frac_x_names = [f"{x_key}_Frac", f"{x_key}_FRAC"]
            frac_y_names = [f"{y_key}_Frac", f"{y_key}_FRAC"]

            if vertex.x_frac:
                self._update_field(record, frac_x_names[0], vertex.x_frac, frac_x_names)
            else:
                self._remove_field(record, frac_x_names)

            if vertex.y_frac:
                self._update_field(record, frac_y_names[0], vertex.y_frac, frac_y_names)
            else:
                self._remove_field(record, frac_y_names)

        stale_total = 0
        if raw is not None:
            stale_total = int(raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)))
            stale_total += int(raw.get("EXTRALOCATIONCOUNT", 0))

        for i in range(len(self.vertices) + 1, stale_total + 1):
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

    _detect_case_mode = detect_case_mode_method_from_dotted_uppercase_fields

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build an oracle-aligned geometry record for this polygon.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.vertices) < 3:
            return None

        svg_points = [ctx.transform_coord_precise(vertex) for vertex in self.vertices]
        geometry_points = [
            svg_coord_to_geometry(
                x_px,
                y_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            for x_px, y_px in svg_points
        ]

        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        pen_width = (
            0
            if self.line_width == LineWidth.SMALLEST
            else int(round(stroke_width_mils * units_per_px))
        )
        pen_color_raw = (
            int(ctx.line_color_override)
            if ctx.line_color_override is not None
            else int(self.color)
            if self.color is not None
            else 0
        )
        fill_color_raw = (
            int(ctx.area_color_override)
            if ctx.area_color_override is not None
            else int(self.area_color)
            if self.area_color is not None
            else pen_color_raw
        )

        operations: list[SchGeometryOp] = []
        if self.is_solid:
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_points],
                    brush=make_solid_brush(
                        fill_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if self.transparent else 0xFF,
                    ),
                )
            )

        operations.append(
            SchGeometryOp.polygons(
                [geometry_points],
                pen=make_pen(
                    pen_color_raw,
                    width=pen_width,
                    line_join="pljRound",
                ),
            )
        )

        xs = [float(vertex.x) for vertex in self.vertices]
        ys = [float(vertex.y) for vertex in self.vertices]
        inflate = stroke_width_mils + 2.0

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="polygon",
            object_id="ePolygon",
            bounds=SchGeometryBounds(
                left=int(round((min(xs) - inflate) * 100000)),
                top=int(round((max(ys) + inflate) * 100000)),
                right=int(round((max(xs) + inflate) * 100000)),
                bottom=int(round((min(ys) - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )


# =============================================================================
# SchDoc-Specific Records
# =============================================================================
