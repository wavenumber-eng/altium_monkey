from pathlib import Path

from altium_monkey import AltiumPcbDoc, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_filled_region.PcbDoc"
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

    pcbdoc.add_region(
        outline_points_mils=[
            (800.0, 5000.0),
            (2000.0, 5000.0),
            (2000.0, 4000.0),
            (800.0, 4000.0),
        ],
        layer=PcbLayer.TOP,
        net="REGION_RECT",
    )
    pcbdoc.add_region(
        outline_points_mils=[
            (2800.0, 5000.0),
            (5200.0, 5000.0),
            (5200.0, 4600.0),
            (4000.0, 4600.0),
            (4000.0, 4000.0),
            (2800.0, 4000.0),
        ],
        layer=PcbLayer.TOP,
        net="REGION_L_SHAPE",
    )
    pcbdoc.add_region(
        outline_points_mils=[
            (800.0, 3200.0),
            (2200.0, 3200.0),
            (2200.0, 1800.0),
            (800.0, 1800.0),
        ],
        hole_points_mils=[
            [
                (1200.0, 2800.0),
                (1800.0, 2800.0),
                (1800.0, 2200.0),
                (1200.0, 2200.0),
            ]
        ],
        layer=PcbLayer.BOTTOM,
        net="REGION_WITH_HOLE",
    )
    pcbdoc.add_region(
        outline_points_mils=[
            (3200.0, 3200.0),
            (5200.0, 2900.0),
            (4700.0, 1800.0),
            (3000.0, 2100.0),
        ],
        layer=PcbLayer.KEEPOUT,
        is_keepout=True,
        keepout_restrictions=31,
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
