from __future__ import annotations

import json
import shutil
from pathlib import Path, PureWindowsPath

from altium_monkey import AltiumSchDoc


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "projects" / "hydroscope" / "TOP_LEVEL.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_IMAGES_DIR = OUTPUT_DIR / "extracted_images"
OUTPUT_MANIFEST = OUTPUT_DIR / "image_manifest.json"


def _source_basename(filename: str) -> str:
    return PureWindowsPath(filename).name or Path(filename).name or ""


def _image_summary(image: object, output_path: Path, index: int) -> dict[str, object]:
    bounds = image.bounds_mils
    return {
        "index": index,
        "source_name": _source_basename(getattr(image, "filename", "")),
        "embedded": bool(getattr(image, "embedded", False)),
        "detected_format": getattr(image, "image_format", None),
        "exported_file": str(output_path.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "byte_count": output_path.stat().st_size,
        "bounds_mils": {
            "x1": bounds.x1_mils,
            "y1": bounds.y1_mils,
            "x2": bounds.x2_mils,
            "y2": bounds.y2_mils,
        },
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if OUTPUT_IMAGES_DIR.exists():
        shutil.rmtree(OUTPUT_IMAGES_DIR)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    image_records = list(schdoc.images)
    embedded_image_records = [
        image for image in image_records if getattr(image, "image_data", None)
    ]
    written_paths = schdoc.extract_embedded_images(OUTPUT_IMAGES_DIR)
    if not written_paths:
        raise RuntimeError(f"No embedded images found in {INPUT_SCHDOC.name}")

    manifest = {
        "source": str(INPUT_SCHDOC.relative_to(EXAMPLES_DIR)).replace("\\", "/"),
        "image_record_count": len(image_records),
        "embedded_storage_entry_count": len(schdoc.embedded_images),
        "extracted_image_count": len(written_paths),
        "images": [
            _image_summary(image, output_path, index)
            for index, (image, output_path) in enumerate(
                zip(embedded_image_records, written_paths, strict=True),
                start=1,
            )
        ],
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Loaded: {INPUT_SCHDOC.relative_to(EXAMPLES_DIR)}")
    print(f"Image records: {len(image_records)}")
    print(f"Embedded storage entries: {len(schdoc.embedded_images)}")
    print(f"Extracted images: {len(written_paths)}")
    print(f"Wrote images: {OUTPUT_IMAGES_DIR.relative_to(SAMPLE_DIR)}")
    print(f"Wrote manifest: {OUTPUT_MANIFEST.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
