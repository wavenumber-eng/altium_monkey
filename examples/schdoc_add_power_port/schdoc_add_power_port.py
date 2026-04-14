from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    PowerObjectStyle,
    SchFontSpec,
    SchPointMils,
    TextOrientation,
    make_sch_power_port,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_power_ports.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    power_port_specs = [
        {
            "location_mils": SchPointMils.from_mils(1400, 6900),
            "text": "VCC_CIRCLE",
            "style": PowerObjectStyle.CIRCLE,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(2900, 6900),
            "text": "+3V3",
            "style": PowerObjectStyle.ARROW,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(4400, 6900),
            "text": "VBAT",
            "style": PowerObjectStyle.BAR,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_90,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(5900, 6900),
            "text": "AC_REF",
            "style": PowerObjectStyle.WAVE,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(7400, 6900),
            "text": "VCC_GOST",
            "style": PowerObjectStyle.GOST_ARROW,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_180,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(8900, 6900),
            "text": "BAR_GOST",
            "style": PowerObjectStyle.GOST_BAR,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_270,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(1400, 4700),
            "text": "PGND",
            "style": PowerObjectStyle.GND_POWER,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": False,
        },
        {
            "location_mils": SchPointMils.from_mils(2900, 4700),
            "text": "AGND",
            "style": PowerObjectStyle.GND_SIGNAL,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": False,
        },
        {
            "location_mils": SchPointMils.from_mils(4400, 4700),
            "text": "EARTH_REF",
            "style": PowerObjectStyle.GND_EARTH,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(5900, 4700),
            "text": "PGND_GOST",
            "style": PowerObjectStyle.GOST_GND_POWER,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": False,
        },
        {
            "location_mils": SchPointMils.from_mils(7400, 4700),
            "text": "CHASSIS",
            "style": PowerObjectStyle.GOST_GND_EARTH,
            "font": SchFontSpec(name="Arial", size=10),
            "color": ColorValue.from_hex("#800000"),
            "orientation": TextOrientation.DEGREES_0,
            "show_net_name": True,
        },
    ]

    for spec in power_port_specs:
        schdoc.add_object(
            make_sch_power_port(
                location_mils=spec["location_mils"],
                text=spec["text"],
                style=spec["style"],
                font=spec["font"],
                color=spec["color"],
                orientation=spec["orientation"],
                show_net_name=spec["show_net_name"],
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Power ports written: {len(reopened.power_ports)}")
    for index, power_port in enumerate(reopened.power_ports, start=1):
        font = power_port.font
        color_text = ColorValue.from_win32(power_port.color or 0).hex
        font_text = "unresolved"
        if font is not None:
            font_text = f"{font.name} {font.size}"
        print(
            f"Power port {index}: {power_port.text!r}, "
            f"{power_port.style.name}, "
            f"{power_port.orientation.name}, "
            f"show_net_name={power_port.show_net_name}, "
            f"font={font_text}, "
            f"location=({power_port.location_mils.x_mils:.0f}, "
            f"{power_port.location_mils.y_mils:.0f}), "
            f"color={color_text}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
