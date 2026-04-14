# schdoc_add_differential_pair

Open a blank schematic, add differential-pair directives, and save the
modified `.SchDoc`.

This sample isolates the differential-pair variant from ordinary parameter
sets so it is easy to open the output on its own.

## What It Shows

1. creating detached parent directives with `make_sch_parameter_set(...)`
2. creating hidden child `DifferentialPair=True` parameters with `make_sch_parameter(...)`
3. adding the parent first, then the child through `add_object(child, owner=parent)`
4. varying `ParameterSetStyle` and `Rotation90`
5. reopening the written file and checking `is_differential_pair()`

## Run

```powershell
uv run python examples\schdoc_add_differential_pair\schdoc_add_differential_pair.py
```

## Output

The script writes:

- `output/blank_with_differential_pairs.SchDoc`

## Expected Result

The output file should contain:

1. a row of differential-pair directives in several styles and orientations
2. one hidden child `DifferentialPair=True` parameter under each directive
3. all directives colored in Altium's native directive red
