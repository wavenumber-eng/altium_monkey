"""Schematic record model for SchRecordType.NO_ERC."""

from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_sch_enums import Rotation90
from .altium_record_types import SchGraphicalObject, SchRecordType
from .altium_serializer import AltiumSerializer, CaseMode, Fields
from .altium_sch_record_helpers import (
    RotatedLocalPointMixin,
    geometry_coord_list,
)


class NoErcSymbol(IntEnum):
    """
    NO_ERC visual symbol types.
    """

    CROSS_THIN = 0
    CROSS = 1
    CROSS_SMALL = 2
    CHECKBOX = 3
    TRIANGLE = 4


_SYMBOL_STRING_MAP: dict[str, NoErcSymbol] = {
    "Thick Cross": NoErcSymbol.CROSS,
    "Thin Cross": NoErcSymbol.CROSS_THIN,
    "Small Cross": NoErcSymbol.CROSS_SMALL,
    "Checkbox": NoErcSymbol.CHECKBOX,
    "Triangle": NoErcSymbol.TRIANGLE,
}

_SYMBOL_ENUM_TO_STRING: dict[NoErcSymbol, str] = {
    value: key for key, value in _SYMBOL_STRING_MAP.items()
}


class AltiumSchNoErc(RotatedLocalPointMixin, SchGraphicalObject):
    """
    NO_ERC record.
    """

    _GEOMETRY_HAIRLINE_WIDTH = 0.6399999856948853
    _GEOMETRY_DIRECTIVE_WIDTH = 32.0
    _GEOMETRY_CHECKBOX_STEM_WIDTH = 6.399999618530273

    def __init__(self) -> None:
        super().__init__()
        self.orientation: Rotation90 = Rotation90.DEG_0
        self.symbol: NoErcSymbol = NoErcSymbol.CROSS_THIN
        self.is_active: bool = True
        self.suppress_all: bool = True
        self.error_kind_set_to_suppress: str = ""
        self.connection_pairs_to_suppress: str = ""
        self._symbol_was_string: bool = True
        self._use_pascal_case: bool = True

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.NO_ERC

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: "FontIDManager | None" = None,
    ) -> None:
        super().parse_from_record(record, font_manager)
        self._use_pascal_case = "Orientation" in record or "SuppressAll" in record

        s = AltiumSerializer()

        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = Rotation90(orient_val)

        symbol_str, _ = s.read_str(record, Fields.SYMBOL, default="1")
        if symbol_str in _SYMBOL_STRING_MAP:
            self.symbol = _SYMBOL_STRING_MAP[symbol_str]
            self._symbol_was_string = True
        else:
            try:
                self.symbol = NoErcSymbol(int(symbol_str))
                self._symbol_was_string = False
            except ValueError:
                self.symbol = NoErcSymbol.CROSS_THIN
                self._symbol_was_string = False

        self.is_active, _ = s.read_bool(record, Fields.IS_ACTIVE, default=True)
        self.suppress_all, _ = s.read_bool(record, Fields.SUPPRESS_ALL, default=True)
        self.error_kind_set_to_suppress, _ = s.read_str(
            record,
            "ErrorKindSetToSuppress",
            default="",
        )
        self.connection_pairs_to_suppress, _ = s.read_str(
            record,
            "ConnectionPairsToSuppress",
            default="",
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()

        mode = CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE
        s = AltiumSerializer(mode)
        raw = self._raw_record

        if self.orientation.value != 0:
            s.write_int(record, Fields.ORIENTATION, self.orientation.value, raw)
        else:
            s.remove_field(record, Fields.ORIENTATION)

        s.write_str(
            record,
            Fields.SYMBOL,
            _SYMBOL_ENUM_TO_STRING.get(self.symbol, str(self.symbol.value)),
            raw,
        )

        s.write_bool(record, Fields.IS_ACTIVE, self.is_active, raw, force=True)
        s.write_bool(record, Fields.SUPPRESS_ALL, self.suppress_all, raw, force=True)

        if not self.suppress_all:
            if self.error_kind_set_to_suppress:
                s.write_str(
                    record,
                    "ErrorKindSetToSuppress",
                    self.error_kind_set_to_suppress,
                    raw,
                )
            if self.connection_pairs_to_suppress:
                s.write_str(
                    record,
                    "ConnectionPairsToSuppress",
                    self.connection_pairs_to_suppress,
                    raw,
                )
        else:
            record.pop("ErrorKindSetToSuppress", None)
            record.pop("ERRORKINDSETTOSUPPRESS", None)
            record.pop("ConnectionPairsToSuppress", None)
            record.pop("CONNECTIONPAIRSTOSUPPRESS", None)

        return record

    def _rotate_svg_offset(self, dx: float, dy: float) -> tuple[float, float]:
        rotation = (self.orientation.value - 1) & 0x03
        match rotation:
            case 0:
                return dx, dy
            case 1:
                return dy, -dx
            case 2:
                return -dx, -dy
            case 3:
                return -dy, dx

        return dx, dy

    def to_geometry(
        self,
        ctx: "SchSvgRenderContext",
        *,
        document_id: str,
        units_per_px: int = 64,
    ) -> "SchGeometryRecord":
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryOp,
            SchGeometryRecord,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
            wrap_record_operations,
        )

        x, y = ctx.transform_point(self.location.x, self.location.y)
        sheet_height_px = float(ctx.sheet_height or 0.0)
        if not self.is_active:
            color_raw = 0x808080
        elif self.color:
            color_raw = int(self.color)
        else:
            color_raw = 0
        hairline_pen = make_pen(color_raw, width=self._GEOMETRY_HAIRLINE_WIDTH)
        directive_pen = make_pen(color_raw, width=self._GEOMETRY_DIRECTIVE_WIDTH)
        checkbox_stem_pen = make_pen(
            color_raw, width=self._GEOMETRY_CHECKBOX_STEM_WIDTH
        )
        triangle_pen = make_pen(color_raw, width=0)
        brush = make_solid_brush(color_raw)
        operations: list = []

        def coord(px: float, py: float) -> list[float]:
            return geometry_coord_list(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )

        def pt(dx: float, dy: float) -> list[float]:
            px, py = self._transform_local_point(x, y, dx, dy)
            return coord(px, py)

        def add_line(p1: list[float], p2: list[float]) -> None:
            operations.append(SchGeometryOp.lines([p1, p2], pen=directive_pen))

        if self.symbol in (NoErcSymbol.CROSS, NoErcSymbol.CROSS_THIN):
            half = 4.0
            pen = (
                hairline_pen if self.symbol == NoErcSymbol.CROSS_THIN else directive_pen
            )
            operations.append(
                SchGeometryOp.lines(
                    [coord(x - half, y + half), coord(x + half, y - half)],
                    pen=pen,
                )
            )
            operations.append(
                SchGeometryOp.lines(
                    [coord(x - half, y - half), coord(x + half, y + half)],
                    pen=pen,
                )
            )
        elif self.symbol == NoErcSymbol.CROSS_SMALL:
            half = 2.0
            add_line(coord(x - half, y + half), coord(x + half, y - half))
            add_line(coord(x - half, y - half), coord(x + half, y + half))
        elif self.symbol == NoErcSymbol.CHECKBOX:
            stem_x, stem_y = self._transform_local_point(x, y, -2.0, -2.0)
            operations.append(
                SchGeometryOp.lines(
                    [coord(x, y), coord(stem_x, stem_y)], pen=checkbox_stem_pen
                )
            )

            rect_points_svg = [
                self._transform_local_point(x, y, -6.0, -6.0),
                self._transform_local_point(x, y, -2.0, -6.0),
                self._transform_local_point(x, y, -2.0, -2.0),
                self._transform_local_point(x, y, -6.0, -2.0),
            ]
            rect_x = min(point_x for point_x, _ in rect_points_svg)
            rect_y = min(point_y for _, point_y in rect_points_svg)
            rect_width = max(point_x for point_x, _ in rect_points_svg) - rect_x
            rect_height = max(point_y for _, point_y in rect_points_svg) - rect_y
            rect_x1, rect_y1 = svg_coord_to_geometry(
                rect_x,
                rect_y,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            rect_x2, rect_y2 = svg_coord_to_geometry(
                rect_x + rect_width,
                rect_y + rect_height,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            operations.append(
                SchGeometryOp.rounded_rectangle(
                    x1=rect_x1,
                    y1=rect_y1,
                    x2=rect_x2,
                    y2=rect_y2,
                    pen=directive_pen,
                )
            )

            operations.append(
                SchGeometryOp.lines(
                    [
                        coord(rect_x + 0.5, rect_y + 1.6667),
                        coord(rect_x + 1.5, rect_y + 2.6667),
                        coord(rect_x + 3.5, rect_y + 0.6667),
                    ],
                    pen=directive_pen,
                )
            )
        elif self.symbol == NoErcSymbol.TRIANGLE:
            triangle_points = [
                pt(0.0, 0.0),
                pt(2.6667, -4.6188),
                pt(-2.6667, -4.6188),
            ]
            operations.append(
                SchGeometryOp.polygons(
                    [triangle_points],
                    brush=brush,
                )
            )
            operations.append(
                SchGeometryOp.polygons(
                    [triangle_points],
                    pen=triangle_pen,
                )
            )

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="noerc",
            object_id="eNoERC",
            bounds=SchGeometryBounds(
                left=int((self.location.x - 5) * 10000),
                top=int((self.location.y + 5) * 10000),
                right=int((self.location.x + 5) * 10000),
                bottom=int((self.location.y - 5) * 10000),
            ),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def __repr__(self) -> str:
        return (
            f"<AltiumSchNoErc at=({self.location.x}, {self.location.y}) "
            f"symbol={self.symbol.name} active={self.is_active}>"
        )
