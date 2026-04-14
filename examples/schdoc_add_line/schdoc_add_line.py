from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineStyle,
    LineWidth,
    SchPointMils,
    make_sch_line,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_lines.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: line-width variations with the same solid style.
    row1_y = 5900
    for x1, x2, line_width in (
        (1100, 2500, LineWidth.SMALLEST),
        (3000, 4400, LineWidth.SMALL),
        (4900, 6300, LineWidth.MEDIUM),
        (6800, 8200, LineWidth.LARGE),
    ):
        schdoc.add_object(
            make_sch_line(
                start_mils=SchPointMils.from_mils(x1, row1_y),
                end_mils=SchPointMils.from_mils(x2, row1_y),
                color=ColorValue.from_hex("#000000"),
                line_width=line_width,
                line_style=LineStyle.SOLID,
            )
        )

    # Row 2: line-style variations at a consistent width.
    row2_y = 3900
    for x1, x2, line_style in (
        (1100, 2500, LineStyle.SOLID),
        (3000, 4400, LineStyle.DASHED),
        (4900, 6300, LineStyle.DOTTED),
        (6800, 8200, LineStyle.DASH_DOT),
    ):
        schdoc.add_object(
            make_sch_line(
                start_mils=SchPointMils.from_mils(x1, row2_y),
                end_mils=SchPointMils.from_mils(x2, row2_y),
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.MEDIUM,
                line_style=line_style,
            )
        )

    # Row 3: different lengths, slopes, and colors.
    for start, end, color_hex, line_width, line_style in (
        (
            SchPointMils.from_mils(1200, 1800),
            SchPointMils.from_mils(2800, 2400),
            "#000000",
            LineWidth.SMALL,
            LineStyle.SOLID,
        ),
        (
            SchPointMils.from_mils(3200, 1700),
            SchPointMils.from_mils(5200, 1100),
            "#1F77B4",
            LineWidth.MEDIUM,
            LineStyle.DASHED,
        ),
        (
            SchPointMils.from_mils(5600, 1200),
            SchPointMils.from_mils(7000, 2200),
            "#D62728",
            LineWidth.LARGE,
            LineStyle.DOTTED,
        ),
        (
            SchPointMils.from_mils(7400, 1700),
            SchPointMils.from_mils(8400, 1700),
            "#2CA02C",
            LineWidth.SMALL,
            LineStyle.DASH_DOT,
        ),
    ):
        schdoc.add_object(
            make_sch_line(
                start_mils=start,
                end_mils=end,
                color=ColorValue.from_hex(color_hex),
                line_width=line_width,
                line_style=line_style,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Lines written: {len(reopened.lines)}")
    for index, line in enumerate(reopened.lines, start=1):
        print(
            f"Line {index}: "
            f"start=({line.location_mils.x_mils:.0f}, {line.location_mils.y_mils:.0f}), "
            f"end=({line.corner_mils.x_mils:.0f}, {line.corner_mils.y_mils:.0f}), "
            f"width={line.line_width.name}, "
            f"style={line.line_style.name}, "
            f"color={ColorValue.from_win32(int(line.color or 0)).hex}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
