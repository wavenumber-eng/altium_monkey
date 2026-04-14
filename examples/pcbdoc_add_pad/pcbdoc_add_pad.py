from pathlib import Path

from altium_monkey import AltiumPcbDoc, PadShape, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_pad.PcbDoc"
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

    pcbdoc.add_pad(
        designator="SMT1",
        position_mils=(1000.0, 4800.0),
        width_mils=70.0,
        height_mils=120.0,
        shape=PadShape.RECTANGLE,
        layer=PcbLayer.TOP,
        net="PAD_TOP",
    )
    pcbdoc.add_pad(
        designator="SMT2",
        position_mils=(2000.0, 4800.0),
        width_mils=120.0,
        height_mils=120.0,
        shape=PadShape.CIRCLE,
        layer=PcbLayer.TOP,
        net="PAD_TOP",
    )
    pcbdoc.add_pad(
        designator="SMT3",
        position_mils=(3000.0, 4800.0),
        width_mils=160.0,
        height_mils=100.0,
        shape=PadShape.ROUNDED_RECTANGLE,
        corner_radius_percent=50,
        rotation_degrees=30.0,
        layer=PcbLayer.TOP,
        net="PAD_ROUNDRECT",
    )
    pcbdoc.add_pad(
        designator="BOT1",
        position_mils=(4000.0, 4800.0),
        width_mils=160.0,
        height_mils=90.0,
        shape=PadShape.OCTAGONAL,
        layer=PcbLayer.BOTTOM,
        net="PAD_BOTTOM",
    )
    pcbdoc.add_pad(
        designator="TH1",
        position_mils=(1000.0, 3300.0),
        width_mils=120.0,
        height_mils=120.0,
        shape=PadShape.CIRCLE,
        layer=PcbLayer.MULTI_LAYER,
        hole_size_mils=40.0,
        plated=True,
        net="PAD_PLATED_TH",
    )
    pcbdoc.add_pad(
        designator="TH2",
        position_mils=(2000.0, 3300.0),
        width_mils=140.0,
        height_mils=120.0,
        shape=PadShape.RECTANGLE,
        layer=PcbLayer.MULTI_LAYER,
        hole_size_mils=45.0,
        plated=True,
        rotation_degrees=45.0,
        net="PAD_PLATED_RECT",
    )
    pcbdoc.add_pad(
        designator="NPTH",
        position_mils=(3000.0, 3300.0),
        width_mils=100.0,
        height_mils=100.0,
        shape=PadShape.CIRCLE,
        layer=PcbLayer.MULTI_LAYER,
        hole_size_mils=70.0,
        plated=False,
        solder_mask_expansion_mils=0.0,
        paste_mask_expansion_mils=0.0,
    )
    pcbdoc.add_pad(
        designator="SLOT",
        position_mils=(4000.0, 3300.0),
        width_mils=220.0,
        height_mils=130.0,
        shape=PadShape.ROUNDED_RECTANGLE,
        corner_radius_percent=40,
        layer=PcbLayer.MULTI_LAYER,
        hole_size_mils=45.0,
        slot_length_mils=150.0,
        slot_rotation_degrees=90.0,
        plated=True,
        net="PAD_PLATED_SLOT",
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
