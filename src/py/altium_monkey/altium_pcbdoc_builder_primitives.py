"""
Primitive-authoring helpers for `PcbDocBuilder`.

This module keeps the main board-builder focused on container/state ownership
while gradually growing board-level primitive insertion support one primitive
family at a time.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from .altium_record_pcb__arc import AltiumPcbArc
from .altium_record_pcb__fill import AltiumPcbFill
from .altium_record_pcb__track import AltiumPcbTrack
from .altium_record_types import PcbLayer
from .altium_resolved_layer_stack import (
    _legacy_layer_id_from_token,
    legacy_layer_to_v7_save_id,
)

def parse_track_stream(data: bytes) -> tuple[AltiumPcbTrack, ...]:
    """
    Parse `Tracks6/Data` into TRACK objects.
    """
    tracks: list[AltiumPcbTrack] = []
    offset = 0
    while offset < len(data):
        track = AltiumPcbTrack()
        consumed = track.parse_from_binary(data, offset)
        tracks.append(track)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Tracks6/Data")
    return tuple(tracks)


def build_track_stream(tracks: Sequence[AltiumPcbTrack]) -> bytes:
    """
    Serialize TRACK objects back into `Tracks6/Data`.
    """
    return b"".join(track.serialize_to_binary() for track in tracks)


def parse_arc_stream(data: bytes) -> tuple[AltiumPcbArc, ...]:
    """
    Parse `Arcs6/Data` into ARC objects.
    """
    arcs: list[AltiumPcbArc] = []
    offset = 0
    while offset < len(data):
        arc = AltiumPcbArc()
        consumed = arc.parse_from_binary(data, offset)
        arcs.append(arc)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Arcs6/Data")
    return tuple(arcs)


def build_arc_stream(arcs: Sequence[AltiumPcbArc]) -> bytes:
    """
    Serialize ARC objects back into `Arcs6/Data`.
    """
    return b"".join(arc.serialize_to_binary() for arc in arcs)


def parse_fill_stream(data: bytes) -> tuple[AltiumPcbFill, ...]:
    """
    Parse `Fills6/Data` into FILL objects.
    """
    fills: list[AltiumPcbFill] = []
    offset = 0
    while offset < len(data):
        fill = AltiumPcbFill()
        consumed = fill.parse_from_binary(data, offset)
        fills.append(fill)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Fills6/Data")
    return tuple(fills)


def build_fill_stream(fills: Sequence[AltiumPcbFill]) -> bytes:
    """
    Serialize FILL objects back into `Fills6/Data`.
    """
    return b"".join(fill.serialize_to_binary() for fill in fills)


def coerce_legacy_layer_id(layer: int | str | PcbLayer) -> int:
    """
    Resolve a public builder layer input into the legacy PCB layer ID.
    """
    if isinstance(layer, PcbLayer):
        return int(layer.value)
    if isinstance(layer, int):
        return int(layer)
    token = str(layer or "").strip()
    legacy_id = _legacy_layer_id_from_token(token)
    if legacy_id is None:
        normalized = "".join(ch for ch in token.upper() if ch.isalnum())
        if normalized.endswith("LAYER"):
            legacy_id = _legacy_layer_id_from_token(normalized[:-5])
    if legacy_id is None:
        raise ValueError(f"Unsupported PCB layer token for authored primitive: {layer!r}")
    return legacy_id


def build_authored_track(
    *,
    start_mils: tuple[float, float],
    end_mils: tuple[float, float],
    width_mils: float,
    layer: int | str | PcbLayer = PcbLayer.TOP,
) -> AltiumPcbTrack:
    """
    Create a modern authored TRACK record from first principles.
    """
    legacy_layer = coerce_legacy_layer_id(layer)
    track = AltiumPcbTrack()
    track.layer = legacy_layer
    track.start_x = track._to_internal_units(start_mils[0])
    track.start_y = track._to_internal_units(start_mils[1])
    track.end_x = track._to_internal_units(end_mils[0])
    track.end_y = track._to_internal_units(end_mils[1])
    track.width = track._to_internal_units(width_mils)
    track.v7_layer_id = legacy_layer_to_v7_save_id(legacy_layer)
    track._original_content_len = 49
    return track


def make_authored_track_guid(track: AltiumPcbTrack, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored TRACK.
    """
    seed = (
        "pcbdoc-builder-track|"
        f"{ordinal}|{track.layer}|{track.start_x}|{track.start_y}|"
        f"{track.end_x}|{track.end_y}|{track.width}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def build_authored_arc(
    *,
    center_mils: tuple[float, float],
    radius_mils: float,
    start_angle: float,
    end_angle: float,
    width_mils: float,
    layer: int | str | PcbLayer = PcbLayer.TOP,
) -> AltiumPcbArc:
    """
    Create a modern authored ARC record from first principles.
    """
    legacy_layer = coerce_legacy_layer_id(layer)
    arc = AltiumPcbArc()
    arc.layer = legacy_layer
    arc.center_x = arc._to_internal_units(center_mils[0])
    arc.center_y = arc._to_internal_units(center_mils[1])
    arc.radius = arc._to_internal_units(radius_mils)
    arc.start_angle = float(start_angle)
    arc.end_angle = float(end_angle)
    arc.width = arc._to_internal_units(width_mils)
    arc.v7_layer_id = legacy_layer_to_v7_save_id(legacy_layer)
    arc._original_content_len = 60
    return arc


def make_authored_arc_guid(arc: AltiumPcbArc, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored ARC.
    """
    seed = (
        "pcbdoc-builder-arc|"
        f"{ordinal}|{arc.layer}|{arc.center_x}|{arc.center_y}|{arc.radius}|"
        f"{arc.start_angle}|{arc.end_angle}|{arc.width}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def build_authored_fill(
    *,
    pos1_mils: tuple[float, float],
    pos2_mils: tuple[float, float],
    rotation_degrees: float = 0.0,
    layer: int | str | PcbLayer = PcbLayer.TOP,
) -> AltiumPcbFill:
    """
    Create a modern authored FILL record from first principles.
    """
    legacy_layer = coerce_legacy_layer_id(layer)
    fill = AltiumPcbFill()
    fill.layer = legacy_layer
    fill.pos1_x = fill._to_internal_units(pos1_mils[0])
    fill.pos1_y = fill._to_internal_units(pos1_mils[1])
    fill.pos2_x = fill._to_internal_units(pos2_mils[0])
    fill.pos2_y = fill._to_internal_units(pos2_mils[1])
    fill.rotation = float(rotation_degrees)
    fill.net_index = None
    fill.component_index = None
    fill.polygon_index = 0xFFFF
    fill.union_index = 0xFFFFFFFF
    fill.user_routed = True
    fill.is_keepout = False
    fill.is_polygon_outline = False
    fill.solder_mask_expansion = 0
    fill.paste_mask_expansion = 0
    fill.keepout_restrictions = 0
    fill.v7_layer_id = legacy_layer_to_v7_save_id(legacy_layer)
    fill._original_content_len = 50
    return fill


def make_authored_fill_guid(fill: AltiumPcbFill, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored FILL.
    """
    seed = (
        "pcbdoc-builder-fill|"
        f"{ordinal}|{fill.layer}|{fill.pos1_x}|{fill.pos1_y}|"
        f"{fill.pos2_x}|{fill.pos2_y}|{fill.rotation}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
