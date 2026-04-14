from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    ColorValue,
    SchFontSpec,
    SchPointMils,
    SchSheetEntryIOType,
    SheetEntrySide,
    TextJustification,
    TextOrientation,
    make_sch_file_name,
    make_sch_sheet_entry,
    make_sch_sheet_name,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "sheet_symbol_input.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "sheet_symbol_output.SchDoc"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    sheet_symbol = schdoc.sheet_symbols[0]

    clk_main = sheet_symbol.get_entry("CLK_MAIN")
    rst_n = sheet_symbol.get_entry("RST_N")
    dbg_tx = sheet_symbol.get_entry("DBG_TX")
    if clk_main is None or rst_n is None or dbg_tx is None:
        raise RuntimeError("Input sample is missing one or more expected sheet entries")

    clk_main.name = "CLK_SYNC"
    clk_main.font = SchFontSpec(name="Arial", size=12, bold=True)
    rst_n.name = "FAULT_N"
    rst_n.distance_from_top_mils = 500
    rst_n.font = SchFontSpec(name="Arial", size=10, bold=True)
    sheet_symbol.move_entry("FAULT_N", index=0)
    if not sheet_symbol.remove_entry(dbg_tx):
        raise RuntimeError("Expected to remove DBG_TX from the input symbol")

    sheet_symbol.add_entry(
        make_sch_sheet_entry(
            name="I2C_SDA",
            side=SheetEntrySide.RIGHT,
            io_type=SchSheetEntryIOType.BIDIRECTIONAL,
            distance_from_top_mils=900,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
        )
    )
    sheet_symbol.set_sheet_name(
        make_sch_sheet_name(
            text="CTRL_MUTATED",
            location_mils=SchPointMils.from_mils(1700, 5100),
            font=SchFontSpec(name="Arial", size=16, bold=True),
            color=ColorValue.from_hex("#20374F"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.BOTTOM_LEFT,
        )
    )
    sheet_symbol.set_file_name(
        make_sch_file_name(
            text="ctrl_mutated.SchDoc",
            location_mils=SchPointMils.from_mils(1700, 3300),
            font=SchFontSpec(name="Arial", size=10, italic=True),
            color=ColorValue.from_hex("#40576D"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.TOP_LEFT,
        )
    )

    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    reopened_symbol = reopened.sheet_symbols[0]
    print(f"Sheet symbols: {len(reopened.sheet_symbols)}")
    print(
        "Entries: "
        f"{[(entry.name, entry.distance_from_top_mils) for entry in reopened_symbol.entries]}"
    )
    print(
        "Sheet name: "
        f"{reopened_symbol.sheet_name.text if reopened_symbol.sheet_name is not None else ''}"
    )
    print(
        "File name: "
        f"{reopened_symbol.file_name.text if reopened_symbol.file_name is not None else ''}"
    )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
