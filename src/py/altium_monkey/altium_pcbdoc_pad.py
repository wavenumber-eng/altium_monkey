"""
Parse binary pad records from PcbDoc and PcbLib pad data streams.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .altium_ole import AltiumOleFile
from .altium_record_types import PcbRecordType

log = logging.getLogger(__name__)


@dataclass
class PcbDocPad:
    """
    Pad instance from PcbDoc (in global board coordinates).

    Attributes:
        designator: Pad number/name (e.g., "1", "2", "GND")
        x: X position in global board coordinates (mils)
        y: Y position in global board coordinates (mils)
        width: Pad width (mils)
        height: Pad height (mils)
        hole_size: Drill hole diameter (0 for SMT)
        shape: Pad shape (1=Round, 2=Rect, 3=Octagon, etc.)
        layer: Layer (TOP, BOTTOM, MULTI_LAYER)
        component_index: Component index (uint16, NOT GUID) - Links pad to component
        net_index: Net index (uint16)
        raw_data: Original binary data for debugging
    """

    designator: str = ""
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    hole_size: float = 0.0
    shape: int = 1  # 1=Round
    layer: str = "MULTI_LAYER"
    component_index: int | None = None
    net_index: int | None = None
    raw_data: bytes | None = None


class BinarySubRecord:
    """
    Binary SubRecord reader (same format as PcbLib).

    Format:
        [4 bytes] Length (N) - Little-endian uint32 (content length, NOT including this field)
        [N bytes] Content
    """

    def __init__(self, data: bytes, offset: int = 0) -> None:
        if len(data) < offset + 4:
            raise ValueError(f"Not enough data for SubRecord at offset {offset}")

        # Read content length
        content_length = struct.unpack("<I", data[offset : offset + 4])[0]

        # Total length includes 4-byte length field + content
        self.length = content_length + 4
        self.content = data[offset + 4 : offset + self.length]

        if len(self.content) < content_length:
            raise ValueError(
                f"SubRecord content truncated: expected {content_length}, got {len(self.content)}"
            )


def read_int32_le(data: bytes, offset: int) -> int:
    """
    Read signed 32-bit integer (little-endian).
    """
    return struct.unpack_from("<i", data, offset)[0]


def read_float64_le(data: bytes, offset: int) -> float:
    """
    Read 64-bit float (little-endian).
    """
    return struct.unpack_from("<d", data, offset)[0]


def find_pad_records(data: bytes) -> list[int]:
    """
    Find Pad record start offsets in binary data.

    Pad records start with a pad discriminator followed by SubRecords.
    We look for that discriminator followed by reasonable SubRecord structure.

    Args:
        data: Binary data from Pads6/Data stream

    Returns:
        List of offsets where pad records start
    """
    records = []
    offset = 0

    while offset < len(data) - 10:
        # Look for the pad record discriminator.
        if data[offset] == 0x02:
            # Check if next 4 bytes look like a SubRecord length (small value)
            try:
                subrecord_len = struct.unpack("<I", data[offset + 1 : offset + 5])[0]
                # SubRecord lengths should be reasonable (< 1000 bytes for designator)
                if 0 < subrecord_len < 1000:
                    records.append(offset)
                    # Skip ahead past this record (estimate ~200-300 bytes)
                    offset += 150
                else:
                    offset += 1
            except Exception:
                offset += 1
        else:
            offset += 1

    return records


def parse_pad_record(data: bytes, offset: int) -> PcbDocPad | None:
    """
    Parse a single Pad record from binary data using SubRecord structure.

    Pad records have same structure as PcbLib:
        [1 byte] Type = 0x02
        [SubRecord 1] Designator (string)
        [SubRecord 2] Unknown
        [SubRecord 3] Unknown
        [SubRecord 4] Unknown
        [SubRecord 5] SizeAndShape - main geometry
        [SubRecord 6] SizeAndShapeByLayer - per-layer variations

    Args:
        data: Full binary data
        offset: Start offset of this pad record

    Returns:
        PcbDocPad object or None if parsing fails
    """
    try:
        pad = PcbDocPad()
        cursor = offset

        # Verify type byte
        type_byte = data[cursor]
        if type_byte != PcbRecordType.PAD:
            return None
        cursor += 1

        # SubRecord 1: Designator (Pascal string: length byte + string)
        try:
            subrecord1 = BinarySubRecord(data, cursor)
            cursor += subrecord1.length
            # Parse as Pascal string: first byte is length, rest is string
            if len(subrecord1.content) >= 1:
                str_len = subrecord1.content[0]
                if str_len > 0 and len(subrecord1.content) >= str_len + 1:
                    pad.designator = subrecord1.content[1 : 1 + str_len].decode(
                        "utf-8", errors="replace"
                    )
                else:
                    pad.designator = ""
            else:
                pad.designator = ""
        except Exception as e:
            log.warning(
                f"Failed to parse SubRecord 1 (designator) at offset {offset}: {e}"
            )
            return None

        # SubRecord 2: Unknown
        try:
            subrecord2 = BinarySubRecord(data, cursor)
            cursor += subrecord2.length
        except Exception:
            return None

        # SubRecord 3: Unknown
        try:
            subrecord3 = BinarySubRecord(data, cursor)
            cursor += subrecord3.length
        except Exception:
            return None

        # SubRecord 4: Unknown
        try:
            subrecord4 = BinarySubRecord(data, cursor)
            cursor += subrecord4.length
        except Exception:
            return None

        # SubRecord 5: SizeAndShape (main geometry)
        try:
            subrecord5 = BinarySubRecord(data, cursor)
            cursor += subrecord5.length

            content = subrecord5.content
            pos = 0

            # Parse header fields (based on record format source)
            if len(content) < 110:
                log.warning(
                    f"SubRecord 5 too short at offset {offset}: {len(content)} bytes"
                )
                return None

            content[pos]  # Layer byte
            pos += 1

            content[pos]  # Flags
            pos += 1

            content[pos]  # More flags
            pos += 1

            # Net index (uint16)
            pad.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            # Skip 2 bytes
            pos += 2

            # Component index (uint16) - THE KEY LINKAGE!
            pad.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            # Skip 4 bytes to reach position
            pos += 4

            # Now at position (offset 13 in SubRecord 5)
            def read_int32() -> int:
                nonlocal pos
                if pos + 4 > len(content):
                    return 0
                val = struct.unpack("<i", content[pos : pos + 4])[0]
                pos += 4
                return val

            # Extract coordinates and sizes
            pad.x = read_int32() / 10000.0  # Convert to mils
            pad.y = read_int32() / 10000.0
            x_size_top = read_int32() / 10000.0
            y_size_top = read_int32() / 10000.0
            read_int32() / 10000.0
            read_int32() / 10000.0
            read_int32() / 10000.0
            read_int32() / 10000.0
            pad.hole_size = read_int32() / 10000.0

            # Use top layer size as width/height
            pad.width = x_size_top
            pad.height = y_size_top

            # Parse shape (3 bytes)
            if pos + 3 <= len(content):
                pad.shape = content[pos]  # Top layer shape
                pos += 3

        except Exception as e:
            log.warning(
                f"Failed to parse SubRecord 5 (geometry) at offset {offset}: {e}"
            )
            return None

        # SubRecord 6: SizeAndShapeByLayer (skip for now)
        try:
            subrecord6 = BinarySubRecord(data, cursor)
            cursor += subrecord6.length

            # TODO: Extract component GUID if present in this SubRecord
            # For now, store the raw data
            pad.raw_data = data[offset:cursor]

        except Exception:
            pass

        return pad if pad.designator or (pad.x != 0.0 and pad.y != 0.0) else None

    except Exception as e:
        log.warning(f"Error parsing pad record at offset {offset}: {e}")
        return None


def parse_pads_from_pcbdoc(pcbdoc_path: Path, verbose: bool = False) -> list[PcbDocPad]:
    """
    Parse all pad records from a PcbDoc file.

    Args:
        pcbdoc_path: Path to .PcbDoc file
        verbose: If True, print detailed parsing info

    Returns:
        List of PcbDocPad objects
    """
    pcbdoc_path = Path(pcbdoc_path)

    if verbose:
        log.info(f"Parsing pads from: {pcbdoc_path.name}")

    ole = AltiumOleFile(str(pcbdoc_path))

    if not ole.exists(["Pads6", "Data"]):
        if verbose:
            log.warning("No Pads6/Data stream found")
        ole.close()
        return []

    # Read binary data
    data = ole.openstream(["Pads6", "Data"])
    ole.close()

    if verbose:
        log.info(f"  Pads6/Data size: {len(data):,} bytes")

    # Find record start offsets
    record_offsets = find_pad_records(data)

    if verbose:
        log.info(f"  Found {len(record_offsets)} potential pad records")

    # Parse each record
    pads = []
    for offset in record_offsets:
        pad = parse_pad_record(data, offset)
        if pad:
            pads.append(pad)

    if verbose:
        log.info(f"  Parsed {len(pads)} pads successfully")

    return pads


def parse_pads_from_pcblib(
    pcblib_path: Path, footprint_name: str = None, verbose: bool = False
) -> list[PcbDocPad]:
    """
    Parse all pad records from a PcbLib file.

    Args:
        pcblib_path: Path to .PcbLib file
        footprint_name: Optional footprint name. If not provided, uses first footprint in library
        verbose: If True, print detailed parsing info

    Returns:
        List of PcbDocPad objects
    """
    pcblib_path = Path(pcblib_path)

    if verbose:
        log.info(f"Parsing pads from: {pcblib_path.name}")

    ole = AltiumOleFile(str(pcblib_path))

    # Get footprint name from Library/Data if not provided
    if footprint_name is None:
        if not ole.exists(["Library", "Data"]):
            if verbose:
                log.warning("No Library/Data stream found")
            ole.close()
            return []

        library_data = ole.openstream(
            ["Library", "Data"]
        )  # Parse footprint name from Library/Data (format: |PATTERN=FootprintName|...)
        library_text = library_data.decode("utf-8", errors="ignore")
        if "|PATTERN=" in library_text:
            start = library_text.index("|PATTERN=") + 9
            end = library_text.find("|", start)
            if end == -1:
                end = library_text.find("\x00", start)
            footprint_name = library_text[start:end].strip()
        else:
            if verbose:
                log.warning("Could not find footprint name in Library/Data")
            ole.close()
            return []

    if verbose:
        log.info(f"  Footprint: {footprint_name}")

    # Read footprint data stream
    if not ole.exists([footprint_name, "Data"]):
        if verbose:
            log.warning(f"No {footprint_name}/Data stream found")
        ole.close()
        return []

    data = ole.openstream([footprint_name, "Data"])
    ole.close()

    if verbose:
        log.info(f"  {footprint_name}/Data size: {len(data):,} bytes")

    # Skip footprint name header at start of Data stream
    offset = 0
    if len(data) >= 4:
        header_size = struct.unpack("<I", data[0:4])[0]
        offset = 4 + header_size  # Skip size field + header content

    # Sequentially walk through records (not heuristic search)
    pads = []
    while offset < len(data) - 5:
        type_byte = data[offset]

        if type_byte == PcbRecordType.PAD:
            pad = parse_pad_record(data, offset)
            if pad:
                pads.append(pad)

            # Skip past this record using SubRecord lengths
            offset += 1  # Type byte
            for _i in range(6):  # 6 SubRecords
                if offset + 4 > len(data):
                    break
                sr_len = struct.unpack("<I", data[offset : offset + 4])[0]
                offset += 4 + sr_len
        elif type_byte == PcbRecordType.TRACK:
            offset += 1  # Type byte
            if offset + 4 <= len(data):
                sr_len = struct.unpack("<I", data[offset : offset + 4])[0]
                offset += 4 + sr_len
        else:
            # Unknown type - stop parsing or skip byte
            break

    if verbose:
        log.info(f"  Parsed {len(pads)} pads successfully")

    return pads
