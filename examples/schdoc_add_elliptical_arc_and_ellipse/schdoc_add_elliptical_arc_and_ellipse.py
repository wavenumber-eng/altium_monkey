from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchPointMils,
    make_sch_ellipse,
    make_sch_elliptical_arc,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_elliptical_arc_and_ellipse.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: full ellipses with ascending primary/secondary radii.
    row1_y = 5600
    for center_x, radius_mils, secondary_radius_mils in (
        (1500, 220.0, 140.0),
        (3500, 320.5, 180.25),
        (5700, 430.25, 240.5),
        (7900, 540.75, 300.25),
    ):
        schdoc.add_object(
            make_sch_ellipse(
                center_mils=SchPointMils.from_mils(center_x, row1_y),
                radius_mils=radius_mils,
                secondary_radius_mils=secondary_radius_mils,
                color=ColorValue.from_hex("#000000"),
                fill_color=ColorValue.from_hex("#FFF4CC"),
                line_width=LineWidth.SMALLEST,
                fill_background=True,
            )
        )

    # Row 2: elliptical arcs with different sweep ranges.
    row2_y = 3400
    for center_x, start_angle, end_angle in (
        (1500, 0.0, 120.0),
        (3500, 25.0, 165.0),
        (5700, 150.0, 315.0),
        (7900, 225.0, 360.0),
    ):
        schdoc.add_object(
            make_sch_elliptical_arc(
                center_mils=SchPointMils.from_mils(center_x, row2_y),
                radius_mils=450.0,
                secondary_radius_mils=250.5,
                start_angle_degrees=start_angle,
                end_angle_degrees=end_angle,
                color=ColorValue.from_hex("#000000"),
                line_width=LineWidth.MEDIUM,
            )
        )

    # Row 3: ellipse stroke/fill style variations.
    row3_y = 1300
    for center_x, stroke_hex, fill_hex, line_width, fill_background in (
        (1500, "#000000", "#FFFFFF", LineWidth.SMALLEST, False),
        (3500, "#1F77B4", "#DCEEFF", LineWidth.SMALL, True),
        (5700, "#D62728", "#FFE0DE", LineWidth.MEDIUM, True),
        (7900, "#2CA02C", "#E5F7E7", LineWidth.LARGE, True),
    ):
        schdoc.add_object(
            make_sch_ellipse(
                center_mils=SchPointMils.from_mils(center_x, row3_y),
                radius_mils=260.0,
                secondary_radius_mils=170.0,
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
        f"{len(reopened.ellipses)} ellipses, "
        f"{len(reopened.elliptical_arcs)} elliptical arcs"
    )
    for index, ellipse in enumerate(reopened.ellipses, start=1):
        print(
            f"Ellipse {index}: "
            f"center=({ellipse.location_mils.x_mils:.0f}, {ellipse.location_mils.y_mils:.0f}), "
            f"radii=({ellipse.radius_mils:.2f}, {ellipse.secondary_radius_mils:.2f}), "
            f"width={ellipse.line_width.name}, "
            f"fill={ellipse.is_solid}"
        )
    for index, arc in enumerate(reopened.elliptical_arcs, start=1):
        print(
            f"Elliptical arc {index}: "
            f"center=({arc.location_mils.x_mils:.0f}, {arc.location_mils.y_mils:.0f}), "
            f"radii=({arc.radius_mils:.2f}, {arc.secondary_radius_mils:.2f}), "
            f"angles=({arc.start_angle:.1f}, {arc.end_angle:.1f}), "
            f"width={arc.line_width.name}, "
            f"color={ColorValue.from_win32(int(arc.color or 0)).hex}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
