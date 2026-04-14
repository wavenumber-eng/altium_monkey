from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
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
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "harness_example.SchDoc"
REFERENCE_SCHDOC = OUTPUT_DIR / "harness_example_ref.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

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

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    reopened_connector = reopened.harness_connectors[0]
    reference = AltiumSchDoc(REFERENCE_SCHDOC)
    reference_connector = reference.harness_connectors[0]

    print(f"Harness connectors: {len(reopened.harness_connectors)}")
    print(f"Entry names: {[entry.name for entry in reopened_connector.entries]}")
    print(
        "Type label: "
        f"{reopened_connector.type_label.text if reopened_connector.type_label else ''}"
    )
    print(f"Ports: {len(reopened.ports)}")
    print(f"Signal harnesses: {len(reopened.signal_harnesses)}")
    print(
        "Connector match ref: "
        f"{(reopened_connector.location.x_mils, reopened_connector.location.y_mils, reopened_connector.xsize, reopened_connector.ysize) == (reference_connector.location.x_mils, reference_connector.location.y_mils, reference_connector.xsize, reference_connector.ysize)}"
    )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
