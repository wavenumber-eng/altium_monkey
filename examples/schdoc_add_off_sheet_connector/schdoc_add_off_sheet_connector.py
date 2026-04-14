from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    OffSheetConnectorStyle,
    SchFontSpec,
    SchPointMils,
    TextOrientation,
    make_sch_off_sheet_connector,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_off_sheet_connector.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    schdoc.add_object(
        make_sch_off_sheet_connector(
            location_mils=SchPointMils.from_mils(2400, 6200),
            text="OFF_LEFT",
            style=OffSheetConnectorStyle.LEFT,
            font=SchFontSpec(name="Arial", size=10),
            color=ColorValue.from_hex("#800000"),
            orientation=TextOrientation.DEGREES_0,
            show_net_name=True,
        )
    )
    schdoc.add_object(
        make_sch_off_sheet_connector(
            location_mils=SchPointMils.from_mils(2400, 5600),
            text="OFF_RIGHT",
            style=OffSheetConnectorStyle.RIGHT,
            font=SchFontSpec(name="Arial", size=10),
            color=ColorValue.from_hex("#003366"),
            orientation=TextOrientation.DEGREES_0,
            show_net_name=True,
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Off-sheet connectors written: {len(reopened.cross_sheet_connectors)}")
    for index, connector in enumerate(reopened.cross_sheet_connectors, start=1):
        font = connector.font
        font_text = "unresolved"
        if font is not None:
            font_text = f"{font.name} {font.size}"
        style = getattr(connector, "style", None)
        style_text = style.name if hasattr(style, "name") else str(style)
        print(
            f"Connector {index}: {connector.text!r}, "
            f"style={style_text}, "
            f"{connector.orientation.name}, "
            f"show_net_name={connector.show_net_name}, "
            f"font={font_text}, "
            f"location=({connector.location_mils.x_mils:.0f}, {connector.location_mils.y_mils:.0f})"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
