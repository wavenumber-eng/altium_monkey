# schdoc_move_note

Open an existing schematic that already contains one note, set that note to a
new absolute rectangle, and save the modified `.SchDoc`.

This example is a focused geometry-mutation probe. It shows how to set an
existing object's absolute position without touching internal Altium coordinate
units.

## What It Shows

1. loading an existing `SchDoc`
2. reading note bounds through `note.bounds_mils`
3. creating a new absolute `SchRectMils`
4. assigning that rectangle back through `note.bounds_mils`
5. saving with `AltiumSchDoc.save(...)`
6. reopening and checking the moved bounds

## Run

From the project root:

```powershell
uv run python examples\schdoc_move_note\schdoc_move_note.py
```

## Input

This sample uses:

```text
examples/schdoc_move_note/input/note_move_input.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_move_note/output/note_move_output.SchDoc
```

## Expected Result

The input note starts at:

1. `(1000, 3000) -> (3000, 4000)` mil

The sample sets the note to:

1. `(2500, 2500) -> (4500, 3500)` mil

This example uses the public structured geometry helpers instead of raw internal
10-mil document units. `location_mils` exists for single-point placement, while
`bounds_mils` is the more natural fit for note/text-frame style records.
