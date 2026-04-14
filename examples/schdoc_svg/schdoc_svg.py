from __future__ import annotations

import json
import shutil
from pathlib import Path

from altium_monkey import AltiumSchDoc
from altium_monkey.altium_prjpcb import AltiumPrjPcb


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
PROJECT_DIR = ASSETS_DIR / "projects" / "hydroscope"
INPUT_PRJPCB = PROJECT_DIR / "Hydroscope.PrjPcb"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_MANIFEST = OUTPUT_DIR / "svg_manifest.json"


def _project_relative(path: Path) -> str:
    return str(path.relative_to(PROJECT_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _render_svgs(
    schdoc_paths: list[Path],
    project_parameters: dict[str, str],
) -> list[dict[str, object]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, object]] = []

    for schdoc_path in schdoc_paths:
        schdoc = AltiumSchDoc(schdoc_path)
        output_path = OUTPUT_DIR / f"{schdoc_path.stem}.svg"
        output_path.write_text(
            schdoc.to_svg(project_parameters=project_parameters),
            encoding="utf-8",
        )
        written.append(
            {
                "source": _project_relative(schdoc_path),
                "svg": _sample_relative(output_path),
                "byte_count": output_path.stat().st_size,
            }
        )

    return written


def main() -> None:
    project = AltiumPrjPcb(INPUT_PRJPCB)
    schdoc_paths = project.get_reachable_schdoc_paths()
    if not schdoc_paths:
        raise RuntimeError(f"No schematic documents found in {INPUT_PRJPCB.name}")

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    project_parameters = dict(project.parameters)
    svgs = _render_svgs(schdoc_paths, project_parameters)

    manifest = {
        "project": str(INPUT_PRJPCB.relative_to(EXAMPLES_DIR)).replace("\\", "/"),
        "schematic_documents": [_project_relative(path) for path in schdoc_paths],
        "project_parameters": project_parameters,
        "variant_handling": "not_applied",
        "hierarchical_channel_handling": "not_applied",
        "svg_count": len(svgs),
        "svgs": svgs,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Loaded project: {INPUT_PRJPCB.relative_to(EXAMPLES_DIR)}")
    print(f"Schematic documents: {len(schdoc_paths)}")
    print("Variant handling: not applied")
    print(f"SVG files written: {manifest['svg_count']}")
    print(f"Wrote manifest: {OUTPUT_MANIFEST.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
