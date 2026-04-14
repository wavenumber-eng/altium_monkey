from pathlib import Path

from altium_monkey import AltiumPcbDoc, PcbLayer

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_track.PcbDoc"
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

    pcbdoc.add_track(
        (700.0, 5200.0),
        (5300.0, 5200.0),
        width_mils=6.0,
        layer=PcbLayer.TOP,
        net="TRACE_TOP_6MIL",
    )
    pcbdoc.add_track(
        (700.0, 4700.0),
        (5300.0, 4700.0),
        width_mils=12.0,
        layer=PcbLayer.TOP,
        net="TRACE_TOP_12MIL",
    )
    pcbdoc.add_track(
        (700.0, 4200.0),
        (5300.0, 4200.0),
        width_mils=24.0,
        layer=PcbLayer.BOTTOM,
        net="TRACE_BOTTOM_24MIL",
    )
    pcbdoc.add_track(
        (700.0, 3300.0),
        (1900.0, 2900.0),
        width_mils=10.0,
        layer=PcbLayer.TOP,
        net="TRACE_POLYLINE",
    )
    pcbdoc.add_track(
        (1900.0, 2900.0),
        (3100.0, 3300.0),
        width_mils=10.0,
        layer=PcbLayer.TOP,
        net="TRACE_POLYLINE",
    )
    pcbdoc.add_track(
        (3100.0, 3300.0),
        (5300.0, 3300.0),
        width_mils=10.0,
        layer=PcbLayer.TOP,
        net="TRACE_POLYLINE",
    )
    pcbdoc.add_track(
        (700.0, 2400.0),
        (5300.0, 2400.0),
        width_mils=8.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_track(
        (700.0, 1900.0),
        (5300.0, 1900.0),
        width_mils=8.0,
        layer=PcbLayer.MECHANICAL_1,
    )
    pcbdoc.add_track(
        (700.0, 1400.0),
        (5300.0, 1400.0),
        width_mils=40.0,
        layer=PcbLayer.KEEPOUT,
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
