from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchPointMils,
    make_sch_bezier,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_beziers.SchDoc"


def _offset_points(
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

    arch = [(0, 0), (300, 700), (900, 700), (1200, 0)]
    s_curve = [(0, 0), (250, 800), (950, -800), (1200, 0)]
    crest = [(0, 250), (350, 900), (850, -200), (1200, 250)]
    wave = [
        (0, 0),
        (250, 700),
        (850, 700),
        (1100, 0),
        (1400, -700),
        (1950, -700),
        (2200, 0),
    ]
    ribbon = [
        (0, 200),
        (350, 900),
        (850, 900),
        (1200, 200),
        (1500, -500),
        (2050, -500),
        (2400, 200),
    ]

    # Row 1: single-segment bezier curves.
    for column_x, shape_points in (
        (900, arch),
        (3200, s_curve),
        (5600, crest),
    ):
        schdoc.add_object(
            make_sch_bezier(
                points_mils=_offset_points(shape_points, column_x, 5600),
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.SMALL,
            )
        )

    # Row 2: connected multi-segment bezier curves.
    for column_x, shape_points, color_hex in (
        (900, wave, "#1F77B4"),
        (4200, ribbon, "#AA4B00"),
    ):
        schdoc.add_object(
            make_sch_bezier(
                points_mils=_offset_points(shape_points, column_x, 3600),
                color=ColorValue.from_hex(color_hex),
                line_width=LineWidth.MEDIUM,
            )
        )

    # Row 3: line-width and color variation on the same curve shape.
    for column_x, line_width, color_hex in (
        (900, LineWidth.SMALLEST, "#000000"),
        (3200, LineWidth.MEDIUM, "#0055AA"),
        (5600, LineWidth.LARGE, "#C62828"),
    ):
        schdoc.add_object(
            make_sch_bezier(
                points_mils=_offset_points(arch, column_x, 1500),
                color=ColorValue.from_hex(color_hex),
                line_width=line_width,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Beziers written: {len(reopened.beziers)}")
    for index, bezier in enumerate(reopened.beziers, start=1):
        points = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})" for point in bezier.points_mils
        )
        print(f"Bezier {index}: points=[{points}], width={bezier.line_width.name}")
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
