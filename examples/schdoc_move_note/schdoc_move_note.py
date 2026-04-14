from __future__ import annotations

from pathlib import Path

from altium_monkey import AltiumSchDoc, SchRectMils


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "note_move_input.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "note_move_output.SchDoc"

TARGET_BOUNDS_MILS = SchRectMils.from_corners_mils(2500, 2500, 4500, 3500)


def _format_bounds(bounds) -> str:
    return (
        f"({bounds.x1_mils:.0f}, {bounds.y1_mils:.0f})"
        f" -> ({bounds.x2_mils:.0f}, {bounds.y2_mils:.0f})"
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    if len(schdoc.notes) != 1:
        raise RuntimeError(
            f"Expected exactly one input note, found {len(schdoc.notes)}"
        )

    note = schdoc.notes[0]
    before_bounds = note.bounds_mils
    note.bounds_mils = TARGET_BOUNDS_MILS

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    moved_note = reopened.notes[0]

    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Bounds before: {_format_bounds(before_bounds)}")
    print(f"Bounds target: {_format_bounds(TARGET_BOUNDS_MILS)}")
    print(f"Bounds after: {_format_bounds(moved_note.bounds_mils)}")
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
