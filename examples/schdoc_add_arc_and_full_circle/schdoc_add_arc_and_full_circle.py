from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchPointMils,
    make_sch_arc,
    make_sch_full_circle,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_arc_and_full_circle.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: full circles with ascending radii, including fractional mil values.
    row1_y = 5600
    for center_x, radius_mils in (
        (1500, 150.0),
        (3400, 250.5),
        (5600, 350.25),
        (7900, 450.75),
    ):
        schdoc.add_object(
            make_sch_full_circle(
                center_mils=SchPointMils.from_mils(center_x, row1_y),
                radius_mils=radius_mils,
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.SMALL,
            )
        )

    # Row 2: arc sweep examples with the same public center/radius input style.
    row2_y = 3400
    for center_x, start_angle, end_angle in (
        (1500, 0.0, 90.0),
        (3500, 30.0, 150.0),
        (5600, 180.0, 315.0),
        (7800, 225.0, 360.0),
    ):
        schdoc.add_object(
            make_sch_arc(
                center_mils=SchPointMils.from_mils(center_x, row2_y),
                radius_mils=400.0,
                start_angle_degrees=start_angle,
                end_angle_degrees=end_angle,
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.MEDIUM,
            )
        )

    # Row 3: stroke color and thickness examples.
    row3_y = 1300
    for center_x, color_hex, line_width in (
        (1600, "#000000", LineWidth.SMALLEST),
        (3600, "#1F77B4", LineWidth.SMALL),
        (5600, "#D62728", LineWidth.MEDIUM),
        (7600, "#2CA02C", LineWidth.LARGE),
    ):
        schdoc.add_object(
            make_sch_full_circle(
                center_mils=SchPointMils.from_mils(center_x, row3_y),
                radius_mils=225.0,
                color=ColorValue.from_hex(color_hex),
                line_width=line_width,
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Arcs written: {len(reopened.arcs)}")
    for index, arc in enumerate(reopened.arcs, start=1):
        print(
            f"Arc {index}: "
            f"center=({arc.location_mils.x_mils:.0f}, {arc.location_mils.y_mils:.0f}), "
            f"radius={arc.radius_mils:.2f}, "
            f"angles=({arc.start_angle:.1f}, {arc.end_angle:.1f}), "
            f"width={arc.line_width.name}, "
            f"color={ColorValue.from_win32(int(arc.color or 0)).hex}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
