# schdoc_add_generic_no_erc

Open a blank schematic, add several detached generic No ERC directives, and
save the modified `.SchDoc`.

This example keeps the public mutation pattern the same as the earlier
schematic primitive samples:

1. load an existing `SchDoc`
2. describe each directive location with `SchPointMils`
3. call `make_sch_no_erc(...)`
4. insert every detached directive with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached generic No ERC directives with `make_sch_no_erc(...)`
2. using `SchPointMils` for public directive coordinates
3. using the native `NoErcSymbol` enum instead of raw integers
4. using the native `Rotation90` enum for directive orientation
5. using `ColorValue` helpers instead of raw Win32 color integers
6. carrying the advanced partial-suppression strings through the public
   factory when `suppress_all=False`
7. reopening the written file and inspecting the created directives

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_generic_no_erc\schdoc_add_generic_no_erc.py
```

## Input

This sample uses:

```text
examples/schdoc_add_generic_no_erc/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_generic_no_erc/output/blank_with_no_ercs.SchDoc
```

## Expected Result

The output file should contain:

1. a top row showing the native No ERC symbol family
2. a second row showing the same symbol at several `Rotation90` orientations
3. one inactive marker example
4. one partial-suppression example with `suppress_all=False`
5. all directives colored in Altium's native directive red

The script also prints the reopened directive locations, symbols, orientations,
and suppression flags so the example is easy to sanity-check without decoding
the binary file directly.
