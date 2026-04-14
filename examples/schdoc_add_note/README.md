# schdoc_add_note

Open a blank schematic, append one top-level note, and save the modified
`.SchDoc`.

This is the smallest existing-document mutation example for schematic notes. It
is intended to show the canonical public create-then-insert path:

1. load an existing `SchDoc`
2. describe note bounds with public coordinate helpers
3. call `make_sch_note(...)`
4. insert the object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. describing note bounds with `SchPointMils` and `SchRectMils`
2. creating a detached note with `make_sch_note(...)`
3. inserting the note with `add_object(...)`
4. resolving a note font from `SchFontSpec` at `add_object(...)` time
5. using `SchHorizontalAlign` instead of raw alignment integers
6. using `ColorValue` helpers instead of raw Win32 color integers
7. using `save()` as the canonical public write path
8. reopening the written file and checking the inserted note

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_note\schdoc_add_note.py
```

## Input

This sample uses:

```text
examples/schdoc_add_note/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_note/output/blank_with_note.SchDoc
```

## Expected Result

The output file should contain one visible note with these characteristics:

1. text: `a note from altium-monkey`
2. author: `altium-monkey`
3. font: `Arial 10`
4. border color: `#000000`
5. fill color: `#CCFFFF`
6. text color: `#000000`
7. bounds: `(1000, 3000) -> (3000, 4000)` mil

The script also prints the public bounds helper, reopened note bounds, font,
and `IndexInSheet` so the example is easy to sanity-check without digging into
the binary file.

This example intentionally spells out the current public `make_sch_note(...)`
surface. Font styling is expressed through `SchFontSpec` instead of raw
`font_id` or direct font-table access. Alignment is expressed as
`SchHorizontalAlign.LEFT`, colors are passed through `ColorValue` helpers, and
the note bounds are built through `SchPointMils` / `SchRectMils` instead of
anonymous coordinate tuples.

Altium schematic notes do not preserve configurable border thickness on reopen.
The native V5 importer forces notes back to the smallest border size, so
`make_sch_note(...)` does not expose a `line_width` parameter even though the
underlying record type inherits rectangle-style fields.
