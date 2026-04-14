# prjpcb_make_project

Create a complete Altium project folder from Python.

This example combines the schematic dynamic-template pattern with the simple
PCB authoring pattern. It writes one D-size schematic, one rectangular PCB, one
OutJob with enabled documentation and fabrication outputs, and one `.PrjPcb`
that references all generated files.

The title block keeps Altium parameter expressions such as `=PROJECT_TITLE`,
`=PCB_PART_NUMBER`, and `=VariantName` in the schematic. Those values are
provided by project parameters and by a project variant named `A`; they are not
written as schematic document parameters.

## What It Shows

1. Creating a D-size SchDoc with a generated title-block template.
2. Creating a PcbDoc with a rectangular board outline and lower-left origin.
3. Creating an OutJob with enabled schematic, PCB drawing, fabrication, BOM, and STEP outputs.
4. Creating a PrjPcb, adding project parameters, adding variant `A`, and adding the generated SchDoc, PcbDoc, and OutJob.
5. Keeping the OutJob output base inside the generated project folder under `outputs/`.

## Run

From the repository root:

```powershell
uv run python examples\prjpcb_make_project\prjpcb_make_project.py
```

## Inputs

The example uses:

```text
examples/assets/schdoc/blank.SchDoc
examples/prjpcb_make_project/assets/logo.png
```

## Output

The script writes a self-contained project folder:

```text
examples/prjpcb_make_project/output/prjpcb_make_project/prjpcb_make_project.PrjPcb
examples/prjpcb_make_project/output/prjpcb_make_project/prjpcb_make_project.SchDoc
examples/prjpcb_make_project/output/prjpcb_make_project/prjpcb_make_project.PcbDoc
examples/prjpcb_make_project/output/prjpcb_make_project/prjpcb_make_project.OutJob
examples/prjpcb_make_project/output/prjpcb_make_project/outputs/
examples/prjpcb_make_project/output/prjpcb_make_project/project_manifest.json
```

Open the generated `.PrjPcb` in Altium to inspect the project documents and run
the generated OutJob.
