from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchRectMils,
    make_sch_compile_mask,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_compile_masks.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: visible compile-mask sizes and fills.
    schdoc.add_object(
        make_sch_compile_mask(
            bounds_mils=SchRectMils.from_corners_mils(900, 5000, 2500, 6400),
            color=ColorValue.from_hex("#008000"),
            fill_color=ColorValue.from_hex("#DFFFD5"),
            line_width=LineWidth.SMALLEST,
        )
    )
    schdoc.add_object(
        make_sch_compile_mask(
            bounds_mils=SchRectMils.from_corners_mils(3000, 5000, 4600, 6400),
            color=ColorValue.from_hex("#008000"),
            fill_color=ColorValue.from_hex("#C7F5C1"),
            line_width=LineWidth.SMALL,
        )
    )
    schdoc.add_object(
        make_sch_compile_mask(
            bounds_mils=SchRectMils.from_corners_mils(5100, 5000, 6700, 6400),
            color=ColorValue.from_hex("#008000"),
            fill_color=ColorValue.from_hex("#A6E89D"),
            line_width=LineWidth.MEDIUM,
        )
    )

    # Row 2: collapsed state and a taller mask region.
    schdoc.add_object(
        make_sch_compile_mask(
            bounds_mils=SchRectMils.from_corners_mils(1400, 2200, 3000, 3600),
            color=ColorValue.from_hex("#008000"),
            fill_color=ColorValue.from_hex("#DFFFD5"),
            line_width=LineWidth.SMALL,
            collapsed=True,
        )
    )
    schdoc.add_object(
        make_sch_compile_mask(
            bounds_mils=SchRectMils.from_corners_mils(4300, 1800, 6200, 3800),
            color=ColorValue.from_hex("#006600"),
            fill_color=ColorValue.from_hex("#C7F5C1"),
            line_width=LineWidth.LARGE,
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Compile masks written: {len(reopened.compile_masks)}")
    for index, compile_mask in enumerate(reopened.compile_masks, start=1):
        bounds = compile_mask.bounds_mils.normalized()
        print(
            "Compile mask "
            f"{index}: "
            f"bounds=({bounds.x1_mils:.0f}, {bounds.y1_mils:.0f}) -> "
            f"({bounds.x2_mils:.0f}, {bounds.y2_mils:.0f}), "
            f"width={compile_mask.line_width.name}, "
            f"collapsed={compile_mask.is_collapsed}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
