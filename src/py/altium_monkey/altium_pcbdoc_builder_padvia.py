"""
Pad/Via helpers for `PcbDocBuilder`.

These are the heaviest primitive records on the board side, so they live in a
separate helper module from tracks/arcs/fills and from text/widestring logic.
"""

from __future__ import annotations

import struct
import uuid
from typing import Sequence

from .altium_pcb_enums import PadShape
from .altium_pcb_pad_authoring import (
    SLOT_HOLE_SHAPE,
    apply_authored_pad_shape,
    validate_non_negative,
)
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__via import AltiumPcbVia
from .altium_record_types import PcbLayer
from .altium_resolved_layer_stack import legacy_layer_to_v7_save_id

_PAD_SUBRECORD2_DEFAULT = b"\x00"
_PAD_SUBRECORD3_DEFAULT = b"\x04|&|0"
_PAD_SUBRECORD4_DEFAULT = b"\x00"


def parse_pad_stream(data: bytes) -> tuple[AltiumPcbPad, ...]:
    """
    Parse `Pads6/Data` into PAD objects.
    """
    pads: list[AltiumPcbPad] = []
    offset = 0
    while offset < len(data):
        pad = AltiumPcbPad()
        consumed = pad.parse_from_binary(data, offset)
        pads.append(pad)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Pads6/Data")
    return tuple(pads)


def build_pad_stream(pads: Sequence[AltiumPcbPad]) -> bytes:
    """
    Serialize PAD objects back into `Pads6/Data`.
    """
    return b"".join(pad.serialize_to_binary() for pad in pads)


def parse_via_stream(data: bytes) -> tuple[AltiumPcbVia, ...]:
    """
    Parse `Vias6/Data` into VIA objects.
    """
    vias: list[AltiumPcbVia] = []
    offset = 0
    while offset < len(data):
        via = AltiumPcbVia()
        consumed = via.parse_from_binary(data, offset)
        vias.append(via)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Vias6/Data")
    return tuple(vias)


def build_via_stream(vias: Sequence[AltiumPcbVia]) -> bytes:
    """
    Serialize VIA objects back into `Vias6/Data`.
    """
    return b"".join(via.serialize_to_binary() for via in vias)


def build_authored_pad(
    *,
    designator: str,
    position_mils: tuple[float, float],
    width_mils: float,
    height_mils: float,
    layer: int | PcbLayer = PcbLayer.TOP,
    shape: int | PadShape = PadShape.RECTANGLE,
    rotation_degrees: float = 0.0,
    hole_size_mils: float = 0.0,
    plated: bool | None = None,
    corner_radius_percent: int | None = None,
    slot_length_mils: float = 0.0,
    slot_rotation_degrees: float = 0.0,
    solder_mask_expansion_mils: float | None = None,
    paste_mask_expansion_mils: float | None = None,
) -> AltiumPcbPad:
    """
    Create a modern authored PAD record from first principles.
    """
    pad = AltiumPcbPad()
    layer_id = int(layer)
    validate_non_negative(width_mils, "width_mils")
    validate_non_negative(height_mils, "height_mils")
    validate_non_negative(hole_size_mils, "hole_size_mils")
    validate_non_negative(slot_length_mils, "slot_length_mils")
    width_iu = pad._to_internal_units(width_mils)
    height_iu = pad._to_internal_units(height_mils)
    hole_iu = pad._to_internal_units(hole_size_mils)
    slot_iu = pad._to_internal_units(slot_length_mils)
    if slot_iu > 0 and hole_iu <= 0:
        raise ValueError("slot_length_mils requires a positive hole_size_mils")
    if slot_iu > 0 and slot_iu < hole_iu:
        raise ValueError(
            "slot_length_mils must be greater than or equal to hole_size_mils"
        )

    pad.designator = str(designator)
    pad.layer = layer_id
    pad.x = pad._to_internal_units(position_mils[0])
    pad.y = pad._to_internal_units(position_mils[1])
    pad.width = width_iu
    pad.height = height_iu
    pad.top_width = width_iu
    pad.top_height = height_iu
    pad.mid_width = width_iu
    pad.mid_height = height_iu
    pad.bot_width = width_iu
    pad.bot_height = height_iu
    apply_authored_pad_shape(
        pad,
        shape=shape,
        width_iu=width_iu,
        height_iu=height_iu,
        corner_radius_percent=corner_radius_percent,
    )
    pad.rotation = float(rotation_degrees)
    pad.layer_v7_save_id = legacy_layer_to_v7_save_id(layer_id)
    pad.hole_size = hole_iu
    pad.is_plated = bool(hole_iu > 0) if plated is None else bool(plated)
    pad.net_index = None
    pad.component_index = None
    pad.polygon_index = 0xFFFF
    pad.union_index = 0xFFFFFFFF
    pad.pad_mode = 0
    pad.user_routed = True
    pad._flags = 0x000C
    pad._subrecord2_data = _PAD_SUBRECORD2_DEFAULT
    pad._subrecord3_data = _PAD_SUBRECORD3_DEFAULT
    pad._subrecord4_data = _PAD_SUBRECORD4_DEFAULT
    if slot_iu > 0:
        pad.hole_shape = SLOT_HOLE_SHAPE
        pad.slot_size = slot_iu
        pad.slot_rotation = float(slot_rotation_degrees)
    if solder_mask_expansion_mils is not None:
        pad.soldermask_expansion_mode = 2
        pad.soldermask_expansion_manual = pad._to_internal_units(
            solder_mask_expansion_mils
        )
        pad._has_mask_expansion = True
    if paste_mask_expansion_mils is not None:
        pad.pastemask_expansion_mode = 2
        pad.pastemask_expansion_manual = pad._to_internal_units(
            paste_mask_expansion_mils
        )
        pad._has_mask_expansion = True
    return pad


def make_authored_pad_guid(pad: AltiumPcbPad, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored PAD.
    """
    seed = (
        "pcbdoc-builder-pad|"
        f"{ordinal}|{pad.designator}|{pad.layer}|{pad.x}|{pad.y}|"
        f"{pad.top_width}|{pad.top_height}|{pad.hole_size}|{pad.rotation}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def make_authored_pad_unique_id(pad: AltiumPcbPad, ordinal: int) -> str:
    """
    Generate a deterministic 8-char board pad unique-id token.
    """
    seed = (
        "pcbdoc-builder-pad-uid|"
        f"{ordinal}|{pad.designator}|{pad.layer}|{pad.x}|{pad.y}|"
        f"{pad.top_width}|{pad.top_height}|{pad.hole_size}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:8].upper()


def build_pad_unique_id_info_record(primitive_index: int, unique_id: str) -> bytes:
    """
    Serialize one board-side pad UniqueIDPrimitiveInformation record.
    """
    body = (
        f"|PRIMITIVEINDEX={int(primitive_index)}"
        f"|PRIMITIVEOBJECTID=Pad"
        f"|UNIQUEID={str(unique_id)}\x00"
    ).encode("ascii")
    return struct.pack("<I", len(body)) + body


def build_authored_via(
    *,
    position_mils: tuple[float, float],
    diameter_mils: float,
    hole_size_mils: float,
    layer_start: int | PcbLayer = PcbLayer.TOP,
    layer_end: int | PcbLayer = PcbLayer.BOTTOM,
) -> AltiumPcbVia:
    """
    Create a modern authored VIA record from first principles.
    """
    via = AltiumPcbVia()
    via.layer = int(PcbLayer.MULTI_LAYER)
    via.net_index = None
    via.component_index = None
    via.polygon_index = 0xFFFF
    via.x = via._to_internal_units(position_mils[0])
    via.y = via._to_internal_units(position_mils[1])
    via.diameter = via._to_internal_units(diameter_mils)
    via.hole_size = via._to_internal_units(hole_size_mils)
    via.layer_start = int(layer_start)
    via.layer_end = int(layer_end)
    via.via_mode = 0
    via.union_index = 0
    via.diameter_by_layer = [0] * 32
    for layer_id in range(
        min(via.layer_start, via.layer_end), max(via.layer_start, via.layer_end) + 1
    ):
        if 1 <= layer_id <= 32:
            via.diameter_by_layer[layer_id - 1] = via.diameter
    return via


def make_authored_via_guid(via: AltiumPcbVia, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored VIA.
    """
    seed = (
        "pcbdoc-builder-via|"
        f"{ordinal}|{via.x}|{via.y}|{via.diameter}|{via.hole_size}|"
        f"{via.layer_start}|{via.layer_end}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
