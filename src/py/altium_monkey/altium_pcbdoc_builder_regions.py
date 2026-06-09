"""
Region helpers for `PcbDocBuilder`.

Board regions are authored as a paired logical `REGION` plus rendered
`ShapeBasedRegion` geometry, matching the synthesized board fixtures.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_record_pcb__region import AltiumPcbRegion, RegionVertex
from .altium_pcb_enums import (
    PcbRegionKind,
    pcb_region_kind_from_native_kind,
    pcb_region_kind_to_native_kind,
)
from .altium_record_pcb__shapebased_region import (
    AltiumPcbShapeBasedRegion,
    PcbExtendedVertex,
    PcbSimpleVertex,
)
from .altium_record_types import PcbLayer


def parse_region_stream(data: bytes) -> tuple[AltiumPcbRegion, ...]:
    """
    Parse `Regions6/Data` into REGION objects.
    """
    regions: list[AltiumPcbRegion] = []
    offset = 0
    while offset < len(data):
        region = AltiumPcbRegion()
        consumed = region.parse_from_binary(data, offset)
        regions.append(region)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Regions6/Data")
    return tuple(regions)


def build_region_stream(regions: Sequence[AltiumPcbRegion]) -> bytes:
    """
    Serialize REGION objects back into `Regions6/Data`.
    """
    return b"".join(region.serialize_to_binary() for region in regions)


def parse_shapebased_region_stream(
    data: bytes,
) -> tuple[AltiumPcbShapeBasedRegion, ...]:
    """
    Parse `ShapeBasedRegions6/Data` into rendered region objects.
    """
    regions: list[AltiumPcbShapeBasedRegion] = []
    offset = 0
    while offset < len(data):
        region = AltiumPcbShapeBasedRegion()
        consumed = region.parse_from_binary(data, offset)
        regions.append(region)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in ShapeBasedRegions6/Data")
    return tuple(regions)


def build_shapebased_region_stream(
    regions: Sequence[AltiumPcbShapeBasedRegion],
) -> bytes:
    """
    Serialize rendered region objects back into `ShapeBasedRegions6/Data`.
    """
    return b"".join(region.serialize_to_binary() for region in regions)


def build_authored_region_pair(
    *,
    outline_points_mils: list[tuple[float, float]],
    layer: int | PcbLayer = PcbLayer.TOP,
    hole_points_mils: list[list[tuple[float, float]]] | None = None,
    outline_vertices: Sequence[PcbExtendedVertex] | None = None,
    net_index: int | None = None,
    polygon_index: int = 0xFFFF,
    subpoly_index: int = -1,
    union_index: int = 0,
    region_kind: PcbRegionKind | int = PcbRegionKind.COPPER,
    is_board_cutout: bool = False,
    is_shapebased: bool = False,
    is_keepout: bool = False,
    keepout_restrictions: int = 0,
    cavity_height_mils: float = 0.0,
    v7_layer: str | None = None,
) -> tuple[AltiumPcbRegion, AltiumPcbShapeBasedRegion]:
    """
    Create a logical REGION plus rendered ShapeBasedRegion from first principles.
    """
    if len(outline_points_mils) < 3:
        raise ValueError("Region outline requires at least 3 points")

    layer_id = int(layer)
    holes = hole_points_mils or []
    parsed_region_kind = (
        region_kind
        if isinstance(region_kind, PcbRegionKind)
        else pcb_region_kind_from_native_kind(
            int(region_kind),
            is_board_cutout=is_board_cutout,
        )
    )
    effective_board_cutout = (
        bool(is_board_cutout) or parsed_region_kind == PcbRegionKind.BOARD_CUTOUT
    )
    properties_kind = pcb_region_kind_to_native_kind(
        parsed_region_kind,
        is_board_cutout=effective_board_cutout,
    )
    v7_layer_text = _v7_layer_text(layer_id, v7_layer)
    shapebased_text = "TRUE" if is_shapebased else "FALSE"
    cavity_height_internal = int(round(float(cavity_height_mils) * 10000.0))
    cavity_height_text = _format_mil_value(float(cavity_height_mils))
    shape_outline = list(outline_vertices or [])
    if not shape_outline:
        for x_mil, y_mil in outline_points_mils:
            vertex = PcbExtendedVertex()
            vertex.is_round = False
            vertex.x = int(round(x_mil * 10000.0))
            vertex.y = int(round(y_mil * 10000.0))
            vertex.center_x = 0
            vertex.center_y = 0
            vertex.radius = 0
            vertex.start_angle = 0.0
            vertex.end_angle = 0.0
            shape_outline.append(vertex)

    region = AltiumPcbRegion()
    region.layer = layer_id
    region.net_index = net_index
    region.component_index = None
    region.polygon_index = int(polygon_index)
    region.is_locked = False
    region.is_keepout = bool(is_keepout)
    region.is_polygon_outline = False
    region.kind = properties_kind
    region.is_board_cutout = effective_board_cutout
    region.is_shapebased = bool(is_shapebased)
    region.keepout_restrictions = int(keepout_restrictions)
    region.subpoly_index = int(subpoly_index)
    region.cavity_height = cavity_height_internal
    region.union_index = int(union_index)
    region._flags1_raw = 0x0C
    region._skip_bytes_9 = b"\xff\xff\xff\xff\x00"
    region._skip_bytes_16 = b"\x00\x00"
    region.properties = {
        "V7_LAYER": v7_layer_text,
        "NAME": "",
        "KIND": str(properties_kind),
        "SUBPOLYINDEX": str(int(subpoly_index)),
        "UNIONINDEX": str(int(union_index)),
        "ARCRESOLUTION": "0.5mil",
        "ISSHAPEBASED": shapebased_text,
        "CAVITYHEIGHT": cavity_height_text,
    }
    region.outline_vertices = [
        RegionVertex(
            x_raw=float(int(round(x_mil * 10000.0))),
            y_raw=float(int(round(y_mil * 10000.0))),
        )
        for x_mil, y_mil in outline_points_mils
    ]
    region.hole_vertices = [
        [
            RegionVertex(
                x_raw=float(int(round(x_mil * 10000.0))),
                y_raw=float(int(round(y_mil * 10000.0))),
            )
            for x_mil, y_mil in hole
        ]
        for hole in holes
    ]
    region.outline_vertex_count = len(region.outline_vertices)
    region.hole_count = len(region.hole_vertices)

    shape_region = AltiumPcbShapeBasedRegion()
    shape_region.layer = layer_id
    shape_region.is_locked = False
    shape_region.is_keepout = bool(is_keepout)
    shape_region.net_index = 0xFFFF if net_index is None else int(net_index)
    shape_region.polygon_index = int(polygon_index)
    shape_region.component_index = 0xFFFF
    shape_region.kind = (
        PcbRegionKind.BOARD_CUTOUT if effective_board_cutout else parsed_region_kind
    )
    shape_region.is_board_cutout = effective_board_cutout
    shape_region.is_shapebased = bool(is_shapebased)
    shape_region.subpoly_index = int(subpoly_index)
    shape_region.keepout_restrictions = 31
    shape_region.union_index = int(union_index)
    shape_region._flags1_raw = 0x0C
    shape_region._header_skip5 = b"\xff\xff\xff\xff\x00"
    shape_region._header_skip2 = b"\x00\x00"
    shape_region._props_has_trailing_null = False
    shape_region.properties = {
        "V7_LAYER": v7_layer_text,
        "NAME": " ",
        "KIND": str(properties_kind),
        "SUBPOLYINDEX": str(int(subpoly_index)),
        "UNIONINDEX": str(int(union_index)),
        "ARCRESOLUTION": "0.5mil",
        "ISSHAPEBASED": shapebased_text,
        "CAVITYHEIGHT": cavity_height_text,
    }
    outline: list[PcbExtendedVertex] = list(shape_outline)
    if outline:
        closing = PcbExtendedVertex()
        first = outline[0]
        closing.is_round = first.is_round
        closing.x = first.x
        closing.y = first.y
        closing.center_x = first.center_x
        closing.center_y = first.center_y
        closing.radius = first.radius
        closing.start_angle = first.start_angle
        closing.end_angle = first.end_angle
        outline.append(closing)
    shape_region.outline = outline
    shape_region.holes = []
    for hole in holes:
        hole_vertices: list[PcbSimpleVertex] = []
        for x_mil, y_mil in hole:
            vertex = PcbSimpleVertex()
            vertex.x = float(int(round(x_mil * 10000.0)))
            vertex.y = float(int(round(y_mil * 10000.0)))
            hole_vertices.append(vertex)
        shape_region.holes.append(hole_vertices)
    shape_region.hole_count = len(shape_region.holes)

    return region, shape_region


def _v7_layer_text(layer_id: int, override: str | None) -> str:
    if override is not None:
        return str(override)
    try:
        return PcbLayer(layer_id).to_json_name()
    except ValueError:
        return str(int(layer_id))


def make_authored_region_guid(region: AltiumPcbRegion, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored logical REGION.
    """
    seed = (
        "pcbdoc-builder-region|"
        f"{ordinal}|{region.layer}|"
        f"{tuple((v.x_raw, v.y_raw) for v in region.outline_vertices)}|"
        f"{tuple(tuple((v.x_raw, v.y_raw) for v in hole) for hole in region.hole_vertices)}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)


def make_authored_shapebased_region_guid(
    region: AltiumPcbShapeBasedRegion,
    ordinal: int,
) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored rendered region.
    """
    seed = (
        "pcbdoc-builder-shapebased-region|"
        f"{ordinal}|{region.layer}|"
        f"{tuple((v.x, v.y, v._is_round_raw) for v in region.outline)}|"
        f"{tuple(tuple((v.x, v.y) for v in hole) for hole in region.holes)}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
