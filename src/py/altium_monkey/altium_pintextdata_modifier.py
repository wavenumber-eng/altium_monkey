"""
Parse and modify schematic PinTextData streams.
"""


import io
import struct
import zlib
from dataclasses import dataclass

from .altium_sch_enums import PinTextOrientation


@dataclass
class PinTextPosition:
    """
    Position data for custom PIN text positioning.
    """
    margin_mils: float
    orientation: PinTextOrientation
    reference_to_component: bool  # True = to Component, False = to Pin

    def __post_init__(self) -> None:
        self.orientation = _coerce_pin_text_orientation(self.orientation)

    def encode_designator_format(self) -> bytes:
        """
        Encode position data for Designator format (bytes 1-4 of 12-byte format).
        
        Returns:
            4 bytes of position data
        """
        # Calculate margin value (round to preserve exact value during round-trips)
        margin_value = round(self.margin_mils * 39.0625)

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
        struct.pack_into('<H', data, 2, margin_value)
        # Byte 4 implicitly 0x00

        return bytes(data)

    @staticmethod
    def decode_designator_format(position_bytes: bytes) -> 'PinTextPosition':
        """
        Decode position data from Designator format (bytes 1-4 of 12-byte format).
        
        Args:
            position_bytes: 4 bytes of position data
        
        Returns:
            PinTextPosition object
        """
        flags = position_bytes[0]
        margin_value = struct.unpack('<H', position_bytes[2:4])[0]
        margin_mils = margin_value / 39.0625

        # Decode flags
        reference_to_component = bool(flags & 0x02)
        orientation = PinTextOrientation.DEG_90 if (flags & 0x04) else PinTextOrientation.DEG_0

        return PinTextPosition(
            margin_mils=margin_mils,
            orientation=orientation,
            reference_to_component=reference_to_component
        )


def _coerce_pin_text_orientation(value: PinTextOrientation | int) -> PinTextOrientation:
    if isinstance(value, PinTextOrientation):
        return value
    if value in (0, 90, 180, 270):
        return PinTextOrientation(value)
    raise ValueError(f"Invalid PinTextOrientation value: {value}")


def _read_record(stream: io.BytesIO) -> tuple[PinTextPosition | None, int | None, int | None]:
    """
    Read one self-describing pin text record from a stream.
    """
    flags_byte = stream.read(1)
    if not flags_byte:
        return None, None, None
    flags = flags_byte[0]

    position = None
    font_id = None
    color = None

    if flags & 0x01:  # Custom position
        margin_dxp = struct.unpack('<i', stream.read(4))[0]
        margin_mils = margin_dxp / 10000.0
        rotation = PinTextOrientation(((flags & 0x0C) >> 2) * 90)
        ref_to_comp = bool(flags & 0x02)
        position = PinTextPosition(margin_mils, rotation, ref_to_comp)

    if flags & 0x10:  # Custom font
        font_id = struct.unpack('<h', stream.read(2))[0]  # INT16 LE
        color = struct.unpack('<I', stream.read(4))[0]    # UINT32 LE

    return position, font_id, color


def _write_record(stream: io.BytesIO, position: PinTextPosition | None,
                  font_id: int | None, color: int | None) -> None:
    """
    Write one self-describing pin text record to a stream.
    """
    flags = 0
    if position is not None:
        flags |= 0x01
        if position.reference_to_component:
            flags |= 0x02
        flags |= ((position.orientation.value // 90) << 2) & 0x0C
    if font_id is not None:
        flags |= 0x10

    stream.write(bytes([flags]))
    if position is not None:
        stream.write(struct.pack('<i', int(position.margin_mils * 10000)))
    if font_id is not None:
        stream.write(struct.pack('<h', font_id))
        stream.write(struct.pack('<I', color or 0))


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
    def create_both_format(cls,
                          name_font_id: int = 1,
                          designator_font_id: int = 1,
                          name_color: int = 0x00000000,  # Black
                          designator_color: int = 0x00000000) -> 'PinTextData':
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
            format_type='BOTH',
            raw_data=bytearray(),  # Will be set by serialize
            name_font_id=name_font_id,
            name_color=name_color,
            designator_font_id=designator_font_id,
            designator_color=designator_color
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def create_name_designator_position_format(cls,
                                              name_font_id: int = 1,
                                              designator_font_id: int = 1,
                                              name_color: int = 0x00000000,
                                              designator_color: int = 0x00000000,
                                              designator_margin_mils: float = 200.0,
                                              designator_position_flags: int = 0x80) -> 'PinTextData':
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
            reference_to_component=False
        )

        obj = cls(
            format_type='NAME_DESIGNATOR_POSITION',
            raw_data=bytearray(),
            name_font_id=name_font_id,
            name_color=name_color,
            designator_font_id=designator_font_id,
            designator_color=designator_color,
            designator_position=designator_position
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def create_position_only_format(cls,
                                    name_margin: int = 0,
                                    name_flags: int = 0x01,
                                    designator_margin: int = 0,
                                    designator_flags: int = 0x01) -> 'PinTextData':
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
            reference_to_component=bool(name_flags & 0x02)
        )
        des_pos = PinTextPosition(
            margin_mils=designator_margin / 10000.0,
            orientation=PinTextOrientation(((designator_flags & 0x0C) >> 2) * 90),
            reference_to_component=bool(designator_flags & 0x02)
        )

        obj = cls(
            format_type='POSITION_ONLY',
            raw_data=bytearray(),
            name_position=name_pos,
            designator_position=des_pos
        )
        obj.raw_data = bytearray(obj.serialize())
        return obj

    @classmethod
    def parse(cls, raw_data: bytes) -> 'PinTextData':
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

        # Determine format_type from the parsed record layout.
        has_name_pos = name_pos is not None
        has_desig_pos = desig_pos is not None
        has_name_font = name_fid is not None
        has_desig_font = desig_fid is not None

        if has_name_pos and has_desig_pos:
            if has_name_font or has_desig_font:
                fmt = 'BOTH_POSITION'
            else:
                fmt = 'POSITION_ONLY'
        elif has_name_pos and not has_desig_pos:
            fmt = 'NAME_POSITION'
        elif not has_name_pos and has_desig_pos:
            fmt = 'DESIGNATOR_POSITION'
        elif has_name_font and has_desig_font:
            fmt = 'BOTH'
        elif has_name_font:
            fmt = 'NAME_ONLY'
        elif has_desig_font:
            fmt = 'DESIGNATOR_ONLY'
        else:
            fmt = 'POSITION_ONLY'  # fallback for 2-byte default

        # For NAME_POSITION with desig font but no desig position, use
        # NAME_POSITION (18-byte Layout A).
        if has_name_pos and has_name_font and has_desig_font and not has_desig_pos:
            fmt = 'NAME_POSITION'
        # For desig position + both fonts but no name position, use
        # NAME_DESIGNATOR_POSITION (18-byte Layout B)
        if not has_name_pos and has_desig_pos and has_name_font and has_desig_font:
            fmt = 'NAME_DESIGNATOR_POSITION'

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

    def modify_color(self, name_color: int | None = None,
                     designator_color: int | None = None) -> None:
        """
        Modify name and/or designator colors.
        
        Args:
            name_color: New name color (Win32 COLORREF format)
            designator_color: New designator color (Win32 COLORREF format)
        """
        if name_color is not None:
            self.name_color = name_color
        if designator_color is not None:
            self.designator_color = designator_color
        self.raw_data = bytearray(self.serialize())

    def modify_font_id(self, name_font_id: int | None = None,
                       designator_font_id: int | None = None) -> None:
        """
        Modify font IDs.
        
        Args:
            name_font_id: New name font ID (1-based)
            designator_font_id: New designator font ID (1-based)
        
        Note:
            Font ID 0 means "use Font ID 1 from font table" (typically Times New Roman).
            We DO modify font ID 0 to apply custom fonts.
            When custom positioning is enabled, font IDs must remain unchanged.
            Setting Font ID to a non-zero value in the position formats breaks
            margin display in Altium.
        """
        # Leave font IDs unchanged when custom position data is present.
        has_position = self.name_position is not None or self.designator_position is not None
        if has_position:
            return  # Font ID must remain unchanged when position is custom

        if name_font_id is not None:
            self.name_font_id = name_font_id
        if designator_font_id is not None:
            self.designator_font_id = designator_font_id
        self.raw_data = bytearray(self.serialize())

    def serialize(self) -> bytes:
        """
        Serialize back to binary format using stream-based architecture.
        """
        stream = io.BytesIO()
        _write_record(stream, self.name_position, self.name_font_id, self.name_color)
        _write_record(stream, self.designator_position, self.designator_font_id, self.designator_color)
        return stream.getvalue()


class PinTextDataModifier:
    """
    Modifies PIN text attributes in PinTextData stream.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, PinTextData]] = []  # List of (designator, PinTextData)

    def parse(self, data: bytes) -> bool:
        """
        Parse PinTextData stream.
        
        Args:
            data: Raw PinTextData stream bytes
        
        Returns:
            True if successfully parsed
        """
        try:
            cursor = 0

            # Skip header
            if cursor + 4 > len(data):
                return False

            header_len = struct.unpack('<I', data[cursor:cursor+4])[0]
            cursor += 4 + header_len

            # Parse entries
            while cursor < len(data):
                if cursor + 11 > len(data):
                    break

                # Read record length (3 bytes + 1 flag)
                record_len_bytes = data[cursor:cursor+3]
                record_len = struct.unpack('<I', record_len_bytes + b'\x00')[0]

                if record_len > 1000 or record_len < 5:
                    break

                # Read designator string length
                str_len = data[cursor+5]
                if str_len > 10 or str_len < 1:
                    break

                # Read designator
                designator = data[cursor+6:cursor+6+str_len].decode('iso-8859-1', errors='replace')

                # Read compressed data
                comp_start = cursor + 6 + str_len
                comp_len = struct.unpack('<I', data[comp_start:comp_start+4])[0]

                if comp_len > 500 or comp_len < 5:
                    break

                compressed_data = data[comp_start+4:comp_start+4+comp_len]

                # Decompress
                binary_attrs = zlib.decompress(compressed_data)

                # Parse into PinTextData object
                pin_data = PinTextData.parse(binary_attrs)
                self.entries.append((designator, pin_data))

                cursor += 4 + record_len

            return len(self.entries) > 0

        except Exception as e:
            print(f"Parse error: {e}")
            return False

    def get_entry(self, designator: str) -> PinTextData | None:
        """
        Get PinTextData for a specific PIN.
        """
        for desig, pin_data in self.entries:
            if desig == designator:
                return pin_data
        return None

    def add_entry(self, designator: str,
                 name_font_id: int = 1,
                 designator_font_id: int = 1,
                 name_color: int = 0x00000000,
                 designator_color: int = 0x00000000) -> bool:
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
        # Check if entry already exists
        if self.get_entry(designator) is not None:
            return False

        # Create new BOTH format PinTextData
        pin_data = PinTextData.create_both_format(
            name_font_id=name_font_id,
            designator_font_id=designator_font_id,
            name_color=name_color,
            designator_color=designator_color
        )

        # Add to entries
        self.entries.append((designator, pin_data))
        return True

    def modify_entry(self, designator: str,
                    name_color: int | None = None,
                    designator_color: int | None = None,
                    name_font_id: int | None = None,
                    designator_font_id: int | None = None) -> bool:
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
        pin_data = self.get_entry(designator)
        if pin_data is None:
            return False

        pin_data.modify_color(name_color, designator_color)
        pin_data.modify_font_id(name_font_id, designator_font_id)
        return True

    def modify_all(self,
                  name_color: int | None = None,
                  designator_color: int | None = None,
                  name_font_id: int | None = None,
                  designator_font_id: int | None = None) -> int:
        """
        Modify text attributes for ALL PINs.
        
        Returns:
            Number of PINs modified
        """
        count = 0
        for desig, _ in self.entries:
            if self.modify_entry(desig, name_color=name_color,
                               designator_color=designator_color,
                               name_font_id=name_font_id,
                               designator_font_id=designator_font_id):
                count += 1
        return count

    def renumber_font_ids(self, font_id_map: dict[int, int]) -> int:
        """
        Renumber font IDs in all PIN entries.
        
        Args:
            font_id_map: Dictionary mapping old font ID -> new font ID
        
        Returns:
            Number of PINs modified
        """
        count = 0
        for _designator, pin_data in self.entries:
            modified = False

            # Renumber name font ID
            if pin_data.name_font_id is not None:
                old_id = pin_data.name_font_id
                if old_id in font_id_map:
                    new_id = font_id_map[old_id]
                    if new_id != old_id:
                        pin_data.modify_font_id(name_font_id=new_id)
                        modified = True

            # Renumber designator font ID
            if pin_data.designator_font_id is not None:
                old_id = pin_data.designator_font_id
                if old_id in font_id_map:
                    new_id = font_id_map[old_id]
                    if new_id != old_id:
                        pin_data.modify_font_id(designator_font_id=new_id)
                        modified = True

            if modified:
                count += 1

        return count

    def serialize(self, original_data: bytes | None = None) -> bytes:
        """
        Serialize back to PinTextData format.
        
        Args:
            original_data: Original PinTextData stream (for header), or None to create new stream
        
        Returns:
            Modified PinTextData stream
        """
        # Copy header from original or create new header
        if original_data:
            # Parse existing header
            header_len = struct.unpack('<I', original_data[:4])[0]
            header_text = original_data[4:4+header_len].decode('ascii')

            # Update Weight to match current number of entries
            import re
            header_text = re.sub(r'Weight=\d+', f'Weight={len(self.entries)}', header_text)

            # Re-encode with updated Weight
            header_bytes = header_text.encode('ascii')
            result = bytearray(struct.pack('<I', len(header_bytes)))
            result.extend(header_bytes)
        else:
            # Create standard Altium header: |HEADER=PinTextData|Weight=N
            # Weight must equal number of PIN entries for Altium to recognize custom settings
            header_text = f'|HEADER=PinTextData|Weight={len(self.entries)}'.encode('ascii')
            result = bytearray(struct.pack('<I', len(header_text)))
            result.extend(header_text)

        # Write modified entries
        for designator, pin_data in self.entries:
            # Get serialized data
            attrs = pin_data.serialize()

            # Use the default zlib compression level so the stream header matches
            # the format Altium expects.
            compressed = zlib.compress(bytes(attrs))  # Default = level 6, header: 0x789C

            # Build record
            designator_bytes = designator.encode('iso-8859-1')
            str_len = len(designator_bytes)

            # Calculate record length
            record_len = 2 + str_len + 4 + len(compressed)  # marker(2) + desig + complen(4) + compressed

            # Write: 4-byte length (3 bytes + flag)
            result.extend(struct.pack('<I', record_len)[:3])
            result.append(0x01)  # Flag byte

            # Write: 2-byte marker + string length + designator
            result.append(0xD0)
            result.append(str_len)
            result.extend(designator_bytes)

            # Write: compressed length + compressed data
            result.extend(struct.pack('<I', len(compressed)))
            result.extend(compressed)

        return bytes(result)
