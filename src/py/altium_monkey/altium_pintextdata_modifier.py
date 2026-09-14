"""
Parse and modify schematic PinTextData streams.
"""

import io
import math
import struct
from copy import deepcopy
from dataclasses import dataclass, field, replace

from .altium_sch_auxiliary_codec import (
    SchAuxiliaryReadLimits,
    _ManagedAuxiliaryReadResult,
    _decode_managed_auxiliary_stream,
    _error as _codec_error,
    _try_parse_managed_i32,
    encode_auxiliary_stream,
)
from .altium_sch_enums import PinTextOrientation


_KNOWN_RECORD_FLAGS = 0x1F
_I16_MIN = -(1 << 15)
_I16_MAX = (1 << 15) - 1
_I32_MIN = -(1 << 31)
_I32_MAX = (1 << 31) - 1
_U32_MAX = (1 << 32) - 1
_MAX_PIN_TEXT_PAYLOAD_BYTES = 22
_FORMAT_BY_LAYOUT: dict[tuple[bool, bool, bool, bool], str] = {
    (False, False, False, False): "POSITION_ONLY",
    (False, False, False, True): "DESIGNATOR_ONLY",
    (False, True, False, False): "NAME_ONLY",
    (False, True, False, True): "BOTH",
    (True, False, False, False): "NAME_POSITION",
    (True, False, False, True): "NAME_POSITION",
    (True, True, False, False): "NAME_POSITION",
    (True, True, False, True): "NAME_POSITION",
    (False, False, True, False): "DESIGNATOR_POSITION",
    (False, False, True, True): "DESIGNATOR_POSITION",
    (False, True, True, False): "DESIGNATOR_POSITION",
    (False, True, True, True): "NAME_DESIGNATOR_POSITION",
    (True, False, True, False): "POSITION_ONLY",
    (True, False, True, True): "BOTH_POSITION",
    (True, True, True, False): "BOTH_POSITION",
    (True, True, True, True): "BOTH_POSITION",
}


def _read_exact(stream: io.BytesIO, size: int, field: str) -> bytes:
    offset = stream.tell()
    value = stream.read(size)
    if len(value) != size:
        raise _codec_error(
            "truncated",
            offset,
            f"{field} requires {size} bytes but only {len(value)} remain",
        )
    return value


def _checked_integer(value: int, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be in [{minimum}, {maximum}]")
    return value


def _checked_i16(value: int, field: str) -> int:
    return _checked_integer(value, _I16_MIN, _I16_MAX, field)


def _checked_u32(value: int, field: str) -> int:
    return _checked_integer(value, 0, _U32_MAX, field)


def _margin_to_dxp(value: float, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field} must be a finite number")
    scaled = round(numeric * 10_000)
    if scaled < _I32_MIN or scaled > _I32_MAX:
        raise ValueError(f"{field} exceeds signed int32 DXP range")
    return scaled


def _decode_pintext_stream(
    data: bytes, limits: SchAuxiliaryReadLimits | None = None
) -> _ManagedAuxiliaryReadResult:
    active_limits = limits or SchAuxiliaryReadLimits()
    pin_limits = replace(
        active_limits,
        max_decompressed_blob_bytes=min(
            active_limits.max_decompressed_blob_bytes,
            _MAX_PIN_TEXT_PAYLOAD_BYTES,
        ),
    )
    return _decode_managed_auxiliary_stream(
        data,
        expected_header="PinTextData",
        limits=pin_limits,
    )


def _validate_pin_index_name(name: str, offset: int) -> None:
    if (
        not name
        or not name.isascii()
        or not name.isdigit()
        or (name != "0" and name.startswith("0"))
    ):
        raise _codec_error(
            "malformed",
            offset,
            f"PinTextData name {name!r} is not a canonical zero-based pin index",
        )
    if int(name) > _I32_MAX:
        raise _codec_error(
            "malformed",
            offset,
            f"PinTextData name {name!r} exceeds the signed 32-bit pin index width",
        )


@dataclass
class PinTextPosition:
    """
    Position data for custom PIN text positioning.
    """

    margin_mils: float
    orientation: PinTextOrientation
    reference_to_component: bool  # True = to Component, False = to Pin
    _raw_margin_dxp: int | None = field(default=None, repr=False, compare=False)
    _raw_margin_mils: float | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.orientation = _coerce_pin_text_orientation(self.orientation)
        _margin_to_dxp(self.margin_mils, "margin_mils")
        if not isinstance(self.reference_to_component, bool):
            raise ValueError("reference_to_component must be a boolean")

    def _to_dxp(self) -> int:
        if (
            self._raw_margin_dxp is not None
            and self._raw_margin_mils == self.margin_mils
        ):
            return self._raw_margin_dxp
        return _margin_to_dxp(self.margin_mils, "pin text margin")

    def encode_designator_format(self) -> bytes:
        """
        Encode position data for Designator format (bytes 1-4 of 12-byte format).

        Returns:
            4 bytes of position data
        """
        # Calculate margin value (round to preserve exact value during round-trips)
        margin_value = round(self.margin_mils * 39.0625)
        if margin_value < 0 or margin_value > 0xFFFF:
            raise ValueError("compact designator margin exceeds uint16 range")

        # Build position flags (byte 1)
        flags = 0x10 | 0x01  # Base + position enabled
        if self.reference_to_component:
            flags |= 0x02
        if self.orientation == PinTextOrientation.DEG_90:
            flags |= 0x04
        # TODO: Support 180 deg and 270 deg in this compact designator variant.

        # Pack: [flags][unknown][margin_int16_LE][unknown]
        data = bytearray(4)
        data[0] = flags
        data[1] = 0x00  # Unknown
        struct.pack_into("<H", data, 2, margin_value)
        # Byte 4 implicitly 0x00

        return bytes(data)

    @staticmethod
    def decode_designator_format(position_bytes: bytes) -> "PinTextPosition":
        """
        Decode position data from Designator format (bytes 1-4 of 12-byte format).

        Args:
            position_bytes: 4 bytes of position data

        Returns:
            PinTextPosition object
        """
        if len(position_bytes) != 4:
            raise ValueError("compact designator position must contain four bytes")
        flags = position_bytes[0]
        margin_value = struct.unpack("<H", position_bytes[2:4])[0]
        margin_mils = margin_value / 39.0625

        # Decode flags
        reference_to_component = bool(flags & 0x02)
        orientation = (
            PinTextOrientation.DEG_90 if (flags & 0x04) else PinTextOrientation.DEG_0
        )

        return PinTextPosition(
            margin_mils=margin_mils,
            orientation=orientation,
            reference_to_component=reference_to_component,
        )


def _coerce_pin_text_orientation(value: PinTextOrientation | int) -> PinTextOrientation:
    if isinstance(value, PinTextOrientation):
        return value
    if value in (0, 90, 180, 270):
        return PinTextOrientation(value)
    raise ValueError(f"Invalid PinTextOrientation value: {value}")


def _read_record(
    stream: io.BytesIO,
) -> tuple[PinTextPosition | None, int | None, int | None]:
    """
    Read one self-describing pin text record from a stream.
    """
    record_offset = stream.tell()
    flags_byte = _read_exact(stream, 1, "pin text flags")
    flags = flags_byte[0]
    unknown_flags = flags & ~_KNOWN_RECORD_FLAGS
    if unknown_flags:
        raise _codec_error(
            "malformed",
            record_offset,
            f"pin text flags contain unknown bits 0x{unknown_flags:02X}",
        )
    if not flags & 0x01 and flags & 0x0E:
        raise _codec_error(
            "malformed",
            record_offset,
            "pin text anchor or rotation bits require a custom position",
        )

    position = None
    font_id = None
    color = None

    if flags & 0x01:  # Custom position
        margin_dxp = struct.unpack("<i", _read_exact(stream, 4, "pin text margin"))[0]
        margin_mils = margin_dxp / 10000.0
        rotation = PinTextOrientation(((flags & 0x0C) >> 2) * 90)
        ref_to_comp = bool(flags & 0x02)
        position = PinTextPosition(
            margin_mils,
            rotation,
            ref_to_comp,
            _raw_margin_dxp=margin_dxp,
            _raw_margin_mils=margin_mils,
        )

    if flags & 0x10:  # Custom font
        font_id = struct.unpack("<h", _read_exact(stream, 2, "pin text font id"))[0]
        color = struct.unpack("<I", _read_exact(stream, 4, "pin text color"))[0]

    return position, font_id, color


def _write_record(
    stream: io.BytesIO,
    position: PinTextPosition | None,
    font_id: int | None,
    color: int | None,
) -> None:
    """
    Write one self-describing pin text record to a stream.
    """
    checked_font_id, checked_color, margin_dxp = _validate_record_values(
        position, font_id, color
    )
    flags = _record_flags(position, checked_font_id)

    stream.write(bytes([flags]))
    if margin_dxp is not None:
        stream.write(struct.pack("<i", margin_dxp))
    if checked_font_id is not None:
        stream.write(struct.pack("<h", checked_font_id))
        stream.write(struct.pack("<I", checked_color or 0))


def _validate_record_values(
    position: PinTextPosition | None,
    font_id: int | None,
    color: int | None,
) -> tuple[int | None, int | None, int | None]:
    checked_font_id = (
        None if font_id is None else _checked_i16(font_id, "pin text font id")
    )
    checked_color = None if color is None else _checked_u32(color, "pin text color")
    if checked_color is not None and checked_font_id is None:
        raise ValueError("pin text color requires a custom font id")
    margin_dxp = None if position is None else position._to_dxp()
    return checked_font_id, checked_color, margin_dxp


def _record_flags(position: PinTextPosition | None, font_id: int | None) -> int:
    flags = 0
    if position is not None:
        flags |= 0x01
        if position.reference_to_component:
            flags |= 0x02
        flags |= ((position.orientation.value // 90) << 2) & 0x0C
    if font_id is not None:
        flags |= 0x10
    return flags


@dataclass
class PinTextData:
    """
    Parsed PinTextData for a single PIN.
    """

    format_type: str  # 'NAME_ONLY', 'DESIGNATOR_ONLY', 'BOTH', 'NAME_POSITION', 'DESIGNATOR_POSITION', 'BOTH_POSITION', 'POSITION_ONLY'
    raw_data: bytearray

    # Parsed fields
    name_font_id: int | None = None
    name_color: int | None = None
    designator_font_id: int | None = None
    designator_color: int | None = None
    position: PinTextPosition | None = None  # Alias for name_position.
    name_position: PinTextPosition | None = None
    designator_position: PinTextPosition | None = None

    @classmethod
    def create_both_format(
        cls,
        name_font_id: int = 1,
        designator_font_id: int = 1,
        name_color: int = 0x00000000,  # Black
        designator_color: int = 0x00000000,
    ) -> "PinTextData":
        """
        Create a new PinTextData entry in BOTH format (14 bytes, no position).

        This enables custom settings for both Name and Designator without custom positioning.

        Args:
            name_font_id: Font ID for name (1-based, references FileHeader fonts)
            designator_font_id: Font ID for designator (1-based)
            name_color: Name color (Win32 COLORREF: 0x00BBGGRR)
            designator_color: Designator color (Win32 COLORREF: 0x00BBGGRR)

        Returns:
            PinTextData object with 14-byte BOTH format
        """
        obj = cls(
            format_type="BOTH",
            raw_data=bytearray(),  # Will be set by serialize
            name_font_id=name_font_id,
            name_color=name_color,
            designator_font_id=designator_font_id,
            designator_color=designator_color,
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def create_name_designator_position_format(
        cls,
        name_font_id: int = 1,
        designator_font_id: int = 1,
        name_color: int = 0x00000000,
        designator_color: int = 0x00000000,
        designator_margin_mils: float = 200.0,
        designator_position_flags: int = 0x80,
    ) -> "PinTextData":
        """
        Create a new PinTextData entry with NAME font + DESIGNATOR with position+font (18 bytes).

        This format has standard NAME section + DESIGNATOR with custom position margin.
        Used when only designator needs custom positioning.

        Args:
            name_font_id: Font ID for name (1-based, references FileHeader fonts)
            designator_font_id: Font ID for designator (1-based)
            name_color: Name color (Win32 COLORREF: 0x00BBGGRR)
            designator_color: Designator color (Win32 COLORREF: 0x00BBGGRR)
            designator_margin_mils: Designator position margin in mils (default 200.0)
            designator_position_flags: Ignored (kept for API compat)

        Returns:
            PinTextData object with 18-byte Layout B format (7+11)
        """
        designator_position = PinTextPosition(
            margin_mils=designator_margin_mils,
            orientation=PinTextOrientation.DEG_0,
            reference_to_component=False,
        )

        obj = cls(
            format_type="NAME_DESIGNATOR_POSITION",
            raw_data=bytearray(),
            name_font_id=name_font_id,
            name_color=name_color,
            designator_font_id=designator_font_id,
            designator_color=designator_color,
            designator_position=designator_position,
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def create_position_only_format(
        cls,
        name_margin: int = 0,
        name_flags: int = 0x01,
        designator_margin: int = 0,
        designator_flags: int = 0x01,
    ) -> "PinTextData":
        """
        Create a new PinTextData entry in POSITION_ONLY format (10 bytes).

        This format is for pins with custom position but default font for both
        Name and Designator. No fonts or colors are stored.

        Args:
            name_margin: Name margin in DXP units (mils * 10000)
            name_flags: Name flags byte (default 0x01 = position custom)
            designator_margin: Designator margin in DXP units
            designator_flags: Designator flags byte (default 0x01)

        Returns:
            PinTextData object with 10-byte POSITION_ONLY format
        """
        name_pos = PinTextPosition(
            margin_mils=name_margin / 10000.0,
            orientation=PinTextOrientation(((name_flags & 0x0C) >> 2) * 90),
            reference_to_component=bool(name_flags & 0x02),
        )
        des_pos = PinTextPosition(
            margin_mils=designator_margin / 10000.0,
            orientation=PinTextOrientation(((designator_flags & 0x0C) >> 2) * 90),
            reference_to_component=bool(designator_flags & 0x02),
        )

        obj = cls(
            format_type="POSITION_ONLY",
            raw_data=bytearray(),
            name_position=name_pos,
            designator_position=des_pos,
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def parse(cls, raw_data: bytes) -> "PinTextData":
        """
        Parse decompressed PinTextData bytes using stream-based architecture.

        Matches native ReadPinTextDataFromStream: reads two sequential variable-length
        records (name then designator), each self-describing via a flags byte.

        Args:
            raw_data: Decompressed PinTextData bytes

        Returns:
            PinTextData object
        """
        stream = io.BytesIO(raw_data)
        name_pos, name_fid, name_color = _read_record(stream)
        desig_pos, desig_fid, desig_color = _read_record(stream)
        if stream.tell() != len(raw_data):
            raise _codec_error(
                "malformed",
                stream.tell(),
                "trailing bytes after the name and designator records",
            )

        layout = (
            name_pos is not None,
            name_fid is not None,
            desig_pos is not None,
            desig_fid is not None,
        )
        fmt = _FORMAT_BY_LAYOUT[layout]

        return cls(
            format_type=fmt,
            raw_data=bytearray(raw_data),
            name_font_id=name_fid,
            name_color=name_color,
            designator_font_id=desig_fid,
            designator_color=desig_color,
            position=name_pos,  # Alias field mirrors name_position.
            name_position=name_pos,
            designator_position=desig_pos,
        )

    def modify_color(
        self, name_color: int | None = None, designator_color: int | None = None
    ) -> None:
        """
        Modify name and/or designator colors.

        Args:
            name_color: New name color (Win32 COLORREF format)
            designator_color: New designator color (Win32 COLORREF format)
        """
        checked_name = (
            None if name_color is None else _checked_u32(name_color, "name_color")
        )
        checked_designator = (
            None
            if designator_color is None
            else _checked_u32(designator_color, "designator_color")
        )
        if checked_name is not None and self.name_font_id is None:
            raise ValueError("name_color requires a custom name font id")
        if checked_designator is not None and self.designator_font_id is None:
            raise ValueError("designator_color requires a custom designator font id")
        if checked_name is not None:
            self.name_color = checked_name
        if checked_designator is not None:
            self.designator_color = checked_designator
        self.raw_data = bytearray(self.serialize())

    def modify_font_id(
        self, name_font_id: int | None = None, designator_font_id: int | None = None
    ) -> None:
        """
        Modify font IDs.

        Args:
            name_font_id: New name font ID (1-based)
            designator_font_id: New designator font ID (1-based)

        Font IDs use the signed 16-bit field width written by the managed
        provider, including when the same record also contains a position.
        """
        checked_name = (
            None if name_font_id is None else _checked_i16(name_font_id, "name_font_id")
        )
        checked_designator = (
            None
            if designator_font_id is None
            else _checked_i16(designator_font_id, "designator_font_id")
        )
        if checked_name is not None:
            self.name_font_id = checked_name
            if self.name_color is None:
                self.name_color = 0
        if checked_designator is not None:
            self.designator_font_id = checked_designator
            if self.designator_color is None:
                self.designator_color = 0
        self.raw_data = bytearray(self.serialize())

    def serialize(self) -> bytes:
        """
        Serialize back to binary format using stream-based architecture.
        """
        stream = io.BytesIO()
        _write_record(stream, self.name_position, self.name_font_id, self.name_color)
        _write_record(
            stream,
            self.designator_position,
            self.designator_font_id,
            self.designator_color,
        )
        return stream.getvalue()


class PinTextDataModifier:
    """
    Modifies PIN text attributes in PinTextData stream.
    """

    def __init__(self) -> None:
        self.entries: list[
            tuple[str, PinTextData]
        ] = []  # List of (designator, PinTextData)
        self._raw_compressed: dict[str, bytes] = {}
        self._compatibility_diagnostics: tuple[str, ...] = ()
        self._unread_suffix = b""

    def parse(
        self,
        data: bytes,
        *,
        limits: SchAuxiliaryReadLimits | None = None,
    ) -> bool:
        """
        Parse PinTextData stream.

        Args:
            data: Raw PinTextData stream bytes

        Returns:
            True if successfully parsed
        """
        result = _decode_pintext_stream(data, limits)
        parsed_entries: list[tuple[str, PinTextData]] = []
        raw_compressed: dict[str, bytes] = {}
        for entry in result.entries:
            parsed_entries.append((entry.name, PinTextData.parse(entry.data)))
            raw_compressed[entry.name] = entry.compressed_data
        self.entries = parsed_entries
        self._raw_compressed = raw_compressed
        self._compatibility_diagnostics = result.diagnostics
        self._unread_suffix = result.unread_suffix
        return True

    def _applicable_entries(self, pin_count: int) -> list[tuple[int, PinTextData]]:
        applicable: list[tuple[int, PinTextData]] = []
        for name, pin_data in self.entries:
            index = _try_parse_managed_i32(name)
            if index is not None and 0 <= index < pin_count:
                applicable.append((index, pin_data))
        return applicable

    def get_entry(self, designator: str) -> PinTextData | None:
        """
        Get PinTextData for a specific PIN.
        """
        for desig, pin_data in self.entries:
            if desig == designator:
                return pin_data
        return None

    def add_entry(
        self,
        designator: str,
        name_font_id: int = 1,
        designator_font_id: int = 1,
        name_color: int = 0x00000000,
        designator_color: int = 0x00000000,
    ) -> bool:
        """
        Add a new PinTextData entry for a PIN that doesn't have one.

        This enables custom settings for PINs with custom settings disabled.

        Args:
            designator: PIN designator (e.g., "1", "2", etc.)
            name_font_id: Font ID for name (defaults to 1)
            designator_font_id: Font ID for designator (defaults to 1)
            name_color: Name color (Win32 COLORREF, defaults to black)
            designator_color: Designator color (Win32 COLORREF, defaults to black)

        Returns:
            True if added, False if entry already exists
        """
        _validate_pin_index_name(designator, 0)
        # Check if entry already exists
        if self.get_entry(designator) is not None:
            return False

        # Create new BOTH format PinTextData
        pin_data = PinTextData.create_both_format(
            name_font_id=name_font_id,
            designator_font_id=designator_font_id,
            name_color=name_color,
            designator_color=designator_color,
        )

        # Add to entries
        self.entries.append((designator, pin_data))
        return True

    def modify_entry(
        self,
        designator: str,
        name_color: int | None = None,
        designator_color: int | None = None,
        name_font_id: int | None = None,
        designator_font_id: int | None = None,
    ) -> bool:
        """
        Modify text attributes for a specific PIN.

        Args:
            designator: PIN designator to modify
            name_color: Name color (Win32 COLORREF format, 0x00BBGGRR)
            designator_color: Designator color (Win32 COLORREF format)
            name_font_id: Name font ID (1-based, references FileHeader fonts)
            designator_font_id: Designator font ID (1-based)

        Returns:
            True if modified

        Note:
            Font styles (bold, italic, underline) cannot be modified here.
            They are defined in the FileHeader font table (FontName1-N, Size1-N, Bold1-N, etc.)
        """
        for index, (entry_name, pin_data) in enumerate(self.entries):
            if entry_name != designator:
                continue
            candidate = _modified_entry(
                pin_data,
                name_color=name_color,
                designator_color=designator_color,
                name_font_id=name_font_id,
                designator_font_id=designator_font_id,
            )
            self.entries[index] = (entry_name, candidate)
            return True
        return False

    def modify_all(
        self,
        name_color: int | None = None,
        designator_color: int | None = None,
        name_font_id: int | None = None,
        designator_font_id: int | None = None,
    ) -> int:
        """
        Modify text attributes for ALL PINs.

        Returns:
            Number of PINs modified
        """
        candidates = [
            (
                designator,
                _modified_entry(
                    pin_data,
                    name_color=name_color,
                    designator_color=designator_color,
                    name_font_id=name_font_id,
                    designator_font_id=designator_font_id,
                ),
            )
            for designator, pin_data in self.entries
        ]
        self.entries = candidates
        return len(candidates)

    def renumber_font_ids(self, font_id_map: dict[int, int]) -> int:
        """
        Renumber font IDs in all PIN entries.

        Args:
            font_id_map: Dictionary mapping old font ID -> new font ID

        Returns:
            Number of PINs modified
        """
        checked_map = {
            _checked_i16(old_id, "source font id"): _checked_i16(
                new_id, "translated font id"
            )
            for old_id, new_id in font_id_map.items()
        }
        for _designator, pin_data in self.entries:
            pin_data.serialize()
        return sum(
            _renumber_entry(pin_data, checked_map)
            for _designator, pin_data in self.entries
        )

    def serialize(self, original_data: bytes | None = None) -> bytes:
        """
        Serialize back to PinTextData format.

        Args:
            original_data: Original PinTextData stream (for header), or None to create new stream

        Returns:
            Modified PinTextData stream
        """
        if not self.entries:
            raise _codec_error(
                "malformed", 0, "PinTextData is omitted when it has no entries"
            )
        for pin_index, _pin_data in self.entries:
            _validate_pin_index_name(pin_index, 0)
        raw_compressed = dict(self._raw_compressed)
        if original_data is not None:
            original_entries = _decode_pintext_stream(original_data).entries
            raw_compressed.update(
                {entry.name: entry.compressed_data for entry in original_entries}
            )
        payloads = [
            (designator, pin_data.serialize()) for designator, pin_data in self.entries
        ]
        return encode_auxiliary_stream(
            "PinTextData", payloads, raw_compressed=raw_compressed
        )


def _modified_entry(
    pin_data: PinTextData,
    *,
    name_color: int | None,
    designator_color: int | None,
    name_font_id: int | None,
    designator_font_id: int | None,
) -> PinTextData:
    candidate = deepcopy(pin_data)
    candidate.modify_font_id(name_font_id, designator_font_id)
    candidate.modify_color(name_color, designator_color)
    return candidate


def _renumber_entry(pin_data: PinTextData, font_id_map: dict[int, int]) -> int:
    old_name = pin_data.name_font_id
    old_designator = pin_data.designator_font_id
    new_name = font_id_map.get(old_name, old_name) if old_name is not None else None
    new_designator = (
        font_id_map.get(old_designator, old_designator)
        if old_designator is not None
        else None
    )
    name_changed = new_name != old_name
    designator_changed = new_designator != old_designator
    if not name_changed and not designator_changed:
        return 0
    pin_data.modify_font_id(
        name_font_id=new_name if name_changed else None,
        designator_font_id=new_designator if designator_changed else None,
    )
    return 1
