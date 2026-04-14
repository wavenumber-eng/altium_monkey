"""
Parse binary PCB arc records from `Arcs6/Data`.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from .altium_ole import AltiumOleFile
from .altium_record_types import PcbRecordType

log = logging.getLogger(__name__)


@dataclass
class PcbDocArc:
    """
    Arc instance from PcbDoc in global board coordinates.

    Attributes:
        center_x: Center X position in global board coordinates (mils)
        center_y: Center Y position in global board coordinates (mils)
        radius: Arc radius (mils)
        start_angle: Start angle (degrees, 0 deg = right, counterclockwise)
        end_angle: End angle (degrees)
        width: Line width (mils)
        layer: Layer name/number
        component_index: Component index (uint16, not GUID) linking the arc to a footprint
        net_index: Net index (uint16)
        is_keepout: True if this is a keepout arc
        is_polygonoutline: True if this is part of a polygon outline
        raw_data: Original binary data for debugging
    """

    center_x: float = 0.0
    center_y: float = 0.0
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    width: float = 0.0
    layer: int = 0
    component_index: int | None = None
    net_index: int | None = None
    is_keepout: bool = False
    is_polygonoutline: bool = False
    raw_data: bytes | None = None


class BinarySubRecord:
    """
    Read one length-prefixed binary subrecord.
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


def find_arc_records(data: bytes) -> list[int]:
    """
    Find candidate arc record offsets in `Arcs6/Data`.
    """

    records = []
    offset = 0

    while offset < len(data) - 10:
        if data[offset] == 0x01:
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


def parse_arc_record(data: bytes, offset: int) -> PcbDocArc | None:
    """
    Parse one arc record from `Arcs6/Data`.
    """

    try:
        arc = PcbDocArc()
        cursor = offset

        type_byte = data[cursor]
        if type_byte != PcbRecordType.ARC:
            return None
        cursor += 1

        try:
            subrecord1 = BinarySubRecord(data, cursor)
            cursor += subrecord1.length

            content = subrecord1.content
            pos = 0

            if len(content) < 47:
                log.warning(f"SubRecord 1 too short at offset {offset}: {len(content)} bytes")
                return None

            arc.layer = content[pos]
            pos += 1

            flags1 = content[pos]
            (flags1 & 0x04) == 0
            arc.is_polygonoutline = (flags1 & 0x02) != 0
            pos += 1

            flags2 = content[pos]
            arc.is_keepout = flags2 == 2
            pos += 1

            arc.net_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            pos += 2

            # Component index links the arc back to its owning footprint.
            arc.component_index = struct.unpack("<H", content[pos : pos + 2])[0]
            pos += 2

            pos += 4

            center_x_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            center_y_raw = struct.unpack("<i", content[pos : pos + 4])[0]
            pos += 4
            arc.center_x = center_x_raw / 10000.0
            arc.center_y = center_y_raw / 10000.0

            radius_raw = struct.unpack("<I", content[pos : pos + 4])[0]
            arc.radius = radius_raw / 10000.0
            pos += 4

            arc.start_angle = struct.unpack("<d", content[pos : pos + 8])[0]
            pos += 8

            arc.end_angle = struct.unpack("<d", content[pos : pos + 8])[0]
            pos += 8

            width_raw = struct.unpack("<I", content[pos : pos + 4])[0]
            arc.width = width_raw / 10000.0
            pos += 4

            arc.raw_data = data[offset:cursor]

        except Exception as e:
            log.warning(f"Failed to parse SubRecord 1 at offset {offset}: {e}")
            return None

        return arc

    except Exception as e:
        log.warning(f"Error parsing arc record at offset {offset}: {e}")
        return None


def parse_arcs_from_pcbdoc(pcbdoc_path: Path, verbose: bool = False) -> list[PcbDocArc]:
    """
    Parse all arc records from a `.PcbDoc` file.
    """

    pcbdoc_path = Path(pcbdoc_path)

    if verbose:
        log.info(f"Parsing arcs from: {pcbdoc_path.name}")

    ole = AltiumOleFile(str(pcbdoc_path))

    if not ole.exists(["Arcs6", "Data"]):
        if verbose:
            log.warning("No Arcs6/Data stream found")
        ole.close()
        return []

    data = ole.openstream(["Arcs6", "Data"])
    ole.close()

    if verbose:
        log.info(f"  Arcs6/Data size: {len(data):,} bytes")

    record_offsets = find_arc_records(data)

    if verbose:
        log.info(f"  Found {len(record_offsets)} potential arc records")

    arcs = []
    for offset in record_offsets:
        arc = parse_arc_record(data, offset)
        if arc:
            arcs.append(arc)

    if verbose:
        log.info(f"  Parsed {len(arcs)} arcs successfully")

    return arcs
