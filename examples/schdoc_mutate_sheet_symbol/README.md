# schdoc_mutate_sheet_symbol

Open a schematic with one existing sheet-symbol group, mutate it through the
live record API, and save the modified `.SchDoc`.

This example demonstrates the attached-record mutation path after a document has
already been parsed:

1. load an existing `SchDoc`
2. get the parsed `sheet_symbol` from `schdoc.sheet_symbols`
3. find existing entries with `symbol.get_entry(...)`
4. rename, move, add, and remove entries through the live symbol API
5. replace the sheet-name and file-name labels with new detached label records
6. persist the result with `AltiumSchDoc.save(...)`

## What It Shows

1. mutating entry text and font in place on already-attached records
2. changing public entry offsets through `distance_from_top_mils`
3. reordering entries with `move_entry(...)`
4. removing an existing entry and adding a new one
5. replacing the symbol-owned sheet-name and file-name labels
6. reopening the written file and printing the resulting group summary

## Run

From the project root:

```powershell
uv run python examples\schdoc_mutate_sheet_symbol\schdoc_mutate_sheet_symbol.py
```

## Input

This sample uses:

```text
examples/schdoc_mutate_sheet_symbol/input/sheet_symbol_input.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_mutate_sheet_symbol/output/sheet_symbol_output.SchDoc
```

## Expected Result

The output file should contain one updated sheet symbol with:

1. renamed entries `FAULT_N`, `CLK_SYNC`, and `I2C_SDA`
2. the removed `DBG_TX` entry no longer present
3. a replacement sheet name of `CTRL_MUTATED`
4. a replacement file name of `ctrl_mutated.SchDoc`

This example intentionally uses the live parsed record objects. Once a sheet
symbol is attached to a document, its entry list and owned labels stay grouped
with the parent automatically when you save.
