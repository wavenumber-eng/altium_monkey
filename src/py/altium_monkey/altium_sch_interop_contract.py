"""Versioned SchDoc and SchLib JSON interoperability contracts."""

from pathlib import Path

SCHDOC_INTEROP_SCHEMA = "altium_monkey.schdoc.interop.a0"
SCHLIB_INTEROP_SCHEMA = "altium_monkey.schlib.interop.a0"

_ENVELOPE_FIELDS = frozenset({"schema", "document"})
_SCHDOC_FIELDS = frozenset({"Header", "Objects"})
_SCHLIB_FIELDS = frozenset({"Header", "Symbols"})


def _unwrap_interop_json(
    data: dict[str, object],
    *,
    schema: str,
    legacy_fields: frozenset[str],
) -> dict[str, object]:
    if set(data) == legacy_fields:
        return data
    if set(data) != _ENVELOPE_FIELDS:
        raise ValueError(
            "Invalid JSON format: expected an exact legacy document or tagged envelope"
        )
    if data["schema"] != schema:
        raise ValueError(f"Invalid JSON format: unsupported schema {data['schema']!r}")
    document = data["document"]
    if not isinstance(document, dict):
        raise ValueError("Invalid JSON format: 'document' must be an object")
    return document


def _unwrap_schdoc_interop_json(data: dict[str, object]) -> dict[str, object]:
    """Return the legacy SchDoc document from accepted ingress forms."""
    return _unwrap_interop_json(
        data,
        schema=SCHDOC_INTEROP_SCHEMA,
        legacy_fields=_SCHDOC_FIELDS,
    )


def _unwrap_schlib_interop_json(data: dict[str, object]) -> dict[str, object]:
    """Return the legacy SchLib document from accepted ingress forms."""
    return _unwrap_interop_json(
        data,
        schema=SCHLIB_INTEROP_SCHEMA,
        legacy_fields=_SCHLIB_FIELDS,
    )


def validate_schdoc_interop_json(
    source: Path | str | dict[str, object],
) -> None:
    """Validate tagged or legacy SchDoc JSON without retaining a document."""
    from .altium_schdoc import AltiumSchDoc

    AltiumSchDoc.from_json(source)


def validate_schlib_interop_json(
    source: Path | str | dict[str, object],
) -> None:
    """Validate tagged or legacy SchLib JSON without retaining a library."""
    from .altium_schlib import AltiumSchLib

    AltiumSchLib.from_json(source)


__all__ = [
    "SCHDOC_INTEROP_SCHEMA",
    "SCHLIB_INTEROP_SCHEMA",
    "validate_schdoc_interop_json",
    "validate_schlib_interop_json",
]
