"""Shared helpers for PCB property-text tokens and payload parsing."""

from __future__ import annotations

from .altium_utilities import decode_byte_array, encode_altium_record, parse_byte_record


def clean_pcb_property_text(value: object) -> str:
    """Normalize a PCB property token into stripped text."""
    return str(value or "").replace("\x00", "").strip()


def parse_pcb_int_token(value: object) -> int | None:
    """Parse an integer token that may be stored as text or a float-like string."""
    text = clean_pcb_property_text(value)
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_pcb_mils_token_as_internal(value: object) -> int | None:
    """Parse a mil-valued token into internal 1e-4 mil units."""
    text = clean_pcb_property_text(value)
    if not text:
        return None
    if text.lower().endswith("mil"):
        text = text[:-3]
    try:
        return int(round(float(text) * 10000.0))
    except (TypeError, ValueError):
        return None


def parse_pcb_property_payload(payload: bytes) -> dict[str, str]:
    """Parse a pipe-delimited PCB property payload into an uppercase-key dict."""
    fields: dict[str, str] = {}
    for pair in parse_byte_record(payload):
        text = decode_byte_array(pair)
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        key_text = str(key or "").strip().upper()
        if key_text:
            fields[key_text] = value
    return fields


def set_pcb_text_property(
    props: dict[str, str],
    key: str,
    value: str,
    *,
    keep_if_present: bool = True,
) -> None:
    """Set or remove a text property token while preserving optional presence."""
    text = str(value or "")
    if text or (keep_if_present and key in props):
        props[key] = text
    else:
        props.pop(key, None)


class PcbPropertyRecordMixin:
    """Shared typed-property roundtrip behavior for PCB text payload records."""

    properties: dict[str, str]
    _typed_signature_at_parse: tuple | None

    def to_record(self) -> dict[str, str]:
        if self._typed_signature_at_parse != self._typed_signature():
            self._sync_typed_fields_to_properties()
        return dict(self.properties or {})


class PcbLengthPrefixedPropertyRecordMixin(PcbPropertyRecordMixin):
    """Shared length-prefixed serialization for PCB property payload records."""

    raw_record_payload: bytes | None
    _properties_raw_signature: tuple | None

    def can_passthrough_raw_payload(self) -> bool:
        return (
            self.raw_record_payload is not None
            and self._properties_raw_signature is not None
            and self._properties_raw_signature == self._properties_signature()
        )

    def serialize_record(self) -> bytes:
        if self._typed_signature_at_parse != self._typed_signature():
            self._sync_typed_fields_to_properties()
        if self.can_passthrough_raw_payload() and self.raw_record_payload is not None:
            return (
                len(self.raw_record_payload).to_bytes(4, byteorder="little")
                + self.raw_record_payload
            )
        return encode_altium_record(self.properties)
