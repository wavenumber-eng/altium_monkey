# hello_schdoc

Create a schematic from scratch, place a text label, and embed a local monkey
image.

This example is intentionally small. It is meant to show the public
object-creation API, not internal record-plumbing.

## What It Shows

1. `AltiumSchDoc()` for a fresh schematic document
2. `make_sch_text_string(...)`
3. `make_sch_embedded_image(...)`
4. `AltiumSchDoc.add_object(...)`
5. `AltiumSchDoc.save(...)`

## Run

From the package root:

```powershell
uv run python examples\hello_schdoc\hello_schdoc.py
```

## Output

The script writes:

```text
examples/hello_schdoc/output/hello_schdoc.SchDoc
```

The sample-specific image asset lives at:

```text
examples/hello_schdoc/assets/monkey.png
```
