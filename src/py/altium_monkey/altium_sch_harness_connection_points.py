"""Codec for the V5 harness connection-point connector side stream."""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass

from .altium_sch_auxiliary_codec import (
    SchAuxiliaryReadLimits,
    SchAuxiliaryStreamError,
    decode_auxiliary_stream,
    encode_auxiliary_stream,
)

_STREAM_NAME = "HarnessConnectionPointConnector"
_DATA_VERSION = 1
_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
_MAX_CONNECTION_POINTS = 50_000
_MAX_CONNECTORS = 200_000
_MAX_PINS = 1_000_000
_MAX_STRING_BYTES = 1_048_576


class HarnessConnectionPointDataError(ValueError):
    """Raised when connector side-stream data is malformed or exceeds limits."""


@dataclass(frozen=True, slots=True)
class HarnessConnectionPointConnectorData:
    """One serialized connector reference and its ordered unique pin IDs."""

    connector_id: str
    pin_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HarnessConnectionPointData:
    """One serialized connection-point row."""

    connection_point_id: str
    connectors: tuple[HarnessConnectionPointConnectorData, ...]


class _PayloadReader:
    def __init__(self, data: bytes) -> None:
        if len(data) > _MAX_PAYLOAD_BYTES:
            raise HarnessConnectionPointDataError(
                "connector payload exceeds byte limit"
            )
        self.data = data
        self.offset = 0

    def read_i32(self, field: str) -> int:
        if self.offset + 4 > len(self.data):
            raise HarnessConnectionPointDataError(f"truncated {field}")
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def read_count(self, field: str, maximum: int) -> int:
        value = self.read_i32(field)
        if value < 0:
            raise HarnessConnectionPointDataError(f"{field} is negative")
        if value > maximum:
            raise HarnessConnectionPointDataError(f"{field} exceeds reviewed limit")
        return value

    def read_string(self, field: str) -> str:
        length = self._read_7bit_length(field)
        if length > _MAX_STRING_BYTES:
            raise HarnessConnectionPointDataError(f"{field} exceeds byte limit")
        end = self.offset + length
        if end > len(self.data):
            raise HarnessConnectionPointDataError(f"truncated {field}")
        raw = self.data[self.offset : end]
        self.offset = end
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HarnessConnectionPointDataError(f"{field} is not UTF-8") from exc

    def _read_7bit_length(self, field: str) -> int:
        value = 0
        for shift in range(0, 35, 7):
            if self.offset >= len(self.data):
                raise HarnessConnectionPointDataError(f"truncated {field} length")
            byte = self.data[self.offset]
            self.offset += 1
            if shift == 28 and byte > 0x0F:
                raise HarnessConnectionPointDataError(f"invalid {field} length")
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
        raise HarnessConnectionPointDataError(f"invalid {field} length")


def _header_without_weight(encoded: bytes) -> bytes:
    header_length = struct.unpack_from("<I", encoded, 0)[0]
    header_end = 4 + header_length
    header = encoded[4:header_end]
    weight = b"|Weight=1"
    if not header.endswith(weight + b"\0"):
        raise HarnessConnectionPointDataError("unexpected auxiliary header encoding")
    replacement = header[: -len(weight + b"\0")] + b"\0"
    return struct.pack("<I", len(replacement)) + replacement + encoded[header_end:]


def _header_with_weight(encoded: bytes) -> bytes:
    header_length = struct.unpack_from("<I", encoded, 0)[0]
    header_end = 4 + header_length
    header = encoded[4:header_end]
    expected = f"|HEADER={_STREAM_NAME}".encode("ascii") + b"\0"
    if header != expected:
        raise HarnessConnectionPointDataError("unexpected connector stream header")
    replacement = header[:-1] + b"|Weight=1\0"
    return struct.pack("<I", len(replacement)) + replacement + encoded[header_end:]


def _decode_payload(stream: bytes) -> bytes:
    try:
        normalized = _header_with_weight(stream)
        entries = decode_auxiliary_stream(
            normalized,
            expected_header=_STREAM_NAME,
            limits=SchAuxiliaryReadLimits(
                max_decompressed_blob_bytes=_MAX_PAYLOAD_BYTES,
                max_total_decompressed_blob_bytes=_MAX_PAYLOAD_BYTES,
            ),
        )
    except (SchAuxiliaryStreamError, struct.error) as exc:
        raise HarnessConnectionPointDataError(str(exc)) from exc
    if len(entries) != 1 or entries[0].name != _STREAM_NAME:
        raise HarnessConnectionPointDataError(
            "connector stream must contain one payload"
        )
    return entries[0].data


def decode_harness_connection_point_stream(
    stream: bytes,
) -> tuple[HarnessConnectionPointData, ...]:
    """Decode one complete managed V5 connector side stream."""
    payload = _decode_payload(stream)
    if not payload:
        return ()
    reader = _PayloadReader(payload)
    version = reader.read_i32("version")
    if version > _DATA_VERSION:
        return ()
    point_count = reader.read_count("connection-point count", _MAX_CONNECTION_POINTS)
    points: list[HarnessConnectionPointData] = []
    connector_total = 0
    pin_total = 0
    for point_index in range(point_count):
        point_id = reader.read_string(f"connection point {point_index} ID")
        connector_count = reader.read_count(
            f"connection point {point_index} connector count",
            _MAX_CONNECTORS - connector_total,
        )
        connector_total += connector_count
        connectors: list[HarnessConnectionPointConnectorData] = []
        for connector_index in range(connector_count):
            connector_id = reader.read_string(
                f"connection point {point_index} connector {connector_index} ID"
            )
            pin_count = reader.read_count(
                f"connection point {point_index} connector {connector_index} pin count",
                _MAX_PINS - pin_total,
            )
            pin_total += pin_count
            pin_ids = tuple(
                reader.read_string(
                    f"connection point {point_index} connector {connector_index} pin {pin_index} ID"
                )
                for pin_index in range(pin_count)
            )
            connectors.append(
                HarnessConnectionPointConnectorData(connector_id, pin_ids)
            )
        points.append(HarnessConnectionPointData(point_id, tuple(connectors)))
    if reader.offset != len(reader.data):
        raise HarnessConnectionPointDataError("trailing connector payload bytes")
    return tuple(points)


def _write_7bit_length(output: bytearray, value: int) -> None:
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)


def _write_string(output: bytearray, value: str, field: str) -> None:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise HarnessConnectionPointDataError(f"{field} is not valid Unicode") from exc
    if len(encoded) > _MAX_STRING_BYTES:
        raise HarnessConnectionPointDataError(f"{field} exceeds byte limit")
    _write_7bit_length(output, len(encoded))
    output.extend(encoded)


def encode_harness_connection_point_stream(
    points: Iterable[HarnessConnectionPointData],
) -> bytes:
    """Encode managed V5 connector data with its weightless stream header."""
    prepared = tuple(points)
    if len(prepared) > _MAX_CONNECTION_POINTS:
        raise HarnessConnectionPointDataError("connection-point count exceeds limit")
    output = bytearray(struct.pack("<ii", _DATA_VERSION, len(prepared)))
    connector_total = 0
    pin_total = 0
    for point_index, point in enumerate(prepared):
        _write_string(
            output, point.connection_point_id, f"connection point {point_index} ID"
        )
        connector_total += len(point.connectors)
        if connector_total > _MAX_CONNECTORS:
            raise HarnessConnectionPointDataError("connector count exceeds limit")
        output.extend(struct.pack("<i", len(point.connectors)))
        for connector_index, connector in enumerate(point.connectors):
            _write_string(
                output,
                connector.connector_id,
                f"connection point {point_index} connector {connector_index} ID",
            )
            pin_total += len(connector.pin_ids)
            if pin_total > _MAX_PINS:
                raise HarnessConnectionPointDataError("pin count exceeds limit")
            output.extend(struct.pack("<i", len(connector.pin_ids)))
            for pin_index, pin_id in enumerate(connector.pin_ids):
                _write_string(
                    output,
                    pin_id,
                    f"connection point {point_index} connector {connector_index} pin {pin_index} ID",
                )
    if len(output) > _MAX_PAYLOAD_BYTES:
        raise HarnessConnectionPointDataError("connector payload exceeds byte limit")
    encoded = encode_auxiliary_stream(_STREAM_NAME, [(_STREAM_NAME, bytes(output))])
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise HarnessConnectionPointDataError("connector stream exceeds byte limit")
    return _header_without_weight(encoded)


__all__ = [
    "HarnessConnectionPointConnectorData",
    "HarnessConnectionPointData",
    "HarnessConnectionPointDataError",
    "decode_harness_connection_point_stream",
    "encode_harness_connection_point_stream",
]
