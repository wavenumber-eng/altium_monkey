from pathlib import Path

from altium_monkey import (
    AltiumPcbDoc,
    PcbBarcodeKind,
    PcbBarcodeRenderMode,
    PcbLayer,
    PcbTextJustification,
    PcbTextKind,
)

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
INPUT_PCBDOC = EXAMPLES_ROOT / "assets" / "pcbdoc" / "blank.PcbDoc"
OUTPUT_PATH = SAMPLE_DIR / "output" / "pcbdoc_add_text.PcbDoc"
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

    pcbdoc.add_text(
        text="Stroke font",
        position_mils=(700.0, 5300.0),
        height_mils=90.0,
        stroke_width_mils=8.0,
        font_kind=PcbTextKind.STROKE,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="Stroke 45deg",
        position_mils=(3300.0, 5300.0),
        height_mils=80.0,
        stroke_width_mils=6.0,
        font_kind=PcbTextKind.STROKE,
        rotation_degrees=45.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="TrueType Arial Bold Italic",
        position_mils=(700.0, 4500.0),
        height_mils=110.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        bold=True,
        italic=True,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="Mirrored bottom text",
        position_mils=(3300.0, 4500.0),
        height_mils=90.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        is_mirrored=True,
        layer=PcbLayer.BOTTOM_OVERLAY,
    )
    pcbdoc.add_text(
        text="INV margin 10",
        position_mils=(700.0, 3500.0),
        height_mils=90.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        is_inverted=True,
        inverted_margin_mils=10.0,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="INV rect centered",
        position_mils=(3300.0, 3500.0),
        height_mils=80.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        is_inverted=True,
        inverted_margin_mils=12.0,
        use_inverted_rectangle=True,
        inverted_rectangle_size_mils=(900.0, 220.0),
        text_justification=PcbTextJustification.CENTER_CENTER,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="Multiline text frame\r\nLine two inside frame\r\nLine three",
        position_mils=(3300.0, 1350.0),
        height_mils=85.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        is_frame=True,
        frame_size_mils=(2000.0, 900.0),
        text_justification=PcbTextJustification.LEFT_TOP,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="AM-12345",
        position_mils=(700.0, 2350.0),
        height_mils=80.0,
        font_kind=PcbTextKind.BARCODE,
        font_name="Arial",
        barcode_kind=PcbBarcodeKind.CODE_39,
        barcode_render_mode=PcbBarcodeRenderMode.BY_FULL_WIDTH,
        barcode_full_size_mils=(1300.0, 260.0),
        barcode_margin_mils=(0.0, 0.0),
        barcode_show_text=True,
        barcode_inverted=False,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="12345678",
        position_mils=(3300.0, 2350.0),
        height_mils=80.0,
        font_kind=PcbTextKind.BARCODE,
        font_name="Arial",
        barcode_kind=PcbBarcodeKind.CODE_128,
        barcode_render_mode=PcbBarcodeRenderMode.BY_MIN_WIDTH,
        barcode_min_width_mils=3.0,
        barcode_full_size_mils=(1300.0, 260.0),
        barcode_margin_mils=(0.0, 0.0),
        barcode_show_text=False,
        barcode_inverted=False,
        layer=PcbLayer.TOP_OVERLAY,
    )
    pcbdoc.add_text(
        text="LOT-2026-04",
        position_mils=(700.0, 1400.0),
        height_mils=80.0,
        font_kind=PcbTextKind.BARCODE,
        font_name="Arial",
        barcode_kind=PcbBarcodeKind.CODE_128,
        barcode_render_mode=PcbBarcodeRenderMode.BY_FULL_WIDTH,
        barcode_full_size_mils=(1800.0, 320.0),
        barcode_margin_mils=(20.0, 20.0),
        barcode_show_text=True,
        barcode_inverted=True,
        layer=PcbLayer.TOP_OVERLAY,
    )

    pcbdoc.save(output_path)
    return output_path


def main() -> None:
    output_path = build_pcbdoc()
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
