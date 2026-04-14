from pathlib import Path

from altium_monkey import AltiumPcbLib, PadShape, PcbBodyProjection, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcblib_add_free_3d_extruded.PcbLib"


def build_pcblib(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint(
        "FREE_EXTRUDED_BODY_DEMO",
        height="120mil",
        description="Footprint with a generic extruded 3D body",
    )

    for designator, x_mils in (("1", -300.0), ("2", 300.0)):
        footprint.add_pad(
            designator=designator,
            position_mils=(x_mils, 0.0),
            width_mils=120.0,
            height_mils=160.0,
            layer=PcbLayer.TOP,
            shape=PadShape.ROUNDED_RECTANGLE,
            rotation_degrees=0.0,
        )

    footprint.add_track(
        (-500.0, -180.0),
        (500.0, -180.0),
        width_mils=8.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    footprint.add_track(
        (-500.0, 180.0),
        (500.0, 180.0),
        width_mils=8.0,
        layer=PcbLayer.TOP_OVERLAY,
    )

    footprint.add_extruded_3d_body(
        outline_points_mils=[
            (-420.0, -140.0),
            (420.0, -140.0),
            (500.0, 0.0),
            (420.0, 140.0),
            (-420.0, 140.0),
            (-500.0, 0.0),
        ],
        layer=PcbLayer.MECHANICAL_1,
        overall_height_mils=120.0,
        standoff_height_mils=0.0,
        side=PcbBodyProjection.TOP,
        name="FREE_EXTRUDED_BODY_DEMO",
        body_color_3d=0x70A0D0,
        opacity=0.9,
    )

    pcblib.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcblib()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
