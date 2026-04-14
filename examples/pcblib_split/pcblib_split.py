from __future__ import annotations

import json
import shutil
from pathlib import Path

from altium_monkey import AltiumPcbLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_PCBLIB = ASSETS_DIR / "pcblib" / "RT_SUPER_C1.PcbLib"
OUTPUT_DIR = SAMPLE_DIR / "output"
SPLIT_DIR = OUTPUT_DIR / "split"
OUTPUT_MANIFEST = OUTPUT_DIR / "pcblib_split_manifest.json"


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)


def split_library() -> dict[str, object]:
    _reset_output_dirs()

    pcblib = AltiumPcbLib.from_file(INPUT_PCBLIB)
    split_outputs = pcblib.split(SPLIT_DIR)

    footprints: list[dict[str, object]] = []
    for footprint in pcblib.footprints:
        split_path = split_outputs[footprint.name]
        footprints.append(
            {
                "name": footprint.name,
                "split_pcblib": _sample_relative(split_path),
                "byte_count": split_path.stat().st_size,
                "pad_count": len(footprint.pads),
                "track_count": len(footprint.tracks),
                "arc_count": len(footprint.arcs),
                "component_body_count": len(footprint.component_bodies),
            }
        )

    manifest: dict[str, object] = {
        "input_pcblib": _example_relative(INPUT_PCBLIB),
        "split_dir": _sample_relative(SPLIT_DIR),
        "footprint_count": len(footprints),
        "footprints": footprints,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = split_library()
    print(f"Input PcbLib: {_example_relative(INPUT_PCBLIB)}")
    print(f"Footprints split: {manifest['footprint_count']}")
    print(f"Wrote split PcbLib folder: {_sample_relative(SPLIT_DIR)}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
