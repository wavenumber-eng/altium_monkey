"""Generated strict structural codecs for PCB manufacturing roots."""

from __future__ import annotations

import json
from typing import cast

import jsonschema_rs
import msgspec

from altium_monkey.pcb_manufacturing._strict_json import preflight_strict_json

from .models import WN_SCHEMA_ROOTS
from .schema_documents import WN_GENERATED_SCHEMA_JSON


class GeneratedSchemaDecodeError(Exception):
    """Generated JSON Schema rejected a structural payload."""

    def __init__(self, schema_id: str, message: str) -> None:
        self.schema_id = schema_id
        self.message = message
        super().__init__(schema_id, message)


class GeneratedStructuralCodec:
    """Strict JSON, generated-schema, and msgspec codec composition."""

    def __init__(self) -> None:
        schemas_by_id = {
            schema_id: cast(
                "dict[str, jsonschema_rs.JSONType]", json.loads(schema_text)
            )
            for schema_id, schema_text in WN_GENERATED_SCHEMA_JSON.items()
        }
        expected_ids = set(WN_SCHEMA_ROOTS.values())
        if set(schemas_by_id) != expected_ids:
            raise ValueError("generated model/schema root inventory differs")
        registry = jsonschema_rs.Registry(list(schemas_by_id.items()))
        self._validators = {
            schema_id: jsonschema_rs.Draft202012Validator(schema, registry=registry)
            for schema_id, schema in schemas_by_id.items()
        }
        self._decoders = {
            schema_id: msgspec.json.Decoder(model)
            for model, schema_id in WN_SCHEMA_ROOTS.items()
        }

    @property
    def schema_ids(self) -> frozenset[str]:
        """Return the exact catalog-discovered codec root inventory."""

        return frozenset(self._decoders)

    def decode(self, schema_id: str, raw: bytes) -> object:
        """Decode untrusted JSON through the complete strict structural boundary."""

        decoder = self._decoders.get(schema_id)
        validator = self._validators.get(schema_id)
        if decoder is None or validator is None:
            raise KeyError(f"unknown generated schema identity {schema_id}")
        preflight_strict_json(raw)
        payload = json.loads(raw)
        errors = tuple(validator.iter_errors(payload))
        if errors:
            raise GeneratedSchemaDecodeError(schema_id, str(errors[0]))
        return decoder.decode(raw)

    def encode(self, schema_id: str, value: object) -> bytes:
        """Validate and serialize one generated root without a property tree."""

        validator = self._validators.get(schema_id)
        if validator is None:
            raise KeyError(f"unknown generated schema identity {schema_id}")
        raw = msgspec.json.encode(value)
        preflight_strict_json(raw)
        errors = tuple(validator.iter_errors(json.loads(raw)))
        if errors:
            raise GeneratedSchemaDecodeError(schema_id, str(errors[0]))
        return raw


__all__ = ("GeneratedSchemaDecodeError", "GeneratedStructuralCodec")
