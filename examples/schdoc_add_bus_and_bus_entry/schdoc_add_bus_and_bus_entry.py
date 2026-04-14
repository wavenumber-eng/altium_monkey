from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchPointMils,
    make_sch_bus,
    make_sch_bus_entry,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_bus_and_entries.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    schdoc.add_object(
        make_sch_bus(
            points_mils=[
                SchPointMils.from_mils(2600, 1800),
                SchPointMils.from_mils(2600, 6200),
                SchPointMils.from_mils(3200, 6200),
            ],
            color=ColorValue.from_hex("#000000"),
            line_width=LineWidth.LARGE,
        )
    )

    for start, end in (
        (
            SchPointMils.from_mils(2600, 2600),
            SchPointMils.from_mils(2700, 2700),
        ),
        (
            SchPointMils.from_mils(2600, 3600),
            SchPointMils.from_mils(2700, 3700),
        ),
        (
            SchPointMils.from_mils(2600, 4600),
            SchPointMils.from_mils(2700, 4700),
        ),
    ):
        schdoc.add_object(
            make_sch_bus_entry(
                start_mils=start,
                end_mils=end,
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.SMALL,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Buses written: {len(reopened.buses)}")
    print(f"Bus entries written: {len(reopened.bus_entries)}")
    if reopened.buses:
        bus = reopened.buses[0]
        points_text = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})" for point in bus.points_mils
        )
        print(f"Bus line width: {bus.line_width.name}")
        print(f"Bus points mils: {points_text}")
    for index, entry in enumerate(reopened.bus_entries, start=1):
        print(
            f"Entry {index}: "
            f"start=({entry.location_mils.x_mils:.0f}, {entry.location_mils.y_mils:.0f}), "
            f"end=({entry.corner_mils.x_mils:.0f}, {entry.corner_mils.y_mils:.0f}), "
            f"width={entry.line_width.name}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
