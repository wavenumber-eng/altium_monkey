# schdoc_add_sheet_symbol

Open a blank schematic, append detached sheet-symbol groups, and save the
modified `.SchDoc`.

This example shows the canonical public path for hierarchy records:

1. load an existing `SchDoc`
2. build one detached `make_sch_sheet_symbol(...)`
3. attach detached `make_sch_sheet_entry(...)` records through `symbol.add_entry(...)`
4. attach detached `make_sch_sheet_name(...)` and `make_sch_file_name(...)`
5. add only the top-level sheet symbol with `AltiumSchDoc.add_object(...)`
6. persist the result with `AltiumSchDoc.save(...)`

## What It Shows

1. using `SchRectMils` for the symbol body bounds
2. using `SchSheetSymbolType`, `SheetEntrySide`, `SchSheetEntryIOType`, and `SchSheetEntryArrowKind` instead of raw integers
3. resolving entry and label fonts from `SchFontSpec`
4. using `ColorValue` helpers instead of raw Win32 color integers
5. keeping entries, sheet name, and file name symbol-owned rather than top-level document objects
6. reopening the written file and printing a concise summary of the created hierarchy groups

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_sheet_symbol\schdoc_add_sheet_symbol.py
```

## Input

This sample uses:

```text
examples/schdoc_add_sheet_symbol/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_sheet_symbol/output/blank_with_sheet_symbols.SchDoc
```

## Expected Result

The output file should contain two hierarchical sheet symbols:

1. a normal `CTRL_CORE` symbol with left-side clock and reset entries plus a right-side ready entry
2. a device-sheet `POWER_IO` symbol with a rounded body and mixed input, output, and bidirectional entries

Only the parent sheet symbols are added to the document. Their child entries and
labels are symbol-owned and follow the parent automatically when the file is
saved.
