# PrjPcb

`AltiumPrjPcb` is the public container for Altium project files. Use it when
you need to inspect or mutate an existing `.PrjPcb`, work with project
parameters and variants, resolve project documents, or run an associated
OutJob.

Use it when you need to:

1. read schematic, PCB, library, and OutJob document references
2. add or replace project document entries
3. set, get, or delete project parameters
4. create or select a project variant
5. resolve reachable SchDoc and PcbDoc paths
6. run the project-associated OutJob through `prj.outjob().run(...)`

## Project Parameters

Project parameters belong in the `.PrjPcb`, not in the schematic document. This
matters for title blocks and dynamic templates that use Altium parameter
expressions such as `=PROJECT_TITLE`, `=PCB_PART_NUMBER`, or `=VariantName`.

Use:

```python
prj = AltiumPrjPcb("project.PrjPcb")
prj.set_parameter("PROJECT_TITLE", "Example Project")
prj.set_parameters({"PCB_PART_NUMBER": "WN-001", "PCB_CODENAME": "demo"})
value = prj.get_parameter("PROJECT_TITLE")
prj.delete_parameter("OLD_PARAMETER")
prj.save("project.PrjPcb")
```

## Variants

Use `add_variant(...)` and `set_current_variant(...)` for the project-level
variant state that Altium uses for project context and special-string
substitution.

```python
prj.add_variant("A", current=True)
assert prj.get_current_variant() == "A"
```

Variant processing in `AltiumDesign` is limited to DNP handling in this
release. Alternate fitted components and variant parameter overrides are not
fully modeled yet.

## Documents

Use `add_document(...)` when the document type can be inferred from the suffix.
Use `set_documents_from_directory(...)` when creating or normalizing a project
folder from existing files.

For minimal new projects, `AltiumPrjPcbBuilder` remains an acceptable public
project-file convenience helper. It is not the same as the retired schematic
fluent builders. It only writes project document membership and basic project
configuration.

## OutJobs

Use `prj.outjob()` to resolve the project-associated OutJob and run it through
the high-level runner:

```python
prj = AltiumPrjPcb("project.PrjPcb")
result = prj.outjob().run(timeout_seconds=300)
```

The runner launches Altium Designer, runs the OutJob, and waits for the script
completion marker. Altium may remain open after the run completes.

## Use With Care

Direct edits to low-level `.PrjPcb` INI sections are sometimes useful for
preserving unusual Altium project settings, but use the public parameter,
variant, document, and OutJob helpers when they exist.

Do not encode machine-specific absolute paths in project documents. Keep
project document references project-relative when creating public examples or
redistributable assets.

## Examples

Start with:

1. [`prjpcb_make_project`](../examples/prjpcb_make_project/README.md)
2. [`outjob_runner`](../examples/outjob_runner/README.md)
3. [`schdoc_apply_dynamic_template`](../examples/schdoc_apply_dynamic_template/README.md)
4. [`hello_altium_design`](../examples/hello_altium_design/README.md)

See [AltiumDesign](altium_design.md) for project-level analysis and generated
JSON contracts.

