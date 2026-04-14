"""
Library-footprint placement helpers for `PcbDocBuilder`.

This module composes the existing board-primitive insertion APIs into the first
builder-owned footprint-placement path:

- create a board component record
- transform footprint-local primitives into board-absolute space
- attach child primitives to that component

The goal of this first slice is common library footprints made of:
- pads
- tracks
- arcs
- fills
- texts
- vias
- logical regions

What is intentionally deferred:
- component-body / model placement
- board-side custom-pad `CustomShapes/*` synthesis
- polygon pour definition/pour-state handling
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from .altium_pcb_embedded_model_compose import (
    collect_pcblib_embedded_model_entries,
    resolve_footprint_body_model_entries,
)
from .altium_pcbdoc_builder_components import _normalize_component_layer
from .altium_pcbdoc_builder_models import swap_body_projection_for_bottom
from .altium_pcbdoc_layers import _build_component_layer_flip_map, _flip_layer
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_pcblib import AltiumPcbFootprint, AltiumPcbLib
    from .altium_pcbdoc_builder import PcbDocBuilder


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


def _forward_transform_angle(local_angle: float, rotation_deg: float, flipped: bool) -> float:
    """
    Convert a footprint-local primitive angle into board-space.
    
    Top-side:    board = component_rotation + local
    Bottom-side: board = component_rotation - local
    """
    if flipped:
        return (float(rotation_deg) - float(local_angle)) % 360.0
    return (float(rotation_deg) + float(local_angle)) % 360.0


def _forward_transform_model_z_rotation(local_angle: float, rotation_deg: float, flipped: bool) -> float:
    """
    Convert a footprint-local 3D model Z rotation into board-space.
    """
    if flipped:
        return (float(local_angle) - float(rotation_deg)) % 360.0
    return (float(local_angle) + float(rotation_deg)) % 360.0


def _component_layer_flip_map(builder: "PcbDocBuilder") -> dict[int, int]:
    return _build_component_layer_flip_map(builder.board_data.top_level_segment.to_mapping())


def _transform_layer(layer_id: int, *, flipped: bool, layer_flip_map: dict[int, int]) -> int:
    return _flip_layer(int(layer_id), layer_flip_map) if flipped else int(layer_id)


def _footprint_local_bounds(footprint: "AltiumPcbFootprint") -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    for pad in footprint.pads:
        xs.append(float(pad.x_mils))
        ys.append(float(pad.y_mils))
    for track in footprint.tracks:
        xs.extend((float(track.start_x_mils), float(track.end_x_mils)))
        ys.extend((float(track.start_y_mils), float(track.end_y_mils)))
    for arc in footprint.arcs:
        xs.extend((float(arc.center_x_mils) - float(arc.radius_mils), float(arc.center_x_mils) + float(arc.radius_mils)))
        ys.extend((float(arc.center_y_mils) - float(arc.radius_mils), float(arc.center_y_mils) + float(arc.radius_mils)))
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
) -> list[dict[str, object]]:
    min_x, min_y, max_x, max_y = _footprint_local_bounds(footprint)
    center_x = (min_x + max_x) / 2.0
    margin = max(40.0, (max_y - min_y) * 0.5 if max_y > min_y else 40.0)
    overlay_layer = PcbLayer.BOTTOM_OVERLAY if layer_token == "BOTTOM" else PcbLayer.TOP_OVERLAY
    mirrored = layer_token == "BOTTOM"

    specs: list[dict[str, object]] = [
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
    body: object,
    *,
    cx_mils: float,
    cy_mils: float,
    rotation_degrees: float,
    flipped: bool,
    layer_flip_map: dict[int, int],
) -> object:
    placed = copy.deepcopy(body)
    placed.layer = _transform_layer(placed.layer, flipped=flipped, layer_flip_map=layer_flip_map)
    if flipped:
        placed.body_projection = swap_body_projection_for_bottom(placed.body_projection)

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
            vertex.start_angle = _forward_transform_angle(start_angle, rotation_degrees, flipped)
            vertex.end_angle = _forward_transform_angle(end_angle, rotation_degrees, flipped)

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
            layer=_transform_layer(track.layer, flipped=flipped, layer_flip_map=layer_flip_map),
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
            start_angle=_forward_transform_angle(arc.start_angle, rotation_degrees, flipped),
            end_angle=_forward_transform_angle(arc.end_angle, rotation_degrees, flipped),
            width_mils=arc.width_mils,
            layer=_transform_layer(arc.layer, flipped=flipped, layer_flip_map=layer_flip_map),
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
            rotation_degrees=_forward_transform_angle(fill.rotation, rotation_degrees, flipped),
            layer=_transform_layer(fill.layer, flipped=flipped, layer_flip_map=layer_flip_map),
            component_index=component_index,
        )


def _resolve_placed_text_value(text: object, designator: str, comment_text: str | None) -> str:
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
) -> tuple[bool, bool]:
    footprint_has_designator_text = any(bool(getattr(text, "is_designator", False)) for text in footprint.texts)
    footprint_has_comment_text = any(bool(getattr(text, "is_comment", False)) for text in footprint.texts)

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
            layer=_transform_layer(text.layer, flipped=flipped, layer_flip_map=layer_flip_map),
            rotation_degrees=_forward_transform_angle(text.rotation, rotation_degrees, flipped),
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
    if footprint_has_designator_text and (comment_text is None or footprint_has_comment_text):
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
) -> None:
    for pad in footprint.pads:
        if getattr(pad, "custom_shape", None) is not None:
            raise NotImplementedError(
                "Footprint placement for custom pads needs board-side CustomShapes/Data synthesis"
            )
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
            layer=_transform_layer(pad.layer, flipped=flipped, layer_flip_map=layer_flip_map),
            shape=int(pad.effective_top_shape),
            rotation_degrees=_forward_transform_angle(pad.rotation, rotation_degrees, flipped),
            hole_size_mils=pad.hole_size_mils,
            plated=pad.is_plated,
            net=None if pad_nets is None else pad_nets.get(str(pad.designator)),
            component_index=component_index,
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
) -> None:
    for region in footprint.regions:
        outline = [
            _forward_transform_point(vertex.x_mils, vertex.y_mils, cx_mils, cy_mils, rotation_degrees, flipped)
            for vertex in region.outline_vertices
        ]
        holes = [
            [
                _forward_transform_point(vertex.x_mils, vertex.y_mils, cx_mils, cy_mils, rotation_degrees, flipped)
                for vertex in hole
            ]
            for hole in region.hole_vertices
        ]
        builder.add_region(
            outline_points_mils=outline,
            hole_points_mils=holes,
            layer=_transform_layer(region.layer, flipped=flipped, layer_flip_map=layer_flip_map),
            is_keepout=bool(region.is_keepout),
            keepout_restrictions=int(region.keepout_restrictions),
            component_index=component_index,
        )


def _resolve_body_model_entries(
    footprint: "AltiumPcbFootprint",
    source_pcblib: "AltiumPcbLib | None",
) -> dict[int, tuple[object, bytes]]:
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
    
    This first slice is intentionally explicit about unsupported features:
    - custom pads raise `NotImplementedError`
    - embedded 3D model placement requires `source_pcblib`
    """
    layer_token = _normalize_component_layer(layer)
    flipped = layer_token == "BOTTOM"
    cx_mils, cy_mils = position_mils
    layer_flip_map = _component_layer_flip_map(builder)
    source_pcblib = _maybe_load_source_pcblib(source_pcblib, source_footprint_library)
    footprint_has_designator_text = any(bool(getattr(text, "is_designator", False)) for text in footprint.texts)
    footprint_has_comment_text = any(bool(getattr(text, "is_comment", False)) for text in footprint.texts)
    effective_comment_on = bool(comment_visible) and (comment_text is not None or footprint_has_comment_text)

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
