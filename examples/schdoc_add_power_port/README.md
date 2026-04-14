# schdoc_add_power_port

Open a blank schematic, add several built-in power-port symbols, and save the
modified `.SchDoc`.

This example uses the detached-object pattern that the other public schematic
mutation examples use:

1. load an existing `SchDoc`
2. describe each power port with public enums and `SchPointMils`
3. call `make_sch_power_port(...)`
4. insert each object with `AltiumSchDoc.add_object(...)`
5. persist the change with `AltiumSchDoc.save(...)`

## What It Shows

1. creating detached power ports with `make_sch_power_port(...)`
2. using `PowerObjectStyle` for built-in symbol selection
3. using `SchFontSpec` instead of raw `FontID` values
4. using `ColorValue` helpers instead of raw Win32 color integers
5. controlling `show_net_name` explicitly for supply and ground symbols
6. reopening the written file and inspecting the created objects

## Scope

This public example is intentionally limited to the built-in Altium power-port
styles. Advanced custom power-port graphics backed by object definitions are
not part of this example surface.

## Run

From the project root:

```powershell
uv run python examples\schdoc_add_power_port\schdoc_add_power_port.py
```

## Input

This sample uses:

```text
examples/schdoc_add_power_port/input/blank.SchDoc
```

## Output

The script writes:

```text
examples/schdoc_add_power_port/output/blank_with_power_ports.SchDoc
```

## Expected Result

The output file should contain:

1. a row of supply-style ports including circle, arrow, bar, wave, GOST arrow,
   and GOST bar variants
2. a row of ground-style ports including power ground, signal ground, earth,
   GOST power ground, and GOST earth variants
3. explicit net-name visibility choices so both hidden-name and visible-name
   cases are easy to inspect in Altium

The script also prints the reopened style, orientation, font, color, and
location for each power port so the example is easy to sanity-check without
inspecting the binary file directly.
