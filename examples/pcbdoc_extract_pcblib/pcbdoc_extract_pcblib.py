from __future__ import annotations

import json
import shutil
from pathlib import Path

from altium_monkey import AltiumDesign


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
PROJECT_FILE = (
    EXAMPLES_ROOT / "assets" / "projects" / "rt_super_c1" / "RT_SUPER_C1.PrjPcb"
)
OUTPUT_DIR = SAMPLE_DIR / "output"
COMBINED_DIR = OUTPUT_DIR / "combined"
SPLIT_DIR = OUTPUT_DIR / "split"
COMBINED_PCBLIB = COMBINED_DIR / "RT_SUPER_C1_extracted.PcbLib"
MANIFEST_PATH = OUTPUT_DIR / "pcblib_extraction_manifest.json"


def _examples_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_ROOT)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def extract_project_footprints() -> dict[str, object]:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    COMBINED_DIR.mkdir(parents=True, exist_ok=True)
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    pcblib = pcbdoc.extract_pcblib(COMBINED_PCBLIB)
    split_outputs = pcblib.split(SPLIT_DIR)

    footprints = [
        {
            "name": footprint.name,
            "split_pcblib": _sample_relative(split_outputs[footprint.name]),
            "pad_count": len(footprint.pads),
            "track_count": len(footprint.tracks),
            "arc_count": len(footprint.arcs),
            "component_body_count": len(footprint.component_bodies),
        }
        for footprint in pcblib.footprints
    ]

    manifest: dict[str, object] = {
        "project": _examples_relative(PROJECT_FILE),
        "pcbdoc": _examples_relative(Path(pcbdoc.filepath or "")),
        "combined_pcblib": _sample_relative(COMBINED_PCBLIB),
        "combined_byte_count": COMBINED_PCBLIB.stat().st_size,
        "split_dir": _sample_relative(SPLIT_DIR),
        "footprint_count": len(footprints),
        "footprints": footprints,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = extract_project_footprints()
    print(f"Loaded project: {_examples_relative(PROJECT_FILE)}")
    print(f"Extracted footprints: {manifest['footprint_count']}")
    print(f"Wrote combined PcbLib: {_sample_relative(COMBINED_PCBLIB)}")
    print(f"Wrote split PcbLib folder: {_sample_relative(SPLIT_DIR)}")
    print(f"Wrote manifest: {_sample_relative(MANIFEST_PATH)}")


if __name__ == "__main__":
    main()
