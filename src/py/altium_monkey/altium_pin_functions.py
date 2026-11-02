"""Native PinFunctionData payloads within shared auxiliary stream framing."""

import struct
from collections.abc import Sequence

from .altium_sch_auxiliary_codec import SchAuxiliaryStreamError
from .altium_record_types import MAX_INDEXED_ITEMS_PER_RECORD


def validate_functions(values: Sequence[str], field: str) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{field} must be a sequence of strings")
    if len(values) > MAX_INDEXED_ITEMS_PER_RECORD:
        raise ValueError(f"{field} has too many entries")
    result = list(values)
    for value in result:
        if not isinstance(value, str):
            raise TypeError(f"{field} entries must be strings")
        if not value or any(char in value for char in "|\x00\r\n"):
            raise ValueError(f"{field} entries must be nonempty and contain no pipe, NUL, or newline")
    return result


def encode_function_payload(defined: Sequence[str], selected: Sequence[str]) -> bytes:
    pairs = []
    for prefix, values in (("PINDEFINEDFUNCTION", defined), ("PINSELECTEDFUNCTION", selected)):
        values = validate_functions(values, prefix)
        if values or prefix == "PINDEFINEDFUNCTION":
            pairs.append(f"{prefix}SCOUNT={len(values)}")
            pairs.extend(f"{prefix}{index}={value}" for index, value in enumerate(values, 1))
    text = ("|" + "|".join(pairs)).encode("utf-16-le")
    return struct.pack("<I", len(text)) + text


def decode_function_payload(payload: bytes) -> tuple[list[str], list[str]]:
    def malformed(reason: str) -> SchAuxiliaryStreamError:
        return SchAuxiliaryStreamError("malformed", 0, reason)

    if len(payload) < 4 or struct.unpack_from("<I", payload)[0] != len(payload) - 4:
        raise malformed("PinFunctionData text length does not match payload")
    try:
        text = payload[4:].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise malformed("PinFunctionData text is not UTF-16 LE") from exc
    fields = {}
    for part in text.split("|"):
        if not part:
            continue
        key, separator, value = part.partition("=")
        key = key.upper()
        if not separator or key in fields:
            raise malformed("invalid or duplicate pin function field")
        fields[key] = value
    result = []
    for prefix in ("PINDEFINEDFUNCTION", "PINSELECTEDFUNCTION"):
        count_text = fields.pop(prefix + "SCOUNT", "0")
        if not count_text.isascii() or not count_text.isdigit() or len(count_text) > 10:
            raise malformed("invalid pin function count")
        count = int(count_text)
        if count > MAX_INDEXED_ITEMS_PER_RECORD:
            raise malformed("pin function count exceeds limit")
        values = []
        for index in range(1, count + 1):
            key = f"{prefix}{index}"
            if key not in fields:
                raise malformed("missing indexed pin function")
            values.append(fields.pop(key))
        try:
            result.append(validate_functions(values, prefix))
        except (ValueError, TypeError) as exc:
            raise malformed(str(exc)) from exc
    if fields:
        raise malformed("unsupported pin function fields")
    return result[0], result[1]
