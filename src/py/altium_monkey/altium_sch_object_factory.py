"""
Shared construction helpers for public schematic object creation APIs.
"""

from __future__ import annotations

from pathlib import Path

from .altium_record_sch__arc import AltiumSchArc
from .altium_record_sch__bezier import AltiumSchBezier
from .altium_record_sch__blanket import AltiumSchBlanket
from .altium_record_sch__bus import AltiumSchBus
from .altium_record_sch__bus_entry import AltiumSchBusEntry
from .altium_record_sch__compile_mask import AltiumSchCompileMask
from .altium_record_sch__cross_sheet_connector import AltiumSchCrossSheetConnector
from .altium_record_sch__ellipse import AltiumSchEllipse
from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc
from .altium_record_sch__file_name import AltiumSchFileName
from .altium_record_sch__harness_connector import (
    AltiumSchHarnessConnector,
    SchHarnessConnectorSide,
)
from .altium_record_sch__harness_entry import AltiumSchHarnessEntry, BusTextStyle
from .altium_record_sch__harness_type import AltiumSchHarnessType
from .altium_record_sch__image import AltiumSchImage
from .altium_record_sch__junction import AltiumSchJunction
from .altium_record_sch__label import AltiumSchLabel
from .altium_record_sch__line import AltiumSchLine
from .altium_record_sch__net_label import AltiumSchNetLabel
from .altium_record_sch__note import AltiumSchNote
from .altium_record_sch__no_erc import AltiumSchNoErc, NoErcSymbol
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_sch__parameter_set import AltiumSchParameterSet
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__polyline import AltiumSchPolyline
from .altium_record_sch__polygon import AltiumSchPolygon
from .altium_record_sch__port import AltiumSchPort
from .altium_record_sch__power_port import AltiumSchPowerPort
from .altium_record_sch__rectangle import AltiumSchRectangle
from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle
from .altium_record_sch__sheet_entry import (
    AltiumSchSheetEntry,
    SchSheetEntryArrowKind,
    SchSheetEntryIOType,
    SheetEntrySide,
)
from .altium_record_sch__sheet_name import AltiumSchSheetName
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol, SchSheetSymbolType
from .altium_record_sch__signal_harness import AltiumSchSignalHarness
from .altium_record_sch__text_frame import AltiumSchTextFrame
from .altium_record_sch__wire import AltiumSchWire
from .altium_sch_enums import (
    IeeeSymbol,
    OffSheetConnectorStyle,
    ParameterSetStyle,
    PinElectrical,
    PinTextRotation,
    PortIOType,
    PortStyle,
    PowerObjectStyle,
    Rotation90,
)
from .altium_record_types import (
    ColorValue,
    LineShape,
    LineStyle,
    LineWidth,
    SchFontSpec,
    SchPointMils,
    SchRectMils,
    TextJustification,
    TextOrientation,
)
from .altium_sch_enums import SchHorizontalAlign


def _normalize_optional_color_value(
    field_name: str, value: ColorValue | None
) -> int | None:
    if value is None:
        return None
    if not isinstance(value, ColorValue):
        raise TypeError(f"{field_name} must be a ColorValue or None")
    return value.win32


def _validate_non_negative_public_mils(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer number of mils")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _validate_line_width_enum(value: LineWidth) -> LineWidth:
    if not isinstance(value, LineWidth):
        raise TypeError("line_width must be a LineWidth value")
    return value


def _validate_line_style_enum(value: LineStyle) -> LineStyle:
    if not isinstance(value, LineStyle):
        raise TypeError("line_style must be a LineStyle value")
    return value


def _validate_line_shape_enum(value: LineShape, *, field_name: str) -> LineShape:
    if not isinstance(value, LineShape):
        raise TypeError(f"{field_name} must be a LineShape value")
    return value


def _validate_rotation90_enum(value: Rotation90) -> Rotation90:
    if not isinstance(value, Rotation90):
        raise TypeError("orientation must be a Rotation90 value")
    return value


def _validate_pin_electrical_enum(value: PinElectrical) -> PinElectrical:
    if not isinstance(value, PinElectrical):
        raise TypeError("electrical_type must be a PinElectrical value")
    return value


def _validate_pin_text_rotation_enum(
    field_name: str, value: PinTextRotation | None
) -> PinTextRotation | None:
    if value is None:
        return None
    if not isinstance(value, PinTextRotation):
        raise TypeError(f"{field_name} must be a PinTextRotation value or None")
    return value


def _validate_ieee_symbol_enum(field_name: str, value: IeeeSymbol) -> IeeeSymbol:
    if not isinstance(value, IeeeSymbol):
        raise TypeError(f"{field_name} must be an IeeeSymbol value")
    return value


def _validate_no_erc_symbol_enum(value: NoErcSymbol) -> NoErcSymbol:
    if not isinstance(value, NoErcSymbol):
        raise TypeError("symbol must be a NoErcSymbol value")
    return value


def _validate_path_points_mils(
    field_name: str,
    value: list[SchPointMils],
    *,
    minimum_count: int,
) -> list[SchPointMils]:
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list of SchPointMils values")
    if len(value) < minimum_count:
        raise ValueError(f"{field_name} must contain at least {minimum_count} points")
    for point in value:
        if not isinstance(point, SchPointMils):
            raise TypeError(f"{field_name} must contain only SchPointMils values")
    return value


def _validate_public_float(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    return float(value)


def _validate_public_bool(field_name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


def _validate_positive_public_mils(field_name: str, value: float) -> float:
    validated = _validate_public_float(field_name, value)
    if validated <= 0.0:
        raise ValueError(f"{field_name} must be greater than zero")
    return validated


def _validate_non_negative_public_float(field_name: str, value: float) -> float:
    validated = _validate_public_float(field_name, value)
    if validated < 0.0:
        raise ValueError(f"{field_name} must be non-negative")
    return validated


def _validate_text_orientation(value: TextOrientation) -> TextOrientation:
    if not isinstance(value, TextOrientation):
        raise TypeError("orientation must be a TextOrientation value")
    return value


def _validate_text_justification(value: TextJustification) -> TextJustification:
    if not isinstance(value, TextJustification):
        raise TypeError("justification must be a TextJustification value")
    return value


def _validate_parameter_set_style(value: ParameterSetStyle) -> ParameterSetStyle:
    if not isinstance(value, ParameterSetStyle):
        raise TypeError("style must be a ParameterSetStyle value")
    return value


def _validate_port_style(value: PortStyle) -> PortStyle:
    if not isinstance(value, PortStyle):
        raise TypeError("style must be a PortStyle value")
    return value


def _validate_port_io_type(value: PortIOType) -> PortIOType:
    if not isinstance(value, PortIOType):
        raise TypeError("io_type must be a PortIOType value")
    return value


def _validate_power_object_style(value: PowerObjectStyle) -> PowerObjectStyle:
    if not isinstance(value, PowerObjectStyle):
        raise TypeError("style must be a PowerObjectStyle value")
    return value


def _validate_off_sheet_connector_style(
    value: OffSheetConnectorStyle,
) -> OffSheetConnectorStyle:
    if not isinstance(value, OffSheetConnectorStyle):
        raise TypeError("style must be an OffSheetConnectorStyle value")
    return value


def _validate_harness_connector_side(
    value: SchHarnessConnectorSide,
) -> SchHarnessConnectorSide:
    if not isinstance(value, SchHarnessConnectorSide):
        raise TypeError("side must be a SchHarnessConnectorSide value")
    return value


def _validate_sheet_symbol_type(
    value: SchSheetSymbolType,
) -> SchSheetSymbolType:
    if not isinstance(value, SchSheetSymbolType):
        raise TypeError("symbol_type must be a SchSheetSymbolType value")
    return value


def _validate_sheet_entry_side(value: SheetEntrySide) -> SheetEntrySide:
    if not isinstance(value, SheetEntrySide):
        raise TypeError("side must be a SheetEntrySide value")
    return value


def _validate_sheet_entry_io_type(
    value: SchSheetEntryIOType,
) -> SchSheetEntryIOType:
    if not isinstance(value, SchSheetEntryIOType):
        raise TypeError("io_type must be a SchSheetEntryIOType value")
    return value


def _validate_sheet_entry_arrow_kind(
    value: SchSheetEntryArrowKind,
) -> SchSheetEntryArrowKind:
    if not isinstance(value, SchSheetEntryArrowKind):
        raise TypeError("arrow_kind must be a SchSheetEntryArrowKind value")
    return value


def _validate_bus_text_style(value: BusTextStyle) -> BusTextStyle:
    if not isinstance(value, BusTextStyle):
        raise TypeError("text_style must be a BusTextStyle value")
    return value


def _validate_optional_pin_font(
    field_name: str, value: SchFontSpec | None
) -> SchFontSpec | None:
    if value is None:
        return None
    if not isinstance(value, SchFontSpec):
        raise TypeError(f"{field_name} must be a SchFontSpec value or None")
    if value.underline or value.strikeout:
        raise ValueError(f"{field_name} cannot use underline or strikeout for pin text")
    return value


def _validate_optional_owner_part_id(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("owner_part_id must be an int or None")
    if value < -1:
        raise ValueError("owner_part_id must be >= -1 or None")
    return value


def _validate_non_negative_public_int(field_name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


_DEFAULT_SHEET_SYMBOL_FILL = ColorValue.from_win32(15724527)
_DEFAULT_SHEET_ENTRY_BORDER = ColorValue.from_hex("#101010")
_DEFAULT_SHEET_ENTRY_FILL = ColorValue.from_hex("#FFFFFF")
_DEFAULT_HARNESS_CONNECTOR_BORDER = ColorValue.from_win32(13213327)
_DEFAULT_HARNESS_CONNECTOR_FILL = ColorValue.from_win32(16511725)
_DEFAULT_HARNESS_ENTRY_BORDER = ColorValue.from_win32(7354880)
_DEFAULT_HARNESS_ENTRY_FILL = ColorValue.from_win32(8454143)
_DEFAULT_SIGNAL_HARNESS_COLOR = ColorValue.from_win32(15187117)


def make_sch_pin(
    *,
    designator: str,
    location_mils: SchPointMils,
    name: str = "",
    orientation: Rotation90 = Rotation90.DEG_0,
    length_mils: float = 300.0,
    electrical_type: PinElectrical = PinElectrical.INPUT,
    pin_color: ColorValue = ColorValue.from_hex("#000000"),
    hidden: bool = False,
    name_visible: bool = True,
    designator_visible: bool = True,
    owner_part_id: int | None = None,
    owner_part_display_mode: int = 0,
    name_font: SchFontSpec | None = None,
    name_rotation: PinTextRotation | None = None,
    name_margin_mils: float | None = None,
    name_color: ColorValue | None = None,
    name_reference_to_component: bool = False,
    designator_font: SchFontSpec | None = None,
    designator_rotation: PinTextRotation | None = None,
    designator_margin_mils: float | None = None,
    designator_color: ColorValue | None = None,
    designator_reference_to_component: bool = False,
    description: str = "",
    swap_id_pin: str = "",
    swap_id_part: str = "",
    swap_id_sequence: str = "",
    default_value: str = "",
    symbol_inner: IeeeSymbol = IeeeSymbol.NONE,
    symbol_outer: IeeeSymbol = IeeeSymbol.NONE,
    symbol_inner_edge: IeeeSymbol = IeeeSymbol.NONE,
    symbol_outer_edge: IeeeSymbol = IeeeSymbol.NONE,
) -> AltiumSchPin:
    """
    Create a schematic library pin using public mil-space inputs.

    The returned object is an `AltiumSchPin` record. Add it to a symbol with
    `symbol.add_pin(pin)`. SchLib save resolves pin text font IDs and PinTextData
    streams; callers do not need to manage font-table indexes.

    Args:
        designator: Pin number shown on the symbol, such as "1" or "A1".
        location_mils: Pin origin in public schematic mils.
        name: Functional pin name shown near the pin.
        orientation: Pin direction as a `Rotation90` value.
        length_mils: Pin line length in mils. Fractional mil values are preserved
            in the text record fields when serialized.
        electrical_type: Electrical behavior as a `PinElectrical` enum value.
        pin_color: Pin line color as a `ColorValue`.
        hidden: Hide the complete pin when true.
        name_visible: Show the pin name when true.
        designator_visible: Show the pin designator when true.
        owner_part_id: Multi-part component part id. None uses the record default.
        owner_part_display_mode: Multi-part display mode id.
        name_font: Optional `SchFontSpec` for custom pin-name text.
        name_rotation: Optional pin-name text rotation, horizontal or vertical.
        name_margin_mils: Optional custom pin-name text margin in mils.
        name_color: Optional pin-name text color as a `ColorValue`.
        name_reference_to_component: Position name text relative to the component
            body instead of the pin.
        designator_font: Optional `SchFontSpec` for custom designator text.
        designator_rotation: Optional designator text rotation.
        designator_margin_mils: Optional custom designator margin in mils.
        designator_color: Optional designator text color as a `ColorValue`.
        designator_reference_to_component: Position designator text relative to
            the component body instead of the pin.
        description: Optional pin description.
        swap_id_pin: Pin swap-group identifier.
        swap_id_part: Part swap-group identifier.
        swap_id_sequence: Swap sequence identifier.
        default_value: Optional simulation/default logic value.
        symbol_inner: IEEE symbol drawn inside the component body.
        symbol_outer: IEEE symbol drawn outside the component body.
        symbol_inner_edge: IEEE symbol drawn at the inner edge.
        symbol_outer_edge: IEEE symbol drawn at the outer edge.

    Returns:
        An unattached `AltiumSchPin` ready for `AltiumSymbol.add_pin(...)`.
    """
    if not isinstance(designator, str) or not designator:
        raise ValueError("designator must be a non-empty string")
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    orientation = _validate_rotation90_enum(orientation)
    length_value = _validate_positive_public_mils("length_mils", length_mils)
    electrical_type = _validate_pin_electrical_enum(electrical_type)
    if not isinstance(pin_color, ColorValue):
        raise TypeError("pin_color must be a ColorValue")

    name_font = _validate_optional_pin_font("name_font", name_font)
    designator_font = _validate_optional_pin_font("designator_font", designator_font)
    name_rotation = _validate_pin_text_rotation_enum("name_rotation", name_rotation)
    designator_rotation = _validate_pin_text_rotation_enum(
        "designator_rotation", designator_rotation
    )
    if name_margin_mils is not None:
        name_margin_mils = _validate_non_negative_public_float(
            "name_margin_mils", name_margin_mils
        )
    if designator_margin_mils is not None:
        designator_margin_mils = _validate_non_negative_public_float(
            "designator_margin_mils", designator_margin_mils
        )

    pin = AltiumSchPin(
        designator,
        name,
        int(round(location_mils.x_mils)),
        int(round(location_mils.y_mils)),
        orientation=orientation,
        length=int(round(length_value)),
        electrical_type=electrical_type,
        pin_color=pin_color.win32,
        hidden=_validate_public_bool("hidden", hidden),
        name_visible=_validate_public_bool("name_visible", name_visible),
        designator_visible=_validate_public_bool(
            "designator_visible", designator_visible
        ),
        owner_part_id=_validate_optional_owner_part_id(owner_part_id),
        owner_part_display_mode=_validate_non_negative_public_int(
            "owner_part_display_mode", owner_part_display_mode
        ),
        name_font=name_font.name if name_font else None,
        name_font_size=name_font.size if name_font else None,
        name_font_bold=name_font.bold if name_font else False,
        name_font_italic=name_font.italic if name_font else False,
        name_rotation=name_rotation,
        name_margin_mils=name_margin_mils,
        name_color=_normalize_optional_color_value("name_color", name_color),
        name_reference_to_component=_validate_public_bool(
            "name_reference_to_component", name_reference_to_component
        ),
        designator_font=designator_font.name if designator_font else None,
        designator_font_size=designator_font.size if designator_font else None,
        designator_font_bold=designator_font.bold if designator_font else False,
        designator_font_italic=designator_font.italic if designator_font else False,
        designator_rotation=designator_rotation,
        designator_margin_mils=designator_margin_mils,
        designator_color=_normalize_optional_color_value(
            "designator_color", designator_color
        ),
        designator_reference_to_component=_validate_public_bool(
            "designator_reference_to_component", designator_reference_to_component
        ),
        description=description,
        swap_id_pin=swap_id_pin,
        swap_id_part=swap_id_part,
        swap_id_sequence=swap_id_sequence,
        default_value=default_value,
        symbol_inner=_validate_ieee_symbol_enum("symbol_inner", symbol_inner),
        symbol_outer=_validate_ieee_symbol_enum("symbol_outer", symbol_outer),
        symbol_inner_edge=_validate_ieee_symbol_enum(
            "symbol_inner_edge", symbol_inner_edge
        ),
        symbol_outer_edge=_validate_ieee_symbol_enum(
            "symbol_outer_edge", symbol_outer_edge
        ),
    )
    pin.location = location_mils.to_coord_point()
    pin._length = int(length_value // 10.0)
    pin._length_mils = length_value
    return pin


def make_sch_note(
    *,
    bounds_mils: SchRectMils,
    text: str,
    author: str = "Author",
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    border_color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    text_color: ColorValue | None = None,
    alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT,
    word_wrap: bool = True,
    clip_to_rect: bool = True,
    text_margin_mils: int = 5,
    collapsed: bool = False,
) -> AltiumSchNote:
    """
    Construct a detached schematic note using public-facing note parameters.

    Args:
        bounds_mils: Absolute note rectangle in mils.
        text: Note text. Ordinary Python newlines are accepted and are encoded
            to Altium's multiline text format on save.
        author: Author string stored on the note object.
        font: Public font description resolved when the note is added to a
            document or library.
        border_color: Optional note border color helper. ``None`` leaves the
            native default color behavior.
        fill_color: Optional note fill color helper. ``None`` leaves the
            native default color behavior.
        text_color: Optional note text color helper. ``None`` leaves the native
            default color behavior.
        alignment: Horizontal text alignment inside the note.
        word_wrap: Whether note text wraps to the note width.
        clip_to_rect: Whether note text is clipped to the note bounds.
        text_margin_mils: Non-negative note text margin in mils.
        collapsed: Whether the note is saved in the collapsed native note form.

    Returns:
        A detached ``AltiumSchNote`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        This factory is the shared construction path for detached schematic
        note objects. The note is later added to a document with
        ``add_object(...)``, which resolves document-bound resources such as
        font IDs from ``font``.

        Note border width is intentionally not part of the public factory
        surface because native Altium schematic note import forces that setting
        back to the smallest border size on reopen.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    if not isinstance(alignment, SchHorizontalAlign):
        raise TypeError("alignment must be a SchHorizontalAlign value")
    validated_text_margin_mils = _validate_non_negative_public_mils(
        "text_margin_mils", text_margin_mils
    )
    location, corner = bounds_mils.to_coord_points()

    note = AltiumSchNote()
    note.location = location
    note.corner = corner
    note.text = text
    note.author = author
    note.font = font
    note.color = _normalize_optional_color_value("border_color", border_color)
    note.area_color = _normalize_optional_color_value("fill_color", fill_color)
    note.text_color = _normalize_optional_color_value("text_color", text_color)
    note.alignment = int(alignment)
    note.word_wrap = word_wrap
    note.clip_to_rect = clip_to_rect
    note.show_border = True
    note.is_solid = True
    note.text_margin_mils = validated_text_margin_mils
    note.collapsed = collapsed
    return note


def make_sch_no_erc(
    *,
    location_mils: SchPointMils,
    symbol: NoErcSymbol = NoErcSymbol.CROSS_THIN,
    orientation: Rotation90 = Rotation90.DEG_90,
    color: ColorValue = ColorValue.from_hex("#FF0000"),
    is_active: bool = True,
    suppress_all: bool = True,
    error_kind_set_to_suppress: str = "",
    connection_pairs_to_suppress: str = "",
) -> AltiumSchNoErc:
    """
    Construct a detached generic schematic No ERC directive.

    Args:
        location_mils: Absolute directive origin in mils.
        symbol: Native No ERC marker style enum.
        orientation: Native 90-degree orientation enum. Defaults to
            ``Rotation90.DEG_90`` to match Altium's new-object default.
        color: Directive color helper. Defaults to Altium's native directive
            red.
        is_active: Whether the directive is active.
        suppress_all: Whether the directive suppresses all supported ERC
            classes. When ``False``, the two optional suppression payload
            strings are written through to the record.
        error_kind_set_to_suppress: Advanced native suppression payload for
            partial No ERC directives. This is preserved as a raw string until
            a higher-level helper is designed for the native error-kind set.
        connection_pairs_to_suppress: Advanced native suppression payload for
            selective connection-pair suppression. This is preserved as a raw
            string until a higher-level helper is designed.

    Returns:
        A detached ``AltiumSchNoErc`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    validated_symbol = _validate_no_erc_symbol_enum(symbol)
    validated_orientation = _validate_rotation90_enum(orientation)
    if not isinstance(color, ColorValue):
        raise TypeError("color must be a ColorValue value")
    validated_color = color.win32
    if not isinstance(is_active, bool):
        raise TypeError("is_active must be a bool")
    if not isinstance(suppress_all, bool):
        raise TypeError("suppress_all must be a bool")
    if not isinstance(error_kind_set_to_suppress, str):
        raise TypeError("error_kind_set_to_suppress must be a string")
    if not isinstance(connection_pairs_to_suppress, str):
        raise TypeError("connection_pairs_to_suppress must be a string")

    no_erc = AltiumSchNoErc()
    no_erc.location_mils = location_mils
    no_erc.symbol = validated_symbol
    no_erc.orientation = validated_orientation
    no_erc.color = validated_color
    no_erc.is_active = is_active
    no_erc.suppress_all = suppress_all
    no_erc.error_kind_set_to_suppress = error_kind_set_to_suppress
    no_erc.connection_pairs_to_suppress = connection_pairs_to_suppress
    no_erc._symbol_was_string = True
    no_erc._use_pascal_case = True
    return no_erc


def make_sch_arc(
    *,
    center_mils: SchPointMils,
    radius_mils: float,
    start_angle_degrees: float = 0.0,
    end_angle_degrees: float = 90.0,
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
) -> AltiumSchArc:
    """
    Construct a detached circular schematic arc.

    Args:
        center_mils: Arc center point in mils.
        radius_mils: Arc radius in mils. Must be greater than zero.
        start_angle_degrees: Arc start angle in degrees.
        end_angle_degrees: Arc end angle in degrees.
        color: Optional arc stroke color helper. ``None`` leaves the native
            default color behavior.
        line_width: Arc stroke thickness enum. Defaults to ``LineWidth.SMALL``
            to match Altium's native new-arc default.

    Returns:
        A detached ``AltiumSchArc`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(center_mils, SchPointMils):
        raise TypeError("center_mils must be a SchPointMils value")
    validated_radius_mils = _validate_positive_public_mils("radius_mils", radius_mils)
    validated_start_angle = _validate_public_float(
        "start_angle_degrees", start_angle_degrees
    )
    validated_end_angle = _validate_public_float("end_angle_degrees", end_angle_degrees)
    validated_line_width = _validate_line_width_enum(line_width)

    arc = AltiumSchArc()
    arc.location_mils = center_mils
    arc.radius_mils = validated_radius_mils
    arc.start_angle = validated_start_angle
    arc.end_angle = validated_end_angle
    arc.color = _normalize_optional_color_value("color", color)
    arc.line_width = validated_line_width
    return arc


def make_sch_full_circle(
    *,
    center_mils: SchPointMils,
    radius_mils: float,
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
) -> AltiumSchArc:
    """
    Construct a detached full circular outline using the schematic arc record.

    Args:
        center_mils: Circle center point in mils.
        radius_mils: Circle radius in mils. Must be greater than zero.
        color: Optional circle stroke color helper. ``None`` leaves the native
            default color behavior.
        line_width: Circle stroke thickness enum. Defaults to
            ``LineWidth.SMALL`` to match Altium's native new-arc default.

    Returns:
        A detached ``AltiumSchArc`` configured as a 360-degree circle and ready
        to be added with ``schdoc.add_object(...)``.
    """
    return make_sch_arc(
        center_mils=center_mils,
        radius_mils=radius_mils,
        start_angle_degrees=0.0,
        end_angle_degrees=360.0,
        color=color,
        line_width=line_width,
    )


def make_sch_ellipse(
    *,
    center_mils: SchPointMils,
    radius_mils: float,
    secondary_radius_mils: float,
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
    fill_background: bool = True,
) -> AltiumSchEllipse:
    """
    Construct a detached schematic ellipse or full ellipse outline.

    Args:
        center_mils: Ellipse center point in mils.
        radius_mils: Primary ellipse radius in mils. Must be greater than zero.
        secondary_radius_mils: Secondary ellipse radius in mils. Must be
            greater than zero.
        color: Optional ellipse stroke color helper. ``None`` leaves the native
            default color behavior.
        fill_color: Optional fill color helper. ``None`` leaves the native
            default area-color behavior.
        line_width: Ellipse stroke thickness enum. Defaults to
            ``LineWidth.SMALLEST`` to match Altium's native new-ellipse
            default.
        fill_background: Whether the ellipse is saved with the native solid
            fill flag enabled.

    Returns:
        A detached ``AltiumSchEllipse`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(center_mils, SchPointMils):
        raise TypeError("center_mils must be a SchPointMils value")
    validated_radius_mils = _validate_positive_public_mils("radius_mils", radius_mils)
    validated_secondary_radius_mils = _validate_positive_public_mils(
        "secondary_radius_mils", secondary_radius_mils
    )
    validated_line_width = _validate_line_width_enum(line_width)

    ellipse = AltiumSchEllipse()
    ellipse.location_mils = center_mils
    ellipse.radius_mils = validated_radius_mils
    ellipse.secondary_radius_mils = validated_secondary_radius_mils
    ellipse.color = _normalize_optional_color_value("color", color)
    ellipse.area_color = _normalize_optional_color_value("fill_color", fill_color)
    ellipse.line_width = validated_line_width
    ellipse.is_solid = fill_background
    ellipse.transparent = False
    return ellipse


def make_sch_elliptical_arc(
    *,
    center_mils: SchPointMils,
    radius_mils: float,
    secondary_radius_mils: float,
    start_angle_degrees: float = 0.0,
    end_angle_degrees: float = 90.0,
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
) -> AltiumSchEllipticalArc:
    """
    Construct a detached elliptical schematic arc.

    Args:
        center_mils: Elliptical arc center point in mils.
        radius_mils: Primary ellipse radius in mils. Must be greater than zero.
        secondary_radius_mils: Secondary ellipse radius in mils. Must be
            greater than zero.
        start_angle_degrees: Arc start angle in degrees.
        end_angle_degrees: Arc end angle in degrees.
        color: Optional stroke color helper. ``None`` leaves the native
            default color behavior.
        line_width: Stroke thickness enum. Defaults to ``LineWidth.SMALL`` to
            match Altium's native new-arc default.

    Returns:
        A detached ``AltiumSchEllipticalArc`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(center_mils, SchPointMils):
        raise TypeError("center_mils must be a SchPointMils value")
    validated_radius_mils = _validate_positive_public_mils("radius_mils", radius_mils)
    validated_secondary_radius_mils = _validate_positive_public_mils(
        "secondary_radius_mils", secondary_radius_mils
    )
    validated_start_angle = _validate_public_float(
        "start_angle_degrees", start_angle_degrees
    )
    validated_end_angle = _validate_public_float("end_angle_degrees", end_angle_degrees)
    validated_line_width = _validate_line_width_enum(line_width)

    arc = AltiumSchEllipticalArc()
    arc.location_mils = center_mils
    arc.radius_mils = validated_radius_mils
    arc.secondary_radius_mils = validated_secondary_radius_mils
    arc.start_angle = validated_start_angle
    arc.end_angle = validated_end_angle
    arc.color = _normalize_optional_color_value("color", color)
    arc.line_width = validated_line_width
    return arc


def make_sch_line(
    *,
    start_mils: SchPointMils,
    end_mils: SchPointMils,
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
    line_style: LineStyle = LineStyle.SOLID,
) -> AltiumSchLine:
    """
    Construct a detached schematic line segment.

    Args:
        start_mils: Line start point in mils.
        end_mils: Line end point in mils.
        color: Optional line color helper. ``None`` leaves the native default
            color behavior.
        line_width: Line stroke thickness enum. Defaults to
            ``LineWidth.SMALL`` to match Altium's native new-line default.
        line_style: Line dash pattern enum. Defaults to ``LineStyle.SOLID`` to
            match Altium's native new-line default.

    Returns:
        A detached ``AltiumSchLine`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(start_mils, SchPointMils):
        raise TypeError("start_mils must be a SchPointMils value")
    if not isinstance(end_mils, SchPointMils):
        raise TypeError("end_mils must be a SchPointMils value")
    validated_line_width = _validate_line_width_enum(line_width)
    validated_line_style = _validate_line_style_enum(line_style)

    line = AltiumSchLine()
    line.location_mils = start_mils
    line.corner_mils = end_mils
    line.color = _normalize_optional_color_value("color", color)
    line.line_width = validated_line_width
    line.line_style = validated_line_style
    return line


def make_sch_wire(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
) -> AltiumSchWire:
    """
    Construct a detached schematic wire path.

    Args:
        points_mils: Ordered wire path points in mils. At least two points are
            required.
        color: Optional wire color helper. ``None`` leaves the native default
            wire color behavior.
        line_width: Wire thickness enum. Defaults to ``LineWidth.SMALL`` to
            match Altium's native new-wire default.

    Returns:
        A detached ``AltiumSchWire`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=2,
    )
    validated_line_width = _validate_line_width_enum(line_width)

    wire = AltiumSchWire()
    wire.points_mils = list(validated_points)
    wire.color = _normalize_optional_color_value("color", color)
    wire.line_width = validated_line_width
    return wire


def make_sch_junction(
    *,
    location_mils: SchPointMils,
    color: ColorValue | None = None,
) -> AltiumSchJunction:
    """
    Construct a detached schematic wire junction.

    Args:
        location_mils: Absolute junction center in mils.
        color: Optional junction color helper. ``None`` leaves the native
            junction color behavior.

    Returns:
        A detached ``AltiumSchJunction`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")

    junction = AltiumSchJunction()
    junction.location_mils = location_mils
    junction.color = _normalize_optional_color_value("color", color)
    return junction


def make_sch_bezier(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
) -> AltiumSchBezier:
    """
    Construct a detached schematic bezier curve.

    Args:
        points_mils: Ordered cubic bezier control points in mils. Native
            schematic beziers use ``4 + 3n`` points: four points for the first
            segment, then three more for each connected segment.
        color: Optional bezier stroke color helper. ``None`` leaves the native
            default color behavior.
        line_width: Bezier stroke thickness enum. Defaults to
            ``LineWidth.SMALL`` to match Altium's native new-bezier default.

    Returns:
        A detached ``AltiumSchBezier`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        Native V5 bezier serialization does not carry a line-style field, so
        the public factory intentionally omits any ``line_style`` parameter.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=4,
    )
    if (len(validated_points) - 1) % 3 != 0:
        raise ValueError(
            "points_mils must contain 4 + 3n points for cubic bezier segments"
        )
    validated_line_width = _validate_line_width_enum(line_width)

    bezier = AltiumSchBezier()
    bezier.points_mils = list(validated_points)
    bezier.color = _normalize_optional_color_value("color", color)
    bezier.line_width = validated_line_width
    return bezier


def make_sch_rectangle(
    *,
    bounds_mils: SchRectMils,
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
    line_style: LineStyle = LineStyle.SOLID,
    fill_background: bool = True,
    transparent_fill: bool = False,
) -> AltiumSchRectangle:
    """
    Construct a detached schematic rectangle.

    Args:
        bounds_mils: Absolute rectangle bounds in mils.
        color: Optional border color helper. ``None`` leaves the native default
            color behavior.
        fill_color: Optional fill color helper. ``None`` leaves the native
            default area-color behavior.
        line_width: Rectangle border thickness enum. Defaults to
            ``LineWidth.SMALLEST`` to match Altium's native new-rectangle
            default.
        line_style: Rectangle border dash pattern enum.
        fill_background: Whether the rectangle interior is filled.
        transparent_fill: Whether the filled interior uses the native
            transparent-fill flag.

    Returns:
        A detached ``AltiumSchRectangle`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    validated_line_width = _validate_line_width_enum(line_width)
    validated_line_style = _validate_line_style_enum(line_style)
    location, corner = bounds_mils.to_coord_points()

    rectangle = AltiumSchRectangle()
    rectangle.location = location
    rectangle.corner = corner
    rectangle.color = _normalize_optional_color_value("color", color)
    rectangle.area_color = _normalize_optional_color_value("fill_color", fill_color)
    rectangle.line_width = validated_line_width
    rectangle.line_style = validated_line_style
    rectangle.is_solid = fill_background
    rectangle.transparent = transparent_fill
    return rectangle


def make_sch_compile_mask(
    *,
    bounds_mils: SchRectMils,
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
    collapsed: bool = False,
) -> AltiumSchCompileMask:
    """
    Construct a detached schematic compile-mask directive.

    Args:
        bounds_mils: Absolute compile-mask bounds in mils.
        color: Optional border color helper. ``None`` leaves the native
            default color behavior.
        fill_color: Optional fill color helper. ``None`` leaves the native
            default area-color behavior.
        line_width: Compile-mask border thickness enum. Defaults to
            ``LineWidth.SMALLEST`` to match the native new-object baseline.
        collapsed: Whether the compile mask starts in its collapsed display
            state.

    Returns:
        A detached ``AltiumSchCompileMask`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        Native V5 compile-mask records do not carry the general rectangle
        ``line_style`` surface, so the public factory intentionally focuses on
        bounds, colors, thickness, and collapse state.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    validated_line_width = _validate_line_width_enum(line_width)
    location, corner = bounds_mils.to_coord_points()

    compile_mask = AltiumSchCompileMask()
    compile_mask.location = location
    compile_mask.corner = corner
    compile_mask.color = _normalize_optional_color_value("color", color)
    compile_mask.area_color = _normalize_optional_color_value(
        "fill_color",
        fill_color,
    )
    compile_mask.line_width = validated_line_width
    compile_mask.is_collapsed = collapsed
    return compile_mask


def make_sch_rounded_rectangle(
    *,
    bounds_mils: SchRectMils,
    corner_x_radius_mils: float = 200.0,
    corner_y_radius_mils: float = 200.0,
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
    fill_background: bool = True,
) -> AltiumSchRoundedRectangle:
    """
    Construct a detached schematic rounded rectangle.

    Args:
        bounds_mils: Absolute rounded-rectangle bounds in mils.
        corner_x_radius_mils: Rounded-corner X radius in mils. Must be
            non-negative.
        corner_y_radius_mils: Rounded-corner Y radius in mils. Must be
            non-negative.
        color: Optional border color helper. ``None`` leaves the native default
            color behavior.
        fill_color: Optional fill color helper. ``None`` leaves the native
            default area-color behavior.
        line_width: Rounded-rectangle border thickness enum. Defaults to
            ``LineWidth.SMALLEST`` to match Altium's native new-object default.
        fill_background: Whether the rounded rectangle interior is filled.

    Returns:
        A detached ``AltiumSchRoundedRectangle`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        Native rounded rectangles do not expose the plain rectangle
        ``line_style`` or ``transparent`` surface in the V5 schematic record
        format, so those options are intentionally omitted here.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    validated_corner_x_radius_mils = _validate_non_negative_public_float(
        "corner_x_radius_mils",
        corner_x_radius_mils,
    )
    validated_corner_y_radius_mils = _validate_non_negative_public_float(
        "corner_y_radius_mils",
        corner_y_radius_mils,
    )
    validated_line_width = _validate_line_width_enum(line_width)
    location, corner = bounds_mils.to_coord_points()

    rounded_rectangle = AltiumSchRoundedRectangle()
    rounded_rectangle.location = location
    rounded_rectangle.corner = corner
    rounded_rectangle.corner_x_radius_mils = validated_corner_x_radius_mils
    rounded_rectangle.corner_y_radius_mils = validated_corner_y_radius_mils
    rounded_rectangle.color = _normalize_optional_color_value("color", color)
    rounded_rectangle.area_color = _normalize_optional_color_value(
        "fill_color",
        fill_color,
    )
    rounded_rectangle.line_width = validated_line_width
    rounded_rectangle.is_solid = fill_background
    return rounded_rectangle


def make_sch_polyline(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALL,
    line_style: LineStyle = LineStyle.SOLID,
    start_line_shape: LineShape = LineShape.NONE,
    end_line_shape: LineShape = LineShape.NONE,
    line_shape_size: LineWidth = LineWidth.SMALLEST,
) -> AltiumSchPolyline:
    """
    Construct a detached schematic polyline.

    Args:
        points_mils: Ordered polyline vertices in mils. At least two points are
            required.
        color: Optional polyline color helper. ``None`` leaves the native
            default color behavior.
        line_width: Polyline stroke thickness enum. Defaults to
            ``LineWidth.SMALL`` to match Altium's native new-polyline default.
        line_style: Polyline dash pattern enum.
        start_line_shape: Optional marker drawn on the first vertex.
        end_line_shape: Optional marker drawn on the last vertex.
        line_shape_size: Native endpoint marker size state. This reuses the
            public ``LineWidth`` enum because Altium stores endpoint marker
            size through the same four-step native size model used for
            schematic line widths: ``SMALLEST``, ``SMALL``, ``MEDIUM``, and
            ``LARGE``.

    Returns:
        A detached ``AltiumSchPolyline`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=2,
    )
    validated_line_width = _validate_line_width_enum(line_width)
    validated_line_style = _validate_line_style_enum(line_style)
    validated_start_line_shape = _validate_line_shape_enum(
        start_line_shape,
        field_name="start_line_shape",
    )
    validated_end_line_shape = _validate_line_shape_enum(
        end_line_shape,
        field_name="end_line_shape",
    )
    validated_line_shape_size = _validate_line_width_enum(line_shape_size)

    polyline = AltiumSchPolyline()
    polyline.points_mils = list(validated_points)
    polyline.color = _normalize_optional_color_value("color", color)
    polyline.line_width = validated_line_width
    polyline.line_style = validated_line_style
    polyline.start_line_shape = validated_start_line_shape
    polyline.end_line_shape = validated_end_line_shape
    polyline.line_shape_size = validated_line_shape_size
    return polyline


def make_sch_polygon(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.LARGE,
    fill_background: bool = True,
    transparent_fill: bool = False,
) -> AltiumSchPolygon:
    """
    Construct a detached schematic polygon.

    Args:
        points_mils: Ordered polygon vertices in mils. At least three points
            are required.
        color: Optional polygon border color helper. ``None`` leaves the
            native default color behavior.
        fill_color: Optional polygon fill color helper. ``None`` leaves the
            native default area-color behavior.
        line_width: Polygon border thickness enum. Defaults to
            ``LineWidth.LARGE`` to match Altium's native new-polygon default.
        fill_background: Whether the polygon interior is filled.
        transparent_fill: Whether the filled interior uses the native
            transparent-fill flag.

    Returns:
        A detached ``AltiumSchPolygon`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        Native V5 polygon serialization does not carry a line-style field, so
        the public factory intentionally omits any ``line_style`` parameter.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=3,
    )
    validated_line_width = _validate_line_width_enum(line_width)

    polygon = AltiumSchPolygon()
    polygon.points_mils = list(validated_points)
    polygon.color = _normalize_optional_color_value("color", color)
    polygon.area_color = _normalize_optional_color_value("fill_color", fill_color)
    polygon.line_width = validated_line_width
    polygon.is_solid = fill_background
    polygon.transparent = transparent_fill
    return polygon


def make_sch_blanket(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
    line_style: LineStyle = LineStyle.DASHED,
    fill_background: bool = False,
    transparent_fill: bool = True,
    collapsed: bool = False,
) -> AltiumSchBlanket:
    """
    Construct a detached schematic blanket directive.

    Args:
        points_mils: Ordered blanket polygon vertices in mils. At least three
            points are required.
        color: Optional blanket border color helper. ``None`` leaves the
            native default color behavior.
        fill_color: Optional blanket fill color helper. ``None`` leaves the
            native default area-color behavior.
        line_width: Blanket border thickness enum. Defaults to
            ``LineWidth.SMALLEST`` to match Altium's native new-blanket
            default.
        line_style: Blanket border dash pattern enum. Defaults to
            ``LineStyle.DASHED`` to match Altium's native new-blanket default.
        fill_background: Whether the blanket interior is filled. Defaults to
            ``False`` to match Altium's native new-blanket default.
        transparent_fill: Whether the blanket interior uses the native
            transparent-fill flag. Defaults to ``True`` to match native new
            blanket behavior.
        collapsed: Whether the blanket starts in its collapsed display state.

    Returns:
        A detached ``AltiumSchBlanket`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=3,
    )
    validated_line_width = _validate_line_width_enum(line_width)
    validated_line_style = _validate_line_style_enum(line_style)

    blanket = AltiumSchBlanket()
    blanket.points_mils = list(validated_points)
    blanket.color = _normalize_optional_color_value("color", color)
    blanket.area_color = _normalize_optional_color_value("fill_color", fill_color)
    blanket.line_width = validated_line_width
    blanket.line_style = validated_line_style
    blanket.is_solid = fill_background
    blanket.transparent = transparent_fill
    blanket.is_collapsed = collapsed
    return blanket


def make_sch_text_frame(
    *,
    bounds_mils: SchRectMils,
    text: str,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    border_color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    text_color: ColorValue | None = None,
    alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT,
    line_width: LineWidth = LineWidth.SMALL,
    word_wrap: bool = True,
    clip_to_rect: bool = True,
    text_margin_mils: int = 5,
    show_border: bool = True,
    fill_background: bool = True,
) -> AltiumSchTextFrame:
    """
    Construct a detached schematic text frame using public-facing parameters.

    Args:
        bounds_mils: Absolute text-frame rectangle in mils.
        text: Text content to place in the frame. Newlines are preserved.
        font: Public font description resolved when the frame is added to a
            document or library.
        border_color: Optional border color helper. ``None`` leaves the native
            default color behavior.
        fill_color: Optional background fill color helper. ``None`` leaves the
            native default color behavior.
        text_color: Optional text color helper. ``None`` leaves the native
            default color behavior.
        alignment: Horizontal text alignment inside the frame.
        line_width: Border thickness enum for the text-frame outline.
        word_wrap: Whether text wraps to the frame width.
        clip_to_rect: Whether text is clipped to the frame bounds.
        text_margin_mils: Non-negative text margin in mils.
        show_border: Whether the frame outline is drawn.
        fill_background: Whether the background is filled with ``fill_color``.

    Returns:
        A detached ``AltiumSchTextFrame`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    if not isinstance(alignment, SchHorizontalAlign):
        raise TypeError("alignment must be a SchHorizontalAlign value")
    validated_line_width = _validate_line_width_enum(line_width)
    validated_text_margin_mils = _validate_non_negative_public_mils(
        "text_margin_mils", text_margin_mils
    )
    location, corner = bounds_mils.to_coord_points()

    text_frame = AltiumSchTextFrame()
    text_frame.location = location
    text_frame.corner = corner
    text_frame.text = text
    text_frame.font = font
    text_frame.color = _normalize_optional_color_value("border_color", border_color)
    text_frame.area_color = _normalize_optional_color_value("fill_color", fill_color)
    text_frame.text_color = _normalize_optional_color_value("text_color", text_color)
    text_frame.alignment = int(alignment)
    text_frame.line_width = validated_line_width
    text_frame.word_wrap = word_wrap
    text_frame.clip_to_rect = clip_to_rect
    text_frame.show_border = show_border
    text_frame.is_solid = fill_background
    text_frame.text_margin_mils = validated_text_margin_mils
    return text_frame


def make_sch_embedded_image(
    *,
    bounds_mils: SchRectMils,
    source_path: str | Path,
    keep_aspect: bool = True,
    filename: str | None = None,
    orientation: Rotation90 = Rotation90.DEG_0,
    draw_border: bool = False,
    line_width: LineWidth = LineWidth.SMALLEST,
    border_color: ColorValue | None = None,
) -> AltiumSchImage:
    """
    Construct a detached schematic embedded-image object.

    Args:
        bounds_mils: Absolute image rectangle in mils.
        source_path: Path to the source image file to embed in the document.
        keep_aspect: Whether the image should preserve its aspect ratio.
        filename: Optional logical filename stored in the SchDoc storage stream.
            Defaults to the source file name.
        orientation: Image rotation in 90-degree increments.
        draw_border: Whether the image border is rendered.
        line_width: Border thickness enum when ``draw_border`` is enabled.
        border_color: Optional image border color helper. ``None`` leaves the
            native default border color behavior.

    Returns:
        A detached ``AltiumSchImage`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Embedded image not found: {source}")
    validated_orientation = _validate_rotation90_enum(orientation)
    validated_line_width = _validate_line_width_enum(line_width)
    location, corner = bounds_mils.to_coord_points()

    image = AltiumSchImage()
    image.location = location
    image.corner = corner
    image.embed_image = True
    image.keep_aspect = keep_aspect
    image.filename = filename or source.name
    image.orientation = validated_orientation
    image.is_solid = draw_border
    image.line_width = validated_line_width
    image.color = _normalize_optional_color_value("border_color", border_color)
    image.image_data = source.read_bytes()
    image.detect_format()
    return image


def make_sch_sheet_symbol(
    *,
    bounds_mils: SchRectMils,
    border_width: LineWidth = LineWidth.MEDIUM,
    border_color: ColorValue | None = None,
    fill_color: ColorValue | None = _DEFAULT_SHEET_SYMBOL_FILL,
    fill_background: bool = True,
    symbol_type: SchSheetSymbolType = SchSheetSymbolType.NORMAL,
    show_hidden_fields: bool = False,
    design_item_id: str = "",
    source_library_name: str = "",
    vault_guid: str = "",
    item_guid: str = "",
    revision_guid: str = "",
    revision_name: str = "",
) -> AltiumSchSheetSymbol:
    """
    Construct a detached hierarchical sheet symbol.

    Args:
        bounds_mils: Sheet-symbol body rectangle in mils.
        border_width: Symbol border thickness enum.
        border_color: Optional border color helper. ``None`` leaves the native
            border color behavior.
        fill_color: Optional interior fill color helper. The default matches
            Altium's ordinary new-sheet-symbol fill color.
        fill_background: Whether the sheet-symbol interior is filled.
        symbol_type: Native sheet-symbol type enum. Use
            ``SchSheetSymbolType.DEVICE_SHEET`` for the rounded-corner variant.
        show_hidden_fields: Whether the symbol is saved with hidden child
            fields visible.
        design_item_id: Optional design-item identifier string.
        source_library_name: Optional source-library name string.
        vault_guid: Optional vault GUID string.
        item_guid: Optional item GUID string.
        revision_guid: Optional revision GUID string.
        revision_name: Optional revision name string.

    Returns:
        A detached ``AltiumSchSheetSymbol`` ready to own entries, sheet name,
        and file name records before it is added with ``schdoc.add_object(...)``.

    Notes:
        This factory creates only the parent symbol record. Attach
        ``make_sch_sheet_entry(...)`` records with ``sheet_symbol.add_entry(...)``
        and labels with ``set_sheet_name(...)`` / ``set_file_name(...)``.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    validated_border_width = _validate_line_width_enum(border_width)
    validated_symbol_type = _validate_sheet_symbol_type(symbol_type)
    if not isinstance(show_hidden_fields, bool):
        raise TypeError("show_hidden_fields must be a bool")
    for field_name, field_value in (
        ("design_item_id", design_item_id),
        ("source_library_name", source_library_name),
        ("vault_guid", vault_guid),
        ("item_guid", item_guid),
        ("revision_guid", revision_guid),
        ("revision_name", revision_name),
    ):
        if not isinstance(field_value, str):
            raise TypeError(f"{field_name} must be a string")

    left_mils = min(bounds_mils.x1_mils, bounds_mils.x2_mils)
    right_mils = max(bounds_mils.x1_mils, bounds_mils.x2_mils)
    top_mils = max(bounds_mils.y1_mils, bounds_mils.y2_mils)
    bottom_mils = min(bounds_mils.y1_mils, bounds_mils.y2_mils)
    width_mils = right_mils - left_mils
    height_mils = top_mils - bottom_mils
    if width_mils <= 0 or height_mils <= 0:
        raise ValueError("bounds_mils must have positive width and height")

    symbol = AltiumSchSheetSymbol()
    symbol.location = SchPointMils.from_mils(left_mils, top_mils).to_coord_point()
    symbol.x_size = int(round(width_mils / 10.0))
    symbol.y_size = int(round(height_mils / 10.0))
    symbol.is_solid = fill_background
    symbol.line_width = validated_border_width
    symbol.color = _normalize_optional_color_value("border_color", border_color)
    symbol.area_color = _normalize_optional_color_value("fill_color", fill_color)
    symbol.symbol_type = (
        "DeviceSheet"
        if validated_symbol_type is SchSheetSymbolType.DEVICE_SHEET
        else "Normal"
    )
    symbol.show_hidden_fields = show_hidden_fields
    symbol.design_item_id = design_item_id
    symbol.source_library_name = source_library_name
    symbol.vault_guid = vault_guid
    symbol.item_guid = item_guid
    symbol.revision_guid = revision_guid
    symbol.revision_name = revision_name
    return symbol


def make_sch_sheet_entry(
    *,
    name: str,
    side: SheetEntrySide = SheetEntrySide.RIGHT,
    io_type: SchSheetEntryIOType = SchSheetEntryIOType.UNSPECIFIED,
    distance_from_top_mils: float,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10, bold=True),
    border_color: ColorValue | None = _DEFAULT_SHEET_ENTRY_BORDER,
    fill_color: ColorValue | None = _DEFAULT_SHEET_ENTRY_FILL,
    text_color: ColorValue | None = None,
    arrow_kind: SchSheetEntryArrowKind = SchSheetEntryArrowKind.BLOCK_TRIANGLE,
    text_style: BusTextStyle = BusTextStyle.FULL,
    harness_type: str = "",
) -> AltiumSchSheetEntry:
    """
    Construct a detached sheet entry owned by a sheet symbol.

    Args:
        name: Entry display name.
        side: Native sheet-entry side enum.
        io_type: Native sheet-entry I/O direction enum.
        distance_from_top_mils: Entry offset from the owning symbol edge in
            mils. Altium sheet entries use a 100-mil placement grid.
        font: Public font description resolved when the entry is added to a
            document or library.
        border_color: Optional entry border color helper.
        fill_color: Optional entry fill color helper.
        text_color: Optional entry text color helper.
        arrow_kind: Native sheet-entry arrow-shape enum.
        text_style: Shared native entry-label display style enum.
        harness_type: Optional native harness-type association string.

    Returns:
        A detached ``AltiumSchSheetEntry`` ready to be attached through
        ``sheet_symbol.add_entry(...)``.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_side = _validate_sheet_entry_side(side)
    validated_io_type = _validate_sheet_entry_io_type(io_type)
    validated_distance_mils = _validate_non_negative_public_float(
        "distance_from_top_mils", distance_from_top_mils
    )
    validated_arrow_kind = _validate_sheet_entry_arrow_kind(arrow_kind)
    validated_text_style = _validate_bus_text_style(text_style)
    if not isinstance(harness_type, str):
        raise TypeError("harness_type must be a string")

    entry = AltiumSchSheetEntry()
    entry.name = name
    entry.side = validated_side.value
    entry.io_type = validated_io_type.value
    entry.distance_from_top_mils = validated_distance_mils
    entry.font = font
    entry.color = _normalize_optional_color_value("border_color", border_color) or 0
    entry.area_color = (
        _normalize_optional_color_value("fill_color", fill_color) or 0xFFFFFF
    )
    entry.text_color = _normalize_optional_color_value("text_color", text_color) or 0
    entry.arrow_kind = validated_arrow_kind.value
    entry.text_style = {
        BusTextStyle.FULL: "Full",
        BusTextStyle.ABBREVIATED: "Abbreviated",
        BusTextStyle.SHORT: "Short",
    }[validated_text_style]
    entry.harness_type = harness_type
    return entry


def make_sch_sheet_name(
    *,
    text: str,
    location_mils: SchPointMils,
    font: SchFontSpec = SchFontSpec(name="Arial", size=14, bold=True),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    justification: TextJustification = TextJustification.BOTTOM_LEFT,
    mirrored: bool = False,
) -> AltiumSchSheetName:
    """
    Construct a detached sheet-name label owned by a sheet symbol.

    Args:
        text: Sheet-name text shown above the symbol body.
        location_mils: Label anchor location in mils.
        font: Public font description resolved when the label is added to a
            document or library.
        color: Optional text color helper.
        orientation: Text rotation in 90-degree increments.
        justification: Text anchor justification relative to ``location_mils``.
        mirrored: Whether the label is mirrored.

    Returns:
        A detached ``AltiumSchSheetName`` ready to be attached through
        ``sheet_symbol.set_sheet_name(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)
    if "\n" in text or "\r" in text:
        raise ValueError("sheet-name labels are single-line only")

    label = AltiumSchSheetName()
    label.location = location_mils.to_coord_point()
    label.text = text
    label.font = font
    label.color = _normalize_optional_color_value("color", color)
    label.orientation = validated_orientation
    label.justification = validated_justification
    label.is_mirrored = mirrored
    return label


def make_sch_file_name(
    *,
    text: str,
    location_mils: SchPointMils,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10, italic=True),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    justification: TextJustification = TextJustification.BOTTOM_LEFT,
    mirrored: bool = False,
) -> AltiumSchFileName:
    """
    Construct a detached file-name label owned by a sheet symbol.

    Args:
        text: Child schematic filename text.
        location_mils: Label anchor location in mils.
        font: Public font description resolved when the label is added to a
            document or library.
        color: Optional text color helper.
        orientation: Text rotation in 90-degree increments.
        justification: Text anchor justification relative to ``location_mils``.
        mirrored: Whether the label is mirrored.

    Returns:
        A detached ``AltiumSchFileName`` ready to be attached through
        ``sheet_symbol.set_file_name(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)
    if "\n" in text or "\r" in text:
        raise ValueError("file-name labels are single-line only")

    label = AltiumSchFileName()
    label.location = location_mils.to_coord_point()
    label.text = text
    label.font = font
    label.color = _normalize_optional_color_value("color", color)
    label.orientation = validated_orientation
    label.justification = validated_justification
    label.is_mirrored = mirrored
    return label


def make_sch_bus(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.MEDIUM,
) -> AltiumSchBus:
    """
    Construct a detached schematic bus path.

    Args:
        points_mils: Ordered bus path points in mils. At least two points are
            required.
        color: Optional bus color helper. ``None`` leaves the native default
            bus color behavior.
        line_width: Bus line thickness enum.

    Returns:
        A detached ``AltiumSchBus`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=2,
    )
    validated_line_width = _validate_line_width_enum(line_width)

    bus = AltiumSchBus()
    bus.points_mils = list(validated_points)
    bus.color = _normalize_optional_color_value("color", color)
    bus.line_width = validated_line_width
    return bus


def make_sch_bus_entry(
    *,
    start_mils: SchPointMils,
    end_mils: SchPointMils,
    color: ColorValue | None = None,
    line_width: LineWidth = LineWidth.SMALLEST,
) -> AltiumSchBusEntry:
    """
    Construct a detached schematic bus entry segment.

    Args:
        start_mils: Start point in mils.
        end_mils: End point in mils.
        color: Optional bus-entry color helper. ``None`` leaves the native
            default bus-entry color behavior.
        line_width: Bus-entry line thickness enum.

    Returns:
        A detached ``AltiumSchBusEntry`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(start_mils, SchPointMils):
        raise TypeError("start_mils must be a SchPointMils value")
    if not isinstance(end_mils, SchPointMils):
        raise TypeError("end_mils must be a SchPointMils value")
    validated_line_width = _validate_line_width_enum(line_width)

    entry = AltiumSchBusEntry()
    entry.location_mils = start_mils
    entry.corner_mils = end_mils
    entry.color = _normalize_optional_color_value("color", color)
    entry.line_width = validated_line_width
    return entry


def make_sch_harness_connector(
    *,
    bounds_mils: SchRectMils,
    side: SchHarnessConnectorSide = SchHarnessConnectorSide.RIGHT,
    primary_position_mils: float | None = None,
    border_width: LineWidth = LineWidth.SMALL,
    border_color: ColorValue | None = _DEFAULT_HARNESS_CONNECTOR_BORDER,
    fill_color: ColorValue | None = _DEFAULT_HARNESS_CONNECTOR_FILL,
) -> AltiumSchHarnessConnector:
    """
    Construct a detached schematic harness connector.

    Args:
        bounds_mils: Connector body rectangle in mils.
        side: Native harness-connector side enum.
        primary_position_mils: Brace connection position measured along the
            connector edge in mils. For left/right connectors this is the
            distance from the top. For top/bottom connectors it is the
            distance from the left.
        border_width: Connector border thickness enum.
        border_color: Optional connector border color helper.
        fill_color: Optional connector fill color helper.

    Returns:
        A detached ``AltiumSchHarnessConnector`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(bounds_mils, SchRectMils):
        raise TypeError("bounds_mils must be a SchRectMils value")
    validated_side = _validate_harness_connector_side(side)
    validated_border_width = _validate_line_width_enum(border_width)
    left_mils = min(bounds_mils.x1_mils, bounds_mils.x2_mils)
    right_mils = max(bounds_mils.x1_mils, bounds_mils.x2_mils)
    top_mils = max(bounds_mils.y1_mils, bounds_mils.y2_mils)
    bottom_mils = min(bounds_mils.y1_mils, bounds_mils.y2_mils)
    width_mils = right_mils - left_mils
    height_mils = top_mils - bottom_mils
    if width_mils <= 0 or height_mils <= 0:
        raise ValueError("bounds_mils must have positive width and height")

    axis_length_mils = (
        width_mils
        if validated_side
        in (SchHarnessConnectorSide.TOP, SchHarnessConnectorSide.BOTTOM)
        else height_mils
    )
    if primary_position_mils is None:
        validated_primary_position_mils = axis_length_mils / 2.0
    else:
        validated_primary_position_mils = _validate_non_negative_public_float(
            "primary_position_mils", primary_position_mils
        )
    if validated_primary_position_mils > axis_length_mils:
        raise ValueError(
            "primary_position_mils must stay within the selected connector edge"
        )

    connector = AltiumSchHarnessConnector()
    connector.location = SchPointMils.from_mils(left_mils, top_mils).to_coord_point()
    connector.xsize = int(round(width_mils / 10.0))
    connector.ysize = int(round(height_mils / 10.0))
    connector.side = validated_side
    connector.primary_connection_position = int(
        round(validated_primary_position_mils / 10.0)
    )
    connector.line_width = validated_border_width
    connector.color = _normalize_optional_color_value("border_color", border_color)
    connector.area_color = _normalize_optional_color_value("fill_color", fill_color)
    return connector


def make_sch_harness_entry(
    *,
    name: str,
    distance_from_top_mils: float,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    border_color: ColorValue | None = _DEFAULT_HARNESS_ENTRY_BORDER,
    fill_color: ColorValue | None = _DEFAULT_HARNESS_ENTRY_FILL,
    text_color: ColorValue | None = None,
    text_style: BusTextStyle = BusTextStyle.FULL,
    harness_type: str = "",
    side: SchHarnessConnectorSide | None = None,
) -> AltiumSchHarnessEntry:
    """
    Construct a detached schematic harness entry.

    Args:
        name: Entry display name.
        distance_from_top_mils: Entry offset along the connector edge in mils.
        font: Public font description resolved when the entry is added to a
            document or library.
        border_color: Optional entry border color helper.
        fill_color: Optional entry fill color helper.
        text_color: Optional entry text color helper.
        text_style: Native bus/harness text display style enum.
        harness_type: Optional native harness-type association string.
        side: Optional explicit entry side enum. When omitted, the native
            entry-side default remains in place.

    Returns:
        A detached ``AltiumSchHarnessEntry`` ready to be attached through
        ``connector.add_entry(...)``.
    """
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_distance_mils = _validate_non_negative_public_float(
        "distance_from_top_mils", distance_from_top_mils
    )
    validated_text_style = _validate_bus_text_style(text_style)
    validated_side = None if side is None else _validate_harness_connector_side(side)
    if not isinstance(harness_type, str):
        raise TypeError("harness_type must be a string")

    entry = AltiumSchHarnessEntry()
    entry.name = name
    entry.distance_from_top_mils = validated_distance_mils
    entry.font = font
    entry.color = _normalize_optional_color_value("border_color", border_color) or 0
    entry.area_color = (
        _normalize_optional_color_value("fill_color", fill_color) or 0xFFFFFF
    )
    entry.text_color = _normalize_optional_color_value("text_color", text_color) or 0
    entry.text_style = validated_text_style
    entry.harness_type = harness_type
    if validated_side is not None:
        entry.side = validated_side.value
    return entry


def make_sch_harness_type(
    *,
    text: str,
    location_mils: SchPointMils,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    justification: TextJustification = TextJustification.CENTER_CENTER,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
) -> AltiumSchHarnessType:
    """
    Construct a detached schematic harness type label.

    Args:
        text: Type-label text.
        location_mils: Absolute label anchor location in mils.
        font: Public font description resolved when the label is added to a
            document or library.
        color: Optional text color helper.
        justification: Native text justification enum.
        orientation: Native text orientation enum.

    Returns:
        A detached ``AltiumSchHarnessType`` ready to be attached through
        ``connector.set_type_label(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)

    harness_type = AltiumSchHarnessType()
    harness_type.text = text
    harness_type.location = location_mils.to_coord_point()
    harness_type.font = font
    harness_type.color = _normalize_optional_color_value("color", color)
    harness_type.justification = validated_justification
    harness_type.orientation = validated_orientation
    harness_type.not_auto_position = True
    return harness_type


def make_sch_signal_harness(
    *,
    points_mils: list[SchPointMils],
    color: ColorValue | None = _DEFAULT_SIGNAL_HARNESS_COLOR,
    line_width: LineWidth = LineWidth.MEDIUM,
) -> AltiumSchSignalHarness:
    """
    Construct a detached signal-harness polyline.

    Args:
        points_mils: Harness path points in mils.
        color: Optional harness color helper.
        line_width: Harness line thickness enum.

    Returns:
        A detached ``AltiumSchSignalHarness`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    validated_points = _validate_path_points_mils(
        "points_mils",
        points_mils,
        minimum_count=2,
    )
    validated_line_width = _validate_line_width_enum(line_width)

    harness = AltiumSchSignalHarness()
    harness.points_mils = list(validated_points)
    harness.color = _normalize_optional_color_value("color", color)
    harness.line_width = validated_line_width
    return harness


def make_sch_text_string(
    *,
    location_mils: SchPointMils,
    text: str,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    justification: TextJustification = TextJustification.BOTTOM_LEFT,
    mirrored: bool = False,
    url: str = "",
) -> AltiumSchLabel:
    """
    Construct a detached schematic text string using public-facing parameters.

    Args:
        location_mils: Text anchor location in mils.
        text: Single-line text content. Use a text frame or note for multiline
            text.
        font: Public font description resolved when the text string is added to
            a document or library.
        color: Optional text color helper. ``None`` leaves the native default
            color behavior.
        orientation: Text rotation in 90-degree increments.
        justification: Text anchor justification relative to ``location_mils``.
        mirrored: Whether the text string is mirrored.
        url: Optional URL string stored on the text string object.

    Returns:
        A detached ``AltiumSchLabel`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)
    if "\n" in text or "\r" in text:
        raise ValueError(
            "text strings are single-line only; use text frame or note for multiline text"
        )

    label = AltiumSchLabel()
    label.location = location_mils.to_coord_point()
    label.text = text
    label.font = font
    label.color = _normalize_optional_color_value("color", color)
    label.orientation = validated_orientation
    label.justification = validated_justification
    label.is_mirrored = mirrored
    label.url = url
    return label


def make_sch_net_label(
    *,
    location_mils: SchPointMils,
    text: str,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    justification: TextJustification = TextJustification.BOTTOM_LEFT,
    mirrored: bool = False,
) -> AltiumSchNetLabel:
    """
    Construct a detached schematic net label using public-facing parameters.

    Args:
        location_mils: Net-label anchor location in mils.
        text: Single-line net name text.
        font: Public font description resolved when the net label is added to a
            document or library.
        color: Optional text color helper. ``None`` leaves the native default
            net-label color behavior.
        orientation: Net-label rotation in 90-degree increments.
        justification: Text anchor justification relative to ``location_mils``.
        mirrored: Whether the net label is mirrored.

    Returns:
        A detached ``AltiumSchNetLabel`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)
    if "\n" in text or "\r" in text:
        raise ValueError("net labels are single-line only")

    label = AltiumSchNetLabel()
    label.location_mils = location_mils
    label.text = text
    label.font = font
    label.color = _normalize_optional_color_value("color", color)
    label.orientation = validated_orientation
    label.justification = validated_justification
    label.is_mirrored = mirrored
    return label


def make_sch_off_sheet_connector(
    *,
    location_mils: SchPointMils,
    text: str,
    style: OffSheetConnectorStyle = OffSheetConnectorStyle.LEFT,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    show_net_name: bool = True,
) -> AltiumSchCrossSheetConnector:
    """
    Construct a detached schematic off-sheet connector.

    Args:
        location_mils: Off-sheet connector anchor location in mils.
        text: Connector name shown next to the symbol.
        style: Built-in off-sheet connector direction enum.
        font: Public font description resolved when the connector is added to a
            document or library.
        color: Optional symbol/text color helper. ``None`` leaves the native
            default off-sheet connector color behavior.
        orientation: Connector orientation in 90-degree increments.
        show_net_name: Whether the connector name is shown.

    Returns:
        A detached ``AltiumSchCrossSheetConnector`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        This public surface targets Altium's native built-in left/right
        off-sheet connector styles.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_style = _validate_off_sheet_connector_style(style)
    validated_orientation = _validate_text_orientation(orientation)
    if not isinstance(show_net_name, bool):
        raise TypeError("show_net_name must be a bool")

    connector = AltiumSchCrossSheetConnector()
    connector.location_mils = location_mils
    connector.text = text
    connector.font = font
    connector.color = _normalize_optional_color_value("color", color)
    connector.orientation = validated_orientation
    connector.show_net_name = show_net_name
    connector.is_cross_sheet_connector = True
    connector.style = validated_style
    return connector


def make_sch_port(
    *,
    location_mils: SchPointMils,
    name: str,
    width_mils: int = 500,
    height_mils: int = 100,
    io_type: PortIOType = PortIOType.UNSPECIFIED,
    style: PortStyle = PortStyle.NONE_HORIZONTAL,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    border_color: ColorValue | None = None,
    fill_color: ColorValue | None = None,
    text_color: ColorValue | None = None,
    alignment: SchHorizontalAlign = SchHorizontalAlign.LEFT,
    border_width: LineWidth = LineWidth.SMALLEST,
    auto_size: bool = True,
    show_net_name: bool = True,
    harness_type: str = "",
) -> AltiumSchPort:
    """
    Construct a detached schematic hierarchical port using public-facing units.

    Args:
        location_mils: Port anchor location in mils.
        name: Port name shown on the port body.
        width_mils: Port body length in mils. Port width is stored natively in
            10-mil units and is rounded to the nearest native unit.
        height_mils: Port body thickness in mils. Port height is stored natively
            in 10-mil units and is rounded to the nearest native unit.
        io_type: Port I/O direction enum.
        style: Native port style enum.
        font: Public font description resolved when the port is added to a
            document or library.
        border_color: Optional border color helper. ``None`` leaves the native
            default border color behavior.
        fill_color: Optional fill color helper. ``None`` leaves the native
            default fill color behavior.
        text_color: Optional text color helper. ``None`` leaves the native
            default text color behavior.
        alignment: Port text alignment enum.
        border_width: Port border thickness enum. ``LineWidth.SMALLEST`` matches
            the native default field-omission behavior.
        auto_size: Whether Altium may auto-size the port.
        show_net_name: Whether the port name is shown.
        harness_type: Optional harness type string. Ordinary hierarchical ports
            should leave this empty.

    Returns:
        A detached ``AltiumSchPort`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        This factory targets ordinary named ports. Cross-reference display mode
        and custom object-definition-backed port graphics are intentionally out
        of scope for this public surface.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_width_mils = _validate_non_negative_public_mils("width_mils", width_mils)
    validated_height_mils = _validate_non_negative_public_mils(
        "height_mils", height_mils
    )
    if validated_width_mils <= 0:
        raise ValueError("width_mils must be greater than zero")
    if validated_height_mils <= 0:
        raise ValueError("height_mils must be greater than zero")
    validated_io_type = _validate_port_io_type(io_type)
    validated_style = _validate_port_style(style)
    if not isinstance(alignment, SchHorizontalAlign):
        raise TypeError("alignment must be a SchHorizontalAlign value")
    validated_border_width = _validate_line_width_enum(border_width)
    if not isinstance(auto_size, bool):
        raise TypeError("auto_size must be a bool")
    if not isinstance(show_net_name, bool):
        raise TypeError("show_net_name must be a bool")
    if not isinstance(harness_type, str):
        raise TypeError("harness_type must be a string")

    port = AltiumSchPort()
    port.location_mils = location_mils
    port.name = name
    port.width_mils = validated_width_mils
    port.height_mils = validated_height_mils
    port.io_type = validated_io_type
    port.style = validated_style
    port.font = font
    port.color = _normalize_optional_color_value("border_color", border_color)
    port.area_color = _normalize_optional_color_value("fill_color", fill_color)
    port.text_color = _normalize_optional_color_value("text_color", text_color) or 0
    port.alignment = alignment
    port.border_width = validated_border_width
    port.auto_size = auto_size
    port.show_net_name = show_net_name
    port.harness_type = harness_type
    return port


def make_sch_power_port(
    *,
    location_mils: SchPointMils,
    text: str,
    style: PowerObjectStyle = PowerObjectStyle.BAR,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    show_net_name: bool | None = None,
) -> AltiumSchPowerPort:
    """
    Construct a detached schematic power port using built-in Altium styles.

    Args:
        location_mils: Power-port anchor location in mils.
        text: Net name displayed and connected by the power port.
        style: Built-in power-port style enum.
        font: Public font description resolved when the power port is added to
            a document or library.
        color: Optional symbol/text color helper. ``None`` leaves the native
            default power-port color behavior.
        orientation: Power-port orientation in 90-degree increments.
        show_net_name: Optional explicit net-name visibility override. When
            ``None``, the native style-based default is used, which hides names
            for ground-style symbols.

    Returns:
        A detached ``AltiumSchPowerPort`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        This public factory currently targets ordinary built-in power-port
        symbols such as bar, arrow, wave, and ground variants. Advanced custom
        power-port graphics backed by ``ObjectDefinitionId`` are intentionally
        out of scope for this surface.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    validated_style = _validate_power_object_style(style)
    validated_orientation = _validate_text_orientation(orientation)
    if show_net_name is not None and not isinstance(show_net_name, bool):
        raise TypeError("show_net_name must be a bool or None")

    power_port = AltiumSchPowerPort()
    power_port.location_mils = location_mils
    power_port.text = text
    power_port.style = validated_style
    power_port.font = font
    power_port.color = _normalize_optional_color_value("color", color)
    power_port.orientation = validated_orientation
    power_port.show_net_name = (
        AltiumSchPowerPort._default_show_net_name(validated_style)
        if show_net_name is None
        else show_net_name
    )
    return power_port


def make_sch_parameter(
    *,
    location_mils: SchPointMils,
    name: str,
    text: str,
    font: SchFontSpec = SchFontSpec(name="Arial", size=10),
    color: ColorValue | None = None,
    orientation: TextOrientation = TextOrientation.DEGREES_0,
    justification: TextJustification = TextJustification.BOTTOM_LEFT,
    show_name: bool = False,
    hidden: bool = False,
) -> AltiumSchParameter:
    """
    Construct a detached schematic parameter record.

    Args:
        location_mils: Parameter anchor location in mils.
        name: Parameter name string.
        text: Parameter value string.
        font: Public font description resolved when the parameter is added to a
            document or library.
        color: Optional text color helper. ``None`` leaves the native default
            color behavior.
        orientation: Text rotation in 90-degree increments.
        justification: Text anchor justification relative to ``location_mils``.
        show_name: Whether the rendered text includes the parameter name.
        hidden: Whether the parameter is hidden.

    Returns:
        A detached ``AltiumSchParameter`` ready to be added with
        ``schdoc.add_object(...)``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(font, SchFontSpec):
        raise TypeError("font must be a SchFontSpec value")
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(show_name, bool):
        raise TypeError("show_name must be a bool")
    if not isinstance(hidden, bool):
        raise TypeError("hidden must be a bool")
    validated_orientation = _validate_text_orientation(orientation)
    validated_justification = _validate_text_justification(justification)

    parameter = AltiumSchParameter()
    parameter.location_mils = location_mils
    parameter.name = name
    parameter.text = text
    parameter.font = font
    parameter.color = _normalize_optional_color_value("color", color)
    parameter.orientation = validated_orientation
    parameter.justification = validated_justification
    parameter.show_name = show_name
    parameter.is_hidden = hidden
    return parameter


def make_sch_parameter_set(
    *,
    location_mils: SchPointMils,
    name: str = "Parameter Set",
    orientation: Rotation90 = Rotation90.DEG_0,
    style: ParameterSetStyle = ParameterSetStyle.LARGE,
    color: ColorValue = ColorValue.from_hex("#FF0000"),
) -> AltiumSchParameterSet:
    """
    Construct a detached schematic parameter-set directive container.

    Args:
        location_mils: Absolute directive origin in mils.
        name: Visible parameter-set label string.
        orientation: Native 90-degree orientation enum.
        style: Native parameter-set display style enum.
        color: Directive color helper. Defaults to Altium's native directive
            red.

    Returns:
        A detached ``AltiumSchParameterSet`` ready to be added with
        ``schdoc.add_object(...)``.

    Notes:
        Parameter sets can own child parameter records. Add those child
        parameters separately with ``schdoc.add_object(parameter, owner=set)``.
        Differential-pair directives are a parameter-set variant: add a hidden
        child parameter with ``name="DifferentialPair"`` and ``text="True"``.
    """
    if not isinstance(location_mils, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    validated_orientation = _validate_rotation90_enum(orientation)
    validated_style = _validate_parameter_set_style(style)
    if not isinstance(color, ColorValue):
        raise TypeError("color must be a ColorValue value")

    parameter_set = AltiumSchParameterSet()
    parameter_set.location_mils = location_mils
    parameter_set.name = name
    parameter_set.orientation = validated_orientation
    parameter_set.style = validated_style
    parameter_set.color = color.win32
    return parameter_set
