# Schematic Contract TypeSpec Sources

These files are the published wire-shape source for Altium Monkey's versioned
schematic transport models. `main.tsp` imports the six roots and their shared
types. Handwritten adapters own semantic validation. The 2026.9.11 generation
baseline uses TypeSpec compiler and JSON Schema emitter version `1.14.0`.

The generated language DTOs are implementation details. Public Python callers
should use `AltiumDesign.to_netlist()` and its returned netlist object,
`SchematicBomPayload`, `SchematicContractLimits`, and
`SchematicContractError`.

Already-published JSON Schema artifacts are immutable compatibility records.
In particular, `../design_b0.schema.json` and `../netlist_a0.schema.json` retain
their originally published bytes even if compiling these sources with a newer
toolchain would change formatting, definition fragment names, descriptions, or
validation detail. A semantic payload change requires a new schema ID and a new
schema file.
