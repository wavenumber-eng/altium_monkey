from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineStyle,
    LineWidth,
    SchPointMils,
    make_sch_blanket,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_blankets.SchDoc"


def _rect_points(x1: int, y1: int, x2: int, y2: int) -> list[SchPointMils]:
    return [
        SchPointMils.from_mils(x1, y1),
        SchPointMils.from_mils(x2, y1),
        SchPointMils.from_mils(x2, y2),
        SchPointMils.from_mils(x1, y2),
    ]


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    # Row 1: common blanket border styles.
    schdoc.add_object(
        make_sch_blanket(
            points_mils=_rect_points(900, 5000, 2500, 6400),
            color=ColorValue.from_hex("#800000"),
            fill_color=ColorValue.from_hex("#FFE6D5"),
            line_width=LineWidth.SMALLEST,
            line_style=LineStyle.DASHED,
            fill_background=False,
            transparent_fill=True,
        )
    )
    schdoc.add_object(
        make_sch_blanket(
            points_mils=_rect_points(3000, 5000, 4600, 6400),
            color=ColorValue.from_hex("#800000"),
            fill_color=ColorValue.from_hex("#FFD5B5"),
            line_width=LineWidth.SMALL,
            line_style=LineStyle.DOTTED,
            fill_background=True,
            transparent_fill=True,
        )
    )
    schdoc.add_object(
        make_sch_blanket(
            points_mils=_rect_points(5100, 5000, 6700, 6400),
            color=ColorValue.from_hex("#800000"),
            fill_color=ColorValue.from_hex("#FFF0E0"),
            line_width=LineWidth.MEDIUM,
            line_style=LineStyle.SOLID,
            fill_background=True,
            transparent_fill=False,
        )
    )

    # Row 2: collapsed blanket state and a non-rectangular blanket region.
    schdoc.add_object(
        make_sch_blanket(
            points_mils=_rect_points(1400, 2200, 3000, 3600),
            color=ColorValue.from_hex("#800000"),
            fill_color=ColorValue.from_hex("#FFE6D5"),
            line_width=LineWidth.SMALL,
            line_style=LineStyle.DASHED,
            fill_background=True,
            transparent_fill=True,
            collapsed=True,
        )
    )
    schdoc.add_object(
        make_sch_blanket(
            points_mils=[
                SchPointMils.from_mils(4200, 2200),
                SchPointMils.from_mils(6000, 2200),
                SchPointMils.from_mils(6600, 3000),
                SchPointMils.from_mils(5600, 3800),
                SchPointMils.from_mils(4200, 3400),
            ],
            color=ColorValue.from_hex("#800000"),
            fill_color=ColorValue.from_hex("#FFD5B5"),
            line_width=LineWidth.SMALLEST,
            line_style=LineStyle.DASH_DOT,
            fill_background=True,
            transparent_fill=True,
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Blankets written: {len(reopened.blankets)}")
    for index, blanket in enumerate(reopened.blankets, start=1):
        points_text = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})" for point in blanket.points_mils
        )
        print(
            "Blanket "
            f"{index}: "
            f"style={blanket.line_style.name}, "
            f"width={blanket.line_width.name}, "
            f"filled={blanket.is_solid}, "
            f"transparent={blanket.transparent}, "
            f"collapsed={blanket.is_collapsed}, "
            f"points=[{points_text}]"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
