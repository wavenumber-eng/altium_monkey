# Schematic JSON interoperability

SchDoc and SchLib retain their established untagged JSON documents as the
default public format. `to_json()` still returns and optionally writes that
legacy document without adding a version marker.

Consumers that need an explicit contract identifier can call
`to_tagged_json()`. Its result is an exact two-field envelope:

```json
{
  "schema": "altium_monkey.schdoc.interop.a0",
  "document": {
    "Header": {},
    "Objects": []
  }
}
```

The SchLib form uses schema
`altium_monkey.schlib.interop.a0` and an inner document with exactly
`Header` and `Symbols` root fields. The constants
`SCHDOC_INTEROP_SCHEMA` and `SCHLIB_INTEROP_SCHEMA` expose these identifiers.

## Ingress and validation

`AltiumSchDoc.from_json()`, `AltiumSchDoc.apply_json()`,
`AltiumSchLib.from_json()`, and `AltiumSchLib.apply_json()` accept either
the exact tagged envelope or their exact legacy document. The public
`validate_schdoc_interop_json()` and `validate_schlib_interop_json()`
functions apply the same structural, resource, and semantic checks without
retaining a model. They return `None` on success and raise `ValueError` for
invalid content. Path inputs preserve normal `FileNotFoundError` and
`OSError` behavior.

Tagged envelopes must contain only `schema` and `document`. Unknown or
cross-kind schema identifiers, missing or extra envelope fields, and the
provisional `format` alias are rejected. Legacy roots likewise reject extra
fields. File input rejects duplicate and casefold-colliding object keys before
the inner document is extracted.

The complete input, including a tagged envelope, is covered by the existing
JSON byte, recursion-depth, aggregate-item, and embedded-blob budgets.
`apply_json()` is transactional: validation or reconstruction failure leaves
the target unchanged.

## JSON Schemas

The handwritten Draft 2020-12 schemas describe both accepted ingress forms:

- `schdoc_interop_a0.schema.json`
- `schlib_interop_a0.schema.json`

These schemas define the version envelope and legacy root shape. The Python
validators remain authoritative for detailed record semantics and resource
limits.
