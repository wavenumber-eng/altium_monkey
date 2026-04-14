from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineStyle,
    LineWidth,
    SchRectMils,
    make_sch_rectangle,
    make_sch_rounded_rectangle,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_rectangles.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: plain rectangles with line-style variation.
    row1_y1 = 5600
    row1_y2 = 6500
    for x1, x2, line_style in (
        (700, 2200, LineStyle.SOLID),
        (2600, 4100, LineStyle.DASHED),
        (4500, 6000, LineStyle.DOTTED),
        (6400, 7900, LineStyle.DASH_DOT),
    ):
        schdoc.add_object(
            make_sch_rectangle(
                bounds_mils=SchRectMils.from_corners_mils(x1, row1_y1, x2, row1_y2),
                color=ColorValue.from_hex("#000000"),
                fill_color=ColorValue.from_hex("#FFF4CC"),
                line_width=LineWidth.SMALL,
                line_style=line_style,
                fill_background=True,
                transparent_fill=False,
            )
        )

    # Row 2: plain rectangles with width/fill/transparent variation.
    row2_y1 = 3900
    row2_y2 = 4800
    for x1, x2, stroke_hex, fill_hex, line_width, fill_background, transparent_fill in (
        (700, 2200, "#000000", "#FFFFFF", LineWidth.SMALLEST, False, False),
        (2600, 4100, "#1F77B4", "#DCEEFF", LineWidth.SMALL, True, False),
        (4500, 6000, "#D62728", "#FFE0DE", LineWidth.MEDIUM, True, True),
        (6400, 7900, "#2CA02C", "#E5F7E7", LineWidth.LARGE, True, False),
    ):
        schdoc.add_object(
            make_sch_rectangle(
                bounds_mils=SchRectMils.from_corners_mils(x1, row2_y1, x2, row2_y2),
                color=ColorValue.from_hex(stroke_hex),
                fill_color=ColorValue.from_hex(fill_hex),
                line_width=line_width,
                line_style=LineStyle.SOLID,
                fill_background=fill_background,
                transparent_fill=transparent_fill,
            )
        )

    # Row 3: rounded rectangles with ascending corner radii.
    row3_y1 = 2200
    row3_y2 = 3100
    for x1, x2, corner_x_radius_mils, corner_y_radius_mils in (
        (700, 2200, 100.0, 100.0),
        (2600, 4100, 200.0, 150.0),
        (4500, 6000, 300.5, 200.25),
        (6400, 7900, 450.0, 260.0),
    ):
        schdoc.add_object(
            make_sch_rounded_rectangle(
                bounds_mils=SchRectMils.from_corners_mils(x1, row3_y1, x2, row3_y2),
                corner_x_radius_mils=corner_x_radius_mils,
                corner_y_radius_mils=corner_y_radius_mils,
                color=ColorValue.from_hex("#000000"),
                fill_color=ColorValue.from_hex("#FFF4CC"),
                line_width=LineWidth.SMALL,
                fill_background=True,
            )
        )

    # Row 4: rounded rectangles with border/fill variation.
    row4_y1 = 700
    row4_y2 = 1600
    for x1, x2, stroke_hex, fill_hex, line_width, fill_background in (
        (700, 2200, "#000000", "#FFFFFF", LineWidth.SMALLEST, False),
        (2600, 4100, "#1F77B4", "#DCEEFF", LineWidth.SMALL, True),
        (4500, 6000, "#D62728", "#FFE0DE", LineWidth.MEDIUM, True),
        (6400, 7900, "#2CA02C", "#E5F7E7", LineWidth.LARGE, True),
    ):
        schdoc.add_object(
            make_sch_rounded_rectangle(
                bounds_mils=SchRectMils.from_corners_mils(x1, row4_y1, x2, row4_y2),
                corner_x_radius_mils=220.0,
                corner_y_radius_mils=160.0,
                color=ColorValue.from_hex(stroke_hex),
                fill_color=ColorValue.from_hex(fill_hex),
                line_width=line_width,
                fill_background=fill_background,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(
        "Shapes written: "
        f"{len(reopened.rectangles)} rectangles, "
        f"{len(reopened.rounded_rectangles)} rounded rectangles"
    )
    for index, rectangle in enumerate(reopened.rectangles, start=1):
        print(
            f"Rectangle {index}: "
            f"bounds={rectangle.bounds_mils}, "
            f"width={rectangle.line_width.name}, "
            f"style={rectangle.line_style.name}, "
            f"transparent={rectangle.transparent}"
        )
    for index, rounded_rectangle in enumerate(reopened.rounded_rectangles, start=1):
        print(
            f"Rounded rectangle {index}: "
            f"bounds={rounded_rectangle.bounds_mils}, "
            f"corner_radii=({rounded_rectangle.corner_x_radius_mils:.2f}, "
            f"{rounded_rectangle.corner_y_radius_mils:.2f}), "
            f"width={rounded_rectangle.line_width.name}, "
            f"fill={rounded_rectangle.is_solid}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
