"""Schematic record model for SchRecordType.IEEE_SYMBOL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_sch_enums import IeeeSymbol, Rotation90, SymbolLineWidth
from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import detect_case_mode_method_from_uppercase_fields

Point = tuple[float, float]
Polyline = list[Point]
ArcDef = tuple[float, float, float, float, float]

_IEEE_SYMBOL_LINE_WIDTH_MILS: dict[SymbolLineWidth, float] = {
    SymbolLineWidth.ZERO: 0.0,
    SymbolLineWidth.SMALL: 1.0,
    SymbolLineWidth.MEDIUM: 3.0,
    SymbolLineWidth.LARGE: 5.0,
}

_IEEE_SYMBOL_SHAPES: dict[
    IeeeSymbol, tuple[list[Polyline], list[Polyline], list[ArcDef]]
] = {
    IeeeSymbol.NONE: ([], [], []),
    IeeeSymbol.DOT: ([], [], [(0.0, 0.0, 3.0, 0.0, 360.0)]),
    IeeeSymbol.RIGHT_LEFT_SIGNAL_FLOW: (
        [],
        [[(12.0, 8.0), (0.0, 4.0), (12.0, 0.0)]],
        [],
    ),
    IeeeSymbol.CLOCK: ([[(0.0, 8.0), (12.0, 4.0), (0.0, 0.0)]], [], []),
    IeeeSymbol.ACTIVE_LOW_INPUT: (
        [[(0.0, 0.0), (0.0, 6.0), (12.0, 0.0), (0.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.ANALOG_SIGNAL_IN: (
        [[(0.0, 0.0), (0.0, 3.0)], [(6.0, 0.0), (6.0, 3.0)]],
        [],
        [(3.0, 3.0, 3.0, 0.0, 180.0)],
    ),
    IeeeSymbol.NOT_LOGIC_CONNECTION: (
        [[(0.0, 0.0), (8.0, 8.0)], [(0.0, 8.0), (8.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.SHIFT_RIGHT: (
        [
            [
                (0.0, 0.0),
                (6.0, 3.0),
                (6.0, 0.0),
                (12.0, 0.0),
                (6.0, 0.0),
                (6.0, -3.0),
                (0.0, 0.0),
            ]
        ],
        [],
        [],
    ),
    IeeeSymbol.POSTPONED_OUTPUT: ([[(8.0, 0.0), (8.0, 8.0), (0.0, 8.0)]], [], []),
    IeeeSymbol.OPEN_COLLECTOR: (
        [
            [(4.0, 0.0), (8.0, 4.0), (4.0, 8.0), (0.0, 4.0), (4.0, 0.0)],
            [(0.0, 0.0), (8.0, 0.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.HIZ: ([[(4.0, 0.0), (8.0, 8.0), (0.0, 8.0), (4.0, 0.0)]], [], []),
    IeeeSymbol.HIGH_CURRENT: (
        [[(0.0, 0.0), (8.0, 4.0), (0.0, 8.0), (0.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.PULSE: (
        [[(0.0, 0.0), (8.0, 0.0), (8.0, 8.0), (16.0, 8.0), (16.0, 0.0), (24.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.SCHMITT: (
        [
            [
                (0.0, 0.0),
                (4.0, 1.0),
                (4.0, 7.0),
                (16.0, 8.0),
                (12.0, 7.0),
                (12.0, 1.0),
                (0.0, 0.0),
            ]
        ],
        [],
        [],
    ),
    IeeeSymbol.DELAY: (
        [
            [(0.0, 2.0), (0.0, -2.0)],
            [(0.0, 0.0), (20.0, 0.0)],
            [(20.0, 2.0), (20.0, -2.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.GROUP_LINE: (
        [[(0.0, 11.0), (3.0, 11.0), (3.0, -11.0), (0.0, -11.0)]],
        [],
        [],
    ),
    IeeeSymbol.GROUP_BIN: (
        [
            [
                (1.0, 0.0),
                (2.0, 1.0),
                (2.0, 2.0),
                (3.0, 3.0),
                (2.0, 4.0),
                (2.0, 5.0),
                (1.0, 6.0),
            ]
        ],
        [],
        [],
    ),
    IeeeSymbol.ACTIVE_LOW_OUTPUT: ([[(0.0, 6.0), (12.0, 0.0)]], [], []),
    IeeeSymbol.PI_SYMBOL: (
        [[(-4.0, 4.0), (5.0, 4.0)], [(-2.0, 4.0), (-3.0, -5.0)]],
        [],
        [(12.0, 2.0, 9.0, 173.0, 244.0)],
    ),
    IeeeSymbol.GREATER_EQUAL: (
        [[(0.0, 6.0), (12.0, 0.0), (0.0, -6.0)], [(0.0, -8.0), (12.0, -2.0)]],
        [],
        [],
    ),
    IeeeSymbol.LESS_EQUAL: (
        [[(12.0, 6.0), (0.0, 0.0), (12.0, -6.0)], [(0.0, -2.0), (12.0, -8.0)]],
        [],
        [],
    ),
    IeeeSymbol.SIGMA: (
        [
            [
                (2.0, 2.0),
                (1.0, 3.0),
                (-3.0, 3.0),
                (-2.0, 0.0),
                (-4.0, -4.0),
                (1.0, -4.0),
                (2.0, -3.0),
            ]
        ],
        [],
        [],
    ),
    IeeeSymbol.OPEN_COLLECTOR_PULL_UP: (
        [
            [(4.0, 0.0), (8.0, 4.0), (4.0, 8.0), (0.0, 4.0), (4.0, 0.0)],
            [(0.0, 0.0), (8.0, 0.0)],
            [(0.0, 4.0), (8.0, 4.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.OPEN_EMITTER: (
        [
            [(4.0, 0.0), (8.0, 4.0), (4.0, 8.0), (0.0, 4.0), (4.0, 0.0)],
            [(0.0, 8.0), (8.0, 8.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.OPEN_EMITTER_PULL_UP: (
        [
            [(4.0, 0.0), (8.0, 4.0), (4.0, 8.0), (0.0, 4.0), (4.0, 0.0)],
            [(0.0, 8.0), (8.0, 8.0)],
            [(0.0, 4.0), (8.0, 4.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.DIGITAL_SIGNAL_IN: (
        [
            [(-2.0, 0.0), (3.0, 0.0)],
            [(0.0, 2.0), (5.0, 2.0)],
            [(-1.0, -1.0), (2.0, 3.0)],
            [(1.0, -1.0), (4.0, 3.0)],
        ],
        [],
        [],
    ),
    IeeeSymbol.AND: (
        [[(40.0, 20.0), (0.0, 20.0), (0.0, -20.0), (40.0, -20.0)]],
        [],
        [(40.0, 0.0, 20.0, 270.0, 90.0)],
    ),
    IeeeSymbol.INVERTER: (
        [[(0.0, 20.0), (0.0, -20.0), (40.0, 0.0), (0.0, 20.0)]],
        [],
        [],
    ),
    IeeeSymbol.OR: (
        [
            [(0.0, 20.0), (30.0, 20.0)],
            [(0.0, -20.0), (30.0, -20.0)],
            [(0.0, 10.0), (6.0, 10.0)],
            [(0.0, -10.0), (6.0, -10.0)],
        ],
        [],
        [
            (-20.0, 0.0, 28.0, 315.0, 45.0),
            (30.0, -20.0, 40.0, 30.0, 90.0),
            (30.0, 20.0, 40.0, 270.0, 330.0),
        ],
    ),
    IeeeSymbol.XOR: (
        [
            [(5.0, 20.0), (35.0, 20.0)],
            [(5.0, -20.0), (35.0, -20.0)],
            [(0.0, 10.0), (6.0, 10.0)],
            [(0.0, -10.0), (6.0, -10.0)],
        ],
        [],
        [
            (-15.0, 0.0, 28.0, 315.0, 45.0),
            (-20.0, 0.0, 28.0, 315.0, 45.0),
            (35.0, -20.0, 40.0, 30.0, 90.0),
            (35.0, 20.0, 40.0, 270.0, 330.0),
        ],
    ),
    IeeeSymbol.SHIFT_LEFT: (
        [[(0.0, 0.0), (6.0, 0.0), (6.0, 3.0), (12.0, 0.0), (6.0, -3.0), (6.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.INPUT_OUTPUT: (
        [],
        [
            [(12.0, 0.0), (0.0, 4.0), (12.0, 8.0)],
            [(14.0, 0.0), (26.0, 4.0), (14.0, 8.0)],
        ],
        [],
    ),
    IeeeSymbol.OPEN_CIRCUIT_OUTPUT: (
        [[(2.0, 0.0), (0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)]],
        [],
        [],
    ),
    IeeeSymbol.LEFT_RIGHT_SIGNAL_FLOW: (
        [],
        [[(0.0, 0.0), (-6.0, 2.0), (-6.0, -2.0)]],
        [],
    ),
    IeeeSymbol.BIDIRECTIONAL_SIGNAL_FLOW: (
        [],
        [[(6.0, 2.0), (0.0, 0.0), (6.0, -2.0)], [(7.0, 2.0), (7.0, -2.0), (13.0, 0.0)]],
        [],
    ),
    IeeeSymbol.INTERNAL_PULL_UP: (
        [
            [(0.0, 8.0), (10.0, 8.0)],
            [
                (5.0, 8.0),
                (5.0, 6.0),
                (9.0, 5.0),
                (1.0, 3.0),
                (9.0, 1.0),
                (1.0, -1.0),
                (5.0, -2.0),
                (5.0, -4.0),
            ],
        ],
        [],
        [],
    ),
    IeeeSymbol.INTERNAL_PULL_DOWN: (
        [
            [
                (5.0, 8.0),
                (5.0, 6.0),
                (9.0, 5.0),
                (1.0, 3.0),
                (9.0, 1.0),
                (1.0, -1.0),
                (5.0, -2.0),
                (5.0, -4.0),
            ],
            [(0.0, -4.0), (10.0, -4.0)],
            [(2.0, -5.0), (8.0, -5.0)],
            [(4.0, -6.0), (6.0, -6.0)],
        ],
        [],
        [],
    ),
}


def _normalize_angle(angle: float) -> float:
    while angle > 360.0:
        angle -= 360.0
    while angle < 0.0:
        angle += 360.0
    return angle


def _rotate_point(point: Point, orientation: Rotation90) -> Point:
    x, y = point
    if orientation == Rotation90.DEG_0:
        return (x, y)
    if orientation == Rotation90.DEG_90:
        return (-y, x)
    if orientation == Rotation90.DEG_180:
        return (-x, -y)
    return (y, -x)


def _transform_point(
    location: Point,
    point: Point,
    orientation: Rotation90,
    is_mirrored: bool,
    scale: float,
) -> Point:
    dx, dy = _rotate_point(point, orientation)
    if is_mirrored:
        dx = -dx
    return (location[0] + dx * scale, location[1] + dy * scale)


def _transform_arc(
    location: Point,
    arc: ArcDef,
    orientation: Rotation90,
    is_mirrored: bool,
    scale: float,
) -> ArcDef:
    cx, cy, radius, start_angle, end_angle = arc
    center_x, center_y = _transform_point(
        location, (cx, cy), orientation, is_mirrored, scale
    )
    rotation_degrees = float(orientation.value * 90)
    start_angle = _normalize_angle(start_angle + rotation_degrees)
    end_angle = _normalize_angle(end_angle + rotation_degrees)
    if is_mirrored:
        start_angle, end_angle = (
            _normalize_angle(180.0 - end_angle),
            _normalize_angle(180.0 - start_angle),
        )
    return (center_x, center_y, radius * scale, start_angle, end_angle)


def _ieee_symbol_shape(
    symbol: IeeeSymbol,
) -> tuple[list[Polyline], list[Polyline], list[ArcDef]]:
    return _IEEE_SYMBOL_SHAPES.get(symbol, ([], [], []))


class AltiumSchIeeeSymbol(SchGraphicalObject):
    """
    IEEE_SYMBOL record.

    A standalone IEEE symbol placed on the schematic.
    Used for logic gate symbols (AND, OR, NAND, NOR, XOR, etc.)
    in IEEE format schematics.
    """

    def __init__(self) -> None:
        super().__init__()
        self.symbol: IeeeSymbol = IeeeSymbol.AND  # Default to AND gate
        self.orientation: Rotation90 = Rotation90.DEG_0
        self.is_mirrored: bool = False
        self.line_width: SymbolLineWidth = SymbolLineWidth.ZERO
        self.scale_factor: int = 100
        # Track field presence
        self._has_symbol: bool = False
        self._has_orientation: bool = False
        self._has_line_width: bool = False
        self._has_scale_factor: bool = False

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.IEEE_SYMBOL

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)

        # Use serializer for field reading
        s = AltiumSerializer()

        # Symbol type
        symbol_val, self._has_symbol = s.read_int(
            record, Fields.SYMBOL, default=IeeeSymbol.AND.value
        )
        self.symbol = IeeeSymbol(symbol_val)

        # Orientation (0-3)
        orient_val, self._has_orientation = s.read_int(
            record, Fields.ORIENTATION, default=0
        )
        self.orientation = Rotation90(orient_val)

        # Mirrored flag
        self.is_mirrored, _ = s.read_bool(record, Fields.IS_MIRRORED, default=False)

        # Line width
        line_width_val, self._has_line_width = s.read_int(
            record, Fields.LINE_WIDTH, default=0
        )
        self.line_width = SymbolLineWidth(line_width_val)

        # Scale factor
        self.scale_factor, self._has_scale_factor = s.read_int(
            record, Fields.SCALE_FACTOR, default=100
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        # Determine case mode from raw record
        mode = self._detect_case_mode()
        s = AltiumSerializer(mode)
        raw = self._raw_record

        if self._has_symbol or self.symbol != IeeeSymbol.AND:
            s.write_int(record, Fields.SYMBOL, self.symbol.value, raw)
        if self._has_orientation or self.orientation != Rotation90.DEG_0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)

        if self.is_mirrored:
            s.write_bool(record, Fields.IS_MIRRORED, self.is_mirrored, raw)

        if self._has_line_width or self.line_width != SymbolLineWidth.ZERO:
            s.write_int(record, Fields.LINE_WIDTH, self.line_width.value, raw)
        if self._has_scale_factor or self.scale_factor != 100:
            s.write_int(record, Fields.SCALE_FACTOR, self.scale_factor, raw)

        return record

    _detect_case_mode = detect_case_mode_method_from_uppercase_fields

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord | None":
        """
        Build a geometry record for a standalone IEEE symbol.
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

        polylines, polygons, arcs = _ieee_symbol_shape(self.symbol)
        if not polylines and not polygons and not arcs:
            return None

        scale = (float(self.scale_factor) / 10.0) if self.scale_factor else 1.0
        location = (float(self.location.x), float(self.location.y))
        stroke_width_mils = _IEEE_SYMBOL_LINE_WIDTH_MILS.get(self.line_width, 0.0)
        pen_width = (
            0
            if self.line_width == SymbolLineWidth.ZERO
            else int(round(stroke_width_mils * units_per_px))
        )
        pen_color_raw = int(self.color) if self.color is not None else 0
        fill_color_raw = int(getattr(ctx, "sheet_area_color", 0xFFFFFF) or 0xFFFFFF)
        pen = make_pen(
            pen_color_raw,
            width=pen_width,
            line_join="pljRound",
        )

        min_x = float("inf")
        min_y = float("inf")
        max_x = float("-inf")
        max_y = float("-inf")

        def _record_bounds_point(point: Point) -> None:
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, point[0])
            min_y = min(min_y, point[1])
            max_x = max(max_x, point[0])
            max_y = max(max_y, point[1])

        operations: list[SchGeometryOp] = []

        for polygon in polygons:
            transformed_polygon = [
                _transform_point(
                    location, point, self.orientation, self.is_mirrored, scale
                )
                for point in polygon
            ]
            for point in transformed_polygon:
                _record_bounds_point(point)
            geometry_polygon = [
                svg_coord_to_geometry(
                    *ctx.transform_point(point[0], point[1]),
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                )
                for point in transformed_polygon
            ]
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_polygon],
                    brush=make_solid_brush(fill_color_raw),
                )
            )
            operations.append(
                SchGeometryOp.polygons(
                    [geometry_polygon],
                    pen=pen,
                )
            )

        for polyline in polylines:
            transformed_polyline = [
                _transform_point(
                    location, point, self.orientation, self.is_mirrored, scale
                )
                for point in polyline
            ]
            for point in transformed_polyline:
                _record_bounds_point(point)
            geometry_points = [
                svg_coord_to_geometry(
                    *ctx.transform_point(point[0], point[1]),
                    sheet_height_px=float(ctx.sheet_height or 0.0),
                    units_per_px=units_per_px,
                )
                for point in transformed_polyline
            ]
            operations.append(
                SchGeometryOp.lines(
                    geometry_points,
                    pen=pen,
                )
            )

        for arc in arcs:
            center_x, center_y, radius_mils, start_angle, end_angle = _transform_arc(
                location,
                arc,
                self.orientation,
                self.is_mirrored,
                scale,
            )
            _record_bounds_point((center_x - radius_mils, center_y - radius_mils))
            _record_bounds_point((center_x + radius_mils, center_y + radius_mils))
            center_px = ctx.transform_point(center_x, center_y)
            center_geometry = svg_coord_to_geometry(
                *center_px,
                sheet_height_px=float(ctx.sheet_height or 0.0),
                units_per_px=units_per_px,
            )
            operations.append(
                SchGeometryOp.arc(
                    center_x=center_geometry[0],
                    center_y=center_geometry[1],
                    width=radius_mils * 2.0 * units_per_px,
                    height=radius_mils * 2.0 * units_per_px,
                    start_angle=start_angle,
                    end_angle=end_angle,
                    pen=pen,
                )
            )

        unique_id = str(self.unique_id or "")
        if not unique_id:
            record_index = int(getattr(self, "_record_index", 0) or 0)
            unique_id = f"IEEE{record_index:05d}"

        bounds = SchGeometryBounds(
            left=int(round(min_x * 100000.0)),
            top=int(round(max_y * 100000.0)),
            right=int(round(max_x * 100000.0)),
            bottom=int(round(min_y * 100000.0)),
        )

        return SchGeometryRecord(
            handle=f"{document_id}\\{unique_id}",
            unique_id=unique_id,
            kind="ieee_symbol",
            object_id="eIEEESymbol",
            bounds=bounds,
            operations=wrap_record_operations(
                unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        from .altium_sch_enums import IEEE_SYMBOL_NAMES

        symbol_name = IEEE_SYMBOL_NAMES.get(self.symbol, str(self.symbol))
        return (
            f"<AltiumSchIeeeSymbol {symbol_name} at=({self.location.x}, {self.location.y}) "
            f"orientation={self.orientation.value * 90}deg>"
        )
