"""Schematic record model for SchRecordType.NO_ERC."""

from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_sch_geometry_oracle import SchGeometryRecord
    from .altium_sch_svg_renderer import SchSvgRenderContext

from .altium_sch_enums import Rotation90
from .altium_record_types import SchGraphicalObject, SchRecordType
from ._sch_managed_defaults import DIRECTIVE_COLOR, GRAPHICAL_FILL_COLOR
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

_DEFAULT_ERROR_KIND_SET = (
    "Port not linked to parent sheet symbol,Unconnected wires,"
    "Nets with no driving source,Nets containing floating input pins,"
    "Nets with multiple names,Mismatched bus-section index ordering,"
    "Bus indices out of range,Bus range syntax errors,Illegal bus range values,"
    "Mismatched bus widths,Mismatched bus label ordering,"
    "Mixed generic and numeric bus labeling,Duplicate nets,"
    "Sheet symbol with duplicate entries,Sheets containing duplicate ports,"
    "Floating net labels,Floating power objects,Nets with only one pin,"
    "Signals with no load,Signals with no driver,"
    "Multiple harness types on a harness"
)
_ERROR_KIND_NAMES = (
    "Off-grid object",
    "Object not completely within sheet boundaries",
    "Missing child sheet for sheet symbol",
    "Missing sub-Project sheet for component",
    "Port not linked to parent sheet symbol",
    "Sheet entry not linked to child sheet",
    "Duplicate sheet numbers",
    "Unconnected wires",
    "Unconnected objects in net",
    "Nets with no driving source",
    "Nets containing floating input pins",
    "Nets with possible connection problems",
    "Nets containing multiple similar objects",
    "Nets with multiple names",
    "Adding items from hidden net to net",
    "Adding hidden net to sheet",
    "Global power-object scope changes",
    "Net parameters with no name",
    "Net parameters with no value",
    "Mismatched bus-section index ordering",
    "Mismatched generics on bus (first index) ",
    "Mismatched generics on bus (second index) ",
    "Mismatched electrical types on bus",
    "Bus indices out of range",
    "Bus range syntax errors",
    "Illegal bus definitions",
    "Illegal bus range values",
    "Mismatched bus widths",
    "Mismatched bus label ordering",
    "Mixed generic and numeric bus labeling",
    "Un-designated parts requiring annotation",
    "Duplicate part designators",
    "Duplicate sheet symbol names",
    "Duplicate nets",
    "Components with duplicate pins",
    "Sheet symbol with duplicate entries",
    "Sheets containing duplicate ports",
    "Components containing duplicate sub-parts",
    "Mismatched hidden pin connections",
    "Mismatched pin visibility",
    "Same parameter containing different values",
    "Same parameter containing different types",
    "Missing component models",
    "Models found in different model locations",
    "Missing component models in model files",
    "Duplicate component models",
    "Missing component model parameters",
    "Errors in component model parameters",
    "Component implementation with duplicate pins usage",
    "Component implementations with invalid pin mappings",
    "Component implementations with missing pins in sequence",
    "Components with duplicate implementations",
    "Unused sub-part in component",
    "Extra pin found in component display mode",
    "Missing pin found in component display mode",
    "Mismatched bus/wire object on wire/bus",
    "Floating net labels",
    "Floating power objects",
    "Nets with only one pin",
    "Signals with no load",
    "Signals with no driver",
    "Signals with multiple drivers",
    "Auto-assigned ports to device pins",
    "No error",
    "Multiple top-level documents",
    "Multiple configuration targets",
    "Conflicting constraints",
    "Missing configuration target",
    "Unique identifiers errors",
    "Missing positive net in differential pair",
    "Missing negative net in differential pair",
    "Same net used in multiple differential pairs",
    "Differential pair unproperly connected to device",
    "Differential pair net connection polarity inversed",
    "Differential pair net unconnected to differential pair pin",
    "Constraint port without pin in configuration",
    "Constraint board not found in configuration",
    "Constraint connector creation failed in configuration",
    "Constraint configuration has duplicate board instance",
    "Missing child HDL entity for sheet symbol",
    "Forbidden OpenBus link",
    "Mismatching address/data widths of OpenBus ports",
    "Conflicting harness definition",
    "Missing harness type on harness",
    "Unknown harness type",
    "Bus object on a harness",
    "Harness object on a wire",
    "Harness object on a bus",
    "Harness connector type syntax error",
    "Multiple harness types on a harness",
    "Cascaded interconnects in OpenBus document",
    "Arbiter loop in OpenBus document",
    "Missing exported function in source file",
    "No exported functions in code symbol",
    "Duplicate code entry names in code symbol",
    "Reserved names used in code symbol",
    "Identifier case mismatch between code symbol and source file",
    "HDL identifier renamed",
    "Ambiguous device sheet path resolution",
    "Sheet names clash",
    "Missing model editor",
    "Sheets containing duplicate FSM states",
    "No input transitions to FSM state",
    "No output transitions from FSM state",
    "Multiple transitions between FSM states",
    "FSM transition with empty condition",
    "No default FSM state specified",
    "Unreachable FSM states",
    "Unused FSM port",
    "Component revision has inapplicable state",
    "Incorrect link in project variant",
    "Component revision is Out of Date",
    "Component revision is Obsolete",
    "Different Net Names",
    "Entry Is Empty",
    "No Mated Part",
    "No Net",
    "Unresolved Conflict",
    "Circular Document Dependency",
    "Unsupported multi-channel alternate item",
    "Fail to add alternate item",
    "External and Schematic Net Names are Unsynchronized",
    "Component has been deleted",
    "Generic Component",
    "Floating Directive Object",
    "Sheet Symbols with duplicated indexes",
    "Duplicated project files",
    "Unwired connection",
    "Unterminated wire",
    "Electrical mismatch - wire terminated at a wrong pin",
    "Electrical mismatch - shorted nets",
    "Empty Cable object",
    "Empty Shield object",
    "Empty Twist object",
    "Unconnected Splice object",
    "Duplicate Designator (WD)",
    "Unwired shield connection",
    "Unspecified connector cavities - no part choice for active pins",
    "Cable with only 1 element",
    "Shield with only 1 element",
    "Twist with only 1 element",
    "Splice with only 1 wire",
    "Wire-net with multiple wire colors",
    "Unnecessary Splice",
    "Unnecessary Tap",
    "Empty Tap object",
    "No tapped Wire in Tap object",
    "Orphaned wire - not routed via any bundle",
    "Orphaned connection point - no wires routed nor objects assigned",
    "Empty bundle object",
    "Duplicate Designator (LD)",
    "Unconnected Harness Entry",
    "Invalid Connection to a Harness Connector",
    "Port with no matching ports",
    "Functional Block has no implementation",
    "Functional Block key components have no implementation",
    "Functional Block key components are not properly implemented",
    "Interface is not properly implemented",
    "I2C Interface has no implementation",
    "I2C Interface implementation has wrong connectivity",
    "I2C Interface implementation contains no pull-up resistors",
    "Reuse Block revision has inapplicable state",
    "Reuse Block revision is Out of Date",
    "Direct connection to Reuse Block's object",
    "Mismatched parameters in connected wire segments",
)
_CONNECTION_PAIR_CODES = (
    "PNI",
    "PNB",
    "PNO",
    "PNC",
    "PNP",
    "PNZ",
    "PNE",
    "PNR",
    "PTI",
    "PTO",
    "PTB",
    "PTU",
    "SEI",
    "SEO",
    "SEB",
    "SEU",
    "UNC",
)
_CONNECTION_PAIRS = tuple(
    f"{left}_{right}"
    for left_index, left in enumerate(_CONNECTION_PAIR_CODES)
    for right in _CONNECTION_PAIR_CODES[left_index:]
)
_DEFAULT_CONNECTION_PAIRS = ",".join(_CONNECTION_PAIRS)


def _normalize_error_kind_set(value: str) -> str:
    selected = {item.lower() for item in value.split(",") if item}
    return ",".join(item for item in _ERROR_KIND_NAMES if item.lower() in selected)


def _normalize_connection_pairs(value: str) -> str:
    source = value.lower()
    selected: list[str] = []
    for pair in _CONNECTION_PAIRS:
        left, right = pair.split("_", maxsplit=1)
        if pair.lower() in source or f"{right}_{left}".lower() in source:
            selected.append(pair)
    return ",".join(selected)


class AltiumSchNoErc(RotatedLocalPointMixin, SchGraphicalObject):
    """
    NO_ERC record.
    """

    _GEOMETRY_HAIRLINE_WIDTH_PX = 0.01
    _GEOMETRY_DIRECTIVE_WIDTH_PX = 0.5
    _GEOMETRY_CHECKBOX_STEM_WIDTH_PX = 0.1

    def __init__(self) -> None:
        super().__init__()
        self.color = DIRECTIVE_COLOR
        self.area_color = GRAPHICAL_FILL_COLOR
        self._init_family_dynamic_unique_id()
        self.orientation: Rotation90 = Rotation90.DEG_90
        self.symbol: NoErcSymbol = NoErcSymbol.CROSS_THIN
        self.is_active: bool = True
        self.suppress_all: bool = True
        self._error_kind_set_to_suppress: str = _DEFAULT_ERROR_KIND_SET
        self._connection_pairs_to_suppress: str = _DEFAULT_CONNECTION_PAIRS
        self._symbol_was_string: bool = True
        self._use_pascal_case: bool = True
        self._source_state: tuple[object, ...] = ()
        self._capture_graphical_source_state()

    @property
    def error_kind_set_to_suppress(self) -> str:
        return self._error_kind_set_to_suppress

    @error_kind_set_to_suppress.setter
    def error_kind_set_to_suppress(self, value: str) -> None:
        self._error_kind_set_to_suppress = _normalize_error_kind_set(str(value))

    @property
    def connection_pairs_to_suppress(self) -> str:
        return self._connection_pairs_to_suppress

    @connection_pairs_to_suppress.setter
    def connection_pairs_to_suppress(self, value: str) -> None:
        self._connection_pairs_to_suppress = _normalize_connection_pairs(str(value))

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
        self._parse_family_dynamic_unique_id(s, record)

        orient_val, _ = s.read_int(record, Fields.ORIENTATION, default=0)
        self.orientation = Rotation90(orient_val)

        symbol_str, _ = s.read_str(record, Fields.SYMBOL, default="Thin Cross")
        if symbol_str in _SYMBOL_STRING_MAP:
            self.symbol = _SYMBOL_STRING_MAP[symbol_str]
            self._symbol_was_string = True
        else:
            self.symbol = NoErcSymbol.CROSS_THIN
            self._symbol_was_string = False

        self.is_active, _ = s.read_bool(record, Fields.IS_ACTIVE, default=True)
        self.suppress_all, _ = s.read_bool(record, Fields.SUPPRESS_ALL, default=True)
        self.error_kind_set_to_suppress, _ = s.read_str(
            record,
            "ErrorKindSetToSuppress",
            default=_DEFAULT_ERROR_KIND_SET,
        )
        self.connection_pairs_to_suppress, _ = s.read_str(
            record,
            "ConnectionPairsToSuppress",
            default=_DEFAULT_CONNECTION_PAIRS if self.suppress_all else "",
        )
        self._apply_imported_color_defaults(area_color=False)
        self._apply_nonpersisted_area_color_default()
        self._source_state = self._semantic_state()

    def _semantic_state(self) -> tuple[object, ...]:
        return (
            self.orientation,
            self.symbol,
            self.is_active,
            self.suppress_all,
            self.error_kind_set_to_suppress,
            self.connection_pairs_to_suppress,
        )

    def serialize_to_record(self) -> dict[str, Any]:
        record = super().serialize_to_record()
        mode = CaseMode.PASCALCASE if self._use_pascal_case else CaseMode.UPPERCASE
        s = AltiumSerializer(mode)
        self._serialize_managed_family_color(
            record, s, Fields.COLOR.canonical, int(self.color or 0)
        )
        self._write_orientation(record, s)
        self._write_symbol(record, s)
        self._write_suppression_flags(record, s)
        self._write_suppression_details(record, s)
        self._serialize_family_dynamic_unique_id(record, s)
        return self._order_authored_graphical_fields(record, self._family_order())

    def _write_orientation(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        if self.orientation.value != 0:
            serializer.write_int(
                record,
                Fields.ORIENTATION,
                self.orientation.value,
                self._raw_record,
                force=bool(self._source_state)
                and self.orientation != self._source_state[0],
            )
        else:
            serializer.remove_field(record, Fields.ORIENTATION)

    def _write_symbol(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        if (
            self._raw_record is None
            or not self._source_state
            or self.symbol != self._source_state[1]
        ):
            serializer.write_str(
                record,
                Fields.SYMBOL,
                _SYMBOL_ENUM_TO_STRING.get(self.symbol, str(self.symbol.value)),
                self._raw_record,
                force=bool(self._source_state) and self.symbol != self._source_state[1],
            )

    def _write_suppression_flags(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        serializer.write_bool(
            record,
            Fields.IS_ACTIVE,
            self.is_active,
            self._raw_record,
            force=bool(self._source_state) and self.is_active != self._source_state[2],
        )
        serializer.write_bool(
            record,
            Fields.SUPPRESS_ALL,
            self.suppress_all,
            self._raw_record,
            force=bool(self._source_state)
            and self.suppress_all != self._source_state[3],
        )

    def _write_suppression_details(
        self, record: dict[str, object], serializer: AltiumSerializer
    ) -> None:
        if self.suppress_all:
            self._remove_fields_case_insensitively(
                record,
                ["ErrorKindSetToSuppress", "ConnectionPairsToSuppress"],
            )
            return
        self._write_suppression_detail(
            record,
            serializer,
            "ErrorKindSetToSuppress",
            self.error_kind_set_to_suppress,
        )
        self._write_suppression_detail(
            record,
            serializer,
            "ConnectionPairsToSuppress",
            self.connection_pairs_to_suppress,
        )

    def _write_suppression_detail(
        self,
        record: dict[str, object],
        serializer: AltiumSerializer,
        field: str,
        value: str,
    ) -> None:
        if not value:
            self._remove_fields_case_insensitively(record, [field])
            return
        # FileFormatV5 always exports initialized detail sets while suppression
        # is active, even when the source omitted the payload.
        serializer.write_str(
            record,
            field,
            value,
            self._raw_record,
            force=True,
        )

    @staticmethod
    def _family_order() -> tuple[str, ...]:
        return (
            "Location.X",
            "Location.X_Frac",
            "Location.Y",
            "Location.Y_Frac",
            "Color",
            "Orientation",
            "Symbol",
            "IsActive",
            "SuppressAll",
            "ErrorKindSetToSuppress",
            "ConnectionPairsToSuppress",
            "UniqueID",
        )

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
            _geometry_item_length,
            make_rounded_rectangle_operation,
            make_pen,
            make_solid_brush,
            wrap_record_operations,
        )

        x, y = ctx.transform_coord_precise(self.location)
        sheet_height_px = float(ctx.sheet_height or 0.0)
        if not self.is_active:
            color_raw = 0x808080
        elif self.color:
            color_raw = int(self.color)
        else:
            color_raw = 0
        hairline_pen = make_pen(
            color_raw,
            width=_geometry_item_length(
                self._GEOMETRY_HAIRLINE_WIDTH_PX,
                units_per_px=units_per_px,
            ),
        )
        directive_pen = make_pen(
            color_raw,
            width=_geometry_item_length(
                self._GEOMETRY_DIRECTIVE_WIDTH_PX,
                units_per_px=units_per_px,
            ),
        )
        checkbox_stem_pen = make_pen(
            color_raw,
            width=_geometry_item_length(
                self._GEOMETRY_CHECKBOX_STEM_WIDTH_PX,
                units_per_px=units_per_px,
            ),
        )
        triangle_pen = make_pen(color_raw, width=0)
        brush = make_solid_brush(color_raw)
        operations: list = []

        def coord(px: float, py: float) -> list[float]:
            coords = geometry_coord_list(
                px,
                py,
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            return coords

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
            operations.append(
                make_rounded_rectangle_operation(
                    x1_px=rect_x,
                    y1_px=rect_y,
                    x2_px=rect_x + rect_width,
                    y2_px=rect_y + rect_height,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                    source_rotation=ctx.rotation,
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
