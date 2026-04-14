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
MODELS_DIR = OUTPUT_DIR / "embedded_3d_models"
MANIFEST_PATH = OUTPUT_DIR / "models.json"


def _examples_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_ROOT)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def extract_models() -> dict[str, object]:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    design = AltiumDesign.from_prjpcb(PROJECT_FILE)
    pcbdoc = design.load_pcbdoc()
    extracted_paths = pcbdoc.extract_embedded_models(MODELS_DIR)
    extracted_by_name = {path.name: path for path in extracted_paths}

    models = []
    for index, (model, compressed_payload) in enumerate(
        pcbdoc.get_embedded_model_entries()
    ):
        expected_prefix = f"{index:03d}__"
        path = next(
            (
                candidate
                for name, candidate in extracted_by_name.items()
                if name.startswith(expected_prefix)
            ),
            None,
        )
        models.append(
            {
                "name": model.name,
                "id": str(model.id),
                "checksum": int(model.checksum) & 0xFFFFFFFF,
                "compressed_byte_count": len(compressed_payload),
                "path": _sample_relative(path) if path is not None else None,
                "byte_count": path.stat().st_size if path is not None else 0,
            }
        )

    manifest: dict[str, object] = {
        "project": _examples_relative(PROJECT_FILE),
        "pcbdoc": _examples_relative(Path(pcbdoc.filepath or "")),
        "model_count": len(extracted_paths),
        "models": models,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    manifest = extract_models()
    print(f"Loaded project: {_examples_relative(PROJECT_FILE)}")
    print(f"Extracted embedded 3D models: {manifest['model_count']}")
    print(f"Wrote model folder: {_sample_relative(MODELS_DIR)}")
    print(f"Wrote manifest: {_sample_relative(MANIFEST_PATH)}")


if __name__ == "__main__":
    main()
