# schdoc_add_port

Open a blank schematic, add several hierarchical ports, and save the modified
`.SchDoc`.

This example follows the detached-object mutation pattern used by the other
public schematic examples:

1. load an existing `SchDoc`
2. describe each port with public enums and mil-based dimensions
3. call `make_sch_port(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached ports with `make_sch_port(...)`
2. using `PortStyle` and `PortIOType` instead of raw integers
3. using `SchFontSpec` instead of raw `FontID` values
4. using mil-based `width_mils` and `height_mils` inputs
5. using `SchHorizontalAlign` and `LineWidth` for text alignment and border thickness
6. reopening the written file and inspecting the created objects

## Scope

This public example is intentionally limited to ordinary named ports. Advanced
cross-reference display mode, harness-specific port behavior, and custom
object-definition-backed port graphics are not part of this example surface.

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_port\schdoc_add_port.py
```

## Input

This sample uses:

```text
examples/schdoc_add_port/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_port/output/blank_with_ports.SchDoc
```

## Expected Result

The output file should contain:

1. a horizontal row with left, right, left-right, and flat horizontal port styles
2. a vertical row with top, bottom, top-bottom, and flat vertical port styles
3. a mix of input, output, bidirectional, and unspecified I/O types
4. explicit examples of alignment, border thickness, font, and hidden-name behavior

The script also prints the reopened style, I/O type, alignment, size, font, and
location for each port so the example is easy to sanity-check without
inspecting the binary file directly.
