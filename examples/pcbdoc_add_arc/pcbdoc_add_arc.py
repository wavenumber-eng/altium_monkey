from pathlib import Path

from altium_monkey import AltiumPcbDoc, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_arc.PcbDoc"
BOARD_WIDTH_MILS = 6000.0
BOARD_HEIGHT_MILS = 6000.0


def _new_demo_board() -> AltiumPcbDoc:
    pcbdoc = AltiumPcbDoc.from_file(INPUT_PCBDOC)
    pcbdoc.set_outline_rectangle_mils(0.0, 0.0, BOARD_WIDTH_MILS, BOARD_HEIGHT_MILS)
    pcbdoc.set_origin_to_outline_lower_left()
    return pcbdoc


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcbdoc = _new_demo_board()

    for index, radius_mils in enumerate((150.0, 250.0, 350.0, 500.0, 650.0)):
        pcbdoc.add_arc(
            center_mils=(900.0 + index * 1000.0, 5000.0),
            radius_mils=radius_mils,
            start_angle_degrees=0.0,
            end_angle_degrees=360.0,
            width_mils=8.0,
            layer=PcbLayer.TOP_OVERLAY,
        )

    arc_cases = [
        (0.0, 90.0, PcbLayer.TOP, "ARC_0_90"),
        (90.0, 180.0, PcbLayer.TOP, "ARC_90_180"),
        (180.0, 270.0, PcbLayer.BOTTOM, "ARC_180_270"),
        (270.0, 45.0, PcbLayer.BOTTOM, "ARC_WRAP"),
        (30.0, 245.0, PcbLayer.MECHANICAL_1, None),
    ]
    for index, (start_angle, end_angle, layer, net) in enumerate(arc_cases):
        pcbdoc.add_arc(
            center_mils=(900.0 + index * 1000.0, 3300.0),
            radius_mils=260.0,
            start_angle_degrees=start_angle,
            end_angle_degrees=end_angle,
            width_mils=10.0 + index * 2.0,
            layer=layer,
            net=net,
        )

    for index, (start_angle, end_angle) in enumerate(
        ((45.0, 135.0), (135.0, 225.0), (225.0, 315.0), (315.0, 45.0))
    ):
        pcbdoc.add_arc(
            center_mils=(1200.0 + index * 1200.0, 1600.0),
            radius_mils=420.0,
            start_angle_degrees=start_angle,
            end_angle_degrees=end_angle,
            width_mils=16.0,
            layer=PcbLayer.TOP_OVERLAY,
        )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
