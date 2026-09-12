"""
Internal helpers for low-level Altium OLE stream parsing and serialization.
"""

import logging
import struct
from collections.abc import Mapping
from typing import Any

from .altium_ole import AltiumOleFile
from .altium_record_types import is_text_record as is_text_record
from .altium_sch_auxiliary_codec import (
    decode_auxiliary_stream,
    encode_auxiliary_stream,
)

log = logging.getLogger(__name__)


def as_dynamic(value: Any) -> Any:
    """Pass a value through `Any` for runtime-only metadata assignments."""
    return value


def _validated_record_header(
    data: bytes, pos: int, section: str | list[str]
) -> tuple[bytes, int, bool]:
    if pos + 4 > len(data):
        raise ValueError(
            f"truncated record-length prefix in section {section!r} at offset {pos}"
        )
    length_bytes = data[pos : pos + 4]
    actual_length = int.from_bytes(length_bytes[:3], byteorder="little")
    is_binary = length_bytes[3] != 0
    if actual_length == 0 and not is_binary:
        raise ValueError(
            f"zero-length text record in section {section!r} at offset {pos}"
        )
    return length_bytes, actual_length, is_binary


def _validated_record_end(
    data: bytes, payload_pos: int, actual_length: int, section: str | list[str]
) -> int:
    record_end = payload_pos + actual_length
    if record_end > len(data):
        raise ValueError(
            f"truncated record payload in section {section!r} at "
            f"offset {payload_pos - 4}: expected {actual_length} bytes, "
            f"found {len(data) - payload_pos}"
        )
    return record_end


def _require_text_record_terminator(
    data: bytes, record_end: int, record_offset: int, section: str | list[str]
) -> None:
    if data[record_end - 1] != 0:
        raise ValueError(
            f"unterminated text record in section {section!r} at offset {record_offset}"
        )


# =============================================================================
# Core Record Parsing
# =============================================================================


def get_records_in_section(
    ole: AltiumOleFile,
    section: str | list[str],
    parse_binary_subrecords: bool = False,
) -> list[dict[str, Any]]:
    """
    Parse one OLE stream section into text-record or binary-record dictionaries.

    Args:
        ole: Open OLE file object.
        section: Section or stream path to read.
        parse_binary_subrecords: Whether to decode binary SubRecords for PCB
            binary streams.

    Returns:
        Parsed record dictionaries. Text records produce key/value mappings.
        Binary records preserve the original payload and metadata needed for
        round-trip writeback.

    Raises:
        IOError: If the section cannot be opened or read.
        ValueError: If record framing is truncated, a text record is
            zero-length, or a required text-record terminator is missing.

    Note:
        - Records are length-prefixed with 4-byte little-endian integers
        - Binary records are identified by non-zero highest byte in length prefix
        - Text records are null-terminated and parsed as key=value pairs
        - Invalid UTF-8 sequences and malformed pairs are logged but processing continues
        - SubRecord parsing adds overhead - only enable when needed
    """

    data = ole.openstream(section)

    # Process records using length prefixes
    pos = 0
    records = []

    while pos < len(data):
        record_offset = pos
        length_bytes, actual_length, is_binary = _validated_record_header(
            data, pos, section
        )
        pos += 4
        record_end = _validated_record_end(data, pos, actual_length, section)

        if is_binary:
            # Store binary record as-is with metadata
            binary_data = data[pos : pos + actual_length]

            # Extract record type (first byte)
            record_type = binary_data[0] if len(binary_data) > 0 else 0

            result = {
                "RECORD": record_type,
                "__BINARY_RECORD__": True,
                "__BINARY_DATA__": binary_data,
                "__ORIGINAL_LENGTH_BYTES__": length_bytes,
            }

            # Optionally parse SubRecords (for PCB files)
            if parse_binary_subrecords and len(binary_data) > 1:
                try:
                    from .altium_binary_utils import parse_subrecords

                    subrecords = parse_subrecords(binary_data, start_offset=1)
                    result["__SUBRECORDS__"] = subrecords
                except Exception as e:
                    # If SubRecord parsing fails, just skip it (file still readable)
                    log.warning(f"SubRecord parsing failed: {e}")

            records.append(result)
            pos += actual_length
        else:
            # Process text record as before
            _require_text_record_terminator(data, record_end, record_offset, section)
            record = data[pos : record_end - 1]

            pos += actual_length

            try:
                # Create dictionary using dictionary comprehension
                result = {}
                empty_pair_cnt = 0

                for pair_index, raw_pair in enumerate(parse_byte_record(record)):
                    field_name = raw_pair.partition(b"=")[0].decode(
                        "ascii", errors="replace"
                    )
                    context = (
                        f"section {section!r}, record {len(records)}, "
                        f"pair {pair_index}, field {field_name!r}"
                    )
                    pair = decode_byte_array(raw_pair, context=context)

                    try:
                        if "=" in pair:
                            key, value = pair.split("=", 1)
                            if not key:
                                log.error(f"Empty key in: {pair}")
                                continue
                            result[key] = value

                        else:
                            # in this case, we don't have a key/value.  Save it as "UNHANDLED" so we can serialize it back
                            result["UNHANDLED" + str(empty_pair_cnt)] = pair
                            empty_pair_cnt = empty_pair_cnt + 1
                    # log.warning(f"Skipping malformed pair: {pair}")
                    except ValueError as e:
                        log.error(f"Error processing pair {pair}: {e}")
                        continue

                records.append(result)

            except UnicodeDecodeError as e:
                log.error("Decode error" + str(e))
                continue

    return records


def create_stream_from_records(
    records: list[dict[str, Any]], *, utf8_sidecars: bool = True
) -> bytes:
    """
    Serialize parsed record dictionaries back into Altium stream bytes.

    Args:
        records: Parsed text or binary record dictionaries.
        utf8_sidecars: When True (schematic convention), non-ASCII values
            receive an auto-synthesized "%UTF8%<KEY>" sidecar pair. PCB
            streams use "UNICODE__<FIELD>" sidebands instead and must pass
            False so no schematic-style sidecars leak into PCB records.

    Returns:
        Length-prefixed Altium stream data.

    Note:
        - Text records are serialized as "|key=value|key=value..." format
        - Binary records with '__BINARY_DATA__' preserve original data exactly
        - Binary records with '__SUBRECORDS__' are re-serialized from parsed SubRecords
        - Non-ASCII values receive a UTF-8 sidecar plus a cp1252-safe fallback
        - UNHANDLED keys (malformed pairs) are serialized without key=value format
        - Each record is prefixed with a 4-byte little-endian length field
        - Text records are null-terminated; binary records use original format
    """
    import struct

    stream_data = bytearray()

    for record in records:
        # Check if this is a binary record
        if record.get("__BINARY_RECORD__", False):
            # Check if we have parsed SubRecords that need re-serialization
            if "__SUBRECORDS__" in record and "__BINARY_DATA__" not in record:
                # Re-serialize from SubRecords
                from .altium_binary_utils import serialize_subrecords

                record_type = record.get("RECORD", 0)
                subrecord_data = serialize_subrecords(record["__SUBRECORDS__"])

                # Reconstruct binary data: [type byte] + [subrecords]
                binary_data = bytes([record_type]) + subrecord_data

                # Reconstruct length bytes (3-byte length + 0x01 high byte for binary marker)
                # Per native serializer parameter implementation: mode byte 0x01 indicates binary record
                actual_length = len(binary_data)
                length_bytes = struct.pack("<I", actual_length | 0x01000000)

                # Write to stream
                stream_data.extend(length_bytes)
                stream_data.extend(binary_data)

            else:
                # Use original binary data
                original_length_bytes = record.get("__ORIGINAL_LENGTH_BYTES__")
                binary_data = record.get("__BINARY_DATA__")

                if original_length_bytes is None or binary_data is None:
                    log.error("Binary record missing required fields")
                    continue

                # Write original 4-byte length header
                stream_data.extend(original_length_bytes)

                # Write binary data
                stream_data.extend(binary_data)

        else:
            # Handle text record. Altium emits a UTF-8 sidecar followed by an
            # ACP-safe fallback whenever a value contains non-ASCII text.
            record_bytes = b"".join(
                _encode_altium_text_pairs(
                    record,
                    skip_private_keys=False,
                    utf8_sidecars=utf8_sidecars,
                )
            )

            # Encode to UTF-8
            # record_bytes = record_string.encode('utf-8')

            # Calculate length (record + null terminator)
            length = len(record_bytes) + 1

            # Write 4-byte length (little-endian) BEFORE the record
            # For text records, highest byte should be 0
            stream_data.extend(length.to_bytes(4, byteorder="little"))

            # Write record data
            stream_data.extend(record_bytes)

            # Write null terminator
            stream_data.append(0)

    return bytes(stream_data)


# =============================================================================
# Storage Stream (Embedded Images)
# =============================================================================


def parse_storage_stream(
    ole: AltiumOleFile,
    debug: bool = False,
) -> dict[str, bytes]:
    """
    Parse the 'Storage' stream to extract embedded images.

    The Storage stream contains zlib-compressed image data in this format:
    - Header record: |HEADER=Icon storage|Weight=N
    - For each file:
        - 5 bytes: metadata (unknown format, appears to be timestamps)
        - 1 byte: filename length
        - N bytes: filename (full path, UTF-8)
        - 4 bytes: compressed data length (uint32 LE)
        - M bytes: zlib-compressed image data

    Args:
        ole: Open AltiumOleFile instance
        debug: Enable debug output

    Returns:
        Dict mapping filename to decompressed image data
    """
    # Check if Storage exists and is a stream (type 2), not a storage directory
    if not ole.exists("Storage") or ole.get_type("Storage") != 2:
        if debug:
            log.info("DEBUG: No Storage stream found (or it's a directory)")
        return {}

    storage_data = ole.openstream("Storage")
    entries = decode_auxiliary_stream(storage_data, expected_header="Icon storage")
    images = {entry.name: entry.data for entry in entries}
    if debug:
        log.info(
            "DEBUG: Parsed %d Storage entries from %d bytes",
            len(entries),
            len(storage_data),
        )
    return images


def parse_storage_stream_raw(
    ole: AltiumOleFile,
    debug: bool = False,
) -> tuple[dict[str, bytes], dict[str, tuple[bytes, bytes]]]:
    """
    Parse the schematic Storage stream while preserving raw compressed entries.

    Returns:
        Tuple of:
        - Dict mapping filename to decompressed image data (for use by IMAGE records)
        - Dict mapping filename to (binary_header, compressed_data) for round-trip
    """
    if not ole.exists("Storage") or ole.get_type("Storage") != 2:
        return {}, {}

    entries = decode_auxiliary_stream(
        ole.openstream("Storage"), expected_header="Icon storage"
    )
    images = {entry.name: entry.data for entry in entries}
    raw_entries = {
        entry.name: (entry.binary_header, entry.compressed_data) for entry in entries
    }
    if debug:
        log.info("DEBUG: Preserved %d raw Storage entries", len(raw_entries))
    return images, raw_entries


def create_storage_stream(
    images: Mapping[str, bytes] | None = None,
    *,
    raw_entries: Mapping[str, tuple[bytes, bytes]] | None = None,
) -> bytes:
    """
    Create a schematic Storage stream for embedded images.

    Args:
        images: Dict mapping filenames to uncompressed image data (can be None or empty)

    Returns:
        Storage stream bytes ready to write to OLE file
    """
    active_images = images or {}
    compressed = (
        {name: raw[1] for name, raw in raw_entries.items()}
        if raw_entries is not None
        else None
    )
    return encode_auxiliary_stream(
        "Icon storage", active_images.items(), raw_compressed=compressed
    )


# =============================================================================
# Low-Level Byte Parsing
# =============================================================================


def parse_byte_record(record: bytes) -> list[bytes]:
    """
    Parse byte array into list of sub-arrays, splitting on '|' and stopping at null.
    """
    if not record:
        return []

    # Skip leading | if present
    if record[0] == ord("|"):
        record = record[1:]

    # Null terminator stops processing.
    null_index = record.find(b"\x00")
    if null_index != -1:
        record = record[:null_index]

    if not record:
        return []

    result = record.split(b"|")

    # Preserve interior empty pairs but match the historical byte-loop behavior
    # that omitted a final empty pair after a trailing delimiter.
    if result and result[-1] == b"":
        result.pop()

    return result


def decode_byte_array(byte_array: bytes, *, context: str | None = None) -> str:
    """
    Decode byte array as UTF-8 if it starts with %UTF8%, otherwise as cp1252.

    Altium is a Windows application and uses Windows-1252 (cp1252) encoding,
    NOT ISO-8859-1. The key difference is bytes 0x80-0x9F:
    - ISO-8859-1: 0x80-0x9F are C1 control characters (not printable)
    - CP1252: 0x80-0x9F are printable chars (smart quotes, dashes, etc.)

    For example, byte 0x96:
    - ISO-8859-1: U+0096 (Start of Guarded Area - control char)
    - CP1252: U+2013 (EN DASH - the correct interpretation)

    Also handles Altium's pipe escape sequences (processed at byte level
    BEFORE character decoding, matching native StrUtils.ProcessMBCSString):
    - 0xA6 (broken bar) -> | (pipe)
    - 0x8E alone -> | (pipe)
    - 0x8E 0x8E -> 0x8E (literal, un-doubled)

    Legacy unmarked UTF-8 is recovered only when strict cp1252 fails. ``context``
    is included in the recovery warning when supplied.

    See native StrUtils.ProcessMBCSString() for authoritative behavior.
    """
    if not byte_array:
        return ""

    # Check if it starts with %UTF8%
    utf8_prefix = b"%UTF8%"

    if byte_array.startswith(utf8_prefix):
        try:
            decoded = byte_array.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"Failed to decode UTF-8 content: {e}") from e
    else:
        # Process MBCS pipe escape sequences at BYTE level before decoding.
        # native StrUtils.ProcessMBCSString() operates on AnsiString (raw bytes),
        # not Unicode. We must do the same to correctly handle byte 0x8E which
        # maps to U+017D (Z with caron) in cp1252 but U+008E in ISO-8859-1.
        processed = _process_pipe_escapes_bytes(byte_array)

        # Decode as cp1252 (Windows-1252) - Altium is a Windows application.
        # Older Monkey writers could put raw UTF-8 beneath an unmarked key. Only
        # recover that legacy defect when strict cp1252 fails and the original
        # bytes form valid UTF-8; valid cp1252 remains authoritative.
        try:
            decoded = processed.decode("cp1252")
        except UnicodeDecodeError as cp1252_error:
            try:
                decoded = byte_array.decode("utf-8")
            except UnicodeDecodeError as utf8_error:
                raise ValueError(
                    f"Failed to decode cp1252 content: {cp1252_error}"
                ) from utf8_error
            location = f" ({context})" if context else ""
            log.warning(
                "Recovered unmarked UTF-8 in Altium text record%s; "
                "rewrite the file to add a %%UTF8%% sidecar",
                location,
            )

    return decoded


def _process_pipe_escapes_bytes(data: bytes) -> bytes:
    """
    Process Altium MBCS pipe escape sequences on raw bytes.

    This operates at the byte level BEFORE character decoding, matching the
    native StrUtils.ProcessMBCSString() which works on AnsiString (raw bytes).

    Escape rules:
    - 0x8E 0x8E (doubled) -> single 0x8E byte (literal)
    - 0x8E (single) -> 0x7C byte (pipe |)
    - 0xA6 (broken bar) -> 0x7C byte (pipe |)

    Args:
        data: Raw byte array (before character decoding)

    Returns:
        Processed byte array with escape sequences resolved
    """
    # Fast path: no special bytes present
    if b"\x8e" not in data and b"\xa6" not in data:
        return data

    result = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x8E:
            # Check for doubled 0x8E (escape for literal 0x8E byte)
            if i + 1 < len(data) and data[i + 1] == 0x8E:
                result.append(0x8E)  # Keep single 0x8E
                i += 2  # Skip both
            else:
                result.append(0x7C)  # Single 0x8E -> pipe |
                i += 1
        elif b == 0xA6:  # Broken bar
            result.append(0x7C)  # -> pipe |
            i += 1
        else:
            result.append(b)
            i += 1

    return bytes(result)


def _escape_pipe_for_altium(value: str) -> str:
    """
    Escape pipe characters for Altium text record format.

    Escape rules:
    - | (pipe) -> broken bar (U+00A6)
    - U+017D (cp1252 byte 0x8E) -> doubled U+017D (encodes to 0x8E 0x8E)

    The U+017D character is what cp1252 decoding produces for byte 0x8E.
    When encoding back to cp1252, U+017D encodes to byte 0x8E, and two U+017D
    bytes 0x8E 0x8E (the doubled escape for literal 0x8E).

    Args:
        value: String value to escape

    Returns:
        Escaped string safe for pipe-delimited format
    """
    # U+017D (Z with caron) is the cp1252 decoded form of byte 0x8E
    _CP1252_0x8E = "\u017d"

    if "|" not in value and _CP1252_0x8E not in value:
        return value

    result = []
    for char in value:
        if char == "|":
            result.append("\xa6")  # Broken bar
        elif char == _CP1252_0x8E:
            result.append(_CP1252_0x8E + _CP1252_0x8E)  # Double it
        else:
            result.append(char)

    return "".join(result)


_UTF8_FIELD_PREFIX = "%UTF8%"


def _needs_utf8_sidecar(value: str) -> bool:
    """Match Altium's dynamic-string sidecar decision for text parameters."""

    return any(ord(char) > 0x7E and char != "\x8e" for char in value)


def _encode_altium_pair(key: str, value: str, *, utf8: bool) -> bytes:
    escaped_value = _escape_pipe_for_altium(value)
    pair = f"|{key}={escaped_value}"
    if utf8:
        return pair.encode("utf-8")
    return pair.encode("cp1252", errors="replace")


def _skip_altium_text_key(key: str, *, skip_private_keys: bool) -> bool:
    if skip_private_keys:
        return key.startswith("_")
    return key.startswith("__") and key.endswith("__")


def _encode_altium_field_pairs(
    key: str,
    raw_value: object,
    explicit_keys: set[str],
    *,
    utf8_sidecars: bool = True,
) -> list[bytes]:
    if "UNHANDLED" in key:
        return [b"|"]

    value = str(raw_value)
    is_utf8 = key.startswith(_UTF8_FIELD_PREFIX)
    pairs: list[bytes] = []
    sibling_key = f"{_UTF8_FIELD_PREFIX}{key}".casefold()
    if (
        utf8_sidecars
        and not is_utf8
        and _needs_utf8_sidecar(value)
        and sibling_key not in explicit_keys
    ):
        pairs.append(
            _encode_altium_pair(f"{_UTF8_FIELD_PREFIX}{key}", value, utf8=True)
        )
    pairs.append(_encode_altium_pair(key, value, utf8=is_utf8))
    return pairs


def _encode_altium_text_pairs(
    record: Mapping[str, object],
    *,
    skip_private_keys: bool,
    utf8_sidecars: bool = True,
) -> list[bytes]:
    """Encode Altium parameter pairs with UTF-8 sidecars and safe fallbacks."""

    explicit_keys = {
        key.casefold()
        for key in record
        if isinstance(key, str) and key.startswith(_UTF8_FIELD_PREFIX)
    }
    pairs: list[bytes] = []
    for key, raw_value in record.items():
        if _skip_altium_text_key(key, skip_private_keys=skip_private_keys):
            continue
        pairs.extend(
            _encode_altium_field_pairs(
                key, raw_value, explicit_keys, utf8_sidecars=utf8_sidecars
            )
        )
    return pairs


def encode_altium_record(record: dict, *, utf8_sidecars: bool = True) -> bytes:
    """
    Encode a record dictionary back to Altium format (round-trip support).

    Handles both text records (key-value pairs) and binary records (PIN, etc.).
    Text records have pipe characters escaped per native
    StrUtils.ReplaceSpecialParameterChars. Non-ASCII values use Altium's
    UTF-8-sidecar plus ACP-safe-fallback representation.

    Args:
        record: Dictionary of key-value pairs, or binary record with special keys
        utf8_sidecars: When True (schematic convention), non-ASCII values
            receive an auto-synthesized "%UTF8%<KEY>" sidecar pair. PCB
            property records use "UNICODE__<FIELD>" sidebands instead and
            must pass False.

    Returns:
        Bytes for length-prefixed record (text or binary)
    """
    # Check for binary record (e.g., PIN records)
    if record.get("__BINARY_RECORD__", False):
        binary_data = record.get("__BINARY_DATA__")
        if binary_data is None:
            raise ValueError("Binary record missing __BINARY_DATA__")

        # Use original length bytes if available (preserves mode byte for round-trip)
        # This is critical for byte-identical round-trips
        if "__ORIGINAL_LENGTH_BYTES__" in record:
            length_bytes = record["__ORIGINAL_LENGTH_BYTES__"]
        else:
            # Fallback: calculate with 0x01 mode byte (per native serializer parameter implementation)
            length = len(binary_data)
            length_bytes = bytes(
                [
                    length & 0xFF,
                    (length >> 8) & 0xFF,
                    (length >> 16) & 0xFF,
                    0x01,  # Binary mode (native standard)
                ]
            )
        return length_bytes + binary_data

    encoded_pairs = _encode_altium_text_pairs(
        record, skip_private_keys=True, utf8_sidecars=utf8_sidecars
    )

    # Join all encoded pairs
    record_bytes = b"".join(encoded_pairs)

    # Add null terminator
    record_bytes += b"\x00"

    # Build length prefix (4 bytes, little-endian)
    length = len(record_bytes)
    length_bytes = length.to_bytes(4, byteorder="little")

    return length_bytes + record_bytes


# =============================================================================
# PcbDoc Parsing (WideStrings6 / Texts6)
# =============================================================================


def parse_widestrings6(ole: AltiumOleFile, verbose: bool = False) -> dict[int, str]:
    """
    Parse WideStrings6/Data stream to build string lookup table.

    This table is used by Texts6 to retrieve Unicode text content (designators, comments, etc.).

    Binary format (from record format binary header):
        - 4 bytes: index (uint32) - string table index
        - 4 bytes: length (uint32) - byte count (NOT character count!)
        - length bytes: UTF-16LE string data (includes 2-byte null terminator)

    For empty strings (length <= 2), no string bytes are present.

    Args:
        ole: Open AltiumOleFile object
        verbose: If True, emit diagnostic output for debugging

    Returns:
        Dict mapping widestring_index -> text string
    """
    if not ole.exists(["WideStrings6", "Data"]):
        if verbose:
            log.warning("WideStrings6/Data stream not found")
        return {}

    data = ole.openstream(["WideStrings6", "Data"])
    strings = {}
    offset = 0
    record_num = 0

    if verbose:
        log.info(f"[WideStrings6] Total data size: {len(data)} bytes")

    while offset + 8 <= len(data):
        record_start = offset

        # Read index and length
        index = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4

        length = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4

        # Empty strings (length <= 2) have no bytes to read
        # (length <= 2 means just null terminator or less)
        if length <= 2:
            strings[index] = ""
            if verbose:
                log.info(
                    f"[WideStrings6] Record {record_num}: index={index}, length={length} (empty)"
                )
            record_num += 1
            continue

        # Length is already in BYTES (not character count!)
        # The string data includes a 2-byte null terminator, so decode length-2 bytes
        byte_count = length
        if offset + byte_count > len(data):
            if verbose:
                log.warning(
                    f"[WideStrings6] Record {record_num}: index={index}, length={length}, "
                    f"exceeds remaining data (offset={offset}, data_len={len(data)})"
                )
            break

        # Decode length-2 bytes as UTF-16LE (exclude null terminator)
        text_bytes = data[offset : offset + length - 2]

        if verbose:
            # Show raw bytes for debugging
            hex_preview = " ".join(
                f"{b:02x}" for b in data[offset : offset + min(32, length)]
            )
            log.info(
                f"[WideStrings6] Record {record_num}: index={index}, length={length}, "
                f"offset={record_start}"
            )
            log.info(f"[WideStrings6]   Raw bytes (first 32): {hex_preview}")

        try:
            text = text_bytes.decode("utf-16le", errors="replace")
        except Exception as e:
            if verbose:
                log.warning(f"[WideStrings6]   Decode error: {e}")
            text = ""

        # Check for control characters (potential corruption)
        has_control_chars = any(ord(c) < 32 and c not in "\t\n\r" for c in text)
        if verbose:
            log.info(f"[WideStrings6]   Decoded text: {repr(text)}")
            if has_control_chars:
                log.warning(
                    "[WideStrings6]   WARNING: Text contains control characters!"
                )

        strings[index] = text
        offset += length  # Advance by length bytes (not length*2!)
        record_num += 1

    if verbose:
        log.info(
            f"[WideStrings6] Parsed {record_num} records, {len(strings)} strings total"
        )

    return strings


def parse_texts6_designators(
    ole: AltiumOleFile, string_table: dict[int, str], verbose: bool = False
) -> dict[int, str]:
    """
    Parse Texts6/Data to extract component designators.

    This is the CORRECT way to get multi-channel designators.
    Components6 only has base designators (e.g., "U6"), but Texts6 has the
    actual displayed designators (e.g., "U6_CH1", "U6_CH2").

    Args:
        ole: Open AltiumOleFile object
        string_table: WideStrings6 lookup table (from parse_widestrings6)
        verbose: If True, emit diagnostic output for debugging

    Returns:
        Dict mapping component_index -> actual_designator
    """
    if not ole.exists(["Texts6", "Data"]):
        if verbose:
            log.warning("Texts6/Data stream not found")
        return {}

    data = ole.openstream(["Texts6", "Data"])
    designator_map = {}
    offset = 0
    record_num = 0
    designator_count = 0

    if verbose:
        log.info(f"[Texts6] Total data size: {len(data)} bytes")
        log.info(f"[Texts6] WideStrings6 table has {len(string_table)} entries")

    while offset < len(data) - 10:
        # Find Text record (type byte = 0x05)
        if data[offset] != 0x05:
            offset += 1
            continue

        try:
            record_start = offset

            # Read SubRecord 1 length
            subrecord_len = struct.unpack("<I", data[offset + 1 : offset + 5])[0]

            # Sanity check
            if subrecord_len < 40 or subrecord_len > 500:
                offset += 1
                continue

            # Parse SubRecord 1 content
            content = data[offset + 5 : offset + 5 + subrecord_len]

            if len(content) < subrecord_len:
                offset += 1
                continue

            component_index = _parse_texts6_component_index(content)
            next_offset = offset + 5 + subrecord_len

            # Check if this is a component text (not board text)
            if component_index == 0xFFFF:
                offset = next_offset
                record_num += 1
                continue

            widestring_index = _parse_texts6_designator_widestring_index(
                content, subrecord_len
            )
            if widestring_index is not None:
                designator_count += 1
                _log_texts6_designator_record(
                    record_num,
                    record_start,
                    component_index,
                    widestring_index,
                    subrecord_len,
                    content,
                    verbose,
                )
                text_content = _resolve_texts6_designator_text(
                    data,
                    next_offset,
                    widestring_index,
                    string_table,
                    verbose,
                )
                if text_content:
                    designator_map[component_index] = text_content

            # Move to next record
            offset = next_offset
            record_num += 1

        except Exception as e:
            if verbose:
                log.error(f"[Texts6] Exception at offset {offset}: {e}")
            offset += 1
            continue

    if verbose:
        log.info(
            f"[Texts6] Parsed {record_num} records, found {designator_count} designators"
        )
        log.info(f"[Texts6] Final designator_map has {len(designator_map)} entries:")
        for comp_idx, des in sorted(designator_map.items()):
            has_ctrl = any(ord(c) < 32 for c in des)
            flag = " [CORRUPTED]" if has_ctrl else ""
            log.info(f"[Texts6]   Component {comp_idx} -> {repr(des)}{flag}")

    return designator_map


def _parse_texts6_component_index(content: bytes) -> int:
    return struct.unpack("<H", content[7:9])[0]


def _parse_texts6_designator_widestring_index(
    content: bytes,
    subrecord_len: int,
) -> int | None:
    if subrecord_len < 123 or len(content) < 119:
        return None
    if content[41] == 0:
        return None
    return struct.unpack("<I", content[115:119])[0]


def _log_texts6_designator_record(
    record_num: int,
    record_start: int,
    component_index: int,
    widestring_index: int,
    subrecord_len: int,
    content: bytes,
    verbose: bool,
) -> None:
    if not verbose:
        return
    log.info(
        f"[Texts6] Record {record_num} @ offset {record_start}: "
        f"component_index={component_index}, is_designator=True, "
        f"widestring_index={widestring_index}, subrecord_len={subrecord_len}"
    )
    hex_around_115 = " ".join(f"{b:02x}" for b in content[110:125])
    log.info(f"[Texts6]   Bytes [110:125]: {hex_around_115}")


def _resolve_texts6_designator_text(
    data: bytes,
    subrecord2_offset: int,
    widestring_index: int,
    string_table: dict[int, str],
    verbose: bool,
) -> str:
    text_content, text_source = _read_texts6_designator_text_source(
        data,
        subrecord2_offset,
        widestring_index,
        string_table,
        verbose,
    )

    text_before_strip = text_content
    if text_content:
        text_content = _strip_texts6_control_prefix(text_content)

    if verbose:
        log.info(f"[Texts6]   Text source: {text_source}")
        log.info(f"[Texts6]   Text before strip: {repr(text_before_strip)}")
        log.info(f"[Texts6]   Text after strip: {repr(text_content)}")
        if _texts6_has_control_chars(text_content):
            log.warning("[Texts6]   WARNING: Designator contains control characters!")

    return text_content


def _read_texts6_designator_text_source(
    data: bytes,
    subrecord2_offset: int,
    widestring_index: int,
    string_table: dict[int, str],
    verbose: bool,
) -> tuple[str, str]:
    if widestring_index in string_table:
        return string_table[widestring_index], "WideStrings6"

    if subrecord2_offset + 4 >= len(data):
        return "", ""

    subrecord2_len = struct.unpack(
        "<I", data[subrecord2_offset : subrecord2_offset + 4]
    )[0]
    if verbose:
        log.info(
            f"[Texts6]   WideStrings6 index {widestring_index} not found, "
            f"trying SubRecord2 (len={subrecord2_len})"
        )
    if not 0 < subrecord2_len < 256:
        return "", ""

    text_bytes = data[subrecord2_offset + 4 : subrecord2_offset + 4 + subrecord2_len]
    if verbose:
        hex_preview = " ".join(f"{b:02x}" for b in text_bytes[:32])
        log.info(f"[Texts6]   SubRecord2 raw bytes: {hex_preview}")
    try:
        return text_bytes.decode("utf-8", errors="replace").rstrip(
            "\x00"
        ), "SubRecord2-UTF8"
    except Exception:
        return text_bytes.decode("latin1", errors="replace").rstrip(
            "\x00"
        ), "SubRecord2-Latin1"


def _strip_texts6_control_prefix(text_content: str) -> str:
    return text_content.lstrip(
        "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f"
        "\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f"
    )


def _texts6_has_control_chars(text_content: str) -> bool:
    if not text_content:
        return False
    return any(ord(c) < 32 and c not in "\t\n\r" for c in text_content)
