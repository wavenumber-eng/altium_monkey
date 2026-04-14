# PcbLib

`AltiumPcbLib` is the public container for PCB footprint libraries. Each library
contains one or more `AltiumPcbFootprint` objects.

Use it when you need to:

1. create footprints programmatically
2. add pads, tracks, arcs, regions, text, and 3D bodies to footprints
3. embed STEP models
4. extract embedded 3D models
5. find, split, or render footprints

## Object Model

PcbLib uses a footprint-oriented API. `AltiumPcbLib` owns embedded models and
footprints. `AltiumPcbFootprint` owns its primitive lists:
`footprint.pads`, `footprint.tracks`, `footprint.arcs`, `footprint.regions`,
`footprint.texts`, `footprint.vias`, and `footprint.component_bodies`.

Attach a footprint to a library before adding primitives so the library can
manage model streams and authoring metadata:

```python
pcblib = AltiumPcbLib()
footprint = pcblib.add_footprint("R0603")
footprint.add_pad(...)
pcblib.save("footprints.PcbLib")
```

## Units

Public PcbLib helper methods use explicit mil-unit parameter names. Metric
package data is common for footprints, so convert millimeters to mils at the
call site for now.

3D STEP model bounds can be inferred when an embedded STEP payload is available.
Explicit projection bounds remain supported for deterministic authored output.

## Direct Record Edits

Directly editing footprint primitive lists is an advanced escape hatch. It can
be useful when preserving parsed libraries or performing a narrow mutation, but
high-level helper methods should be preferred for authored output.

## Examples

Start with:

1. [`hello_pcblib`](../examples/hello_pcblib/README.md)
2. [`pcblib_find_footprint`](../examples/pcblib_find_footprint/README.md)
3. [`pcblib_split`](../examples/pcblib_split/README.md)
4. [`pcblib_footprint_svg`](../examples/pcblib_footprint_svg/README.md)
5. [`pcblib_extract_3d_models`](../examples/pcblib_extract_3d_models/README.md)
6. [`pcblib_add_free_3d_extruded`](../examples/pcblib_add_free_3d_extruded/README.md)
7. [`pcblib_synthesize_power_resistor_lib`](../examples/pcblib_synthesize_power_resistor_lib/README.md)

See [API patterns](api_patterns/index.md) for the differences between schematic
and PCB object systems.

