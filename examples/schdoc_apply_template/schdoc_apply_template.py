from pathlib import Path

from altium_monkey import AltiumSchDoc


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "projects" / "bunny_brain" / "bunny_brain_D.SchDoc"
TEMPLATE_SCHDOT = ASSETS_DIR / "templates" / "Wavenumber__ANSI_D.SchDot"
TEMPLATE_REF = TEMPLATE_SCHDOT.relative_to(EXAMPLES_DIR)
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "bunny_brain_D_with_applied_template.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    template_before = schdoc.get_template()
    removed_count = schdoc.clear_template()
    inserted_count = schdoc.apply_template(
        TEMPLATE_SCHDOT,
        clear_existing=False,
        template_filename=TEMPLATE_REF,
    )
    template_after = schdoc.get_template()

    schdoc.save(OUTPUT_SCHDOC)

    print(
        f"Input template: {template_before.filename if template_before else '<none>'}"
    )
    print(f"Removed template records: {removed_count}")
    print(f"Applied template: {TEMPLATE_REF}")
    print(f"Inserted template records: {inserted_count}")
    print(f"Output template: {template_after.filename if template_after else '<none>'}")
    print(f"Show template graphics: {schdoc.sheet.show_template_graphics}")
    print(f"Template file name: {schdoc.sheet.template_filename!r}")
    print(f"Wrote SchDoc: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
