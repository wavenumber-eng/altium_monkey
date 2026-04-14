from pathlib import Path

from altium_monkey import (
    AltiumPcbFootprint,
    AltiumPcbLib,
    PadShape,
    PcbBodyProjection,
    PcbLayer,
)

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
STEP_MODEL_PATH = EXAMPLES_ROOT / "assets" / "3d" / "RESC1608X06N.step"
OUTPUT_PATH = SAMPLE_DIR / "output" / "hello_pcblib.PcbLib"

FOOTPRINT_NAME = "R0603_0.55MM_MD"
FOOTPRINT_HEIGHT_MILS = 21.6535


def _add_box_tracks(
    footprint: AltiumPcbFootprint,
    *,
    left_mils: float,
    bottom_mils: float,
    right_mils: float,
    top_mils: float,
    width_mils: float,
    layer: PcbLayer,
) -> None:
    footprint.add_track(
        (left_mils, bottom_mils),
        (right_mils, bottom_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (left_mils, top_mils),
        (right_mils, top_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (right_mils, bottom_mils),
        (right_mils, top_mils),
        width_mils=width_mils,
        layer=layer,
    )
    footprint.add_track(
        (left_mils, bottom_mils),
        (left_mils, top_mils),
        width_mils=width_mils,
        layer=layer,
    )


def build_pcblib(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcblib = AltiumPcbLib()
    step_model = pcblib.add_embedded_model(
        name=STEP_MODEL_PATH.name,
        model_data=STEP_MODEL_PATH.read_bytes(),
    )

    footprint = pcblib.add_footprint(
        FOOTPRINT_NAME,
        height=f"{FOOTPRINT_HEIGHT_MILS}mil",
        description="Resistor 0603 Medium Density",
    )

    for designator, x_mils in (("1", -32.4803), ("2", 32.4803)):
        footprint.add_pad(
            designator=designator,
            position_mils=(x_mils, 0.0),
            width_mils=33.4646,
            height_mils=27.5591,
            layer=PcbLayer.TOP,
            shape=PadShape.RECTANGLE,
            rotation_degrees=90.0,
        )

    footprint.add_track(
        (-3.9370, -12.7953),
        (3.9370, -12.7953),
        width_mils=7.8740,
        layer=PcbLayer.TOP_OVERLAY,
    )
    footprint.add_track(
        (-3.9370, 12.7953),
        (3.9370, 12.7953),
        width_mils=7.8740,
        layer=PcbLayer.TOP_OVERLAY,
    )

    footprint.add_track(
        (0.0, -15.7480),
        (0.0, 15.7480),
        width_mils=3.9370,
        layer=PcbLayer.MECHANICAL_15,
    )
    footprint.add_track(
        (-15.7480, 0.0),
        (15.7480, 0.0),
        width_mils=3.9370,
        layer=PcbLayer.MECHANICAL_15,
    )
    _add_box_tracks(
        footprint,
        left_mils=-57.0866,
        bottom_mils=-27.5590,
        right_mils=57.0866,
        top_mils=27.5591,
        width_mils=1.9685,
        layer=PcbLayer.MECHANICAL_15,
    )

    _add_box_tracks(
        footprint,
        left_mils=-31.4960,
        bottom_mils=-15.7480,
        right_mils=31.4961,
        top_mils=15.7480,
        width_mils=3.9370,
        layer=PcbLayer.MECHANICAL_13,
    )

    footprint.add_embedded_3d_model(
        step_model,
        layer=PcbLayer.MECHANICAL_13,
        side=PcbBodyProjection.TOP,
    )

    pcblib.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcblib()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
