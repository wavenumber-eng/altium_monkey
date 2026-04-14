# Extract SchDoc Images

Open a schematic, find embedded IMAGE records, and write each embedded image
payload to disk.

This example uses Hydroscope's top-level schematic because it contains several
placed images. The image records reference original `.png` source names, but the
embedded payloads in this file are stored by Altium as BMP data. The extraction
API therefore detects the output extension from the embedded bytes instead of
trusting the source filename extension.

Run from the package root:

```powershell
uv run python examples\schdoc_extract_images\schdoc_extract_images.py
```

The example uses:

```text
examples/assets/projects/hydroscope/TOP_LEVEL.SchDoc
```

It writes:

```text
examples/schdoc_extract_images/output/extracted_images/
examples/schdoc_extract_images/output/image_manifest.json
```

The important pattern is:

```python
schdoc = AltiumSchDoc(INPUT_SCHDOC)
written_paths = schdoc.extract_embedded_images(OUTPUT_IMAGES_DIR)
```

`AltiumSchDoc.extract_embedded_images(...)` writes one file per placed embedded
IMAGE record as `<index>__<source stem>.<detected extension>`. Linked image
records without embedded payload bytes are skipped.
