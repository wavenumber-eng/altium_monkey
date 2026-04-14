# pcblib_synthesize_power_resistor_lib

Generate a small SQP20A power-resistor PcbLib set from code.

This example combines three pieces:

1. CadQuery synthesizes a STEP model for each resistor value.
2. The STEP model includes dot-matrix ink markings generated from the part value.
3. `AltiumPcbLib` creates a through-hole footprint, embeds the generated STEP model, computes matching model/body checksum metadata, and saves one PcbLib per part number.

The default run generates a small subset so the example check stays fast. Use `--all` to generate the full built-in SQP20AJB value list.

## Run

```powershell
uv run python examples\pcblib_synthesize_power_resistor_lib\pcblib_synthesize_power_resistor_lib.py
```

Generate all built-in SQP20 values:

```powershell
uv run python examples\pcblib_synthesize_power_resistor_lib\pcblib_synthesize_power_resistor_lib.py --all
```

Generate one explicit value:

```powershell
uv run python examples\pcblib_synthesize_power_resistor_lib\pcblib_synthesize_power_resistor_lib.py --part SQP20AJB-10R
```

## Outputs

- `output/step_models/`: generated STEP files
- `output/pcblib/`: generated Altium PcbLib files
- `output/synthesis_manifest.json`: generated part list and checksums

`AltiumPcbLib.add_embedded_model(...)` calculates the native checksum from the
synthesized STEP bytes and writes it consistently to `Library/Models/Data` and
`Library/ComponentBodies6/Data`. This generated-footprint example supplies the
known model projection bounds and height from the package dimensions. If those
values are omitted, `add_embedded_3d_model(...)` can infer rectangular STEP
bounds through the core OCCT-backed geometry helper.
