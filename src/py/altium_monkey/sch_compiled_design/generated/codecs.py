"""Generated structural codecs for schematic compiled-design roots."""

from __future__ import annotations

import msgspec

from .models import WN_SCHEMA_ROOTS


class GeneratedSchemaDecodeError(Exception):
    """A generated schematic DTO rejected a structural payload."""


class GeneratedStructuralCodec:
    """Generated DTO conversion after handwritten bounded framing."""

    def __init__(self) -> None:
        self._decoders = {
            schema_id: msgspec.json.Decoder(model)
            for model, schema_id in WN_SCHEMA_ROOTS.items()
        }
        self._models = {
            schema_id: model for model, schema_id in WN_SCHEMA_ROOTS.items()
        }

    @property
    def schema_ids(self) -> frozenset[str]:
        """Return the exact catalog-discovered root inventory."""

        return frozenset(self._decoders)

    def decode(self, schema_id: str, raw: bytes | str) -> object:
        """Materialize one already-preflighted current root."""

        decoder = self._decoders.get(schema_id)
        if decoder is None:
            raise KeyError(f"unknown generated schema identity {schema_id}")
        try:
            return decoder.decode(raw)
        except msgspec.ValidationError as error:
            raise GeneratedSchemaDecodeError(str(error)) from error

    def convert(self, schema_id: str, value: object) -> object:
        """Convert one already-materialized mapping into its root DTO."""

        model = self._models.get(schema_id)
        if model is None:
            raise KeyError(f"unknown generated schema identity {schema_id}")
        try:
            return msgspec.convert(value, type=model, strict=True)
        except msgspec.ValidationError as error:
            raise GeneratedSchemaDecodeError(str(error)) from error

    @staticmethod
    def encode(value: object) -> bytes:
        """Serialize one canonicalized generated DTO."""

        return msgspec.json.encode(value)


__all__ = ("GeneratedSchemaDecodeError", "GeneratedStructuralCodec")
