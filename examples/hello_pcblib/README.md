# hello_pcblib

Create a PCB library from scratch and author a real 0603 resistor footprint.

This example does not clone an existing PcbLib. It creates a new footprint,
adds pads, silkscreen/fabrication tracks, a component body, and embeds a STEP
model from a standalone file on disk.

The model checksum is computed by `AltiumPcbLib.add_embedded_model(...)`.
`AltiumPcbFootprint.add_embedded_3d_model(...)` infers the STEP projection
bounds and height through the core OCCT-backed geometry helper.

## What It Shows

1. `AltiumPcbLib()`
2. `AltiumPcbLib.add_embedded_model(...)`
3. `AltiumPcbLib.add_footprint(...)`
4. `AltiumPcbFootprint.add_pad(...)`
5. `AltiumPcbFootprint.add_track(...)`
6. `AltiumPcbFootprint.add_embedded_3d_model(...)`
7. `AltiumPcbLib.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\hello_pcblib\hello_pcblib.py
```

## Output

The script writes:

```text
examples/hello_pcblib/output/hello_pcblib.PcbLib
```
