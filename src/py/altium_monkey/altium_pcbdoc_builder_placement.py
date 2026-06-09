"""
Library-footprint placement helpers for `PcbDocBuilder`.

This module composes the existing board-primitive insertion APIs into the first
builder-owned footprint-placement path:

- create a board component record
- transform footprint-local primitives into board-absolute space
- attach child primitives to that component

The goal of this first slice is common library footprints made of:
- pads
- custom pads
- tracks
- arcs
- fills
- texts
- vias
- logical regions

What is intentionally deferred:
- polygon pour definition/pour-state handling
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Sequence, TypedDict, cast

from .altium_pcb_embedded_model_compose import (
    collect_pcblib_embedded_model_entries,
    resolve_footprint_body_model_entries,
)
from .altium_pcbdoc_builder_components import _normalize_component_layer
from .altium_pcbdoc_layers import _build_component_layer_flip_map, _flip_layer
from .altium_pcb_enums import PcbBodyProjection
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_pcblib import AltiumPcbFootprint, AltiumPcbLib
    from .altium_pcbdoc_builder import PcbDocBuilder
    from .altium_record_pcb__component_body import AltiumPcbComponentBody
    from .altium_record_pcb__model import AltiumPcbModel
    from .altium_record_pcb__pad import AltiumPcbPad


class _ComponentTextSpec(TypedDict):
    text: str
    position_mils: tuple[float, float]
    height_mils: float
    layer: PcbLayer
    rotation_degrees: float
    is_designator: bool
    is_comment: bool
    is_mirrored: bool


def _forward_transform_point(
    lx: float,
    ly: float,
    cx: float,
    cy: float,
    rotation_deg: float,
    flipped: bool,
) -> tuple[float, float]:
    """
    Convert footprint-local coordinates into board-absolute coordinates.

    This is the forward inverse of `altium_pcbdoc._reverse_transform_point()`:
      local -> mirror_Y (if bottom) -> rotate -> translate
    """
    x = float(lx)
    y = -float(ly) if flipped else float(ly)
    angle = float(rotation_deg) % 360.0

    if angle == 0.0:
        dx, dy = x, y
    elif angle == 90.0:
        dx, dy = -y, x
    elif angle == 180.0:
        dx, dy = -x, -y
    elif angle == 270.0:
        dx, dy = y, -x
    else:
        rad = math.radians(angle)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        dx = x * cos_r - y * sin_r
        dy = x * sin_r + y * cos_r

    return float(cx) + dx, float(cy) + dy


def _forward_transform_angle(
    local_angle: float, rotation_deg: float, flipped: bool
) -> float:
    """
    Convert a footprint-local primitive angle into board-space.

    Top-side:    board = component_rotation + local
    Bottom-side: board = component_rotation - local
    """
    if flipped:
        return (float(rotation_deg) - float(local_angle)) % 360.0
    return (float(rotation_deg) + float(local_angle)) % 360.0


def _forward_transform_model_z_rotation(
    local_angle: float, rotation_deg: float, flipped: bool
) -> float:
    """
    Convert a footprint-local 3D model Z rotation into board-space.
    """
    if flipped:
        return (float(local_angle) - float(rotation_deg)) % 360.0
    return (float(local_angle) + float(rotation_deg)) % 360.0


def _component_layer_flip_map(builder: "PcbDocBuilder") -> dict[int, int]:
    return _build_component_layer_flip_map(
        builder.board_data.top_level_segment.to_mapping()
    )


def _transform_layer(
    layer_id: int, *, flipped: bool, layer_flip_map: dict[int, int]
) -> int:
    return _flip_layer(int(layer_id), layer_flip_map) if flipped else int(layer_id)


def _component_copper_layer_id(layer_token: str) -> int | None:
    try:
        layer = PcbLayer.from_json_name(layer_token)
    except ValueError:
        return None
    return int(layer) if layer.is_copper() else None


def _transform_placed_primitive_layer(
    layer_id: int,
    *,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> int:
    if flipped:
        return _transform_layer(layer_id, flipped=True, layer_flip_map=layer_flip_map)
    if (
        placement_copper_layer_id is not None
        and placement_copper_layer_id != int(PcbLayer.TOP)
        and int(layer_id) == int(PcbLayer.TOP)
    ):
        return placement_copper_layer_id
    return int(layer_id)


def _footprint_local_bounds(
    footprint: "AltiumPcbFootprint",
) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    for pad in footprint.pads:
        xs.append(float(pad.x_mils))
        ys.append(float(pad.y_mils))
    for track in footprint.tracks:
        xs.extend((float(track.start_x_mils), float(track.end_x_mils)))
        ys.extend((float(track.start_y_mils), float(track.end_y_mils)))
    for arc in footprint.arcs:
        xs.extend(
            (
                float(arc.center_x_mils) - float(arc.radius_mils),
                float(arc.center_x_mils) + float(arc.radius_mils),
            )
        )
        ys.extend(
            (
                float(arc.center_y_mils) - float(arc.radius_mils),
                float(arc.center_y_mils) + float(arc.radius_mils),
            )
        )
    for fill in footprint.fills:
        xs.extend((float(fill.pos1_x_mils), float(fill.pos2_x_mils)))
        ys.extend((float(fill.pos1_y_mils), float(fill.pos2_y_mils)))
    for region in footprint.regions:
        for vertex in region.outline_vertices:
            xs.append(float(vertex.x_mils))
            ys.append(float(vertex.y_mils))

    if not xs or not ys:
        return (-50.0, -50.0, 50.0, 50.0)
    return min(xs), min(ys), max(xs), max(ys)


def _default_component_text_specs(
    footprint: "AltiumPcbFootprint",
    *,
    designator: str,
    comment_text: str | None,
    layer_token: str,
    rotation_degrees: float,
) -> list[_ComponentTextSpec]:
    min_x, min_y, max_x, max_y = _footprint_local_bounds(footprint)
    center_x = (min_x + max_x) / 2.0
    margin = max(40.0, (max_y - min_y) * 0.5 if max_y > min_y else 40.0)
    overlay_layer = (
        PcbLayer.BOTTOM_OVERLAY if layer_token == "BOTTOM" else PcbLayer.TOP_OVERLAY
    )
    mirrored = layer_token == "BOTTOM"

    specs: list[_ComponentTextSpec] = [
        {
            "text": str(designator),
            "position_mils": (center_x, max_y + margin),
            "height_mils": 60.0,
            "layer": overlay_layer,
            "rotation_degrees": float(rotation_degrees),
            "is_designator": True,
            "is_comment": False,
            "is_mirrored": mirrored,
        }
    ]
    if comment_text is not None:
        specs.append(
            {
                "text": str(comment_text),
                "position_mils": (center_x, min_y - margin),
                "height_mils": 60.0,
                "layer": overlay_layer,
                "rotation_degrees": float(rotation_degrees),
                "is_designator": False,
                "is_comment": True,
                "is_mirrored": mirrored,
            }
        )
    return specs


def _transform_component_body_into_board(
    body: "AltiumPcbComponentBody",
    *,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
) -> "AltiumPcbComponentBody":
    placed = copy.deepcopy(body)
    placed.layer = _transform_layer(
        placed.layer, flipped=flipped, layer_flip_map=layer_flip_map
    )
    if flipped:
        if placed.body_projection == PcbBodyProjection.TOP:
            placed.body_projection = PcbBodyProjection.BOTTOM
        elif placed.body_projection == PcbBodyProjection.BOTTOM:
            placed.body_projection = PcbBodyProjection.TOP

    for vertex in placed.outline:
        tx, ty = _forward_transform_point(
            float(vertex.x) / 10000.0,
            float(vertex.y) / 10000.0,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        vertex.x = int(round(tx * 10000.0))
        vertex.y = int(round(ty * 10000.0))
        if int(getattr(vertex, "center_x", 0)) or int(getattr(vertex, "center_y", 0)):
            ccx, ccy = _forward_transform_point(
                float(vertex.center_x) / 10000.0,
                float(vertex.center_y) / 10000.0,
                cx_mils,
                cy_mils,
                rotation_degrees,
                flipped,
            )
            vertex.center_x = int(round(ccx * 10000.0))
            vertex.center_y = int(round(ccy * 10000.0))
        if bool(getattr(vertex, "is_round", False)):
            start_angle = float(vertex.start_angle)
            end_angle = float(vertex.end_angle)
            if flipped:
                start_angle, end_angle = end_angle, start_angle
            vertex.start_angle = _forward_transform_angle(
                start_angle, rotation_degrees, flipped
            )
            vertex.end_angle = _forward_transform_angle(
                end_angle, rotation_degrees, flipped
            )

    if getattr(placed, "holes", None):
        for hole in placed.holes:
            for vertex in hole:
                tx, ty = _forward_transform_point(
                    float(vertex.x) / 10000.0,
                    float(vertex.y) / 10000.0,
                    cx_mils,
                    cy_mils,
                    rotation_degrees,
                    flipped,
                )
                vertex.x = float(int(round(tx * 10000.0)))
                vertex.y = float(int(round(ty * 10000.0)))

    mx, my = _forward_transform_point(
        float(getattr(placed, "model_2d_x", 0)) / 10000.0,
        float(getattr(placed, "model_2d_y", 0)) / 10000.0,
        cx_mils,
        cy_mils,
        rotation_degrees,
        flipped,
    )
    placed.model_2d_x = int(round(mx * 10000.0))
    placed.model_2d_y = int(round(my * 10000.0))
    placed.model_3d_rotz = _forward_transform_model_z_rotation(
        float(getattr(placed, "model_3d_rotz", 0.0)),
        rotation_degrees,
        flipped,
    )
    return placed


def _maybe_load_source_pcblib(
    source_pcblib: "AltiumPcbLib | None",
    source_footprint_library: str,
) -> "AltiumPcbLib | None":
    """Load the source PcbLib when a real .PcbLib path is provided."""
    if source_pcblib is not None or not source_footprint_library:
        return source_pcblib

    maybe_lib_path = Path(str(source_footprint_library))
    if maybe_lib_path.suffix.lower() != ".pcblib" or not maybe_lib_path.exists():
        return source_pcblib

    from .altium_pcblib import AltiumPcbLib

    return AltiumPcbLib.from_file(maybe_lib_path)


def _add_placed_tracks(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> None:
    for track in footprint.tracks:
        start = _forward_transform_point(
            track.start_x_mils,
            track.start_y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        end = _forward_transform_point(
            track.end_x_mils,
            track.end_y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_track(
            start,
            end,
            width_mils=track.width_mils,
            layer=_transform_placed_primitive_layer(
                track.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            component_index=component_index,
        )


def _add_placed_arcs(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> None:
    for arc in footprint.arcs:
        center = _forward_transform_point(
            arc.center_x_mils,
            arc.center_y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_arc(
            center_mils=center,
            radius_mils=arc.radius_mils,
            start_angle=_forward_transform_angle(
                arc.start_angle, rotation_degrees, flipped
            ),
            end_angle=_forward_transform_angle(
                arc.end_angle, rotation_degrees, flipped
            ),
            width_mils=arc.width_mils,
            layer=_transform_placed_primitive_layer(
                arc.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            component_index=component_index,
        )


def _add_placed_fills(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> None:
    for fill in footprint.fills:
        pos1 = _forward_transform_point(
            fill.pos1_x_mils,
            fill.pos1_y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        pos2 = _forward_transform_point(
            fill.pos2_x_mils,
            fill.pos2_y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_fill(
            pos1,
            pos2,
            rotation_degrees=_forward_transform_angle(
                fill.rotation, rotation_degrees, flipped
            ),
            layer=_transform_placed_primitive_layer(
                fill.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            component_index=component_index,
        )


def _resolve_placed_text_value(
    text: object, designator: str, comment_text: str | None
) -> str:
    if bool(getattr(text, "is_designator", False)):
        return designator
    if bool(getattr(text, "is_comment", False)) and comment_text is not None:
        return str(comment_text)
    return str(getattr(text, "text_content", ""))


def _add_placed_texts(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    designator: str,
    comment_text: str | None,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> tuple[bool, bool]:
    footprint_has_designator_text = any(
        bool(getattr(text, "is_designator", False)) for text in footprint.texts
    )
    footprint_has_comment_text = any(
        bool(getattr(text, "is_comment", False)) for text in footprint.texts
    )

    for text in footprint.texts:
        position = _forward_transform_point(
            text.x_mils,
            text.y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_text(
            text=_resolve_placed_text_value(text, designator, comment_text),
            position_mils=position,
            height_mils=text.height_mils,
            layer=_transform_placed_primitive_layer(
                text.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            rotation_degrees=_forward_transform_angle(
                text.rotation, rotation_degrees, flipped
            ),
            stroke_width_mils=text.stroke_width_mils,
            font_name=text.font_name or "Arial",
            is_comment=text.is_comment,
            is_designator=text.is_designator,
            is_mirrored=bool(text.is_mirrored),
            component_index=component_index,
        )

    return footprint_has_designator_text, footprint_has_comment_text


def _add_default_component_texts_if_needed(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    designator: str,
    comment_text: str | None,
    footprint_has_designator_text: bool,
    footprint_has_comment_text: bool,
    layer_token: str,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
) -> None:
    if footprint_has_designator_text and (
        comment_text is None or footprint_has_comment_text
    ):
        return

    for spec in _default_component_text_specs(
        footprint,
        designator=designator,
        comment_text=comment_text if not footprint_has_comment_text else None,
        layer_token=layer_token,
        rotation_degrees=rotation_degrees,
    ):
        if spec["is_designator"] and footprint_has_designator_text:
            continue
        builder.add_text(
            text=str(spec["text"]),
            position_mils=_forward_transform_point(
                spec["position_mils"][0],
                spec["position_mils"][1],
                cx_mils,
                cy_mils,
                rotation_degrees,
                flipped,
            ),
            height_mils=float(spec["height_mils"]),
            layer=spec["layer"],
            rotation_degrees=float(spec["rotation_degrees"]),
            is_comment=bool(spec["is_comment"]),
            is_designator=bool(spec["is_designator"]),
            is_mirrored=bool(spec["is_mirrored"]),
            component_index=component_index,
        )


def _add_placed_pads(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    pad_nets: Mapping[str, str] | None,
    placement_copper_layer_id: int | None,
) -> None:
    for pad in footprint.pads:
        custom_shape = getattr(pad, "custom_shape", None)
        if custom_shape is not None:
            _add_placed_custom_pad(
                builder,
                pad,
                component_index=component_index,
                cx_mils=cx_mils,
                cy_mils=cy_mils,
                rotation_degrees=rotation_degrees,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                pad_nets=pad_nets,
                placement_copper_layer_id=placement_copper_layer_id,
            )
            continue
        position = _forward_transform_point(
            pad.x_mils,
            pad.y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_pad(
            designator=pad.designator,
            position_mils=position,
            width_mils=pad._from_internal_units(pad.top_width),
            height_mils=pad._from_internal_units(pad.top_height),
            layer=_transform_placed_primitive_layer(
                pad.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            shape=int(pad.effective_top_shape),
            rotation_degrees=_forward_transform_angle(
                pad.rotation, rotation_degrees, flipped
            ),
            hole_size_mils=pad.hole_size_mils,
            plated=pad.is_plated,
            net=None if pad_nets is None else pad_nets.get(str(pad.designator)),
            component_index=component_index,
        )


def _vertices_to_points_mils(
    vertices: Sequence[object] | None,
) -> list[tuple[float, float]]:
    points = [
        (float(getattr(vertex, "x_mils")), float(getattr(vertex, "y_mils")))
        for vertex in list(vertices or [])
    ]
    if len(points) >= 2 and points[0] == points[-1]:
        return points[:-1]
    return points


def _custom_shape_region_ids(footprint: "AltiumPcbFootprint") -> set[int]:
    region_ids: set[int] = set()
    for pad in footprint.pads:
        custom_shape = getattr(pad, "custom_shape", None)
        if custom_shape is None:
            continue
        iter_layer_shapes = getattr(custom_shape, "iter_layer_shapes", None)
        if not callable(iter_layer_shapes):
            continue
        for layer_shape in cast(Sequence[object], iter_layer_shapes()):
            region = getattr(layer_shape, "region", None)
            if region is not None:
                region_ids.add(id(region))
            shape_region = getattr(layer_shape, "shape_region", None)
            if shape_region is not None:
                region_ids.add(id(shape_region))
    return region_ids


def _custom_pad_layer_shape_geometry(
    layer_shape: object,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    source_region = getattr(layer_shape, "region", None) or getattr(
        layer_shape,
        "shape_region",
        None,
    )
    if source_region is None:
        return [], []
    raw_outline = getattr(source_region, "outline_vertices", None)
    outline = _vertices_to_points_mils(
        raw_outline if isinstance(raw_outline, Sequence) else None
    )
    raw_holes = getattr(source_region, "hole_vertices", None)
    holes = []
    if isinstance(raw_holes, Sequence):
        holes = [
            _vertices_to_points_mils(hole if isinstance(hole, Sequence) else None)
            for hole in raw_holes
        ]
    return outline, holes


def _transform_points(
    points: list[tuple[float, float]],
    *,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
) -> list[tuple[float, float]]:
    return [
        _forward_transform_point(
            point_x,
            point_y,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        for point_x, point_y in points
    ]


def _ordered_custom_pad_layer_shapes(pad: "AltiumPcbPad") -> list[object]:
    custom_shape = getattr(pad, "custom_shape", None)
    if custom_shape is None:
        return []
    iter_layer_shapes = getattr(custom_shape, "iter_layer_shapes", None)
    if not callable(iter_layer_shapes):
        return []
    shapes = list(cast(Sequence[object], iter_layer_shapes()))
    primary = getattr(custom_shape, "primary_layer_shape", None)
    if primary is not None and primary in shapes:
        return [primary] + [shape for shape in shapes if shape is not primary]
    return shapes


def _add_placed_custom_pad(
    builder: "PcbDocBuilder",
    pad: "AltiumPcbPad",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    pad_nets: Mapping[str, str] | None,
    placement_copper_layer_id: int | None,
) -> None:
    layer_shapes = _ordered_custom_pad_layer_shapes(pad)
    if not layer_shapes:
        raise NotImplementedError("Custom pad has no source layer shape")

    primary_shape = layer_shapes[0]
    outline, holes = _custom_pad_layer_shape_geometry(primary_shape)
    if len(outline) < 3:
        raise NotImplementedError("Custom pad outline requires at least 3 points")
    position = _forward_transform_point(
        float(getattr(pad, "x_mils")),
        float(getattr(pad, "y_mils")),
        cx_mils,
        cy_mils,
        rotation_degrees,
        flipped,
    )
    primary_layer = _transform_placed_primitive_layer(
        int(getattr(primary_shape, "layer", getattr(pad, "layer", PcbLayer.TOP))),
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    builder.add_custom_pad(
        designator=str(getattr(pad, "designator", "")),
        position_mils=position,
        outline_points_mils=_transform_points(
            outline,
            cx_mils=cx_mils,
            cy_mils=cy_mils,
            rotation_degrees=rotation_degrees,
            flipped=flipped,
        ),
        layer=primary_layer,
        anchor_width_mils=float(pad._from_internal_units(pad.top_width)),
        anchor_height_mils=float(pad._from_internal_units(pad.top_height)),
        anchor_rotation_degrees=_forward_transform_angle(
            float(getattr(pad, "rotation", 0.0)),
            rotation_degrees,
            flipped,
        ),
        anchor_shape=int(getattr(pad, "effective_top_shape", 1)),
        hole_points_mils=[
            _transform_points(
                hole,
                cx_mils=cx_mils,
                cy_mils=cy_mils,
                rotation_degrees=rotation_degrees,
                flipped=flipped,
            )
            for hole in holes
        ],
        outline_points_are_local=False,
        net=None
        if pad_nets is None
        else pad_nets.get(str(getattr(pad, "designator", ""))),
        component_index=component_index,
    )

    authored_pad = builder.pads[-1]
    zero_based_pad_index = len(builder.pads) - 1
    for layer_shape in layer_shapes[1:]:
        extra_outline, extra_holes = _custom_pad_layer_shape_geometry(layer_shape)
        if len(extra_outline) < 3:
            continue
        builder._add_custom_pad_layer_shape(
            pad=authored_pad,
            zero_based_pad_index=zero_based_pad_index,
            outline_points_mils=_transform_points(
                extra_outline,
                cx_mils=cx_mils,
                cy_mils=cy_mils,
                rotation_degrees=rotation_degrees,
                flipped=flipped,
            ),
            layer=_transform_placed_primitive_layer(
                int(getattr(layer_shape, "layer", primary_layer)),
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            hole_points_mils=[
                _transform_points(
                    hole,
                    cx_mils=cx_mils,
                    cy_mils=cy_mils,
                    rotation_degrees=rotation_degrees,
                    flipped=flipped,
                )
                for hole in extra_holes
            ],
            component_index=component_index,
            record=builder.custom_shapes[-1],
        )


def _add_placed_vias(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
) -> None:
    for via in footprint.vias:
        position = _forward_transform_point(
            via.x_mils,
            via.y_mils,
            cx_mils,
            cy_mils,
            rotation_degrees,
            flipped,
        )
        builder.add_via(
            position_mils=position,
            diameter_mils=via.diameter_mils,
            hole_size_mils=via.hole_size_mils,
            layer_start=via.layer_start,
            layer_end=via.layer_end,
            ipc4761_via_type=via.ipc4761_via_type,
            ipc4761_features=()
            if via.via_structure is None
            else tuple(via.via_structure.features),
            propagation_delay_ps=via.propagation_delay_ps,
            hole_positive_tolerance_mils=via.hole_positive_tolerance_mils,
            hole_negative_tolerance_mils=via.hole_negative_tolerance_mils,
            is_tent_top=via.is_tent_top,
            is_tent_bottom=via.is_tent_bottom,
            solder_mask_expansion_top_mils=(
                via.soldermask_expansion_front / 10000.0
                if getattr(via, "_has_soldermask_expansion_front", False)
                else None
            ),
            solder_mask_expansion_bottom_mils=(
                via.soldermask_expansion_back / 10000.0
                if getattr(via, "_has_soldermask_expansion_back", False)
                else None
            ),
            is_test_fab_top=via.is_test_fab_top,
            is_test_fab_bottom=via.is_test_fab_bottom,
            is_assy_testpoint_top=via.is_assy_testpoint_top,
            is_assy_testpoint_bottom=via.is_assy_testpoint_bottom,
            component_index=component_index,
        )


def _add_placed_regions(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
    placement_copper_layer_id: int | None,
) -> None:
    custom_region_ids = _custom_shape_region_ids(footprint)
    for region in footprint.regions:
        if id(region) in custom_region_ids:
            continue
        outline = [
            _forward_transform_point(
                vertex.x_mils,
                vertex.y_mils,
                cx_mils,
                cy_mils,
                rotation_degrees,
                flipped,
            )
            for vertex in region.outline_vertices
        ]
        holes = [
            [
                _forward_transform_point(
                    vertex.x_mils,
                    vertex.y_mils,
                    cx_mils,
                    cy_mils,
                    rotation_degrees,
                    flipped,
                )
                for vertex in hole
            ]
            for hole in region.hole_vertices
        ]
        builder.add_region(
            outline_points_mils=outline,
            hole_points_mils=holes,
            layer=_transform_placed_primitive_layer(
                region.layer,
                flipped=flipped,
                layer_flip_map=layer_flip_map,
                placement_copper_layer_id=placement_copper_layer_id,
            ),
            is_keepout=bool(region.is_keepout),
            keepout_restrictions=int(region.keepout_restrictions),
            region_kind=region.region_kind,
            cavity_height_mils=region.cavity_height_mils,
            component_index=component_index,
        )


def _resolve_body_model_entries(
    footprint: "AltiumPcbFootprint",
    source_pcblib: "AltiumPcbLib | None",
) -> dict[int, tuple["AltiumPcbModel", bytes]]:
    if not footprint.component_bodies or source_pcblib is None:
        return {}
    resolved_entries = resolve_footprint_body_model_entries(
        footprint,
        collect_pcblib_embedded_model_entries(
            source_pcblib.raw_models_data,
            source_pcblib.raw_models,
        ),
    )
    return {id(body): (model, payload) for body, model, payload in resolved_entries}


def _add_placed_component_bodies(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    component_index: int,
    source_pcblib: "AltiumPcbLib | None",
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
) -> None:
    body_model_entries = _resolve_body_model_entries(footprint, source_pcblib)
    for body in footprint.component_bodies:
        resolved_model = body_model_entries.get(id(body))
        if bool(getattr(body, "model_is_embedded", False)) and resolved_model is None:
            raise NotImplementedError(
                "Embedded 3D model placement requires source_pcblib for payload resolution"
            )

        placed_body = _transform_component_body_into_board(
            body,
            cx_mils=cx_mils,
            cy_mils=cy_mils,
            rotation_degrees=rotation_degrees,
            flipped=flipped,
            layer_flip_map=layer_flip_map,
        )
        if resolved_model is not None:
            model, payload = resolved_model
            authored_model = builder.add_embedded_model(
                name=model.name,
                model_data=payload,
                model_id=model.id,
                rotation_x_degrees=float(model.rotation_x),
                rotation_y_degrees=float(model.rotation_y),
                rotation_z_degrees=float(model.rotation_z),
                z_offset_mil=float(model.z_offset) / 10000.0,
                checksum=int(model.checksum),
                model_source=str(model.model_source or "Undefined"),
                data_is_compressed=True,
            )
            placed_body.model_id = authored_model.id
            placed_body.model_name = str(authored_model.name)
            placed_body.model_checksum = int(authored_model.checksum) & 0xFFFFFFFF
            placed_body.model_is_embedded = True
            model_source = str(authored_model.model_source or "")
            placed_body.model_source = model_source + (
                "\x00" if model_source and not model_source.endswith("\x00") else ""
            )
        builder.add_component_body(placed_body, component_index=component_index)


def place_footprint_into_builder(
    builder: "PcbDocBuilder",
    footprint: "AltiumPcbFootprint",
    *,
    designator: str,
    position_mils: tuple[float, float],
    layer: str | PcbLayer | int = "TOP",
    rotation_degrees: float = 0.0,
    source_footprint_library: str = "",
    comment_text: str | None = None,
    comment_visible: bool = False,
    component_parameters: Mapping[str, str] | None = None,
    pad_nets: Mapping[str, str] | None = None,
    source_pcblib: "AltiumPcbLib | None" = None,
) -> int:
    """
    Place one `AltiumPcbFootprint` onto the board through the builder path.

    Embedded 3D model placement requires `source_pcblib` so model payloads can
    be resolved from the source library.
    """
    layer_token = _normalize_component_layer(layer)
    flipped = layer_token == "BOTTOM"
    placement_copper_layer_id = _component_copper_layer_id(layer_token)
    cx_mils, cy_mils = position_mils
    layer_flip_map = _component_layer_flip_map(builder)
    source_pcblib = _maybe_load_source_pcblib(source_pcblib, source_footprint_library)
    footprint_has_designator_text = any(
        bool(getattr(text, "is_designator", False)) for text in footprint.texts
    )
    footprint_has_comment_text = any(
        bool(getattr(text, "is_comment", False)) for text in footprint.texts
    )
    effective_comment_on = bool(comment_visible) and (
        comment_text is not None or footprint_has_comment_text
    )

    component_index = builder.add_component(
        designator=designator,
        footprint=footprint.name,
        position_mils=position_mils,
        layer=layer_token,
        rotation_degrees=rotation_degrees,
        source_footprint_library=source_footprint_library,
        comment_on=effective_comment_on,
        description=str(footprint.parameters.get("DESCRIPTION", "") or ""),
        parameters=dict(component_parameters or {}),
    )

    _add_placed_tracks(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    _add_placed_arcs(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    _add_placed_fills(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    footprint_has_designator_text, footprint_has_comment_text = _add_placed_texts(
        builder,
        footprint,
        component_index=component_index,
        designator=designator,
        comment_text=comment_text,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    _add_default_component_texts_if_needed(
        builder,
        footprint,
        component_index=component_index,
        designator=designator,
        comment_text=comment_text,
        footprint_has_designator_text=footprint_has_designator_text,
        footprint_has_comment_text=footprint_has_comment_text,
        layer_token=layer_token,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
    )
    _add_placed_pads(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        pad_nets=pad_nets,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    _add_placed_vias(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
    )
    _add_placed_regions(
        builder,
        footprint,
        component_index=component_index,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
        placement_copper_layer_id=placement_copper_layer_id,
    )
    _add_placed_component_bodies(
        builder,
        footprint,
        component_index=component_index,
        source_pcblib=source_pcblib,
        cx_mils=cx_mils,
        cy_mils=cy_mils,
        rotation_degrees=rotation_degrees,
        flipped=flipped,
        layer_flip_map=layer_flip_map,
    )

    return component_index
