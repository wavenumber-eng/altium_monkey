from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    PortIOType,
    PortStyle,
    SchFontSpec,
    SchHorizontalAlign,
    SchPointMils,
    make_sch_port,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_ports.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    port_specs = [
        {
            "location_mils": SchPointMils.from_mils(1200, 7100),
            "name": "CLK_IN",
            "width_mils": 700,
            "height_mils": 100,
            "io_type": PortIOType.INPUT,
            "style": PortStyle.LEFT,
            "font": SchFontSpec(name="Arial", size=10),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#D9EAF7"),
            "text_color": ColorValue.from_hex("#073763"),
            "alignment": SchHorizontalAlign.LEFT,
            "border_width": LineWidth.SMALL,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(2600, 7100),
            "name": "DATA_OUT",
            "width_mils": 700,
            "height_mils": 130,
            "io_type": PortIOType.OUTPUT,
            "style": PortStyle.RIGHT,
            "font": SchFontSpec(name="Courier New", size=12),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#FCE5CD"),
            "text_color": ColorValue.from_hex("#7F6000"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.MEDIUM,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(4100, 7100),
            "name": "DATA_IO",
            "width_mils": 800,
            "height_mils": 130,
            "io_type": PortIOType.BIDIRECTIONAL,
            "style": PortStyle.LEFT_RIGHT,
            "font": SchFontSpec(name="Times New Roman", size=12),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#D9EAD3"),
            "text_color": ColorValue.from_hex("#274E13"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.LARGE,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(5800, 7100),
            "name": "PLAIN_BUS",
            "width_mils": 700,
            "height_mils": 100,
            "io_type": PortIOType.UNSPECIFIED,
            "style": PortStyle.NONE_HORIZONTAL,
            "font": SchFontSpec(name="Arial", size=10),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#EFEFEF"),
            "text_color": ColorValue.from_hex("#222222"),
            "alignment": SchHorizontalAlign.RIGHT,
            "border_width": LineWidth.SMALLEST,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(1500, 4700),
            "name": "UPLINK",
            "width_mils": 600,
            "height_mils": 100,
            "io_type": PortIOType.OUTPUT,
            "style": PortStyle.TOP,
            "font": SchFontSpec(name="Arial", size=10),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#D9EAF7"),
            "text_color": ColorValue.from_hex("#073763"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.SMALL,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(3000, 4700),
            "name": "DOWNLINK",
            "width_mils": 600,
            "height_mils": 130,
            "io_type": PortIOType.INPUT,
            "style": PortStyle.BOTTOM,
            "font": SchFontSpec(name="Courier New", size=12),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#FCE5CD"),
            "text_color": ColorValue.from_hex("#7F6000"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.MEDIUM,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(4500, 4700),
            "name": "VERT_IO",
            "width_mils": 700,
            "height_mils": 130,
            "io_type": PortIOType.BIDIRECTIONAL,
            "style": PortStyle.TOP_BOTTOM,
            "font": SchFontSpec(name="Times New Roman", size=12),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#D9EAD3"),
            "text_color": ColorValue.from_hex("#274E13"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.LARGE,
            "auto_size": False,
            "show_net_name": True,
        },
        {
            "location_mils": SchPointMils.from_mils(6200, 4700),
            "name": "VERT_PLAIN",
            "width_mils": 600,
            "height_mils": 100,
            "io_type": PortIOType.UNSPECIFIED,
            "style": PortStyle.NONE_VERTICAL,
            "font": SchFontSpec(name="Arial", size=10),
            "border_color": ColorValue.from_hex("#000000"),
            "fill_color": ColorValue.from_hex("#EFEFEF"),
            "text_color": ColorValue.from_hex("#222222"),
            "alignment": SchHorizontalAlign.CENTER,
            "border_width": LineWidth.SMALLEST,
            "auto_size": False,
            "show_net_name": False,
        },
    ]

    for spec in port_specs:
        schdoc.add_object(
            make_sch_port(
                location_mils=spec["location_mils"],
                name=spec["name"],
                width_mils=spec["width_mils"],
                height_mils=spec["height_mils"],
                io_type=spec["io_type"],
                style=spec["style"],
                font=spec["font"],
                border_color=spec["border_color"],
                fill_color=spec["fill_color"],
                text_color=spec["text_color"],
                alignment=spec["alignment"],
                border_width=spec["border_width"],
                auto_size=spec["auto_size"],
                show_net_name=spec["show_net_name"],
            )
        )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Loaded: {INPUT_SCHDOC.name}")
    print(f"Ports written: {len(reopened.ports)}")
    for index, port in enumerate(reopened.ports, start=1):
        font = port.font
        font_text = "unresolved"
        if font is not None:
            font_text = f"{font.name} {font.size}"
        print(
            f"Port {index}: {port.name!r}, "
            f"{port.style.name}, "
            f"{port.io_type.name}, "
            f"{port.alignment.name}, "
            f"size=({port.width_mils}mil x {port.height_mils}mil), "
            f"auto_size={port.auto_size}, "
            f"show_net_name={port.show_net_name}, "
            f"font={font_text}, "
            f"location=({port.location_mils.x_mils:.0f}, {port.location_mils.y_mils:.0f})"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
