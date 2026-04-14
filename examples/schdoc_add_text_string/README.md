# schdoc_add_text_string

Open a blank schematic, add several text strings, and save the modified
`.SchDoc`.

This example shows the canonical public create-then-insert path for standalone
schematic text strings:

1. load an existing `SchDoc`
2. describe each text anchor with `SchPointMils`
3. call `make_sch_text_string(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached text strings with `make_sch_text_string(...)`
2. using `SchPointMils` for absolute placement
3. using `SchFontSpec` instead of raw `font_id`
4. using `TextOrientation` for 90-degree text rotation
5. using `TextJustification` for anchor alignment
6. using `ColorValue` helpers instead of raw Win32 color integers
7. reopening the written file and inspecting the created text strings

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_text_string\schdoc_add_text_string.py
```

## Input

This sample uses:

```text
examples/schdoc_add_text_string/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_text_string/output/blank_with_text_strings.SchDoc
```

## Expected Result

The output file should contain three visible text strings:

1. bottom-left justified, `Arial 10`
2. centered and rotated 90 degrees, `Courier New 12`
3. top-right justified and mirrored, `Times New Roman 14`

Each text string uses a different anchor location so it is easy to verify the
placement visually in Altium. The script also prints the reopened text, font,
orientation, justification, mirror state, location, and color for a quick
terminal sanity check.

Text strings are single-line objects. If you need multiline text, use
`make_sch_text_frame(...)` or `make_sch_note(...)` instead.
