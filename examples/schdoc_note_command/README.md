# schdoc_note_command

Open an existing schematic that already contains two notes, mutate one note in
place, delete the other, and save the modified `.SchDoc`.

This example is the first explicit query-then-mutate workflow for schematic
objects. It shows that records returned from the document can be edited through
their public properties and removed through the document container API.

## What It Shows

1. loading an existing `SchDoc`
2. iterating existing note objects from `schdoc.notes`
3. changing a note font with `note.font = SchFontSpec(...)`
4. deleting an existing note with `AltiumSchDoc.remove_object(...)`
5. persisting the result with `AltiumSchDoc.save(...)`
6. reopening the written file and checking the surviving notes

## Run

From the project root:

```powershell
uv run python examples\schdoc_note_command\schdoc_note_command.py
```

## Input

This sample uses:

```text
examples/schdoc_note_command/input/note_commands_input.SchDoc
```

The input schematic contains two visible notes:

1. `change my font to courier 12`
2. `delete me`

## Output

The script writes:

```text
examples/schdoc_note_command/output/note_commands_output.SchDoc
```

## Expected Result

The output file should contain exactly one note:

1. text: `change my font to courier 12`
2. font: `Courier New 12`

The note with text `delete me` should be removed from the document.

The script prints the note counts before and after the mutation, the number of
updated and removed notes, and the surviving note's resolved font.
