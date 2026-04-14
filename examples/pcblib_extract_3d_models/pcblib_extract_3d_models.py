import json
import shutil
from pathlib import Path

from altium_monkey import AltiumPcbLib

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
SOURCE_PCBLIB_PATH = EXAMPLES_ROOT / "assets" / "pcblib" / "R0603_0.55MM_MD.PcbLib"
OUTPUT_DIR = SAMPLE_DIR / "output" / "extracted_models"
MANIFEST_PATH = SAMPLE_DIR / "output" / "models.json"


def extract_models() -> list[Path]:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    pcblib = AltiumPcbLib.from_file(SOURCE_PCBLIB_PATH)
    extracted_paths = pcblib.extract_embedded_models(OUTPUT_DIR)

    manifest = {
        "source_pcblib": SOURCE_PCBLIB_PATH.relative_to(EXAMPLES_ROOT).as_posix(),
        "model_count": len(extracted_paths),
        "models": [
            {
                "path": path.relative_to(SAMPLE_DIR).as_posix(),
                "byte_count": path.stat().st_size,
            }
            for path in extracted_paths
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return extracted_paths


def main() -> None:
    extracted_paths = extract_models()
    print(f"Extracted {len(extracted_paths)} model(s) to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
