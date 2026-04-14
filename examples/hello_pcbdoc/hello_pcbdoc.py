from pathlib import Path

from altium_monkey import AltiumBoardOutline, AltiumPcbDoc, BoardOutlineVertex, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SAMPLE_DIR / "output" / "hello_pcbdoc.PcbDoc"

BOARD_WIDTH_MILS = 4000.0
BOARD_HEIGHT_MILS = 2500.0


def make_rectangular_outline_mils(
    width_mils: float,
    height_mils: float,
) -> AltiumBoardOutline:
    """
    Create board-outline vertices with the origin at the lower left.
    """
    return AltiumBoardOutline(
        vertices=[
            BoardOutlineVertex.line(0.0, 0.0),
            BoardOutlineVertex.line(width_mils, 0.0),
            BoardOutlineVertex.line(width_mils, height_mils),
            BoardOutlineVertex.line(0.0, height_mils),
        ]
    )


def build_pcbdoc(output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pcbdoc = AltiumPcbDoc()
    board_outline = make_rectangular_outline_mils(BOARD_WIDTH_MILS, BOARD_HEIGHT_MILS)
    pcbdoc.set_board_outline(board_outline)
    pcbdoc.set_origin_to_outline_lower_left()
    pcbdoc.add_text(
        text="altium-monkey wuz here",
        position_mils=(250.0, 2250.0),
        height_mils=150.0,
        stroke_width_mils=18.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
