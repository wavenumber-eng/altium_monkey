"""
PrimitiveParameters authoring helpers for `PcbDocBuilder`.

The parser already treats `PrimitiveParameters/Data` as component-owned cached
metadata keyed by component `UNIQUEID`. The builder should use the same model.

Current corpus-backed understanding:

- `PrimitiveParameters/Data` is a flat record list.
- For each component with cached parameters, Altium emits two leading records:
  1. `PRIMITIVEID=<UID> | ID=Component#<n> | APPURTENANCE=System | VARIANTGUID=System | COUNT=0`
  2. `PRIMITIVEID=<UID> | ID=Component#<n> | VARIANTGUID= | COUNT=<param_count>`
- Then follow `param_count` records with `NAME` / `VALUE` pairs.
- `PrimitiveParameters/Header` appears to be `2 * component_group_count`, i.e.
  the count of those two `PRIMITIVEID` records per included component.

What remains partially inferred:

- The exact meaning of `APPURTENANCE` / `VARIANTGUID` beyond the common corpus
  values above.
- Additional per-parameter flags such as `ISIMPORTED` and Unicode sideband
  fields. The current builder writes a minimal `NAME` / `VALUE` contract plus
  `ISIMPORTED=FALSE` for authored parameters.
"""

from __future__ import annotations

import struct
from collections import OrderedDict
from typing import Sequence

from .altium_pcb_component import AltiumPcbComponent
from .altium_utilities import create_stream_from_records, decode_byte_array, parse_byte_record


def parse_component_parameter_stream(data: bytes) -> dict[str, dict[str, str]]:
    """
    Parse `PrimitiveParameters/Data` into `UNIQUEID -> {name: value}`.
    """
    parameter_map: dict[str, dict[str, str]] = {}
    offset = 0
    current_uid: str | None = None
    while offset < len(data):
        if len(data) < offset + 4:
            raise ValueError("Invalid PrimitiveParameters/Data stream")
        record_len = struct.unpack("<I", data[offset:offset + 4])[0]
        offset += 4
        if len(data) < offset + record_len:
            raise ValueError("Invalid PrimitiveParameters/Data stream")
        raw_record = data[offset:offset + record_len]
        offset += record_len
        fields: OrderedDict[str, str] = OrderedDict()
        for part in parse_byte_record(raw_record):
            decoded = decode_byte_array(part)
            if "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            fields[key] = value
        if "PRIMITIVEID" in fields:
            current_uid = fields["PRIMITIVEID"]
            if "COUNT" in fields and current_uid not in parameter_map:
                parameter_map[current_uid] = {}
            continue
        if current_uid and "NAME" in fields and "VALUE" in fields:
            parameter_map.setdefault(current_uid, {})[fields["NAME"]] = fields["VALUE"]
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in PrimitiveParameters/Data")
    return parameter_map


def build_component_parameter_stream(
    components: Sequence[AltiumPcbComponent],
) -> tuple[bytes, bytes]:
    """
    Build `PrimitiveParameters/Header` and `PrimitiveParameters/Data`.
    
    Only components with non-empty `parameters` dicts are included.
    """
    records: list[dict[str, str]] = []
    group_count = 0
    for index, component in enumerate(components):
        if not component.parameters:
            continue
        unique_id = str(component.unique_id or "").strip()
        if not unique_id:
            continue
        group_count += 1
        component_id = f"Component#{index}"
        param_items = list(component.parameters.items())
        records.append(
            OrderedDict(
                (
                    ("PRIMITIVEID", unique_id),
                    ("ID", component_id),
                    ("APPURTENANCE", "System"),
                    ("VARIANTGUID", "System"),
                    ("COUNT", "0"),
                )
            )
        )
        records.append(
            OrderedDict(
                (
                    ("PRIMITIVEID", unique_id),
                    ("ID", component_id),
                    ("VARIANTGUID", ""),
                    ("COUNT", str(len(param_items))),
                )
            )
        )
        for name, value in param_items:
            records.append(
                OrderedDict(
                    (
                        ("NAME", str(name)),
                        ("VALUE", str(value)),
                        ("ISIMPORTED", "FALSE"),
                    )
                )
            )

    header = struct.pack("<I", group_count * 2)
    data = create_stream_from_records(records)
    return header, data
