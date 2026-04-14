from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchPointMils,
    make_sch_polygon,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_polygons.SchDoc"


def _offset_shape(
    points: list[tuple[float, float]],
    dx_mils: float,
    dy_mils: float,
) -> list[SchPointMils]:
    return [
        SchPointMils.from_mils(x_mils + dx_mils, y_mils + dy_mils)
        for x_mils, y_mils in points
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    triangle = [(0, 0), (500, 700), (1000, 0)]
    pentagon = [(0, 250), (250, 700), (800, 620), (1000, 200), (500, 0)]
    hexagon = [(0, 250), (200, 650), (700, 650), (900, 250), (700, 0), (200, 0)]
    arrow = [
        (0, 250),
        (450, 700),
        (450, 450),
        (1000, 450),
        (1000, 50),
        (450, 50),
        (450, 0),
    ]

    # Row 1: filled polygon gallery with different point counts.
    for column_x, shape_points, fill_hex in (
        (900, triangle, "#FFF4CC"),
        (2700, pentagon, "#DCEEFF"),
        (4700, hexagon, "#E5F7E7"),
        (6800, arrow, "#FFE0DE"),
    ):
        schdoc.add_object(
            make_sch_polygon(
                points_mils=_offset_shape(shape_points, column_x, 5200),
                color=ColorValue.from_hex("#000000"),
                fill_color=ColorValue.from_hex(fill_hex),
                line_width=LineWidth.LARGE,
                fill_background=True,
                transparent_fill=False,
            )
        )

    # Row 2: fill and border variation.
    for (
        column_x,
        fill_background,
        transparent_fill,
        line_width,
        stroke_hex,
        fill_hex,
    ) in (
        (900, False, False, LineWidth.SMALLEST, "#000000", "#FFFFFF"),
        (2700, True, False, LineWidth.SMALL, "#1F77B4", "#DCEEFF"),
        (4700, True, True, LineWidth.MEDIUM, "#D62728", "#FFE0DE"),
        (6800, True, False, LineWidth.LARGE, "#2CA02C", "#E5F7E7"),
    ):
        schdoc.add_object(
            make_sch_polygon(
                points_mils=_offset_shape(hexagon, column_x, 2600),
                color=ColorValue.from_hex(stroke_hex),
                fill_color=ColorValue.from_hex(fill_hex),
                line_width=line_width,
                fill_background=fill_background,
                transparent_fill=transparent_fill,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Polygons written: {len(reopened.polygons)}")
    for index, polygon in enumerate(reopened.polygons, start=1):
        points = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})" for point in polygon.points_mils
        )
        print(
            f"Polygon {index}: "
            f"points=[{points}], "
            f"width={polygon.line_width.name}, "
            f"fill={polygon.is_solid}, "
            f"transparent={polygon.transparent}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
