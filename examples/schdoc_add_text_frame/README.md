# schdoc_add_text_frame

Open a blank schematic, add several text frames, and save the modified
`.SchDoc`.

This example is the text-frame companion to `schdoc_add_note`. It shows the
canonical public create-then-insert path for the standalone text-frame object:

1. load an existing `SchDoc`
2. describe absolute frame rectangles in mils
3. call `make_sch_text_frame(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached text frames with `make_sch_text_frame(...)`
2. using `SchRectMils` for absolute placement
3. using `SchFontSpec` instead of raw `font_id`
4. using `SchHorizontalAlign` instead of raw alignment integers
5. using `LineWidth` for border thickness
6. using `ColorValue` helpers instead of raw Win32 color integers
7. reopening the written file and inspecting the created text frames

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_text_frame\schdoc_add_text_frame.py
```

## Input

This sample uses:

```text
examples/schdoc_add_text_frame/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_text_frame/output/blank_with_text_frames.SchDoc
```

## Expected Result

The output file should contain three visible text frames:

1. left aligned, `LineWidth.SMALL`, `Arial 10`
2. center aligned, `LineWidth.MEDIUM`, `Courier New 12`
3. right aligned, `LineWidth.LARGE`, `Times New Roman 14`

Each frame uses a different absolute rectangle so it is easy to verify the
placement visually in Altium. The script also prints the reopened frame count,
alignment, line width, font, and bounds for a quick terminal sanity check.
