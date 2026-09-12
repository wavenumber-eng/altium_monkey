"""
Central Serializer for Altium Record Fields

Provides unified read/write operations for all record types, handling:
- Case mode (UPPERCASE for SchLib, PascalCase for SchDoc)
- Type conversion (int, bool, str, color, coord)
- Font ID translation (file ID <-> internal ID)
- Field presence tracking for round-trip support

Architecture:
    AltiumSerializer is stateless and receives context per-call.
    Font translation requires a FontIDManager instance.

    # Reading
    x = serializer.read_int(record, 'Location.X', default=0)
    is_solid = serializer.read_bool(record, 'IsSolid', default=False)
    font_id = serializer.read_font_id(record, 'FontID', font_manager)

    # Writing
    serializer.write_int(record, 'Location.X', value, raw_record)
    serializer.write_bool(record, 'IsSolid', True, raw_record)
    serializer.write_font_id(record, 'FontID', internal_id, font_manager, raw_record)
"""

from __future__ import annotations

import logging
import math
import re
import struct
import zlib
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .altium_font_manager import FontIDManager

log = logging.getLogger(__name__)


# =============================================================================
# Constants (native Feature Parity)
# =============================================================================

# Double tolerance for zero comparison
DOUBLE_TOLERANCE = 1e-06

# Invalid characters for OLE stream names
# Implementation note: serializer parameter implementation:24 invalidNameChars
INVALID_STREAM_CHARS = frozenset('\\/:*?"<>|!')
MAX_STREAM_NAME_LEN = 31

# Special characters for MBCS processing
# Implementation note: StrUtils.ProcessMBCSString
MBCS_ESCAPE_CHAR = "\u008e"  # byte 142 - escape character
BROKEN_BAR_CHAR = "\u00a6"  # U+00A6 - escaped pipe

# Binary string encoding
# Binary strings use DXP.Utils.EncodingDefault (Windows-1252/ANSI), NOT UTF-8!
BINARY_STRING_ENCODING = "cp1252"

_ASCII_WHITESPACE = " \t\n\r\v\f"
_ASCII_INTEGER = re.compile(r"[+-]?[0-9](?:_?[0-9])*")
_ASCII_FLOAT = re.compile(
    r"[+-]?(?:(?:[0-9](?:_?[0-9])*)(?:\.(?:[0-9](?:_?[0-9])*)?)?"
    r"|\.(?:[0-9](?:_?[0-9])*))(?:[eE][+-]?(?:[0-9](?:_?[0-9])*))?"
)
_ASCII_NONFINITE = re.compile(r"[+-]?(?:inf(?:inity)?|nan)", re.IGNORECASE)
_MANAGED_FLOAT_INFINITY = "\u221e"
_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1
_I16_MIN = -(1 << 15)
_I16_MAX = (1 << 15) - 1
_I64_MIN = -(1 << 63)
_I64_MAX = (1 << 63) - 1
_U32_MIN = 0
_U32_MAX = (1 << 32) - 1


def _parse_ascii_integer(value: object, minimum: int, maximum: int) -> int:
    """Parse the reviewed portable decimal grammar within a signed width."""
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer field value")
    text = str(value).strip(_ASCII_WHITESPACE)
    if _ASCII_INTEGER.fullmatch(text) is None:
        raise ValueError("expected an ASCII decimal integer")
    parsed = int(text.replace("_", ""), 10)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"integer is outside [{minimum}, {maximum}]")
    return parsed


def _parse_ascii_float(value: object, *, single: bool) -> float:
    """Parse the reviewed portable real grammar and optionally quantize to f32."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a real field value")
    text = str(value).strip(_ASCII_WHITESPACE)
    if single and text in {_MANAGED_FLOAT_INFINITY, f"-{_MANAGED_FLOAT_INFINITY}"}:
        return math.inf if text == _MANAGED_FLOAT_INFINITY else -math.inf
    explicit_nonfinite = _ASCII_NONFINITE.fullmatch(text) is not None
    if _ASCII_FLOAT.fullmatch(text) is None and not explicit_nonfinite:
        raise ValueError("expected an ASCII floating-point value")
    parsed = float(text.replace("_", ""))
    if not explicit_nonfinite and not math.isfinite(parsed):
        raise ValueError("value is outside the IEEE floating-point range")
    if not single:
        return parsed
    try:
        return struct.unpack("<f", struct.pack("<f", parsed))[0]
    except OverflowError as error:
        raise ValueError("value is outside the IEEE f32 range") from error


def _require_integer_width(value: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("integer field value must be an int")
    if not minimum <= value <= maximum:
        raise ValueError(f"integer is outside [{minimum}, {maximum}]")
    return value


def require_color_wire_value(value: int | None) -> int:
    """Return a managed Param UInt32 color value or raise ``ValueError``."""
    return _require_integer_width(value or 0, _U32_MIN, _U32_MAX)


def require_coordinate_wire_parts(
    whole: int, fraction: int, field: str
) -> tuple[int, int]:
    """Validate one Param coordinate's signed 16/32-bit wire parts."""
    try:
        checked_whole = _require_integer_width(whole, _I16_MIN, _I16_MAX)
    except (TypeError, ValueError) as error:
        raise type(error)(f"{field}: {error}") from error
    try:
        checked_fraction = _require_integer_width(fraction, _I32_MIN, _I32_MAX)
    except (TypeError, ValueError) as error:
        raise type(error)(f"{field}_Frac: {error}") from error
    return checked_whole, checked_fraction


def real_num_equal(r1: float, r2: float) -> bool:
    """
    Check if two doubles are equal within tolerance.

    Implementation note: GeomUtils.RealNumEqual()

    Args:
        r1: First double value
        r2: Second double value

    Returns:
        True if values are within DOUBLE_TOLERANCE of each other
    """
    return abs(r1 - r2) < DOUBLE_TOLERANCE


def format_param_n3(value: float) -> str:
    """Format a V5 Param real with the managed invariant ``N3`` contract."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return f"{value:,.3f}"


def _format_param_float(value: float) -> str:
    """Format a managed Single so its own Param reader accepts the output."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return _MANAGED_FLOAT_INFINITY if value > 0 else f"-{_MANAGED_FLOAT_INFINITY}"
    return str(value)


def _format_param_double(value: float) -> str:
    """Format a managed Double so its own Param reader accepts the output."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    return str(value)


def read_coord_binary(data: bytes, offset: int = 0) -> tuple[int, int]:
    """Read a signed 16-bit whole-mil coordinate into internal units."""
    whole_mils, new_offset = read_short_binary(data, offset)
    return whole_mils * 100_000, new_offset


def write_coord_binary(value: int) -> bytes:
    """Write an internal-unit coordinate as signed 16-bit whole mils."""
    whole_mils = abs(value) // 100_000
    if value < 0:
        whole_mils = -whole_mils
    return write_short_binary(whole_mils)


# =============================================================================
# String Handling Utilities (native Feature Parity)
# =============================================================================


def sanitize_stream_name(name: str) -> str:
    """
    Sanitize a string for use as OLE stream name.

    Replaces invalid characters with '_' and truncates to 31 chars.
    Matches the stream-name sanitization used by Altium records.

    Implementation note: serializer parameter implementation:294-308

    Args:
        name: Raw stream name

    Returns:
        Sanitized stream name safe for OLE
    """
    result = []
    for char in name:
        if char in INVALID_STREAM_CHARS:
            result.append("_")
        else:
            result.append(char)
    sanitized = "".join(result)
    if len(sanitized) > MAX_STREAM_NAME_LEN:
        # native uses Substring(0, 30), so truncate to 30 chars
        return sanitized[: MAX_STREAM_NAME_LEN - 1]
    return sanitized


def escape_pipe(value: str) -> str:
    """
    Escape pipe characters for Altium record values.

    Pipe (|) is the field separator, so it must be escaped as broken bar.
    Also handles the MBCS escape character by doubling it.
    Matches native GetSafeParamValue().

    Implementation note: native implementation:1905-1930

    Args:
        value: Raw string value

    Returns:
        String with pipes escaped as broken bars
    """
    result = []
    for char in value:
        if char == "|":
            result.append(BROKEN_BAR_CHAR)
        elif char == MBCS_ESCAPE_CHAR:
            result.append(MBCS_ESCAPE_CHAR)
            result.append(MBCS_ESCAPE_CHAR)  # Double escape
        else:
            result.append(char)
    return "".join(result)


def unescape_pipe(value: str) -> str:
    """
    Reverse pipe escaping (broken bar to pipe).

    Args:
        value: Escaped string value

    Returns:
        String with broken bars converted to pipes
    """
    return value.replace(BROKEN_BAR_CHAR, "|")


def process_mbcs_string(value: str) -> str:
    """
    Process multi-byte character set encoding.

    Handles special character sequences used by Altium:
    - Double escape (MBCS_ESCAPE_CHAR twice) -> preserve one
    - Single escape (MBCS_ESCAPE_CHAR once) -> pipe
    - Broken bar -> pipe

    Matches native StrUtils.ProcessMBCSString().

    Implementation note: StrUtils.ProcessMBCSString

    Args:
        value: String with MBCS encoding

    Returns:
        Decoded string with special characters processed
    """
    if not any(c in value for c in (MBCS_ESCAPE_CHAR, BROKEN_BAR_CHAR)):
        return value  # Fast path: no special chars

    result = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == MBCS_ESCAPE_CHAR:
            # Check for double escape
            if i + 1 < len(value) and value[i + 1] == MBCS_ESCAPE_CHAR:
                result.append(MBCS_ESCAPE_CHAR)  # Keep one escape char
                i += 2  # Skip both
            else:
                result.append("|")  # Single escape -> pipe
                i += 1
        elif char == BROKEN_BAR_CHAR:
            result.append("|")  # Broken bar -> pipe
            i += 1
        else:
            result.append(char)
            i += 1
    return "".join(result)


def parse_utf8_field_name(name: str) -> tuple[str, bool]:
    """
    Parse field name for UTF-8 prefix.

    Returns (clean_name, is_utf8).
    If name contains %UTF8%, returns the name without prefix and True.

    Implementation note: StrUtils.ParseWideUtfData

    Args:
        name: Field name possibly containing %UTF8% prefix

    Returns:
        (clean_name, is_utf8) tuple
    """
    UTF8_PREFIX = "%UTF8%"
    if UTF8_PREFIX in name:
        return name.replace(UTF8_PREFIX, "").strip(), True
    return name, False


def read_dynamic_string_field(
    serializer: "AltiumSerializer",
    record: dict[str, Any],
    record_view: Mapping[str, Any],
    field: FieldDef | str,
    *,
    default: str = "",
) -> tuple[str, bool, bool]:
    """
    Read a dynamic-string field, preferring the `%UTF8%` sidecar when present.

    Returns `(value, was_present, used_utf8_sidecar)`.
    """
    field_def = serializer._get_field_def(field)
    utf8_name = f"%UTF8%{field_def.pascal}"
    utf8_value = record_view.get(utf8_name)
    if utf8_value is None:
        utf8_value = record_view.get(utf8_name.upper())
    if utf8_value is not None:
        return process_mbcs_string(str(utf8_value)), True, True

    value, present = serializer.read_str(record, field_def, default=default)
    return process_mbcs_string(value), present, False


def write_dynamic_string_field(
    serializer: "AltiumSerializer",
    record: dict[str, object],
    field: FieldDef | str,
    value: str,
    *,
    raw_record: dict[str, object] | None,
    used_utf8_sidecar: bool,
    was_present: bool,
    force: bool = False,
) -> None:
    """
    Write a dynamic-string field using the Altium `%UTF8%` sidecar contract.

    When `used_utf8_sidecar` is true, preserve the legacy ANSI fallback field and
    update the actual case-insensitive sidecar key found in the source record.
    """
    field_def = serializer._get_field_def(field)
    utf8_pascal = f"%UTF8%{field_def.pascal}"
    matching_field_keys = _matching_case_insensitive_keys(record, field_def.pascal)
    matching_utf8_keys = _matching_case_insensitive_keys(record, utf8_pascal)

    if not value:
        _remove_keys(record, (*matching_field_keys, *matching_utf8_keys))
        return

    if used_utf8_sidecar:
        _write_existing_dynamic_sidecar(record, matching_utf8_keys, utf8_pascal, value)
        return

    if was_present or value:
        serializer.write_str(record, field_def, value, raw_record, force=force)
    else:
        serializer.remove_field(record, field_def)
    for key in matching_utf8_keys:
        record.pop(key)


def _matching_case_insensitive_keys(
    record: Mapping[str, object], field: str
) -> tuple[str, ...]:
    normalized = field.casefold()
    return tuple(key for key in record if key.casefold() == normalized)


def _remove_keys(record: dict[str, object], keys: tuple[str, ...]) -> None:
    for key in keys:
        record.pop(key)


def _remove_dynamic_string_field(record: dict[str, object], field: str) -> None:
    ordinary = field.casefold()
    utf8 = f"%utf8%{ordinary}"
    for key in tuple(record):
        if key.casefold() in (ordinary, utf8):
            record.pop(key)


def _write_existing_dynamic_sidecar(
    record: dict[str, object],
    matching_keys: tuple[str, ...],
    default_key: str,
    value: str,
) -> None:
    output_key = matching_keys[0] if matching_keys else default_key
    for key in matching_keys:
        if key != output_key:
            record.pop(key)
    record[output_key] = value


# =============================================================================
# Binary Mode Support (native Feature Parity)
# =============================================================================


class SerializerMode(Enum):
    """
    Serializer mode for Altium records.

    ASCII: String-based key=value pairs (mode=0 in native)
    BINARY: Raw binary data (mode=1 in native)

    Implementation note: serializer parameter implementation:968-976
    """

    ASCII = 0
    BINARY = 1


# Binary read functions - standalone helpers for use outside AltiumSerializer


def read_int_binary(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read 4-byte little-endian signed int from binary data.

    Binary-mode integer reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<i", data, offset)[0]
    return value, offset + 4


def read_uint_binary(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read 4-byte little-endian unsigned int from binary data.
    Used for colors (Win32 BGR format).

    Binary-mode unsigned integer reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<I", data, offset)[0]
    return value, offset + 4


def read_short_binary(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read 2-byte little-endian short from binary data.

    Binary-mode short reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<h", data, offset)[0]
    return value, offset + 2


def read_byte_binary(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read 1-byte unsigned from binary data.

    Binary-mode byte reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = data[offset]
    return value, offset + 1


def read_bool_binary(data: bytes, offset: int) -> tuple[bool, int]:
    """
    Read 1-byte bool from binary data.

    Binary-mode boolean reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = data[offset] != 0
    return value, offset + 1


def read_float_binary(data: bytes, offset: int) -> tuple[float, int]:
    """
    Read 4-byte IEEE 754 float from binary data.

    Binary-mode float reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<f", data, offset)[0]
    return value, offset + 4


def read_double_binary(data: bytes, offset: int) -> tuple[float, int]:
    """
    Read 8-byte IEEE 754 double from binary data.

    Binary-mode double reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<d", data, offset)[0]
    return value, offset + 8


def read_long_binary(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read 8-byte little-endian signed long from binary data.

    Binary-mode long reader.

    Args:
        data: Binary data buffer
        offset: Starting offset

    Returns:
        (value, new_offset)
    """
    value = struct.unpack_from("<q", data, offset)[0]
    return value, offset + 8


# Binary write functions - standalone helpers


def write_int_binary(value: int) -> bytes:
    """
    Write 4-byte little-endian signed int.
    """
    return struct.pack("<i", value)


def write_uint_binary(value: int) -> bytes:
    """
    Write 4-byte little-endian unsigned int.
    """
    return struct.pack("<I", value)


def write_short_binary(value: int) -> bytes:
    """
    Write 2-byte little-endian short.
    """
    return struct.pack("<h", value)


def write_byte_binary(value: int) -> bytes:
    """
    Write 1-byte unsigned.
    """
    return bytes([value & 0xFF])


def write_bool_binary(value: bool) -> bytes:
    """
    Write 1-byte bool (0x01 for True, 0x00 for False).
    """
    return bytes([0x01 if value else 0x00])


def write_float_binary(value: float) -> bytes:
    """
    Write 4-byte IEEE 754 float.
    """
    return struct.pack("<f", value)


def write_double_binary(value: float) -> bytes:
    """
    Write 8-byte IEEE 754 double.
    """
    return struct.pack("<d", value)


def write_long_binary(value: int) -> bytes:
    """
    Write 8-byte little-endian signed long.
    """
    return struct.pack("<q", value)


# =============================================================================
# Binary Blob Support (Zlib compression)
# =============================================================================


def read_binary_blob_ascii(record: dict, field_name: str) -> bytes | None:
    """
    Read Zlib-compressed binary blob from ASCII record.

    ASCII mode format:
    - {field}_Len: compressed length (int)
    - {field}: hex-encoded compressed data

    Implementation note: serializer parameter implementation:927-965 WriteBinary

    Args:
        record: Source record dict
        field_name: Base field name (without _Len suffix)

    Returns:
        Decompressed bytes or None if not present
    """
    len_key = f"{field_name}_Len"
    if len_key not in record:
        return None

    hex_data = record.get(field_name, "")
    if not hex_data:
        return None

    try:
        compressed = bytes.fromhex(hex_data)
        decompressed = zlib.decompress(compressed)
        return decompressed
    except (ValueError, zlib.error) as e:
        log.warning(f"Failed to decompress binary blob {field_name}: {e}")
        return None


def write_binary_blob_ascii(record: dict, field_name: str, data: bytes) -> None:
    """
    Write Zlib-compressed binary blob to ASCII record.

    Writes:
    - {field}_Len: compressed length
    - {field}: hex-encoded compressed data (UPPERCASE)

    Implementation note: serializer parameter implementation:927-965 WriteBinary

    Args:
        record: Target record dict
        field_name: Base field name (without _Len suffix)
        data: Raw bytes to compress and write
    """
    compressed = zlib.compress(data)
    hex_encoded = compressed.hex().upper()  # native uses uppercase hex

    record[f"{field_name}_Len"] = str(len(compressed))
    record[field_name] = hex_encoded


# =============================================================================
# Case Mode
# =============================================================================


class CaseMode(Enum):
    """
    Field name case mode for Altium records.

    UPPERCASE: Used by SchLib files (LOCATION.X, FONTID, OWNERINDEX)
    PASCALCASE: Used by SchDoc files (Location.X, FontID, OwnerIndex)

    The serializer uses this to determine output field name casing.
    Reading is always case-insensitive (Altium behavior).
    """

    UPPERCASE = auto()
    PASCALCASE = auto()


# =============================================================================
# Field Definitions
# =============================================================================


@dataclass(frozen=True)
class FieldDef:
    """
    Field definition with case variants.

    Defines the canonical name and both case variants for a field.
    Used by the serializer to read/write with correct casing.

    Attributes:
        canonical: Canonical field name (used for API)
        pascal: PascalCase variant (SchDoc)
        upper: UPPERCASE variant (SchLib)
    """

    canonical: str
    pascal: str
    upper: str

    @classmethod
    def simple(cls, pascal: str) -> FieldDef:
        """
        Create field def from PascalCase name.
        """
        return cls(canonical=pascal, pascal=pascal, upper=pascal.upper())

    @classmethod
    def dotted(cls, base: str, suffix: str) -> FieldDef:
        """
        Create field def for dotted names like Location.X
        """
        pascal = f"{base}.{suffix}"
        upper = f"{base.upper()}.{suffix.upper()}"
        return cls(canonical=pascal, pascal=pascal, upper=upper)

    def get_name(self, mode: CaseMode) -> str:
        """
        Get field name for the given case mode.
        """
        return self.pascal if mode == CaseMode.PASCALCASE else self.upper

    def find_in_record(self, record: Mapping[str, object]) -> tuple[str, bool]:
        """
        Find this field in a record (case-insensitive).

        Returns:
            (found_key, exists) - the key that was found and whether it exists
        """
        for key in [self.pascal, self.upper, self.canonical]:
            if key in record:
                return key, True
        # Check case-insensitive
        lower = self.canonical.lower()
        for key in record:
            if key.lower() == lower:
                return key, True
        return self.pascal, False


# =============================================================================
# Common Field Definitions
# =============================================================================

# These are the most commonly used fields across record types.
# Individual record classes can define additional fields as needed.


class Fields:
    """
    Common field definitions used across record types.
    """

    # Location fields
    LOCATION_X = FieldDef.dotted("Location", "X")
    LOCATION_Y = FieldDef.dotted("Location", "Y")
    LOCATION_X_FRAC = FieldDef.dotted("Location", "X_Frac")
    LOCATION_Y_FRAC = FieldDef.dotted("Location", "Y_Frac")

    # Corner fields (rectangles, etc.)
    CORNER_X = FieldDef.dotted("Corner", "X")
    CORNER_Y = FieldDef.dotted("Corner", "Y")
    CORNER_X_FRAC = FieldDef.dotted("Corner", "X_Frac")
    CORNER_Y_FRAC = FieldDef.dotted("Corner", "Y_Frac")

    # Owner/hierarchy fields
    OWNER_INDEX = FieldDef.simple("OwnerIndex")
    OWNER_PART_ID = FieldDef.simple("OwnerPartId")
    OWNER_PART_DISPLAY_MODE = FieldDef.simple("OwnerPartDisplayMode")
    INDEX_IN_SHEET = FieldDef.simple("IndexInSheet")
    UNIQUE_ID = FieldDef.simple("UniqueId")

    # Graphics fields
    COLOR = FieldDef.simple("Color")
    AREA_COLOR = FieldDef.simple("AreaColor")
    LINE_WIDTH = FieldDef.simple("LineWidth")
    LINE_STYLE = FieldDef.simple("LineStyle")
    LINE_STYLE_EXT = FieldDef.simple("LineStyleExt")
    IS_SOLID = FieldDef.simple("IsSolid")
    TRANSPARENT = FieldDef.simple("Transparent")

    # Text fields
    TEXT = FieldDef.simple("Text")
    FONT_ID = FieldDef.simple("FontID")
    ORIENTATION = FieldDef.simple("Orientation")
    JUSTIFICATION = FieldDef.simple("Justification")
    IS_MIRRORED = FieldDef.simple("IsMirrored")
    IS_HIDDEN = FieldDef.simple("IsHidden")
    URL = FieldDef.simple("URL")

    # Arc/ellipse fields
    RADIUS = FieldDef.simple("Radius")
    RADIUS_FRAC = FieldDef.simple("Radius_Frac")
    SECONDARY_RADIUS = FieldDef.simple("SecondaryRadius")
    SECONDARY_RADIUS_FRAC = FieldDef.simple("SecondaryRadius_Frac")
    START_ANGLE = FieldDef.simple("StartAngle")
    END_ANGLE = FieldDef.simple("EndAngle")

    # Rounded rectangle fields
    CORNER_X_RADIUS = FieldDef.simple("CornerXRadius")
    CORNER_Y_RADIUS = FieldDef.simple("CornerYRadius")

    # Polyline/polygon fields
    LOCATION_COUNT = FieldDef.simple("LocationCount")
    START_LINE_SHAPE = FieldDef.simple("StartLineShape")
    END_LINE_SHAPE = FieldDef.simple("EndLineShape")
    LINE_SHAPE_SIZE = FieldDef.simple("LineShapeSize")

    # Wire-specific fields
    UNDERLINE_COLOR = FieldDef.simple("UnderlineColor")
    ASSIGNED_INTERFACE = FieldDef.simple("AssignedInterface")
    ASSIGNED_INTERFACE_SIGNAL = FieldDef.simple("AssignedInterfaceSignal")

    # Power port fields
    STYLE = FieldDef.simple("Style")
    SHOW_NET_NAME = FieldDef.simple("ShowNetName")
    IS_CROSS_SHEET_CONNECTOR = FieldDef.simple("IsCrossSheetConnector")
    OVERRIDE_DISPLAY_STRING = FieldDef.simple("OverrideDisplayString")
    OBJECT_DEFINITION_ID = FieldDef.simple("ObjectDefinitionId")

    # Port fields
    NAME = FieldDef.simple("Name")
    IO_TYPE = FieldDef.simple("IOType")
    ALIGNMENT = FieldDef.simple("Alignment")
    WIDTH = FieldDef.simple("Width")
    HEIGHT = FieldDef.simple("Height")
    TEXT_COLOR = FieldDef.simple("TextColor")
    BORDER_WIDTH = FieldDef.simple("BorderWidth")
    CROSS_REFERENCE = FieldDef.simple("CrossReference")
    AUTO_SIZE = FieldDef.simple("AutoSize")
    CONNECTED_END = FieldDef.simple("ConnectedEnd")
    HARNESS_TYPE = FieldDef.simple("HarnessType")
    HARNESS_COLOR = FieldDef.simple("HarnessColor")
    PORT_NAME_IS_HIDDEN = FieldDef.simple("PortNameIsHidden")

    # NoERC fields
    SYMBOL = FieldDef.simple("Symbol")
    IS_ACTIVE = FieldDef.simple("IsActive")
    SUPPRESS_ALL = FieldDef.simple("SuppressAll")

    # Designator and Parameter fields
    READ_ONLY_STATE = FieldDef.simple("ReadOnlyState")
    IS_HIDDEN = FieldDef.simple("IsHidden")
    PARAM_TYPE = FieldDef.simple("ParamType")
    SHOW_NAME = FieldDef.simple("ShowName")
    NOT_ALLOW_LIBRARY_SYNCHRONIZE = FieldDef.simple("NotAllowLibrarySynchronize")
    NOT_ALLOW_DATABASE_SYNCHRONIZE = FieldDef.simple("NotAllowDatabaseSynchronize")
    OVERRIDE_NOT_AUTO_POSITION = FieldDef.simple("OverrideNotAutoPosition")
    TEXT_HORZ_ANCHOR = FieldDef.simple("TextHorzAnchor")
    TEXT_VERT_ANCHOR = FieldDef.simple("TextVertAnchor")
    IS_IMAGE_PARAMETER = FieldDef.simple("IsImageParameter")

    # Note fields
    AUTHOR = FieldDef.simple("Author")
    COLLAPSED = FieldDef.simple("Collapsed")

    # TextFrame fields
    WORD_WRAP = FieldDef.simple("WordWrap")
    CLIP_TO_RECT = FieldDef.simple("ClipToRect")
    SHOW_BORDER = FieldDef.simple("ShowBorder")
    TEXT_MARGIN = FieldDef.simple("TextMargin")
    TEXT_MARGIN_FRAC = FieldDef.simple("TextMargin_Frac")

    # Image fields
    EMBED_IMAGE = FieldDef.simple("EmbedImage")
    FILENAME = FieldDef.simple("FileName")
    KEEP_ASPECT = FieldDef.simple("KeepAspect")

    # IEEE Symbol fields
    SCALE_FACTOR = FieldDef.simple("ScaleFactor")

    # Harness connector fields
    X_SIZE = FieldDef.simple("XSize")
    Y_SIZE = FieldDef.simple("YSize")
    HARNESS_CONNECTOR_SIDE = FieldDef.simple("HarnessConnectorSide")
    PRIMARY_CONNECTION_POSITION = FieldDef.simple("PrimaryConnectionPosition")

    # Harness entry fields
    TEXT_FONT_ID = FieldDef.simple("TextFontID")
    TEXT_STYLE = FieldDef.simple("TextStyle")
    SIDE = FieldDef.simple("Side")
    DISTANCE_FROM_TOP = FieldDef.simple("DistanceFromTop")
    DISTANCE_FROM_TOP_FRAC = FieldDef.simple("DistanceFromTop_Frac")
    DISTANCE_FROM_TOP_FRAC1 = FieldDef.simple("DistanceFromTop_Frac1")
    OWNER_INDEX_ADDITIONAL_LIST = FieldDef.simple("OwnerIndexAdditionalList")
    NOT_AUTO_POSITION = FieldDef.simple("NotAutoPosition")

    # Implementation fields
    MODEL_NAME = FieldDef.simple("ModelName")
    MODEL_TYPE = FieldDef.simple("ModelType")
    DESCRIPTION = FieldDef.simple("Description")
    IS_CURRENT = FieldDef.simple("IsCurrent")

    # Arrow/style fields
    ARROW_KIND = FieldDef.simple("ArrowKind")

    # Sheet symbol fields
    SYMBOL_TYPE = FieldDef.simple("SymbolType")

    # Component fields
    LIB_REFERENCE = FieldDef.simple("LibReference")
    LIBRARY_PATH = FieldDef.simple("LibraryPath")
    SOURCE_LIBRARY_NAME = FieldDef.simple("SourceLibraryName")
    COMPONENT_DESCRIPTION = FieldDef.simple("ComponentDescription")
    PART_COUNT = FieldDef.simple("PartCount")
    CURRENT_PART_ID = FieldDef.simple("CurrentPartId")
    DISPLAY_MODE = FieldDef.simple("DisplayMode")
    DISPLAY_MODE_COUNT = FieldDef.simple("DisplayModeCount")
    SHOW_HIDDEN_PINS = FieldDef.simple("ShowHiddenPins")
    SHOW_HIDDEN_FIELDS = FieldDef.simple("ShowHiddenFields")
    DISPLAY_FIELD_NAMES = FieldDef.simple("DisplayFieldNames")
    DESIGNATOR_LOCKED = FieldDef.simple("DesignatorLocked")
    PART_ID_LOCKED = FieldDef.simple("PartIDLocked")
    PINS_MOVEABLE = FieldDef.simple("PinsMoveable")
    OVERRIDE_COLORS = FieldDef.simple("OverideColors")
    PIN_COLOR = FieldDef.simple("PinColor")
    COMPONENT_KIND = FieldDef.simple("ComponentKind")
    DATABASE_TABLE_NAME = FieldDef.simple("DatabaseTableName")
    USE_DB_TABLE_NAME = FieldDef.simple("UseDBTableName")
    USE_LIBRARY_NAME = FieldDef.simple("UseLibraryName")
    DESIGN_ITEM_ID = FieldDef.simple("DesignItemId")
    SHEET_PART_FILENAME = FieldDef.simple("SheetPartFileName")
    TARGET_FILENAME = FieldDef.simple("TargetFileName")
    VAULT_GUID = FieldDef.simple("VaultGUID")
    ITEM_GUID = FieldDef.simple("ItemGUID")
    REVISION_GUID = FieldDef.simple("RevisionGUID")
    REVISION_NAME = FieldDef.simple("RevisionName")
    SYMBOL_VAULT_GUID = FieldDef.simple("SymbolVaultGUID")
    SYMBOL_ITEM_GUID = FieldDef.simple("SymbolItemGUID")
    SYMBOL_REVISION_GUID = FieldDef.simple("SymbolRevisionGUID")
    ALL_PIN_COUNT = FieldDef.simple("AllPinCount")
    FOOTPRINT = FieldDef.simple("Footprint")

    # Header/sheet settings fields
    SHEET_SIZE = FieldDef.simple("SheetSize")
    SHEET_ORIENTATION = FieldDef.simple("SheetOrientation")
    GRID_SIZE = FieldDef.simple("GridSize")
    SNAP_GRID_SIZE = FieldDef.simple("SnapGridSize")
    VISIBLE_GRID_SIZE = FieldDef.simple("VisibleGridSize")
    SHOW_GRID = FieldDef.simple("ShowGrid")
    SNAP_TO_GRID = FieldDef.simple("SnapToGrid")
    USE_CUSTOM_SHEET = FieldDef.simple("UseCustomSheet")
    CUSTOM_X = FieldDef.simple("CustomX")
    CUSTOM_Y = FieldDef.simple("CustomY")
    CUSTOM_X_ZONES = FieldDef.simple("CustomXZones")
    CUSTOM_Y_ZONES = FieldDef.simple("CustomYZones")
    CUSTOM_MARGIN_WIDTH = FieldDef.simple("CustomMarginWidth")
    TITLE_BLOCK_ON = FieldDef.simple("TitleBlockOn")
    DOCUMENT_NAME = FieldDef.simple("DocumentName")

    # Record type
    RECORD = FieldDef.simple("RECORD")


# =============================================================================
# Serializer
# =============================================================================


def _read_param_boolean(record: Mapping[str, object], field: FieldDef | str) -> bool:
    """Read exact text Param booleans, retaining typed programmatic inputs."""
    definition = FieldDef.simple(field) if isinstance(field, str) else field
    key, present = definition.find_in_record(record)
    value = record.get(key) if present else None
    if isinstance(value, (bool, int)):
        return bool(value)
    return isinstance(value, str) and value == "T"


class AltiumSerializer:
    """
    Central serializer for Altium record fields.

    Handles:
    - Case mode (UPPERCASE vs PascalCase)
    - Type conversion (int, bool, str, color)
    - Field presence tracking
    - Font ID translation (via FontIDManager)

    The serializer is instantiated with a case mode that determines
    the output field naming. Reading is always case-insensitive.

    Attributes:
        mode: Case mode for output field names
    """

    def __init__(self, mode: CaseMode = CaseMode.PASCALCASE) -> None:
        """
        Initialize serializer with case mode.

        Args:
            mode: Case mode for output field names
        """
        self.mode = mode

    # =========================================================================
    # Read Methods
    # =========================================================================

    def read_int(
        self, record: dict, field: FieldDef | str, default: int = 0
    ) -> tuple[int, bool]:
        """
        Read integer field from record.

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record
        """
        field_def = self._get_field_def(field)
        try:
            return self._read_int_checked(record, field_def, default)
        except (ValueError, TypeError):
            key, _ = field_def.find_in_record(record)
            log.warning(f"Invalid int value for {field_def.canonical}: {record[key]}")
            return default, True

    def _read_int_checked(
        self, record: dict[str, object], field: FieldDef | str, default: int = 0
    ) -> tuple[int, bool]:
        """Read a signed i32 and raise for malformed or out-of-range input."""
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)
        if not exists:
            return default, False
        try:
            return _parse_ascii_integer(record[key], _I32_MIN, _I32_MAX), True
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field_def.canonical}: {error}") from error

    def read_bool(
        self, record: dict, field: FieldDef | str, default: bool = False
    ) -> tuple[bool, bool]:
        """
        Read boolean field from record.

        Altium uses 'T'/'F' strings, or '1'/'0', or actual booleans.

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            value = record[key]
            if isinstance(value, bool):
                return value, True
            if isinstance(value, str):
                return value.upper() in ("T", "TRUE", "1"), True
            if isinstance(value, int):
                return value != 0, True
            return default, True
        return default, False

    def read_str(
        self, record: dict, field: FieldDef | str, default: str = ""
    ) -> tuple[str, bool]:
        """
        Read string field from record.

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            return str(record[key]), True
        return default, False

    def read_color(
        self, record: dict, field: FieldDef | str, default: int | None = None
    ) -> tuple[int | None, bool]:
        """
        Read Win32 color field from record.

        Colors are stored as integers in BGR format (0x00BBGGRR).

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the color int and whether field was in record
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            try:
                return _parse_ascii_integer(record[key], _U32_MIN, _U32_MAX), True
            except (ValueError, TypeError):
                return default, True
        return default, False

    def read_coord(
        self, record: dict, base: str, prefix: str = ""
    ) -> tuple[int, int, bool]:
        """
        Read coordinate with fractional part.

        Reads both the integer and fractional parts of a coordinate.
        Args:
            record: Source record dict
            base: Base field name (e.g., 'Location', 'Corner')
            prefix: Coordinate prefix (e.g., 'X', 'Y', or '' for radius)

        Returns:
            (value, frac, was_present) - integer part, fractional part, and presence
        """
        if prefix:
            field_def = FieldDef.dotted(base, prefix)
            frac_def = FieldDef.dotted(base, f"{prefix}_Frac")
        else:
            field_def = FieldDef.simple(base)
            frac_def = FieldDef.simple(f"{base}_Frac")

        value, present = self.read_int(record, field_def, default=0)
        if not _I16_MIN <= value <= _I16_MAX:
            log.warning(
                "Invalid coord whole value for %s: %s",
                field_def.canonical,
                value,
            )
            value = 0
        frac, _ = self.read_int(record, frac_def, default=0)

        return value, frac, present

    def coordinate_has_invalid_wire_value(
        self, record: dict[str, object], base: str, prefix: str = ""
    ) -> bool:
        """Return whether a present coordinate part violates its Param width."""
        if prefix:
            whole = FieldDef.dotted(base, prefix)
            fraction = FieldDef.dotted(base, f"{prefix}_Frac")
        else:
            whole = FieldDef.simple(base)
            fraction = FieldDef.simple(f"{base}_Frac")
        return self._field_violates_width(record, whole, _I16_MIN, _I16_MAX) or (
            self._field_violates_width(record, fraction, _I32_MIN, _I32_MAX)
        )

    @staticmethod
    def _field_violates_width(
        record: dict[str, object], field: FieldDef, minimum: int, maximum: int
    ) -> bool:
        key, present = field.find_in_record(record)
        if not present:
            return False
        try:
            _parse_ascii_integer(record[key], minimum, maximum)
        except (TypeError, ValueError):
            return True
        return False

    def write_coord(
        self,
        record: dict,
        base: str,
        prefix: str,
        value: int,
        frac: int = 0,
        raw_record: dict | None = None,
        skip_if_zero: bool = False,
        force: bool = False,
    ) -> None:
        """
        Write coordinate with fractional part.

        Args:
            record: Target record dict
            base: Base field name (e.g., 'Location', 'Corner')
            prefix: Coordinate prefix (e.g., 'X', 'Y')
            value: Integer part
            frac: Fractional part (default 0)
            raw_record: Original raw record for round-trip
            skip_if_zero: If True, skip writing if value and frac are both 0
            force: If True, add whole and fractional fields missing from raw_record
        """
        if skip_if_zero and value == 0 and frac == 0:
            return

        if prefix:
            field_def = FieldDef.dotted(base, prefix)
            frac_def = FieldDef.dotted(base, f"{prefix}_Frac")
        else:
            field_def = FieldDef.simple(base)
            frac_def = FieldDef.simple(f"{base}_Frac")
        value, frac = require_coordinate_wire_parts(value, frac, field_def.canonical)
        if value != 0:
            self.write_int(record, field_def, value, raw_record, force=force)

        if frac != 0:
            self.write_int(record, frac_def, frac, raw_record, force=force)

    def read_font_id(
        self,
        record: dict,
        field: FieldDef | str,
        font_manager: FontIDManager | None = None,
        default: int = 1,
    ) -> tuple[int, bool]:
        """
        Read font ID field with translation.

        If font_manager is provided, translates file font ID to internal ID.
        Otherwise returns raw font ID.

        Args:
            record: Source record dict
            field: Field definition or canonical name
            font_manager: Optional FontIDManager for translation
            default: Default value if field missing

        Returns:
            (internal_font_id, was_present)
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)
        if not exists:
            return default, False
        try:
            raw_id = _parse_ascii_integer(record[key], _I16_MIN, _I16_MAX)
        except (TypeError, ValueError):
            return default, True

        if font_manager:
            # Apply in_translator if available
            internal_id = font_manager.translate_in(raw_id)
            return internal_id, True

        return raw_id, exists

    # =========================================================================
    # Write Methods
    # =========================================================================

    def write_int(
        self,
        record: dict,
        field: FieldDef | str,
        value: int,
        raw_record: dict | None = None,
        skip_if_default: bool = False,
        default: int = 0,
        force: bool = False,
    ) -> None:
        """
        Write integer field to record.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip (preserves field names)
            skip_if_default: If True, skip writing if value equals default
            default: Default value for skip_if_default check
            force: If True, add field even when missing from raw_record
        """
        value = _require_integer_width(value, _I32_MIN, _I32_MAX)
        if skip_if_default and value == default:
            return

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, str(value), raw_record, force=force)

    def write_bool(
        self,
        record: dict,
        field: FieldDef | str,
        value: bool,
        raw_record: dict | None = None,
        skip_if_false: bool = False,
        force: bool = False,
    ) -> None:
        """
        Write boolean field to record.

        Uses Altium's 'T'/'F' format.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip
            skip_if_false: If True, skip writing if value is False
            force: If True, add field even when missing from raw_record
        """
        if skip_if_false and not value:
            return

        field_def = self._get_field_def(field)
        self._write_field(
            record, field_def, "T" if value else "F", raw_record, force=force
        )

    def write_str(
        self,
        record: dict,
        field: FieldDef | str,
        value: str,
        raw_record: dict | None = None,
        skip_if_empty: bool = False,
        force: bool = False,
    ) -> None:
        """
        Write string field to record.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip
            skip_if_empty: If True, skip writing if value is empty
            force: If True, add field even when missing from raw_record
        """
        if skip_if_empty and not value:
            return

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, value, raw_record, force=force)

    def write_color(
        self,
        record: dict,
        field: FieldDef | str,
        value: int | None,
        raw_record: dict | None = None,
        skip_if_none: bool = False,
        force: bool = False,
    ) -> None:
        """
        Write Win32 color field to record.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Color value (BGR int) or None
            raw_record: Original raw record for round-trip
            skip_if_none: If True, skip writing if value is None
            force: If True, add field even when missing from raw_record
        """
        if skip_if_none and value is None:
            return
        value = require_color_wire_value(value)

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, str(value), raw_record, force=force)

    def write_font_id(
        self,
        record: dict,
        field: FieldDef | str,
        internal_id: int,
        font_manager: FontIDManager | None = None,
        raw_record: dict | None = None,
    ) -> None:
        """
        Write font ID field with translation.

        If font_manager is provided, translates internal ID to output file ID.
        Otherwise writes raw internal ID.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            internal_id: Internal font ID
            font_manager: Optional FontIDManager for translation
            raw_record: Original raw record for round-trip
        """
        if font_manager:
            # Apply out_translator if available
            file_id = font_manager.translate_out(internal_id)
        else:
            file_id = internal_id
        file_id = _require_integer_width(file_id, _I16_MIN, _I16_MAX)

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, str(file_id), raw_record)

    # =========================================================================
    # Numeric Type Methods (native Feature Parity)
    # =========================================================================

    def read_float(
        self, record: dict, field: FieldDef | str, default: float = 0.0
    ) -> tuple[float, bool]:
        """
        Read float field from record (ASCII string format).

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record

        Implementation note: serializer parameter implementation ReadFloat
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            try:
                return _parse_ascii_float(record[key], single=True), True
            except (ValueError, TypeError):
                log.warning(
                    f"Invalid float value for {field_def.canonical}: {record[key]}"
                )
                return default, True
        return default, False

    def read_double(
        self, record: dict, field: FieldDef | str, default: float = 0.0
    ) -> tuple[float, bool]:
        """
        Read double field from record (ASCII string format).

        Supports both regular decimal notation and scientific notation (1.5E-3).

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record

        The binary-field reader accepts only restricted decimal syntax, while
        the ASCII-field reader accepts exponent notation. This public helper
        deliberately follows the broader ASCII syntax.
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            try:
                return _parse_ascii_float(record[key], single=False), True
            except (ValueError, TypeError):
                log.warning(
                    f"Invalid double value for {field_def.canonical}: {record[key]}"
                )
                return default, True
        return default, False

    def read_long(
        self, record: dict, field: FieldDef | str, default: int = 0
    ) -> tuple[int, bool]:
        """
        Read 64-bit integer field from record.

        Args:
            record: Source record dict
            field: Field definition or canonical name
            default: Default value if field missing

        Returns:
            (value, was_present) - the value and whether field was in record

        Implementation note: serializer parameter implementation ReadLong
        """
        field_def = self._get_field_def(field)
        key, exists = field_def.find_in_record(record)

        if exists:
            try:
                return _parse_ascii_integer(record[key], _I64_MIN, _I64_MAX), True
            except (ValueError, TypeError):
                log.warning(
                    f"Invalid long value for {field_def.canonical}: {record[key]}"
                )
                return default, True
        return default, False

    def write_float(
        self,
        record: dict,
        field: FieldDef | str,
        value: float,
        raw_record: dict | None = None,
        skip_if_zero: bool = False,
    ) -> None:
        """
        Write float field to record (ASCII string format).

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip
            skip_if_zero: If True, skip writing if value is zero

        Implementation note: serializer parameter implementation WriteFloat
        """
        value = _parse_ascii_float(value, single=True)
        if skip_if_zero and value == 0.0:
            return

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, _format_param_float(value), raw_record)

    def write_double(
        self,
        record: dict,
        field: FieldDef | str,
        value: float,
        raw_record: dict | None = None,
        skip_if_zero: bool = False,
        format_n3: bool = True,
    ) -> None:
        """
        Write double field to record (ASCII string format).

        Uses "N3" format (3 decimal places) by default for compatibility.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip
            skip_if_zero: If True, skip writing if value is within DOUBLE_TOLERANCE of zero
            format_n3: If True, use N3 format (3 decimal places)

        Implementation note: serializer parameter implementation WriteDouble, DoubleToString uses "N3" format
        """
        value = _parse_ascii_float(value, single=False)
        if skip_if_zero and real_num_equal(0.0, value):
            return

        field_def = self._get_field_def(field)
        if format_n3:
            str_value = format_param_n3(value)
        else:
            str_value = _format_param_double(value)
        self._write_field(record, field_def, str_value, raw_record)

    def write_long(
        self,
        record: dict,
        field: FieldDef | str,
        value: int,
        raw_record: dict | None = None,
        skip_if_default: bool = False,
        default: int = 0,
    ) -> None:
        """
        Write 64-bit integer field to record.

        Args:
            record: Target record dict
            field: Field definition or canonical name
            value: Value to write
            raw_record: Original raw record for round-trip
            skip_if_default: If True, skip writing if value equals default
            default: Default value for skip_if_default check

        Implementation note: serializer parameter implementation WriteLong
        """
        value = _require_integer_width(value, _I64_MIN, _I64_MAX)
        if skip_if_default and value == default:
            return

        field_def = self._get_field_def(field)
        self._write_field(record, field_def, str(value), raw_record)

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _get_field_def(self, field: FieldDef | str) -> FieldDef:
        """
        Convert field arg to FieldDef.
        """
        if isinstance(field, FieldDef):
            return field
        return FieldDef.simple(field)

    def _write_field(
        self,
        record: dict,
        field_def: FieldDef,
        value: str,
        raw_record: dict | None,
        force: bool = False,
    ) -> None:
        """
        Write a field to record with correct casing.

        Round-trip logic:
        - If raw_record exists and contains field: update with preserved key
        - If raw_record exists but field NOT in it: skip (don't add new fields)
        - If raw_record is None (synthesis): always write with case mode

        Args:
            record: Target record dict
            field_def: Field definition
            value: Value to write
            raw_record: Original raw record for round-trip (None = synthesis mode)
            force: If True, write even if not in raw_record (for required fields)
        """
        # Check if field exists in raw_record (for round-trip)
        if raw_record is not None:
            existing_key, exists = field_def.find_in_record(raw_record)
            if exists:
                # Preserve original key from raw record
                record[existing_key] = value
                return
            elif not force:
                # Round-trip mode: don't add fields that weren't in original
                return

        # Synthesis mode or forced: use case mode for new field
        key = field_def.get_name(self.mode)
        record[key] = value

    def remove_field(self, record: dict, field: FieldDef | str) -> None:
        """
        Remove a field from record (all case variants).

        Args:
            record: Target record dict
            field: Field definition or canonical name
        """
        field_def = self._get_field_def(field)
        normalized = field_def.canonical.casefold()
        for key in tuple(record):
            if str(key).casefold() == normalized:
                record.pop(key)


# =============================================================================
# Serializer Factory
# =============================================================================


def get_serializer(mode: CaseMode = CaseMode.PASCALCASE) -> AltiumSerializer:
    """
    Get a serializer instance for the given case mode.

    Args:
        mode: Case mode for output field names

    Returns:
        AltiumSerializer instance
    """
    return AltiumSerializer(mode)


# Default serializers for common use cases
SCHLIB_SERIALIZER = AltiumSerializer(CaseMode.UPPERCASE)
SCHDOC_SERIALIZER = AltiumSerializer(CaseMode.PASCALCASE)
