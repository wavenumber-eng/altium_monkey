# schdoc_add_embedded_image

Open a blank schematic, add one embedded image, and save the modified
`.SchDoc`.

This example shows the canonical public object path for embedded schematic
images:

1. load an existing `SchDoc`
2. describe the image rectangle in mils
3. call `make_sch_embedded_image(...)`
4. insert the image with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating a detached embedded image with `make_sch_embedded_image(...)`
2. using `SchRectMils` for absolute placement
3. using `Rotation90` for image orientation
4. using `LineWidth` and `ColorValue` for border styling
5. saving embedded image payloads through the normal `SchDoc` object path
6. reopening the written file and checking the embedded payload

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_embedded_image\schdoc_add_embedded_image.py
```

## Input

This sample uses:

```text
examples/schdoc_add_embedded_image/input/blank.SchDoc
examples/schdoc_add_embedded_image/examples/monkey.png
```

## Output

The script writes:

```text
examples/schdoc_add_embedded_image/output/blank_with_embedded_image.SchDoc
```

## Expected Result

The output file should contain one visible embedded image with these
characteristics:

1. source asset: `monkey.png`
2. bounds: `(1500, 2500) -> (3900, 4900)` mil
3. aspect ratio preserved
4. border enabled with `LineWidth.MEDIUM`
5. one embedded payload entry in the storage stream

The script also prints the reopened image format, bounds, border width, and
embedded storage count for a quick terminal sanity check.
