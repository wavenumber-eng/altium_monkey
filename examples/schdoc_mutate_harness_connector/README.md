# schdoc_mutate_harness_connector

Mutate an existing harness connector group through the connector-owned public
API.

This sample:

1. builds its own standalone baseline harness document that matches the
   creation example geometry and styling
2. reopens it from disk through `AltiumSchDoc(...)`
3. looks up entries through `connector.get_entry(...)`
4. renames `SDA` to `SDA0` and replaces `SCL` with `SCL0`
5. mutates connector, entry, signal-harness, and port colors and fonts
6. moves the connector type label to `3700 mil, 4600 mil` and renames it to
   `I2C_CTRL`
7. saves the mutated document back through the normal `save()` path

The example is self-contained. It writes both an input and mutated output file
so the round-trip result is easy to inspect.

## Run

```powershell
uv run python examples\schdoc_mutate_harness_connector\schdoc_mutate_harness_connector.py
```

The script writes:

```text
output/harness_mutation_input.SchDoc
output/harness_mutation_output.SchDoc
```
