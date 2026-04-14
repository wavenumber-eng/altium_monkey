# schlib_find_symbol

Build an index of symbol names across a folder of SchLib files and run simple
fuzzy searches against that index.

This is useful when a project has many local schematic libraries and you need to
find which library contains a symbol before inserting it into a schematic,
splitting a library, or merging libraries.

## What It Shows

1. `AltiumSchLib.get_symbol_names(...)`
2. Building a `{library_filename: [symbol_name, ...]}` dictionary
3. Searching symbol names with a lightweight Python fuzzy matcher
4. Writing the index and query results to JSON

## Run

From the package root:

```powershell
uv run python examples\schlib_find_symbol\schlib_find_symbol.py
```

To scan a different folder:

```powershell
uv run python examples\schlib_find_symbol\schlib_find_symbol.py --schlib-dir path\to\schlibs --query RT685 --query MOSFET
```

The example uses queries such as `rt685`, `r_2p`, `mosfet`, and `smt tp` to
show exact, partial, and approximate matches.

## Output

The script prints the discovered symbols and writes:

```text
examples/schlib_find_symbol/output/symbol_index.json
```
