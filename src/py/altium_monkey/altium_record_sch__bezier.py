"""Schematic record model for SchRecordType.BEZIER."""

from typing import TYPE_CHECKING, Any, cast

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
from ._sch_managed_defaults import BEZIER_COLOR
from .altium_serializer import AltiumSerializer, Fields
from .altium_sch_record_helpers import (
    _validate_schematic_vertex_counts,
    _validate_schematic_vertex_total,
    detect_case_mode_method_from_dotted_uppercase_fields,
    indexed_coord_has_invalid_wire_value,
    read_indexed_coord,
    validate_indexed_coord,
)
from .altium_sch_svg_renderer import LINE_WIDTH_MILS, SchSvgRenderContext
from ._sch_managed_numeric import managed_internal_coord, unchecked_i32 as _managed_i32


_ManagedPoint = tuple[int, int]


def _managed_half_sum(first: int, second: int) -> int:
    total = _managed_i32(first + second)
    return total // 2 if total >= 0 else -(abs(total) // 2)


def _managed_midpoint(first: _ManagedPoint, second: _ManagedPoint) -> _ManagedPoint:
    return (
        _managed_half_sum(first[0], second[0]),
        _managed_half_sum(first[1], second[1]),
    )


def _subdivide_managed_bezier(
    output: list[_ManagedPoint],
    p0: _ManagedPoint,
    p1: _ManagedPoint,
    p2: _ManagedPoint,
    p3: _ManagedPoint,
    depth: int,
) -> None:
    if depth == 0:
        output.append(p3)
        return
    p4 = _managed_midpoint(p0, p1)
    center = _managed_midpoint(p1, p2)
    p5 = _managed_midpoint(p2, p3)
    p6 = _managed_midpoint(p4, center)
    p7 = _managed_midpoint(center, p5)
    split = _managed_midpoint(p6, p7)
    _subdivide_managed_bezier(output, p0, p4, p6, split, depth - 1)
    _subdivide_managed_bezier(output, split, p7, p5, p3, depth - 1)


def _managed_bezier_points(control_points: list[_ManagedPoint]) -> list[_ManagedPoint]:
    output: list[_ManagedPoint] = []
    for index in range(0, len(control_points) - 3, 3):
        if not output:
            output.append(control_points[index])
        _subdivide_managed_bezier(
            output,
            control_points[index],
            control_points[index + 1],
            control_points[index + 2],
            control_points[index + 3],
            5,
        )
    return output


def _managed_bezier_spans(
    control_points: list[_ManagedPoint],
) -> list[list[_ManagedPoint]]:
    return [
        _managed_bezier_points(control_points[index : index + 4])
        for index in range(0, len(control_points) - 3, 3)
    ]


class AltiumSchBezier(SchGraphicalObject):
    """
    Bezier curve record.

    Bezier curve defined by control points.

    Public code should use ``points_mils`` for normal bezier creation,
    inspection, and mutation. Bezier points use cubic segments with native
    control-point groups of ``4 + 3n`` points: four points for the first
    segment, then three more points for each connected segment. Native V5
    bezier records do not carry a line-style field.
    """

    def __init__(self) -> None:
        super().__init__()
        self.color = BEZIER_COLOR
        self._apply_nonpersisted_area_color_default()
        self.vertices: list[CoordPoint] = []
        self._source_vertices: tuple[CoordPoint, ...] = ()
        self._source_has_invalid_vertex_wire_value = False
        self.line_width: LineWidth = LineWidth.SMALL
        # Note: Bezier does NOT support LineStyle (per native file format implementation)
        # Track which fields were present
        self._has_line_width: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.BEZIER

    @property
    def points_mils(self) -> list[SchPointMils]:
        """
        Public bezier control-point list expressed in mils.

        Native schematic beziers use cubic segments with control-point counts
        of ``4 + 3n``. Public callers should mutate this property rather than
        the raw ``vertices`` storage list.
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
        if len(value) < 4:
            raise ValueError("points_mils must contain at least 4 points")
        if (len(value) - 1) % 3 != 0:
            raise ValueError(
                "points_mils must contain 4 + 3n points for cubic bezier segments"
            )
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
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        # Note: Bezier does NOT support LineStyle - ignore if present in file

        # Parse vertices (control points)
        vertex_count, _ = s.read_int(record, Fields.LOCATION_COUNT, default=0)
        extra_vertex_count, _ = s.read_int(record, "EXTRALOCATIONCOUNT", default=0)
        _validate_schematic_vertex_counts(vertex_count, extra_vertex_count)
        self.vertices = []
        self._source_has_invalid_vertex_wire_value = False

        for i in range(vertex_count):
            # Vertices use indexed field names: X1, Y1, X2, Y2, etc.
            x, x_frac = read_indexed_coord(record, f"X{i + 1}")
            y, y_frac = read_indexed_coord(record, f"Y{i + 1}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(record, f"X{i + 1}")
                or indexed_coord_has_invalid_wire_value(record, f"Y{i + 1}")
            )

        for i in range(vertex_count + 1, vertex_count + extra_vertex_count + 1):
            x, x_frac = read_indexed_coord(record, f"EX{i}")
            y, y_frac = read_indexed_coord(record, f"EY{i}")
            self.vertices.append(CoordPoint(x, y, x_frac, y_frac))
            self._source_has_invalid_vertex_wire_value |= (
                indexed_coord_has_invalid_wire_value(record, f"EX{i}")
                or indexed_coord_has_invalid_wire_value(record, f"EY{i}")
            )
        self._source_vertices = tuple(self.vertices)

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

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record
        vertices_changed = raw is None or tuple(self.vertices) != self._source_vertices
        preserve_invalid_source = (
            not vertices_changed and self._source_has_invalid_vertex_wire_value
        )
        _validate_schematic_vertex_total(len(self.vertices))

        main_vertex_count = min(len(self.vertices), 50)
        extra_vertex_count = max(len(self.vertices) - main_vertex_count, 0)

        s.write_int(
            record,
            Fields.LOCATION_COUNT,
            main_vertex_count,
            raw,
            force=bool(self.vertices),
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

        # Write line width conditionally
        self._serialize_managed_family_int(
            record, s, Fields.LINE_WIDTH.canonical, self.line_width.value
        )
        # Note: Bezier does NOT serialize LineStyle (per native file format implementation)
        self._remove_field(record, [Fields.LINE_STYLE.pascal, Fields.LINE_STYLE.upper])
        self._remove_field(
            record, [Fields.LINE_STYLE_EXT.pascal, Fields.LINE_STYLE_EXT.upper]
        )
        if raw is None or self._area_color_dirty:
            self._remove_fields_case_insensitively(record, ["AreaColor", "AREACOLOR"])

        # Write vertices - Xn/Yn for the first 50, EXn/EYn for the remainder.
        for i, vertex in enumerate(self.vertices, 1):
            if i <= 50:
                x_key = f"X{i}"
                y_key = f"Y{i}"
            else:
                x_key = f"EX{i}"
                y_key = f"EY{i}"
            validate_indexed_coord(vertex, x_key, y_key)
            if preserve_invalid_source:
                continue

            self._update_field(record, x_key, vertex.x, [x_key], force=True)
            self._update_field(record, y_key, vertex.y, [y_key], force=True)

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

        stale_start = (
            stale_total + 1 if preserve_invalid_source else len(self.vertices) + 1
        )
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
        Build an oracle-aligned geometry record for this bezier curve.
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

        if len(self.vertices) < 4:
            return None

        internal_points = [
            (
                managed_internal_coord(vertex.x, vertex.x_frac),
                managed_internal_coord(vertex.y, vertex.y_frac),
            )
            for vertex in self.vertices
        ]
        flattened_spans = [
            [ctx.transform_point(x / 100_000.0, y / 100_000.0) for x, y in span]
            for span in _managed_bezier_spans(internal_points)
        ]

        stroke_width_mils = LINE_WIDTH_MILS.get(self.line_width, 1.0)
        pen = make_pen(
            int(self.color) if self.color is not None else 0,
            width=0
            if self.line_width == LineWidth.SMALLEST
            else _geometry_item_length(
                stroke_width_mils * ctx.get_stroke_scale(),
                units_per_px=units_per_px,
            ),
            line_join="pljRound",
        )

        operations: list[SchGeometryOp] = [
            SchGeometryOp.lines(
                [
                    svg_coord_to_geometry(
                        point[0],
                        point[1],
                        sheet_height_px=float(ctx.sheet_height or 0.0),
                        units_per_px=units_per_px,
                    )
                    for point in span
                ],
                pen=pen,
            )
            for span in flattened_spans
        ]

        xs = [float(vertex.x) for vertex in self.vertices]
        ys = [float(vertex.y) for vertex in self.vertices]
        inflate = stroke_width_mils + 2.0

        unique_id = cast(str, self.unique_id)
        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=unique_id,
            kind="bezier",
            object_id="eBezier",
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
