"""
Component-authoring helpers for `PcbDocBuilder`.

This module authors board-level component records separately from the child
primitives they own. The builder currently covers the minimal component record
surface needed for placed-footprint workflows:

- one `Components6/Data` record
- child primitive ownership through each primitive's `component_index`
- one board `PrimitiveGuids/Data` record for the component object itself

Authored component `UNIQUEID` values are stable deterministic 8-character
tokens derived from component placement inputs.
"""

from __future__ import annotations

import struct
import uuid
from collections import OrderedDict
from typing import Sequence

from .altium_component_kind import parse_component_kind
from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_pcb_component import AltiumPcbComponent
from .altium_record_types import PcbLayer
from .altium_utilities import (
    create_stream_from_records,
    decode_byte_array,
    parse_byte_record,
)


def _format_legacy_scientific(value: float) -> str:
    text = f"{float(value): .14E}"
    mantissa, exponent = text.split("E", 1)
    return f"{mantissa}E{int(exponent):+05d}"


def _normalize_component_layer(layer: str | PcbLayer | int) -> str:
    if isinstance(layer, PcbLayer):
        if layer == PcbLayer.TOP:
            return "TOP"
        if layer == PcbLayer.BOTTOM:
            return "BOTTOM"
    if isinstance(layer, int):
        if layer == PcbLayer.TOP.value:
            return "TOP"
        if layer == PcbLayer.BOTTOM.value:
            return "BOTTOM"
    text = str(layer).strip().upper()
    if "BOTTOM" in text:
        return "BOTTOM"
    if "TOP" in text:
        return "TOP"
    raise ValueError(f"Unsupported component layer: {layer!r}")


def _deterministic_component_unique_id(
    designator: str,
    footprint: str,
    x_mils: float,
    y_mils: float,
    layer: str,
    rotation_degrees: float,
) -> str:
    seed = (
        f"pcbdoc-builder-component|{designator}|{footprint}|"
        f"{x_mils:g}|{y_mils:g}|{layer}|{rotation_degrees:g}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:8].upper()


def parse_component_stream(data: bytes) -> tuple[AltiumPcbComponent, ...]:
    """
    Parse `Components6/Data` into component records.
    """
    components: list[AltiumPcbComponent] = []
    offset = 0
    while offset < len(data):
        if len(data) < offset + 4:
            raise ValueError("Invalid Components6/Data stream")
        record_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if len(data) < offset + record_len:
            raise ValueError("Invalid Components6/Data stream")
        raw_record = data[offset : offset + record_len]
        offset += record_len
        fields: OrderedDict[str, str] = OrderedDict()
        for part in parse_byte_record(raw_record):
            decoded = decode_byte_array(part)
            if "=" not in decoded:
                continue
            key, value = decoded.split("=", 1)
            fields[key] = value
        components.append(
            AltiumPcbComponent(
                designator=fields.get("SOURCEDESIGNATOR", ""),
                footprint=fields.get("PATTERN", ""),
                layer=fields.get("LAYER", ""),
                x=fields.get("X", ""),
                y=fields.get("Y", ""),
                rotation=fields.get("ROTATION", ""),
                unique_id=fields.get("UNIQUEID", ""),
                description=fields.get("SOURCEDESCRIPTION", ""),
                raw_record=dict(fields),
                component_kind=parse_component_kind(fields),
            )
        )
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Components6/Data")
    return tuple(components)


def build_component_stream(components: Sequence[AltiumPcbComponent]) -> bytes:
    """
    Serialize component records back into `Components6/Data`.
    """
    return create_stream_from_records(
        [component.raw_record for component in components]
    )


def build_authored_component(
    *,
    designator: str,
    footprint: str,
    position_mils: tuple[float, float],
    layer: str | PcbLayer | int = "TOP",
    rotation_degrees: float = 0.0,
    source_footprint_library: str = "",
    name_on: bool = True,
    comment_on: bool = False,
    name_auto_position: int = 1,
    comment_auto_position: int = 3,
    description: str = "",
    unique_id: str | None = None,
) -> AltiumPcbComponent:
    """
    Create the smallest useful authored component record.
    """
    layer_token = _normalize_component_layer(layer)
    x_mils, y_mils = position_mils
    unique_id = unique_id or _deterministic_component_unique_id(
        designator=designator,
        footprint=footprint,
        x_mils=x_mils,
        y_mils=y_mils,
        layer=layer_token,
        rotation_degrees=rotation_degrees,
    )
    raw_record: OrderedDict[str, str] = OrderedDict(
        (
            ("SELECTION", "FALSE"),
            ("LAYER", layer_token),
            ("LOCKED", "FALSE"),
            ("POLYGONOUTLINE", "FALSE"),
            ("USERROUTED", "TRUE"),
            ("KEEPOUT", "FALSE"),
            ("PRIMITIVELOCK", "TRUE"),
            ("X", _format_mil_value(x_mils)),
            ("Y", _format_mil_value(y_mils)),
            ("PATTERN", footprint),
            ("NAMEON", "TRUE" if name_on else "FALSE"),
            ("COMMENTON", "TRUE" if comment_on else "FALSE"),
            ("GROUPNUM", "0"),
            ("COUNT", "0"),
            ("ROTATION", _format_legacy_scientific(rotation_degrees)),
            ("NAMEAUTOPOSITION", str(int(name_auto_position))),
            ("COMMENTAUTOPOSITION", str(int(comment_auto_position))),
            ("UNIONINDEX", "0"),
            ("SOURCEFOOTPRINTLIBRARY", source_footprint_library),
            ("UNIQUEID", unique_id),
            ("JUMPERSVISIBLE", "TRUE"),
        )
    )
    if description:
        raw_record["SOURCEDESCRIPTION"] = description
    raw_record["SOURCEDESIGNATOR"] = designator

    return AltiumPcbComponent(
        designator=designator,
        footprint=footprint,
        layer=layer_token,
        x=raw_record["X"],
        y=raw_record["Y"],
        rotation=raw_record["ROTATION"],
        unique_id=unique_id,
        description=description,
        raw_record=dict(raw_record),
    )


def make_authored_component_guid(
    component: AltiumPcbComponent, ordinal: int
) -> uuid.UUID:
    seed = (
        f"pcbdoc-builder-component-guid|{component.unique_id}|"
        f"{component.footprint}|{component.x}|{component.y}|{ordinal}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
