"""
Parse binary text records from PcbDoc Texts6/Data streams.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .altium_ole import AltiumOleFile
from .altium_record_types import PcbRecordType

log = logging.getLogger(__name__)


@dataclass
class PcbDocText:
    """
    Text instance from PcbDoc in global board coordinates.

    Texts can be component designators, comments, silkscreen labels, or
    other board annotations.
    """

    x: float = 0.0
    y: float = 0.0
    height: float = 0.0
    rotation: float = 0.0
    strokewidth: float = 0.0
    layer: int = 0
    component_index: int | None = None
    is_mirrored: bool = False
    is_comment: bool = False
    is_designator: bool = False
    text_content: str = ""
    raw_data: bytes | None = None


class BinarySubRecord:
    """
    Binary subrecord reader for PcbDoc text payloads.
    """

    def __init__(self, data: bytes, offset: int = 0) -> None:
        if len(data) < offset + 4:
            raise ValueError(f"Not enough data for SubRecord at offset {offset}")

        content_length = struct.unpack("<I", data[offset : offset + 4])[0]
        self.length = content_length + 4
        self.content = data[offset + 4 : offset + self.length]

        if len(self.content) < content_length:
            raise ValueError(
                f"SubRecord content truncated: expected {content_length}, got {len(self.content)}"
            )


def parse_widestrings_table(ole: AltiumOleFile) -> dict[int, str]:
    """
    Parse WideStrings6/Data into a lookup table keyed by string index.
    """

    if not ole.exists(["WideStrings6", "Data"]):
        return {}

    data = ole.openstream(["WideStrings6", "Data"])
    strings: dict[int, str] = {}
    offset = 0

    while offset < len(data) - 8:
        try:
            index = struct.unpack("<I", data[offset : offset + 4])[0]
            offset += 4

            length = struct.unpack("<I", data[offset : offset + 4])[0]
            offset += 4

            if offset + length * 2 <= len(data):
                text_bytes = data[offset : offset + length * 2]
                text = text_bytes.decode("utf-16le", errors="replace")
                strings[index] = text
                offset += length * 2
            else:
                break
        except Exception:
            break

    return strings


def find_text_records(data: bytes) -> list[int]:
    """
    Find likely text-record start offsets in a Texts6/Data stream.
    """

    records: list[int] = []
    offset = 0

    while offset < len(data) - 10:
        if data[offset] == 0x05:
            try:
                subrecord_len = struct.unpack("<I", data[offset + 1 : offset + 5])[0]
                if 0 < subrecord_len < 500:
                    records.append(offset)
                    offset += subrecord_len + 10
                else:
                    offset += 1
            except Exception:
                offset += 1
        else:
            offset += 1

    return records


def parse_text_record(data: bytes, offset: int, string_table: dict[int, str]) -> PcbDocText | None:
    """
    Parse a single Texts6 record from binary data.
    """

    try:
        text = PcbDocText()
        cursor = offset

        type_byte = data[cursor]
        if type_byte != PcbRecordType.TEXT:
            return None
        cursor += 1

        try:
            subrecord1 = BinarySubRecord(data, cursor)
            cursor += subrecord1.length

            content = subrecord1.content
            pos = 0

            if len(content) < 40:
                return None

            text.layer = content[pos]
            pos += 1

            pos += 6

            # Component index (offset 7-8) - key component linkage
            text.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            pos += 4

            x_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            y_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            text.x = x_raw / 10000.0
            text.y = y_raw / 10000.0

            height_raw = struct.unpack("<I", content[pos : pos + 4])[0]
            text.height = height_raw / 10000.0
            pos += 4

            pos += 2

            text.rotation = struct.unpack("<d", content[pos : pos + 8])[0]
            pos += 8

            text.is_mirrored = content[pos] != 0
            pos += 1

            if pos + 4 <= len(content):
                strokewidth_raw = struct.unpack("<I", content[pos : pos + 4])[0]
                text.strokewidth = strokewidth_raw / 10000.0
            pos += 4

            if len(content) >= 123:
                if pos < 40:
                    pos = 40

                if pos < len(content):
                    text.is_comment = content[pos] != 0
                pos += 1

                if pos < len(content):
                    text.is_designator = content[pos] != 0
                pos += 1

                # Keep WideStrings linkage handling lightweight in this helper.
                # The builder/parser-owned Texts6 surfaces carry richer text resolution.
                _ = string_table

            text.raw_data = data[offset:cursor]

        except Exception as e:
            log.warning(f"Failed to parse SubRecord 1 at offset {offset}: {e}")
            return None

        return text

    except Exception as e:
        log.warning(f"Error parsing text record at offset {offset}: {e}")
        return None


def parse_texts_from_pcbdoc(pcbdoc_path: Path, verbose: bool = False) -> list[PcbDocText]:
    """
    Parse all text records from a PcbDoc file.
    """

    pcbdoc_path = Path(pcbdoc_path)

    if verbose:
        log.info(f"Parsing texts from: {pcbdoc_path.name}")

    ole = AltiumOleFile(str(pcbdoc_path))

    string_table = parse_widestrings_table(ole)

    if not ole.exists(["Texts6", "Data"]):
        if verbose:
            log.warning("No Texts6/Data stream found")
        ole.close()
        return []

    data = ole.openstream(["Texts6", "Data"])
    ole.close()

    if verbose:
        log.info(f"  Texts6/Data size: {len(data):,} bytes")
        log.info(f"  String table:     {len(string_table)} entries")

    record_offsets = find_text_records(data)

    if verbose:
        log.info(f"  Found {len(record_offsets)} potential text records")

    texts: list[PcbDocText] = []
    for offset in record_offsets:
        text = parse_text_record(data, offset, string_table)
        if text:
            texts.append(text)

    if verbose:
        log.info(f"  Parsed {len(texts)} texts successfully")

    return texts
