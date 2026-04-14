# schdoc_add_harness_connector

Create a schematic harness connector group inside an existing blank schematic
through the public connector-owned API.

This sample shows:

1. opening an existing `input/blank.SchDoc`
2. `make_sch_harness_connector(...)` with `bounds_mils: SchRectMils`
3. `make_sch_harness_entry(...)` with public mil-based offsets and `SchFontSpec`
4. `make_sch_harness_type(...)` with `location_mils: SchPointMils`
5. adding the matching harness port and signal harness
6. matching the reference composition in `output/harness_example_ref.SchDoc`

Only the connector group root is added to the document:

```python
schdoc.add_object(connector)
```

Harness entries and harness type labels are connector-owned and are not valid
top-level `schdoc.add_object(...)` calls.

## Run

```powershell
uv run python examples\schdoc_add_harness_connector\schdoc_add_harness_connector.py
```

The script writes:

```text
output/harness_example.SchDoc
```
