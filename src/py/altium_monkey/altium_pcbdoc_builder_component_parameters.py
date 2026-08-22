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
from collections.abc import Mapping, Sequence

from .altium_pcb_component import AltiumPcbComponent
from .altium_pcb_property_helpers import (
    decode_dxp_parameter_value,
    encode_dxp_parameter_value,
    encode_pcb_unicode_sideband,
    resolve_pcb_unicode_field,
)
from .altium_utilities import (
    create_stream_from_records,
    decode_byte_array,
    parse_byte_record,
)


def parse_component_parameter_stream(data: bytes) -> dict[str, dict[str, str]]:
    """
    Parse `PrimitiveParameters/Data` into `UNIQUEID -> {name: value}`.
    """
    parameter_map: dict[str, dict[str, str]] = {}
    offset = 0
    current_uid: str | None = None
    while offset < len(data):
        raw_record, offset = _read_length_prefixed_record(data, offset)
        fields = _parse_parameter_record_fields(raw_record)
        if "PRIMITIVEID" in fields:
            current_uid = fields["PRIMITIVEID"]
            if "COUNT" in fields and current_uid not in parameter_map:
                parameter_map[current_uid] = {}
            continue
        if current_uid and "NAME" in fields and "VALUE" in fields:
            resolved_name, resolved_value = _resolve_parameter_record_text(fields)
            parameter_map.setdefault(current_uid, {})[resolved_name] = resolved_value
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in PrimitiveParameters/Data")
    return parameter_map


def _read_length_prefixed_record(data: bytes, offset: int) -> tuple[bytes, int]:
    """Read one `[uint32 len][payload]` record; returns (payload, new offset)."""
    if len(data) < offset + 4:
        raise ValueError("Invalid PrimitiveParameters/Data stream")
    record_len = struct.unpack("<I", data[offset : offset + 4])[0]
    offset += 4
    if len(data) < offset + record_len:
        raise ValueError("Invalid PrimitiveParameters/Data stream")
    return data[offset : offset + record_len], offset + record_len


def _parse_parameter_record_fields(raw_record: bytes) -> OrderedDict[str, str]:
    """Split a pipe-delimited record payload into ordered key/value fields."""
    fields: OrderedDict[str, str] = OrderedDict()
    for part in parse_byte_record(raw_record):
        decoded = decode_byte_array(part)
        if "=" not in decoded:
            continue
        key, value = decoded.split("=", 1)
        fields[key] = value
    return fields


def _resolve_parameter_record_text(fields: Mapping[str, str]) -> tuple[str, str]:
    """
    Resolve a parameter record's authoritative name/value text.

    UNICODE__ sidebands are the authoritative Unicode text; the plain-field
    bytes depend on the writing machine's ANSI code page.
    """
    unicode_name = resolve_pcb_unicode_field(fields, "NAME")
    unicode_value = resolve_pcb_unicode_field(fields, "VALUE")
    resolved_name = unicode_name if unicode_name is not None else fields["NAME"]
    resolved_value = (
        unicode_value
        if unicode_value is not None
        else decode_dxp_parameter_value(fields["VALUE"])
    )
    return resolved_name, resolved_value


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
            records.append(_build_parameter_record(name, value))

    header = struct.pack("<I", group_count * 2)
    # PCB streams use UNICODE__ sidebands, not the schematic %UTF8% sidecars.
    data = create_stream_from_records(records, utf8_sidecars=False)
    return header, data


def _build_parameter_record(name: object, value: object) -> OrderedDict[str, str]:
    """
    Build one NAME/VALUE parameter record.

    Altium marks non-ASCII parameters with a UNICODE=EXISTS flag and
    authoritative UNICODE__ code-unit sidebands; the plain fields remain the
    code-page fallback.
    """
    name_text = str(name)
    value_text = "" if value is None else str(value)
    record: OrderedDict[str, str] = OrderedDict()
    needs_unicode = any(ord(ch) > 0x7F for ch in name_text + value_text)
    if needs_unicode:
        record["UNICODE"] = "EXISTS"
    record["NAME"] = name_text
    record["VALUE"] = encode_dxp_parameter_value(value)
    record["ISIMPORTED"] = "FALSE"
    if any(ord(ch) > 0x7F for ch in name_text):
        record["UNICODE__NAME"] = encode_pcb_unicode_sideband(name_text)
    if any(ord(ch) > 0x7F for ch in value_text):
        record["UNICODE__VALUE"] = encode_pcb_unicode_sideband(value_text)
    return record
