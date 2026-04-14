from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    SchFontSpec,
    SchHorizontalAlign,
    SchPointMils,
    SchRectMils,
    make_sch_note,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_note.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    note_before = len(schdoc.notes)

    note_bounds = SchRectMils.from_points(
        SchPointMils.from_mils(1000, 3000),
        SchPointMils.from_mils(3000, 4000),
    )
    note = make_sch_note(
        bounds_mils=note_bounds,
        text="a note from altium-monkey",
        author="altium-monkey",
        font=SchFontSpec(name="Arial", size=10),
        border_color=ColorValue.from_hex("#000000"),
        fill_color=ColorValue.from_hex("#CCFFFF"),
        text_color=ColorValue.from_rgb(0, 0, 0),
        alignment=SchHorizontalAlign.LEFT,
        word_wrap=True,
        clip_to_rect=True,
        text_margin_mils=5,
        collapsed=False,
    )
    schdoc.add_object(note)
    schdoc.save(OUTPUT_SCHDOC)

    updated = AltiumSchDoc(OUTPUT_SCHDOC)
    inserted_note = updated.notes[-1]
    font_info = updated.font_manager.get_font_info(inserted_note.font_id)

    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Notes before: {note_before}")
    print(f"Notes after: {len(updated.notes)}")
    print(f"Note author: {inserted_note.author}")
    print(f"Note font: {font_info['name']} {font_info['size']}")
    print(
        "Public bounds helper mils: "
        f"({note_bounds.x1_mils:.0f}, {note_bounds.y1_mils:.0f})"
        f" -> ({note_bounds.x2_mils:.0f}, {note_bounds.y2_mils:.0f})"
    )
    print(
        "Note bounds mils: "
        f"({inserted_note.location.x_mils:.0f}, {inserted_note.location.y_mils:.0f})"
        f" -> ({inserted_note.corner.x_mils:.0f}, {inserted_note.corner.y_mils:.0f})"
    )
    print(f"Note border color: {ColorValue.from_win32(inserted_note.color or 0).hex}")
    print(
        f"Note fill color: {ColorValue.from_win32(inserted_note.area_color or 0).hex}"
    )
    print(
        f"Note text color: {ColorValue.from_win32(inserted_note.text_color or 0).hex}"
    )
    print(f"Note IndexInSheet: {inserted_note.index_in_sheet}")
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
