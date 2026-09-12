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
from typing import TYPE_CHECKING, Any, Callable, cast, overload

from .altium_sch_enums import PinOrientation as PinOrientation  # noqa: F401
from .altium_sch_enums import SchHorizontalAlign as SchHorizontalAlign  # noqa: F401
from .altium_sch_enums import TextJustification as TextJustification  # noqa: F401
from .altium_sch_enums import TextOrientation as TextOrientation  # noqa: F401
from ._sch_managed_defaults import GRAPHICAL_FILL_COLOR

_RecordFields = dict[str, Any]
MAX_INDEXED_ITEMS_PER_RECORD = 65_536
_MANAGED_GEOMETRY_WIRING_ORIGIN_RECORD_CODES = frozenset(
    {1, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 28, 30}
)
_MANAGED_GEOMETRY_DYNAMIC_UNIQUE_ID_RECORD_CODES = frozenset(
    {5, 6, 7, 8, 10, 11, 12, 13, 14, 28, 30}
)

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager
    from .altium_serializer import AltiumSerializer
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


def _generate_available_unique_id(
    generate: Callable[[], str], is_in_use: Callable[[str], bool]
) -> str:
    """Generate identifiers until the owning container accepts one."""
    while True:
        unique_id = generate()
        if not is_in_use(unique_id):
            return unique_id


class _ManagedUniqueIdOwner:
    """Shared managed identity lifecycle for document and library objects."""

    unique_id: str | None
    _is_unique_id_locked: bool

    def _set_unique_id(self, value: str) -> None:
        """Apply the managed SetUniqueId lock rule."""
        if not self._is_unique_id_locked:
            self._update_unique_id(value)

    def _update_unique_id(self, value: str) -> None:
        """Replace the UniqueID even when managed identity is locked."""
        self.unique_id = value

    def _reset_unique_id(self) -> None:
        """Clear the UniqueID without synthesizing a replacement."""
        self.unique_id = ""

    def _set_unique_id_locked(self, locked: bool) -> None:
        """Set the document-managed identity lock state."""
        self._is_unique_id_locked = locked


# =============================================================================
# Type Enforcement Descriptor
# =============================================================================


def _require_int_width(field: str, value: int, minimum: int, maximum: int) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside [{minimum}, {maximum}]")
    return value


def _require_optional_int_width(
    field: str, value: int | None, minimum: int, maximum: int
) -> None:
    if value is not None:
        _require_int_width(field, value, minimum, maximum)


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

    def __init__(
        self,
        default: int = 0,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> None:
        self.default = int(default)
        self.minimum = minimum
        self.maximum = maximum
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
        parsed = int(value)
        if self.minimum is not None and parsed < self.minimum:
            raise ValueError(f"{self.name} is below {self.minimum}")
        if self.maximum is not None and parsed > self.maximum:
            raise ValueError(f"{self.name} is above {self.maximum}")
        setattr(obj, self.private_name, parsed)


class OptionalIntField:
    """
    Descriptor that enforces integer type on assignment, allowing None.

    Like IntField but accepts None values for optional fields.

    Example usage:
        class MyClass:
            owner_part_id = OptionalIntField()  # Can be None or int
    """

    def __init__(
        self, *, minimum: int | None = None, maximum: int | None = None
    ) -> None:
        self.minimum = minimum
        self.maximum = maximum
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
            parsed = int(value)
            if self.minimum is not None and parsed < self.minimum:
                raise ValueError(f"{self.name} is below {self.minimum}")
            if self.maximum is not None and parsed > self.maximum:
                raise ValueError(f"{self.name} is above {self.maximum}")
            setattr(obj, self.private_name, parsed)


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

    # Harness-layout document records
    HARNESS_COMPONENT = 106
    HARNESS_SPLICE = 108
    HARNESS_LAYOUT_LABEL = 109
    HARNESS_LAYOUT_CONNECTION_POINT = 110
    HARNESS_BUNDLE = 111
    LINE_VIEW = 126
    HARNESS_LAYOUT_COVERING = 128
    REUSE_BLOCK_IMPLEMENTATION_INFO = 138
    HARNESS_CAVITY = 140
    HARNESS_CAVITY_COMPONENT = 141

    # Additional records
    NOTE = 209  # Annotation note
    COMPILE_MASK = 211  # Compile mask
    HARNESS_CONNECTOR = 215  # Harness connector
    HARNESS_ENTRY = 216  # Harness entry point
    HARNESS_TYPE = 217  # Harness type definition
    SIGNAL_HARNESS = 218  # Signal harness
    HIGH_LEVEL_CODE_SYMBOL = 220
    HIGH_LEVEL_CODE_ENTRY = 221
    HIGH_LEVEL_CODE_NAME = 222
    HIGH_LEVEL_CODE_FILE_NAME = 223
    BLANKET = 225  # Blanket directive
    HYPERLINK = 226  # Hyperlink
    RICH_TEXT_DOCUMENT = 240
    RTF_LINK = 241
    OBJECT_DEFINITION = 129  # ObjectDefinitions stream metadata


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
    Legacy/TV6 PCB layer IDs used by PcbDoc and PcbLib primitive APIs.

    Enum values map to Altium's compact legacy layer enum. That enum contains
    Top, Mid1 through Mid30, Bottom, and Mechanical 1 through Mechanical 16
    only; values 73, 74, and 75 are Drill Drawing, Multi-Layer, and Connect.
    Extended mechanical layers such as Mechanical 17 and AD 26.8.1 extended
    signal layers such as Mid31 use separate serialized V7 saved-layer
    identities and must not be represented by adding linear `PcbLayer` enum
    values.

    Current public authoring methods accept these enum values for legacy
    `layer` arguments. Some record families also carry V7 side fields, but this
    enum is intentionally not the V7 layer-reference model.
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

    def to_display_name(self) -> str:
        """
        Return Altium's default human-facing label for this layer enum.

        This is not board-stack-aware. For parsed PcbDoc files, use
        ResolvedLayerStack when user-renamed board layers must be preserved.
        """
        return _LAYER_TO_DISPLAY_NAME.get(self, f"Unknown ({self.value})")

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

_LAYER_TO_DISPLAY_NAME: dict[PcbLayer, str] = {
    PcbLayer.TOP: "Top Layer",
    PcbLayer.BOTTOM: "Bottom Layer",
    PcbLayer.TOP_OVERLAY: "Top Overlay",
    PcbLayer.BOTTOM_OVERLAY: "Bottom Overlay",
    PcbLayer.TOP_PASTE: "Top Paste",
    PcbLayer.BOTTOM_PASTE: "Bottom Paste",
    PcbLayer.TOP_SOLDER: "Top Solder",
    PcbLayer.BOTTOM_SOLDER: "Bottom Solder",
    PcbLayer.DRILL_GUIDE: "Drill Guide",
    PcbLayer.KEEPOUT: "Keep-Out Layer",
    PcbLayer.DRILL_DRAWING: "Drill Drawing",
    PcbLayer.MULTI_LAYER: "Multi-Layer",
    PcbLayer.CONNECT: "Connections",
}
for _i in range(1, 31):
    _LAYER_TO_DISPLAY_NAME[PcbLayer(1 + _i)] = f"Mid-Layer {_i}"
for _i in range(1, 17):
    _LAYER_TO_DISPLAY_NAME[PcbLayer(38 + _i)] = f"Internal Plane {_i}"
for _i in range(1, 17):
    _LAYER_TO_DISPLAY_NAME[PcbLayer(56 + _i)] = f"Mechanical {_i}"

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
    setattr(record, "location", value.to_coord_point())


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
    def record_type(self) -> IntEnum:
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


class _AccessibilityFlag:
    """Preserve the public flag while tracking assignments for private snapshots."""

    @overload
    def __get__(
        self, obj: None, objtype: type["SchPrimitive"] | None = None
    ) -> "_AccessibilityFlag": ...

    @overload
    def __get__(
        self, obj: "SchPrimitive", objtype: type["SchPrimitive"] | None = None
    ) -> bool: ...

    def __get__(
        self, obj: "SchPrimitive | None", objtype: type["SchPrimitive"] | None = None
    ) -> "bool | _AccessibilityFlag":
        if obj is None:
            return self
        return cast(bool, vars(obj)["is_not_accessible"])

    def __set__(self, obj: "SchPrimitive", value: bool) -> None:
        vars(obj)["is_not_accessible"] = value
        # Even assigning the same value is an explicit write to engine state.
        obj._accessibility_assignment_revision += 1


class SchPrimitive(Primitive, _ManagedUniqueIdOwner):
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
    is_not_accessible = _AccessibilityFlag()

    def __init__(self) -> None:
        super().__init__()
        # Initialize via descriptors (will call __set__)
        self.owner_index = -1
        self.owner_part_id = None
        self.owner_part_display_mode = None
        self._accessibility_assignment_revision = 0
        self.is_not_accessible = False
        self._graphically_locked: bool = False
        self._graphically_locked_dirty = False
        # Auto-generate a unique_id for newly created objects.
        # Parsed records will overwrite with actual value from file
        self.unique_id: str | None = generate_unique_id()
        self._is_unique_id_locked: bool = False
        self.index_in_sheet = -1  # Detached managed-object sentinel
        # Parent reference is set during hierarchy building and is not serialized.
        self.parent: SchPrimitive | None = None
        # Narrow document/library binding context for resource resolution such
        # as fonts. This is not serialized and does not imply ownership.
        self._bound_schematic_context: Any | None = None
        self._capture_primitive_source_state()

    def _capture_primitive_source_state(self) -> None:
        """Remember managed metadata state for sparse four-state serialization."""
        self._source_owner_index = self.owner_index
        self._source_owner_part_id = self.owner_part_id
        self._source_owner_part_display_mode = self.owner_part_display_mode
        self._source_is_not_accessible = self.is_not_accessible
        self._source_graphically_locked = self.graphically_locked
        self._graphically_locked_dirty = False
        self._source_index_in_sheet = self.index_in_sheet

    def _apply_authored_graphical_metadata_defaults(self) -> None:
        """Apply SchDataGraphicalObject detached defaults to adapter families."""
        self.owner_part_id = -1
        self.owner_part_display_mode = 0
        self._graphically_locked = False
        self._capture_primitive_source_state()

    def _apply_imported_graphical_metadata_defaults(self) -> None:
        """Apply ImportGraphicalObject zero/false semantics after base import."""
        from .altium_serializer import AltiumSerializer

        serializer = AltiumSerializer()
        self.owner_part_id = _require_int_width(
            "OwnerPartId",
            serializer._read_int_checked(self._record, "OwnerPartId", default=0)[0],
            -(1 << 15),
            (1 << 15) - 1,
        )
        self.owner_part_display_mode = _require_int_width(
            "OwnerPartDisplayMode",
            serializer._read_int_checked(
                self._record, "OwnerPartDisplayMode", default=0
            )[0],
            0,
            (1 << 8) - 1,
        )
        self._graphically_locked = False
        self._capture_primitive_source_state()

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

        from .altium_serializer import AltiumSerializer

        serializer = AltiumSerializer()
        self.owner_index = serializer._read_int_checked(r, "OwnerIndex", default=0)[0]
        owner_part_id, owner_part_id_present = serializer._read_int_checked(
            r, "OwnerPartId", default=0
        )
        self.owner_part_id = (
            _require_int_width("OwnerPartId", owner_part_id, -(1 << 15), (1 << 15) - 1)
            if owner_part_id_present
            else None
        )
        owner_part_display_mode, owner_part_display_mode_present = (
            serializer._read_int_checked(r, "OwnerPartDisplayMode", default=0)
        )
        self.owner_part_display_mode = (
            _require_int_width(
                "OwnerPartDisplayMode",
                owner_part_display_mode,
                0,
                (1 << 8) - 1,
            )
            if owner_part_display_mode_present
            else None
        )

        # Note: Altium has a typo - "IsNotAccesible" (missing 's')
        self.is_not_accessible = r.get("IsNotAccesible", "F") == "T"
        self._graphically_locked = r.get("GraphicallyLocked", "F") == "T"
        # Preserve the parser's established missing-field representation. The
        # managed identity helpers operate only on explicit identity strings.
        self.unique_id = r.get("UniqueID")

        # Z-order position - descriptor handles int conversion
        self.index_in_sheet = serializer._read_int_checked(
            r, "IndexInSheet", default=0
        )[0]
        self._capture_primitive_source_state()

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize common SchPrimitive fields, preserving original structure.
        """
        self._validate_primitive_metadata_widths()
        # Start with raw record if available (for round-trip)
        record = self._get_base_record()

        self._serialize_managed_int(
            record,
            "OWNERINDEX",
            ["OwnerIndex", "OWNERINDEX"],
            self.owner_index,
            self._source_owner_index,
        )
        self._serialize_managed_bool(
            record,
            "ISNOTACCESIBLE",
            ["IsNotAccesible", "ISNOTACCESIBLE"],
            self.is_not_accessible,
            self._source_is_not_accessible,
        )
        if self.index_in_sheet == -2:
            self._remove_fields_case_insensitively(
                record, ["IndexInSheet", "INDEXINSHEET"]
            )
        else:
            self._serialize_managed_optional_int(
                record,
                "INDEXINSHEET",
                ["IndexInSheet", "INDEXINSHEET"],
                self.index_in_sheet,
                self._source_index_in_sheet,
            )
        self._serialize_managed_optional_int(
            record,
            "OWNERPARTID",
            ["OwnerPartId", "OWNERPARTID"],
            self.owner_part_id,
            self._source_owner_part_id,
        )
        self._serialize_managed_optional_int(
            record,
            "OWNERPARTDISPLAYMODE",
            ["OwnerPartDisplayMode", "OWNERPARTDISPLAYMODE"],
            self.owner_part_display_mode,
            self._source_owner_part_display_mode,
        )
        if self._graphically_locked_dirty:
            self._remove_fields_case_insensitively(
                record, ["GraphicallyLocked", "GRAPHICALLYLOCKED"]
            )
            if self.graphically_locked:
                self._update_field(
                    record,
                    "GRAPHICALLYLOCKED",
                    "T",
                    ["GraphicallyLocked", "GRAPHICALLYLOCKED"],
                    force=True,
                )
        else:
            self._serialize_managed_bool(
                record,
                "GRAPHICALLYLOCKED",
                ["GraphicallyLocked", "GRAPHICALLYLOCKED"],
                self.graphically_locked,
                self._source_graphically_locked,
            )

        # UniqueID is family-owned in V5; subclasses may move it to the exact
        # exporter position after writing their fields.
        if (
            self.unique_id
            and not getattr(self, "_supports_managed_geometry_dynamic_unique_id", False)
            and not getattr(self, "_family_owns_dynamic_unique_id", False)
        ):
            self._update_field(
                record, "UNIQUEID", self.unique_id, ["UniqueID", "UNIQUEID"]
            )

        return record

    def _validate_primitive_metadata_widths(self) -> None:
        _require_int_width("OwnerIndex", self.owner_index, -(1 << 31), (1 << 31) - 1)
        _require_optional_int_width(
            "OwnerPartId", self.owner_part_id, -(1 << 15), (1 << 15) - 1
        )
        _require_optional_int_width(
            "OwnerPartDisplayMode", self.owner_part_display_mode, 0, (1 << 8) - 1
        )
        _require_optional_int_width(
            "IndexInSheet", self.index_in_sheet, -(1 << 31), (1 << 31) - 1
        )

    @property
    def graphically_locked(self) -> bool:
        return self._graphically_locked

    @graphically_locked.setter
    def graphically_locked(self, value: bool) -> None:
        self._graphically_locked = bool(value)
        self._graphically_locked_dirty = True

    def _serialize_managed_int(
        self,
        record: dict[str, object],
        canonical: str,
        names: list[str],
        value: int,
        source: int,
    ) -> None:
        self._serialize_managed_optional_int(record, canonical, names, value, source)

    def _serialize_managed_optional_int(
        self,
        record: dict[str, object],
        canonical: str,
        names: list[str],
        value: int | None,
        source: int | None,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        self._remove_fields_case_insensitively(record, names)
        if value not in (None, 0):
            self._update_field(record, canonical, value, names, force=True)

    def _serialize_managed_font_id(
        self,
        record: _RecordFields,
        serializer: "AltiumSerializer",
        field: str,
        value: int,
        font_manager: "FontIDManager | None",
        *,
        default: int = 1,
    ) -> None:
        source, _ = serializer.read_font_id(
            self._raw_record or {}, field, font_manager, default=default
        )
        if self._raw_record is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value != 0:
            serializer.write_font_id(record, field, value, font_manager, None)

    def _serialize_managed_bool(
        self,
        record: dict[str, object],
        canonical: str,
        names: list[str],
        value: bool,
        source: bool,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        self._remove_fields_case_insensitively(record, names)
        if value:
            self._update_field(record, canonical, "T", names, force=True)

    def _serialize_managed_string(
        self,
        record: dict[str, object],
        canonical: str,
        names: list[str],
        value: str,
        source: str,
    ) -> None:
        if self._raw_record is not None and value == source:
            return
        self._remove_fields_case_insensitively(record, names)
        if value:
            self._update_field(record, canonical, value, names, force=True)

    @staticmethod
    def _remove_fields_case_insensitively(
        record: dict[str, object], names: list[str]
    ) -> None:
        folded = {name.casefold() for name in names}
        for key in list(record):
            if key.casefold() in folded:
                record.pop(key)

    @staticmethod
    def _move_fields_to_end_case_insensitively(
        record: dict[str, object], names: list[str]
    ) -> None:
        folded = {name.casefold() for name in names}
        moved = [
            (key, record.pop(key)) for key in list(record) if key.casefold() in folded
        ]
        record.update(moved)

    def _order_authored_graphical_fields(
        self,
        record: dict[str, object],
        family_order: tuple[str, ...],
    ) -> dict[str, object]:
        """Return new graphical records in the managed V5 export sequence."""
        if self._raw_record is not None:
            return record
        base_order = (
            "RECORD",
            "OwnerIndex",
            "IsNotAccesible",
            "OwnerIndexAdditionalList",
            "IndexInSheet",
            "IgnoreOnLoad",
            "WiringDiagramOriginUniqueId",
            "IsSchematicBlockObject",
            "UniqueIDInReuseBlock",
            "OwnerPartId",
            "OwnerPartDisplayMode",
            "SelectionMemory",
            "UnionIndex",
            "GraphicallyLocked",
        )
        return self._order_fields_case_insensitively(
            record, (*base_order, *family_order)
        )

    def _order_changed_graphical_family_fields(
        self,
        record: dict[str, object],
        family_order: tuple[str, ...],
        *,
        changed: bool,
    ) -> dict[str, object]:
        """Canonicalize one changed V5 family without moving unrelated fields."""
        if self._raw_record is None:
            return self._order_authored_graphical_fields(record, family_order)
        if not changed:
            return record
        family_names = set(map(str.casefold, family_order))
        family_names.update(map(str.casefold, map("%UTF8%{}".format, family_order)))
        ordered_family = self._order_fields_case_insensitively(record, family_order)
        family_items = [
            (key, value)
            for key, value in ordered_family.items()
            if key.casefold() in family_names
        ]
        return self._replace_graphical_family_fields(record, family_names, family_items)

    @staticmethod
    def _replace_graphical_family_fields(
        record: dict[str, object],
        family_names: set[str],
        family_items: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, Any] = {}
        emitted = False
        for key, value in record.items():
            if key.casefold() in family_names:
                if not emitted:
                    result.update(family_items)
                    emitted = True
                continue
            result[key] = value
        if not emitted:
            result.update(family_items)
        return result

    def _order_fields_case_insensitively(
        self,
        record: dict[str, object],
        ordered_names: tuple[str, ...],
    ) -> dict[str, object]:
        """Order owned fields while retaining unknown fields and UTF8 companions."""
        by_name = {key.casefold(): (key, value) for key, value in record.items()}
        result: dict[str, Any] = {}
        emitted: set[str] = set()
        for name in ordered_names:
            if name == "__Vertices__":
                for key, value in record.items():
                    if self._is_vertex_field(key) and key.casefold() not in emitted:
                        result[key] = value
                        emitted.add(key.casefold())
                continue
            item = by_name.get(name.casefold())
            if item is not None:
                result[item[0]] = item[1]
                emitted.add(item[0].casefold())
            utf8_name = f"%UTF8%{name}"
            utf8_item = by_name.get(utf8_name.casefold())
            if utf8_item is not None:
                result[utf8_item[0]] = utf8_item[1]
                emitted.add(utf8_item[0].casefold())
        for key, value in record.items():
            if key.casefold() not in emitted:
                result[key] = value
        return result

    def _init_family_dynamic_unique_id(self) -> None:
        """Mark a V5 family whose UniqueID field uses DynamicString."""
        self._family_owns_dynamic_unique_id = True
        self._has_family_unique_id = False
        self._family_unique_id_used_utf8 = False
        self._source_family_unique_id = self.unique_id or ""

    def _parse_family_dynamic_unique_id(
        self, serializer: "AltiumSerializer", record: dict[str, object]
    ) -> None:
        from .altium_serializer import read_dynamic_string_field

        value, present, used_utf8 = read_dynamic_string_field(
            serializer, record, self._record, "UniqueID", default=""
        )
        self.unique_id = value if present else None
        self._has_family_unique_id = present
        self._family_unique_id_used_utf8 = used_utf8
        self._source_family_unique_id = value

    def _serialize_family_dynamic_unique_id(
        self, record: dict[str, object], serializer: "AltiumSerializer"
    ) -> None:
        from .altium_serializer import write_dynamic_string_field

        value = self.unique_id or ""
        if self._raw_record is not None and value == self._source_family_unique_id:
            return
        if not value:
            self._remove_fields_case_insensitively(
                record, ["UniqueID", "%UTF8%UniqueID"]
            )
            return
        write_dynamic_string_field(
            serializer,
            record,
            "UniqueID",
            value,
            raw_record=self._raw_record,
            used_utf8_sidecar=self._family_unique_id_used_utf8,
            was_present=self._has_family_unique_id,
            force=True,
        )

    @staticmethod
    def _is_vertex_field(key: str) -> bool:
        normalized = key.casefold()
        if normalized in {"locationcount", "extralocationcount"}:
            return True
        for prefix in ("ex", "ey", "x", "y"):
            if not normalized.startswith(prefix):
                continue
            suffix = normalized[len(prefix) :].removesuffix("_frac")
            return bool(suffix) and suffix.isascii() and suffix.isdigit()
        return False

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
        self._apply_authored_graphical_metadata_defaults()
        self._supports_managed_geometry_wiring_origin = (
            self.record_type.value in _MANAGED_GEOMETRY_WIRING_ORIGIN_RECORD_CODES
        )
        self._supports_managed_geometry_dynamic_unique_id = (
            self.record_type.value in _MANAGED_GEOMETRY_DYNAMIC_UNIQUE_ID_RECORD_CODES
        )
        if self._supports_managed_geometry_wiring_origin:
            self.wiring_diagram_origin_unique_id = ""
            self._source_wiring_diagram_origin_unique_id = ""
            self._wiring_diagram_origin_unique_id_used_utf8 = False
        if self._supports_managed_geometry_dynamic_unique_id:
            self._source_geometry_unique_id = self.unique_id
            self._geometry_unique_id_used_utf8 = False
        self.location = CoordPoint()
        # CRITICAL: Use None for colors, not defaults
        # When None, these fields are omitted from serialized output
        # Altium will use its own rendering defaults when displaying the symbol
        self.color: int | None = (
            None  # Win32 format (0x00BBGGRR), None if not specified
        )
        self._area_color: int | None = None
        self._area_color_dirty = False
        # Track which location fields were present in original
        self._has_location_x: bool = False
        self._has_location_y: bool = False
        self._has_color: bool = False
        self._has_area_color: bool = False
        self._capture_graphical_source_state()
        self._capture_primitive_source_state()

    def _capture_graphical_source_state(self) -> None:
        """Remember parsed color state so semantic defaults stay sparse on write."""
        self._source_color = self.color
        self._source_area_color = self.area_color
        self._area_color_dirty = False

    def _apply_imported_color_defaults(self, *, area_color: bool) -> None:
        """Apply V5 missing-field semantics without materializing absent colors."""
        if not self._has_color:
            self.color = 0
        if area_color and not self._has_area_color:
            self.area_color = 0
        self._capture_graphical_source_state()

    def _apply_nonpersisted_area_color_default(self) -> None:
        """Keep the inherited managed fill state without persisting AreaColor."""
        self._area_color = GRAPHICAL_FILL_COLOR
        self._source_area_color = GRAPHICAL_FILL_COLOR
        self._area_color_dirty = False

    @property
    def area_color(self) -> int | None:
        return self._area_color

    @area_color.setter
    def area_color(self, value: int | None) -> None:
        self._area_color = value
        self._area_color_dirty = True

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

        self._parse_geometry_dynamic_metadata(r)
        self._parse_graphical_location(record, r)
        self._parse_graphical_colors(record)
        self._apply_imported_graphical_metadata_defaults()
        self._capture_graphical_source_state()

    def _parse_geometry_dynamic_metadata(self, record: CaseInsensitiveDict) -> None:
        if not self._supports_managed_geometry_wiring_origin:
            return

        from .altium_serializer import process_mbcs_string

        wiring_name = "WiringDiagramOriginUniqueId"
        wiring_utf8_name = f"%UTF8%{wiring_name}"
        wiring_utf8_value = record.get(wiring_utf8_name)
        wiring_value = record.get(wiring_name)
        self._wiring_diagram_origin_unique_id_used_utf8 = wiring_utf8_value is not None
        self.wiring_diagram_origin_unique_id = process_mbcs_string(
            str(
                wiring_utf8_value
                if wiring_utf8_value is not None
                else wiring_value or ""
            )
        )
        self._source_wiring_diagram_origin_unique_id = (
            self.wiring_diagram_origin_unique_id
        )
        if self._supports_managed_geometry_dynamic_unique_id:
            unique_id_utf8 = record.get("%UTF8%UniqueID")
            unique_id_ordinary = record.get("UniqueID")
            self._geometry_unique_id_used_utf8 = unique_id_utf8 is not None
            unique_id_value = (
                unique_id_utf8 if unique_id_utf8 is not None else unique_id_ordinary
            )
            self.unique_id = (
                process_mbcs_string(str(unique_id_value))
                if unique_id_value is not None
                else None
            )
            self._source_geometry_unique_id = self.unique_id

    def _parse_graphical_location(
        self, raw_record: _RecordFields, record: CaseInsensitiveDict
    ) -> None:
        # Parse location with fractional parts
        # NOTE: Altium uses mixed case: Location.X (not LOCATION.X)
        # Track if fields were present
        self._has_location_x = "Location.X" in raw_record or "LOCATION.X" in raw_record
        self._has_location_y = "Location.Y" in raw_record or "LOCATION.Y" in raw_record

        ignores_location = self.record_type in {
            SchRecordType.POLYLINE,
            SchRecordType.POLYGON,
            SchRecordType.BEZIER,
        }
        # Vertex families do not import inherited graphical Location fields.
        if ignores_location:
            x, x_frac, y, y_frac = 0, 0, 0, 0
        else:
            from .altium_serializer import AltiumSerializer

            serializer = AltiumSerializer()
            x = self._read_graphical_whole(record.get("Location.X", 0))
            y = self._read_graphical_whole(record.get("Location.Y", 0))
            x_frac, _ = serializer.read_int(record, "Location.X_Frac")
            y_frac, _ = serializer.read_int(record, "Location.Y_Frac")
        self.location = CoordPoint(x, y, x_frac, y_frac)

    @staticmethod
    def _read_graphical_whole(value: object) -> int:
        """Read the established graphical float adapter within Coord width."""
        try:
            parsed = int(float(str(value))) if value else 0
        except (TypeError, ValueError, OverflowError):
            return 0
        return parsed if -(1 << 15) <= parsed <= (1 << 15) - 1 else 0

    def _parse_graphical_colors(self, record: _RecordFields) -> None:
        from .altium_serializer import AltiumSerializer

        serializer = AltiumSerializer()
        self._has_color = "Color" in record or "COLOR" in record
        color, _ = serializer.read_color(record, "Color", default=0)
        if self._has_color:
            self.color = color

        self._has_area_color = "AreaColor" in record or "AREACOLOR" in record
        ignores_area_color = self.record_type in {
            SchRecordType.LINE,
            SchRecordType.ARC,
            SchRecordType.ELLIPTICAL_ARC,
            SchRecordType.POLYLINE,
            SchRecordType.BEZIER,
            SchRecordType.WIRE,
            SchRecordType.BUS,
            SchRecordType.BUS_ENTRY,
            SchRecordType.JUNCTION,
            SchRecordType.NET_LABEL,
            SchRecordType.NO_ERC,
            SchRecordType.LABEL,
            SchRecordType.HYPERLINK,
            SchRecordType.PARAMETER_SET,
            SchRecordType.POWER_PORT,
            SchRecordType.IMAGE,
        }
        if self._has_area_color and not ignores_area_color:
            self.area_color = serializer.read_color(record, "AreaColor", default=0)[0]

    def serialize_to_record(self) -> dict[str, Any]:
        """
        Serialize common SchGraphicalObject fields, preserving original structure.
        """
        record = super().serialize_to_record()
        if self._supports_managed_geometry_dynamic_unique_id:
            self._serialize_geometry_unique_id(record)
        if self._supports_managed_geometry_wiring_origin:
            self._serialize_wiring_diagram_origin_unique_id(record)
        self._serialize_graphical_location(record)
        self._serialize_graphical_colors(record)
        return record

    def _serialize_geometry_unique_id(self, record: _RecordFields) -> None:
        value = self.unique_id or ""
        source = self._source_geometry_unique_id or ""
        if self._raw_record is not None and value == source:
            return
        if not value:
            self._remove_fields_case_insensitively(
                record, ["UniqueID", "%UTF8%UniqueID"]
            )
            return
        if self._geometry_unique_id_used_utf8:
            self._update_field(
                record,
                "%UTF8%UNIQUEID",
                value,
                ["%UTF8%UniqueID", "%UTF8%UNIQUEID"],
                force=True,
            )
            return
        self._remove_fields_case_insensitively(record, ["%UTF8%UniqueID"])
        self._update_field(
            record, "UNIQUEID", value, ["UniqueID", "UNIQUEID"], force=True
        )

    def _move_geometry_identity_to_end_if_needed(self, record: _RecordFields) -> None:
        if not self._supports_managed_geometry_dynamic_unique_id:
            self._move_fields_to_end_case_insensitively(
                record, ["UniqueID", "%UTF8%UniqueID"]
            )
            return
        if (
            self._raw_record is None
            or self.unique_id != self._source_geometry_unique_id
        ):
            self._move_fields_to_end_case_insensitively(
                record, ["UniqueID", "%UTF8%UniqueID"]
            )

    def _serialize_wiring_diagram_origin_unique_id(self, record: _RecordFields) -> None:
        field = "WiringDiagramOriginUniqueId"
        utf8_field = f"%UTF8%{field}"
        value = self.wiring_diagram_origin_unique_id
        changed = value != self._source_wiring_diagram_origin_unique_id
        if self._raw_record is not None and not changed:
            return
        if not value:
            self._remove_fields_case_insensitively(record, [field, utf8_field])
            return
        if self._wiring_diagram_origin_unique_id_used_utf8:
            self._update_field(
                record,
                utf8_field.upper(),
                value,
                [utf8_field, utf8_field.upper()],
                force=True,
            )
            return
        self._remove_fields_case_insensitively(record, [utf8_field])
        self._update_field(
            record,
            field.upper(),
            value,
            [field, field.upper()],
            force=True,
        )

    def _serialize_graphical_location(self, record: _RecordFields) -> None:
        self._serialize_graphical_axis(
            record,
            axis="X",
            value=self.location.x,
            fraction=self.location.x_frac,
            was_present=self._has_location_x,
        )
        self._serialize_graphical_axis(
            record,
            axis="Y",
            value=self.location.y,
            fraction=self.location.y_frac,
            was_present=self._has_location_y,
        )

    def _serialize_graphical_axis(
        self,
        record: _RecordFields,
        *,
        axis: str,
        value: int,
        fraction: int,
        was_present: bool,
    ) -> None:
        canonical = f"LOCATION.{axis}"
        pascal = f"Location.{axis}"
        frac_canonical = f"LOCATION.{axis}_FRAC"
        frac_pascal = f"Location.{axis}_Frac"
        source_aware_location = self._supports_managed_geometry_wiring_origin or (
            self.record_type
            in {
                SchRecordType.SHEET_SYMBOL,
                SchRecordType.SHEET_NAME,
                SchRecordType.FILE_NAME,
                SchRecordType.HARNESS_CONNECTOR,
                SchRecordType.HARNESS_TYPE,
            }
        )
        if source_aware_location:
            from .altium_serializer import AltiumSerializer

            serializer = AltiumSerializer()
            source_value, source_fraction, _ = serializer.read_coord(
                self._raw_record or {}, "Location", axis
            )
            if self._raw_record is not None and (value, fraction) == (
                source_value,
                source_fraction,
            ):
                return
            self._remove_fields_case_insensitively(
                record, [pascal, canonical, frac_pascal, frac_canonical]
            )
            if value != 0 or fraction != 0:
                serializer.write_coord(
                    record,
                    "Location",
                    axis,
                    value,
                    fraction,
                    force=True,
                )
            return
        from .altium_serializer import require_coordinate_wire_parts

        require_coordinate_wire_parts(value, fraction, pascal)
        if was_present or value != 0 or fraction != 0:
            self._update_field(
                record,
                canonical,
                value,
                [pascal, canonical],
                force=value != 0 or fraction != 0,
            )
        if fraction != 0:
            self._update_field(
                record,
                frac_canonical,
                fraction,
                [frac_pascal, frac_canonical],
                force=True,
            )

    def _serialize_graphical_colors(self, record: _RecordFields) -> None:
        self._serialize_graphical_color(
            record,
            value=self.color,
            source_value=self._source_color,
            was_present=self._has_color,
            canonical_name="COLOR",
            field_names=["Color", "COLOR"],
        )
        nonpersisted_area_color = self.record_type in {
            SchRecordType.LINE,
            SchRecordType.ARC,
            SchRecordType.ELLIPTICAL_ARC,
            SchRecordType.POLYLINE,
            SchRecordType.BEZIER,
            SchRecordType.WIRE,
            SchRecordType.BUS,
            SchRecordType.BUS_ENTRY,
            SchRecordType.JUNCTION,
            SchRecordType.NET_LABEL,
            SchRecordType.NO_ERC,
            SchRecordType.LABEL,
            SchRecordType.HYPERLINK,
            SchRecordType.PARAMETER_SET,
            SchRecordType.POWER_PORT,
        }
        if nonpersisted_area_color:
            if self._raw_record is None or self._area_color_dirty:
                self._remove_fields_case_insensitively(
                    record, ["AreaColor", "AREACOLOR"]
                )
        else:
            self._serialize_graphical_color(
                record,
                value=self.area_color,
                source_value=self._source_area_color,
                was_present=self._has_area_color,
                canonical_name="AREACOLOR",
                field_names=["AreaColor", "AREACOLOR"],
            )

    def _serialize_graphical_color(
        self,
        record: _RecordFields,
        *,
        value: int | None,
        source_value: int | None,
        was_present: bool,
        canonical_name: str,
        field_names: list[str],
    ) -> None:
        value = self._require_optional_color(value)
        if self._raw_record is not None and value == source_value:
            return
        if self._supports_managed_geometry_wiring_origin:
            self._remove_fields_case_insensitively(record, field_names)
            if value not in (None, 0):
                self._update_field(
                    record, canonical_name, value, field_names, force=True
                )
            return
        if self._raw_record is None and value == 0:
            self._remove_field(record, field_names)
            return
        if not was_present and value == source_value:
            return
        if value is None:
            self._remove_field(record, field_names)
            return
        self._update_field(
            record,
            canonical_name,
            value,
            field_names,
            force=value != source_value,
        )

    @staticmethod
    def _require_optional_color(value: int | None) -> int | None:
        if value is None:
            return None
        from .altium_serializer import require_color_wire_value

        return require_color_wire_value(value)

    def _serialize_managed_family_int(
        self,
        record: _RecordFields,
        serializer: "AltiumSerializer",
        field: str,
        value: int,
    ) -> None:
        source, _ = serializer.read_int(self._raw_record or {}, field, default=0)
        if self._raw_record is not None and value == source:
            return
        if value == 0:
            serializer.remove_field(record, field)
            return
        serializer.write_int(record, field, value, self._raw_record, force=True)

    def _serialize_managed_family_color(
        self,
        record: _RecordFields,
        serializer: "AltiumSerializer",
        field: str,
        value: int,
    ) -> None:
        source, _ = serializer.read_color(self._raw_record or {}, field, default=0)
        if self._raw_record is not None and value == source:
            return
        if value == 0:
            serializer.remove_field(record, field)
            return
        serializer.write_color(record, field, value, self._raw_record, force=True)

    def _serialize_managed_family_bool(
        self,
        record: _RecordFields,
        serializer: "AltiumSerializer",
        field: str,
        value: bool,
    ) -> None:
        source, _ = serializer.read_bool(self._raw_record or {}, field, default=False)
        if self._raw_record is not None and value == source:
            return
        serializer.remove_field(record, field)
        if value:
            serializer.write_bool(record, field, True, None, force=True)

    def _serialize_managed_family_coord(
        self,
        record: _RecordFields,
        serializer: "AltiumSerializer",
        base: str,
        prefix: str,
        value: int,
        fraction: int,
    ) -> None:
        source_value, source_fraction, _ = serializer.read_coord(
            self._raw_record or {}, base, prefix
        )
        if self._raw_record is not None and (value, fraction) == (
            source_value,
            source_fraction,
        ):
            return
        field = f"{base}.{prefix}" if prefix else base
        frac_field = f"{field}_Frac"
        serializer.remove_field(record, field)
        serializer.remove_field(record, frac_field)
        if value != 0 or fraction != 0:
            serializer.write_coord(
                record,
                base,
                prefix,
                value,
                fraction,
                force=True,
            )

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

    def parse_from_binary(self, data: bytes, offset: int = 0) -> int | None:
        """
        Parse primitive from binary data.

        Args:
            data: Binary data starting with type byte
        """
        _ = offset
        self._raw_binary = data
        return None

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


class PcbV7LayerTokenMixin:
    """
    Mixin for PCB records that persist their V7 layer as V7_LAYER text.

    Host classes must provide a ``properties`` dict holding native
    pipe-separated record properties.
    """

    properties: dict[str, str]

    @property
    def v7_layer(self) -> str:
        """Return the native V7_LAYER property text when present."""

        return str(self.properties.get("V7_LAYER", "")).strip()

    @v7_layer.setter
    def v7_layer(self, value: str) -> None:
        text = str(value or "").strip()
        if text:
            self.properties["V7_LAYER"] = text
        else:
            self.properties.pop("V7_LAYER", None)


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
