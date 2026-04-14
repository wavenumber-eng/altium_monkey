from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineShape,
    LineStyle,
    LineWidth,
    SchPointMils,
    make_sch_polyline,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_polylines.SchDoc"


def _path_points(column_x: int, row_y: int) -> list[SchPointMils]:
    return [
        SchPointMils.from_mils(column_x, row_y),
        SchPointMils.from_mils(column_x + 500, row_y),
        SchPointMils.from_mils(column_x + 900, row_y - 500),
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    black = ColorValue.from_hex("#000000")

    # Row 1: end-marker gallery.
    for column_x, end_shape in zip(
        (900, 2100, 3300, 4500, 5700, 6900),
        (
            LineShape.ARROW,
            LineShape.SOLID_ARROW,
            LineShape.TAIL,
            LineShape.SOLID_TAIL,
            LineShape.CIRCLE,
            LineShape.SQUARE,
        ),
        strict=True,
    ):
        schdoc.add_object(
            make_sch_polyline(
                points_mils=_path_points(column_x, 6200),
                color=black,
                line_width=LineWidth.SMALL,
                line_style=LineStyle.SOLID,
                end_line_shape=end_shape,
                line_shape_size=LineWidth.MEDIUM,
            )
        )

    # Row 2: start-marker gallery.
    for column_x, start_shape in zip(
        (900, 2100, 3300, 4500, 5700, 6900),
        (
            LineShape.ARROW,
            LineShape.SOLID_ARROW,
            LineShape.TAIL,
            LineShape.SOLID_TAIL,
            LineShape.CIRCLE,
            LineShape.SQUARE,
        ),
        strict=True,
    ):
        schdoc.add_object(
            make_sch_polyline(
                points_mils=_path_points(column_x, 4600),
                color=black,
                line_width=LineWidth.SMALL,
                line_style=LineStyle.SOLID,
                start_line_shape=start_shape,
                line_shape_size=LineWidth.MEDIUM,
            )
        )

    # Row 3: endpoint-size gallery using the same arrow shape.
    for column_x, marker_size in zip(
        (1200, 3000, 4800, 6600),
        (
            LineWidth.SMALLEST,
            LineWidth.SMALL,
            LineWidth.MEDIUM,
            LineWidth.LARGE,
        ),
        strict=True,
    ):
        schdoc.add_object(
            make_sch_polyline(
                points_mils=_path_points(column_x, 3000),
                color=black,
                line_width=LineWidth.MEDIUM,
                line_style=LineStyle.SOLID,
                end_line_shape=LineShape.SOLID_ARROW,
                line_shape_size=marker_size,
            )
        )

    # Row 4: line-style gallery with a common end marker.
    for column_x, line_style, color_hex in zip(
        (1200, 3000, 4800, 6600),
        (
            LineStyle.SOLID,
            LineStyle.DASHED,
            LineStyle.DOTTED,
            LineStyle.DASH_DOT,
        ),
        ("#000000", "#1F77B4", "#D62728", "#2CA02C"),
        strict=True,
    ):
        schdoc.add_object(
            make_sch_polyline(
                points_mils=_path_points(column_x, 1500),
                color=ColorValue.from_hex(color_hex),
                line_width=LineWidth.MEDIUM,
                line_style=line_style,
                end_line_shape=LineShape.CIRCLE,
                line_shape_size=LineWidth.MEDIUM,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Polylines written: {len(reopened.polylines)}")
    for index, polyline in enumerate(reopened.polylines, start=1):
        points = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})"
            for point in polyline.points_mils
        )
        print(
            f"Polyline {index}: "
            f"points=[{points}], "
            f"width={polyline.line_width.name}, "
            f"style={polyline.line_style.name}, "
            f"start={polyline.start_line_shape.name}, "
            f"end={polyline.end_line_shape.name}, "
            f"marker_size={polyline.line_shape_size.name}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
