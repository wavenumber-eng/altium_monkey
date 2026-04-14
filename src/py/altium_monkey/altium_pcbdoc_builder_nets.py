"""
Net-authoring helpers for `PcbDocBuilder`.

This module keeps board-level net table ownership separate from the main
builder class while primitive insertion grows. The exact native unique-id
generation for Nets6 records is not fully known yet, so authored
nets use deterministic synthetic IDs with a stable 8-character uppercase token.
"""

from __future__ import annotations

import struct
import uuid
from typing import Sequence

from .altium_record_pcb__net import AltiumPcbNet
from .altium_utilities import create_stream_from_records, decode_byte_array, parse_byte_record


def parse_net_stream(data: bytes) -> tuple[AltiumPcbNet, ...]:
    """
    Parse `Nets6/Data` into NET objects.
    """
    nets: list[AltiumPcbNet] = []
    offset = 0
    while offset < len(data):
        if len(data) < offset + 4:
            raise ValueError("Invalid Nets6/Data stream")
        record_len = struct.unpack("<I", data[offset:offset + 4])[0]
        offset += 4
        if len(data) < offset + record_len:
            raise ValueError("Invalid Nets6/Data stream")
        raw_record = data[offset:offset + record_len]
        offset += record_len
        fields: dict[str, str] = {}
        for part in parse_byte_record(raw_record):
            decoded = decode_byte_array(part)
            if "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            fields[key] = value
        nets.append(AltiumPcbNet.from_record(fields))
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Nets6/Data")
    return tuple(nets)


def build_net_stream(nets: Sequence[AltiumPcbNet]) -> bytes:
    """
    Serialize NET objects back into `Nets6/Data`.
    """
    return create_stream_from_records([net.to_record() for net in nets])


def _deterministic_net_unique_id(name: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"pcbdoc-builder-net|{name}").hex[:8].upper()


def build_authored_net(
    name: str,
    *,
    preferred_width_mils: float = 10.0,
) -> AltiumPcbNet:
    """
    Create a modern authored NET record from first principles.
    """
    width = f"{format(float(preferred_width_mils), 'g')}mil"
    raw_record: dict[str, str] = {
        "SELECTION": "FALSE",
        "LAYER": "TOP",
        "LOCKED": "FALSE",
        "POLYGONOUTLINE": "FALSE",
        "USERROUTED": "TRUE",
        "KEEPOUT": "FALSE",
        "UNIONINDEX": "0",
        "PRIMITIVELOCK": "FALSE",
        "VISIBLE": "TRUE",
        "COLOR": "65535",
        "LOOPREMOVAL": "TRUE",
        "OVERRIDECOLORFORDRAW": "FALSE",
        "JUMPERSVISIBLE": "TRUE",
        "MANHATTANLENGTH": "0",
    }
    raw_record["TOPLAYER_MRWIDTH"] = width
    for index in range(1, 31):
        raw_record[f"MIDLAYER{index}_MRWIDTH"] = width
    raw_record["BOTTOMLAYER_MRWIDTH"] = width

    return AltiumPcbNet(
        name=name,
        unique_id=_deterministic_net_unique_id(name),
        color=65535,
        visible=True,
        override_color=False,
        keepout=False,
        locked=False,
        user_routed=True,
        loop_removal=True,
        jumpers_visible=True,
        polygon_outline=False,
        layer="TOP",
        union_index=0,
        _raw_record=raw_record,
    )
