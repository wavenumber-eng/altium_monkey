from pathlib import Path

from altium_monkey import AltiumPcbDoc, AltiumPcbLib

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
PCBLIB_PATH = EXAMPLES_ROOT / "assets" / "pcblib" / "R0603_0.55MM_MD.PcbLib"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_insert_footprint_from_pcblib.PcbDoc"


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    source_pcblib = AltiumPcbLib.from_file(PCBLIB_PATH)
    footprint = source_pcblib.footprints[0]

    pcbdoc = AltiumPcbDoc()
    pcbdoc.set_outline_rectangle_mils(0.0, 0.0, 5000.0, 3000.0)
    pcbdoc.set_origin_to_outline_lower_left()
    pcbdoc.add_component_from_pcblib(
        footprint,
        designator="R1",
        position_mils=(2000.0, 1500.0),
        layer="TOP",
        rotation_degrees=0.0,
        source_pcblib=source_pcblib,
        comment_text="10k",
        component_parameters={
            "Value": "10k",
            "Manufacturer Part Number": "ERJ-3EKF1002V",
        },
        pad_nets={"1": "VIN", "2": "SENSE"},
    )
    pcbdoc.add_component_from_pcblib(
        footprint,
        designator="R2",
        position_mils=(3000.0, 1500.0),
        layer="TOP",
        rotation_degrees=180.0,
        source_pcblib=source_pcblib,
        comment_text="1k",
        component_parameters={
            "Value": "1k",
            "Manufacturer Part Number": "ERJ-3EKF1001V",
        },
        pad_nets={"1": "SENSE", "2": "GND"},
    )
    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
