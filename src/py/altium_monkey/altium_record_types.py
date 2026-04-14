"""
Altium record type definitions and base classes.

This module provides shared enums, descriptors, and base record classes used by
the schematic and PCB object models.
"""

import random
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum
from math import isfinite
from typing import TYPE_CHECKING, Any

from .altium_sch_enums import PinOrientation as PinOrientation  # noqa: F401
from .altium_sch_enums import SchHorizontalAlign as SchHorizontalAlign  # noqa: F401
from .altium_sch_enums import TextJustification as TextJustification  # noqa: F401
from .altium_sch_enums import TextOrientation as TextOrientation  # noqa: F401

if TYPE_CHECKING:
    from .altium_sch_binding import SchematicBindingContext

# =============================================================================
# Case-Insensitive Dictionary
# =============================================================================


class CaseInsensitiveDict(dict):
    """
    Case-insensitive dictionary for Altium record field lookups.

    Field lookups are case-insensitive. This class preserves that behavior:
    - Lookups are case-insensitive: d['Location.X'] finds 'LOCATION.X'
    - Original keys are preserved for iteration and serialization
    - First key wins: if you set 'Foo' then 'FOO', 'Foo' is kept

    This enables clean parsing code:
        value = record.get('OwnerIndex', 0)  # finds 'OwnerIndex' or 'OWNERINDEX'

    Instead of the verbose fallback pattern:
        value = r.get('OwnerIndex', 0)
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._lower_map: dict[str, str] = {}  # lowercase -> original key
        if data:
            for key, value in data.items():
                self[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        lower_key = key.lower()
        if lower_key in self._lower_map:
            # Update existing key (preserve original casing)
            super().__setitem__(self._lower_map[lower_key], value)
        else:
            # New key
            self._lower_map[lower_key] = key
            super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        lower_key = key.lower()
        if lower_key in self._lower_map:
            return super().__getitem__(self._lower_map[lower_key])
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key.lower() in self._lower_map

    def __delitem__(self, key: str) -> None:
        lower_key = key.lower()
        if lower_key in self._lower_map:
            original_key = self._lower_map.pop(lower_key)
            super().__delitem__(original_key)
        else:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        lower_key = key.lower()
        if lower_key in self._lower_map:
            return super().__getitem__(self._lower_map[lower_key])
        return default

    def pop(self, key: str, *args: Any) -> Any:
        lower_key = key.lower()
        if lower_key in self._lower_map:
            original_key = self._lower_map.pop(lower_key)
            return super().pop(original_key)
        if args:
            return args[0]
        raise KeyError(key)

    def copy(self) -> "CaseInsensitiveDict":
        """
        Return a shallow copy.
        """
        return CaseInsensitiveDict(dict(self))


# =============================================================================
# Unique ID Generation
# =============================================================================


def generate_unique_id() -> str:
    """
    Generate a random 8-character unique ID for Altium objects.

    Altium uses 8 uppercase ASCII letters (e.g., 'ABCDEFGH') to uniquely
    identify objects within a document. This ID is stored in the UNIQUEID
    field of record data.

    Returns:
        8-character uppercase ASCII string
    """
    return "".join(random.choices(string.ascii_uppercase, k=8))


# =============================================================================
# Type Enforcement Descriptor
# =============================================================================


class IntField:
    """
    Descriptor that enforces integer type on assignment.

    Use this for fields that MUST be integers (coordinates, font IDs,
    dimensions, etc.). Float values are automatically converted to int.

    Example usage:
        class MyClass:
            x = IntField(default=0)
            font_id = IntField(default=1)
    """

    def __init__(self, default: int = 0) -> None:
        self.default = int(default)
        self.name = ""  # Set by __set_name__
        self.private_name = ""

    def __set_name__(self, owner: type[Any], name: str) -> None:
        self.name = name
        self.private_name = "_" + name

    def __get__(self, obj: Any, objtype: type[Any] | None = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self.private_name, self.default)

    def __set__(self, obj: Any, value: Any) -> None:
        setattr(obj, self.private_name, int(value))


class OptionalIntField:
    """
    Descriptor that enforces integer type on assignment, allowing None.

    Like IntField but accepts None values for optional fields.

    Example usage:
        class MyClass:
            owner_part_id = OptionalIntField()  # Can be None or int
    """

    def __init__(self) -> None:
        self.name = ""  # Set by __set_name__
        self.private_name = ""

    def __set_name__(self, owner: type[Any], name: str) -> None:
        self.name = name
        self.private_name = "_" + name

    def __get__(self, obj: Any, objtype: type[Any] | None = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self.private_name, None)

    def __set__(self, obj: Any, value: Any) -> None:
        if value is None:
            setattr(obj, self.private_name, None)
        else:
            setattr(obj, self.private_name, int(value))


class SchRecordType(IntEnum):
    """
    Record type IDs for Altium SchLib/SchDoc files.

    Same record types are used in both SchLib (symbol libraries) and
    SchDoc (schematic documents), though some types are more common in one vs the other.
    """

    # Core symbol records
    HEADER = 0  # FileHeader with font table
    COMPONENT = 1  # Component/symbol container
    PIN = 2  # Component pin
    IEEE_SYMBOL = 3  # IEEE symbol notation
    LABEL = 4  # Text label

    # Graphical primitives
    BEZIER = 5  # Bezier curve
    POLYLINE = 6  # Multi-segment line
    POLYGON = 7  # Filled polygon
    ELLIPSE = 8  # Ellipse/circle
    PIECHART = 9  # Pie chart shape
    ROUND_RECTANGLE = 10  # Rounded rectangle
    ELLIPTICAL_ARC = 11  # Elliptical arc
    ARC = 12  # Circular arc
    LINE = 13  # Single line segment
    RECTANGLE = 14  # Rectangle

    # Schematic-specific records
    SHEET_SYMBOL = 15  # Schematic sheet symbol
    SHEET_ENTRY = 16  # Sheet entry port
    POWER_PORT = 17  # Power/ground port
    PORT = 18  # Generic port
    NO_ERC = 22  # ERC suppression marker
    NET_LABEL = 25  # Net label
    BUS = 26  # Bus line
    WIRE = 27  # Wire/connection
    TEXT_FRAME = 28  # Text box/frame
    JUNCTION = 29  # Wire junction dot
    IMAGE = 30  # Embedded image
    SHEET = 31  # Sheet properties
    SHEET_NAME = 32  # Sheet name field
    FILE_NAME = 33  # File name field
    DESIGNATOR = 34  # Component designator
    BUS_ENTRY = 37  # Bus entry point
    TEMPLATE = 39  # Template definition
    PARAMETER = 41  # Component parameter
    PARAMETER_SET = 43  # Parameter group

    # Implementation records (often marked as garbage in SchLib)
    IMPLEMENTATION_LIST = 44  # Implementation container
    IMPLEMENTATION = 45  # Model implementation
    MAP_DEFINER_LIST = 46  # Map definer container
    MAP_DEFINER = 47  # Map definition
    IMPL_PARAMS = 48  # Implementation parameters

    # Additional records
    NOTE = 209  # Annotation note
    COMPILE_MASK = 211  # Compile mask
    HARNESS_CONNECTOR = 215  # Harness connector
    HARNESS_ENTRY = 216  # Harness entry point
    HARNESS_TYPE = 217  # Harness type definition
    SIGNAL_HARNESS = 218  # Signal harness
    BLANKET = 225  # Blanket directive
    HYPERLINK = 226  # Hyperlink


class PcbRecordType(IntEnum):
    """
    Record type IDs for Altium PcbLib/PcbDoc binary primitives.

    These record types are used in binary streams (Pads6/Data, Tracks6/Data, etc.)
    in both PcbLib (footprint libraries) and PcbDoc (PCB layout) files.
    """

    ARC = 1  # 0x01 - Curved trace or silkscreen arc
    PAD = 2  # 0x02 - Component pad (SMT or through-hole)
    VIA = 3  # 0x03 - Through-hole via
    TRACK = 4  # 0x04 - Straight trace or silkscreen line
    TEXT = 5  # 0x05 - Text string on PCB
    FILL = 6  # 0x06 - Filled rectangle
    REGION = 11  # 0x0B - Filled copper polygon/region
    COMPONENT_BODY = 12  # 0x0C - 3D extruded component body shape
    MODEL = 156  # 0x9C - 3D model reference (STEP file)


class PcbLayer(IntEnum):
    """
    PCB layer IDs used by PcbDoc and PcbLib primitive APIs.

    Enum values map directly to the on-disk PCB layer ids used by Altium's
    binary format. Public authoring methods accept these enum values anywhere a
    layer argument is required, and rendering/export helpers use them to group
    copper, mask, paste, overlay, keepout, drill, and mechanical layers.
    """

    TOP = 1
    MID1 = 2
    MID2 = 3
    MID3 = 4
    MID4 = 5
    MID5 = 6
    MID6 = 7
    MID7 = 8
    MID8 = 9
    MID9 = 10
    MID10 = 11
    MID11 = 12
    MID12 = 13
    MID13 = 14
    MID14 = 15
    MID15 = 16
    MID16 = 17
    MID17 = 18
    MID18 = 19
    MID19 = 20
    MID20 = 21
    MID21 = 22
    MID22 = 23
    MID23 = 24
    MID24 = 25
    MID25 = 26
    MID26 = 27
    MID27 = 28
    MID28 = 29
    MID29 = 30
    MID30 = 31
    BOTTOM = 32
    TOP_OVERLAY = 33
    BOTTOM_OVERLAY = 34
    TOP_PASTE = 35
    BOTTOM_PASTE = 36
    TOP_SOLDER = 37
    BOTTOM_SOLDER = 38
    INTERNAL_PLANE_1 = 39
    INTERNAL_PLANE_2 = 40
    INTERNAL_PLANE_3 = 41
    INTERNAL_PLANE_4 = 42
    INTERNAL_PLANE_5 = 43
    INTERNAL_PLANE_6 = 44
    INTERNAL_PLANE_7 = 45
    INTERNAL_PLANE_8 = 46
    INTERNAL_PLANE_9 = 47
    INTERNAL_PLANE_10 = 48
    INTERNAL_PLANE_11 = 49
    INTERNAL_PLANE_12 = 50
    INTERNAL_PLANE_13 = 51
    INTERNAL_PLANE_14 = 52
    INTERNAL_PLANE_15 = 53
    INTERNAL_PLANE_16 = 54
    DRILL_GUIDE = 55
    KEEPOUT = 56
    MECHANICAL_1 = 57
    MECHANICAL_2 = 58
    MECHANICAL_3 = 59
    MECHANICAL_4 = 60
    MECHANICAL_5 = 61
    MECHANICAL_6 = 62
    MECHANICAL_7 = 63
    MECHANICAL_8 = 64
    MECHANICAL_9 = 65
    MECHANICAL_10 = 66
    MECHANICAL_11 = 67
    MECHANICAL_12 = 68
    MECHANICAL_13 = 69
    MECHANICAL_14 = 70
    MECHANICAL_15 = 71
    MECHANICAL_16 = 72
    DRILL_DRAWING = 73
    MULTI_LAYER = 74
    CONNECT = 75  # System/warehouse layer; runtime-computed, not stored in binary

    def to_json_name(self) -> str:
        """
        Return the layer name as it appears in Altium JSON exports.
        """
        return _LAYER_TO_JSON.get(self, f"UNKNOWN_{self.value}")

    @classmethod
    def from_json_name(cls, name: str) -> "PcbLayer":
        """
        Look up a PcbLayer from its JSON export name.
        """
        result = _JSON_TO_LAYER.get(name.upper())
        if result is None:
            raise ValueError(f"Unknown layer name: {name!r}")
        return result

    @classmethod
    def from_byte(cls, value: int) -> "PcbLayer | None":
        """
        Look up a PcbLayer from a binary byte value. Returns None if unknown.
        """
        try:
            return cls(value)
        except ValueError:
            return None

    def is_copper(self) -> bool:
        """
        Signal copper layer (TOP, MID1-30, BOTTOM).
        """
        return PcbLayer.TOP <= self <= PcbLayer.BOTTOM

    def is_signal(self) -> bool:
        """
        Signal layer (same as is_copper).
        """
        return self.is_copper()

    def is_internal_plane(self) -> bool:
        """
        Internal plane layer (INTERNAL_PLANE_1 through INTERNAL_PLANE_16).
        """
        return PcbLayer.INTERNAL_PLANE_1 <= self <= PcbLayer.INTERNAL_PLANE_16

    def is_overlay(self) -> bool:
        """
        Silkscreen overlay layer (TOP_OVERLAY or BOTTOM_OVERLAY).
        """
        return self in (PcbLayer.TOP_OVERLAY, PcbLayer.BOTTOM_OVERLAY)

    def is_solder_mask(self) -> bool:
        """
        Solder mask layer (TOP_SOLDER or BOTTOM_SOLDER).
        """
        return self in (PcbLayer.TOP_SOLDER, PcbLayer.BOTTOM_SOLDER)

    def is_paste_mask(self) -> bool:
        """
        Paste mask layer (TOP_PASTE or BOTTOM_PASTE).
        """
        return self in (PcbLayer.TOP_PASTE, PcbLayer.BOTTOM_PASTE)

    def is_mechanical(self) -> bool:
        """
        Mechanical layer (MECHANICAL_1 through MECHANICAL_16).
        """
        return PcbLayer.MECHANICAL_1 <= self <= PcbLayer.MECHANICAL_16

    def is_top_side(self) -> bool:
        """
        Top-side layer (top copper, overlay, paste, mask).
        """
        return self in (
            PcbLayer.TOP,
            PcbLayer.TOP_OVERLAY,
            PcbLayer.TOP_PASTE,
            PcbLayer.TOP_SOLDER,
        )

    def is_bottom_side(self) -> bool:
        """
        Bottom-side layer (bottom copper, overlay, paste, mask).
        """
        return self in (
            PcbLayer.BOTTOM,
            PcbLayer.BOTTOM_OVERLAY,
            PcbLayer.BOTTOM_PASTE,
            PcbLayer.BOTTOM_SOLDER,
        )

    @property
    def default_color(self) -> str:
        """
        Default Altium layer color as hex string (#RRGGBB).
        """
        return _LAYER_DEFAULT_COLORS.get(self, "#808080")


# Layer <-> JSON name lookup tables
_LAYER_TO_JSON: dict[PcbLayer, str] = {
    PcbLayer.TOP: "TOP",
    PcbLayer.BOTTOM: "BOTTOM",
    PcbLayer.TOP_OVERLAY: "TOPOVERLAY",
    PcbLayer.BOTTOM_OVERLAY: "BOTTOMOVERLAY",
    PcbLayer.TOP_PASTE: "TOPPASTE",
    PcbLayer.BOTTOM_PASTE: "BOTTOMPASTE",
    PcbLayer.TOP_SOLDER: "TOPSOLDER",
    PcbLayer.BOTTOM_SOLDER: "BOTTOMSOLDER",
    PcbLayer.KEEPOUT: "KEEPOUT",
    PcbLayer.MULTI_LAYER: "MULTILAYER",
    PcbLayer.CONNECT: "CONNECT",
    PcbLayer.DRILL_GUIDE: "DRILLGUIDE",
    PcbLayer.DRILL_DRAWING: "DRILLDRAWING",
}
# Add MID layers
for _i in range(1, 31):
    _LAYER_TO_JSON[PcbLayer(1 + _i)] = f"MID{_i}"
# Add internal plane layers (JSON uses PLANE{N}, not INTERNALPLANE{N})
for _i in range(1, 17):
    _LAYER_TO_JSON[PcbLayer(38 + _i)] = f"PLANE{_i}"
# Add mechanical layers
for _i in range(1, 17):
    _LAYER_TO_JSON[PcbLayer(56 + _i)] = f"MECHANICAL{_i}"

# Reverse lookup
_JSON_TO_LAYER: dict[str, PcbLayer] = {v: k for k, v in _LAYER_TO_JSON.items()}

# Default Altium layer colors (approximate Altium Designer defaults)
_LAYER_DEFAULT_COLORS: dict[PcbLayer, str] = {
    PcbLayer.TOP: "#FF0000",  # Red
    PcbLayer.BOTTOM: "#0000FF",  # Blue
    PcbLayer.MID1: "#808000",  # Olive
    PcbLayer.MID2: "#008080",  # Teal
    PcbLayer.TOP_OVERLAY: "#FFFF00",  # Yellow
    PcbLayer.BOTTOM_OVERLAY: "#808080",  # Gray
    PcbLayer.TOP_PASTE: "#808080",  # Gray
    PcbLayer.BOTTOM_PASTE: "#808080",  # Gray
    PcbLayer.TOP_SOLDER: "#800080",  # Purple
    PcbLayer.BOTTOM_SOLDER: "#800080",  # Purple
    PcbLayer.KEEPOUT: "#FF00FF",  # Magenta
    PcbLayer.MULTI_LAYER: "#C0C0C0",  # Silver
    PcbLayer.DRILL_GUIDE: "#808080",  # Gray
    PcbLayer.DRILL_DRAWING: "#808080",  # Gray
    PcbLayer.MECHANICAL_1: "#FF8000",  # Orange
    PcbLayer.MECHANICAL_2: "#FF8000",  # Orange
    PcbLayer.MECHANICAL_13: "#FF8000",  # Orange (3D body layer)
}


# NOTE: PinElectricalType removed - use PinElectrical from altium_sch_enums.py
# NOTE: PinOrientation moved to altium_sch_enums.py (re-exported above)
# NOTE: TextOrientation moved to altium_sch_enums.py (re-exported above)


class LineWidth(IntEnum):
    """
    Schematic line width enumeration.

    Native Altium schematic size mapping:
    - 0 = Smallest
    - 1 = Small
    - 2 = Medium
    - 3 = Large
    """

    SMALLEST = 0
    SMALL = 1
    MEDIUM = 2
    LARGE = 3


class LineStyle(IntEnum):
    """
    Line style enumeration for polylines and lines.
    """

    SOLID = 0
    DASHED = 1
    DOTTED = 2
    DASH_DOT = 3  # Dash-dot pattern (from LineStyleExt)


class LineShape(IntEnum):
    """
    Line ending shape enumeration for polylines.

    Controls the shape drawn at the start/end of polylines.
    These are "decorations" like arrowheads, tails, circles, squares.

    Stored as the schematic line-shape enum.
    """

    NONE = 0  # No decoration
    ARROW = 1  # Open arrow (two lines forming V)
    SOLID_ARROW = 2  # Filled/solid arrow triangle
    TAIL = 3  # Open tail (opposite direction of arrow)
    SOLID_TAIL = 4  # Filled/solid tail
    CIRCLE = 5  # Circle at endpoint
    SQUARE = 6  # Square at endpoint


class ReadOnlyState(IntEnum):
    """
    Read-only state for parameters and designators.

    Controls which parts of the parameter/designator can be edited:
    - NONE: Fully editable
    - NAME: Name is read-only, value editable
    - VALUE: Value is read-only, name editable
    - NAME_AND_VALUE: Both name and value are read-only
    """

    NONE = 0
    NAME = 1
    VALUE = 2
    NAME_AND_VALUE = 3


class CoordPoint:
    """
    Coordinate point with sub-mil precision.

    Altium stores coordinates as integer + fractional parts:
    - Integer part: 10mil units (0.01 inch)
    - Fractional part: 1/10000 of integer unit (sub-mil precision)

    Float inputs are automatically converted to int at assignment.
    """

    __slots__ = ("_x", "_y", "_x_frac", "_y_frac")

    def __init__(
        self,
        x: int = 0,
        y: int = 0,
        x_frac: int = 0,
        y_frac: int = 0,
    ) -> None:
        """
        Initialize coordinate point with integer values.
        """
        self._x = int(x)
        self._y = int(y)
        self._x_frac = int(x_frac)
        self._y_frac = int(y_frac)

    @property
    def x(self) -> int:
        """
        X coordinate integer part.
        """
        return self._x

    @x.setter
    def x(self, value: int | float) -> None:
        """
        Set X coordinate - float values are converted to int.
        """
        self._x = int(value)

    @property
    def y(self) -> int:
        """
        Y coordinate integer part.
        """
        return self._y

    @y.setter
    def y(self, value: int | float) -> None:
        """
        Set Y coordinate - float values are converted to int.
        """
        self._y = int(value)

    @property
    def x_frac(self) -> int:
        """
        X coordinate fractional part.
        """
        return self._x_frac

    @x_frac.setter
    def x_frac(self, value: int | float) -> None:
        """
        Set X fractional part - float values are converted to int.
        """
        self._x_frac = int(value)

    @property
    def y_frac(self) -> int:
        """
        Y coordinate fractional part.
        """
        return self._y_frac

    @y_frac.setter
    def y_frac(self, value: int | float) -> None:
        """
        Set Y fractional part - float values are converted to int.
        """
        self._y_frac = int(value)

    @property
    def x_mils(self) -> float:
        """
        Get X coordinate in mils.
        """
        return self._x * 10.0 + self._x_frac / 10000.0

    @property
    def y_mils(self) -> float:
        """
        Get Y coordinate in mils.
        """
        return self._y * 10.0 + self._y_frac / 10000.0

    @classmethod
    def from_mils(cls, x_mils: float, y_mils: float) -> "CoordPoint":
        """
        Create from mil coordinates.

                Uses round() for fractional parts to avoid floating-point precision loss.
        """
        x = int(x_mils / 10.0)
        x_frac = round((x_mils - x * 10.0) * 10000.0)
        y = int(y_mils / 10.0)
        y_frac = round((y_mils - y * 10.0) * 10000.0)
        return cls(x, y, x_frac, y_frac)

    def __repr__(self) -> str:
        """
        Return string representation.
        """
        return f"CoordPoint(x={self._x}, y={self._y}, x_frac={self._x_frac}, y_frac={self._y_frac})"

    def __eq__(self, other: object) -> bool:
        """
        Check equality with another CoordPoint.
        """
        if not isinstance(other, CoordPoint):
            return NotImplemented
        return (
            self._x == other._x
            and self._y == other._y
            and self._x_frac == other._x_frac
            and self._y_frac == other._y_frac
        )


def win32_color_to_rgb(color: int) -> tuple[int, int, int]:
    """
    Convert Win32 color (0x00BBGGRR) to RGB tuple.

    Args:
        color: Win32 color integer (little-endian BGR)

    Returns:
        (R, G, B) tuple
    """
    r = color & 0xFF
    g = (color >> 8) & 0xFF
    b = (color >> 16) & 0xFF
    return (r, g, b)


def rgb_to_win32_color(r: int, g: int, b: int) -> int:
    """
    Convert RGB tuple to Win32 color (0x00BBGGRR).

    Args:
        r, g, b: RGB values (0-255)

    Returns:
        Win32 color integer
    """
    return r | (g << 8) | (b << 16)


def hex_to_win32_color(color_hex: str) -> int:
    """
    Convert a hex color string `#RRGGBB` to Win32 color form.

    Invalid or incomplete values return `0`.
    """
    color_text = str(color_hex or "").strip().lstrip("#")
    if len(color_text) != 6:
        return 0
    return rgb_to_win32_color(
        int(color_text[0:2], 16),
        int(color_text[2:4], 16),
        int(color_text[4:6], 16),
    )


def color_to_hex(color: int | None) -> str:
    """
    Convert Win32 color to hex string #RRGGBB.

        Altium doesn't serialize zero values, so missing color fields mean black (0).
        When color is None, returns black (#000000).
    """
    if color is None:
        return "#000000"
    r, g, b = win32_color_to_rgb(color)
    return f"#{r:02X}{g:02X}{b:02X}"


@dataclass(frozen=True, slots=True)
class ColorValue:
    """
    Public color helper for schematic and PCB APIs.

    The public API should not require callers to know Altium's internal Win32
    BGR integer encoding. `ColorValue` provides a small boundary type that can
    be created from RGB, hex, or an existing Win32 color integer.
    """

    _win32: int

    def __post_init__(self) -> None:
        if isinstance(self._win32, bool) or not isinstance(self._win32, int):
            raise TypeError("ColorValue expects an integer Win32 color value")
        if self._win32 < 0 or self._win32 > 0xFFFFFF:
            raise ValueError("Win32 color must be between 0x000000 and 0xFFFFFF")

    @classmethod
    def from_win32(cls, color: int) -> "ColorValue":
        """
        Create a color helper from an Altium Win32 BGR integer.
        """
        return cls(color)

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> "ColorValue":
        """
        Create a color helper from RGB byte values.
        """
        for channel_name, channel in (("r", r), ("g", g), ("b", b)):
            if isinstance(channel, bool) or not isinstance(channel, int):
                raise TypeError(f"{channel_name} must be an integer in 0..255")
            if channel < 0 or channel > 255:
                raise ValueError(f"{channel_name} must be between 0 and 255")
        return cls(rgb_to_win32_color(r, g, b))

    @classmethod
    def from_hex(cls, color_hex: str) -> "ColorValue":
        """
        Create a color helper from a `#RRGGBB` or `RRGGBB` string.
        """
        color_text = str(color_hex or "").strip().lstrip("#")
        if len(color_text) != 6:
            raise ValueError("Hex colors must use exactly 6 hexadecimal digits")
        try:
            r = int(color_text[0:2], 16)
            g = int(color_text[2:4], 16)
            b = int(color_text[4:6], 16)
        except ValueError as exc:
            raise ValueError("Hex colors must use only hexadecimal digits") from exc
        return cls.from_rgb(r, g, b)

    @property
    def win32(self) -> int:
        """
        Altium Win32 BGR integer form.
        """
        return self._win32

    @property
    def rgb(self) -> tuple[int, int, int]:
        """
        Color as an `(r, g, b)` tuple.
        """
        return win32_color_to_rgb(self._win32)

    @property
    def hex(self) -> str:
        """
        Color as a `#RRGGBB` string.
        """
        return color_to_hex(self._win32)

    def __int__(self) -> int:
        return self._win32

    def __str__(self) -> str:
        return self.hex


_MILS_PER_MM = 1000.0 / 25.4


@dataclass(frozen=True, slots=True)
class SchFontSpec:
    """
    Public schematic font specification.

    This boundary type lets high-level object factories describe text styling
    without exposing document font-table details. The document resolves the
    specification to a concrete `font_id` when the object is added.
    """

    name: str
    size: int
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("font name must be a non-empty string")
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise TypeError("font size must be an integer point value")
        if self.size <= 0:
            raise ValueError("font size must be positive")
        for field_name in ("bold", "italic", "underline", "strikeout"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a bool")


def _coerce_public_coord_value(field_name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a numeric coordinate value")
    coerced = float(value)
    if not isfinite(coerced):
        raise ValueError(f"{field_name} must be finite")
    return coerced


@dataclass(frozen=True, slots=True)
class SchPointMils:
    """
    Public schematic point helper expressed in mils.

    This is the preferred public coordinate boundary type for schematic
    mutation/factory APIs. It keeps the public contract in mils while still
    leaving room for explicit `from_mm(...)` construction later.
    """

    x_mils: float
    y_mils: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x_mils", _coerce_public_coord_value("x_mils", self.x_mils)
        )
        object.__setattr__(
            self, "y_mils", _coerce_public_coord_value("y_mils", self.y_mils)
        )

    @classmethod
    def from_mils(cls, x_mils: float, y_mils: float) -> "SchPointMils":
        """
        Construct a public point directly from mil coordinates.
        """
        return cls(x_mils=x_mils, y_mils=y_mils)

    @classmethod
    def from_mm(cls, x_mm: float, y_mm: float) -> "SchPointMils":
        """
        Construct a public point from millimeter coordinates.
        """
        x_mm_value = _coerce_public_coord_value("x_mm", x_mm)
        y_mm_value = _coerce_public_coord_value("y_mm", y_mm)
        return cls(x_mm_value * _MILS_PER_MM, y_mm_value * _MILS_PER_MM)

    @property
    def x_mm(self) -> float:
        """
        X coordinate converted to millimeters.
        """
        return self.x_mils / _MILS_PER_MM

    @property
    def y_mm(self) -> float:
        """
        Y coordinate converted to millimeters.
        """
        return self.y_mils / _MILS_PER_MM

    def to_coord_point(self) -> CoordPoint:
        """
        Convert the public point helper to an internal `CoordPoint`.
        """
        return CoordPoint.from_mils(self.x_mils, self.y_mils)

    def translated(self, dx_mils: float, dy_mils: float) -> "SchPointMils":
        """
        Return a copy translated by the given mil offsets.
        """
        return SchPointMils(
            x_mils=self.x_mils + _coerce_public_coord_value("dx_mils", dx_mils),
            y_mils=self.y_mils + _coerce_public_coord_value("dy_mils", dy_mils),
        )


@dataclass(frozen=True, slots=True)
class SchRectMils:
    """
    Public schematic rectangle helper expressed in mils.

    The rectangle stores two opposite corners and normalizes them on demand.
    This makes it suitable for user-facing note/text-frame/style APIs where the
    caller should not need to care about the internal corner ordering.
    """

    x1_mils: float
    y1_mils: float
    x2_mils: float
    y2_mils: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x1_mils", _coerce_public_coord_value("x1_mils", self.x1_mils)
        )
        object.__setattr__(
            self, "y1_mils", _coerce_public_coord_value("y1_mils", self.y1_mils)
        )
        object.__setattr__(
            self, "x2_mils", _coerce_public_coord_value("x2_mils", self.x2_mils)
        )
        object.__setattr__(
            self, "y2_mils", _coerce_public_coord_value("y2_mils", self.y2_mils)
        )

    @classmethod
    def from_corners_mils(
        cls, x1_mils: float, y1_mils: float, x2_mils: float, y2_mils: float
    ) -> "SchRectMils":
        """
        Construct a public rectangle from mil-space corner coordinates.
        """
        return cls(x1_mils=x1_mils, y1_mils=y1_mils, x2_mils=x2_mils, y2_mils=y2_mils)

    @classmethod
    def from_corners_mm(
        cls, x1_mm: float, y1_mm: float, x2_mm: float, y2_mm: float
    ) -> "SchRectMils":
        """
        Construct a public rectangle from millimeter corner coordinates.
        """
        return cls(
            x1_mils=_coerce_public_coord_value("x1_mm", x1_mm) * _MILS_PER_MM,
            y1_mils=_coerce_public_coord_value("y1_mm", y1_mm) * _MILS_PER_MM,
            x2_mils=_coerce_public_coord_value("x2_mm", x2_mm) * _MILS_PER_MM,
            y2_mils=_coerce_public_coord_value("y2_mm", y2_mm) * _MILS_PER_MM,
        )

    @classmethod
    def from_points(cls, p1: SchPointMils, p2: SchPointMils) -> "SchRectMils":
        """
        Construct a rectangle from two public point helpers.
        """
        if not isinstance(p1, SchPointMils) or not isinstance(p2, SchPointMils):
            raise TypeError("p1 and p2 must both be SchPointMils values")
        return cls(p1.x_mils, p1.y_mils, p2.x_mils, p2.y_mils)

    def normalized(self) -> "SchRectMils":
        """
        Return a copy whose corners are ordered lower-left to upper-right.
        """
        return SchRectMils(
            x1_mils=min(self.x1_mils, self.x2_mils),
            y1_mils=min(self.y1_mils, self.y2_mils),
            x2_mils=max(self.x1_mils, self.x2_mils),
            y2_mils=max(self.y1_mils, self.y2_mils),
        )

    @property
    def width_mils(self) -> float:
        """
        Rectangle width in mils.
        """
        bounds = self.normalized()
        return bounds.x2_mils - bounds.x1_mils

    @property
    def height_mils(self) -> float:
        """
        Rectangle height in mils.
        """
        bounds = self.normalized()
        return bounds.y2_mils - bounds.y1_mils

    def to_coord_points(self) -> tuple[CoordPoint, CoordPoint]:
        """
        Convert the rectangle to normalized internal `CoordPoint` corners.
        """
        bounds = self.normalized()
        return (
            CoordPoint.from_mils(bounds.x1_mils, bounds.y1_mils),
            CoordPoint.from_mils(bounds.x2_mils, bounds.y2_mils),
        )

    def translated(self, dx_mils: float, dy_mils: float) -> "SchRectMils":
        """
        Return a copy translated by the given mil offsets.
        """
        dx = _coerce_public_coord_value("dx_mils", dx_mils)
        dy = _coerce_public_coord_value("dy_mils", dy_mils)
        return SchRectMils(
            x1_mils=self.x1_mils + dx,
            y1_mils=self.y1_mils + dy,
            x2_mils=self.x2_mils + dx,
            y2_mils=self.y2_mils + dy,
        )


def _set_record_location_mils(record: object, value: SchPointMils) -> None:
    if not isinstance(value, SchPointMils):
        raise TypeError("location_mils must be a SchPointMils value")
    record.location = value.to_coord_point()


class Primitive(ABC):
    """
    Base class for all Altium primitives/records.

    Provides common interface for parsing and serialization.

    ROUND-TRIP SUPPORT:
        For byte-identical round-trips, the raw record is stored during parsing.
        During serialization, the raw record is used as the base, with only
        semantically understood fields being updated. This preserves:
        - Unknown fields
        - Original field name casing
        - Fields with default values that were explicitly present
    """

    def __init__(self) -> None:
        self._raw_record: dict[str, Any] | None = None
        self._record: CaseInsensitiveDict = (
            CaseInsensitiveDict()
        )  # Empty for synthesis mode

    @property
    @abstractmethod
    def record_type(self) -> SchRecordType:
        """
        Record type ID.
        """
        pass

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse primitive from raw record dictionary.

        Stores a copy of the raw record for round-trip serialization.
        Also creates a case-insensitive view (self._record) for field lookups.

        Field names are case-insensitive, so 'Location.X' and 'LOCATION.X'
        are equivalent. The _record property provides this case-insensitive
        lookup while preserving original keys for serialization.

        Args:
            record: Dictionary with record fields (text or binary)
            font_manager: Optional FontIDManager for font ID translation
        """
        # Store raw record for round-trip support (preserves exact keys)
        self._raw_record = record.copy()
        # Create case-insensitive view for parsing (mirrors Altium behavior)
        self._record = CaseInsensitiveDict(record)

    @abstractmethod
    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize primitive to record dictionary.

        Returns:
            Dictionary with record fields for writing
        """
        pass

    def _get_base_record(self) -> dict[str, Any]:
        """
        Get base record for serialization.

        If we have a raw record from parsing, use it as the base.
        Otherwise, create a new record with just the RECORD type.

        Note: Internal fields starting with '_' (like _parse_index) are
        filtered out to avoid writing invalid data to files.
        """
        if self._raw_record is not None:
            # Filter out internal fields (those starting with '_')
            return {k: v for k, v in self._raw_record.items() if not k.startswith("_")}
        return {"RECORD": str(self.record_type.value)}

    def _update_field(
        self,
        record: dict[str, Any],
        canonical_name: str,
        value: Any,
        alt_names: list[str] | None = None,
        force: bool = False,
    ) -> None:
        """
        Update a field in the record, preserving original field name if present.

        Parsed records keep their original field names and omit fields that were
        absent in the source unless ``force`` is true. New objects always emit
        canonical field names.
        Subclass serializers should call _update_field unconditionally and let
        this helper decide whether the write is a round-trip update or a
        synthesized field emission.

        Args:
            record: Record dict to update
            canonical_name: The canonical field name (used if field not present)
            value: The value to set
            alt_names: Alternative field names to check (for case variations)
            force: Add the field even when serializing a parsed record that omitted it
        """
        # Check if any version of this field exists in the record
        all_names = [canonical_name] + (alt_names or [])
        existing_name = None
        for name in all_names:
            if name in record:
                existing_name = name
                break

        if existing_name:
            # Update existing field with same name (preserves original casing)
            record[existing_name] = str(value) if not isinstance(value, str) else value
        else:
            # Field not present in record
            # Only add for NEW records (synthesis mode), or explicit OOP mutation.
            if self._raw_record is None or force:
                field_name = self._field_name_for_new_field(canonical_name, alt_names)
                record[field_name] = str(value) if not isinstance(value, str) else value

    def _field_name_for_new_field(
        self,
        canonical_name: str,
        alt_names: list[str] | None,
    ) -> str:
        """
        Choose field casing for synthesized fields.

        New SchDoc records use PascalCase. Forced additions to parsed uppercase
        records use the uppercase spelling when one is available.
        """
        names = alt_names or []
        if self._raw_record is not None and any(
            key != "RECORD" and key.isupper() and len(key) > 2
            for key in self._raw_record
        ):
            for name in [canonical_name, *names]:
                if name.isupper():
                    return name
        return names[0] if names else canonical_name

    def _remove_field(self, record: dict[str, Any], names: list[str]) -> None:
        """
        Remove a field by any of its possible names.
        """
        for name in names:
            record.pop(name, None)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} record={self.record_type.name}>"


class SchPrimitive(Primitive):
    """
    Base class for schematic primitives.

    Adds common schematic fields:
    - Owner tracking (which component/part owns this)
    - Visibility and accessibility flags
    - Z-order positioning (IndexInSheet)
    """

    # Integer fields with type enforcement
    owner_index = IntField(default=0)
    owner_part_id = OptionalIntField()
    owner_part_display_mode = OptionalIntField()
    index_in_sheet = OptionalIntField()

    def __init__(self) -> None:
        super().__init__()
        # Initialize via descriptors (will call __set__)
        self.owner_index = 0
        # Default to -1 for standalone objects in SchDoc files.
        # SchLib objects should explicitly set to 1-N for part variants
        self.owner_part_id = -1
        self.owner_part_display_mode = None
        self.is_not_accessible: bool = False
        self.graphically_locked: bool = False
        # Auto-generate a unique_id for newly created objects.
        # Parsed records will overwrite with actual value from file
        self.unique_id: str = generate_unique_id()
        self.index_in_sheet = None  # Z-order position in schematic
        # Parent reference is set during hierarchy building and is not serialized.
        self.parent: SchPrimitive | None = None
        # Narrow document/library binding context for resource resolution such
        # as fonts. This is not serialized and does not imply ownership.
        self._bound_schematic_context: Any | None = None

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse common SchPrimitive fields.
        """
        # Store raw record for round-trip
        super().parse_from_record(record, font_manager)
        r = self._record  # Case-insensitive view

        # Descriptors handle int conversion automatically
        self.owner_index = r.get("OwnerIndex", 0)
        self.owner_part_id = r.get("OwnerPartId")
        self.owner_part_display_mode = r.get("OwnerPartDisplayMode")

        # Note: Altium has a typo - "IsNotAccesible" (missing 's')
        self.is_not_accessible = r.get("IsNotAccesible", "F") == "T"
        self.graphically_locked = r.get("GraphicallyLocked", "F") == "T"
        self.unique_id = r.get("UniqueID")

        # Z-order position - descriptor handles int conversion
        self.index_in_sheet = r.get("IndexInSheet")

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize common SchPrimitive fields, preserving original structure.
        """
        # Start with raw record if available (for round-trip)
        record = self._get_base_record()

        # Update fields we understand, preserving original names
        # Only add OwnerIndex if object has an actual owner (owner_index > 0)
        # Top-level objects (owner_index=0) should NOT have this field
        owner_index = getattr(self, "_owner_index", 0)
        if owner_index > 0:
            self._update_field(
                record, "OWNERINDEX", owner_index, ["OwnerIndex", "OWNERINDEX"]
            )

        if self.owner_part_id is not None:
            self._update_field(
                record,
                "OWNERPARTID",
                self.owner_part_id,
                ["OwnerPartId", "OWNERPARTID"],
            )

        if self.owner_part_display_mode is not None:
            self._update_field(
                record,
                "OWNERPARTDISPLAYMODE",
                self.owner_part_display_mode,
                ["OwnerPartDisplayMode", "OWNERPARTDISPLAYMODE"],
            )

        # Note: Preserve the Altium typo "IsNotAccesible" (missing 's')
        if self.is_not_accessible:
            self._update_field(
                record, "ISNOTACCESIBLE", "T", ["IsNotAccesible", "ISNOTACCESIBLE"]
            )

        if self.graphically_locked:
            self._update_field(
                record,
                "GRAPHICALLYLOCKED",
                "T",
                ["GraphicallyLocked", "GRAPHICALLYLOCKED"],
            )

        # Only update UniqueID if we have one
        if self.unique_id:
            self._update_field(
                record, "UNIQUEID", self.unique_id, ["UniqueID", "UNIQUEID"]
            )

        # Z-order position
        # -1 is valid (used by SheetName/FileName children)
        # -2 is sentinel meaning "omit this field" (used for first SheetEntry)
        if self.index_in_sheet is not None and self.index_in_sheet != -2:
            self._update_field(
                record,
                "INDEXINSHEET",
                self.index_in_sheet,
                ["IndexInSheet", "INDEXINSHEET"],
            )

        return record

    def _bind_to_schematic_context(self, context: "SchematicBindingContext") -> None:
        """
        Attach a narrow schematic binding context to this record.

        The binding context gives records access to document/library-scoped
        services such as font resolution without exposing the full container API
        or affecting structural ownership.
        """
        self._bound_schematic_context = context


class SchGraphicalObject(SchPrimitive):
    """
    Base class for graphical objects (things drawn on the schematic).

    Adds:
    - Location (position with sub-mil precision)
    - Color (line/outline color)
    - AreaColor (fill color)
    """

    def __init__(self) -> None:
        super().__init__()
        self.location = CoordPoint()
        # CRITICAL: Use None for colors, not defaults
        # When None, these fields are omitted from serialized output
        # Altium will use its own rendering defaults when displaying the symbol
        self.color: int | None = (
            None  # Win32 format (0x00BBGGRR), None if not specified
        )
        self.area_color: int | None = (
            None  # Win32 format (0x00BBGGRR), None if not specified
        )
        # Track which location fields were present in original
        self._has_location_x: bool = False
        self._has_location_y: bool = False

    def parse_from_record(
        self,
        record: dict[str, Any],
        font_manager: Any | None = None,
    ) -> None:
        """
        Parse common SchGraphicalObject fields.
        """
        super().parse_from_record(record, font_manager)
        r = self._record  # Case-insensitive view

        # Parse location with fractional parts
        # NOTE: Altium uses mixed case: Location.X (not LOCATION.X)
        # Track if fields were present
        self._has_location_x = "Location.X" in record or "LOCATION.X" in record
        self._has_location_y = "Location.Y" in record or "LOCATION.Y" in record

        # Handle both integer and float string values (translation may produce floats)
        x_val = r.get("Location.X", 0)
        y_val = r.get("Location.Y", 0)
        x = int(float(x_val)) if x_val else 0
        y = int(float(y_val)) if y_val else 0
        x_frac = int(r.get("Location.X_Frac", 0))
        y_frac = int(r.get("Location.Y_Frac", 0))
        self.location = CoordPoint(x, y, x_frac, y_frac)

        # Parse colors (Win32 format)
        # NOTE: Check both cases
        if "Color" in record:
            self.color = int(record["Color"])
        elif "COLOR" in record:
            self.color = int(record["COLOR"])
        # Keep color=None if not present

        if "AreaColor" in record:
            self.area_color = int(record["AreaColor"])
        elif "AREACOLOR" in record:
            self.area_color = int(record["AREACOLOR"])
        # Keep area_color=None if not present

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize common SchGraphicalObject fields, preserving original structure.
        """
        record = super().serialize_to_record()

        # Location fields: write if present in original (round-trip) OR non-zero (synthesis)
        # Altium omits Location.X=0 and Location.Y=0 in Library Splitter output
        if self._has_location_x or self.location.x != 0:
            self._update_field(
                record, "LOCATION.X", self.location.x, ["Location.X", "LOCATION.X"]
            )
        if self._has_location_y or self.location.y != 0:
            self._update_field(
                record, "LOCATION.Y", self.location.y, ["Location.Y", "LOCATION.Y"]
            )

        if self.location.x_frac != 0:
            self._update_field(
                record,
                "LOCATION.X_FRAC",
                self.location.x_frac,
                ["Location.X_Frac", "LOCATION.X_FRAC"],
            )

        if self.location.y_frac != 0:
            self._update_field(
                record,
                "LOCATION.Y_FRAC",
                self.location.y_frac,
                ["Location.Y_Frac", "LOCATION.Y_FRAC"],
            )

        # Nullable colors are explicit object state: None means omitted/default,
        # any integer value must serialize even if the parsed source omitted it.
        if self.color is not None:
            self._update_field(
                record, "COLOR", self.color, ["Color", "COLOR"], force=True
            )

        if self.area_color is not None:
            self._update_field(
                record,
                "AREACOLOR",
                self.area_color,
                ["AreaColor", "AREACOLOR"],
                force=True,
            )

        return record

    @property
    def color_rgb(self) -> tuple[int, int, int] | None:
        """
        Get line color as (R, G, B) tuple.
        """
        if self.color is None:
            return None
        return win32_color_to_rgb(self.color)

    @property
    def area_color_rgb(self) -> tuple[int, int, int] | None:
        """
        Get fill color as (R, G, B) tuple.
        """
        if self.area_color is None:
            return None
        return win32_color_to_rgb(self.area_color)

    @property
    def location_mils(self) -> SchPointMils:
        """
        Public location helper expressed in mils.
        """
        return SchPointMils.from_mils(self.location.x_mils, self.location.y_mils)

    @location_mils.setter
    def location_mils(self, value: SchPointMils) -> None:
        return _set_record_location_mils(self, value)

    def to_svg(self, ctx: Any | None = None) -> list[str]:
        """
        Legacy per-record SVG hook.

        Most schematic graphics now render through the document-level IR path.
        Record classes that still need direct SVG output should override this.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not implement direct SVG rendering; "
            "use the document-level schematic SVG pipeline instead"
        )


class PcbPrimitive(Primitive):
    """
    Base class for PCB primitives (binary records in PcbLib/PcbDoc).

    PCB primitives are fundamentally different from schematic primitives:
    - Always binary format (SubRecord structure)
    - Coordinates in 10,000 units/mil (not 10mil + fractional)
    - Component/net linkage via uint16 indices
    - Layer-based rendering
    """

    def __init__(self) -> None:
        super().__init__()
        self.layer: int = 0  # Layer number (1=TOP, 32=BOTTOM, 74=MULTI_LAYER)
        self.component_index: int | None = (
            None  # Links to component (uint16, 0xFFFF for unlinked)
        )
        self.net_index: int | None = None  # Links to net (uint16, 0xFFFF for unlinked)
        self.is_locked: bool = False  # Locked for editing
        self.is_keepout: bool = False  # Keepout primitive
        self.is_polygon_outline: bool = False  # Part of polygon outline

        # Raw binary data (for round-trip)
        self._raw_binary: bytes | None = None

    @property
    @abstractmethod
    def record_type(self) -> PcbRecordType:
        """
        PCB record type ID.
        """
        pass

    def parse_from_binary(self, data: bytes) -> None:
        """
        Parse primitive from binary data.

        Args:
            data: Binary data starting with type byte
        """
        self._raw_binary = data

    def serialize_to_record(self) -> dict[str, Any]:
        """
        PCB primitives use binary format, not text records.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} is a binary PCB primitive; use serialize_to_binary() instead"
        )

    def serialize_to_binary(self) -> bytes:
        """
        Serialize primitive to binary data.

        Returns:
            Binary data ready to write to stream
        """
        if self._raw_binary is not None:
            return self._raw_binary
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement serialize_to_binary or store _raw_binary"
        )


class PcbGraphicalObject(PcbPrimitive):
    """
    Base class for PCB graphical primitives (tracks, arcs, pads, etc.).

    Adds common geometry fields:
    - Position coordinates (in 10,000 units/mil)
    - Width/size dimensions
    """

    def __init__(self) -> None:
        super().__init__()
        # Positions in internal units (10,000 = 1 mil)
        self.x: int = 0
        self.y: int = 0
        self.width: int = 0

    @classmethod
    def _from_internal_units(cls, value: int) -> float:
        """
        Convert internal units (10,000/mil) to mils.
        """
        return value / 10000.0

    @classmethod
    def _to_internal_units(cls, mils: float) -> int:
        """
        Convert mils to internal units (10,000/mil).
        """
        return int(mils * 10000.0)

    @property
    def x_mils(self) -> float:
        """
        Get X coordinate in mils.
        """
        return self._from_internal_units(self.x)

    @property
    def y_mils(self) -> float:
        """
        Get Y coordinate in mils.
        """
        return self._from_internal_units(self.y)

    @property
    def width_mils(self) -> float:
        """
        Get width in mils.
        """
        return self._from_internal_units(self.width)


# Utility functions for record handling


def is_text_record(record: dict[str, Any]) -> bool:
    """
    Check if record is text-based (vs binary).

    Args:
        record: Record dictionary

    Returns:
        True if text record, False if binary
    """
    return "__BINARY_RECORD__" not in record


def is_binary_record(record: dict[str, Any]) -> bool:
    """
    Check if record is binary.
    """
    return "__BINARY_RECORD__" in record


def get_binary_data(record: dict[str, Any]) -> bytes | None:
    """
    Extract binary data from record.

    Args:
        record: Record dictionary

    Returns:
        Binary data bytes or None if text record
    """
    if is_binary_record(record):
        return record.get("__BINARY_DATA__")
    return None


def parse_bool(value: Any) -> bool:
    """
    Parse boolean from Altium format.

    Altium uses 'T'/'F' strings or integers. JSON export uses 'true'/'false'.

    Args:
        value: Value to parse (str, int, bool)

    Returns:
        Boolean value
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        upper = value.upper()
        # Accept both Altium format ('T'/'F') and JSON format ('true'/'false')
        return upper == "T" or upper == "TRUE" or value == "1"
    if isinstance(value, int):
        return value != 0
    return False


def serialize_bool(value: bool) -> str:
    """
    Serialize boolean to Altium format ('T' or 'F').
    """
    return "T" if value else "F"
