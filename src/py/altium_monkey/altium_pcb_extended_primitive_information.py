"""
Typed PCB ExtendedPrimitiveInformation/Data model.

The stream uses ordinary Altium length-prefixed text records:
- 4 byte little-endian payload length
- pipe-delimited key=value payload with trailing null
"""

from __future__ import annotations

from .altium_pcb_property_helpers import (
    clean_pcb_property_text as _clean_text,
    parse_pcb_int_token as _parse_int_token,
    parse_pcb_mils_token_as_internal as _parse_mils_token_as_internal,
    parse_pcb_property_payload,
    PcbLengthPrefixedPropertyRecordMixin,
    set_pcb_text_property,
)


class AltiumPcbExtendedPrimitiveInformation(PcbLengthPrefixedPropertyRecordMixin):
    """
    Typed wrapper for one ExtendedPrimitiveInformation record.
    """

    def __init__(self) -> None:
        self.properties: dict[str, str] = {}
        self.primitive_index: int | None = None
        self.primitive_object_id: str = ""
        self.info_type: str = ""
        self.paste_mask_expansion_mode: str = ""
        self.paste_mask_expansion_manual_token: str = ""
        self.solder_mask_expansion_mode: str = ""
        self.solder_mask_expansion_manual_token: str = ""
        self.raw_record_payload: bytes | None = None
        self._typed_signature_at_parse: tuple | None = None
        self._properties_raw_signature: tuple | None = None

    @classmethod
    def from_payload(cls, payload: bytes) -> "AltiumPcbExtendedPrimitiveInformation":
        item = cls()
        item.raw_record_payload = bytes(payload)
        item.properties = parse_pcb_property_payload(payload)
        item._load_typed_fields_from_properties()
        item._typed_signature_at_parse = item._typed_signature()
        item._properties_raw_signature = item._properties_signature()
        return item

    @property
    def paste_mask_expansion_manual(self) -> int | None:
        return _parse_mils_token_as_internal(self.paste_mask_expansion_manual_token)

    @property
    def solder_mask_expansion_manual(self) -> int | None:
        return _parse_mils_token_as_internal(self.solder_mask_expansion_manual_token)

    def _load_typed_fields_from_properties(self) -> None:
        props = self.properties or {}
        self.primitive_index = _parse_int_token(props.get("PRIMITIVEINDEX", ""))
        self.primitive_object_id = _clean_text(props.get("PRIMITIVEOBJECTID", ""))
        self.info_type = _clean_text(props.get("TYPE", ""))
        self.paste_mask_expansion_mode = _clean_text(
            props.get("PASTEMASKEXPANSIONMODE", "")
        )
        self.paste_mask_expansion_manual_token = _clean_text(
            props.get("PASTEMASKEXPANSION_MANUAL", "")
        )
        self.solder_mask_expansion_mode = _clean_text(
            props.get("SOLDERMASKEXPANSIONMODE", "")
        )
        self.solder_mask_expansion_manual_token = _clean_text(
            props.get("SOLDERMASKEXPANSION_MANUAL", "")
        )

    def _sync_typed_fields_to_properties(self) -> None:
        props = dict(self.properties or {})

        if self.primitive_index is None and "PRIMITIVEINDEX" not in props:
            props.pop("PRIMITIVEINDEX", None)
        else:
            props["PRIMITIVEINDEX"] = (
                "" if self.primitive_index is None else str(int(self.primitive_index))
            )

        set_pcb_text_property(props, "PRIMITIVEOBJECTID", self.primitive_object_id)
        set_pcb_text_property(props, "TYPE", self.info_type)
        set_pcb_text_property(
            props, "PASTEMASKEXPANSIONMODE", self.paste_mask_expansion_mode
        )
        set_pcb_text_property(
            props,
            "PASTEMASKEXPANSION_MANUAL",
            self.paste_mask_expansion_manual_token,
        )
        set_pcb_text_property(
            props, "SOLDERMASKEXPANSIONMODE", self.solder_mask_expansion_mode
        )
        set_pcb_text_property(
            props,
            "SOLDERMASKEXPANSION_MANUAL",
            self.solder_mask_expansion_manual_token,
        )
        self.properties = props

    def _typed_signature(self) -> tuple:
        return (
            self.primitive_index,
            self.primitive_object_id,
            self.info_type,
            self.paste_mask_expansion_mode,
            self.paste_mask_expansion_manual_token,
            self.solder_mask_expansion_mode,
            self.solder_mask_expansion_manual_token,
        )

    def _properties_signature(self) -> tuple:
        return tuple(
            (str(key), str(value)) for key, value in (self.properties or {}).items()
        )


def parse_extended_primitive_information_stream(
    raw: bytes,
) -> list[AltiumPcbExtendedPrimitiveInformation]:
    """
    Parse an ExtendedPrimitiveInformation/Data stream into typed records.
    """
    out: list[AltiumPcbExtendedPrimitiveInformation] = []
    pos = 0
    total = len(raw or b"")
    while pos + 4 <= total:
        record_len = int.from_bytes(raw[pos : pos + 4], byteorder="little")
        pos += 4
        if record_len <= 0 or pos + record_len > total:
            break
        payload = raw[pos : pos + record_len]
        pos += record_len
        out.append(AltiumPcbExtendedPrimitiveInformation.from_payload(payload))
    return out
