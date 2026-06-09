from __future__ import annotations

import json
from pathlib import Path

from altium_monkey import (
    AltiumPcbArc,
    AltiumPcbComponentBody,
    AltiumPcbFill,
    AltiumPcbFootprint,
    AltiumPcbLib,
    AltiumPcbPad,
    AltiumPcbRegion,
    AltiumPcbShapeBasedRegion,
    AltiumPcbText,
    AltiumPcbTrack,
    AltiumPcbVia,
    PcbTextKind,
)
from altium_monkey.altium_ole import AltiumOleFile

SAMPLE_DIR = Path(__file__).resolve().parent
ASSET_DIR = SAMPLE_DIR / "assets"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcblib_recreate_via_feature_libraries.json"

LIBRARIES = [
    "via_ipc4761_type_matrix.PcbLib",
    "via_propagation_delay_matrix.PcbLib",
    "via_features_and_all_primitives.PcbLib",
    "via_test_point_flags.PcbLib",
    "footprint_parameters.PcbLib",
]

OWNED_STREAMS = [
    "Parameters",
    "PrimitiveParameters",
    "ViaStructureManager",
    "ViaStructures",
]


def _iu_to_mils(value: int | float) -> float:
    return float(value) / 10000.0


def _optional_iu_to_mils(value: int | float | None) -> float | None:
    if value is None:
        return None
    if int(value) == 0:
        return None
    return _iu_to_mils(value)


def _manual_mask_mils(mode: int | object, value: int | float | None) -> float | None:
    if int(mode) != 2:
        return None
    return _iu_to_mils(0 if value is None else value)


def _copy_parameters(source: AltiumPcbFootprint, target: AltiumPcbFootprint) -> None:
    for name, value in source.parameters.items():
        target.set_parameter(name, value)
    for name, value in source.footprint_primitive_parameters.items():
        target.set_footprint_primitive_parameter(name, value)


def _via_features(via: AltiumPcbVia) -> object:
    if via.via_structure is None:
        return None
    return list(via.via_structure.features)


def _replay_via(target: AltiumPcbFootprint, via: AltiumPcbVia) -> None:
    target.add_via(
        position_mils=(via.x_mils, via.y_mils),
        diameter_mils=via.diameter_mils,
        hole_size_mils=via.hole_size_mils,
        layer_start=via.layer_start,
        layer_end=via.layer_end,
        ipc4761_via_type=via.ipc4761_via_type,
        ipc4761_features=_via_features(via),
        propagation_delay_ps=via.propagation_delay_ps,
        hole_positive_tolerance_mils=via.hole_positive_tolerance_mils,
        hole_negative_tolerance_mils=via.hole_negative_tolerance_mils,
        is_tent_top=via.is_tent_top,
        is_tent_bottom=via.is_tent_bottom,
        solder_mask_expansion_top_mils=_optional_iu_to_mils(
            via.soldermask_expansion_front
        ),
        solder_mask_expansion_bottom_mils=_optional_iu_to_mils(
            via.soldermask_expansion_back
        ),
        is_test_fab_top=via.is_test_fab_top,
        is_test_fab_bottom=via.is_test_fab_bottom,
        is_assy_testpoint_top=via.is_assy_testpoint_top,
        is_assy_testpoint_bottom=via.is_assy_testpoint_bottom,
    )


def _replay_pad(target: AltiumPcbFootprint, pad: AltiumPcbPad) -> None:
    local_stack = int(getattr(pad, "pad_mode", 0) or 0) != 0
    target.add_pad(
        designator=pad.designator,
        position_mils=(pad.x_mils, pad.y_mils),
        width_mils=pad.width_mils,
        height_mils=_iu_to_mils(pad.height),
        layer=pad.layer,
        shape=pad.semantic_top_shape,
        rotation_degrees=float(pad.rotation or 0.0),
        hole_size_mils=pad.hole_size_mils,
        plated=pad.is_plated,
        corner_radius_percent=pad.corner_radius_percentage or None,
        top_shape=pad.top_shape if local_stack else None,
        top_width_mils=_iu_to_mils(pad.top_width) if local_stack else None,
        top_height_mils=_iu_to_mils(pad.top_height) if local_stack else None,
        mid_shape=pad.mid_shape if local_stack else None,
        mid_width_mils=_iu_to_mils(pad.mid_width) if local_stack else None,
        mid_height_mils=_iu_to_mils(pad.mid_height) if local_stack else None,
        bottom_shape=pad.bot_shape if local_stack else None,
        bottom_width_mils=_iu_to_mils(pad.bot_width) if local_stack else None,
        bottom_height_mils=_iu_to_mils(pad.bot_height) if local_stack else None,
        pad_mode=pad.pad_mode if local_stack else None,
        slot_length_mils=_iu_to_mils(pad.slot_size),
        slot_rotation_degrees=float(pad.slot_rotation or 0.0),
        hole_shape=pad.hole_shape,
        solder_mask_expansion_mode=pad.soldermask_expansion_mode,
        solder_mask_expansion_mils=_manual_mask_mils(
            pad.soldermask_expansion_mode,
            pad.soldermask_expansion_manual,
        ),
        paste_mask_expansion_mode=pad.pastemask_expansion_mode,
        paste_mask_expansion_mils=_manual_mask_mils(
            pad.pastemask_expansion_mode,
            pad.pastemask_expansion_manual,
        ),
        hole_positive_tolerance_mils=pad.hole_positive_tolerance_mils,
        hole_negative_tolerance_mils=pad.hole_negative_tolerance_mils,
        is_test_fab_top=pad.is_test_fab_top,
        is_test_fab_bottom=pad.is_test_fab_bottom,
        is_assy_testpoint_top=pad.is_assy_test_point_top,
        is_assy_testpoint_bottom=pad.is_assy_test_point_bottom,
    )


def _replay_track(target: AltiumPcbFootprint, track: AltiumPcbTrack) -> None:
    target.add_track(
        (track.start_x_mils, track.start_y_mils),
        (track.end_x_mils, track.end_y_mils),
        width_mils=track.width_mils,
        layer=track.layer,
        v7_layer_id=track.v7_layer_id or None,
        solder_mask_expansion_mils=_optional_iu_to_mils(track.solder_mask_expansion),
        paste_mask_expansion_mils=_optional_iu_to_mils(track.paste_mask_expansion),
    )


def _replay_arc(target: AltiumPcbFootprint, arc: AltiumPcbArc) -> None:
    target.add_arc(
        center_mils=(arc.center_x_mils, arc.center_y_mils),
        radius_mils=arc.radius_mils,
        start_angle_degrees=float(arc.start_angle),
        end_angle_degrees=float(arc.end_angle),
        width_mils=arc.width_mils,
        layer=arc.layer,
        v7_layer_id=arc.v7_layer_id or None,
        solder_mask_expansion_mils=_optional_iu_to_mils(arc.solder_mask_expansion),
        paste_mask_expansion_mils=_optional_iu_to_mils(arc.paste_mask_expansion),
    )


def _replay_fill(target: AltiumPcbFootprint, fill: AltiumPcbFill) -> None:
    target.add_fill(
        (fill.pos1_x_mils, fill.pos1_y_mils),
        (fill.pos2_x_mils, fill.pos2_y_mils),
        layer=fill.layer,
        rotation_degrees=float(fill.rotation or 0.0),
        v7_layer_id=fill.v7_layer_id or None,
        solder_mask_expansion_mils=_optional_iu_to_mils(fill.solder_mask_expansion),
        paste_mask_expansion_mils=_optional_iu_to_mils(fill.paste_mask_expansion),
    )


def _points_from_region(
    region: AltiumPcbRegion | AltiumPcbShapeBasedRegion,
) -> list[tuple[float, float]]:
    if isinstance(region, AltiumPcbShapeBasedRegion):
        return [(vertex.x_mils, vertex.y_mils) for vertex in region.outline]
    return [(vertex.x_mils, vertex.y_mils) for vertex in region.outline_vertices]


def _holes_from_region(
    region: AltiumPcbRegion | AltiumPcbShapeBasedRegion,
) -> list[list[tuple[float, float]]]:
    raw_holes = (
        region.holes
        if isinstance(region, AltiumPcbShapeBasedRegion)
        else region.hole_vertices
    )
    return [[(vertex.x_mils, vertex.y_mils) for vertex in hole] for hole in raw_holes]


def _replay_region(
    target: AltiumPcbFootprint,
    region: AltiumPcbRegion | AltiumPcbShapeBasedRegion,
) -> None:
    target.add_region(
        outline_points_mils=_points_from_region(region),
        layer=region.layer,
        hole_points_mils=_holes_from_region(region),
        kind=region.region_kind,
        is_board_cutout=region.is_board_cutout,
        is_shapebased=region.is_shapebased,
        is_keepout=region.is_keepout,
        keepout_restrictions=region.keepout_restrictions,
        subpoly_index=region.subpoly_index,
        cavity_height_mils=region.cavity_height_mils,
        v7_layer=region.properties.get("V7_LAYER"),
    )


def _text_kind(text: AltiumPcbText) -> PcbTextKind:
    if int(text.font_type) == 1:
        return PcbTextKind.TRUETYPE
    if int(text.font_type) == 2:
        return PcbTextKind.BARCODE
    return PcbTextKind.STROKE


def _replay_text(target: AltiumPcbFootprint, text: AltiumPcbText) -> None:
    target.add_text(
        text=text.text_content,
        position_mils=(text.x_mils, text.y_mils),
        height_mils=text.height_mils,
        layer=text.layer,
        rotation_degrees=float(text.rotation or 0.0),
        stroke_width_mils=text.stroke_width_mils,
        font_kind=_text_kind(text),
        stroke_font_type=text.stroke_font_type,
        font_name=text.font_name or "Arial",
        bold=text.is_bold,
        italic=text.is_italic,
        barcode_kind=text.barcode_kind,
        barcode_render_mode=text.barcode_render_mode,
        barcode_full_size_mils=(
            text.barcode_full_width_mils,
            text.barcode_full_height_mils,
        ),
        barcode_margin_mils=(
            _iu_to_mils(text.barcode_x_margin),
            _iu_to_mils(text.barcode_y_margin),
        ),
        barcode_min_width_mils=text.barcode_min_width_mils,
        barcode_show_text=text.barcode_show_text,
        barcode_inverted=text.barcode_inverted,
        is_comment=text.is_comment,
        is_designator=text.is_designator,
        is_mirrored=text.is_mirrored,
        is_inverted=text.is_inverted,
        inverted_margin_mils=_iu_to_mils(text.margin_border_width),
        use_inverted_rectangle=text.use_inverted_rectangle,
        inverted_rectangle_size_mils=(
            text.textbox_rect_width_mils,
            text.textbox_rect_height_mils,
        ),
        is_frame=text.is_frame,
        frame_size_mils=(text.textbox_rect_width_mils, text.textbox_rect_height_mils),
        text_justification=text.effective_justification,
    )


def _points_from_body(body: AltiumPcbComponentBody) -> list[tuple[float, float]]:
    return [(vertex.x_mils, vertex.y_mils) for vertex in body.outline]


def _replay_component_body(
    target: AltiumPcbFootprint,
    body: AltiumPcbComponentBody,
) -> None:
    target.add_component_body(
        outline_points_mils=_points_from_body(body),
        layer=body.layer,
        overall_height_mils=body.overall_height_mils,
        standoff_height_mils=body.standoff_height_mils,
        cavity_height_mils=body.cavity_height_mils,
        body_projection=body.body_projection,
        model_2d_mils=(_iu_to_mils(body.model_2d_x), _iu_to_mils(body.model_2d_y)),
        model_2d_rotation_degrees=body.model_2d_rotation,
        model_3d_rotx_degrees=body.model_3d_rotx,
        model_3d_roty_degrees=body.model_3d_roty,
        model_3d_rotz_degrees=body.model_3d_rotz,
        model_3d_dz_mils=_iu_to_mils(body.model_3d_dz),
        model_checksum=body.model_checksum,
        identifier=body.identifier,
        name=body.name or " ",
        body_color_3d=body.body_color_3d,
        body_opacity_3d=body.body_opacity_3d,
        model_type=body.model_type,
        model_source=body.model_source,
    )


def _replay_primitive(target: AltiumPcbFootprint, primitive: object) -> None:
    if isinstance(primitive, AltiumPcbVia):
        _replay_via(target, primitive)
    elif isinstance(primitive, AltiumPcbPad):
        _replay_pad(target, primitive)
    elif isinstance(primitive, AltiumPcbTrack):
        _replay_track(target, primitive)
    elif isinstance(primitive, AltiumPcbArc):
        _replay_arc(target, primitive)
    elif isinstance(primitive, AltiumPcbFill):
        _replay_fill(target, primitive)
    elif isinstance(primitive, (AltiumPcbRegion, AltiumPcbShapeBasedRegion)):
        _replay_region(target, primitive)
    elif isinstance(primitive, AltiumPcbText):
        _replay_text(target, primitive)
    elif isinstance(primitive, AltiumPcbComponentBody):
        _replay_component_body(target, primitive)
    else:
        raise TypeError(f"Unsupported footprint primitive: {type(primitive).__name__}")


def _recreate_footprint(source: AltiumPcbFootprint) -> AltiumPcbLib:
    rebuilt = AltiumPcbLib()
    target = rebuilt.add_footprint(
        source.name,
        height=source.parameters.get("HEIGHT", "0mil"),
        description=source.parameters.get("DESCRIPTION", ""),
        item_guid=source.parameters.get("ITEMGUID", ""),
        revision_guid=source.parameters.get("REVISIONGUID", ""),
    )
    _copy_parameters(source, target)
    for primitive in source.primitives:
        _replay_primitive(target, primitive)
    return rebuilt


def _read_owned_streams(path: Path, storage_name: str) -> dict[str, bytes]:
    streams: dict[str, bytes] = {}
    with AltiumOleFile(str(path)) as ole:
        for stream_name in OWNED_STREAMS:
            ole_name = f"{storage_name}/{stream_name}"
            if ole.exists(ole_name):
                streams[stream_name] = ole.openstream(ole_name)
    return streams


def _via_summary(footprint) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for via in footprint.vias:
        structure = getattr(via, "via_structure", None)
        rows.append(
            {
                "ipc4761_via_type": int(via.ipc4761_via_type),
                "propagation_delay_ps": via.propagation_delay_ps,
                "is_test_fab_top": bool(via.is_test_fab_top),
                "is_test_fab_bottom": bool(via.is_test_fab_bottom),
                "is_assy_testpoint_top": bool(via.is_assy_testpoint_top),
                "is_assy_testpoint_bottom": bool(via.is_assy_testpoint_bottom),
                "feature_rows": []
                if structure is None
                else [
                    {
                        "feature_type": int(feature.feature_type),
                        "side": int(feature.side),
                        "material": feature.material,
                    }
                    for feature in structure.features
                ],
            }
        )
    return rows


def _summary(source_path: Path, output_path: Path) -> dict[str, object]:
    source = AltiumPcbLib.from_file(source_path)
    output = AltiumPcbLib.from_file(output_path)
    source_fp = source.footprints[0]
    output_fp = output.footprints[0]
    source_storage = getattr(source_fp, "_ole_storage_name", source_fp.name)
    output_storage = getattr(output_fp, "_ole_storage_name", output_fp.name)
    source_streams = _read_owned_streams(source_path, source_storage)
    output_streams = _read_owned_streams(output_path, output_storage)
    stream_names = sorted(set(source_streams) | set(output_streams))

    return {
        "source": source_path.relative_to(SAMPLE_DIR).as_posix(),
        "output": output_path.relative_to(SAMPLE_DIR).as_posix(),
        "footprint": output_fp.name,
        "source_record_count": len(source_fp.primitives),
        "record_count": len(output_fp.primitives),
        "pad_count": len(output_fp.pads),
        "via_count": len(output_fp.vias),
        "track_count": len(output_fp.tracks),
        "arc_count": len(output_fp.arcs),
        "text_count": len(output_fp.texts),
        "fill_count": len(output_fp.fills),
        "region_count": len(output_fp.regions),
        "via_structure_count": len(output_fp.via_structures),
        "via_structure_link_count": len(output_fp.via_structure_links),
        "owned_streams_exact_match": {
            name: source_streams.get(name) == output_streams.get(name)
            for name in stream_names
        },
        "semantic_match": {
            "record_count": len(source_fp.primitives) == len(output_fp.primitives),
            "parameters": source_fp.parameters == output_fp.parameters,
            "footprint_primitive_parameters": (
                source_fp.footprint_primitive_parameters
                == output_fp.footprint_primitive_parameters
            ),
            "via_features": _via_summary(source_fp) == _via_summary(output_fp),
            "via_structure_counts": (
                len(source_fp.via_structures) == len(output_fp.via_structures)
                and len(source_fp.via_structure_links)
                == len(output_fp.via_structure_links)
            ),
        },
        "parameters": dict(output_fp.parameters),
        "footprint_primitive_parameters": dict(
            output_fp.footprint_primitive_parameters
        ),
        "vias": _via_summary(output_fp),
    }


def recreate_libraries(output_dir: Path = OUTPUT_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for filename in LIBRARIES:
        source_path = ASSET_DIR / filename
        source = AltiumPcbLib.from_file(source_path)
        rebuilt = _recreate_footprint(source.footprints[0])
        output_path = output_dir / filename
        rebuilt.save(output_path)
        outputs.append(output_path)
    return outputs


def main() -> None:
    outputs = recreate_libraries()
    manifest = {
        "libraries": [
            _summary(ASSET_DIR / output_path.name, output_path)
            for output_path in outputs
        ]
    }
    OUTPUT_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(outputs)} PcbLib files")
    print(f"Wrote {OUTPUT_MANIFEST}")


if __name__ == "__main__":
    main()
