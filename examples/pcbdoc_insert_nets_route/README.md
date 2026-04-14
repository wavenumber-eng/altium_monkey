# pcbdoc_insert_nets_route

Open an existing blank PcbDoc, insert two footprints from existing PcbLib files,
assign pad nets, and add routed copper tracks/vias on those nets.

## What It Shows

1. `AltiumPcbDoc.add_component_from_pcblib(...)`
2. Assigning `pad_nets` during footprint placement
3. `AltiumPcbDoc.add_track(...)`
4. `AltiumPcbDoc.add_via(...)`
5. Automatic net creation from routed primitives
6. Saving the modified PcbDoc as a new output file

The example places a 3-pin connector footprint and a 0603 resistor footprint,
then routes `VIN`, `SENSE`, and `GND`.

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_insert_nets_route\pcbdoc_insert_nets_route.py
```

## Output

```text
examples/pcbdoc_insert_nets_route/output/pcbdoc_insert_nets_route.PcbDoc
```
