# prjpcb_make_project

Create a complete Altium project folder from Python.

This example combines a dynamically created schematic template with the simple
PCB authoring pattern. It writes one D-size schematic, one rectangular PCB, one
OutJob with schematic PDF and fabrication outputs, and one `.PrjPcb`
that references all generated files. The generated schematic inherits its sheet
context from the generated `.SchDot` template when the template is applied.

The title block keeps Altium parameter expressions such as
`="Schematic Page"`, `=CCA_PART_NUMBER`, `=CCA_CODENAME`, `=CCA_MIXDOWN`,
`=PCB_PART_NUMBER`, `=PCB_CODENAME`, and `=PCB_MIXDOWN` in the schematic. The
schematic page value is provided by a document parameter. The part values are
provided by project parameters.

## What It Shows

1. Creating a generated D-size title-block template and applying it to a SchDoc.
2. Creating a PcbDoc with a rectangular board outline and lower-left origin.
3. Creating an OutJob with schematic PDF, fabrication, BOM, and STEP outputs.
4. Creating a PrjPcb, adding project parameters, adding variant `A`, and adding the generated SchDoc, PcbDoc, and OutJob.
5. Keeping the OutJob output base inside the generated project folder under `outputs/`.

## Run

From the repository root:

```powershell
uv run python examples\prjpcb_make_project\prjpcb_make_project.py
```

## Inputs

The example uses one input image:

```text
examples/prjpcb_make_project/assets/logo.png
```

## Output

The script writes a self-contained project folder:

```text
examples/prjpcb_make_project/output/ULTRA-MONKEY/ULTRA-MONKEY.PrjPcb
examples/prjpcb_make_project/output/ULTRA-MONKEY/ULTRA-MONKEY.SchDoc
examples/prjpcb_make_project/output/ULTRA-MONKEY/ULTRA-MONKEY.PcbDoc
examples/prjpcb_make_project/output/ULTRA-MONKEY/ULTRA-MONKEY.OutJob
examples/prjpcb_make_project/output/ULTRA-MONKEY/outputs/
examples/prjpcb_make_project/output/ULTRA-MONKEY/project_manifest.json
```

Open the generated `.PrjPcb` in Altium to inspect the project documents and run
the generated OutJob.
