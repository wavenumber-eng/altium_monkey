"""Schematic record model for SchRecordType.POLYGON."""

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryOp, SchGeometryRecord

from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchGraphicalObject,
    SchPointMils,
    SchRecordType,
)
from ._sch_managed_defaults import GRAPHICAL_BORDER_COLOR, GRAPHICAL_FILL_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _validate_schematic_vertex_counts,
    _validate_schematic_vertex_total,
    detect_case_mode_method_from_dotted_uppercase_fields,
    indexed_coord_has_invalid_wire_value,
    read_indexed_coord,
    validate_indexed_coord,
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
        if self.record_type is SchRecordType.POLYGON:
            self.color = GRAPHICAL_BORDER_COLOR
            self.area_color = GRAPHICAL_FILL_COLOR
        self.vertices: list[CoordPoint] = []
        self._source_vertices: tuple[CoordPoint, ...] = ()
        self._source_has_invalid_vertex_wire_value = False
        self.line_width: LineWidth = LineWidth.LARGE
        # Note: Polygon does NOT support LineStyle (per native file format implementation)
        self.is_solid: bool = True
        self.transparent: bool = False
        # Track which fields were present
        self._has_line_width: bool = False
        self._has_is_solid: bool = False
        self._has_transparent: bool = False
        self._roundtrip_default_vertices: tuple[CoordPoint, ...] | None = None

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
        _validate_schematic_vertex_total(len(value))
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
        # Read through the indexed case-insensitive copy captured by the base
        # parse; case-insensitive lookups on the raw dict degrade to key scans.
        r = self._record

        # Parse line width
        line_width_val, self._has_line_width = s.read_int(
            r, Fields.LINE_WIDTH, default=0
        )
        self.line_width = LineWidth(line_width_val)
        # Note: Polygon does NOT support LineStyle - ignore if present in file

        # Parse boolean fields using the native V5 import defaults.
        # (presence checks deliberately stay exact-case on the raw record)
        if self.record_type is SchRecordType.BLANKET:
            self._has_is_solid = any(name in record for name in ("IsSolid", "ISSOLID"))
            self._has_transparent = any(
                name in record for name in ("Transparent", "TRANSPARENT")
            )
            self.is_solid = False
            self.transparent = True
        else:
            self.is_solid, self._has_is_solid = s.read_bool(
                r, Fields.IS_SOLID, default=False
            )
            self.transparent, self._has_transparent = s.read_bool(
                r, Fields.TRANSPARENT, default=False
            )
        self._apply_imported_color_defaults(area_color=True)

        # Parse vertices
        vertex_count, _ = s.read_int(r, Fields.LOCATION_COUNT, default=0)
        extra_vertex_count, _ = s.read_int(r, "EXTRALOCATIONCOUNT", default=0)
        _validate_schematic_vertex_counts(vertex_count, extra_vertex_count)
        self.vertices = []
        self._source_has_invalid_vertex_wire_value = False
        self._roundtrip_default_vertices = None

        for i in range(vertex_count):
            # Vertices use indexed field names: X1, Y1, X2, Y2, etc.
            x, x_frac = read_indexed_coord(r, f"X{i + 1}")
            y, y_frac = read_indexed_coord(r, f"Y{i + 1}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(r, f"X{i + 1}")
                or indexed_coord_has_invalid_wire_value(r, f"Y{i + 1}")
            )

        for i in range(vertex_count + 1, vertex_count + extra_vertex_count + 1):
            x, x_frac = read_indexed_coord(r, f"EX{i}")
            y, y_frac = read_indexed_coord(r, f"EY{i}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(r, f"EX{i}")
                or indexed_coord_has_invalid_wire_value(r, f"EY{i}")
            )
        self._source_vertices = tuple(self.vertices)

    def _set_roundtrip_default_vertices(self, vertices: list[CoordPoint]) -> None:
        """Expose managed semantic defaults without materializing absent fields."""
        self.vertices = vertices
        self._roundtrip_default_vertices = tuple(vertices)

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize to a record.
        """
        record = super().serialize_to_record()

        # Blanket owns its bounds location; ordinary polygons use only vertices.
        if self.record_type is not SchRecordType.BLANKET:
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
        vertices = self.vertices
        if (
            self._roundtrip_default_vertices is not None
            and tuple(vertices) == self._roundtrip_default_vertices
        ):
            vertices = []
        vertices_changed = raw is None or tuple(vertices) != self._source_vertices
        preserve_invalid_source = (
            not vertices_changed and self._source_has_invalid_vertex_wire_value
        )
        _validate_schematic_vertex_total(len(vertices))

        main_vertex_count = min(len(vertices), 50)
        extra_vertex_count = max(len(vertices) - main_vertex_count, 0)

        s.write_int(
            record,
            Fields.LOCATION_COUNT,
            main_vertex_count,
            raw,
            force=bool(vertices),
        )
        if raw is None and main_vertex_count == 0:
            s.remove_field(record, Fields.LOCATION_COUNT)
        elif raw is not None and main_vertex_count == 0:
            source_count = int(
                raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)) or 0
            )
            if source_count != 0:
                s.remove_field(record, Fields.LOCATION_COUNT)

        if extra_vertex_count > 0:
            self._update_field(
                record,
                "EXTRALOCATIONCOUNT",
                extra_vertex_count,
                ["ExtraLocationCount", "EXTRALOCATIONCOUNT"],
                force=True,
            )
        else:
            self._remove_field(record, ["ExtraLocationCount", "EXTRALOCATIONCOUNT"])

        # LineWidth - skip if default (0 = SMALLEST)
        # Altium's Library Splitter omits LineWidth=0
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )
        # Blanket owns the same spellings in its family serializer.
        if self.record_type is not SchRecordType.BLANKET:
            self._remove_field(
                record, [Fields.LINE_STYLE.pascal, Fields.LINE_STYLE.upper]
            )
            self._remove_field(
                record, [Fields.LINE_STYLE_EXT.pascal, Fields.LINE_STYLE_EXT.upper]
            )

        # Always export IsSolid - matches Altium's serialization behavior
        if self.record_type is not SchRecordType.BLANKET:
            self._serialize_managed_family_bool(
                record, s, Fields.IS_SOLID.canonical, self.is_solid
            )

            # Only export Transparent if True - Altium's Library Splitter omits Transparent=F
            self._serialize_managed_family_bool(
                record, s, Fields.TRANSPARENT.canonical, self.transparent
            )

        # Write vertices - Xn/Yn for the first 50, EXn/EYn for the remainder.
        for i, vertex in enumerate(vertices, 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"
            validate_indexed_coord(vertex, x_key, y_key)
            if preserve_invalid_source:
                continue

            # Altium omits zero-value vertex coordinates
            if vertex.x != 0:
                self._update_field(record, x_key, vertex.x, [x_key], force=True)
            else:
                self._remove_field(record, [x_key])
            if vertex.y != 0:
                self._update_field(record, y_key, vertex.y, [y_key], force=True)
            else:
                self._remove_field(record, [y_key])

            frac_x_names = [f"{x_key}_Frac", f"{x_key}_FRAC"]
            frac_y_names = [f"{y_key}_Frac", f"{y_key}_FRAC"]

            if vertex.x_frac:
                self._update_field(
                    record, frac_x_names[0], vertex.x_frac, frac_x_names, force=True
                )
            else:
                self._remove_field(record, frac_x_names)

            if vertex.y_frac:
                self._update_field(
                    record, frac_y_names[0], vertex.y_frac, frac_y_names, force=True
                )
            else:
                self._remove_field(record, frac_y_names)

        stale_total = 0
        if raw is not None:
            stale_total = int(raw.get("LocationCount", raw.get("LOCATIONCOUNT", 0)))
            stale_total += int(
                raw.get("ExtraLocationCount", raw.get("EXTRALOCATIONCOUNT", 0))
            )

        stale_start = stale_total + 1 if preserve_invalid_source else len(vertices) + 1
        for i in range(stale_start, stale_total + 1):
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

        self._move_geometry_identity_to_end_if_needed(record)
        return self._order_authored_graphical_fields(
            record,
            (
                "LineWidth",
                "Color",
                "AreaColor",
                "IsSolid",
                "Transparent",
                "__Vertices__",
                "UniqueID",
            ),
        )

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
            SchGeometryRecord,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        if len(self.vertices) < 2:
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
        operations = self._geometry_operations(ctx, geometry_points, units_per_px)

        xs = [float(vertex.x) for vertex in self.vertices]
        ys = [float(vertex.y) for vertex in self.vertices]
        inflate = stroke_width_mils + 2.0

        unique_id = cast(str, self.unique_id)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=unique_id,
            kind="polygon",
            object_id="ePolygon",
            bounds=SchGeometryBounds(
                left=int(round((min(xs) - inflate) * 100000)),
                top=int(round((max(ys) + inflate) * 100000)),
                right=int(round((max(xs) + inflate) * 100000)),
                bottom=int(round((min(ys) - inflate) * 100000)),
            ),
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def _geometry_operations(
        self,
        ctx: SchSvgRenderContext,
        geometry_points: list[tuple[float, float]],
        units_per_px: int,
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_item_length,
            make_pen,
            make_solid_brush,
        )

        pen_width = _geometry_item_length(
            (
                0
                if self.line_width == LineWidth.SMALLEST
                else LINE_WIDTH_MILS.get(self.line_width, 1.0)
            )
            * ctx.get_stroke_scale(),
            units_per_px=units_per_px,
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
        if self.is_solid and len(geometry_points) > 2:
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_points],
                    brush=make_solid_brush(
                        fill_color_raw,
                        alpha=SEMI_TRANSPARENT_ALPHA if self.transparent else 0xFF,
                    ),
                )
            )

        pen = make_pen(pen_color_raw, width=pen_width, line_join="pljRound")
        if len(geometry_points) == 2:
            operations.append(SchGeometryOp.lines(geometry_points, pen=pen))
        else:
            operations.append(SchGeometryOp.polygons([geometry_points], pen=pen))
        return operations


# =============================================================================
# SchDoc-Specific Records
# =============================================================================
