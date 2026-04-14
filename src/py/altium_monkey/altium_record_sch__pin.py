"""Schematic record model for SchRecordType.PIN."""

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from struct import pack, unpack
from typing import Any

from .altium_sch_enums import (
    IEEE_SYMBOL_NAMES,
    PIN_ELECTRICAL_NAMES,
    ROTATION_NAMES,
    IeeeSymbol,
    PinElectrical,
    PinItemMode,
    PinOrientation,
    PinTextAnchor,
    PinTextRotation,
    Rotation90,
    StdLogicState,
    SymbolLineWidth,
)
from .altium_record_types import (
    CoordPoint,
    SchPrimitive,
    SchRecordType,
    color_to_hex,
    parse_bool,
    rgb_to_win32_color,
)
from .altium_sch_svg_renderer import (
    PIN_LINE_WIDTH,
    SchSvgRenderContext,
    render_text_with_overline,
    svg_ellipse,
    svg_group,
    svg_line,
    svg_path,
    svg_polygon,
    svg_rect,
    svg_text_or_poly,
)
from .altium_serializer import process_mbcs_string
from .altium_text_metrics import measure_text_width
from .altium_ttf_metrics import get_font_factor


def _get_case_insensitive(
    record: dict[str, object],
    key: str,
    default: object | None = None,
) -> object | None:
    """
    Get value from record with case-insensitive key lookup.

        Altium files may have UPPERCASE keys (older exports) or MixedCase keys
        (newer exports). This function tries both variants.

        Args:
           record: Dictionary to search
            key: Key in MixedCase format (e.g., 'Location.X')
            default: Default value if key not found

        Returns:
            Value from record or default
    """
    # Try exact match first (most common)
    if key in record:
        return record[key]
    # Try uppercase variant
    upper_key = key.upper()
    if upper_key in record:
        return record[upper_key]
    return default


def _has_case_insensitive(record: dict, key: str) -> bool:
    """
    Check if key exists in record with case-insensitive lookup.
    """
    return key in record or key.upper() in record


def _read_swap_id_part_and_pin(record: dict[str, Any]) -> tuple[str, bool]:
    """
    Read the pin swap-part mapping using Altium's MBCS string rules.
    """
    for key in ("%UTF8%SwapIDPart", "SwapIDPart", "SwapIdPartAndPartPin"):
        if _has_case_insensitive(record, key):
            value = str(_get_case_insensitive(record, key))
            return process_mbcs_string(value), True
    return "|&|", False


def _normalize_native_pin_text(text: str, *, native_svg_export: bool) -> str:
    """
    Match native SVG's spaced-minus normalization without rewriting lone '-' pins.
    """
    if not native_svg_export or "-" not in text:
        return text
    return re.sub(r"(?<=\s)-(?=\s|$)", "\u2212", text)


# =============================================================================
# Electrical Type Glyph Constants
# =============================================================================
# Arrow dimensions in mils (1 mil = 1 SVG pixel at standard scale)
ARROW_LENGTH = 6  # Distance from arrow tip to base
ARROW_HALF_HEIGHT = 2  # Half-height of arrow (+/-2 from center)
BIDIR_GAP = 1  # Gap between bidirectional arrows
BIDIR_TOTAL_LENGTH = 13  # Total length of bidirectional symbol

# Symbol fill color (surface/background color from Altium)
SYMBOL_FILL_COLOR = "#F8FCFF"


# =============================================================================
# IEEE Symbol Constants
# =============================================================================
# All dimensions are in mils (1 mil = 1 SVG pixel at standard scale).
# The offsets below position each symbol family relative to the pin body end.

# Inner symbol base offset from body_end (toward inside of symbol)
INNER_SYMBOL_OFFSET = 4  # Distance from body_end into symbol body

# Inner edge symbol - at the body edge (no offset)
INNER_EDGE_OFFSET = 0

# Outer edge symbol offset from hot_spot
OUTER_EDGE_DOT_OFFSET = 3  # Dot radius

# Schmitt trigger dimensions (larger symbol)
SCHMITT_WIDTH = 16
SCHMITT_HEIGHT = 8

# Pulse symbol dimensions
PULSE_STEP_WIDTH = 4
PULSE_HEIGHT = 4

# Internal pull resistor dimensions
PULL_RESISTOR_WIDTH = 6
PULL_RESISTOR_HEIGHT = 10

# Symbol line stroke (hairline with non-scaling)
SYMBOL_STROKE_WIDTH = 0.5

# =============================================================================
# Inner Symbol Text Offset Widths
# =============================================================================
# When an inner symbol is present, the pin name text must be pushed further
# into the component body to make room for the symbol. These widths are the
# additional offset applied to the margin.
#
# Representative widths:
#   - SCHMITT: extends 16 mils from body_end (cx-14 to cx+2)
#   - PULSE: extends 12 mils from body_end (cx-10 to cx+2)
#   - INTERNAL_PULL_*: extends 6 mils from body_end
#   - Others (diamond, triangle shapes): ~4-8 mils
# IEEE Symbol text offsets (in mils)
# These push the pin NAME text further from the body edge.
# Values are tuned to the current SVG contract.
INNER_SYMBOL_TEXT_OFFSETS = {
    IeeeSymbol.SCHMITT: 16,
    IeeeSymbol.PULSE: 12,
    IeeeSymbol.INTERNAL_PULL_UP: 6,
    IeeeSymbol.INTERNAL_PULL_DOWN: 6,
    IeeeSymbol.SHIFT_LEFT: 6,
    IeeeSymbol.POSTPONED_OUTPUT: 4,
    IeeeSymbol.OPEN_COLLECTOR: 4,
    IeeeSymbol.OPEN_COLLECTOR_PULL_UP: 4,
    IeeeSymbol.OPEN_EMITTER: 4,
    IeeeSymbol.OPEN_EMITTER_PULL_UP: 4,
    IeeeSymbol.OPEN_CIRCUIT_OUTPUT: 4,
    IeeeSymbol.HIZ: 4,
    IeeeSymbol.HIGH_CURRENT: 4,
    # Note: DOT and CLOCK are NOT valid at symbol_inner per Altium UI
    # Note: ACTIVE_LOW_INPUT is at outer_edge, not inner
}

# Inner edge symbols (only CLOCK is valid here per Altium UI)
INNER_EDGE_TEXT_OFFSETS = {
    IeeeSymbol.DOT: 1,
    IeeeSymbol.CLOCK: 2,
}

# =============================================================================
# IEEE Symbol Y Offsets (for bounds expansion)
# =============================================================================
# When IEEE symbols extend beyond the default +/-3 pixel vertical bounds,
# the innerBounds rectangle expands and the vertical center shifts.
# This affects designator text Y positioning for horizontal pins (DEG_0, DEG_180).
#
# Default innerBounds: +/-300000 DXP (+/-3 pixels) from pin line
#
# These offsets are subtracted from the designator Y position.
INNER_SYMBOL_Y_OFFSETS = {
    IeeeSymbol.PULSE: 0.0,
}


# =============================================================================
# Pin Text Measurement & Rendering
# =============================================================================
#
# Pin text has TWO different font sizes for different purposes:
#
# 1. MEASUREMENT SIZE (for text width calculation):
#    - Uses the PT size directly (e.g., 10pt -> measure at 10px)
#    - TTF metrics provide 0.00% error vs GDI+ at any size
#    - This matches what Altium uses internally for positioning
#
# 2. RENDERING SIZE (for SVG font-size attribute):
#    - Uses font-specific conversion factors:
#      * Arial: pt * 0.8 (10pt -> 8px)
#      * Times New Roman: pt * 0.9 (10pt -> 9px)
#      * Default: pt * 8/9 (~0.889)
#    - Uses FLOAT values (not int-truncated) to avoid right-aligned text gaps
#
# =============================================================================
# =============================================================================
# These flags control how text position is adjusted after initial placement.
# Text-alignment flags for the designator positioning helpers.
TEXT_ALIGN_NONE = 0  # NoUpdateCp - no offset applied
TEXT_ALIGN_RIGHT = 2  # X -= text_width
TEXT_ALIGN_CENTER = 6  # X -= text_width / 2
TEXT_ALIGN_BOTTOM = 8  # Y += font_height

# Perpendicular offset in mils.
# Applied only when text is perpendicular to the pin.
DESIGNATOR_PERPENDICULAR_OFFSET = 2  # 200000 internal units = 2 mils


def calculate_designator_text_alignment(
    orientation: "Rotation90", is_perpendicular: bool
) -> int:
    """
    Calculate TextAlignment flags for pin designator placement.

    Truth table:
    | Orientation | Perpendicular | Bottom | Right | Notes |
    |-------------|---------------|--------|-------|-------|
    | DEG_0       | False         | Yes    | No    | Aligned with pin |
    | DEG_0       | True          | No     | No    | Perpendicular |
    | DEG_90      | False         | Yes    | Yes   | Aligned with pin |
    | DEG_90      | True          | Yes    | No    | Perpendicular |
    | DEG_180     | False         | Yes    | Yes   | Aligned with pin |
    | DEG_180     | True          | No     | No    | Perpendicular |
    | DEG_270     | False         | Yes    | Yes   | Aligned with pin |
    | DEG_270     | True          | No     | No    | Perpendicular |

    Args:
        orientation: Pin orientation (DEG_0, DEG_90, DEG_180, DEG_270)
        is_perpendicular: True if text rotation differs from pin alignment

    Returns:
        TextAlignment flags (combination of TEXT_ALIGN_BOTTOM and TEXT_ALIGN_RIGHT)
    """
    # Import here to avoid circular dependency
    from .altium_sch_enums import Rotation90

    # Determine if pin is vertical
    pin_is_vertical = orientation in (Rotation90.DEG_90, Rotation90.DEG_270)

    # First clause: Apply Bottom unless perpendicular AND (DEG_0 or DEG_270)
    # (!flag || (orient != eRotate0 && orient != eRotate270)) ? Bottom : NoUpdateCp
    if is_perpendicular and orientation in (Rotation90.DEG_0, Rotation90.DEG_270):
        alignment = TEXT_ALIGN_NONE
    else:
        alignment = TEXT_ALIGN_BOTTOM

    # Second clause: Apply Right based on complex conditions
    # ((flag && !IsRotationVertical(orient)) ? NoUpdateCp
    #  : ((flag || (orient != eRotate0 && orient != eRotate90)) ? Right : NoUpdateCp))
    if is_perpendicular and not pin_is_vertical:
        # Perpendicular text on horizontal pin: NoUpdateCp (no Right)
        pass  # Right not added
    elif is_perpendicular or orientation not in (Rotation90.DEG_0, Rotation90.DEG_90):
        # Either perpendicular, OR not DEG_0/DEG_90 -> add Right
        alignment |= TEXT_ALIGN_RIGHT

    return alignment


def apply_text_alignment_offset(
    x: float, y: float, text_width: float, font_height: float, alignment: int
) -> tuple[float, float]:
    """
    Apply TextAlignment offset to text position.

    Args:
        x: Initial X position
        y: Initial Y position
        text_width: Measured text width (from MeasureString)
        font_height: Measured font height (from MeasureString)
        alignment: TextAlignment flags

    Returns:
        Adjusted (x, y) position tuple
    """
    x_offset = 0.0
    y_offset = 0.0

    # Horizontal alignment
    if (alignment & TEXT_ALIGN_CENTER) == TEXT_ALIGN_CENTER:
        x_offset = text_width / 2.0
    elif (alignment & TEXT_ALIGN_RIGHT) == TEXT_ALIGN_RIGHT:
        x_offset = text_width

    # Vertical alignment
    if (alignment & TEXT_ALIGN_BOTTOM) == TEXT_ALIGN_BOTTOM:
        y_offset = font_height

    return (x - x_offset, y + y_offset)


@dataclass
class PinTextSettings:
    """
    Custom text settings for pin name or designator.

    Stored in PinTextData stream when custom settings are used.

    Font resolution:
    - font_id: Resolved numeric ID (from parsing or after FontIDManager resolution)
    - font_name/font_size: Unresolved font spec (from constructor, resolved at build time)

    At save time, if font_id is None but font_name is set, the owning schematic
    library resolves the font spec to a numeric ID via
    FontIDManager.get_or_create_font().

    Note: font_id and position_margin default to None to avoid serializing
    default values. When None, the system default is used (font_id=1, margin=0).
    """

    font_mode: PinItemMode = PinItemMode.DEFAULT
    font_id: int | None = (
        None  # Resolved font table index (1-based), None = use default (1)
    )
    font_name: str | None = (
        None  # Unresolved font name (e.g., "Arial"), resolved at build time
    )
    font_size: int | None = (
        None  # Unresolved font size in points, resolved at build time
    )
    font_bold: bool = False  # Font bold flag for resolution
    font_italic: bool = False  # Font italic flag for resolution
    color: int = 0x000000  # Win32 BGR format
    position_mode: PinItemMode = PinItemMode.DEFAULT
    position_margin: int | None = (
        None  # Internal units (100000 = 1 mil), None = use default (0)
    )
    position_margin_frac: int | None = (
        None  # Fractional sub-10000 precision, None = not present
    )
    rotation: Rotation90 = Rotation90.DEG_0
    rotation_anchor: PinTextAnchor = PinTextAnchor.PIN


@dataclass(frozen=True)
class _PinTextRenderSpec:
    text: str
    display_text: str
    clean_text: str
    font_family: str
    font_size_px: float
    font_size: float
    line_height: float
    is_bold: bool
    is_italic: bool
    font_weight: str | None
    font_style: str | None
    fill: str
    text_width: float
    margin: float
    rotated: bool
    settings: PinTextSettings | None


def _validate_pin_text_rotation(rotation: PinTextRotation | int | None) -> int | None:
    """
    Validate and normalize pin text rotation value.

    Args:
        rotation: Rotation value (PinTextRotation enum, int 0/90, or None)

    Returns:
        Normalized rotation (0, 90, or None)

    Raises:
        ValueError: If rotation is not 0, 90, or None
    """
    if rotation is None:
        return None
    if isinstance(rotation, PinTextRotation):
        return rotation.value
    if rotation in (0, 90):
        return rotation
    raise ValueError(f"Pin text rotation must be 0 or 90 degrees, got {rotation}")


def _resolve_pin_location_state(
    *,
    designator: str | None,
    name: str | None,
    x: int | None,
    y: int | None,
    orientation: int | PinOrientation,
    length: int,
) -> tuple[bool, CoordPoint, int, Rotation90]:
    is_user_mode = designator is not None and name is not None
    if is_user_mode and x is not None and y is not None:
        return (
            is_user_mode,
            CoordPoint(x // 10, y // 10),
            length // 10,
            Rotation90(int(orientation) & 0x03),
        )
    return is_user_mode, CoordPoint(), 30, Rotation90.DEG_0


def _resolve_pin_electrical_type(
    electrical_type: int | PinElectrical,
    *,
    is_user_mode: bool,
) -> PinElectrical:
    if isinstance(electrical_type, PinElectrical):
        return electrical_type
    if is_user_mode:
        return PinElectrical(electrical_type)
    return PinElectrical.PASSIVE


def _resolve_owner_part_id(owner_part_id: int | None, designator: str | None) -> int:
    if owner_part_id is not None:
        return owner_part_id
    if designator is not None:
        return 1
    return -1


def _apply_constructor_text_settings(
    settings: PinTextSettings,
    *,
    font_name: str | None,
    font_size: int | None,
    font_bold: bool,
    font_italic: bool,
    rotation: int | None,
    margin_mils: float | None,
    color: int | None,
    reference_to_component: bool,
) -> float | None:
    has_font = any(
        (font_name is not None, font_size is not None, font_bold, font_italic)
    )
    has_position = (
        margin_mils is not None
        or (rotation is not None and rotation != 0)
        or reference_to_component
    )
    if has_font:
        settings.font_mode = PinItemMode.CUSTOM
        settings.font_name = font_name
        settings.font_size = font_size
        settings.font_bold = font_bold
        settings.font_italic = font_italic
    if color is not None:
        settings.color = color
        if not has_font:
            settings.font_mode = PinItemMode.CUSTOM
    if has_position:
        settings.position_mode = PinItemMode.CUSTOM
        if rotation is not None:
            settings.rotation = (
                Rotation90(rotation // 90) if rotation else Rotation90.DEG_0
            )
        if reference_to_component:
            settings.rotation_anchor = PinTextAnchor.COMPONENT
    return margin_mils


def _parse_pin_visibility_flags(
    record: dict[str, Any], pin_conglomerate: int
) -> tuple[bool, bool, bool, bool]:
    def _flag(field_name: str, bit_mask: int) -> bool:
        if _has_case_insensitive(record, field_name):
            return parse_bool(_get_case_insensitive(record, field_name))
        return (pin_conglomerate & bit_mask) != 0

    return (
        _flag("IsHidden", 0x04),
        _flag("ShowName", 0x08),
        _flag("ShowDesignator", 0x10),
        _flag("IsNotAccessible", 0x20),
    )


def _parse_pin_symbol_fields(
    record: dict[str, Any],
) -> tuple[IeeeSymbol, IeeeSymbol, IeeeSymbol, IeeeSymbol, SymbolLineWidth]:
    symbol_line_width = SymbolLineWidth.ZERO
    symbol_lw = record.get("SymBol_LineWidth", 0)
    if symbol_lw is not None:
        symbol_line_width = SymbolLineWidth(int(symbol_lw))
    return (
        IeeeSymbol(int(record.get("SymBol_Inner", 0))),
        IeeeSymbol(int(record.get("SymBol_Outer", 0))),
        IeeeSymbol(int(record.get("SymBol_InnerEdge", 0))),
        IeeeSymbol(int(record.get("SymBol_OuterEdge", 0))),
        symbol_line_width,
    )


def _translate_pin_font_id(font_manager: Any, raw_font_id: int) -> int:
    return font_manager.translate_in(raw_font_id) if font_manager else raw_font_id


def _apply_pin_text_position_conglomerate(
    settings: PinTextSettings,
    *,
    value: int | None,
) -> None:
    if value is None:
        return
    if value & 0x01:
        settings.position_mode = PinItemMode.CUSTOM
        settings.rotation_anchor = PinTextAnchor((value >> 1) & 0x01)
        settings.rotation = Rotation90((value >> 2) & 0x03)
    if value & 0x10:
        settings.font_mode = PinItemMode.CUSTOM


def _read_optional_pin_text_value(
    record: dict[str, Any],
    *field_names: str,
) -> Any:
    for field_name in field_names:
        if _has_case_insensitive(record, field_name):
            value = _get_case_insensitive(record, field_name)
            if isinstance(value, str):
                try:
                    return int(value)
                except ValueError:
                    return value
            return value
    return None


def _parse_pin_text_settings_from_record(
    record: dict[str, Any],
    *,
    settings: PinTextSettings,
    prefix: str,
    font_manager: Any,
) -> None:
    legacy_prefix = f"{prefix}_"
    _apply_pin_text_position_conglomerate(
        settings,
        value=_read_optional_pin_text_value(
            record, f"Pin{prefix}_PositionConglomerate"
        ),
    )

    font_mode = _read_optional_pin_text_value(record, f"{prefix}FontMode")
    if font_mode is not None:
        settings.font_mode = PinItemMode(int(font_mode))
    position_mode = _read_optional_pin_text_value(record, f"{prefix}PositionMode")
    if position_mode is not None:
        settings.position_mode = PinItemMode(int(position_mode))
    rotation_relative = _read_optional_pin_text_value(
        record, f"{prefix}CustomRotationRelative"
    )
    if rotation_relative is not None:
        settings.rotation = Rotation90(int(rotation_relative))
    rotation_anchor = _read_optional_pin_text_value(
        record, f"{prefix}CustomRotationAnchor"
    )
    if rotation_anchor is not None:
        settings.rotation_anchor = PinTextAnchor(int(rotation_anchor))

    position_margin = _read_optional_pin_text_value(
        record,
        f"{legacy_prefix}CustomPosition_Margin",
        f"{prefix}CustomPositionMargin",
    )
    if position_margin is not None:
        settings.position_margin = int(position_margin)
    position_margin_frac = _read_optional_pin_text_value(
        record,
        f"{legacy_prefix}CustomPosition_Margin_Frac",
        f"{prefix}CustomPositionMarginFrac",
    )
    if position_margin_frac is not None:
        settings.position_margin_frac = int(position_margin_frac)
    font_id = _read_optional_pin_text_value(
        record,
        f"{legacy_prefix}CustomFontID",
        f"{prefix}CustomFontID",
    )
    if font_id is not None:
        settings.font_id = _translate_pin_font_id(font_manager, int(font_id))
    color = _read_optional_pin_text_value(
        record,
        f"{legacy_prefix}CustomColor",
        f"{prefix}CustomColor",
    )
    if color is not None:
        settings.color = int(color)


def _cache_pin_text_margin(
    settings: PinTextSettings,
) -> tuple[float | None, float | None]:
    if settings.position_margin is None:
        return None, None
    margin_frac = settings.position_margin_frac or 0
    return (
        settings.position_margin + margin_frac / 100000.0,
        settings.position_margin * 10 + margin_frac / 10000.0,
    )


def _write_pin_text_settings(
    record: dict[str, Any],
    *,
    prefix: str,
    settings: PinTextSettings,
    margin_mils: float | None,
) -> None:
    conglomerate = 0
    if settings.position_mode == PinItemMode.CUSTOM:
        conglomerate |= 0x01
        conglomerate |= settings.rotation_anchor.value << 1
        conglomerate |= settings.rotation.value << 2
        if margin_mils is not None:
            internal_coord = int(round(margin_mils * 10000))
            margin_whole = internal_coord // 100000
            margin_frac = internal_coord % 100000
            record[f"{prefix}_CustomPosition_Margin"] = str(margin_whole)
            if margin_frac != 0:
                record[f"{prefix}_CustomPosition_Margin_Frac"] = str(margin_frac)
    if settings.font_mode == PinItemMode.CUSTOM:
        conglomerate |= 0x10
        if settings.font_id is not None:
            record[f"{prefix}_CustomFontID"] = str(settings.font_id)
        if settings.color is not None:
            record[f"{prefix}_CustomColor"] = str(settings.color)
    if conglomerate != 0:
        record[f"Pin{prefix}_PositionConglomerate"] = str(conglomerate)


class AltiumSchPin(SchPrimitive):
    """
    PIN record.

    Represents a component pin with electrical properties, position,
    and optional IEEE symbol decorations.

    This class supports two construction modes:

    1. **User-friendly constructor**:
       Pass designator, name, position, and optional styling parameters.
       pin = AltiumSchPin("1", "VCC", 0, 0)
       pin = AltiumSchPin("1", "VCC", 0, 0, name_font="Arial", name_margin_mils=150)

    2. **Parsing mode** (internal):
       Call with no arguments, then populate via parse_from_record() or binary parsing.
       pin = AltiumSchPin()
       pin.parse_from_record(record_dict)

    When any styling parameter is provided (name_font, name_color, margin, etc.),
    a PinTextData stream will be generated in the SchLib file to store these settings.

    See Also:
        make_sch_pin(): Public high-level factory using SchPointMils, SchFontSpec,
            and ColorValue boundary types.
        AltiumSchLib.add_symbol(): Canonical container-owned symbol authoring path.
    """

    def __init__(
        self,
        designator: str | None = None,
        name: str | None = None,
        x: int | None = None,
        y: int | None = None,
        *,  # All remaining args are keyword-only
        orientation: int | PinOrientation = 0,
        length: int = 300,
        electrical_type: int | PinElectrical = PinElectrical.INPUT,
        pin_color: int = 0x000000,
        hidden: bool = False,
        name_visible: bool = True,
        designator_visible: bool = True,
        owner_part_id: int | None = None,
        owner_part_display_mode: int = 0,
        # Custom font/style settings - None means no customization
        name_font: str | None = None,
        name_font_size: int | None = None,
        name_font_bold: bool = False,
        name_font_italic: bool = False,
        name_rotation: PinTextRotation | int | None = None,
        name_margin_mils: float | None = None,
        name_color: int | None = None,
        name_reference_to_component: bool = False,
        designator_font: str | None = None,
        designator_font_size: int | None = None,
        designator_font_bold: bool = False,
        designator_font_italic: bool = False,
        designator_rotation: PinTextRotation | int | None = None,
        designator_margin_mils: float | None = None,
        designator_color: int | None = None,
        designator_reference_to_component: bool = False,
        # Advanced settings
        description: str = "",
        swap_id_pin: str = "",
        swap_id_part: str = "",
        swap_id_sequence: str = "",
        default_value: str = "",
        # IEEE symbols
        symbol_inner: IeeeSymbol | int = IeeeSymbol.NONE,
        symbol_outer: IeeeSymbol | int = IeeeSymbol.NONE,
        symbol_inner_edge: IeeeSymbol | int = IeeeSymbol.NONE,
        symbol_outer_edge: IeeeSymbol | int = IeeeSymbol.NONE,
    ) -> None:
        """
        Create a new AltiumSchPin.

        This is the record-level constructor. Public authoring examples should
        prefer make_sch_pin(...) so coordinates, fonts, and colors use the
        higher-level helper types.

        When called with no arguments, creates a blank pin for parsing mode.

        Args:
            designator: Pin number/designator shown on schematic (e.g., "1", "A1", "VCC").
                This is the electrical identifier used in netlists.
            name: Pin name describing function (e.g., "VCC", "DATA0", "CLK").
                Shown adjacent to pin unless name_visible=False.
            x: X position in mils (1 mil = 0.001 inch = 0.0254 mm).
            y: Y position in mils. Pins are typically spaced 100 mils apart.
            orientation: Pin orientation (0=Right, 1=Up, 2=Left, 3=Down).
            length: Pin length in mils (default 300).
            electrical_type: Electrical type (PinElectrical enum or int 0-7).
            pin_color: Pin line color in Win32 BGR format (0x00BBGGRR).
            hidden: Hide entire pin on schematic.
            name_visible: Show pin name text.
            designator_visible: Show pin designator text.
            owner_part_id: Part ID for multi-part symbols (default 1).
            owner_part_display_mode: Display mode variant.
            name_font: Font family for name text. None = no customization.
            name_font_size: Font size for name text. None = no customization.
            name_rotation: Name text rotation (0 or 90 degrees, or PinTextRotation enum).
            name_margin_mils: Name margin/offset in mils from default position.
            name_color: Name text color in Win32 BGR format.
            name_reference_to_component: Name position relative to component (not pin).
            designator_font: Font family for designator text.
            designator_font_size: Font size for designator text.
            designator_rotation: Designator text rotation (0 or 90 degrees).
            designator_margin_mils: Designator margin/offset in mils.
            designator_color: Designator text color in Win32 BGR format.
            designator_reference_to_component: Designator position relative to component.
            description: Pin description (for documentation).
            swap_id_pin: Pin swap group identifier.
            swap_id_part: Swap ID part identifier.
            swap_id_sequence: Swap ID sequence.
            default_value: Default logic value for simulation.
            symbol_inner: IEEE symbol inside component body.
            symbol_outer: IEEE symbol outside component body.
            symbol_inner_edge: IEEE symbol at inner edge (e.g., clock).
            symbol_outer_edge: IEEE symbol at outer edge (e.g., negation dot).

        Raises:
            ValueError: If name_rotation or designator_rotation is not 0 or 90.

            Create a pin with custom font styling:

            Create a pin with custom text positioning:

            Create a clock input with IEEE symbol:
        """
        super().__init__()

        # Validate rotation values early
        name_rotation = _validate_pin_text_rotation(name_rotation)
        designator_rotation = _validate_pin_text_rotation(designator_rotation)

        is_user_mode, self.location, self._length, self.orientation = (
            _resolve_pin_location_state(
                designator=designator,
                name=name,
                x=x,
                y=y,
                orientation=orientation,
                length=length,
            )
        )

        # Fractional coordinate precision (legacy Altium text format)
        # These fields store sub-10000 precision for coordinates/lengths
        # When None, the field is not written to output (not present in original)
        self.location_x_frac: int | None = None
        self.location_y_frac: int | None = None
        self.pin_length_frac: int | None = None

        # Computed length in mils (combines length + pin_length_frac)
        # Set at parse time, used by length_mils property
        # Compute _length_mils once at parse time.
        self._length_mils: float = self._length * 10.0

        # Identification
        self.designator = str(designator) if designator is not None else "0"
        self.name = str(name) if name is not None else "0"
        self.description = description

        # Electrical properties
        self.electrical = _resolve_pin_electrical_type(
            electrical_type,
            is_user_mode=is_user_mode,
        )
        self.formal_type: StdLogicState = StdLogicState.FORCING_UNKNOWN
        self.default_value = default_value

        # Visibility flags
        self.is_hidden = hidden
        self.show_name = name_visible
        self.show_designator = designator_visible
        self.is_not_accessible: bool = False

        # Appearance
        self.color = pin_color
        self.symbol_line_width: SymbolLineWidth = SymbolLineWidth.ZERO

        # IEEE symbol decorations
        self.symbol_inner = (
            IeeeSymbol(symbol_inner) if isinstance(symbol_inner, int) else symbol_inner
        )
        self.symbol_outer = (
            IeeeSymbol(symbol_outer) if isinstance(symbol_outer, int) else symbol_outer
        )
        self.symbol_inner_edge = (
            IeeeSymbol(symbol_inner_edge)
            if isinstance(symbol_inner_edge, int)
            else symbol_inner_edge
        )
        self.symbol_outer_edge = (
            IeeeSymbol(symbol_outer_edge)
            if isinstance(symbol_outer_edge, int)
            else symbol_outer_edge
        )

        # Pin swapping
        self.swap_id_pin = swap_id_pin
        self.swap_id_pair: str = ""
        if swap_id_sequence:
            self.swap_id_part_and_pin = f"{swap_id_part}|&|{swap_id_sequence}"
        else:
            self.swap_id_part_and_pin = swap_id_part if swap_id_part else "|&|"
        self._has_swap_id_part: bool = (
            False  # Track if SwapIDPart was present in original
        )

        # Advanced properties
        self.pin_package_length: int = 0
        self.propagation_delay: float = 0.0
        self.hidden_net_name: str = ""

        # Owner tracking
        self.owner_index: int = 0
        self.owner_index_additional_list: bool = False
        self.owner_part_id = _resolve_owner_part_id(owner_part_id, designator)
        self.owner_part_display_mode = owner_part_display_mode

        # Text customization (used by PinTextData stream)
        # Store all styling directly in settings, not in duplicate private attrs.
        self.name_settings = PinTextSettings()
        self.designator_settings = PinTextSettings()

        # Multi-function pin support
        self.defined_functions: list[str] = []
        self.selected_functions: list[str] = []
        self.hide_name_as_function: bool = False
        self.symbolic_name: str = ""
        self.show_symbolic_name_as_function: bool = False

        # Connection tracking (SchDoc only)
        self.connected_object_id: str = ""

        # Raw binary data
        self._raw_binary: bytes | None = None

        # === Custom styling ===
        # Store all styling in PinTextSettings - no private attrs for font/color/rotation

        # Computed margin values in mils (used by SVG renderer, serialization)
        # These remain as private attrs since they're computed values, not font specs
        self._name_margin_mils: float | None = name_margin_mils
        self._designator_margin_mils: float | None = designator_margin_mils

        self._name_margin_mils = _apply_constructor_text_settings(
            self.name_settings,
            font_name=name_font,
            font_size=name_font_size,
            font_bold=name_font_bold,
            font_italic=name_font_italic,
            rotation=name_rotation,
            margin_mils=name_margin_mils,
            color=name_color,
            reference_to_component=name_reference_to_component,
        )
        self._designator_margin_mils = _apply_constructor_text_settings(
            self.designator_settings,
            font_name=designator_font,
            font_size=designator_font_size,
            font_bold=designator_font_bold,
            font_italic=designator_font_italic,
            rotation=designator_rotation,
            margin_mils=designator_margin_mils,
            color=designator_color,
            reference_to_component=designator_reference_to_component,
        )

        # The legacy _needs_pintextdata and _use_custom_fonts flags were removed.
        # Use the needs_pintextdata property instead, which computes the same value

    @property
    def record_type(self) -> SchRecordType:
        return SchRecordType.PIN

    @property
    def x_mils(self) -> float:
        """
        X position in mils.
        """
        return self.location.x * 10.0

    @property
    def y_mils(self) -> float:
        """
        Y position in mils.
        """
        return self.location.y * 10.0

    @property
    def length_mils(self) -> float:
        """
        Pin length in mils.

                Value is computed once at parse time from the whole and fractional
                pin-length fields.
        """
        return self._length_mils

    @property
    def length(self) -> int:
        """
        Pin length in 10-mil units (internal Altium units).

                Setting this property also updates _length_mils to keep them in sync.
        """
        return self._length

    @length.setter
    def length(self, value: int) -> None:
        """
        Set pin length and sync _length_mils.

                The _length_mils value will be set to value * 10.0 (whole mils only).
                If fractional precision is needed, _length_mils can be set directly after.
        """
        self._length = value
        self._length_mils = value * 10.0

    @property
    def name_margin_mils(self) -> float | None:
        """
        Name margin in mils, or None if using default.

                Value is computed once at parse time from position_margin + position_margin_frac.
                For SchLib pins, set from PinTextData stream.
                For SchDoc pins, set from Name_CustomPosition_Margin fields.
        """
        return self._name_margin_mils

    @property
    def designator_margin_mils(self) -> float | None:
        """
        Designator margin in mils, or None if using default.

                Value is computed once at parse time from position_margin + position_margin_frac.
                For SchLib pins, set from PinTextData stream.
                For SchDoc pins, set from Designator_CustomPosition_Margin fields.
        """
        return self._designator_margin_mils

    @property
    def needs_custom_name_pintextdata(self) -> bool:
        """
        Check if name needs custom PinTextData entry.

                This is the canonical detection path for name-side PinTextData.
        """
        return (
            self.name_settings.font_mode == PinItemMode.CUSTOM
            or self.name_settings.position_mode == PinItemMode.CUSTOM
        )

    @property
    def needs_custom_designator_pintextdata(self) -> bool:
        """
        Check if designator needs custom PinTextData entry.

                This is the canonical detection path for designator-side PinTextData.
        """
        return (
            self.designator_settings.font_mode == PinItemMode.CUSTOM
            or self.designator_settings.position_mode == PinItemMode.CUSTOM
        )

    @property
    def needs_pintextdata(self) -> bool:
        """
        Check if this pin needs any PinTextData entry (name or designator).

                This is the canonical detection path for whether any PinTextData is
                needed for the pin.
        """
        return (
            self.needs_custom_name_pintextdata
            or self.needs_custom_designator_pintextdata
        )

    @property
    def electrical_name(self) -> str:
        """
        Human-readable electrical type name.
        """
        return PIN_ELECTRICAL_NAMES.get(self.electrical, f"Unknown({self.electrical})")

    @property
    def orientation_name(self) -> str:
        """
        Human-readable orientation name.
        """
        return ROTATION_NAMES.get(self.orientation, f"Unknown({self.orientation})")

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse PIN from record dictionary.

        PIN records can be binary (__BINARY_DATA__) or text-based.
        Most PIN records in SchLib files are binary.

        Args:
           record: Source record dictionary
            font_manager: Optional FontIDManager for font ID translation
        """
        super().parse_from_record(record)
        self._font_manager = font_manager

        # Check if this is a binary record
        if "__BINARY_DATA__" in record:
            self._source_is_binary = True
            self._parse_binary(record["__BINARY_DATA__"])
        else:
            self._source_is_binary = False
            self._parse_text(record)

    def _parse_text(self, record: dict[str, Any]) -> None:
        """
        Parse PIN from text record (rare, but possible in some files).

                Note: Altium files may have UPPERCASE keys (older exports) or MixedCase
                keys (newer exports). All lookups use case-insensitive helpers.
        """
        # Location - use case-insensitive lookup for LOCATION.X vs Location.X
        self.location = CoordPoint(
            int(_get_case_insensitive(record, "Location.X", 0)),
            int(_get_case_insensitive(record, "Location.Y", 0)),
        )

        # Fractional precision (legacy Altium format, sub-10000 precision)
        # Only set if present in original record (use None = not present)
        if _has_case_insensitive(record, "Location.X_Frac"):
            frac_val = int(_get_case_insensitive(record, "Location.X_Frac", 0))
            self.location_x_frac = frac_val
            self.location.x_frac = frac_val
        if _has_case_insensitive(record, "Location.Y_Frac"):
            frac_val = int(_get_case_insensitive(record, "Location.Y_Frac", 0))
            self.location_y_frac = frac_val
            self.location.y_frac = frac_val
        if _has_case_insensitive(record, "PinLength_Frac"):
            self.pin_length_frac = int(
                _get_case_insensitive(record, "PinLength_Frac", 0)
            )

        # Basic properties
        # Note: Altium omits Name/Designator fields when they're empty strings.
        # Default to empty string when parsing files, not '0' like the constructor.
        self.designator = _get_case_insensitive(record, "Designator", "")
        self.name = _get_case_insensitive(record, "Name", "")
        self.description = _get_case_insensitive(record, "Description", "")

        # Length - default to 0 because Altium omits PinLength field when length=0
        self.length = int(_get_case_insensitive(record, "PinLength", 0))

        # Compute _length_mils at parse time from the whole and fractional fields.
        # Formula: length is in 10-mil units, pin_length_frac is in DXP units (1/10000 mil)
        # _length_mils = length * 10 + pin_length_frac / 10000.0
        frac = self.pin_length_frac or 0
        self._length_mils = self.length * 10 + frac / 10000.0

        # Orientation - can come from 'Orientation' field OR from PinConglomerate bits 0-1
        # In SchDoc text records, orientation is stored in PinConglomerate, not a separate field
        if _has_case_insensitive(record, "Orientation"):
            self.orientation = Rotation90(
                int(_get_case_insensitive(record, "Orientation")) & 0x03
            )
        else:
            # Extract from PinConglomerate (bits 0-1)
            pin_conglomerate = int(_get_case_insensitive(record, "PinConglomerate", 0))
            self.orientation = Rotation90(pin_conglomerate & 0x03)

        # Electrical type
        self.electrical = PinElectrical(
            int(_get_case_insensitive(record, "Electrical", 0))
        )  # Default is INPUT (0)

        # FormalType native JSON field
        if _has_case_insensitive(record, "FormalType"):
            self.formal_type = StdLogicState(
                int(_get_case_insensitive(record, "FormalType"))
            )

        pin_conglomerate = int(_get_case_insensitive(record, "PinConglomerate", 0))
        (
            self.is_hidden,
            self.show_name,
            self.show_designator,
            self.is_not_accessible,
        ) = _parse_pin_visibility_flags(record, pin_conglomerate)

        # Color
        self.color = int(_get_case_insensitive(record, "Color", 0))

        (
            self.symbol_inner,
            self.symbol_outer,
            self.symbol_inner_edge,
            self.symbol_outer_edge,
            self.symbol_line_width,
        ) = _parse_pin_symbol_fields(self._record)

        # Owner tracking
        if _has_case_insensitive(record, "OwnerIndex"):
            self.owner_index = int(_get_case_insensitive(record, "OwnerIndex"))
        if _has_case_insensitive(record, "OwnerIndexForSaveAdditionalList"):
            self.owner_index_additional_list = parse_bool(
                _get_case_insensitive(record, "OwnerIndexForSaveAdditionalList")
            )

        # Swap IDs
        # SwapIdPin is for individual pin ID
        self.swap_id_pin = _get_case_insensitive(record, "SwapIdPin", "")
        self.swap_id_pair = _get_case_insensitive(record, "SwapIdPair", "")
        # SwapIDPart stores part/sequence mapping (e.g., "|&|" or "part|&|seq").
        # Native import reads this through MBCS processing, so escaped pipe
        # sentinels such as 0xA6 must become literal separators before binary
        # SchLib serialization.
        self.swap_id_part_and_pin, self._has_swap_id_part = _read_swap_id_part_and_pin(
            record
        )

        _parse_pin_text_settings_from_record(
            record,
            settings=self.name_settings,
            prefix="Name",
            font_manager=self._font_manager,
        )
        _parse_pin_text_settings_from_record(
            record,
            settings=self.designator_settings,
            prefix="Designator",
            font_manager=self._font_manager,
        )

        # Compute margin values and position conglomerates when custom settings detected
        # The legacy _needs_pintextdata/_use_custom_fonts flags were removed.
        # Use the needs_pintextdata property instead
        name_has_custom = (
            self.name_settings.position_mode == PinItemMode.CUSTOM
            or self.name_settings.font_mode == PinItemMode.CUSTOM
        )
        des_has_custom = (
            self.designator_settings.position_mode == PinItemMode.CUSTOM
            or self.designator_settings.font_mode == PinItemMode.CUSTOM
        )
        if name_has_custom or des_has_custom:
            (
                self._name_custom_position_margin,
                self._name_margin_mils,
            ) = _cache_pin_text_margin(self.name_settings)
            (
                self._designator_custom_position_margin,
                self._designator_margin_mils,
            ) = _cache_pin_text_margin(self.designator_settings)

    def _parse_binary(self, binary_data: bytes) -> None:
        """
        Parse PIN from binary data.

        This is the primary parsing method for SchLib PIN records.
        Binary format based on native pin export format.
        """
        if len(binary_data) < 30:
            raise ValueError(f"PIN binary data too short: {len(binary_data)} bytes")

        self._raw_binary = binary_data
        cursor = 0

        # Byte 0: RECORD instruction (single byte, should be 0x02)
        record_type = binary_data[cursor]
        if record_type != SchRecordType.PIN:
            raise ValueError(f"Invalid PIN record type: {record_type}")
        cursor += 1

        # Bytes 1-4: OwnerIndex (int32 LE)
        (self.owner_index,) = unpack("<I", binary_data[cursor : cursor + 4])
        cursor += 4

        # Bytes 5-6: OwnerPartId (int16 LE)
        (self.owner_part_id,) = unpack("<h", binary_data[cursor : cursor + 2])
        cursor += 2

        # Byte 7: OwnerPartDisplayMode
        self.owner_part_display_mode = binary_data[cursor]
        cursor += 1

        # Bytes 8-11: Symbol decorations (4 bytes)
        # Order from Altium: InnerEdge, OuterEdge, Inside, Outside
        self.symbol_inner_edge = IeeeSymbol(binary_data[cursor])
        self.symbol_outer_edge = IeeeSymbol(binary_data[cursor + 1])
        self.symbol_inner = IeeeSymbol(binary_data[cursor + 2])
        self.symbol_outer = IeeeSymbol(binary_data[cursor + 3])
        cursor += 4

        # Description (Pascal string)
        desc_len = binary_data[cursor]
        cursor += 1
        self.description = binary_data[cursor : cursor + desc_len].decode(
            "iso-8859-1", errors="replace"
        )
        cursor += desc_len

        # FormalType (was incorrectly skipped as "unknown byte 0x01")
        self.formal_type = StdLogicState(binary_data[cursor])
        cursor += 1

        # Electrical type byte
        self.electrical = PinElectrical(binary_data[cursor] & 0x0F)
        cursor += 1

        # PinConglomerate (orientation + visibility + flags)
        conglomerate = binary_data[cursor]
        self.orientation = Rotation90(conglomerate & 0x03)
        self.is_hidden = (conglomerate & 0x04) != 0
        self.show_name = (conglomerate & 0x08) != 0
        self.show_designator = (conglomerate & 0x10) != 0
        self.is_not_accessible = (conglomerate & 0x20) != 0
        self.graphically_locked = (conglomerate & 0x40) != 0
        self.owner_index_additional_list = (conglomerate & 0x80) != 0
        cursor += 1

        # Pin length (int16 LE, units of 10mil)
        (self.length,) = unpack("<h", binary_data[cursor : cursor + 2])
        cursor += 2

        # Compute the precombined length in mils at parse time.
        # Binary format has no frac field - length is stored as int16 in 10-mil units
        self._length_mils = self.length * 10.0

        # X, Y coordinates (int16 LE each, units of 10mil)
        x, y = unpack("<hh", binary_data[cursor : cursor + 4])
        self.location = CoordPoint(x, y)
        cursor += 4

        # Color (Win32 format - int32 LE)
        (self.color,) = unpack("<I", binary_data[cursor : cursor + 4])
        cursor += 4

        # Name (Pascal string)
        # Use cp1252 (Windows-1252) as Altium is a Windows application
        name_len = binary_data[cursor]
        cursor += 1
        self.name = binary_data[cursor : cursor + name_len].decode(
            "cp1252", errors="replace"
        )
        cursor += name_len

        # Designator (Pascal string)
        desig_len = binary_data[cursor]
        cursor += 1
        self.designator = binary_data[cursor : cursor + desig_len].decode(
            "cp1252", errors="replace"
        )
        cursor += desig_len

        # SwapIdPin (Pascal string)
        swap_len = binary_data[cursor]
        cursor += 1
        self.swap_id_pin = binary_data[cursor : cursor + swap_len].decode(
            "cp1252", errors="replace"
        )
        cursor += swap_len

        # SwapIDPart (Pascal string) - format: "{part}|&|{sequence}"
        part_seq_len = binary_data[cursor]
        cursor += 1
        self.swap_id_part_and_pin = binary_data[cursor : cursor + part_seq_len].decode(
            "cp1252", errors="replace"
        )
        cursor += part_seq_len

        # DefaultValue (Pascal string)
        default_len = binary_data[cursor]
        cursor += 1
        self.default_value = binary_data[cursor : cursor + default_len].decode(
            "iso-8859-1", errors="replace"
        )
        cursor += default_len

        # Store cursor position for potential extended data parsing
        self._parsed_cursor = cursor

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize PIN to record dictionary.

        Returns appropriate format based on source:
        - Binary format (SchLib): dictionary with __BINARY_DATA__
        - Text format (SchDoc): dictionary with key-value pairs
        """
        # Use text format if source was text (e.g., SchDoc files)
        if not getattr(self, "_source_is_binary", True):
            return self._serialize_text()

        # Default to binary format (SchLib)
        binary_data = self._serialize_binary()
        return {
            "RECORD": str(self.record_type.value),
            "__BINARY_RECORD__": True,
            "__BINARY_DATA__": binary_data,
        }

    def _serialize_text(self) -> dict[str, Any]:
        """
        Serialize PIN to text-format record dictionary (for SchDoc files).

        Text format is used in SchDoc files where PINs are stored as
        key-value pairs rather than binary records.
        """
        record = {
            "RECORD": str(self.record_type.value),
            "OwnerIndex": str(self.owner_index),
        }
        self._serialize_text_header_fields(record)
        self._serialize_text_geometry_fields(record)
        self._serialize_text_identity_fields(record)
        self._serialize_text_symbol_fields(record)
        self._serialize_text_swap_and_metadata(record)
        self._serialize_text_visibility_fields(record)
        self._serialize_text_settings_fields(record)
        return record

    def _serialize_text_header_fields(self, record: dict[str, Any]) -> None:
        if self.owner_part_id is not None:
            record["OwnerPartId"] = str(self.owner_part_id)
        if self.formal_type is not None and self.formal_type.value != 0:
            record["FormalType"] = str(self.formal_type.value)
        record["PinConglomerate"] = str(self._text_pin_conglomerate())

    def _text_pin_conglomerate(self) -> int:
        return (
            (self.orientation.value & 0x03)
            | (0x04 if self.is_hidden else 0)
            | (0x08 if self.show_name else 0)
            | (0x10 if self.show_designator else 0)
            | (0x20 if self.is_not_accessible else 0)
            | (0x40 if self.graphically_locked else 0)
            | (0x80 if self.owner_index_additional_list else 0)
        )

    def _serialize_text_geometry_fields(self, record: dict[str, Any]) -> None:
        self._serialize_text_length_fields(record)
        record["Location.X"] = str(self.location.x)
        x_frac = self.location.x_frac or (
            self.location_x_frac if self.location_x_frac is not None else 0
        )
        if x_frac != 0:
            record["Location.X_Frac"] = str(x_frac)
        record["Location.Y"] = str(self.location.y)
        y_frac = self.location.y_frac or (
            self.location_y_frac if self.location_y_frac is not None else 0
        )
        if y_frac != 0:
            record["Location.Y_Frac"] = str(y_frac)

    def _serialize_text_length_fields(self, record: dict[str, Any]) -> None:
        if self._length_mils == 0:
            return
        internal_coord = int(round(self._length_mils * 10000))
        length_whole = internal_coord // 100000
        length_frac = internal_coord % 100000
        if length_whole != 0:
            record["PinLength"] = str(length_whole)
        if length_frac != 0:
            record["PinLength_Frac"] = str(length_frac)

    def _serialize_text_identity_fields(self, record: dict[str, Any]) -> None:
        record["Name"] = self.name
        record["Designator"] = self.designator
        record["Color"] = str(self.color)
        record["Electrical"] = str(self.electrical.value)

    def _serialize_text_symbol_fields(self, record: dict[str, Any]) -> None:
        if self.symbol_inner_edge.value != 0:
            record["SymBol_InnerEdge"] = str(self.symbol_inner_edge.value)
        if self.symbol_outer_edge.value != 0:
            record["SymBol_OuterEdge"] = str(self.symbol_outer_edge.value)
        if self.symbol_inner.value != 0:
            record["SymBol_Inner"] = str(self.symbol_inner.value)
        if self.symbol_outer.value != 0:
            record["SymBol_Outer"] = str(self.symbol_outer.value)
        if self.symbol_line_width.value != 0:
            record["SymBol_LineWidth"] = str(self.symbol_line_width.value)

    def _serialize_text_swap_and_metadata(self, record: dict[str, Any]) -> None:
        if self.swap_id_pin:
            record["SwapIdPin"] = self.swap_id_pin
        if self._has_swap_id_part or (
            self.swap_id_part_and_pin and self.swap_id_part_and_pin != "|&|"
        ):
            record["SwapIDPart"] = self.swap_id_part_and_pin
        if self.description:
            record["Description"] = self.description
        if self.unique_id:
            record["UniqueID"] = self.unique_id

    def _serialize_text_visibility_fields(self, record: dict[str, Any]) -> None:
        record["Orientation"] = str(self.orientation.value)
        record["ShowName"] = "true" if self.show_name else "false"
        record["ShowDesignator"] = "true" if self.show_designator else "false"
        record["IsHidden"] = "true" if self.is_hidden else "false"

    def _serialize_text_settings_fields(self, record: dict[str, Any]) -> None:
        _write_pin_text_settings(
            record,
            prefix="Name",
            settings=self.name_settings,
            margin_mils=self._name_margin_mils,
        )
        _write_pin_text_settings(
            record,
            prefix="Designator",
            settings=self.designator_settings,
            margin_mils=self._designator_margin_mils,
        )

    def _serialize_binary(self) -> bytes:
        """
        Serialize PIN to binary format.

        Binary format based on native pin export format.
        """
        data = bytearray()

        # Byte 0: RECORD instruction (single byte)
        data.append(0x02)

        # Bytes 1-4: OwnerIndex (int32 LE)
        data.extend(pack("<I", self.owner_index))

        # Bytes 5-6: OwnerPartId (int16 LE)
        owner_part = self.owner_part_id if self.owner_part_id is not None else -1
        data.extend(pack("<h", owner_part))

        # Byte 7: OwnerPartDisplayMode
        display_mode = (
            self.owner_part_display_mode
            if self.owner_part_display_mode is not None
            else 0
        )
        data.append(display_mode)

        # Bytes 8-11: Symbol decorations (4 bytes)
        data.append(self.symbol_inner_edge.value)
        data.append(self.symbol_outer_edge.value)
        data.append(self.symbol_inner.value)
        data.append(self.symbol_outer.value)

        # Description (Pascal string)
        desc_bytes = self.description.encode("iso-8859-1", errors="replace")
        data.append(len(desc_bytes))
        data.extend(desc_bytes)

        # FormalType enum value
        data.append(self.formal_type.value)

        # Electrical type
        data.append(self.electrical.value)

        # PinConglomerate
        conglomerate = (
            (self.orientation.value & 0x03)
            | (0x04 if self.is_hidden else 0)
            | (0x08 if self.show_name else 0)
            | (0x10 if self.show_designator else 0)
            | (0x20 if self.is_not_accessible else 0)
            | (0x40 if self.graphically_locked else 0)
            | (0x80 if self.owner_index_additional_list else 0)
        )
        data.append(conglomerate)

        # Pin length (int16 LE) - reconstruct from _length_mils
        # Binary format stores length in 10-mil units (no fractional component)
        length_10mil = int(round(self._length_mils / 10))
        data.extend(pack("<h", length_10mil))

        # X, Y coordinates (int16 LE each)
        data.extend(pack("<h", self.location.x))
        data.extend(pack("<h", self.location.y))

        # Color (int32 LE)
        data.extend(pack("<I", self.color))

        # Name (Pascal string)
        name_bytes = self.name.encode("iso-8859-1", errors="replace")
        data.append(len(name_bytes))
        data.extend(name_bytes)

        # Designator (Pascal string)
        desig_bytes = self.designator.encode("iso-8859-1", errors="replace")
        data.append(len(desig_bytes))
        data.extend(desig_bytes)

        # SwapIdPin (Pascal string)
        swap_bytes = self.swap_id_pin.encode("iso-8859-1", errors="replace")
        data.append(len(swap_bytes))
        data.extend(swap_bytes)

        # SwapIDPart (Pascal string) - format: "{part}|&|{sequence}"
        part_seq = self.swap_id_part_and_pin
        part_seq_bytes = part_seq.encode("iso-8859-1", errors="replace")
        data.append(len(part_seq_bytes))
        data.extend(part_seq_bytes)

        # DefaultValue (Pascal string)
        default_bytes = self.default_value.encode("iso-8859-1", errors="replace")
        data.append(len(default_bytes))
        data.extend(default_bytes)

        return bytes(data)

    def get_hot_spot(self) -> CoordPoint:
        """
        Calculate the pin's electrical connection point (hot spot).

        In Altium, the pin's location is where it attaches to the symbol body (inside).
        The hot spot is at location + length in the direction of orientation,
        which is where wires connect (outside the symbol).

        Returns:
            CoordPoint of the pin's electrical connection point (tip/hot spot)
        """
        # Preserve SchDoc sub-unit precision: location carries x_frac/y_frac and
        # pin length carries PinLength_Frac. Native SVG line endpoints include
        # this fractional information (e.g. x=392.5 + len=7.5 -> x2=400.0).
        scale = 100000
        x_total = self.location.x * scale + self.location.x_frac
        y_total = self.location.y * scale + self.location.y_frac
        length_total = self.length * scale + (self.pin_length_frac or 0)

        if self.orientation == Rotation90.DEG_0:  # Right
            x_total += length_total
        elif self.orientation == Rotation90.DEG_90:  # Up
            y_total += length_total
        elif self.orientation == Rotation90.DEG_180:  # Left
            x_total -= length_total
        elif self.orientation == Rotation90.DEG_270:  # Down
            y_total -= length_total

        return CoordPoint(
            x_total // scale,
            y_total // scale,
            x_total % scale,
            y_total % scale,
        )

    @property
    def connection_point(self) -> tuple[int, int]:
        """
        Pin connection point (wire side / hot spot) as tuple.

                Convenience property delegating to get_hot_spot(). Returns `(x, y)`
                in internal 10-mil units.
        """
        cp = self.get_hot_spot()
        return (cp.x, cp.y)

    # Alias for get_hot_spot().
    def get_end_location(self) -> CoordPoint:
        """
        Alias for get_hot_spot().
        """
        return self.get_hot_spot()

    def _get_inner_symbols_text_offset(self) -> float:
        """
        Calculate total text offset caused by inner symbols.

        When inner symbols (SCHMITT, PULSE, etc.) or inner edge symbols (CLOCK)
        are present, the pin name text must be pushed further from body_end to
        avoid overlapping the symbol graphics.

        This implements the same logic as Altium's DrawSymbols() which calculates
        innerBounds by drawing symbols and getting their bounding box, then passes
        innerBounds to CalculatePinNamePosition().

        Note: Large symbols (SCHMITT, PULSE, etc.) can be assigned to either
        inner or inner_edge positions. Either way they push text by their width.

        Returns:
            Total offset in mils to add to margin for text positioning.
        """
        offset = 0.0

        # Inner symbol offset (symbols drawn inside the body)
        if self.symbol_inner != IeeeSymbol.NONE:
            offset += INNER_SYMBOL_TEXT_OFFSETS.get(self.symbol_inner, 0)

        # Inner edge symbol offset
        # Large symbols at inner_edge also push text (SCHMITT, PULSE, etc.)
        # Small symbols like CLOCK/DOT have smaller offsets
        if self.symbol_inner_edge != IeeeSymbol.NONE:
            # First check if it's a large symbol that pushes text significantly
            if self.symbol_inner_edge in INNER_SYMBOL_TEXT_OFFSETS:
                offset += INNER_SYMBOL_TEXT_OFFSETS[self.symbol_inner_edge]
            else:
                # Small inner_edge symbols (CLOCK, DOT)
                offset += INNER_EDGE_TEXT_OFFSETS.get(self.symbol_inner_edge, 0)

        # Native innerBounds expands further when Schmitt and Clock coexist.
        if (
            self.symbol_inner == IeeeSymbol.SCHMITT
            and self.symbol_inner_edge == IeeeSymbol.CLOCK
        ):
            offset += 4

        return offset

    def get_text_y_offset(self) -> float:
        """
        Get the Y offset for designator text due to IEEE symbol bounds expansion.

        When IEEE symbols extend beyond the default +/-3 pixel vertical bounds,
        the innerBounds rectangle expands and its center shifts. This shifts
        the designator text Y position for horizontal pins (DEG_0, DEG_180).
        1. Starting with pin's OwnBoundingRectangle (+/-300000 DXP = +/-3 pixels)
        2. Drawing inner/inner_edge symbols into a geometry group
        3. Getting the actual drawn bounds
        4. Union with initial bounds to get expanded innerBounds
        5. Using innerBounds center for text Y positioning

        For horizontal pins, symbols that extend beyond +/-3 pixels vertically
        shift the center, which shifts the designator Y position.

        Returns:
            Y offset in pixels to subtract from the designator Y position.
            Positive value means the center shifted upward (smaller SVG Y).
        """
        offset = 0.0

        # Check inner symbol for vertical bounds expansion
        if self.symbol_inner != IeeeSymbol.NONE:
            offset += INNER_SYMBOL_Y_OFFSETS.get(self.symbol_inner, 0)

        # Check inner edge symbol for vertical bounds expansion
        if self.symbol_inner_edge != IeeeSymbol.NONE:
            offset += INNER_SYMBOL_Y_OFFSETS.get(self.symbol_inner_edge, 0)

        return offset

    def _get_name_inner_bounds_shift(self) -> float:
        """
        Get the axis shift used for pin-name placement when inner bounds expand.

        Native pin-name placement for custom-margin PULSE labels on SchDoc pins
        tracks a half-pixel shifted innerBounds center, while default-positioned
        pulse names remain on the legacy integer anchor. Keep this separate from
        designator centering so the default IEEE-symbol cases stay stable.
        """
        offset = self.get_text_y_offset()
        if self._name_margin_mils is None:
            return offset
        if (
            self.symbol_inner == IeeeSymbol.PULSE
            or self.symbol_inner_edge == IeeeSymbol.PULSE
        ):
            return offset + 0.5
        return offset

    @staticmethod
    def _render_pin_text(
        ctx: SchSvgRenderContext,
        x: float,
        y: float,
        text: str,
        *,
        font_size: float,
        font_family: str,
        fill: str,
        transform: str | None = None,
        font_weight: str | None = None,
        font_style: str | None = None,
        poly_target_advance: float | None = None,
    ) -> str:
        """
        Dispatch pin text to SVG text or polygon text by render options.
        """
        return svg_text_or_poly(
            ctx,
            x,
            y,
            text,
            font_size=font_size,
            font_family=font_family,
            fill=fill,
            transform=transform,
            font_weight=font_weight,
            font_style=font_style,
            poly_target_advance=poly_target_advance,
        )

    @staticmethod
    def _parse_svg_rotation(transform: str | None) -> float:
        if not transform:
            return 0.0
        match = re.search(r"rotate\(\s*([-+]?\d+(?:\.\d+)?)", transform)
        return float(match.group(1)) if match else 0.0

    @staticmethod
    def _parse_svg_numeric(value: str | None, *, default: float = 0.0) -> float:
        if value is None:
            return default
        text = str(value).strip()
        if text.endswith("px"):
            text = text[:-2]
        return float(text) if text else default

    @staticmethod
    def _parse_svg_color(value: str | None) -> int | None:
        if not value or value == "none":
            return None
        color_text = str(value).strip()
        if not color_text.startswith("#") or len(color_text) != 7:
            return None
        return rgb_to_win32_color(
            int(color_text[1:3], 16),
            int(color_text[3:5], 16),
            int(color_text[5:7], 16),
        )

    @classmethod
    def _parse_svg_arc_path(cls, d: str | None) -> dict[str, float] | None:
        if not d:
            return None
        match = re.fullmatch(
            r"\s*M\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*"
            r"A\s*"
            r"([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s+"
            r"([-+]?\d+(?:\.\d+)?)\s+"
            r"([01])\s*,\s*([01])\s+"
            r"([-+]?\d+(?:\.\d+)?)\s*,\s*([-+]?\d+(?:\.\d+)?)\s*",
            str(d),
        )
        if not match:
            return None
        return {
            "x1": float(match.group(1)),
            "y1": float(match.group(2)),
            "rx": float(match.group(3)),
            "ry": float(match.group(4)),
            "rotation": float(match.group(5)),
            "large_arc": float(match.group(6)),
            "sweep": float(match.group(7)),
            "x2": float(match.group(8)),
            "y2": float(match.group(9)),
        }

    @classmethod
    def _pen_from_svg(
        cls,
        stroke: str | None,
        stroke_width: str | None,
        *,
        units_per_px: int,
    ) -> dict[str, Any] | None:
        from .altium_sch_geometry_oracle import make_pen

        color_raw = cls._parse_svg_color(stroke)
        if color_raw is None:
            return None
        stroke_width_px = cls._parse_svg_numeric(stroke_width, default=0.0)
        if stroke_width_px <= 0.500001:
            return make_pen(color_raw, width=0)
        return make_pen(
            color_raw,
            width=int(round(stroke_width_px * units_per_px)),
        )

    def _svg_elements_to_geometry_operations(
        self,
        elements: list[str],
        ctx: SchSvgRenderContext,
        *,
        units_per_px: int = 64,
    ) -> list[Any]:
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            SchGeometryOpKind,
            make_font_payload,
            make_solid_brush,
            svg_coord_to_geometry,
        )
        from .altium_svg_arc_helpers import svg_circle_arc_center_for_flags

        sheet_height_px = float(ctx.sheet_height or 0.0)
        effective_orientation = self._get_effective_orientation(ctx)
        wrapped = "<root>" + "".join(elements) + "</root>"
        root = ET.fromstring(wrapped)
        ordered_operations: list[tuple[int, int, SchGeometryOp]] = []
        visible_text_roles: list[str] = []
        if getattr(ctx, "show_pin_names", True) and self.show_name and self.name:
            visible_text_roles.append("name")
        if (
            getattr(ctx, "show_pin_designators", True)
            and self.show_designator
            and self.designator
        ):
            visible_text_roles.append("designator")
        hot_spot_coord = self.get_hot_spot()
        pin_segment_masked = ctx.is_segment_fully_under_compile_mask(
            self.location.x,
            self.location.y,
            hot_spot_coord.x,
            hot_spot_coord.y,
        )
        text_slot = 0
        op_index = 0
        body_line_emitted = False

        def resolve_text_role_font(role: str) -> tuple[str, float, bool, bool] | None:
            if role == "name":
                name_settings = self.name_settings
                is_custom_font = (
                    name_settings is not None
                    and name_settings.font_mode == PinItemMode.CUSTOM
                    and name_settings.font_id is not None
                )
                if (
                    is_custom_font
                    and name_settings is not None
                    and name_settings.font_id is not None
                ):
                    font_id = name_settings.font_id
                    font_name, _, is_bold, is_italic, _ = ctx.get_font_info(font_id)
                    font_size_px = ctx.get_font_size_for_width(font_id)
                else:
                    font_name, font_size_px, is_bold, is_italic, _ = (
                        ctx.get_system_font_info()
                    )
                return (font_name, font_size_px, is_bold, is_italic)

            if role == "designator":
                designator_settings = self.designator_settings
                is_custom_font = (
                    designator_settings is not None
                    and designator_settings.font_mode == PinItemMode.CUSTOM
                    and designator_settings.font_id is not None
                )
                if (
                    is_custom_font
                    and designator_settings is not None
                    and designator_settings.font_id is not None
                ):
                    font_id = designator_settings.font_id
                    font_name, _, is_bold, is_italic, _ = ctx.get_font_info(font_id)
                    font_size_px = ctx.get_font_size_for_width(font_id)
                else:
                    font_name, font_size_px, is_bold, is_italic, _ = (
                        ctx.get_system_font_info()
                    )
                return (font_name, font_size_px, is_bold, is_italic)

            return None

        def text_role_is_rotated(role: str) -> bool:
            if role == "name":
                settings = self.name_settings
            elif role == "designator":
                settings = self.designator_settings
            else:
                return False

            if effective_orientation in (Rotation90.DEG_90, Rotation90.DEG_270):
                return self._vertical_pin_text_is_rotated(settings)
            elif (
                effective_orientation in (Rotation90.DEG_0, Rotation90.DEG_180)
                and settings is not None
                and settings.position_mode == PinItemMode.CUSTOM
                and settings.rotation == Rotation90.DEG_90
            ):
                return True

            return False

        def role_font_baseline_step(font_size_px: float) -> float:
            if ctx.options.truncate_font_size_for_baseline:
                return float(int(font_size_px))
            return float(font_size_px)

        def role_font_half_baseline_step(font_size_px: float) -> float:
            if ctx.options.truncate_font_size_for_baseline:
                return float(int(font_size_px / 2.0))
            return float(font_size_px) / 2.0

        def resolve_text_role_baseline_step(
            role: str,
            *,
            rotation: float,
            font_size_px: float,
            baseline_y: float,
            font_family: str,
        ) -> tuple[float, bool]:
            role_rotated = text_role_is_rotated(role)

            if role == "name" and not role_rotated:
                if effective_orientation == Rotation90.DEG_90:
                    return role_font_baseline_step(font_size_px), True
                if effective_orientation == Rotation90.DEG_270:
                    if (
                        self.name_settings is not None
                        and self.name_settings.position_mode == PinItemMode.CUSTOM
                        and self.name_settings.rotation == Rotation90.DEG_90
                    ):
                        baseline_fraction = baseline_y - math.floor(baseline_y)
                        return float(int(font_size_px) - 1) + baseline_fraction, True
                    return role_font_half_baseline_step(font_size_px), True

            if role == "designator" and not role_rotated:
                if effective_orientation == Rotation90.DEG_90:
                    if self._designator_margin_mils is None:
                        return role_font_baseline_step(font_size_px), True
                    return float(int(font_size_px)), True
                if effective_orientation == Rotation90.DEG_270:
                    return role_font_baseline_step(font_size_px), True

            if (
                role == "designator"
                and abs(rotation) > 1e-9
                and not ctx.options.truncate_font_size_for_baseline
                and self.orientation in (Rotation90.DEG_0, Rotation90.DEG_180)
            ):
                if (
                    self.designator_settings is not None
                    and self.designator_settings.position_mode == PinItemMode.CUSTOM
                    and self.designator_settings.rotation == Rotation90.DEG_90
                    and self.orientation == Rotation90.DEG_180
                ):
                    return float(int(font_size_px)), True
                return float(font_size_px), True

            baseline_step = float(int(font_size_px))
            if abs(rotation) <= 1e-9:
                baseline_fraction = baseline_y - math.floor(baseline_y)
                if font_family != "arial":
                    baseline_step -= baseline_fraction
                elif role == "name":
                    point_size_estimate = int(
                        round(font_size_px / get_font_factor("Arial"))
                    )
                    if abs(baseline_fraction - 0.5) <= 1e-9:
                        if point_size_estimate == 12:
                            baseline_step -= 0.5
                        elif pin_segment_masked:
                            baseline_step += 0.5
                    else:
                        if point_size_estimate in {5, 9} and not pin_segment_masked:
                            baseline_step -= 0.5
                        elif point_size_estimate == 13:
                            baseline_step += 0.5
            return baseline_step, False

        def append_operation(op: SchGeometryOp, *, priority: int) -> None:
            nonlocal op_index
            if (
                ordered_operations
                and op.kind_str() == "gotLines"
                and ordered_operations[-1][0] == priority
                and ordered_operations[-1][2].kind_str() == "gotLines"
            ):
                previous_priority, previous_index, previous_op = ordered_operations[-1]
                previous_pen = previous_op.payload.get("pen")
                current_pen = op.payload.get("pen")
                previous_points = previous_op.payload.get("points") or []
                current_points = op.payload.get("points") or []
                if (
                    previous_pen == current_pen
                    and previous_points
                    and current_points
                    and abs(float(previous_points[-1][0]) - float(current_points[0][0]))
                    <= 1e-6
                    and abs(float(previous_points[-1][1]) - float(current_points[0][1]))
                    <= 1e-6
                ):
                    merged_points = [
                        [float(point[0]), float(point[1])] for point in previous_points
                    ] + [
                        [float(point[0]), float(point[1])]
                        for point in current_points[1:]
                    ]
                    ordered_operations[-1] = (
                        previous_priority,
                        previous_index,
                        SchGeometryOp.lines(merged_points, pen=current_pen),
                    )
                    return
            ordered_operations.append((priority, op_index, op))
            op_index += 1

        def group_priority(group_role: str | None, *, default: int = 2) -> int:
            if group_role == "inner_symbol_group_high":
                return 4
            if group_role == "inner_symbol_group_normal":
                return 2
            if group_role == "inner_symbol":
                return 1
            return default

        def line_points_from_svg_element(svg_element: ET.Element) -> list[list[float]]:
            x1, y1 = svg_coord_to_geometry(
                self._parse_svg_numeric(svg_element.get("x1")),
                self._parse_svg_numeric(svg_element.get("y1")),
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            x2, y2 = svg_coord_to_geometry(
                self._parse_svg_numeric(svg_element.get("x2")),
                self._parse_svg_numeric(svg_element.get("y2")),
                sheet_height_px=sheet_height_px,
                units_per_px=units_per_px,
            )
            return [[x1, y1], [x2, y2]]

        def reorder_trailing_analog_symbol_lines() -> None:
            if len(ordered_operations) < 2:
                return

            candidates = ordered_operations[-2:]
            if not all(isinstance(entry[2], SchGeometryOp) for entry in candidates):
                return

            def is_symbol_line(entry: tuple[int, int, SchGeometryOp]) -> bool:
                op = entry[2]
                if op.kind != SchGeometryOpKind.LINES:
                    return False
                points = op.payload.get("points")
                pen = op.payload.get("pen")
                return (
                    isinstance(points, list)
                    and len(points) == 2
                    and isinstance(pen, dict)
                    and float(pen.get("width", 0)) == 0.0
                )

            if not all(is_symbol_line(entry) for entry in candidates):
                return

            def analog_line_sort_key(
                entry: tuple[int, int, SchGeometryOp],
            ) -> tuple[float, int]:
                op = entry[2]
                points = op.payload["points"]
                x, y = float(points[0][0]), float(points[0][1])
                if effective_orientation == Rotation90.DEG_180:
                    return (x, entry[1])
                if effective_orientation == Rotation90.DEG_90:
                    return (y, entry[1])
                if effective_orientation == Rotation90.DEG_270:
                    return (-y, entry[1])
                return (-x, entry[1])

            reordered = sorted(candidates, key=analog_line_sort_key)
            ordered_operations[-2:] = [
                (candidates[i][0], candidates[i][1], reordered[i][2])
                for i in range(len(candidates))
            ]

        def append_geometry_from_element(
            element: ET.Element, *, group_role: str | None = None
        ) -> None:
            nonlocal text_slot, body_line_emitted
            tag = element.tag.split("}", 1)[-1]

            def append_text_geometry(
                *,
                text_value: str,
                baseline_x: float,
                baseline_y: float,
                font_name_attr: str,
                font_size_attr: float,
                font_weight_attr: str | None,
                font_style_attr: str | None,
                fill_attr: str | None,
                transform_attr: str | None,
            ) -> None:
                nonlocal text_slot
                rotation = self._parse_svg_rotation(transform_attr)
                text_role = (
                    visible_text_roles[text_slot]
                    if text_slot < len(visible_text_roles)
                    else ""
                )
                text_slot += 1
                resolved_font = resolve_text_role_font(text_role)
                if resolved_font is not None:
                    font_name, font_size_px, font_is_bold, font_is_italic = (
                        resolved_font
                    )
                else:
                    font_name = font_name_attr
                    font_size_px = font_size_attr
                    font_is_bold = str(font_weight_attr or "").lower() == "bold"
                    font_is_italic = str(font_style_attr or "").lower() == "italic"
                font_family = font_name_attr.strip().lower()
                baseline_step, _ = resolve_text_role_baseline_step(
                    text_role,
                    rotation=rotation,
                    font_size_px=font_size_px,
                    baseline_y=baseline_y,
                    font_family=font_family,
                )
                if (
                    text_role == "name"
                    and self._name_margin_mils is None
                    and (
                        self.symbol_inner == IeeeSymbol.PULSE
                        or self.symbol_inner_edge == IeeeSymbol.PULSE
                    )
                ):
                    baseline_step += 0.5
                theta = math.radians(rotation)
                geometry_x_px = baseline_x + baseline_step * math.sin(theta)
                geometry_y_px = baseline_y - baseline_step * math.cos(theta)
                geometry_x, geometry_y = svg_coord_to_geometry(
                    geometry_x_px,
                    geometry_y_px,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                fill_raw = self._parse_svg_color(fill_attr) or 0
                append_operation(
                    SchGeometryOp.string(
                        x=geometry_x,
                        y=geometry_y,
                        text=text_value,
                        font=make_font_payload(
                            name=font_name,
                            size_px=font_size_px,
                            units_per_px=units_per_px,
                            rotation=rotation,
                            underline=False,
                            italic=font_is_italic,
                            bold=font_is_bold,
                            strikeout=False,
                        ),
                        brush=make_solid_brush(fill_raw),
                    ),
                    priority=4 if group_role == "inner_symbol_group_high" else 3,
                )

            if tag == "g":
                child_group_role = group_role
                group_id = str(element.get("id", "") or "")
                if group_id.endswith("_PinInnerSymbol") and (
                    self.symbol_inner != IeeeSymbol.NONE
                    or self.symbol_inner_edge != IeeeSymbol.NONE
                ):
                    has_inner_body_symbol = (
                        self.symbol_inner_edge != IeeeSymbol.NONE
                        or self.symbol_inner != IeeeSymbol.NONE
                    )
                    inner_symbol_priority = 4 if has_inner_body_symbol else 2
                    grouped_role = (
                        "inner_symbol_group_high"
                        if inner_symbol_priority == 4
                        else "inner_symbol_group_normal"
                    )
                    append_operation(
                        SchGeometryOp.begin_group(group_id),
                        priority=inner_symbol_priority,
                    )
                    grouped_polyline: list[list[float]] = []
                    grouped_pen: dict[str, Any] | None = None
                    for child in element:
                        child_tag = child.tag.split("}", 1)[-1]
                        if child_tag != "line":
                            if grouped_polyline:
                                append_operation(
                                    SchGeometryOp.lines(
                                        grouped_polyline, pen=grouped_pen
                                    ),
                                    priority=inner_symbol_priority,
                                )
                                grouped_polyline = []
                                grouped_pen = None
                            append_geometry_from_element(
                                child,
                                group_role=grouped_role,
                            )
                            continue
                        points = line_points_from_svg_element(child)
                        pen = self._pen_from_svg(
                            child.get("stroke"),
                            child.get("stroke-width"),
                            units_per_px=units_per_px,
                        )
                        if (
                            grouped_polyline
                            and grouped_pen == pen
                            and abs(grouped_polyline[-1][0] - points[0][0]) <= 1e-6
                            and abs(grouped_polyline[-1][1] - points[0][1]) <= 1e-6
                        ):
                            grouped_polyline.append(points[1])
                        else:
                            if grouped_polyline:
                                append_operation(
                                    SchGeometryOp.lines(
                                        grouped_polyline, pen=grouped_pen
                                    ),
                                    priority=inner_symbol_priority,
                                )
                            grouped_polyline = [points[0], points[1]]
                            grouped_pen = pen
                    if grouped_polyline:
                        append_operation(
                            SchGeometryOp.lines(grouped_polyline, pen=grouped_pen),
                            priority=inner_symbol_priority,
                        )
                    append_operation(
                        SchGeometryOp.end_group(),
                        priority=inner_symbol_priority,
                    )
                    return
                if group_id.endswith("_PinInnerSymbol") and any(
                    child.tag.split("}", 1)[-1] == "path" for child in element
                ):
                    line_children = [
                        child
                        for child in element
                        if child.tag.split("}", 1)[-1] == "line"
                    ]

                    def analog_symbol_line_sort_key(child: ET.Element) -> float:
                        if effective_orientation == Rotation90.DEG_180:
                            return self._parse_svg_numeric(child.get("x1"))
                        if effective_orientation == Rotation90.DEG_90:
                            return self._parse_svg_numeric(child.get("y1"))
                        if effective_orientation == Rotation90.DEG_270:
                            return -self._parse_svg_numeric(child.get("y1"))
                        return -self._parse_svg_numeric(child.get("x1"))

                    for child in sorted(line_children, key=analog_symbol_line_sort_key):
                        points = line_points_from_svg_element(child)
                        append_operation(
                            SchGeometryOp.lines(
                                points,
                                pen=self._pen_from_svg(
                                    child.get("stroke"),
                                    child.get("stroke-width"),
                                    units_per_px=units_per_px,
                                ),
                            ),
                            priority=2,
                        )
                    for child in element:
                        if child.tag.split("}", 1)[-1] != "path":
                            continue
                        append_geometry_from_element(
                            child,
                            group_role="inner_symbol_group_normal",
                        )
                    return
                if group_id.endswith("_PinInnerSymbol"):
                    child_group_role = "inner_symbol"
                for child in element:
                    append_geometry_from_element(child, group_role=child_group_role)
                return

            if tag == "line":
                pen = self._pen_from_svg(
                    element.get("stroke"),
                    element.get("stroke-width"),
                    units_per_px=units_per_px,
                )
                (x1, y1), (x2, y2) = line_points_from_svg_element(element)
                priority = group_priority(group_role)
                if group_role is None and not body_line_emitted:
                    priority = 0
                    body_line_emitted = True
                append_operation(
                    SchGeometryOp.lines(
                        [[x1, y1], [x2, y2]],
                        pen=pen,
                    ),
                    priority=priority,
                )
                return

            if tag == "polygon":
                point_pairs: list[list[float]] = []
                for point_text in str(element.get("points", "")).split():
                    if "," not in point_text:
                        continue
                    px_text, py_text = point_text.split(",", 1)
                    gx, gy = svg_coord_to_geometry(
                        self._parse_svg_numeric(px_text),
                        self._parse_svg_numeric(py_text),
                        sheet_height_px=sheet_height_px,
                        units_per_px=units_per_px,
                    )
                    point_pairs.append([gx, gy])
                if not point_pairs:
                    return

                fill_raw = self._parse_svg_color(element.get("fill"))
                brush = make_solid_brush(fill_raw) if fill_raw is not None else None
                pen = self._pen_from_svg(
                    element.get("stroke"),
                    element.get("stroke-width"),
                    units_per_px=units_per_px,
                )
                append_operation(
                    SchGeometryOp.polygons(
                        [point_pairs],
                        brush=brush,
                        pen=pen,
                    ),
                    priority=group_priority(group_role),
                )
                return

            if tag == "rect":
                x = self._parse_svg_numeric(element.get("x"))
                y = self._parse_svg_numeric(element.get("y"))
                width = self._parse_svg_numeric(element.get("width"))
                height = self._parse_svg_numeric(element.get("height"))
                rx = self._parse_svg_numeric(element.get("rx"))
                ry = self._parse_svg_numeric(element.get("ry"))
                x1, y1 = svg_coord_to_geometry(
                    x,
                    y,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                x2, y2 = svg_coord_to_geometry(
                    x + width,
                    y + height,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                fill_raw = self._parse_svg_color(element.get("fill"))
                brush = make_solid_brush(fill_raw) if fill_raw is not None else None
                pen = self._pen_from_svg(
                    element.get("stroke"),
                    element.get("stroke-width"),
                    units_per_px=units_per_px,
                )
                append_operation(
                    SchGeometryOp.rounded_rectangle(
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        corner_x_radius=rx * units_per_px,
                        corner_y_radius=ry * units_per_px,
                        brush=brush,
                        pen=pen,
                    ),
                    priority=group_priority(group_role),
                )
                return

            if tag == "ellipse":
                center_x, center_y = svg_coord_to_geometry(
                    self._parse_svg_numeric(element.get("cx")),
                    self._parse_svg_numeric(element.get("cy")),
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                radius_x = self._parse_svg_numeric(element.get("rx"))
                radius_y = self._parse_svg_numeric(element.get("ry"))
                pen = self._pen_from_svg(
                    element.get("stroke"),
                    element.get("stroke-width"),
                    units_per_px=units_per_px,
                )
                if effective_orientation == Rotation90.DEG_180:
                    start_angle = 0.0
                    end_angle = -360.0
                elif effective_orientation == Rotation90.DEG_270:
                    start_angle = -270.0
                    end_angle = -630.0
                else:
                    start_angle = -90.0
                    end_angle = -450.0
                append_operation(
                    SchGeometryOp.arc(
                        center_x=center_x,
                        center_y=center_y,
                        width=radius_x * 2.0 * units_per_px,
                        height=radius_y * 2.0 * units_per_px,
                        start_angle=start_angle,
                        end_angle=end_angle,
                        pen=pen,
                    ),
                    priority=group_priority(group_role),
                )
                return

            if tag == "path":
                if (
                    str(element.get("data-text-source", "")).strip().lower()
                    == "polytext"
                ):
                    append_text_geometry(
                        text_value=str(element.get("data-text-value", "") or ""),
                        baseline_x=self._parse_svg_numeric(
                            element.get("data-text-anchor-x")
                        ),
                        baseline_y=self._parse_svg_numeric(
                            element.get("data-text-anchor-y")
                        ),
                        font_name_attr=str(
                            element.get("data-text-font-family", "") or ""
                        ),
                        font_size_attr=self._parse_svg_numeric(
                            element.get("data-text-font-size")
                        ),
                        font_weight_attr=element.get("font-weight"),
                        font_style_attr=element.get("font-style"),
                        fill_attr=element.get("fill"),
                        transform_attr=element.get("transform"),
                    )
                    return
                arc_spec = self._parse_svg_arc_path(element.get("d"))
                if arc_spec is None:
                    raise ValueError(
                        f"Unsupported pin SVG path for geometry conversion: {element.get('d')}"
                    )
                if abs(arc_spec["rotation"]) > 1e-9:
                    raise ValueError(
                        f"Unsupported rotated pin SVG arc for geometry conversion: {element.get('d')}"
                    )
                if group_role is None:
                    reorder_trailing_analog_symbol_lines()
                center_x_px, center_y_px = svg_circle_arc_center_for_flags(
                    arc_spec["x1"],
                    arc_spec["y1"],
                    arc_spec["x2"],
                    arc_spec["y2"],
                    arc_spec["rx"],
                    int(arc_spec["large_arc"]),
                    int(arc_spec["sweep"]),
                )
                center_x, center_y = svg_coord_to_geometry(
                    center_x_px,
                    center_y_px,
                    sheet_height_px=sheet_height_px,
                    units_per_px=units_per_px,
                )
                raw_start_angle = math.degrees(
                    math.atan2(
                        arc_spec["y1"] - center_y_px, arc_spec["x1"] - center_x_px
                    )
                )
                raw_end_angle = math.degrees(
                    math.atan2(
                        arc_spec["y2"] - center_y_px, arc_spec["x2"] - center_x_px
                    )
                )
                if int(arc_spec["sweep"]) == 1:
                    start_angle = raw_end_angle
                    end_angle = raw_start_angle
                    if end_angle >= start_angle:
                        end_angle -= 360.0
                else:
                    start_angle = raw_start_angle
                    end_angle = raw_end_angle
                    if end_angle <= start_angle:
                        end_angle += 360.0
                # Pin SVG path arcs are only used for ANALOG_SIGNAL_IN semicircles.
                # GeometryMaker serializes equivalent vertical semicircles differently
                # for 90-degree vs 270-degree pin orientations, so preserve that
                # branch explicitly instead of relying on atan2 normalization alone.
                if abs(arc_spec["x1"] - arc_spec["x2"]) <= 1e-6:
                    if effective_orientation == Rotation90.DEG_90:
                        start_angle = -90.0
                        end_angle = -270.0
                    elif effective_orientation == Rotation90.DEG_270:
                        start_angle = -450.0
                        end_angle = -270.0
                elif abs(arc_spec["y1"] - arc_spec["y2"]) <= 1e-6:
                    start_angle = 0.0
                    end_angle = -180.0
                pen = self._pen_from_svg(
                    element.get("stroke"),
                    element.get("stroke-width"),
                    units_per_px=units_per_px,
                )
                append_operation(
                    SchGeometryOp.arc(
                        center_x=center_x,
                        center_y=center_y,
                        width=arc_spec["rx"] * 2.0 * units_per_px,
                        height=arc_spec["ry"] * 2.0 * units_per_px,
                        start_angle=start_angle,
                        end_angle=end_angle,
                        pen=pen,
                    ),
                    priority=group_priority(group_role),
                )
                return

            if tag != "text":
                raise ValueError(
                    f"Unsupported pin SVG primitive for geometry conversion: {tag}"
                )

            append_text_geometry(
                text_value=element.text or "",
                baseline_x=self._parse_svg_numeric(element.get("x")),
                baseline_y=self._parse_svg_numeric(element.get("y")),
                font_name_attr=str(element.get("font-family", "")),
                font_size_attr=self._parse_svg_numeric(element.get("font-size")),
                font_weight_attr=element.get("font-weight"),
                font_style_attr=element.get("font-style"),
                fill_attr=element.get("fill"),
                transform_attr=element.get("transform"),
            )

        for child in root:
            append_geometry_from_element(child)

        return [
            op
            for _, _, op in sorted(
                ordered_operations, key=lambda item: (item[0], item[1])
            )
        ]

    def to_geometry(
        self,
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int = 64,
        wrap_record: bool = True,
    ) -> Any:
        from .altium_sch_geometry_oracle import (
            SchGeometryBounds,
            SchGeometryRecord,
            wrap_record_operations,
        )

        if self.is_hidden and not ctx.show_pins:
            return None if wrap_record else []

        operations = self._svg_elements_to_geometry_operations(
            self.to_svg(ctx),
            ctx,
            units_per_px=units_per_px,
        )
        if not wrap_record:
            return operations

        return SchGeometryRecord(
            handle=f"{document_id}\\{self.unique_id}",
            unique_id=self.unique_id,
            kind="pin",
            object_id="ePin",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=wrap_record_operations(
                self.unique_id,
                operations,
                units_per_px=units_per_px,
            ),
        )

    def to_svg(self, ctx: SchSvgRenderContext | None = None) -> list[str]:
        """Render pin to SVG elements."""
        if ctx is None:
            ctx = SchSvgRenderContext()

        if self.is_hidden and not ctx.show_pins:
            return []

        elements: list[str] = []
        body_end, hot_spot_coord, hot_spot = self._get_svg_endpoints(ctx)
        stroke = self._resolve_pin_stroke(ctx)
        self._append_pin_line(elements, body_end, hot_spot, hot_spot_coord, stroke, ctx)

        outer_ieee_symbol_elements, inner_ieee_symbol_elements = (
            self._render_ieee_symbols(
                body_end,
                hot_spot,
                stroke,
                ctx,
            )
        )
        defer_ieee_symbols_until_after_text = (
            self._inner_symbol_group_renders_after_text()
        )
        elements.extend(outer_ieee_symbol_elements)

        if ctx.show_pin_direction and self.symbol_outer == IeeeSymbol.NONE:
            elements.extend(self._render_electrical_glyph(body_end, stroke, ctx))

        if not defer_ieee_symbols_until_after_text:
            elements.extend(inner_ieee_symbol_elements)

        elements.extend(self._render_pin_name_svg(body_end, stroke, ctx))
        elements.extend(self._render_pin_designator_svg(body_end, stroke, ctx))

        if defer_ieee_symbols_until_after_text:
            elements.extend(inner_ieee_symbol_elements)

        self._append_pin_hot_spot_junctions(elements, hot_spot_coord, hot_spot, ctx)
        return elements

    def _get_svg_endpoints(
        self,
        ctx: SchSvgRenderContext,
    ) -> tuple[tuple[float, float], CoordPoint, tuple[float, float]]:
        """Return transformed body and hot-spot coordinates."""
        body_end = ctx.transform_coord_precise(self.location)
        hot_spot_coord = self.get_hot_spot()
        hot_spot = ctx.transform_coord_precise(hot_spot_coord)
        return (
            (round(body_end[0], 3), round(body_end[1], 3)),
            hot_spot_coord,
            (round(hot_spot[0], 3), round(hot_spot[1], 3)),
        )

    def _resolve_pin_stroke(self, ctx: SchSvgRenderContext) -> str:
        """Resolve the pin line color, including component overrides."""
        if ctx.pin_color_override is not None:
            return color_to_hex(ctx.pin_color_override)
        if self.color:
            return color_to_hex(self.color)
        return ctx.default_stroke

    def _append_pin_line(
        self,
        elements: list[str],
        body_end: tuple[float, float],
        hot_spot: tuple[float, float],
        hot_spot_coord: CoordPoint,
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> None:
        """Append the main pin line to the SVG output."""
        stroke_width = PIN_LINE_WIDTH * ctx.get_stroke_scale()
        pin_line_stroke = ctx.apply_compile_mask_color(
            stroke,
            ctx.is_segment_fully_under_compile_mask(
                self.location.x,
                self.location.y,
                hot_spot_coord.x,
                hot_spot_coord.y,
            ),
        )
        elements.append(
            svg_line(
                body_end[0],
                body_end[1],
                hot_spot[0],
                hot_spot[1],
                stroke=pin_line_stroke,
                stroke_width=stroke_width,
            )
        )

    def _append_pin_hot_spot_junctions(
        self,
        elements: list[str],
        hot_spot_coord: CoordPoint,
        hot_spot: tuple[float, float],
        ctx: SchSvgRenderContext,
    ) -> None:
        """Append junction markers at the pin hot spot when needed."""
        from .altium_sch_svg_renderer import SchJunctionZOrder

        if (hot_spot_coord.x, hot_spot_coord.y) not in ctx.connection_points:
            return

        junction_size = 4
        junction_r = 2
        junction_color = ctx.options.junction_color_override or "#000000"
        rx = hot_spot[0] - junction_size / 2
        ry = hot_spot[1] - junction_size / 2
        junction_elements = [
            f'<rect x = "{rx}" y="{ry}" width="{junction_size}" height="{junction_size}" '
            f'rx="{junction_r}" ry="{junction_r}" fill="{junction_color}" fill-opacity="1"/>',
            f'<rect x = "{rx}" y="{ry}" width="{junction_size}" height="{junction_size}" '
            f'stroke="{junction_color}" stroke-width="0.5px" vector-effect="non-scaling-stroke" '
            f'rx="{junction_r}" ry="{junction_r}"/>',
        ]
        if ctx.options.junction_z_order == SchJunctionZOrder.ALWAYS_ON_TOP:
            ctx.deferred_junctions.extend(junction_elements)
        else:
            elements.extend(junction_elements)

    def _render_pin_name_svg(
        self,
        body_end: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> list[str]:
        """Render the pin name text."""
        spec = self._resolve_pin_name_render_spec(ctx, stroke)
        if spec is None:
            return []
        name_x, name_y, name_transform = self._calculate_pin_name_position(
            body_end, ctx, spec
        )
        return self._render_pin_name_text(ctx, spec, name_x, name_y, name_transform)

    def _resolve_pin_name_render_spec(
        self,
        ctx: SchSvgRenderContext,
        stroke: str,
    ) -> _PinTextRenderSpec | None:
        """Resolve font, color, width, margin, and rotation for the pin name."""
        if not (ctx.show_pin_names and self.show_name and self.name):
            return None

        name_settings = self.name_settings
        is_custom_font = (
            name_settings is not None
            and name_settings.font_mode == PinItemMode.CUSTOM
            and name_settings.font_id is not None
        )
        if (
            is_custom_font
            and name_settings is not None
            and name_settings.font_id is not None
        ):
            font_id = name_settings.font_id
            font_name, _, is_bold, is_italic, _ = ctx.get_font_info(font_id)
            font_size_px = ctx.get_font_size_for_width(font_id)
            line_height = ctx.get_font_line_height(font_id)
        else:
            font_name, font_size_px, is_bold, is_italic, _ = ctx.get_system_font_info()
            line_height = 10.0

        font_size = ctx.get_baseline_font_size(font_size_px)
        font_weight = "bold" if is_bold else None
        font_style = "italic" if is_italic else None
        if (
            name_settings
            and name_settings.font_mode == PinItemMode.CUSTOM
            and name_settings.color != 0
        ):
            fill = color_to_hex(name_settings.color)
        else:
            fill = stroke

        display_name = _normalize_native_pin_text(
            self.name,
            native_svg_export=getattr(ctx, "native_svg_export", False),
        )
        clean_name = (
            display_name.replace("\\", "") if "\\" in display_name else display_name
        )
        text_width = measure_text_width(
            clean_name,
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
            use_altium_algorithm=False,
        )

        margin = self._get_default_or_custom_margin(self._name_margin_mils, 5)
        margin += self._get_inner_symbols_text_offset()
        rotated = self._is_name_text_rotated(self._get_effective_orientation(ctx), ctx)
        return _PinTextRenderSpec(
            text=self.name,
            display_text=display_name,
            clean_text=clean_name,
            font_family=font_name,
            font_size_px=font_size_px,
            font_size=font_size,
            line_height=line_height,
            is_bold=is_bold,
            is_italic=is_italic,
            font_weight=font_weight,
            font_style=font_style,
            fill=fill,
            text_width=text_width,
            margin=margin,
            rotated=rotated,
            settings=name_settings,
        )

    def _get_default_or_custom_margin(
        self,
        margin_mils: float | None,
        default_margin: float,
    ) -> float:
        """Return the SVG-space margin for pin text."""
        if margin_mils is None:
            return default_margin
        return margin_mils / 10.0

    def _is_name_text_rotated(
        self,
        effective_orient: Rotation90,
        ctx: SchSvgRenderContext,
    ) -> bool:
        """Return whether the pin name should render rotated."""
        if ctx.schlib_mode:
            return False
        if effective_orient in (Rotation90.DEG_90, Rotation90.DEG_270):
            return self._vertical_pin_text_is_rotated(self.name_settings)
        return (
            effective_orient in (Rotation90.DEG_0, Rotation90.DEG_180)
            and self.name_settings is not None
            and self.name_settings.position_mode == PinItemMode.CUSTOM
            and self.name_settings.rotation == Rotation90.DEG_90
        )

    def _calculate_pin_name_position(
        self,
        body_end: tuple[float, float],
        ctx: SchSvgRenderContext,
        spec: _PinTextRenderSpec,
    ) -> tuple[float, float, str | None]:
        """Calculate the pin-name anchor and optional rotation transform."""
        effective_orient = self._get_effective_orientation(ctx)
        name_inner_bounds_shift = self._get_name_inner_bounds_shift()
        horizontal_centering_offset = int((spec.font_size_px - 1) / 2)
        centering_offset = int(spec.font_size_px / 2)
        y_correction = self._calculate_pin_name_y_correction(body_end, spec)
        if ctx.options.truncate_font_size_for_baseline:
            perpendicular_baseline_offset = int(spec.font_size_px)
            perpendicular_baseline_half_offset = int(spec.font_size_px / 2)
        else:
            perpendicular_baseline_offset = spec.font_size_px
            perpendicular_baseline_half_offset = spec.font_size_px / 2.0

        if effective_orient == Rotation90.DEG_0:
            return self._calculate_pin_name_position_right(
                body_end,
                spec,
                horizontal_centering_offset,
                y_correction,
                name_inner_bounds_shift,
            )
        if effective_orient == Rotation90.DEG_90:
            return self._calculate_pin_name_position_up(
                body_end,
                ctx,
                spec,
                horizontal_centering_offset,
                centering_offset,
                perpendicular_baseline_offset,
                name_inner_bounds_shift,
            )
        if effective_orient == Rotation90.DEG_180:
            return self._calculate_pin_name_position_left(
                body_end,
                spec,
                horizontal_centering_offset,
                y_correction,
                name_inner_bounds_shift,
            )
        return self._calculate_pin_name_position_down(
            body_end,
            ctx,
            spec,
            horizontal_centering_offset,
            centering_offset,
            perpendicular_baseline_half_offset,
            name_inner_bounds_shift,
        )

    def _calculate_pin_name_y_correction(
        self,
        body_end: tuple[float, float],
        spec: _PinTextRenderSpec,
    ) -> float:
        """Return the native pin-name centering correction for horizontal labels."""
        horizontal_centering_offset = int((spec.font_size_px - 1) / 2)
        body_y_mod_20 = int(round(body_end[1])) % 20
        if horizontal_centering_offset >= 8:
            return -1.0
        if horizontal_centering_offset >= 6:
            return 0.0
        if body_y_mod_20 == 5:
            return 0.5 if horizontal_centering_offset <= 3 else -0.5
        if body_y_mod_20 == 10:
            if horizontal_centering_offset <= 3:
                if (
                    len(spec.clean_text) > 30
                    and spec.font_family.lower().startswith("arial")
                    and spec.clean_text.isalnum()
                ):
                    return 0.5
                return 0.0
            return -0.5
        return 0.0

    def _calculate_pin_name_position_right(
        self,
        body_end: tuple[float, float],
        spec: _PinTextRenderSpec,
        horizontal_centering_offset: int,
        y_correction: float,
        name_inner_bounds_shift: float,
    ) -> tuple[float, float, str | None]:
        """Position the name for a right-pointing pin."""
        inner_bounds_offset = 2
        if spec.rotated:
            name_x = body_end[0] - spec.margin - int(spec.font_size_px / 2)
            name_y = body_end[1] + spec.text_width / 2
            return (name_x, name_y, f"rotate(-90 {name_x:.4f} {name_y:.4f})")
        name_x = body_end[0] - spec.margin - spec.text_width - inner_bounds_offset
        name_y = (
            body_end[1]
            + horizontal_centering_offset
            - name_inner_bounds_shift
            + y_correction
        )
        return (name_x, name_y, None)

    def _calculate_pin_name_position_up(
        self,
        body_end: tuple[float, float],
        ctx: SchSvgRenderContext,
        spec: _PinTextRenderSpec,
        horizontal_centering_offset: int,
        centering_offset: int,
        perpendicular_baseline_offset: float,
        name_inner_bounds_shift: float,
    ) -> tuple[float, float, str | None]:
        """Position the name for an up-pointing pin."""
        use_native_90_tuning = ctx.rotation == 0 and not ctx.mirror
        if spec.rotated:
            if use_native_90_tuning:
                name_x = (
                    body_end[0] + horizontal_centering_offset - name_inner_bounds_shift
                )
                name_y = body_end[1] + spec.text_width + spec.margin + 2
            else:
                name_x = body_end[0] + centering_offset
                name_y = body_end[1] + spec.text_width + spec.margin
            return (name_x, name_y, f"rotate(-90 {name_x:.4f} {name_y:.4f})")

        name_x = body_end[0] - spec.text_width / 2
        name_y = body_end[1] + perpendicular_baseline_offset + spec.margin
        if use_native_90_tuning:
            name_y += 2 if self._name_margin_mils is not None else -3
        return (name_x, name_y, None)

    def _calculate_pin_name_position_left(
        self,
        body_end: tuple[float, float],
        spec: _PinTextRenderSpec,
        horizontal_centering_offset: int,
        y_correction: float,
        name_inner_bounds_shift: float,
    ) -> tuple[float, float, str | None]:
        """Position the name for a left-pointing pin."""
        inner_bounds_offset = 2
        if spec.rotated:
            name_x = body_end[0] + spec.margin + spec.font_size_px
            parent_orient = self._get_parent_orientation()
            if (
                self.orientation == Rotation90.DEG_180
                and parent_orient == Rotation90.DEG_90
                and self._name_margin_mils is not None
            ):
                name_x += spec.line_height - spec.font_size_px
            name_y = body_end[1] + spec.text_width / 2
            return (name_x, name_y, f"rotate(-90 {name_x:.4f} {name_y:.4f})")

        name_x = body_end[0] + spec.margin + inner_bounds_offset
        name_y = (
            body_end[1]
            + horizontal_centering_offset
            - name_inner_bounds_shift
            + y_correction
        )
        return (name_x, name_y, None)

    def _calculate_pin_name_position_down(
        self,
        body_end: tuple[float, float],
        ctx: SchSvgRenderContext,
        spec: _PinTextRenderSpec,
        horizontal_centering_offset: int,
        centering_offset: int,
        perpendicular_baseline_half_offset: float,
        name_inner_bounds_shift: float,
    ) -> tuple[float, float, str | None]:
        """Position the name for a down-pointing pin."""
        use_native_270_tuning = ctx.rotation == 0 and not ctx.mirror
        if spec.rotated:
            if use_native_270_tuning:
                name_x = (
                    body_end[0] + horizontal_centering_offset - name_inner_bounds_shift
                )
                name_y = body_end[1] - spec.margin - 2
            else:
                name_x = body_end[0] + centering_offset
                name_y = body_end[1] - spec.margin
            return (name_x, name_y, f"rotate(-90 {name_x:.4f} {name_y:.4f})")

        use_float_half_height = (
            ctx.options.truncate_font_size_for_baseline
            and spec.settings is not None
            and spec.settings.position_mode == PinItemMode.CUSTOM
            and spec.settings.rotation == Rotation90.DEG_90
            and spec.settings.rotation_anchor == PinTextAnchor.PIN
        )
        vertical_half_offset = (
            spec.font_size_px / 2.0
            if use_float_half_height
            else perpendicular_baseline_half_offset
        )
        name_x = body_end[0] - spec.text_width / 2
        if self._name_margin_mils is None:
            name_y = body_end[1] - vertical_half_offset
        else:
            name_y = body_end[1] - (vertical_half_offset + spec.margin)
        return (name_x, name_y, None)

    def _render_pin_name_text(
        self,
        ctx: SchSvgRenderContext,
        spec: _PinTextRenderSpec,
        name_x: float,
        name_y: float,
        name_transform: str | None,
    ) -> list[str]:
        """Render the pin name, including overbar handling."""
        elements: list[str] = []
        if name_transform and "\\" in spec.display_text:
            elements.append(
                self._render_pin_text(
                    ctx,
                    name_x,
                    name_y,
                    spec.clean_text,
                    font_size=spec.font_size,
                    font_family=spec.font_family,
                    fill=spec.fill,
                    transform=name_transform,
                    font_weight=spec.font_weight,
                    font_style=spec.font_style,
                    poly_target_advance=spec.text_width,
                )
            )
            _, overline_elements = render_text_with_overline(
                spec.display_text,
                name_x,
                name_y,
                spec.font_size_px,
                spec.font_family,
                fill=spec.fill,
                stroke_color=spec.fill,
            )
            for line in overline_elements:
                if "transform=" not in line:
                    line = line.replace("/>", f' transform="{name_transform}"/>')
                elements.append(line)
            return elements

        if "\\" in spec.display_text:
            overline_baseline_y = name_y
            if abs(name_y - (math.floor(name_y) + 0.5)) <= 1e-9:
                overline_baseline_y = float(math.floor(name_y + 0.5))
            _, overline_elements = render_text_with_overline(
                spec.display_text,
                name_x,
                overline_baseline_y,
                spec.font_size_px,
                spec.font_family,
                fill=spec.fill,
                stroke_color=spec.fill,
            )
            elements.extend(overline_elements)
            elements.append(
                self._render_pin_text(
                    ctx,
                    name_x,
                    name_y,
                    spec.clean_text,
                    font_size=spec.font_size,
                    font_family=spec.font_family,
                    fill=spec.fill,
                    font_weight=spec.font_weight,
                    font_style=spec.font_style,
                    poly_target_advance=spec.text_width,
                )
            )
            return elements

        elements.append(
            self._render_pin_text(
                ctx,
                name_x,
                name_y,
                spec.clean_text,
                font_size=spec.font_size,
                font_family=spec.font_family,
                fill=spec.fill,
                transform=name_transform,
                font_weight=spec.font_weight,
                font_style=spec.font_style,
                poly_target_advance=spec.text_width,
            )
        )
        return elements

    def _render_pin_designator_svg(
        self,
        body_end: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> list[str]:
        """Render the pin designator text."""
        spec = self._resolve_pin_designator_render_spec(ctx, stroke)
        if spec is None:
            return []

        effective_orient = self._get_effective_orientation(ctx)
        baseline_offset = self._get_designator_baseline_offset(ctx, spec.font_size_px)
        descent_offset = self._get_designator_descent_offset(spec)
        pin_is_vertical = effective_orient in (Rotation90.DEG_90, Rotation90.DEG_270)
        is_perpendicular = spec.rotated != pin_is_vertical
        base_x, base_y = self._get_pin_designator_anchor(
            body_end, effective_orient, spec.margin
        )
        if is_perpendicular:
            if pin_is_vertical:
                base_x -= DESIGNATOR_PERPENDICULAR_OFFSET
            else:
                base_y -= DESIGNATOR_PERPENDICULAR_OFFSET

        desig_x, desig_y, desig_transform = self._calculate_pin_designator_position(
            effective_orient,
            spec,
            base_x,
            base_y,
            baseline_offset,
            descent_offset,
        )
        desig_x, desig_transform = (
            self._apply_pin_designator_rotated_component_adjustments(
                effective_orient,
                spec,
                desig_x,
                desig_y,
                desig_transform,
                descent_offset,
            )
        )
        return [
            self._render_pin_text(
                ctx,
                desig_x,
                desig_y,
                self.designator,
                font_size=spec.font_size,
                font_family=spec.font_family,
                fill=spec.fill,
                transform=desig_transform,
                font_weight=spec.font_weight,
                font_style=spec.font_style,
                poly_target_advance=spec.text_width,
            )
        ]

    def _resolve_pin_designator_render_spec(
        self,
        ctx: SchSvgRenderContext,
        stroke: str,
    ) -> _PinTextRenderSpec | None:
        """Resolve font, color, width, margin, and rotation for the designator."""
        if not (ctx.show_pin_numbers and self.show_designator and self.designator):
            return None

        designator_settings = self.designator_settings
        is_custom_font = (
            designator_settings is not None
            and designator_settings.font_mode == PinItemMode.CUSTOM
            and designator_settings.font_id is not None
        )
        if (
            is_custom_font
            and designator_settings is not None
            and designator_settings.font_id is not None
        ):
            font_id = designator_settings.font_id
            font_name, _, is_bold, is_italic, _ = ctx.get_font_info(font_id)
            font_size_px = ctx.get_font_size_for_width(font_id)
        else:
            font_name, font_size_px, is_bold, is_italic, _ = ctx.get_system_font_info()

        font_size = ctx.get_baseline_font_size(font_size_px)
        font_weight = "bold" if is_bold else None
        font_style = "italic" if is_italic else None
        text_width = measure_text_width(
            self.designator,
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        if (
            designator_settings
            and designator_settings.font_mode == PinItemMode.CUSTOM
            and designator_settings.color != 0
        ):
            fill = color_to_hex(designator_settings.color)
        else:
            fill = stroke

        rotated = self._is_designator_text_rotated(ctx)
        margin = self._get_default_or_custom_margin(self._designator_margin_mils, 8)
        return _PinTextRenderSpec(
            text=self.designator,
            display_text=self.designator,
            clean_text=self.designator,
            font_family=font_name,
            font_size_px=font_size_px,
            font_size=font_size,
            line_height=font_size_px,
            is_bold=is_bold,
            is_italic=is_italic,
            font_weight=font_weight,
            font_style=font_style,
            fill=fill,
            text_width=text_width,
            margin=margin,
            rotated=rotated,
            settings=designator_settings,
        )

    def _is_designator_text_rotated(self, ctx: SchSvgRenderContext) -> bool:
        """Return whether the pin designator should render rotated."""
        if ctx.schlib_mode:
            return False
        if self.orientation in (Rotation90.DEG_90, Rotation90.DEG_270):
            return self._vertical_pin_text_is_rotated(self.designator_settings)
        return (
            self.orientation in (Rotation90.DEG_0, Rotation90.DEG_180)
            and self.designator_settings is not None
            and self.designator_settings.position_mode == PinItemMode.CUSTOM
            and self.designator_settings.rotation == Rotation90.DEG_90
        )

    def _get_designator_baseline_offset(
        self,
        ctx: SchSvgRenderContext,
        font_size_px: float,
    ) -> float:
        """Return the SVG baseline offset for designator text."""
        if ctx.options.truncate_font_size_for_baseline:
            return int(font_size_px)
        return font_size_px

    def _get_designator_descent_offset(self, spec: _PinTextRenderSpec) -> int:
        """Return the bottom-alignment descent offset for designators."""
        font_factor = get_font_factor(spec.font_family, spec.is_bold, spec.is_italic)
        if font_factor >= 1.0:
            return 1
        pt = spec.font_size_px / font_factor
        return max(1, round((1 - font_factor) * pt + 0.5))

    def _get_pin_designator_anchor(
        self,
        body_end: tuple[float, float],
        effective_orient: Rotation90,
        margin: float,
    ) -> tuple[float, float]:
        """Return the rotated base anchor for the designator algorithm."""
        if effective_orient == Rotation90.DEG_0:
            return (body_end[0] + margin, body_end[1])
        if effective_orient == Rotation90.DEG_90:
            return (body_end[0], body_end[1] - margin)
        if effective_orient == Rotation90.DEG_180:
            return (body_end[0] - margin, body_end[1])
        return (body_end[0], body_end[1] + margin)

    def _calculate_pin_designator_position(
        self,
        effective_orient: Rotation90,
        spec: _PinTextRenderSpec,
        base_x: float,
        base_y: float,
        baseline_offset: float,
        descent_offset: int,
    ) -> tuple[float, float, str | None]:
        """Apply native alignment rules to the designator anchor."""
        y_offset = self.get_text_y_offset()
        if effective_orient == Rotation90.DEG_0:
            if spec.rotated:
                desig_x = base_x + baseline_offset
                desig_y = base_y
                return (desig_x, desig_y, f"rotate(-90 {desig_x:.4f} {desig_y:.4f})")
            return (base_x, base_y - descent_offset - y_offset, None)

        if effective_orient == Rotation90.DEG_90:
            if spec.rotated:
                desig_x = base_x - descent_offset
                desig_y = base_y
                return (desig_x, desig_y, f"rotate(-90 {desig_x:.4f} {desig_y:.4f})")
            desig_x = base_x - spec.text_width
            desig_y = base_y - DESIGNATOR_PERPENDICULAR_OFFSET
            if self._designator_margin_mils is None:
                desig_y += baseline_offset
            elif spec.font_family == "Times New Roman" and baseline_offset == int(
                spec.font_size_px
            ):
                desig_y += 1
            return (desig_x, desig_y, None)

        if effective_orient == Rotation90.DEG_180:
            if spec.rotated:
                desig_x = base_x - DESIGNATOR_PERPENDICULAR_OFFSET
                if spec.font_family == "Times New Roman":
                    desig_x += 1
                desig_y = base_y
                return (desig_x, desig_y, f"rotate(-90 {desig_x:.4f} {desig_y:.4f})")
            return (base_x - spec.text_width, base_y - descent_offset - y_offset, None)

        if spec.rotated:
            desig_x = base_x
            if self._get_parent_orientation() in (Rotation90.DEG_0, Rotation90.DEG_180):
                desig_x -= descent_offset
            desig_y = base_y + spec.text_width
            return (desig_x, desig_y, f"rotate(-90 {desig_x:.4f} {desig_y:.4f})")
        return (base_x - spec.text_width, base_y + baseline_offset, None)

    def _apply_pin_designator_rotated_component_adjustments(
        self,
        effective_orient: Rotation90,
        spec: _PinTextRenderSpec,
        desig_x: float,
        desig_y: float,
        desig_transform: str | None,
        descent_offset: int,
    ) -> tuple[float, str | None]:
        """Apply the remaining rotated-component tweaks to the designator anchor."""
        parent_orient = self._get_parent_orientation()
        if (
            spec.rotated
            and self.orientation in (Rotation90.DEG_90, Rotation90.DEG_270)
            and parent_orient in (Rotation90.DEG_90, Rotation90.DEG_270)
        ):
            if effective_orient == Rotation90.DEG_90:
                desig_x -= DESIGNATOR_PERPENDICULAR_OFFSET - descent_offset
            else:
                desig_x -= DESIGNATOR_PERPENDICULAR_OFFSET
            if desig_transform is not None:
                desig_transform = f"rotate(-90 {desig_x:.4f} {desig_y:.4f})"

        if (
            spec.rotated
            and spec.settings is not None
            and spec.settings.position_mode == PinItemMode.DEFAULT
            and spec.settings.font_mode != PinItemMode.CUSTOM
            and self.orientation in (Rotation90.DEG_90, Rotation90.DEG_270)
            and parent_orient in (Rotation90.DEG_90, Rotation90.DEG_270)
        ):
            desig_x += 1
            if desig_transform is not None:
                desig_transform = f"rotate(-90 {desig_x:.4f} {desig_y:.4f})"
        return (desig_x, desig_transform)

    def _render_electrical_glyph(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render electrical type glyph (INPUT/OUTPUT/IO arrows).

        Only INPUT, OUTPUT, and I/O electrical types produce visible glyphs.
        Other types (OpenCollector, Passive, HiZ, OpenEmitter, Power) show no glyph.

        The glyphs are positioned at the body end of the pin (where it connects
        to the symbol), not at the hot spot (wire connection point).

        When ShowPinDirection is enabled and no explicit outer symbol is set,
        electrical types map to IEEE symbols:
        - eElectricInput -> eRightLeftSignalFlow (arrow pointing toward body)
        - eElectricOutput -> eLeftRightSignalFlow (arrow pointing away from body)
        - eElectricIO -> eBidirectionalSignalFlow (two arrows)

        Args:
            body_end: Transformed coordinates of pin body connection point
            stroke: Stroke color for arrow outline
            ctx: Render context

        Returns:
            List of SVG polygon elements for the arrow(s)
        """
        elements = []

        # Only render for INPUT, OUTPUT, and I/O electrical types
        if self.electrical == PinElectrical.INPUT:
            elements.extend(self._render_input_arrow(body_end, stroke, ctx))
        elif self.electrical == PinElectrical.OUTPUT:
            elements.extend(self._render_output_arrow(body_end, stroke, ctx))
        elif self.electrical == PinElectrical.IO:
            elements.extend(self._render_bidirectional_arrows(body_end, stroke, ctx))

        return elements

    def _render_input_arrow(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render INPUT arrow (pointing toward body).

        For right-pointing pin (DEG_0): arrow tip at body_end, pointing left.
        Arrow vertices: tip at (x, y), corners at (x+6, y +/- 2)
        When outer_edge symbol (DOT, ALI, ALO) is present, arrow shifts +6 pixels
        outward to make room for the outer_edge symbol.
        """
        x, y = body_end

        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when HAS outer_edge, shift +6; when NO outer_edge, no shift
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        # Calculate arrow points based on pin orientation
        if self.orientation == Rotation90.DEG_0:  # Right-pointing pin
            # Arrow points left (toward body which is on the left)
            points = [
                (x + ARROW_LENGTH + outer_offset, y - ARROW_HALF_HEIGHT),  # Right-top
                (x + outer_offset, y),  # Left-center (tip)
                (
                    x + ARROW_LENGTH + outer_offset,
                    y + ARROW_HALF_HEIGHT,
                ),  # Right-bottom
            ]
        elif self.orientation == Rotation90.DEG_90:  # Up-pointing pin
            # Arrow points down (toward body which is below)
            points = [
                (x - ARROW_HALF_HEIGHT, y - ARROW_LENGTH - outer_offset),  # Left-top
                (x, y - outer_offset),  # Center-bottom (tip)
                (x + ARROW_HALF_HEIGHT, y - ARROW_LENGTH - outer_offset),  # Right-top
            ]
        elif self.orientation == Rotation90.DEG_180:  # Left-pointing pin
            # Arrow points right (toward body which is on the right)
            points = [
                (x - ARROW_LENGTH - outer_offset, y - ARROW_HALF_HEIGHT),  # Left-top
                (x - outer_offset, y),  # Right-center (tip)
                (x - ARROW_LENGTH - outer_offset, y + ARROW_HALF_HEIGHT),  # Left-bottom
            ]
        elif self.orientation == Rotation90.DEG_270:  # Down-pointing pin
            # Arrow points up (toward body which is above)
            points = [
                (x - ARROW_HALF_HEIGHT, y + ARROW_LENGTH + outer_offset),  # Left-bottom
                (x, y + outer_offset),  # Center-top (tip)
                (
                    x + ARROW_HALF_HEIGHT,
                    y + ARROW_LENGTH + outer_offset,
                ),  # Right-bottom
            ]
        else:
            return []

        return self._make_arrow_polygons(points, stroke, ctx)

    def _render_output_arrow(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render OUTPUT arrow (pointing away from body).

        For right-pointing pin (DEG_0): arrow tip at body_end + 6, pointing right.
        Arrow vertices: tip at (x+6, y), base corners at (x, y +/- 2)
        When outer_edge symbol (DOT, ALI, ALO) is present, arrow shifts +6 pixels
        outward to make room for the outer_edge symbol.
        """
        x, y = body_end

        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when HAS outer_edge, shift +6; when NO outer_edge, no shift
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        # Calculate arrow points based on pin orientation
        if self.orientation == Rotation90.DEG_0:  # Right-pointing pin
            # Arrow points right (away from body)
            points = [
                (x + ARROW_LENGTH + outer_offset, y),  # Right-center (tip)
                (x + outer_offset, y - ARROW_HALF_HEIGHT),  # Left-top
                (x + outer_offset, y + ARROW_HALF_HEIGHT),  # Left-bottom
            ]
        elif self.orientation == Rotation90.DEG_90:  # Up-pointing pin
            # Arrow points up (away from body)
            points = [
                (x, y - ARROW_LENGTH - outer_offset),  # Center-top (tip)
                (x - ARROW_HALF_HEIGHT, y - outer_offset),  # Left-bottom
                (x + ARROW_HALF_HEIGHT, y - outer_offset),  # Right-bottom
            ]
        elif self.orientation == Rotation90.DEG_180:  # Left-pointing pin
            # Arrow points left (away from body)
            points = [
                (x - ARROW_LENGTH - outer_offset, y),  # Left-center (tip)
                (x - outer_offset, y - ARROW_HALF_HEIGHT),  # Right-top
                (x - outer_offset, y + ARROW_HALF_HEIGHT),  # Right-bottom
            ]
        elif self.orientation == Rotation90.DEG_270:  # Down-pointing pin
            # Arrow points down (away from body)
            points = [
                (x, y + ARROW_LENGTH + outer_offset),  # Center-bottom (tip)
                (x - ARROW_HALF_HEIGHT, y + outer_offset),  # Left-top
                (x + ARROW_HALF_HEIGHT, y + outer_offset),  # Right-top
            ]
        else:
            return []

        return self._make_arrow_polygons(points, stroke, ctx)

    def _render_bidirectional_arrows(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render bidirectional I/O arrows (two arrows with gap).

        For right-pointing pin (DEG_0):
        - First arrow (INPUT-style): tip at body_end, corners at body_end + (6, +/-2)
        - Second arrow (OUTPUT-style): tip at body_end + 13, base at body_end + (7, +/-2)
        - 1 pixel gap between arrows (first ends at +6, second starts at +7)
        When outer_edge symbol (DOT, ALI, ALO) is present, arrows shift +6 pixels
        outward to make room for the outer_edge symbol.
        """
        x, y = body_end
        elements = []

        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when HAS outer_edge, shift +6; when NO outer_edge, no shift
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        # Calculate arrow points based on pin orientation
        if self.orientation == Rotation90.DEG_0:  # Right-pointing pin
            # First arrow (INPUT-style, points left)
            points1 = [
                (x + ARROW_LENGTH + outer_offset, y - ARROW_HALF_HEIGHT),
                (x + outer_offset, y),
                (x + ARROW_LENGTH + outer_offset, y + ARROW_HALF_HEIGHT),
            ]
            # Second arrow (OUTPUT-style, points right)
            points2 = [
                (x + ARROW_LENGTH + BIDIR_GAP + outer_offset, y - ARROW_HALF_HEIGHT),
                (x + ARROW_LENGTH + BIDIR_GAP + outer_offset, y + ARROW_HALF_HEIGHT),
                (x + BIDIR_TOTAL_LENGTH + outer_offset, y),
            ]
        elif self.orientation == Rotation90.DEG_90:  # Up-pointing pin
            # First arrow (INPUT-style, points down)
            points1 = [
                (x - ARROW_HALF_HEIGHT, y - ARROW_LENGTH - outer_offset),
                (x, y - outer_offset),
                (x + ARROW_HALF_HEIGHT, y - ARROW_LENGTH - outer_offset),
            ]
            # Second arrow (OUTPUT-style, points up)
            points2 = [
                (x - ARROW_HALF_HEIGHT, y - ARROW_LENGTH - BIDIR_GAP - outer_offset),
                (x + ARROW_HALF_HEIGHT, y - ARROW_LENGTH - BIDIR_GAP - outer_offset),
                (x, y - BIDIR_TOTAL_LENGTH - outer_offset),
            ]
        elif self.orientation == Rotation90.DEG_180:  # Left-pointing pin
            # First arrow (INPUT-style, points right)
            points1 = [
                (x - ARROW_LENGTH - outer_offset, y - ARROW_HALF_HEIGHT),
                (x - outer_offset, y),
                (x - ARROW_LENGTH - outer_offset, y + ARROW_HALF_HEIGHT),
            ]
            # Second arrow (OUTPUT-style, points left)
            points2 = [
                (x - ARROW_LENGTH - BIDIR_GAP - outer_offset, y - ARROW_HALF_HEIGHT),
                (x - ARROW_LENGTH - BIDIR_GAP - outer_offset, y + ARROW_HALF_HEIGHT),
                (x - BIDIR_TOTAL_LENGTH - outer_offset, y),
            ]
        elif self.orientation == Rotation90.DEG_270:  # Down-pointing pin
            # First arrow (INPUT-style, points up)
            points1 = [
                (x - ARROW_HALF_HEIGHT, y + ARROW_LENGTH + outer_offset),
                (x, y + outer_offset),
                (x + ARROW_HALF_HEIGHT, y + ARROW_LENGTH + outer_offset),
            ]
            # Second arrow (OUTPUT-style, points down)
            points2 = [
                (x - ARROW_HALF_HEIGHT, y + ARROW_LENGTH + BIDIR_GAP + outer_offset),
                (x + ARROW_HALF_HEIGHT, y + ARROW_LENGTH + BIDIR_GAP + outer_offset),
                (x, y + BIDIR_TOTAL_LENGTH + outer_offset),
            ]
        else:
            return []

        elements.extend(self._make_arrow_polygons(points1, stroke, ctx))
        elements.extend(self._make_arrow_polygons(points2, stroke, ctx))
        return elements

    def _get_effective_orientation(self, ctx: SchSvgRenderContext) -> Rotation90:
        """
        Calculate effective pin orientation after component transforms.

        The stored pin orientation is relative to the component.
        Component rotation and mirror modify the effective world-space orientation.

        Transform order:
        1. Start with pin's stored orientation
        2. If component is mirrored, flip horizontal axis (DEG_0 -> DEG_180)
        3. Add component rotation

        Based on analysis of Altium's pin.GetState_Orientation().
        """
        # Start with stored orientation
        orientation = self.orientation.value  # 0, 1, 2, 3

        # If component is mirrored, flip the horizontal axis
        # DEG_0 (right) -> DEG_180 (left)
        # DEG_90 (up) and DEG_270 (down) stay same but are flipped side
        if ctx.mirror:
            if orientation == 0:
                orientation = 2  # DEG_0 -> DEG_180
            elif orientation == 2:
                orientation = 0  # DEG_180 -> DEG_0

        # Add component rotation (ctx.rotation is in degrees: 0, 90, 180, 270)
        rotation_steps = ctx.rotation // 90
        orientation = (orientation + rotation_steps) % 4

        return Rotation90(orientation)

    def _should_reverse_polygon_winding(self, ctx: SchSvgRenderContext) -> bool:
        """
        Determine if polygon winding order should be reversed.

        Altium's GetSymbolMirrored() returns true for pins oriented left (DEG_180)
        or down (DEG_270) in world space. This causes a scale transform of (-1, 1)
        or (1, -1), effectively reversing polygon winding.

        DISABLED: The point definitions now use consistent winding across all
        orientations, matching native Altium output. No reversal needed.
        """
        # Reversal disabled - point definitions are now consistent
        return False

    def _make_arrow_polygons(
        self,
        points: list[tuple[float, float]],
        stroke: str,
        ctx: SchSvgRenderContext | None = None,
    ) -> list[str]:
        """
        Create SVG polygon elements for an arrow.

        Altium renders arrows as two separate polygons:
        1. Fill polygon (background color, NO stroke)
        2. Stroke polygon (pin color, fill=none)

        Args:
            points: List of (x, y) tuples defining the arrow vertices
            stroke: Stroke color for the arrow outline
            ctx: Render context (used to determine winding order)

        Returns:
            List of two SVG polygon strings (fill + stroke)
        """
        # Reverse point order if mirroring requires it
        # This matches Altium's scale transform effect on polygon winding
        if ctx is not None and self._should_reverse_polygon_winding(ctx):
            points = list(reversed(points))

        return [
            # Fill polygon - no stroke (stroke="" and stroke_width=0)
            svg_polygon(
                points,
                stroke="",
                stroke_width=0,
                fill=SYMBOL_FILL_COLOR,
                fill_opacity=1.0,
            ),
            # Stroke polygon - no fill
            svg_polygon(
                points,
                stroke=stroke,
                stroke_width=0.5,
                vector_effect="non-scaling-stroke",
            ),
        ]

    # =========================================================================
    # IEEE Symbol Rendering Methods
    # =========================================================================
    # Implements pin decoration symbols per IEEE standard.

    def _render_ieee_symbols(
        self,
        body_end: tuple[float, float],
        hot_spot: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> tuple[list[str], list[str]]:
        """
        Render IEEE symbols for this pin, split by native draw order.

        Args:
            body_end: Transformed coordinates of pin body connection point
            hot_spot: Transformed coordinates of wire connection point
            stroke: Stroke color for symbol outlines
            ctx: Render context

        Returns:
            Tuple of:
            - outer/outer_edge SVG elements rendered inline before text
            - inner/inner_edge SVG elements wrapped in PinInnerSymbol group
        """
        inner_symbol_elements: list[str] = []
        outer_symbol_elements: list[str] = []

        # Render inner edge symbol (at body edge - Clock, etc.)
        if self.symbol_inner_edge != IeeeSymbol.NONE:
            inner_symbol_elements.extend(
                self._render_inner_edge_symbol(body_end, stroke, ctx)
            )

        # Render inner symbol (inside the symbol body)
        if self.symbol_inner != IeeeSymbol.NONE:
            inner_symbol_elements.extend(
                self._render_inner_symbol(body_end, stroke, ctx)
            )

        # Render outer edge symbol (near hot spot - Dot, ActiveLow)
        if self.symbol_outer_edge != IeeeSymbol.NONE:
            outer_symbol_elements.extend(
                self._render_outer_edge_symbol(body_end, hot_spot, stroke, ctx)
            )

        # Render outer symbol (beyond hot spot - signal flow arrows)
        if self.symbol_outer != IeeeSymbol.NONE:
            outer_symbol_elements.extend(
                self._render_outer_symbol(body_end, hot_spot, stroke, ctx)
            )

        grouped_inner_symbols = inner_symbol_elements
        if inner_symbol_elements and self.unique_id:
            group_id = f"{self.unique_id}_PinInnerSymbol"
            grouped_inner_symbols = [svg_group(inner_symbol_elements, id=group_id)]

        return (outer_symbol_elements, grouped_inner_symbols)

    def _inner_symbol_group_renders_after_text(self) -> bool:
        """
        Match native ordering for decorations rendered in PinInnerSymbol.
        """
        return (
            self.symbol_inner_edge != IeeeSymbol.NONE
            or self.symbol_inner != IeeeSymbol.NONE
        )

    def _get_parent_orientation(self) -> Rotation90:
        """
        Return the parent component orientation when present.
        """
        parent_orient_raw = getattr(
            getattr(self, "parent", None), "orientation", Rotation90.DEG_0
        )
        try:
            return Rotation90(int(parent_orient_raw))
        except (ValueError, TypeError):
            return Rotation90.DEG_0

    def _vertical_pin_text_is_rotated(self, settings: "PinTextSettings | None") -> bool:
        """
        Determine native rotation for text attached to a vertical pin.

        Custom text anchored to the component uses the component orientation as
        the zero-degree reference, so unrotated components keep that text
        horizontal in native SVG.
        """
        if settings is None:
            return True
        if settings.position_mode == PinItemMode.DEFAULT:
            return True
        if (
            settings.position_mode == PinItemMode.CUSTOM
            and settings.rotation == Rotation90.DEG_0
        ):
            if settings.rotation_anchor == PinTextAnchor.COMPONENT:
                return self._get_parent_orientation() in (
                    Rotation90.DEG_90,
                    Rotation90.DEG_270,
                )
            return True
        return False

    @staticmethod
    def _rotate_schematic_delta(
        dx: float,
        dy: float,
        rotation: Rotation90,
    ) -> tuple[float, float]:
        """
        Rotate a schematic-space delta around the origin.
        """
        if rotation == Rotation90.DEG_0:
            return (dx, dy)
        if rotation == Rotation90.DEG_90:
            return (-dy, dx)
        if rotation == Rotation90.DEG_180:
            return (-dx, -dy)
        return (dy, -dx)

    def _get_painter_symbol_orientation(
        self,
        pin_orientation: Rotation90,
        symbol: IeeeSymbol,
    ) -> Rotation90:
        """
        Mirror Altium's painter-side symbol orientation rules.
        """
        if pin_orientation in (Rotation90.DEG_90, Rotation90.DEG_270):
            result = pin_orientation
        else:
            result = Rotation90.DEG_0

        if symbol == IeeeSymbol.DOT and pin_orientation == Rotation90.DEG_0:
            result = Rotation90.DEG_90

        if (
            symbol in (IeeeSymbol.DIGITAL_SIGNAL_IN, IeeeSymbol.SCHMITT)
            and pin_orientation == Rotation90.DEG_180
        ):
            result = Rotation90.DEG_180

        return result

    def _get_painter_symbol_mirror(
        self,
        pin_orientation: Rotation90,
        symbol: IeeeSymbol,
        symbol_orientation: Rotation90,
    ) -> bool:
        """
        Mirror Altium's painter-side symbol mirror rules.
        """
        if symbol == IeeeSymbol.SCHMITT:
            if symbol_orientation == Rotation90.DEG_180:
                return True

            return symbol_orientation == Rotation90.DEG_270

        return pin_orientation in (Rotation90.DEG_180, Rotation90.DEG_270)

    def _get_painter_symbol_offset(
        self,
        symbol: IeeeSymbol,
        pin_orientation: Rotation90,
    ) -> tuple[float, float] | None:
        """
        Return the painter-local symbol anchor offset in mils.
        """
        if symbol == IeeeSymbol.DOT:
            return (3.0, 0.0)
        if symbol == IeeeSymbol.ACTIVE_LOW_INPUT:
            return (0.0, 0.0)
        if symbol == IeeeSymbol.ACTIVE_LOW_OUTPUT:
            return (6.0, 0.0)
        if symbol == IeeeSymbol.CLOCK:
            return (-4.0, 0.0)
        if symbol == IeeeSymbol.POSTPONED_OUTPUT:
            return (-2.0, 0.0)
        if symbol in (
            IeeeSymbol.OPEN_COLLECTOR,
            IeeeSymbol.HIZ,
            IeeeSymbol.HIGH_CURRENT,
            IeeeSymbol.OPEN_COLLECTOR_PULL_UP,
            IeeeSymbol.OPEN_EMITTER,
            IeeeSymbol.OPEN_EMITTER_PULL_UP,
            IeeeSymbol.OPEN_CIRCUIT_OUTPUT,
        ):
            return (-4.0, 0.0)
        if symbol == IeeeSymbol.PULSE:
            return (-2.0, 0.0)
        if symbol == IeeeSymbol.SCHMITT:
            y_offset = -4.0
            if pin_orientation in (Rotation90.DEG_180, Rotation90.DEG_270):
                y_offset = -y_offset

            return (-18.0, y_offset)
        if symbol == IeeeSymbol.SHIFT_LEFT:
            return (-8.0, 0.0)
        if symbol == IeeeSymbol.RIGHT_LEFT_SIGNAL_FLOW:
            return (6.0, 0.0)
        if symbol in (IeeeSymbol.ANALOG_SIGNAL_IN, IeeeSymbol.DIGITAL_SIGNAL_IN):
            y_offset = 4.0
            if pin_orientation in (Rotation90.DEG_180, Rotation90.DEG_270):
                y_offset = -y_offset

            return (9.0, y_offset)
        if symbol == IeeeSymbol.NOT_LOGIC_CONNECTION:
            return (9.0, 0.0)
        if symbol == IeeeSymbol.LEFT_RIGHT_SIGNAL_FLOW:
            return (12.0, 0.0)
        if symbol == IeeeSymbol.BIDIRECTIONAL_SIGNAL_FLOW:
            return (6.0, 0.0)
        if symbol in (IeeeSymbol.INTERNAL_PULL_UP, IeeeSymbol.INTERNAL_PULL_DOWN):
            y_offset = -1.0
            if pin_orientation in (Rotation90.DEG_180, Rotation90.DEG_270):
                y_offset = -y_offset

            return (-6.0, y_offset)

        return None

    def _get_painter_symbol_location(
        self,
        body_end: tuple[float, float],
        pin_orientation: Rotation90,
        symbol: IeeeSymbol,
        role: str,
    ) -> tuple[float, float] | None:
        """
        Compute the final SVG anchor location for a painter-driven symbol.
        """
        offset = self._get_painter_symbol_offset(symbol, pin_orientation)
        if offset is None:
            return None

        pin_x_sch = body_end[0]
        pin_y_sch = -body_end[1]
        rotated_offset = self._rotate_schematic_delta(
            offset[0],
            offset[1],
            pin_orientation,
        )
        symbol_x_sch = pin_x_sch + rotated_offset[0]
        symbol_y_sch = pin_y_sch + rotated_offset[1]

        if (role == "inner" and self.symbol_inner_edge != IeeeSymbol.NONE) or (
            role == "outer" and self.symbol_outer_edge == IeeeSymbol.NONE
        ):
            shift_map = {
                Rotation90.DEG_0: (-6.0, 0.0),
                Rotation90.DEG_90: (0.0, -6.0),
                Rotation90.DEG_180: (6.0, 0.0),
                Rotation90.DEG_270: (0.0, 6.0),
            }
            shift_x, shift_y = shift_map[pin_orientation]
            symbol_x_sch += shift_x
            symbol_y_sch += shift_y

        return (symbol_x_sch, -symbol_y_sch)

    def _get_painter_symbol_polylines(
        self,
        symbol: IeeeSymbol,
        symbol_orientation: Rotation90,
    ) -> list[list[tuple[float, float]]] | None:
        """
        Return painter-local line geometry in schematic coordinates.
        """
        if symbol == IeeeSymbol.CLOCK:
            return [[(4.0, 2.0), (0.0, 0.0), (4.0, -2.0)]]
        if symbol == IeeeSymbol.ACTIVE_LOW_INPUT:
            return [[(0.0, 0.0), (6.0, 3.0), (6.0, 0.0), (0.0, 0.0)]]
        if symbol == IeeeSymbol.ACTIVE_LOW_OUTPUT:
            return [[(0.0, 0.0), (-6.0, 3.0)]]
        if symbol == IeeeSymbol.POSTPONED_OUTPUT:
            return [[(-4.0, 2.0), (0.0, 2.0), (0.0, -2.0)]]
        if symbol == IeeeSymbol.OPEN_COLLECTOR:
            return [
                [(2.0, 0.0), (0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)],
                [(-2.0, -2.0), (2.0, -2.0)],
            ]
        if symbol == IeeeSymbol.HIZ:
            return [[(2.0, 2.0), (-2.0, 2.0), (0.0, -2.0), (2.0, 2.0)]]
        if symbol == IeeeSymbol.HIGH_CURRENT:
            return [[(2.0, 0.0), (-2.0, 2.0), (-2.0, -2.0), (2.0, 0.0)]]
        if symbol == IeeeSymbol.PULSE:
            return [
                [
                    (0.0, 0.0),
                    (-4.0, 0.0),
                    (-4.0, 4.0),
                    (-8.0, 4.0),
                    (-8.0, 0.0),
                    (-12.0, 0.0),
                ]
            ]
        if symbol == IeeeSymbol.SCHMITT:
            if symbol_orientation in (Rotation90.DEG_0, Rotation90.DEG_90):
                return [
                    [
                        (0.0, 0.0),
                        (4.0, 1.0),
                        (4.0, 7.0),
                        (16.0, 8.0),
                        (12.0, 7.0),
                        (12.0, 1.0),
                        (0.0, 0.0),
                    ]
                ]
            if symbol_orientation == Rotation90.DEG_180:
                return [
                    [
                        (0.0, -8.0),
                        (-4.0, -7.0),
                        (-4.0, -1.0),
                        (-16.0, 0.0),
                        (-12.0, -1.0),
                        (-12.0, -7.0),
                        (0.0, -8.0),
                    ]
                ]
            return [
                [
                    (0.0, 8.0),
                    (4.0, 7.0),
                    (4.0, 1.0),
                    (16.0, 0.0),
                    (12.0, 1.0),
                    (12.0, 7.0),
                    (0.0, 8.0),
                ]
            ]
        if symbol == IeeeSymbol.OPEN_COLLECTOR_PULL_UP:
            return [
                [(2.0, 0.0), (0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)],
                [(-2.0, -2.0), (2.0, -2.0)],
                [(-2.0, 0.0), (2.0, 0.0)],
            ]
        if symbol == IeeeSymbol.OPEN_EMITTER:
            return [
                [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0), (0.0, 2.0)],
                [(-2.0, 2.0), (2.0, 2.0)],
            ]
        if symbol == IeeeSymbol.OPEN_EMITTER_PULL_UP:
            return [
                [(0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0), (0.0, 2.0)],
                [(-2.0, 2.0), (2.0, 2.0)],
                [(-2.0, 0.0), (2.0, 0.0)],
            ]
        if symbol == IeeeSymbol.SHIFT_LEFT:
            return [
                [
                    (0.0, 0.0),
                    (3.0, 0.0),
                    (3.0, 2.0),
                    (6.0, 0.0),
                    (3.0, -2.0),
                    (3.0, 0.0),
                ]
            ]
        if symbol == IeeeSymbol.OPEN_CIRCUIT_OUTPUT:
            return [[(2.0, 0.0), (0.0, 2.0), (-2.0, 0.0), (0.0, -2.0), (2.0, 0.0)]]
        if symbol == IeeeSymbol.INTERNAL_PULL_UP:
            return [
                [(-2.0, 4.0), (4.0, 4.0)],
                [
                    (1.0, 4.0),
                    (1.0, 3.0),
                    (3.0, 2.5),
                    (-1.0, 1.5),
                    (3.0, 0.5),
                    (-1.0, -0.5),
                    (1.0, -1.0),
                    (1.0, -2.0),
                ],
            ]
        if symbol == IeeeSymbol.INTERNAL_PULL_DOWN:
            return [
                [
                    (1.0, 5.0),
                    (1.0, 4.0),
                    (3.0, 3.5),
                    (-1.0, 2.5),
                    (3.0, 1.5),
                    (-1.0, 0.5),
                    (1.0, 0.0),
                    (1.0, -1.0),
                ],
                [(-2.0, -1.0), (4.0, -1.0)],
                [(-1.0, -2.0), (3.0, -2.0)],
                [(0.0, -3.0), (2.0, -3.0)],
            ]
        if symbol == IeeeSymbol.DIGITAL_SIGNAL_IN:
            if symbol_orientation in (Rotation90.DEG_0, Rotation90.DEG_90):
                return [
                    [(-2.0, 0.0), (3.0, 0.0)],
                    [(0.0, 2.0), (5.0, 2.0)],
                    [(-1.0, -1.0), (2.0, 3.0)],
                    [(1.0, -1.0), (4.0, 3.0)],
                ]

            return [
                [(2.0, -1.0), (-3.0, -1.0)],
                [(0.0, 1.0), (-5.0, 1.0)],
                [(1.0, -2.0), (-2.0, 2.0)],
                [(-1.0, -2.0), (-4.0, 2.0)],
            ]
        if symbol == IeeeSymbol.NOT_LOGIC_CONNECTION:
            return [
                [(-2.0, -2.0), (2.0, 2.0)],
                [(-2.0, 2.0), (2.0, -2.0)],
            ]

        return None

    def _transform_painter_symbol_point(
        self,
        symbol_location: tuple[float, float],
        pin_orientation: Rotation90,
        symbol_orientation: Rotation90,
        symbol_mirrored: bool,
        point: tuple[float, float],
    ) -> tuple[float, float]:
        """
        Transform a painter-local schematic point into SVG coordinates.
        """
        dx, dy = point

        if symbol_mirrored:
            if pin_orientation == Rotation90.DEG_270:
                dy = -dy
            else:
                dx = -dx

        dx, dy = self._rotate_schematic_delta(dx, dy, symbol_orientation)

        return (symbol_location[0] + dx, symbol_location[1] - dy)

    def _render_exact_painter_symbol(
        self,
        body_end: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
        symbol: IeeeSymbol,
        role: str,
    ) -> list[str] | None:
        """
        Render a line-based IEEE symbol using painter-derived geometry.
        """
        pin_orientation = self._get_effective_orientation(ctx)
        symbol_orientation = self._get_painter_symbol_orientation(
            pin_orientation,
            symbol,
        )
        symbol_mirrored = self._get_painter_symbol_mirror(
            pin_orientation,
            symbol,
            symbol_orientation,
        )
        symbol_location = self._get_painter_symbol_location(
            body_end,
            pin_orientation,
            symbol,
            role,
        )
        polylines = self._get_painter_symbol_polylines(
            symbol,
            symbol_orientation,
        )
        if symbol_location is None or polylines is None:
            return None

        elements: list[str] = []
        for polyline in polylines:
            transformed_points = [
                self._transform_painter_symbol_point(
                    symbol_location,
                    pin_orientation,
                    symbol_orientation,
                    symbol_mirrored,
                    point,
                )
                for point in polyline
            ]
            for start, end in zip(
                transformed_points,
                transformed_points[1:],
                strict=False,
            ):
                elements.append(
                    self._make_symbol_line(
                        start[0],
                        start[1],
                        end[0],
                        end[1],
                        stroke,
                    )
                )

        return elements

    def _render_inner_symbol(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render inner symbol (inside the component body).

        Inner symbols include: POSTPONED_OUTPUT, OPEN_COLLECTOR, HIZ,
        HIGH_CURRENT, PULSE, SCHMITT, OPEN_COLLECTOR_PULL_UP, OPEN_EMITTER,
        OPEN_EMITTER_PULL_UP, SHIFT_LEFT, OPEN_CIRCUIT_OUTPUT,
        INTERNAL_PULL_UP, INTERNAL_PULL_DOWN

        Args:
            body_end: Where pin connects to symbol body
            stroke: Stroke color
            ctx: Render context

        Returns:
            List of SVG line elements
        """
        sym = self.symbol_inner
        exact_elements = self._render_exact_painter_symbol(
            body_end,
            stroke,
            ctx,
            sym,
            "inner",
        )
        if exact_elements is not None:
            return exact_elements

        x, y = body_end
        elements = []

        if self.orientation == Rotation90.DEG_0:
            cx, cy = x - INNER_SYMBOL_OFFSET, y
        elif self.orientation == Rotation90.DEG_90:
            cx, cy = x, y + INNER_SYMBOL_OFFSET
        elif self.orientation == Rotation90.DEG_180:
            cx, cy = x + INNER_SYMBOL_OFFSET, y
        elif self.orientation == Rotation90.DEG_270:
            cx, cy = x, y - INNER_SYMBOL_OFFSET
        else:
            return []

        if sym == IeeeSymbol.POSTPONED_OUTPUT:
            elements.extend(self._draw_postponed_output(cx, cy, stroke))
        elif sym == IeeeSymbol.OPEN_COLLECTOR:
            elements.extend(self._draw_open_collector(cx, cy, stroke, has_bottom=True))
        elif sym == IeeeSymbol.HIZ:
            elements.extend(self._draw_hiz(cx, cy, stroke))
        elif sym == IeeeSymbol.HIGH_CURRENT:
            elements.extend(self._draw_high_current(cx, cy, stroke))
        elif sym == IeeeSymbol.PULSE:
            elements.extend(self._draw_pulse(cx, cy, stroke))
        elif sym == IeeeSymbol.SCHMITT:
            elements.extend(self._draw_schmitt(cx, cy, stroke))
        elif sym == IeeeSymbol.OPEN_COLLECTOR_PULL_UP:
            elements.extend(
                self._draw_open_collector(
                    cx, cy, stroke, has_bottom=True, has_middle=True
                )
            )
        elif sym == IeeeSymbol.OPEN_EMITTER:
            elements.extend(self._draw_open_emitter(cx, cy, stroke, has_top=True))
        elif sym == IeeeSymbol.OPEN_EMITTER_PULL_UP:
            elements.extend(
                self._draw_open_emitter(cx, cy, stroke, has_top=True, has_middle=True)
            )
        elif sym == IeeeSymbol.SHIFT_LEFT:
            elements.extend(self._draw_shift_left(cx, cy, stroke))
        elif sym == IeeeSymbol.OPEN_CIRCUIT_OUTPUT:
            elements.extend(self._draw_diamond(cx, cy, stroke))
        elif sym == IeeeSymbol.INTERNAL_PULL_UP:
            elements.extend(self._draw_internal_pull_up(cx, cy, stroke))
        elif sym == IeeeSymbol.INTERNAL_PULL_DOWN:
            elements.extend(self._draw_internal_pull_down(cx, cy, stroke))
        # Handle edge symbols that can also appear in symbol_inner slot
        # These are rendered at body_end (x, y), not at the offset position
        elif sym == IeeeSymbol.DOT:
            elements.extend(self._draw_dot_inner_edge(x, y, stroke))
        elif sym == IeeeSymbol.CLOCK:
            elements.extend(self._draw_clock(x, y, stroke))
        elif sym == IeeeSymbol.ACTIVE_LOW_INPUT:
            elements.extend(self._draw_active_low_input(x, y, stroke))

        return elements

    def _render_inner_edge_symbol(
        self, body_end: tuple[float, float], stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Render inner edge symbol (at the body edge).

        Inner edge symbols include: CLOCK, DOT (when at inner edge), SCHMITT

        Args:
            body_end: Where pin connects to symbol body
            stroke: Stroke color
            ctx: Render context

        Returns:
            List of SVG elements
        """
        sym = self.symbol_inner_edge
        exact_elements = self._render_exact_painter_symbol(
            body_end,
            stroke,
            ctx,
            sym,
            "inner_edge",
        )
        if exact_elements is not None:
            return exact_elements

        x, y = body_end
        elements = []

        if sym == IeeeSymbol.CLOCK:
            elements.extend(self._draw_clock(x, y, stroke))
        elif sym == IeeeSymbol.DOT:
            elements.extend(self._draw_dot_inner_edge(x, y, stroke))
        elif sym == IeeeSymbol.SCHMITT:
            # Inner edge Schmitt is same as inner Schmitt
            cx, cy = x, y
            if self.orientation == Rotation90.DEG_0:
                cx = x - INNER_SYMBOL_OFFSET
            elif self.orientation == Rotation90.DEG_90:
                cy = y + INNER_SYMBOL_OFFSET
            elif self.orientation == Rotation90.DEG_180:
                cx = x + INNER_SYMBOL_OFFSET
            elif self.orientation == Rotation90.DEG_270:
                cy = y - INNER_SYMBOL_OFFSET
            elements.extend(self._draw_schmitt(cx, cy, stroke))

        return elements

    def _render_outer_edge_symbol(
        self,
        body_end: tuple[float, float],
        hot_spot: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> list[str]:
        """
        Render outer edge symbol (near the symbol body edge).

        NOTE: Despite the name "outer edge", these symbols are drawn at body_end
        (the symbol body edge), not at hot_spot (wire connection point).
        The pin line still extends from body_end to hot_spot, and the symbol
        background covers the overlapping portion.

        Outer edge symbols include: DOT, ACTIVE_LOW_INPUT, ACTIVE_LOW_OUTPUT

        Args:
            body_end: Where pin connects to symbol body (symbol position)
            hot_spot: Where wire connects (not used for positioning)
            stroke: Stroke color
            ctx: Render context

        Returns:
            List of SVG elements
        """
        sym = self.symbol_outer_edge
        exact_elements = self._render_exact_painter_symbol(
            body_end,
            stroke,
            ctx,
            sym,
            "outer_edge",
        )
        if exact_elements is not None:
            return exact_elements

        x, y = body_end
        elements = []

        if sym == IeeeSymbol.DOT:
            elements.extend(self._draw_dot_outer_edge(x, y, stroke))
        elif sym == IeeeSymbol.ACTIVE_LOW_INPUT:
            elements.extend(self._draw_active_low_input(x, y, stroke))
        elif sym == IeeeSymbol.ACTIVE_LOW_OUTPUT:
            elements.extend(self._draw_active_low_output(x, y, stroke))

        return elements

    def _render_outer_symbol(
        self,
        body_end: tuple[float, float],
        hot_spot: tuple[float, float],
        stroke: str,
        ctx: SchSvgRenderContext,
    ) -> list[str]:
        """
        Render outer symbol (near the symbol body edge).

        NOTE: Despite the name "outer", these symbols are drawn at body_end,
        not at hot_spot. They overlay the pin line near the body.

        Outer symbols include: RIGHT_LEFT_SIGNAL_FLOW, LEFT_RIGHT_SIGNAL_FLOW,
        BIDIRECTIONAL_SIGNAL_FLOW, ANALOG_SIGNAL_IN, DIGITAL_SIGNAL_IN,
        NOT_LOGIC_CONNECTION

        Args:
            body_end: Where pin connects to symbol body (symbol position)
            hot_spot: Where wire connects (not used for positioning)
            stroke: Stroke color
            ctx: Render context

        Returns:
            List of SVG elements
        """
        sym = self.symbol_outer
        exact_elements = self._render_exact_painter_symbol(
            body_end,
            stroke,
            ctx,
            sym,
            "outer",
        )
        if exact_elements is not None:
            return exact_elements

        x, y = body_end
        elements = []

        if sym == IeeeSymbol.RIGHT_LEFT_SIGNAL_FLOW:
            elements.extend(self._draw_right_left_signal_flow(x, y, stroke, ctx))
        elif sym == IeeeSymbol.LEFT_RIGHT_SIGNAL_FLOW:
            elements.extend(self._draw_left_right_signal_flow(x, y, stroke, ctx))
        elif sym == IeeeSymbol.BIDIRECTIONAL_SIGNAL_FLOW:
            elements.extend(self._draw_bidirectional_signal_flow(x, y, stroke, ctx))
        elif sym == IeeeSymbol.ANALOG_SIGNAL_IN:
            elements.extend(self._draw_analog_signal_in(x, y, stroke))
        elif sym == IeeeSymbol.DIGITAL_SIGNAL_IN:
            elements.extend(self._draw_digital_signal_in(x, y, stroke))
        elif sym == IeeeSymbol.NOT_LOGIC_CONNECTION:
            elements.extend(self._draw_not_logic_connection(x, y, stroke))

        return elements

    # =========================================================================
    # Symbol Drawing Primitives
    # =========================================================================

    def _make_symbol_line(
        self, x1: float, y1: float, x2: float, y2: float, stroke: str
    ) -> str:
        """
        Create a hairline SVG line for symbol drawing.
        """
        return svg_line(
            x1,
            y1,
            x2,
            y2,
            stroke=stroke,
            stroke_width=SYMBOL_STROKE_WIDTH,
            vector_effect="non-scaling-stroke",
        )

    def _get_inner_symbol_orientation(self) -> Rotation90:
        """
        Return the native visual orientation for inner IEEE symbols.
        """
        if self.orientation == Rotation90.DEG_90:
            return Rotation90.DEG_270
        if self.orientation == Rotation90.DEG_270:
            return Rotation90.DEG_90
        return self.orientation

    def _draw_postponed_output(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw POSTPONED_OUTPUT symbol (L-shape).
        """
        orientation = self._get_inner_symbol_orientation()

        if orientation == Rotation90.DEG_0:
            # Right-pointing pin: L-shape opening to the left
            return [
                self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy - 2, stroke),
                self._make_symbol_line(cx + 2, cy - 2, cx + 2, cy + 2, stroke),
            ]
        elif orientation == Rotation90.DEG_90:
            # Up-pointing pin: L-shape opening downward
            return [
                self._make_symbol_line(cx + 2, cy - 2, cx + 2, cy + 2, stroke),
                self._make_symbol_line(cx + 2, cy + 2, cx - 2, cy + 2, stroke),
            ]
        elif orientation == Rotation90.DEG_180:
            # Left-pointing pin: L-shape opening to the right
            return [
                self._make_symbol_line(cx + 2, cy + 2, cx - 2, cy + 2, stroke),
                self._make_symbol_line(cx - 2, cy + 2, cx - 2, cy - 2, stroke),
            ]
        elif orientation == Rotation90.DEG_270:
            # Down-pointing pin: L-shape opening upward
            return [
                self._make_symbol_line(cx - 2, cy + 2, cx - 2, cy - 2, stroke),
                self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy - 2, stroke),
            ]
        return []

    def _draw_diamond(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw a diamond shape (used for OPEN_CIRCUIT_OUTPUT).
        """
        return [
            self._make_symbol_line(cx + 2, cy, cx, cy - 2, stroke),
            self._make_symbol_line(cx, cy - 2, cx - 2, cy, stroke),
            self._make_symbol_line(cx - 2, cy, cx, cy + 2, stroke),
            self._make_symbol_line(cx, cy + 2, cx + 2, cy, stroke),
        ]

    def _draw_open_collector(
        self,
        cx: float,
        cy: float,
        stroke: str,
        has_bottom: bool = False,
        has_middle: bool = False,
    ) -> list[str]:
        """
        Draw OPEN_COLLECTOR symbol (diamond with optional lines).
        """
        elements = self._draw_diamond(cx, cy, stroke)

        if has_bottom:
            # Bottom line below diamond
            elements.append(
                self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy + 2, stroke)
            )

        if has_middle:
            # Middle line through diamond
            elements.append(self._make_symbol_line(cx - 2, cy, cx + 2, cy, stroke))

        return elements

    def _draw_open_emitter(
        self,
        cx: float,
        cy: float,
        stroke: str,
        has_top: bool = False,
        has_middle: bool = False,
    ) -> list[str]:
        """
        Draw OPEN_EMITTER symbol (diamond with top line).

        Similar to open collector but with line on top instead of bottom.
        """
        elements = self._draw_diamond(cx, cy, stroke)

        if has_top:
            # Top line above diamond
            elements.append(
                self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy - 2, stroke)
            )

        if has_middle:
            # Middle line through diamond
            elements.append(self._make_symbol_line(cx - 2, cy, cx + 2, cy, stroke))

        return elements

    def _draw_hiz(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw HIZ symbol (triangle pointing down).
        """
        return [
            self._make_symbol_line(cx + 2, cy - 2, cx - 2, cy - 2, stroke),
            self._make_symbol_line(cx - 2, cy - 2, cx, cy + 2, stroke),
            self._make_symbol_line(cx, cy + 2, cx + 2, cy - 2, stroke),
        ]

    def _draw_high_current(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw HIGH_CURRENT symbol (triangle pointing right).
        """
        orientation = self._get_inner_symbol_orientation()

        if orientation == Rotation90.DEG_0:
            # Right-pointing pin: arrow points right (toward body which is left)
            return [
                self._make_symbol_line(cx + 2, cy, cx - 2, cy - 2, stroke),
                self._make_symbol_line(cx - 2, cy - 2, cx - 2, cy + 2, stroke),
                self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_90:
            # Up-pointing pin: arrow points down
            return [
                self._make_symbol_line(cx, cy + 2, cx + 2, cy - 2, stroke),
                self._make_symbol_line(cx + 2, cy - 2, cx - 2, cy - 2, stroke),
                self._make_symbol_line(cx - 2, cy - 2, cx, cy + 2, stroke),
            ]
        elif orientation == Rotation90.DEG_180:
            # Left-pointing pin: arrow points left
            return [
                self._make_symbol_line(cx - 2, cy, cx + 2, cy + 2, stroke),
                self._make_symbol_line(cx + 2, cy + 2, cx + 2, cy - 2, stroke),
                self._make_symbol_line(cx + 2, cy - 2, cx - 2, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_270:
            # Down-pointing pin: arrow points up
            return [
                self._make_symbol_line(cx, cy - 2, cx - 2, cy + 2, stroke),
                self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy + 2, stroke),
                self._make_symbol_line(cx + 2, cy + 2, cx, cy - 2, stroke),
            ]
        return []

    def _draw_pulse(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw PULSE symbol (staircase/step pattern).
        """
        orientation = self._get_inner_symbol_orientation()

        if orientation == Rotation90.DEG_0:
            return [
                self._make_symbol_line(cx + 2, cy, cx - 2, cy, stroke),
                self._make_symbol_line(cx - 2, cy, cx - 2, cy - 4, stroke),
                self._make_symbol_line(cx - 2, cy - 4, cx - 6, cy - 4, stroke),
                self._make_symbol_line(cx - 6, cy - 4, cx - 6, cy, stroke),
                self._make_symbol_line(cx - 6, cy, cx - 10, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_90:
            return [
                self._make_symbol_line(cx, cy + 2, cx, cy - 2, stroke),
                self._make_symbol_line(cx, cy - 2, cx + 4, cy - 2, stroke),
                self._make_symbol_line(cx + 4, cy - 2, cx + 4, cy - 6, stroke),
                self._make_symbol_line(cx + 4, cy - 6, cx, cy - 6, stroke),
                self._make_symbol_line(cx, cy - 6, cx, cy - 10, stroke),
            ]
        elif orientation == Rotation90.DEG_180:
            return [
                self._make_symbol_line(cx - 2, cy, cx + 2, cy, stroke),
                self._make_symbol_line(cx + 2, cy, cx + 2, cy + 4, stroke),
                self._make_symbol_line(cx + 2, cy + 4, cx + 6, cy + 4, stroke),
                self._make_symbol_line(cx + 6, cy + 4, cx + 6, cy, stroke),
                self._make_symbol_line(cx + 6, cy, cx + 10, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_270:
            return [
                self._make_symbol_line(cx, cy - 2, cx, cy + 2, stroke),
                self._make_symbol_line(cx, cy + 2, cx - 4, cy + 2, stroke),
                self._make_symbol_line(cx - 4, cy + 2, cx - 4, cy + 6, stroke),
                self._make_symbol_line(cx - 4, cy + 6, cx, cy + 6, stroke),
                self._make_symbol_line(cx, cy + 6, cx, cy + 10, stroke),
            ]
        return []

    def _draw_schmitt(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw SCHMITT trigger symbol (hysteresis loop).
        """
        orientation = self._get_inner_symbol_orientation()

        if orientation in (Rotation90.DEG_0, Rotation90.DEG_90):
            # Pointing right/up - standard orientation
            return [
                self._make_symbol_line(cx - 14, cy + 4, cx - 10, cy + 3, stroke),
                self._make_symbol_line(cx - 10, cy + 3, cx - 10, cy - 3, stroke),
                self._make_symbol_line(cx - 10, cy - 3, cx + 2, cy - 4, stroke),
                self._make_symbol_line(cx + 2, cy - 4, cx - 2, cy - 3, stroke),
                self._make_symbol_line(cx - 2, cy - 3, cx - 2, cy + 3, stroke),
                self._make_symbol_line(cx - 2, cy + 3, cx - 14, cy + 4, stroke),
            ]
        else:
            # Pointing left/down - mirrored orientation
            return [
                self._make_symbol_line(cx + 14, cy - 4, cx + 10, cy - 3, stroke),
                self._make_symbol_line(cx + 10, cy - 3, cx + 10, cy + 3, stroke),
                self._make_symbol_line(cx + 10, cy + 3, cx - 2, cy + 4, stroke),
                self._make_symbol_line(cx - 2, cy + 4, cx + 2, cy + 3, stroke),
                self._make_symbol_line(cx + 2, cy + 3, cx + 2, cy - 3, stroke),
                self._make_symbol_line(cx + 2, cy - 3, cx + 14, cy - 4, stroke),
            ]

    def _draw_shift_left(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw SHIFT_LEFT symbol (left-pointing chevron with line).
        """
        orientation = self._get_inner_symbol_orientation()

        if orientation == Rotation90.DEG_0:
            return [
                self._make_symbol_line(cx + 2, cy, cx - 1, cy, stroke),
                self._make_symbol_line(cx - 1, cy, cx - 1, cy - 2, stroke),
                self._make_symbol_line(cx - 1, cy - 2, cx - 4, cy, stroke),
                self._make_symbol_line(cx - 4, cy, cx - 1, cy + 2, stroke),
                self._make_symbol_line(cx - 1, cy + 2, cx - 1, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_90:
            return [
                self._make_symbol_line(cx, cy + 2, cx, cy - 1, stroke),
                self._make_symbol_line(cx, cy - 1, cx + 2, cy - 1, stroke),
                self._make_symbol_line(cx + 2, cy - 1, cx, cy - 4, stroke),
                self._make_symbol_line(cx, cy - 4, cx - 2, cy - 1, stroke),
                self._make_symbol_line(cx - 2, cy - 1, cx, cy - 1, stroke),
            ]
        elif orientation == Rotation90.DEG_180:
            return [
                self._make_symbol_line(cx - 2, cy, cx + 1, cy, stroke),
                self._make_symbol_line(cx + 1, cy, cx + 1, cy + 2, stroke),
                self._make_symbol_line(cx + 1, cy + 2, cx + 4, cy, stroke),
                self._make_symbol_line(cx + 4, cy, cx + 1, cy - 2, stroke),
                self._make_symbol_line(cx + 1, cy - 2, cx + 1, cy, stroke),
            ]
        elif orientation == Rotation90.DEG_270:
            return [
                self._make_symbol_line(cx, cy - 2, cx, cy + 1, stroke),
                self._make_symbol_line(cx, cy + 1, cx - 2, cy + 1, stroke),
                self._make_symbol_line(cx - 2, cy + 1, cx, cy + 4, stroke),
                self._make_symbol_line(cx, cy + 4, cx + 2, cy + 1, stroke),
                self._make_symbol_line(cx + 2, cy + 1, cx, cy + 1, stroke),
            ]
        return []

    def _draw_internal_pull_up(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw INTERNAL_PULL_UP symbol (resistor with top line).
        """
        # Horizontal line at top, then resistor zigzag going down
        return [
            self._make_symbol_line(cx - 2, cy - 4, cx + 4, cy - 4, stroke),  # Top line
            self._make_symbol_line(cx + 1, cy - 4, cx + 1, cy - 3, stroke),
            self._make_symbol_line(cx + 1, cy - 3, cx + 3, cy - 2.5, stroke),
            self._make_symbol_line(cx + 3, cy - 2.5, cx - 1, cy - 1.5, stroke),
            self._make_symbol_line(cx - 1, cy - 1.5, cx + 3, cy - 0.5, stroke),
            self._make_symbol_line(cx + 3, cy - 0.5, cx - 1, cy + 0.5, stroke),
            self._make_symbol_line(cx - 1, cy + 0.5, cx + 1, cy + 1, stroke),
            self._make_symbol_line(cx + 1, cy + 1, cx + 1, cy + 2, stroke),
        ]

    def _draw_internal_pull_down(self, cx: float, cy: float, stroke: str) -> list[str]:
        """
        Draw INTERNAL_PULL_DOWN symbol (resistor with ground lines).
        """
        # Resistor zigzag then ground symbol at bottom
        return [
            self._make_symbol_line(cx + 1, cy - 5, cx + 1, cy - 4, stroke),
            self._make_symbol_line(cx + 1, cy - 4, cx + 3, cy - 3.5, stroke),
            self._make_symbol_line(cx + 3, cy - 3.5, cx - 1, cy - 2.5, stroke),
            self._make_symbol_line(cx - 1, cy - 2.5, cx + 3, cy - 1.5, stroke),
            self._make_symbol_line(cx + 3, cy - 1.5, cx - 1, cy - 0.5, stroke),
            self._make_symbol_line(cx - 1, cy - 0.5, cx + 1, cy, stroke),
            self._make_symbol_line(cx + 1, cy, cx + 1, cy + 1, stroke),
            # Ground symbol (three lines)
            self._make_symbol_line(cx - 2, cy + 1, cx + 4, cy + 1, stroke),
            self._make_symbol_line(cx - 1, cy + 2, cx + 3, cy + 2, stroke),
            self._make_symbol_line(cx, cy + 3, cx + 2, cy + 3, stroke),
        ]

    def _draw_clock(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw CLOCK symbol (chevron at body edge).
        """
        if self.orientation == Rotation90.DEG_0:
            # Right-pointing pin: chevron opens to the right
            return [
                self._make_symbol_line(x, y - 2, x - 4, y, stroke),
                self._make_symbol_line(x - 4, y, x, y + 2, stroke),
            ]
        elif self.orientation == Rotation90.DEG_90:
            # Up-pointing pin: chevron opens upward
            return [
                self._make_symbol_line(x - 2, y, x, y + 4, stroke),
                self._make_symbol_line(x, y + 4, x + 2, y, stroke),
            ]
        elif self.orientation == Rotation90.DEG_180:
            # Left-pointing pin: chevron opens to the left
            return [
                self._make_symbol_line(x, y + 2, x + 4, y, stroke),
                self._make_symbol_line(x + 4, y, x, y - 2, stroke),
            ]
        elif self.orientation == Rotation90.DEG_270:
            # Down-pointing pin: chevron opens downward
            return [
                self._make_symbol_line(x + 2, y, x, y - 4, stroke),
                self._make_symbol_line(x, y - 4, x - 2, y, stroke),
            ]
        return []

    def _draw_dot(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw DOT symbol (inversion circle at outer edge).
        """
        r = 3  # Dot radius
        # Position dot centered at edge, shifted away from body
        if self.orientation == Rotation90.DEG_0:
            cx, cy = x + r, y
        elif self.orientation == Rotation90.DEG_90:
            cx, cy = x, y - r
        elif self.orientation == Rotation90.DEG_180:
            cx, cy = x - r, y
        elif self.orientation == Rotation90.DEG_270:
            cx, cy = x, y + r
        else:
            cx, cy = x, y

        return [
            # Background fill (rounded rect approximating circle)
            svg_rect(
                cx - r,
                cy - r,
                r * 2,
                r * 2,
                rx=r,
                ry=r,
                stroke="",
                stroke_width=0,
                fill=SYMBOL_FILL_COLOR,
            ),
            # Outline ellipse
            svg_ellipse(
                cx,
                cy,
                r,
                r,
                stroke=stroke,
                stroke_width=SYMBOL_STROKE_WIDTH,
                fill="none",
                vector_effect="non-scaling-stroke",
            ),
        ]

    def _draw_edge_dot(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw DOT at the body boundary for either edge-symbol slot.
        """
        r = 3  # Dot radius
        # Position dot starting at body_end, extending toward hot_spot
        if self.orientation == Rotation90.DEG_0:
            # Right-pointing pin: dot at body_end, extending right
            cx, cy = x + r, y
            rx, ry = x, y - r
        elif self.orientation == Rotation90.DEG_90:
            # Up-pointing pin: dot at body_end, extending up
            cx, cy = x, y - r
            rx, ry = x - r, y - r * 2
        elif self.orientation == Rotation90.DEG_180:
            # Left-pointing pin: dot at body_end, extending left
            cx, cy = x - r, y
            rx, ry = x - r * 2, y - r
        elif self.orientation == Rotation90.DEG_270:
            # Down-pointing pin: dot at body_end, extending down
            cx, cy = x, y + r
            rx, ry = x - r, y
        else:
            cx, cy = x, y
            rx, ry = x - r, y - r

        return [
            svg_rect(
                rx,
                ry,
                r * 2,
                r * 2,
                rx=r,
                ry=r,
                stroke=None,
                stroke_width=None,
                fill=SYMBOL_FILL_COLOR,
                fill_opacity=1,
            ),
            svg_ellipse(
                cx,
                cy,
                r,
                r,
                stroke=stroke,
                stroke_width=SYMBOL_STROKE_WIDTH,
                fill="none",
                vector_effect="non-scaling-stroke",
            ),
        ]

    _draw_dot_inner_edge = _draw_edge_dot
    _draw_dot_outer_edge = _draw_edge_dot

    def _draw_active_low_input(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw ACTIVE_LOW_INPUT symbol (closed triangle outline).

        Small triangle indicating active-low input.
        Native SVG uses 3 lines to form a closed triangle.
        """
        if self.orientation == Rotation90.DEG_0:
            return [
                self._make_symbol_line(x, y, x + 6, y + 3, stroke),
                self._make_symbol_line(x + 6, y + 3, x + 6, y, stroke),
                self._make_symbol_line(x + 6, y, x, y, stroke),  # Close triangle
            ]
        elif self.orientation == Rotation90.DEG_90:
            return [
                self._make_symbol_line(x, y, x - 3, y - 6, stroke),
                self._make_symbol_line(x - 3, y - 6, x, y - 6, stroke),
                self._make_symbol_line(x, y - 6, x, y, stroke),  # Close triangle
            ]
        elif self.orientation == Rotation90.DEG_180:
            return [
                self._make_symbol_line(x, y, x - 6, y - 3, stroke),
                self._make_symbol_line(x - 6, y - 3, x - 6, y, stroke),
                self._make_symbol_line(x - 6, y, x, y, stroke),  # Close triangle
            ]
        elif self.orientation == Rotation90.DEG_270:
            return [
                self._make_symbol_line(x, y, x + 3, y + 6, stroke),
                self._make_symbol_line(x + 3, y + 6, x, y + 6, stroke),
                self._make_symbol_line(x, y + 6, x, y, stroke),  # Close triangle
            ]
        return []

    def _draw_active_low_output(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw ACTIVE_LOW_OUTPUT symbol (diagonal line).

        Single diagonal line indicating active-low output.
        """
        if self.orientation == Rotation90.DEG_0:
            return [self._make_symbol_line(x, y, x - 6, y + 3, stroke)]
        elif self.orientation == Rotation90.DEG_90:
            return [self._make_symbol_line(x, y, x - 3, y + 6, stroke)]
        elif self.orientation == Rotation90.DEG_180:
            return [self._make_symbol_line(x, y, x + 6, y - 3, stroke)]
        elif self.orientation == Rotation90.DEG_270:
            return [self._make_symbol_line(x, y, x + 3, y - 6, stroke)]
        return []

    def _draw_right_left_signal_flow(
        self, x: float, y: float, stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Draw RIGHT_LEFT_SIGNAL_FLOW symbol (input arrow).

        Filled triangle pointing toward body (input indication).
        Native Altium renders points in consistent winding order:
        - Horizontal arrows: Y ascending (smallest Y first)
        - Vertical arrows: X ascending (smallest X first)
        - Base offset: +6 pixels toward hot_spot
        - DrawSymbolOuter: when NO outer_edge, shift -6 toward body; when HAS outer_edge, no shift
        - Net: HAS outer_edge = +6, NO outer_edge = 0
        """
        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when NO outer_edge, shift -6; when HAS outer_edge, no shift
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        if self.orientation == Rotation90.DEG_0:
            # Arrow pointing left, base at right: (base-top, tip, base-bottom)
            points = [
                (x + 6 + outer_offset, y - 2),
                (x + outer_offset, y),
                (x + 6 + outer_offset, y + 2),
            ]
        elif self.orientation == Rotation90.DEG_90:
            # Arrow pointing down, base at top: (base-left, tip, base-right)
            points = [
                (x - 2, y - 6 - outer_offset),
                (x, y - outer_offset),
                (x + 2, y - 6 - outer_offset),
            ]
        elif self.orientation == Rotation90.DEG_180:
            # Arrow pointing right, base at left: (base-top, tip, base-bottom)
            points = [
                (x - 6 - outer_offset, y - 2),
                (x - outer_offset, y),
                (x - 6 - outer_offset, y + 2),
            ]
        elif self.orientation == Rotation90.DEG_270:
            # Arrow pointing up, base at bottom: (base-left, tip, base-right)
            points = [
                (x - 2, y + 6 + outer_offset),
                (x, y + outer_offset),
                (x + 2, y + 6 + outer_offset),
            ]
        else:
            return []
        return self._make_arrow_polygons(points, stroke, ctx)

    def _draw_left_right_signal_flow(
        self, x: float, y: float, stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Draw LEFT_RIGHT_SIGNAL_FLOW symbol (output arrow).

        Filled triangle pointing away from body (output indication).
        Native Altium renders points in consistent winding order:
        - Horizontal arrows: (tip, base-top, base-bottom) - Y ascending for base
        - Vertical arrows: (tip, base-left, base-right) - X ascending for base
        - Base offset: +12 pixels toward hot_spot (LRS is further out than RLS)
        - DrawSymbolOuter: when NO outer_edge, shift -6 toward body; when HAS outer_edge, no shift
        - Net: HAS outer_edge = +12, NO outer_edge = +6
        """
        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when NO outer_edge, shift -6; when HAS outer_edge, no shift
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0
        base_offset = 6  # LRS base position is +6 from body_end

        if self.orientation == Rotation90.DEG_0:
            ox = x + base_offset + outer_offset
            # Right-pointing arrow: tip right, base left - (tip, base-top, base-bottom)
            points = [(ox, y), (ox - 6, y - 2), (ox - 6, y + 2)]
        elif self.orientation == Rotation90.DEG_90:
            oy = y - base_offset - outer_offset
            # Up-pointing arrow: tip top, base bottom - (tip, base-left, base-right)
            points = [(x, oy), (x - 2, oy + 6), (x + 2, oy + 6)]
        elif self.orientation == Rotation90.DEG_180:
            ox = x - base_offset - outer_offset
            # Left-pointing arrow: tip left, base right - (tip, base-top, base-bottom)
            points = [(ox, y), (ox + 6, y - 2), (ox + 6, y + 2)]
        elif self.orientation == Rotation90.DEG_270:
            oy = y + base_offset + outer_offset
            # Down-pointing arrow: tip bottom, base top - (tip, base-left, base-right)
            points = [(x, oy), (x - 2, oy - 6), (x + 2, oy - 6)]
        else:
            return []
        return self._make_arrow_polygons(points, stroke, ctx)

    def _draw_bidirectional_signal_flow(
        self, x: float, y: float, stroke: str, ctx: SchSvgRenderContext
    ) -> list[str]:
        """
        Draw BIDIRECTIONAL_SIGNAL_FLOW symbol (two arrows).

        Two triangles pointing in opposite directions.
        Native Altium renders with consistent winding order:
        - Input arrow (points1): same order as _draw_right_left_signal_flow
        - Output arrow (points2): (base-top/left, base-bottom/right, tip)
        - Base offset: +6 pixels toward hot_spot
        - DrawSymbolOuter: when NO outer_edge, shift -6 toward body; when HAS outer_edge, no shift
        - Net: HAS outer_edge = +6, NO outer_edge = 0
        """
        # Offset adjustment based on outer_edge symbol presence
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        elements = []
        if self.orientation == Rotation90.DEG_0:
            # Input arrow pointing left, output arrow pointing right
            points1 = [
                (x + 6 + outer_offset, y - 2),
                (x + outer_offset, y),
                (x + 6 + outer_offset, y + 2),
            ]
            points2 = [
                (x + 7 + outer_offset, y - 2),
                (x + 7 + outer_offset, y + 2),
                (x + 13 + outer_offset, y),
            ]
        elif self.orientation == Rotation90.DEG_90:
            # Input arrow pointing down, output arrow pointing up
            points1 = [
                (x - 2, y - 6 - outer_offset),
                (x, y - outer_offset),
                (x + 2, y - 6 - outer_offset),
            ]
            points2 = [
                (x - 2, y - 7 - outer_offset),
                (x + 2, y - 7 - outer_offset),
                (x, y - 13 - outer_offset),
            ]
        elif self.orientation == Rotation90.DEG_180:
            # Input arrow pointing right, output arrow pointing left
            points1 = [
                (x - 6 - outer_offset, y - 2),
                (x - outer_offset, y),
                (x - 6 - outer_offset, y + 2),
            ]
            points2 = [
                (x - 7 - outer_offset, y - 2),
                (x - 7 - outer_offset, y + 2),
                (x - 13 - outer_offset, y),
            ]
        elif self.orientation == Rotation90.DEG_270:
            # Input arrow pointing up, output arrow pointing down
            points1 = [
                (x - 2, y + 6 + outer_offset),
                (x, y + outer_offset),
                (x + 2, y + 6 + outer_offset),
            ]
            points2 = [
                (x - 2, y + 7 + outer_offset),
                (x + 2, y + 7 + outer_offset),
                (x, y + 13 + outer_offset),
            ]
        else:
            return []
        elements.extend(self._make_arrow_polygons(points1, stroke, ctx))
        elements.extend(self._make_arrow_polygons(points2, stroke, ctx))
        return elements

    def _draw_analog_signal_in(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw ANALOG_SIGNAL_IN symbol (two vertical lines with semicircle arc).

        Native Altium SVG uses a path element for the arc.
        Pattern: M{x1},{y1} A{rx},{ry} 0 0,1 {x2},{y2}
        """
        # Offset adjustment based on outer_edge symbol presence
        # native DrawSymbolOuter: when NO outer_edge, shift -6 mils; when HAS outer_edge, no shift
        # So relative to body_end:
        #   - No outer_edge: symbol at +1 to +5 (shifted left by 6)
        #   - Has outer_edge: symbol at +7 to +11 (not shifted)
        has_outer_edge = self.symbol_outer_edge != IeeeSymbol.NONE
        outer_offset = 6 if has_outer_edge else 0

        if self.orientation == Rotation90.DEG_0:
            # Symbol extends right from body_end
            # Two vertical lines + semicircle arc on top
            left_x = x + 1 + outer_offset
            right_x = x + 5 + outer_offset
            leg_bottom_y = y - 4
            leg_top_y = y - 6
            arc_radius = 2
            return [
                self._make_symbol_line(left_x, leg_bottom_y, left_x, leg_top_y, stroke),
                self._make_symbol_line(
                    right_x, leg_bottom_y, right_x, leg_top_y, stroke
                ),
                # Arc from left to right (semicircle on top)
                svg_path(
                    f"M{left_x:.1f},{leg_top_y:.1f} A{arc_radius},{arc_radius} 0 0,1 {right_x:.1f},{leg_top_y:.1f}",
                    stroke=stroke,
                    stroke_width=SYMBOL_STROKE_WIDTH,
                    vector_effect="non-scaling-stroke",
                ),
            ]
        elif self.orientation == Rotation90.DEG_90:
            # Symbol extends upward and to the left of body_end
            # Native: two horizontal legs + vertical arc on left side
            lower_y = y - 1 - outer_offset  # Closer to body_end (larger SVG Y)
            upper_y = y - 5 - outer_offset  # Further from body_end (smaller SVG Y)
            left_x = x - 6  # Arc side
            right_x = x - 4  # Leg ends
            arc_radius = 2
            return [
                # Two horizontal legs
                self._make_symbol_line(right_x, upper_y, left_x, upper_y, stroke),
                self._make_symbol_line(right_x, lower_y, left_x, lower_y, stroke),
                # Vertical arc on left side, from lower to upper
                svg_path(
                    f"M{left_x:.1f},{lower_y:.1f} A{arc_radius},{arc_radius} 0 0,1 {left_x:.1f},{upper_y:.1f}",
                    stroke=stroke,
                    stroke_width=SYMBOL_STROKE_WIDTH,
                    vector_effect="non-scaling-stroke",
                ),
            ]
        elif self.orientation == Rotation90.DEG_180:
            # Symbol extends left and above body_end
            # Native: two vertical legs + horizontal arc at top
            right_x = x - 1 - outer_offset  # Closer to body_end
            left_x = x - 5 - outer_offset  # Further from body_end
            lower_y = y - 4  # Closer to body_end (larger SVG Y)
            upper_y = y - 6  # Further from body_end (smaller SVG Y = arc)
            arc_radius = 2
            return [
                # Two vertical legs
                self._make_symbol_line(left_x, lower_y, left_x, upper_y, stroke),
                self._make_symbol_line(right_x, lower_y, right_x, upper_y, stroke),
                # Horizontal arc at top, from left to right
                svg_path(
                    f"M{left_x:.1f},{upper_y:.1f} A{arc_radius},{arc_radius} 0 0,1 {right_x:.1f},{upper_y:.1f}",
                    stroke=stroke,
                    stroke_width=SYMBOL_STROKE_WIDTH,
                    vector_effect="non-scaling-stroke",
                ),
            ]
        elif self.orientation == Rotation90.DEG_270:
            # Symbol extends downward and to the left of body_end
            # Native: two horizontal legs + vertical arc on left side
            upper_y = y + 1 + outer_offset  # Closer to body_end (smaller SVG Y)
            lower_y = y + 5 + outer_offset  # Further from body_end (larger SVG Y)
            left_x = x - 6  # Arc side
            right_x = x - 4  # Leg ends
            arc_radius = 2
            return [
                # Two horizontal legs
                self._make_symbol_line(right_x, upper_y, left_x, upper_y, stroke),
                self._make_symbol_line(right_x, lower_y, left_x, lower_y, stroke),
                # Vertical arc on left side, from lower to upper (sweep=1)
                svg_path(
                    f"M{left_x:.1f},{lower_y:.1f} A{arc_radius},{arc_radius} 0 0,1 {left_x:.1f},{upper_y:.1f}",
                    stroke=stroke,
                    stroke_width=SYMBOL_STROKE_WIDTH,
                    vector_effect="non-scaling-stroke",
                ),
            ]
        return []

    def _draw_digital_signal_in(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw DIGITAL_SIGNAL_IN symbol (crossed lines pattern).

        Indicates digital input capability.
        """
        if self.orientation in (Rotation90.DEG_0, Rotation90.DEG_90):
            return [
                self._make_symbol_line(x + 2, y, x + 7, y, stroke),
                self._make_symbol_line(x + 4, y - 2, x + 9, y - 2, stroke),
                self._make_symbol_line(x + 3, y + 1, x + 6, y - 3, stroke),
                self._make_symbol_line(x + 5, y + 1, x + 8, y - 3, stroke),
            ]
        else:
            return [
                self._make_symbol_line(x - 2, y + 1, x - 7, y + 1, stroke),
                self._make_symbol_line(x - 4, y - 1, x - 9, y - 1, stroke),
                self._make_symbol_line(x - 3, y + 2, x - 6, y - 2, stroke),
                self._make_symbol_line(x - 5, y + 2, x - 8, y - 2, stroke),
            ]

    def _draw_not_logic_connection(self, x: float, y: float, stroke: str) -> list[str]:
        """
        Draw NOT_LOGIC_CONNECTION symbol (X pattern).

        Indicates non-logic connection point.
        """
        offset = 3
        if self.orientation == Rotation90.DEG_0:
            cx = x + offset + 3
        elif self.orientation == Rotation90.DEG_90:
            cx, cy = x, y - offset - 3
            return [
                self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy + 2, stroke),
                self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy - 2, stroke),
            ]
        elif self.orientation == Rotation90.DEG_180:
            cx = x - offset - 3
        elif self.orientation == Rotation90.DEG_270:
            cx, cy = x, y + offset + 3
            return [
                self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy + 2, stroke),
                self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy - 2, stroke),
            ]
        else:
            cx = x

        cy = y
        return [
            self._make_symbol_line(cx - 2, cy - 2, cx + 2, cy + 2, stroke),
            self._make_symbol_line(cx - 2, cy + 2, cx + 2, cy - 2, stroke),
        ]

    def __repr__(self) -> str:
        return (
            f"<AltiumSchPin {self.designator}:{self.name} "
            f"at ({self.x_mils}, {self.y_mils}) mils, "
            f"{self.electrical_name}, {self.orientation_name}>"
        )

    def format_info(self, indent: str = "  ") -> str:
        """
        Format pin information for display.

        Returns:
            Multi-line string with all pin properties
        """
        lines = [
            f"{indent}PIN: {self.designator} - '{self.name}'",
            f"{indent}  Position: ({self.x_mils}, {self.y_mils}) mils",
            f"{indent}  Length: {self.length_mils} mils",
            f"{indent}  Orientation: {self.orientation_name}",
            f"{indent}  Electrical: {self.electrical_name}",
            f"{indent}  Visible: name={self.show_name}, desig={self.show_designator}, hidden={self.is_hidden}",
        ]

        if self.description:
            lines.append(f"{indent}  Description: {self.description}")

        # Symbol decorations
        decorations = []
        if self.symbol_inner != IeeeSymbol.NONE:
            decorations.append(f"inner={IEEE_SYMBOL_NAMES[self.symbol_inner]}")
        if self.symbol_outer != IeeeSymbol.NONE:
            decorations.append(f"outer={IEEE_SYMBOL_NAMES[self.symbol_outer]}")
        if self.symbol_inner_edge != IeeeSymbol.NONE:
            decorations.append(
                f"inner_edge={IEEE_SYMBOL_NAMES[self.symbol_inner_edge]}"
            )
        if self.symbol_outer_edge != IeeeSymbol.NONE:
            decorations.append(
                f"outer_edge={IEEE_SYMBOL_NAMES[self.symbol_outer_edge]}"
            )

        if decorations:
            lines.append(f"{indent}  Symbols: {', '.join(decorations)}")

        return "\n".join(lines)
