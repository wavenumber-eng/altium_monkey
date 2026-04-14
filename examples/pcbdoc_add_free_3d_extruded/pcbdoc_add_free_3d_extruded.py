from pathlib import Path

from altium_monkey import AltiumPcbDoc, PcbBodyProjection, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_free_3d_extruded.PcbDoc"


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcbdoc = AltiumPcbDoc.from_file(INPUT_PCBDOC)

    pcbdoc.add_extruded_3d_body(
        outline_points_mils=[
            (2900.0, 2300.0),
            (4700.0, 2300.0),
            (4900.0, 2800.0),
            (4700.0, 3300.0),
            (2900.0, 3300.0),
            (2700.0, 2800.0),
        ],
        layer=PcbLayer.MECHANICAL_1,
        overall_height_mils=180.0,
        standoff_height_mils=20.0,
        side=PcbBodyProjection.TOP,
        name="FREE_EXTRUDED_HEX",
        body_color_3d=0xC07030,
        opacity=0.85,
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
