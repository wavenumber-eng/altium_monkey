from pathlib import Path

from altium_monkey import AltiumPcbDoc, PcbBodyProjection, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
STEP_MODEL_PATH = EXAMPLES_ROOT / "assets" / "3d" / "RESC1608X06N.step"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_free_3d_step.PcbDoc"


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcbdoc = AltiumPcbDoc.from_file(INPUT_PCBDOC)

    step_model = pcbdoc.add_embedded_model(
        name=STEP_MODEL_PATH.name,
        model_data=STEP_MODEL_PATH.read_bytes(),
    )
    pcbdoc.add_embedded_3d_model(
        step_model,
        layer=PcbLayer.MECHANICAL_13,
        side=PcbBodyProjection.TOP,
        location_mils=(4000.0, 3000.0),
        rotation_z_degrees=0.0,
        name="FREE_STEP_RESISTOR",
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
