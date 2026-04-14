# schdoc_add_parameter_set

Open a blank schematic, add ordinary parameter-set directives, and save the
modified `.SchDoc`.

This sample shows the parent/child directive pattern without the differential
pair variant:

1. load an existing `SchDoc`
2. create a detached parent directive with `make_sch_parameter_set(...)`
3. create a detached child parameter with `make_sch_parameter(...)`
4. add the parent with `AltiumSchDoc.add_object(...)`
5. add the child with `AltiumSchDoc.add_object(child, owner=parent)`
6. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached `ParameterSet` directives with `make_sch_parameter_set(...)`
2. creating child `ClassName` parameters with `make_sch_parameter(...)`
3. using `SchPointMils` for public directive and child coordinates
4. using `ParameterSetStyle` and `Rotation90` enums instead of raw integers
5. using `ColorValue` helpers instead of raw Win32 color integers
6. reopening the written file and inspecting the recovered parent/child hierarchy

## Run

```powershell
uv run python examples\schdoc_add_parameter_set\schdoc_add_parameter_set.py
```

## Output

The script writes:

- `output/blank_with_parameter_sets.SchDoc`

## Expected Result

The output file should contain:

1. a row of ordinary parameter-set directives in several styles and orientations
2. one child `ClassName` parameter under each parameter set
3. all directives colored in Altium's native directive red
