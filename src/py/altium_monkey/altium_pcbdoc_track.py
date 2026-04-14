"""
Parse binary track records from PcbDoc Tracks6/Data streams.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .altium_ole import AltiumOleFile
from .altium_record_types import PcbRecordType

log = logging.getLogger(__name__)


@dataclass
class PcbDocTrack:
    """
    Track instance from PcbDoc in global board coordinates.
    """

    start_x: float = 0.0
    start_y: float = 0.0
    end_x: float = 0.0
    end_y: float = 0.0
    width: float = 0.0
    layer: int = 0
    component_index: int | None = None
    net_index: int | None = None
    is_keepout: bool = False
    is_polygonoutline: bool = False
    raw_data: bytes | None = None


class BinarySubRecord:
    """
    Binary subrecord reader for PcbDoc track payloads.
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


def find_track_records(data: bytes) -> list[int]:
    """
    Find likely track-record start offsets in a Tracks6/Data stream.
    """

    records: list[int] = []
    offset = 0

    while offset < len(data) - 10:
        if data[offset] == PcbRecordType.TRACK:
            try:
                subrecord_len = struct.unpack("<I", data[offset + 1 : offset + 5])[0]
                if 0 < subrecord_len < 200:
                    records.append(offset)
                    offset += subrecord_len + 10
                else:
                    offset += 1
            except Exception:
                offset += 1
        else:
            offset += 1

    return records


def parse_track_record(data: bytes, offset: int) -> PcbDocTrack | None:
    """
    Parse a single Tracks6 record from binary data.
    """

    try:
        track = PcbDocTrack()
        cursor = offset

        if data[cursor] != PcbRecordType.TRACK:
            return None
        cursor += 1

        try:
            subrecord1 = BinarySubRecord(data, cursor)
            cursor += subrecord1.length

            content = subrecord1.content
            pos = 0

            if len(content) < 35:
                log.warning(f"SubRecord 1 too short at offset {offset}: {len(content)} bytes")
                return None

            track.layer = content[pos]
            pos += 1

            flags1 = content[pos]
            track.is_polygonoutline = (flags1 & 0x02) != 0
            pos += 1

            flags2 = content[pos]
            track.is_keepout = flags2 == 2
            pos += 1

            track.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            pos += 2

            # Component index is the key ownership linkage.
            track.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            pos += 4

            start_x_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            start_y_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            track.start_x = start_x_raw / 10000.0
            track.start_y = start_y_raw / 10000.0

            end_x_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            end_y_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            track.end_x = end_x_raw / 10000.0
            track.end_y = end_y_raw / 10000.0

            width_raw = struct.unpack("<I", content[pos : pos + 4])[0]
            track.width = width_raw / 10000.0

            track.raw_data = data[offset:cursor]

        except Exception as exc:
            log.warning(f"Failed to parse SubRecord 1 at offset {offset}: {exc}")
            return None

        return track

    except Exception as exc:
        log.warning(f"Error parsing track record at offset {offset}: {exc}")
        return None


def parse_tracks_from_pcbdoc(pcbdoc_path: Path, verbose: bool = False) -> list[PcbDocTrack]:
    """
    Parse all track records from a PcbDoc file.
    """

    pcbdoc_path = Path(pcbdoc_path)

    if verbose:
        log.info(f"Parsing tracks from: {pcbdoc_path.name}")

    ole = AltiumOleFile(str(pcbdoc_path))

    if not ole.exists(["Tracks6", "Data"]):
        if verbose:
            log.warning("No Tracks6/Data stream found")
        ole.close()
        return []

    data = ole.openstream(["Tracks6", "Data"])
    ole.close()

    if verbose:
        log.info(f"  Tracks6/Data size: {len(data):,} bytes")

    record_offsets = find_track_records(data)

    if verbose:
        log.info(f"  Found {len(record_offsets)} potential track records")

    tracks: list[PcbDocTrack] = []
    for offset in record_offsets:
        track = parse_track_record(data, offset)
        if track:
            tracks.append(track)

    if verbose:
        log.info(f"  Parsed {len(tracks)} tracks successfully")

    return tracks


def parse_tracks_from_pcblib(
    pcblib_path: Path,
    footprint_name: str | None = None,
    verbose: bool = False,
) -> list[PcbDocTrack]:
    """
    Parse all track records from a PcbLib footprint stream.
    """

    pcblib_path = Path(pcblib_path)

    if verbose:
        log.info(f"Parsing tracks from: {pcblib_path.name}")

    ole = AltiumOleFile(str(pcblib_path))

    if footprint_name is None:
        if not ole.exists(["Library", "Data"]):
            if verbose:
                log.warning("No Library/Data stream found")
            ole.close()
            return []

        library_data = ole.openstream(["Library", "Data"])
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

    if not ole.exists([footprint_name, "Data"]):
        if verbose:
            log.warning(f"No {footprint_name}/Data stream found")
        ole.close()
        return []

    data = ole.openstream([footprint_name, "Data"])
    ole.close()

    if verbose:
        log.info(f"  {footprint_name}/Data size: {len(data):,} bytes")

    offset = 0
    if len(data) >= 4:
        header_size = struct.unpack("<I", data[0:4])[0]
        offset = 4 + header_size

    tracks: list[PcbDocTrack] = []
    while offset < len(data) - 5:
        type_byte = data[offset]

        if type_byte == PcbRecordType.PAD:
            offset += 1
            for _ in range(6):
                if offset + 4 > len(data):
                    break
                sr_len = struct.unpack("<I", data[offset : offset + 4])[0]
                offset += 4 + sr_len
        elif type_byte == PcbRecordType.TRACK:
            track = parse_track_record(data, offset)
            if track:
                tracks.append(track)

            offset += 1
            if offset + 4 <= len(data):
                sr_len = struct.unpack("<I", data[offset : offset + 4])[0]
                offset += 4 + sr_len
        else:
            break

    if verbose:
        log.info(f"  Parsed {len(tracks)} tracks successfully")

    return tracks
