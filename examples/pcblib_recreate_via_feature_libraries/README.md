# pcblib_recreate_via_feature_libraries

Rebuild a set of synthetic PcbLib via-feature libraries through the public
PcbLib OOP API.

The example loads each source library from `assets/`, parses it into an
`AltiumPcbLib`, creates a new library with `add_footprint(...)`, and recreates
the footprint by calling public primitive authoring helpers such as
`add_via(...)`, `add_pad(...)`, `add_track(...)`, `add_arc(...)`,
`add_fill(...)`, `add_region(...)`, and `add_text(...)`. It then reparses the
authored output and writes a manifest showing semantic recreation checks.

This sample intentionally does not call `add_existing_footprint(...)`; it is
meant to demonstrate from-API authoring of the via feature rows and the mixed
primitive record order needed by `ViaStructures.PRIMITIVEINDEX`.

Run:

```powershell
uv run python examples\pcblib_recreate_via_feature_libraries\pcblib_recreate_via_feature_libraries.py
```

Outputs:

- `output/via_ipc4761_type_matrix.PcbLib`
- `output/via_propagation_delay_matrix.PcbLib`
- `output/via_features_and_all_primitives.PcbLib`
- `output/via_test_point_flags.PcbLib`
- `output/footprint_parameters.PcbLib`
- `output/pcblib_recreate_via_feature_libraries.json`

The source libraries are synthetic fixtures covering IPC-4761 via structure
types, propagation delay, mixed footprint primitives, fabrication and assembly
testpoint flags, footprint `Parameters`, and footprint `PrimitiveParameters`.

The manifest includes exact owned-stream byte comparisons as diagnostics, but
the pass criterion is semantic readback: matching primitive counts, footprint
parameters, primitive parameters, via feature rows/materials, propagation
delay, testpoint flags, and via-structure link counts.
