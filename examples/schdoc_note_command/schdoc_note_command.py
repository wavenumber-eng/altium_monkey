from __future__ import annotations

from pathlib import Path

from altium_monkey import AltiumSchDoc, SchFontSpec


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "note_commands_input.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "note_commands_output.SchDoc"

FONT_COMMAND_TEXT = "change my font to courier 12"
DELETE_COMMAND_TEXT = "delete me"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    notes_before = len(schdoc.notes)
    updated_count = 0
    removed_count = 0

    for note in list(schdoc.notes):
        normalized_text = note.text.strip().lower()
        if normalized_text == FONT_COMMAND_TEXT:
            note.font = SchFontSpec(name="Courier New", size=12)
            updated_count += 1
        elif normalized_text == DELETE_COMMAND_TEXT:
            if schdoc.remove_object(note):
                removed_count += 1

    if updated_count != 1:
        raise RuntimeError(
            f"Expected to update exactly one note, but updated {updated_count}"
        )
    if removed_count != 1:
        raise RuntimeError(
            f"Expected to remove exactly one note, but removed {removed_count}"
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    notes_after = len(reopened.notes)
    if notes_after != 1:
        raise RuntimeError(f"Expected exactly one note after save, found {notes_after}")

    surviving_note = reopened.notes[0]
    surviving_font = reopened.font_manager.get_font_info(surviving_note.font_id) or {}

    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Notes before: {notes_before}")
    print(f"Updated notes: {updated_count}")
    print(f"Removed notes: {removed_count}")
    print(f"Notes after: {notes_after}")
    print(f"Surviving note text: {surviving_note.text}")
    print(
        "Surviving note font: "
        f"{surviving_font.get('name', 'Unknown')} {surviving_font.get('size', '?')}"
    )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
