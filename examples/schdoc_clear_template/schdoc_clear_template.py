from pathlib import Path

from altium_monkey import AltiumSchDoc


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "projects" / "bunny_brain" / "bunny_brain_D.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "bunny_brain_D_without_template.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    template_before = schdoc.get_template()
    removed_count = schdoc.clear_template()
    template_after = schdoc.get_template()

    schdoc.save(OUTPUT_SCHDOC)

    print(
        f"Input template: {template_before.filename if template_before else '<none>'}"
    )
    print(f"Removed template records: {removed_count}")
    print(f"Has template after clear: {template_after is not None}")
    print(f"Show template graphics: {schdoc.sheet.show_template_graphics}")
    print(f"Template file name: {schdoc.sheet.template_filename!r}")
    print(f"Wrote SchDoc: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
