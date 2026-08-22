from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import cast

from altium_monkey import (
    AltiumLayerStackDocument,
    AltiumPcbDoc,
    AltiumStackupXDocument,
    PcbDocBuilder,
    PcbLayerRef,
    PcbSvgRenderOptions,
    StackupXFeature,
    StackupXFeatureId,
    StackupXLayer,
    StackupXLayerType,
    StackupXProperty,
    StackupXSpan,
    StackupXStack,
)
from altium_monkey.altium_layer_stack_document import AltiumStackLayer
from altium_monkey.altium_resolved_layer_stack import ResolvedLayerStack

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_PCBDOC = OUTPUT_DIR / "pcbdoc_v7_128_signal_track_row.PcbDoc"
OUTPUT_STACKUPX = OUTPUT_DIR / "ad26_128_signal.stackupx"
OUTPUT_SVG = OUTPUT_DIR / "pcbdoc_v7_128_signal_track_row.svg"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcbdoc_v7_128_signal_track_row.json"

SIGNAL_LAYER_COUNT = 128
TRACK_LENGTH_MILS = 1000.0
TRACK_WIDTH_MILS = 10.0
# Wide enough that the rotated per-layer overlay labels do not overlap.
TRACK_PITCH_MILS = 40.0
TRACK_START_X_MILS = 100.0
TRACK_START_Y_MILS = 100.0
BOARD_MARGIN_MILS = 100.0

# Layer-number labels above each track, written on the same copper layer as
# the track they describe and rotated to flow in +Y.
LABEL_HEIGHT_MILS = 25.0
LABEL_STROKE_WIDTH_MILS = 4.0
LABEL_GAP_MILS = 25.0
LABEL_LENGTH_ALLOWANCE_MILS = 150.0

# StackUpX ids must be GUID strings; Altium's Layer Stack Manager rejects
# anything else. Omitting the id arguments generates random GUIDs, but this
# sample derives deterministic uuid5 ids so regenerated output stays stable.
SAMPLE_ID_NAMESPACE = uuid.UUID("fea0ec73-7811-4d52-8abf-2a9ac93df1b5")


def _sample_guid(*parts: object) -> str:
    return str(uuid.uuid5(SAMPLE_ID_NAMESPACE, "|".join(str(part) for part in parts)))


def _relative_to_examples(path: Path) -> str:
    try:
        return path.resolve().relative_to(EXAMPLES_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _signal_layer_refs() -> tuple[PcbLayerRef, ...]:
    return (
        PcbLayerRef.top(),
        *(PcbLayerRef.mid_layer(number) for number in range(1, SIGNAL_LAYER_COUNT - 1)),
        PcbLayerRef.bottom(),
    )


def _saved_layer_id(ref: PcbLayerRef) -> int:
    if ref.v7_saved_layer_id is None:
        raise RuntimeError(f"{ref.token} does not have a V7 saved-layer id")
    return ref.v7_saved_layer_id


def _signal_layer_name(signal_index: int) -> str:
    if signal_index == 0:
        return "Top Layer"
    if signal_index == SIGNAL_LAYER_COUNT - 1:
        return "Bottom Layer"
    return f"SIG{signal_index + 1:02d}"


def _signal_layer(signal_index: int) -> StackupXLayer:
    return StackupXLayer(
        id=_sample_guid("layer-copper", f"{signal_index + 1:03d}"),
        type_id=StackupXLayerType.COPPER_SIGNAL,
        name=_signal_layer_name(signal_index),
        is_shared=True,
        is_enabled=True,
        is_stiffener=None,
        is_adhesive=None,
        properties=(
            StackupXProperty("Thickness", "LengthValue", "1.4mil"),
            StackupXProperty("ComponentPlacement", "", "None"),
        ),
        raw_attributes=(),
    )


def _dielectric_layer(index: int) -> StackupXLayer:
    return StackupXLayer(
        id=_sample_guid("layer-dielectric", f"{index:03d}"),
        type_id=StackupXLayerType.PREPREG,
        name=f"Dielectric {index}",
        is_shared=True,
        is_enabled=True,
        is_stiffener=None,
        is_adhesive=None,
        properties=(
            StackupXProperty("Thickness", "LengthValue", "4mil"),
            StackupXProperty("DielectricConstant", "DimensionlessValue", "4.200"),
            StackupXProperty("Material", "String", "FR-4"),
        ),
        raw_attributes=(),
    )


def _overlay_layer(side: str) -> StackupXLayer:
    # Altium-authored stackups carry silkscreen as overlay layers with no properties.
    return StackupXLayer(
        id=_sample_guid("layer-overlay", side),
        type_id=StackupXLayerType.OVERLAY,
        name=f"{side} Overlay",
        is_shared=True,
        is_enabled=True,
        is_stiffener=None,
        is_adhesive=None,
        properties=(),
        raw_attributes=(),
    )


def _solder_mask_layer(side: str) -> StackupXLayer:
    return StackupXLayer(
        id=_sample_guid("layer-solder", side),
        type_id=StackupXLayerType.SOLDER_MASK,
        name=f"{side} Solder",
        is_shared=True,
        is_enabled=True,
        is_stiffener=None,
        is_adhesive=None,
        properties=(
            StackupXProperty("Thickness", "LengthValue", "0.4mil"),
            StackupXProperty("Material", "String", "Solder Resist"),
            StackupXProperty("DielectricConstant", "DimensionlessValue", "3.5"),
            StackupXProperty("CoverlayExpansion", "LengthValue", "0mil"),
        ),
        raw_attributes=(),
    )


def _core_stack_layers() -> tuple[StackupXLayer, ...]:
    layers: list[StackupXLayer] = []
    for signal_index in range(SIGNAL_LAYER_COUNT):
        layers.append(_signal_layer(signal_index))
        if signal_index < SIGNAL_LAYER_COUNT - 1:
            layers.append(_dielectric_layer(signal_index + 1))
    return tuple(layers)


def _build_stackupx() -> AltiumStackupXDocument:
    core_layers = _core_stack_layers()
    # Match Altium's native ordering: overlay (silkscreen) and solder mask wrap
    # the copper/dielectric core on both sides.
    layers = (
        _overlay_layer("Top"),
        _solder_mask_layer("Top"),
        *core_layers,
        _solder_mask_layer("Bottom"),
        _overlay_layer("Bottom"),
    )
    stack = StackupXStack(
        id=_sample_guid("stack-main"),
        name="AD26 128 Signal Proof",
        stack_type="0",
        is_flex=False,
        is_symmetric=False,
        template_id="",
        layers=layers,
        via_spans=(
            StackupXSpan(
                id=_sample_guid("span-through"),
                auto_name="Top Layer-Bottom Layer",
                span_type="Layer",
                start_layer_id=core_layers[0].id,
                stop_layer_id=core_layers[-1].id,
                raw_attributes=(),
            ),
            StackupXSpan(
                id=_sample_guid("span-mid31-mid126"),
                auto_name="Mid31-Mid126",
                span_type="Layer",
                start_layer_id=core_layers[2 * 31].id,
                stop_layer_id=core_layers[2 * 126].id,
                raw_attributes=(),
            ),
        ),
        drill_spans=(),
        raw_attributes=(),
    )
    return AltiumStackupXDocument(
        serializer_version="1.1.0.0",
        version="2.1.0.0",
        id=_sample_guid("stackup-doc-128-signal-proof"),
        revision_id=_sample_guid("stackup-revision-128-signal-proof"),
        revision_date="2026-07-29T00:00:00.0000000Z",
        features=(
            StackupXFeature(id=StackupXFeatureId.STANDARD_STACKUP, name="Standard"),
        ),
        stackup_attributes=(),
        stacks=(stack,),
        branches=(),
    )


def _board_size_mils() -> tuple[float, float]:
    width = (
        TRACK_START_X_MILS
        + TRACK_PITCH_MILS * (SIGNAL_LAYER_COUNT - 1)
        + BOARD_MARGIN_MILS
    )
    height = (
        TRACK_START_Y_MILS
        + TRACK_LENGTH_MILS
        + LABEL_GAP_MILS
        + LABEL_LENGTH_ALLOWANCE_MILS
        + BOARD_MARGIN_MILS
    )
    return width, height


def _add_track_row(builder: PcbDocBuilder) -> None:
    for index, ref in enumerate(_signal_layer_refs()):
        x_mils = TRACK_START_X_MILS + TRACK_PITCH_MILS * index
        builder.add_track(
            (x_mils, TRACK_START_Y_MILS),
            (x_mils, TRACK_START_Y_MILS + TRACK_LENGTH_MILS),
            width_mils=TRACK_WIDTH_MILS,
            layer=ref,
        )


def _add_label_row(builder: PcbDocBuilder) -> None:
    # One label per track on the same copper layer it describes, rotated 90
    # degrees so the text flows in +Y above the track end and neighboring
    # labels cannot overlap.
    label_y = TRACK_START_Y_MILS + TRACK_LENGTH_MILS + LABEL_GAP_MILS
    for index, ref in enumerate(_signal_layer_refs()):
        x_mils = TRACK_START_X_MILS + TRACK_PITCH_MILS * index
        builder.add_text(
            text=f"L{index + 1:03d}",
            position_mils=(x_mils + LABEL_HEIGHT_MILS / 2.0, label_y),
            height_mils=LABEL_HEIGHT_MILS,
            layer=ref,
            rotation_degrees=90.0,
            stroke_width_mils=LABEL_STROKE_WIDTH_MILS,
        )


def _stack_layer_id(layer: AltiumStackLayer) -> int:
    layer_id = getattr(layer, "layer_id", None)
    if layer_id is None:
        raise RuntimeError(f"{getattr(layer, 'display_name', '<unnamed>')} has no id")
    return int(layer_id)


def _copper_stack_layers(
    stack: AltiumLayerStackDocument,
) -> tuple[AltiumStackLayer, ...]:
    return tuple(
        layer for layer in stack.physical_stacks[0].layers if layer.family == "copper"
    )


def _signal_layer_rows(stack: AltiumLayerStackDocument) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, (layer, expected_ref) in enumerate(
        zip(_copper_stack_layers(stack), _signal_layer_refs(), strict=True)
    ):
        rows.append(
            {
                "index": index,
                "token": expected_ref.token,
                "display_name": str(layer.display_name),
                "legacy_layer_id": expected_ref.legacy_layer_id,
                "v7_saved_layer_id": _stack_layer_id(layer),
                "expected_v7_saved_layer_id": _saved_layer_id(expected_ref),
            }
        )
    return rows


def _display_names_by_token(
    signal_layers: list[dict[str, object]],
) -> dict[str, str]:
    return {str(row["token"]): str(row["display_name"]) for row in signal_layers}


def _readback_tracks(
    pcbdoc: AltiumPcbDoc,
    *,
    signal_layers: list[dict[str, object]],
) -> list[dict[str, object]]:
    display_names = _display_names_by_token(signal_layers)
    rows: list[dict[str, object]] = []
    for index, (track, expected_ref) in enumerate(
        zip(pcbdoc.tracks, _signal_layer_refs(), strict=True)
    ):
        actual_ref = track.layer_ref()
        rows.append(
            {
                "index": index,
                "token": actual_ref.token,
                "expected_token": expected_ref.token,
                "stack_display_name": display_names[actual_ref.token],
                "expected_stack_display_name": display_names[expected_ref.token],
                "legacy_layer_id": int(track.layer),
                "v7_saved_layer_id": int(track.v7_layer_id),
                "expected_v7_saved_layer_id": _saved_layer_id(expected_ref),
                "matches_expected": actual_ref == expected_ref
                and int(track.v7_layer_id) == _saved_layer_id(expected_ref),
            }
        )
    return rows


def _stack_summary(stack: AltiumLayerStackDocument) -> dict[str, object]:
    copper_layers = _copper_stack_layers(stack)
    resolved = cast(ResolvedLayerStack, stack.to_resolved_layer_stack())
    return {
        "physical_copper_layer_count": len(copper_layers),
        "inner_signal_layer_count": len(resolved.inner_signal_layers),
        "copper_layer_names": [layer.display_name for layer in copper_layers],
        "inner_signal_layers": list(resolved.inner_signal_layers),
    }


def _sentinel_saved_ids() -> dict[str, int]:
    return {
        ref.token: _saved_layer_id(ref)
        for ref in (
            PcbLayerRef.top(),
            PcbLayerRef.mid_layer(31),
            PcbLayerRef.mid_layer(126),
            PcbLayerRef.bottom(),
        )
    }


def _write_manifest(
    *,
    stackupx_path: Path,
    pcbdoc_path: Path,
    svg_path: Path,
    manifest_path: Path,
    pcbdoc: AltiumPcbDoc,
    stack: AltiumLayerStackDocument,
) -> Path:
    signal_layers = _signal_layer_rows(stack)
    tracks = _readback_tracks(pcbdoc, signal_layers=signal_layers)
    manifest = {
        "schema": "altium_monkey.examples.pcbdoc_v7_128_signal_track_row.a0",
        "signal_layer_count": SIGNAL_LAYER_COUNT,
        "track_count": len(tracks),
        "label_count": sum(1 for _ in pcbdoc.texts),
        "all_label_layers_match_tracks": all(
            text.layer_ref() == expected_ref
            for text, expected_ref in zip(
                pcbdoc.texts, _signal_layer_refs(), strict=True
            )
        ),
        "track_geometry_mils": {
            "length": TRACK_LENGTH_MILS,
            "width": TRACK_WIDTH_MILS,
            "pitch": TRACK_PITCH_MILS,
        },
        "outputs": {
            "pcbdoc": _relative_to_examples(pcbdoc_path),
            "stackupx": _relative_to_examples(stackupx_path),
            "svg": _relative_to_examples(svg_path),
            "manifest": _relative_to_examples(manifest_path),
        },
        "stackupx_generated": True,
        "stackupx_reimported": True,
        "stack": _stack_summary(stack),
        "signal_layers": signal_layers,
        "sentinel_saved_ids": _sentinel_saved_ids(),
        "tracks": tracks,
        "all_track_tokens_match": all(
            row["token"] == row["expected_token"] for row in tracks
        ),
        "all_track_saved_ids_match": all(
            row["v7_saved_layer_id"] == row["expected_v7_saved_layer_id"]
            for row in tracks
        ),
        "all_tracks_match_expected": all(row["matches_expected"] for row in tracks),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_sample(
    output_dir: Path = OUTPUT_DIR,
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stackupx_path = output_dir / OUTPUT_STACKUPX.name
    pcbdoc_path = output_dir / OUTPUT_PCBDOC.name
    svg_path = output_dir / OUTPUT_SVG.name
    manifest_path = output_dir / OUTPUT_MANIFEST.name

    _build_stackupx().write(stackupx_path)
    stack = AltiumLayerStackDocument.from_stackupx(stackupx_path)
    support = stack.native_pcbdoc_write_support()
    if not support.supported:
        details = "; ".join(support.reasons) or "unsupported stack shape"
        raise RuntimeError(f"128-signal StackUpX cannot author PcbDoc: {details}")

    board_width, board_height = _board_size_mils()
    builder = PcbDocBuilder()
    builder.set_layer_stack_document(stack)
    builder.set_outline_rectangle_mils(0.0, 0.0, board_width, board_height)
    builder.set_origin_to_outline_lower_left()
    _add_track_row(builder)
    _add_label_row(builder)
    builder.save(pcbdoc_path)

    pcbdoc = AltiumPcbDoc.from_file(pcbdoc_path)
    readback_stack = AltiumLayerStackDocument.from_pcbdoc(pcbdoc)
    signal_layers = _signal_layer_rows(readback_stack)
    tracks = _readback_tracks(pcbdoc, signal_layers=signal_layers)
    if len(tracks) != SIGNAL_LAYER_COUNT or not all(
        row["matches_expected"] for row in tracks
    ):
        raise RuntimeError("128-signal track layer readback did not match")

    svg_path.write_text(
        pcbdoc.to_svg(
            PcbSvgRenderOptions(
                visible_layers=tuple(_signal_layer_refs()),
                include_render_layer_metadata=True,
            )
        ),
        encoding="utf-8",
    )
    _write_manifest(
        stackupx_path=stackupx_path,
        pcbdoc_path=pcbdoc_path,
        svg_path=svg_path,
        manifest_path=manifest_path,
        pcbdoc=pcbdoc,
        stack=readback_stack,
    )
    return pcbdoc_path, stackupx_path, svg_path, manifest_path


def main() -> None:
    pcbdoc_path, stackupx_path, svg_path, manifest_path = build_sample()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"Wrote {pcbdoc_path}")
    print(f"Wrote {stackupx_path}")
    print(f"Wrote {svg_path}")
    print(f"Wrote {manifest_path}")
    print(f"Signal layers: {manifest['signal_layer_count']}")
    print(f"Tracks: {manifest['track_count']}")
    print(f"All tracks match expected layers: {manifest['all_tracks_match_expected']}")


if __name__ == "__main__":
    main()
