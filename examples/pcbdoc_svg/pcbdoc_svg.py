from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from altium_monkey import AltiumDesign, PcbLayer, PcbSvgRenderOptions
from altium_monkey.altium_resolved_layer_stack import (
    ResolvedLayerStack,
    resolved_layer_stack_from_pcbdoc,
)


SCALE_FACTOR = 10.0
COPPER_LAYER_COLOR = "#000000"
DRILL_HOLE_COLOR = "#00A000"

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
PROJECT_DIR = EXAMPLES_DIR / "assets" / "projects" / "rt_super_c1"
PROJECT_FILE = PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_LAYERS_DIR = OUTPUT_DIR / "layers"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcbdoc_svg_manifest.json"
OUTPUT_BOARD_OUTLINE = OUTPUT_DIR / "board_outline.svg"

DRILLS_LAYER_KEY = "DRILLS"


def _copper_layer_colors() -> dict[PcbLayer, str]:
    return {layer: COPPER_LAYER_COLOR for layer in PcbLayer if layer.is_copper()}


def _examples_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _safe_filename(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return token.strip("_").lower() or "layer"


def _display_name_for_layer(layer: PcbLayer, stack: ResolvedLayerStack) -> str:
    resolved = stack.layer_by_legacy_id(layer.value)
    if resolved is not None:
        return resolved.display_name
    if layer == PcbLayer.MULTI_LAYER:
        return "Multi-Layer"
    return layer.name.replace("_", " ").title()


def resolve_pcb_svg_layer(
    selector: PcbLayer | int | str,
    stack: ResolvedLayerStack,
) -> PcbLayer:
    """
    Resolve common user-facing layer selectors to the PcbLayer enum used by SVG options.
    """
    if isinstance(selector, PcbLayer):
        return selector
    if isinstance(selector, int):
        return PcbLayer(selector)

    raw = str(selector).strip()
    if not raw:
        raise ValueError("Layer selector cannot be empty")
    if raw.isdecimal():
        return PcbLayer(int(raw))

    normalized = re.sub(r"[^A-Za-z0-9]+", "", raw).upper()
    for candidate in (raw.upper(), normalized):
        try:
            return PcbLayer.from_json_name(candidate)
        except ValueError:
            pass

    resolved = stack.layer_by_name(raw) or stack.layer_by_token(raw)
    if resolved is None or resolved.legacy_id is None:
        raise ValueError(f"Unknown PCB layer selector: {selector!r}")
    return PcbLayer(int(resolved.legacy_id))


def _layer_sort_key(layer_key: str) -> tuple[int, str]:
    if layer_key == DRILLS_LAYER_KEY:
        return (10_000, layer_key)
    try:
        return (PcbLayer.from_json_name(layer_key).value, layer_key)
    except ValueError:
        return (9_000, layer_key)


def _layer_output_path(layer_key: str) -> Path:
    if layer_key == DRILLS_LAYER_KEY:
        return OUTPUT_LAYERS_DIR / "layer_drills.svg"
    layer = PcbLayer.from_json_name(layer_key)
    filename = f"layer_{layer.value:02d}_{_safe_filename(layer_key)}.svg"
    return OUTPUT_LAYERS_DIR / filename


def _layer_manifest_entry(
    layer_key: str,
    output_path: Path,
    stack: ResolvedLayerStack,
) -> dict[str, object]:
    if layer_key == DRILLS_LAYER_KEY:
        return {
            "key": DRILLS_LAYER_KEY,
            "display_name": "Drill Holes",
            "legacy_id": None,
            "enum_name": None,
            "selector_examples": [DRILLS_LAYER_KEY],
            "svg": _sample_relative(output_path),
            "byte_count": output_path.stat().st_size,
        }

    layer = PcbLayer.from_json_name(layer_key)
    display_name = _display_name_for_layer(layer, stack)
    return {
        "key": layer_key,
        "display_name": display_name,
        "legacy_id": layer.value,
        "enum_name": layer.name,
        "selector_examples": [
            layer.name,
            layer.value,
            layer_key,
            display_name,
        ],
        "svg": _sample_relative(output_path),
        "byte_count": output_path.stat().st_size,
    }


def _write_svg(path: Path, svg: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def main() -> None:
    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    stack = resolved_layer_stack_from_pcbdoc(pcbdoc)
    project_parameters = design.get_pcb_project_parameters()

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_LAYERS_DIR.mkdir(parents=True, exist_ok=True)

    layer_options = PcbSvgRenderOptions(
        svg_display_scale=SCALE_FACTOR,
        layer_colors=_copper_layer_colors(),
        drill_hole_mode="overlay",
        drill_hole_overlay_plated_color=DRILL_HOLE_COLOR,
        drill_hole_overlay_non_plated_color=DRILL_HOLE_COLOR,
        drill_hole_overlay_opacity=1.0,
    )
    outline_options = PcbSvgRenderOptions(svg_display_scale=SCALE_FACTOR)

    layer_svgs = pcbdoc.to_layer_svgs(
        options=layer_options,
        project_parameters=project_parameters,
    )
    _write_svg(
        OUTPUT_BOARD_OUTLINE,
        pcbdoc.to_board_outline_svg(
            options=outline_options,
            project_parameters=project_parameters,
        ),
    )

    layers: list[dict[str, object]] = []
    for layer_key in sorted(layer_svgs, key=_layer_sort_key):
        output_path = _layer_output_path(layer_key)
        _write_svg(output_path, layer_svgs[layer_key])
        layers.append(_layer_manifest_entry(layer_key, output_path, stack))

    selector_demo_layer = resolve_pcb_svg_layer("Top Layer", stack)
    keepout_layer = resolve_pcb_svg_layer("Keep-Out Layer", stack)

    manifest = {
        "project": _examples_relative(PROJECT_FILE),
        "pcbdoc": _examples_relative(Path(pcbdoc.filepath or "")),
        "scale_factor": SCALE_FACTOR,
        "project_parameters": project_parameters,
        "board_outline_svg": _sample_relative(OUTPUT_BOARD_OUTLINE),
        "render_style": {
            "copper_layer_color": COPPER_LAYER_COLOR,
            "drill_hole_color": DRILL_HOLE_COLOR,
            "drill_hole_mode": "overlay",
        },
        "layer_count": len(layers),
        "layers": layers,
        "selector_demo": {
            "input": "Top Layer",
            "resolved_key": selector_demo_layer.to_json_name(),
            "resolved_legacy_id": selector_demo_layer.value,
            "visible_layers_option": "PcbSvgRenderOptions(visible_layers={PcbLayer.TOP})",
        },
        "keepout": {
            "selector_resolves_to": keepout_layer.to_json_name(),
            "selector_legacy_id": keepout_layer.value,
            "written_for_this_board": any(
                layer["key"] == keepout_layer.to_json_name() for layer in layers
            ),
            "note": (
                "Keep-Out Layer is selectable for SVG export. It is emitted by "
                "automatic per-layer export only when keepout primitives are present."
            ),
        },
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Loaded project: {_examples_relative(PROJECT_FILE)}")
    print(f"Loaded PCB: {Path(pcbdoc.filepath or '').name}")
    print(f"SVG display scale factor: {SCALE_FACTOR:g}")
    print(f"Layer SVG files written: {len(layers)}")
    print(f"Wrote board outline: {_sample_relative(OUTPUT_BOARD_OUTLINE)}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
