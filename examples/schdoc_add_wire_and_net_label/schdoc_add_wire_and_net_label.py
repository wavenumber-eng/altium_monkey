from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchFontSpec,
    SchPointMils,
    TextJustification,
    TextOrientation,
    make_sch_net_label,
    make_sch_wire,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_wires_and_net_labels.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    schdoc.add_object(
        make_sch_wire(
            points_mils=[
                SchPointMils.from_mils(1500, 6500),
                SchPointMils.from_mils(3600, 6500),
            ],
            color=ColorValue.from_hex("#000080"),
            line_width=LineWidth.SMALL,
        )
    )
    schdoc.add_object(
        make_sch_net_label(
            location_mils=SchPointMils.from_mils(2550, 6500),
            text="CLK_MAIN",
            font=SchFontSpec(name="Arial", size=10),
            color=ColorValue.from_hex("#000080"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.BOTTOM_LEFT,
            mirrored=False,
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Wires written: {len(reopened.wires)}")
    print(f"Net labels written: {len(reopened.net_labels)}")

    for index, wire in enumerate(reopened.wires, start=1):
        points_text = ", ".join(
            f"({point.x_mils:.0f}, {point.y_mils:.0f})" for point in wire.points_mils
        )
        print(f"Wire {index}: width={wire.line_width.name}, points={points_text}")

    for index, label in enumerate(reopened.net_labels, start=1):
        font = label.font
        font_text = "unresolved"
        if font is not None:
            font_text = f"{font.name} {font.size}"
        print(
            f"Net label {index}: {label.text!r}, "
            f"{label.orientation.name}, "
            f"{label.justification.name}, "
            f"mirrored={label.is_mirrored}, "
            f"font={font_text}, "
            f"location=({label.location_mils.x_mils:.0f}, {label.location_mils.y_mils:.0f})"
        )

    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
