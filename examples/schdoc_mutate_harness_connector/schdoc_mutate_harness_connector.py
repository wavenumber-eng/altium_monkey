from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    LineWidth,
    SchFontSpec,
    SchHarnessConnectorSide,
    SchHorizontalAlign,
    SchPointMils,
    SchRectMils,
    make_sch_harness_connector,
    make_sch_harness_entry,
    make_sch_harness_type,
    make_sch_port,
    make_sch_signal_harness,
)


SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SAMPLE_DIR / "output"
BASELINE_SCHDOC = OUTPUT_DIR / "harness_mutation_input.SchDoc"
OUTPUT_SCHDOC = OUTPUT_DIR / "harness_mutation_output.SchDoc"


def build_input_document() -> AltiumSchDoc:
    """Build a standalone baseline document for the mutation example."""
    schdoc = AltiumSchDoc()

    connector = make_sch_harness_connector(
        bounds_mils=SchRectMils.from_corners_mils(3450, 5100, 4200, 4700),
        side=SchHarnessConnectorSide.RIGHT,
        primary_position_mils=200,
    )
    connector.add_entry(
        make_sch_harness_entry(
            name="SDA",
            distance_from_top_mils=100,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            text_color=ColorValue.from_hex("#000000"),
        )
    )
    connector.add_entry(
        make_sch_harness_entry(
            name="SCL",
            distance_from_top_mils=300,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            text_color=ColorValue.from_hex("#000000"),
        )
    )
    connector.set_type_label(
        make_sch_harness_type(
            text="I2C",
            location_mils=SchPointMils.from_mils(3900, 4650),
            font=SchFontSpec(name="Arial", size=12, bold=True),
        )
    )
    schdoc.add_object(connector)

    schdoc.add_object(
        make_sch_port(
            location_mils=SchPointMils.from_mils(5200, 4900),
            name="I2C",
            width_mils=600,
            height_mils=100,
            font=SchFontSpec(name="Arial", size=10),
            fill_color=ColorValue.from_hex("#FFFF80"),
            alignment=SchHorizontalAlign.CENTER,
            harness_type="I2C",
        )
    )

    schdoc.add_object(
        make_sch_signal_harness(
            points_mils=[
                SchPointMils.from_mils(4200, 4900),
                SchPointMils.from_mils(5200, 4900),
            ],
        )
    )

    return schdoc


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    build_input_document().save(BASELINE_SCHDOC)

    schdoc = AltiumSchDoc(BASELINE_SCHDOC)
    for connector in schdoc.harness_connectors:
        if connector.type_label is None or connector.type_label.text != "I2C":
            continue

        sda_entry = connector.get_entry("SDA")
        if sda_entry is None:
            continue

        connector.line_width = LineWidth.LARGE
        connector.color = ColorValue.from_hex("#1F4E79").win32
        connector.area_color = ColorValue.from_hex("#D9EAF7").win32

        sda_entry.name = "SDA0"
        sda_entry.font = SchFontSpec(name="Courier New", size=12, bold=True)
        sda_entry.color = ColorValue.from_hex("#0B5394").win32
        sda_entry.area_color = ColorValue.from_hex("#D9EAF7").win32
        sda_entry.text_color = ColorValue.from_hex("#003366").win32

        connector.add_entry(
            make_sch_harness_entry(
                name="SCL0",
                distance_from_top_mils=300,
                font=SchFontSpec(name="Courier New", size=12, bold=True),
                border_color=ColorValue.from_hex("#0B5394"),
                fill_color=ColorValue.from_hex("#D9EAF7"),
                text_color=ColorValue.from_hex("#003366"),
            )
        )
        connector.move_entry("SCL0", index=1)
        connector.remove_entry_by_name("SCL")
        if connector.type_label is not None:
            connector.type_label.text = "I2C_CTRL"
            connector.type_label.font = SchFontSpec(
                name="Courier New",
                size=14,
                bold=True,
            )
            connector.type_label.color = ColorValue.from_hex("#0B5394").win32
            connector.type_label.location = SchPointMils.from_mils(
                3700,
                4600,
            ).to_coord_point()

    for signal_harness in schdoc.signal_harnesses:
        signal_harness.line_width = LineWidth.LARGE
        signal_harness.color = ColorValue.from_hex("#0B5394").win32

    for port in schdoc.ports:
        if port.name != "I2C":
            continue
        port.font = SchFontSpec(name="Courier New", size=12, bold=True)
        port.color = ColorValue.from_hex("#0B5394").win32
        port.area_color = ColorValue.from_hex("#D9EAF7").win32
        port.text_color = ColorValue.from_hex("#003366").win32
        port.border_width = LineWidth.LARGE
        port.harness_type = "I2C_CTRL"

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    reopened_connector = reopened.harness_connectors[0]
    reopened_sda = reopened_connector.get_entry("SDA0")
    reopened_port = reopened.ports[0] if reopened.ports else None
    reopened_signal_harness = (
        reopened.signal_harnesses[0] if reopened.signal_harnesses else None
    )

    print(f"Entry names: {[entry.name for entry in reopened_connector.entries]}")
    print(
        "Type label: "
        f"{reopened_connector.type_label.text if reopened_connector.type_label else ''}"
    )
    print(f"SDA font: {reopened_sda.font if reopened_sda is not None else None}")
    print(
        "Connector colors: "
        f"border={reopened_connector.color} fill={reopened_connector.area_color}"
    )
    print(
        "Signal harness: "
        f"width={reopened_signal_harness.line_width if reopened_signal_harness else None} "
        f"color={reopened_signal_harness.color if reopened_signal_harness else None}"
    )
    print(
        "Port: "
        f"font={reopened_port.font if reopened_port is not None else None} "
        f"fill={reopened_port.area_color if reopened_port is not None else None}"
    )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
