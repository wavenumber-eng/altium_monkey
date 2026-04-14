from pathlib import Path

from altium_monkey import AltiumSchDoc, AltiumSchParameter


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "projects" / "m2_emmc" / "m2_emmc.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOT = OUTPUT_DIR / "m2_emmc_extracted_template.SchDot"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    source_template = schdoc.get_template()
    extracted_count = schdoc.extract_template(OUTPUT_SCHDOT)

    extracted_template = AltiumSchDoc(OUTPUT_SCHDOT)
    visual_count = len(
        [
            obj
            for obj in extracted_template.all_objects
            if obj is not extracted_template.sheet
            and not isinstance(obj, AltiumSchParameter)
        ]
    )

    print(f"Source SchDoc: {INPUT_SCHDOC.relative_to(EXAMPLES_DIR)}")
    print(
        f"Source template: {source_template.filename if source_template else '<none>'}"
    )
    print(f"Extracted records: {extracted_count}")
    print(f"Extracted visual records: {visual_count}")
    print(
        f"Extracted has Template record: {extracted_template.get_template() is not None}"
    )
    print(f"Wrote SchDot: {OUTPUT_SCHDOT.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
