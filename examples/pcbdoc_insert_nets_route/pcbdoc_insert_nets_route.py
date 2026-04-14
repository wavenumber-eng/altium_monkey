from pathlib import Path

from altium_monkey import AltiumPcbDoc, AltiumPcbLib

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_ROOT / "assets" / "pcblib"
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
CONNECTOR_PCBLIB = ASSETS_DIR / "03R-JWPF-VSLE-S.PcbLib"
RESISTOR_PCBLIB = ASSETS_DIR / "R0603_0.55MM_MD.PcbLib"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_insert_nets_route.PcbDoc"


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    connector_lib = AltiumPcbLib.from_file(CONNECTOR_PCBLIB)
    resistor_lib = AltiumPcbLib.from_file(RESISTOR_PCBLIB)

    pcbdoc = AltiumPcbDoc.from_file(INPUT_PCBDOC)

    pcbdoc.add_component_from_pcblib(
        connector_lib.footprints[0],
        designator="J1",
        position_mils=(2500.0, 3000.0),
        source_pcblib=connector_lib,
        source_footprint_library=str(CONNECTOR_PCBLIB.name),
        comment_text="Power Input",
        pad_nets={"1": "VIN", "2": "SENSE", "3": "GND"},
    )
    pcbdoc.add_component_from_pcblib(
        resistor_lib.footprints[0],
        designator="R1",
        position_mils=(4100.0, 3000.0),
        source_pcblib=resistor_lib,
        source_footprint_library=str(RESISTOR_PCBLIB.name),
        comment_text="10k",
        component_parameters={"Value": "10k"},
        pad_nets={"1": "VIN", "2": "SENSE"},
    )

    pcbdoc.add_track((2578.74, 3000.0), (4067.52, 3000.0), width_mils=12.0, net="VIN")
    pcbdoc.add_track(
        (4132.48, 3000.0),
        (4132.48, 2650.0),
        width_mils=10.0,
        net="SENSE",
    )
    pcbdoc.add_track((4132.48, 2650.0), (2500.0, 2650.0), width_mils=10.0, net="SENSE")
    pcbdoc.add_track((2500.0, 2650.0), (2500.0, 3000.0), width_mils=10.0, net="SENSE")
    pcbdoc.add_via(
        position_mils=(2300.0, 2800.0),
        diameter_mils=28.0,
        hole_size_mils=12.0,
        net="GND",
    )
    pcbdoc.add_track((2421.26, 3000.0), (2300.0, 2800.0), width_mils=14.0, net="GND")

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
