from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from altium_monkey import AltiumPcbLib, PcbSvgRenderOptions


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_PCBLIB = ASSETS_DIR / "pcblib" / "RT_SUPER_C1.PcbLib"
OUTPUT_DIR = SAMPLE_DIR / "output"
FOOTPRINT_SVG_DIR = OUTPUT_DIR / "footprints"
LAYER_SVG_DIR = OUTPUT_DIR / "layers"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcblib_footprint_svg_manifest.json"
SCALE_FACTOR = 10.0


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "footprint"


def _footprint_svg_stem(index: int, footprint_name: str) -> str:
    return f"{index:03d}_{_safe_filename_part(footprint_name)}"


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    FOOTPRINT_SVG_DIR.mkdir(parents=True, exist_ok=True)
    LAYER_SVG_DIR.mkdir(parents=True, exist_ok=True)


def _render_options() -> PcbSvgRenderOptions:
    return PcbSvgRenderOptions(
        svg_display_scale=SCALE_FACTOR,
        include_metadata=False,
        show_board_outline=False,
        drill_hole_mode="overlay",
        drill_hole_overlay_plated_color="#00A651",
        drill_hole_overlay_non_plated_color="#00A651",
        drill_hole_overlay_opacity=0.75,
        drill_hole_overlay_outline=True,
    )


def _render_footprint_svgs(pcblib: AltiumPcbLib) -> list[dict[str, object]]:
    footprint_entries: list[dict[str, object]] = []
    options = _render_options()

    for footprint_index, footprint in enumerate(pcblib.footprints, start=1):
        safe_name = _footprint_svg_stem(footprint_index, footprint.name)
        composed_svg_path = FOOTPRINT_SVG_DIR / f"{safe_name}.svg"
        composed_svg_path.write_text(
            footprint.to_svg(options=options),
            encoding="utf-8",
        )

        layer_entries: list[dict[str, object]] = []
        for layer_name, layer_svg in footprint.to_layer_svgs(options=options).items():
            layer_svg_path = LAYER_SVG_DIR / f"{safe_name}_{layer_name}.svg"
            layer_svg_path.write_text(layer_svg, encoding="utf-8")
            layer_entries.append(
                {
                    "layer": layer_name,
                    "svg": _sample_relative(layer_svg_path),
                    "byte_count": layer_svg_path.stat().st_size,
                }
            )

        footprint_entries.append(
            {
                "index": footprint_index,
                "name": footprint.name,
                "pads": len(footprint.pads),
                "tracks": len(footprint.tracks),
                "arcs": len(footprint.arcs),
                "texts": len(footprint.texts),
                "composed_svg": _sample_relative(composed_svg_path),
                "composed_byte_count": composed_svg_path.stat().st_size,
                "layers": layer_entries,
            }
        )

    return footprint_entries


def main() -> None:
    _reset_output_dirs()

    pcblib = AltiumPcbLib.from_file(INPUT_PCBLIB)
    if not pcblib.footprints:
        raise RuntimeError(f"No footprints found in {INPUT_PCBLIB.name}")

    footprint_entries = _render_footprint_svgs(pcblib)
    svg_count = len(footprint_entries) + sum(
        len(entry["layers"]) for entry in footprint_entries
    )

    manifest = {
        "input_pcblib": _example_relative(INPUT_PCBLIB),
        "scale_factor": SCALE_FACTOR,
        "footprint_count": len(footprint_entries),
        "svg_count": svg_count,
        "footprints": footprint_entries,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Input PcbLib: {_example_relative(INPUT_PCBLIB)}")
    print(f"Footprints rendered: {manifest['footprint_count']}")
    print(f"SVG files written: {manifest['svg_count']}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
