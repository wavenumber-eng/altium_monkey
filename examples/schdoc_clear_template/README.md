# schdoc_clear_template

Open an existing schematic that has embedded template graphics, remove the
template, and save the cleared schematic.

This example shows the canonical public path for stripping title-block and
border content that came from a schematic template:

1. load an existing `.SchDoc` with `AltiumSchDoc(...)`
2. inspect the current template with `AltiumSchDoc.get_template()`
3. remove the template with `AltiumSchDoc.clear_template()`
4. save the modified schematic with `AltiumSchDoc.save(...)`

## What It Shows

1. removing the `AltiumSchTemplate` record
2. removing template-owned graphics, text, and embedded images
3. clearing sheet metadata such as `ShowTemplateGraphics` and `TemplateFileName`
4. preserving normal schematic content that is not owned by the template
5. using a project asset as the input schematic

## Run

From the repo root:

```powershell
uv run python examples\schdoc_clear_template\schdoc_clear_template.py
```

## Input

This sample uses:

```text
examples/assets/projects/bunny_brain/bunny_brain_D.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_clear_template/output/bunny_brain_D_without_template.SchDoc
```

## Expected Result

The output schematic should open without the original template title-block,
border, logo, and other template-owned drawing objects. The schematic objects
that belong to the design remain in the document.
