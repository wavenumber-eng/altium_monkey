# pcbdoc_add_pad

Add PCB pad primitives to an existing PcbDoc.

The sample creates a rectangular board outline, sets the origin to the
lower-left outline corner, and places surface-mount pads, bottom-side pads,
rounded rectangles, plated through-hole pads, a plated slot, and a non-plated
through hole with explicit zero solder-mask and paste-mask expansion.

## What It Shows

1. `AltiumPcbDoc.from_file(...)`
2. `AltiumPcbDoc.set_outline_rectangle_mils(...)`
3. `AltiumPcbDoc.add_pad(...)`
4. `PadShape` and `PcbLayer`
5. Slotted holes through `slot_length_mils` and `slot_rotation_degrees`
6. Manual mask expansion through `solder_mask_expansion_mils` and `paste_mask_expansion_mils`
7. `AltiumPcbDoc.save(...)`

## Run

From the repository root:

```powershell
uv run python examples\pcbdoc_add_pad\pcbdoc_add_pad.py
```

## Output

```text
examples/pcbdoc_add_pad/output/pcbdoc_add_pad.PcbDoc
```
