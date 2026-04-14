from __future__ import annotations

from pathlib import Path

from altium_monkey import (
    AltiumSchDoc,
    AltiumSchSheetSymbol,
    ColorValue,
    LineWidth,
    SchFontSpec,
    SchPointMils,
    SchRectMils,
    SchSheetEntryArrowKind,
    SchSheetEntryIOType,
    SchSheetSymbolType,
    SheetEntrySide,
    TextJustification,
    TextOrientation,
    make_sch_file_name,
    make_sch_sheet_entry,
    make_sch_sheet_name,
    make_sch_sheet_symbol,
)


SAMPLE_DIR = Path(__file__).resolve().parent
INPUT_SCHDOC = SAMPLE_DIR / "input" / "blank.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
OUTPUT_SCHDOC = OUTPUT_DIR / "blank_with_sheet_symbols.SchDoc"


def _build_ctrl_symbol() -> AltiumSchSheetSymbol:
    symbol = make_sch_sheet_symbol(
        bounds_mils=SchRectMils.from_corners_mils(1000, 5200, 3000, 3600),
        border_width=LineWidth.LARGE,
        border_color=ColorValue.from_hex("#202020"),
        fill_color=ColorValue.from_hex("#F0E6C8"),
        fill_background=True,
        symbol_type=SchSheetSymbolType.NORMAL,
        show_hidden_fields=False,
        design_item_id="CTRL_CORE",
        source_library_name="generated",
        revision_name="A",
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="CLK_MAIN",
            side=SheetEntrySide.LEFT,
            io_type=SchSheetEntryIOType.INPUT,
            distance_from_top_mils=100,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
            arrow_kind=SchSheetEntryArrowKind.TRIANGLE,
        )
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="RST_N",
            side=SheetEntrySide.LEFT,
            io_type=SchSheetEntryIOType.INPUT,
            distance_from_top_mils=300,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
        )
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="READY_N",
            side=SheetEntrySide.RIGHT,
            io_type=SchSheetEntryIOType.OUTPUT,
            distance_from_top_mils=500,
            font=SchFontSpec(name="Arial", size=10),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
        )
    )
    symbol.set_sheet_name(
        make_sch_sheet_name(
            text="CTRL_CORE",
            location_mils=SchPointMils.from_mils(1100, 5300),
            font=SchFontSpec(name="Arial", size=14, bold=True),
            color=ColorValue.from_hex("#303030"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.BOTTOM_LEFT,
        )
    )
    symbol.set_file_name(
        make_sch_file_name(
            text="ctrl_core.SchDoc",
            location_mils=SchPointMils.from_mils(1100, 3500),
            font=SchFontSpec(name="Arial", size=10, italic=True),
            color=ColorValue.from_hex("#505050"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.TOP_LEFT,
        )
    )
    return symbol


def _build_power_symbol() -> AltiumSchSheetSymbol:
    symbol = make_sch_sheet_symbol(
        bounds_mils=SchRectMils.from_corners_mils(3800, 5200, 5900, 3600),
        border_width=LineWidth.MEDIUM,
        border_color=ColorValue.from_hex("#2D2D2D"),
        fill_color=ColorValue.from_hex("#DCEAFF"),
        fill_background=True,
        symbol_type=SchSheetSymbolType.DEVICE_SHEET,
        show_hidden_fields=True,
        design_item_id="POWER_IO",
        source_library_name="generated",
        revision_name="B",
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="VIN_12V",
            side=SheetEntrySide.LEFT,
            io_type=SchSheetEntryIOType.INPUT,
            distance_from_top_mils=200,
            font=SchFontSpec(name="Arial", size=10, bold=True),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
        )
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="PWR_GOOD",
            side=SheetEntrySide.RIGHT,
            io_type=SchSheetEntryIOType.OUTPUT,
            distance_from_top_mils=400,
            font=SchFontSpec(name="Arial", size=10),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
            arrow_kind=SchSheetEntryArrowKind.ARROW,
        )
    )
    symbol.add_entry(
        make_sch_sheet_entry(
            name="I2C_SDA",
            side=SheetEntrySide.RIGHT,
            io_type=SchSheetEntryIOType.BIDIRECTIONAL,
            distance_from_top_mils=700,
            font=SchFontSpec(name="Arial", size=10),
            border_color=ColorValue.from_hex("#101010"),
            fill_color=ColorValue.from_hex("#FFFFFF"),
            text_color=ColorValue.from_hex("#101010"),
        )
    )
    symbol.set_sheet_name(
        make_sch_sheet_name(
            text="POWER_IO",
            location_mils=SchPointMils.from_mils(3900, 5300),
            font=SchFontSpec(name="Arial", size=14, bold=True),
            color=ColorValue.from_hex("#2C4058"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.BOTTOM_LEFT,
        )
    )
    symbol.set_file_name(
        make_sch_file_name(
            text="power_io.SchDoc",
            location_mils=SchPointMils.from_mils(3900, 3500),
            font=SchFontSpec(name="Arial", size=10, italic=True),
            color=ColorValue.from_hex("#4F647C"),
            orientation=TextOrientation.DEGREES_0,
            justification=TextJustification.TOP_LEFT,
        )
    )
    return symbol


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    schdoc.add_object(_build_ctrl_symbol())
    schdoc.add_object(_build_power_symbol())
    schdoc.save(OUTPUT_SCHDOC)

    reopened = AltiumSchDoc(OUTPUT_SCHDOC)
    print(f"Sheet symbols: {len(reopened.sheet_symbols)}")
    for index, symbol in enumerate(reopened.sheet_symbols, start=1):
        entry_names = [entry.name for entry in symbol.entries]
        symbol_kind = "device-sheet" if symbol.is_device_sheet else "normal"
        sheet_name = symbol.sheet_name.text if symbol.sheet_name is not None else ""
        file_name = symbol.file_name.text if symbol.file_name is not None else ""
        print(
            f"Symbol {index}: {symbol_kind} {sheet_name} -> {file_name} entries={entry_names}"
        )
    print(f"Wrote: {OUTPUT_SCHDOC.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
