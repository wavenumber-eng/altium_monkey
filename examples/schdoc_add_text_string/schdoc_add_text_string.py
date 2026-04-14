from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    SchFontSpec,
    SchPointMils,
    TextJustification,
    TextOrientation,
    make_sch_text_string,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_text_strings.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    text_specs = [
        {
            "location_mils": SchPointMils.from_mils(1200, 6200),
            "text": "Bottom left Arial 10",
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#000000"),
            "orientation": TextOrientation.DEGREES_0,
            "justification": TextJustification.BOTTOM_LEFT,
            "mirrored": False,
            "url": "",
        },
        {
            "location_mils": SchPointMils.from_mils(4200, 6200),
            "text": "Center 90 Courier 12",
            "font": SchFontSpec(name="Courier New", size=12),
            "color": ColorValue.from_hex("#003366"),
            "orientation": TextOrientation.DEGREES_90,
            "justification": TextJustification.CENTER_CENTER,
            "mirrored": False,
            "url": "https://example.com/text-string",
        },
        {
            "location_mils": SchPointMils.from_mils(7600, 6200),
            "text": "Top right mirrored",
            "font": SchFontSpec(name="Times New Roman", size=14),
            "color": ColorValue.from_hex("#660000"),
            "orientation": TextOrientation.DEGREES_0,
            "justification": TextJustification.TOP_RIGHT,
            "mirrored": True,
            "url": "",
        },
    ]

    for spec in text_specs:
        schdoc.add_object(
            make_sch_text_string(
                location_mils=spec["location_mils"],
                text=spec["text"],
                font=spec["font"],
                color=spec["color"],
                orientation=spec["orientation"],
                justification=spec["justification"],
                mirrored=spec["mirrored"],
                url=spec["url"],
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Text strings written: {len(reopened.text_strings)}")
    for index, text_string in enumerate(reopened.text_strings, start=1):
        font_info = reopened.font_manager.get_font_info(text_string.font_id)
        color_text = ColorValue.from_win32(text_string.color or 0).hex
        print(
            f"Text string {index}: {text_string.text!r}, "
            f"{font_info['name']} {font_info['size']}, "
            f"{text_string.orientation.name}, "
            f"{text_string.justification.name}, "
            f"mirrored={text_string.is_mirrored}, "
            f"location=({text_string.location_mils.x_mils:.0f}, "
            f"{text_string.location_mils.y_mils:.0f}), "
            f"color={color_text}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
