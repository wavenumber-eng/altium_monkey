"""Parse stored embedded-board reference records from a PcbDoc."""

from __future__ import annotations

from dataclasses import dataclass
import struct


@dataclass(frozen=True, slots=True)
class EmbeddedBoardStreamError(ValueError):
    """Stable failure while decoding EmbeddedBoards6 source records."""

    detail: str


@dataclass(frozen=True, slots=True)
class AltiumPcbEmbeddedBoard:
    """One immutable EmbeddedBoards6/Data source record."""

    document_path: str
    layer: str
    x: str
    y: str
    x1: str
    y1: str
    x2: str
    y2: str
    rotation: str
    mirror: str
    row_count: str
    column_count: str
    row_spacing: str
    column_spacing: str
    origin_mode: str
    raw_fields: tuple[tuple[str, str], ...]

    @classmethod
    def from_fields(
        cls,
        fields: tuple[tuple[str, str], ...],
    ) -> AltiumPcbEmbeddedBoard:
        """Create a typed view without interpreting placement semantics."""

        values = dict(fields)
        return cls(
            document_path=values.get("DOCUMENTPATH", ""),
            layer=values.get("LAYER", ""),
            x=values.get("X", ""),
            y=values.get("Y", ""),
            x1=values.get("X1", ""),
            y1=values.get("Y1", ""),
            x2=values.get("X2", ""),
            y2=values.get("Y2", ""),
            rotation=values.get("ROTATION", ""),
            mirror=values.get("MIRROR", ""),
            row_count=values.get("ROWCOUNT", ""),
            column_count=values.get("COLCOUNT", ""),
            row_spacing=values.get("ROWSPACING", ""),
            column_spacing=values.get("COLSPACING", ""),
            origin_mode=values.get("ORIGINMODE", ""),
            raw_fields=fields,
        )

    def _state_signature(self) -> tuple[tuple[str, str], ...]:
        return self.raw_fields


def parse_embedded_boards6_stream(
    data: bytes,
    *,
    expected_count: int,
) -> tuple[AltiumPcbEmbeddedBoard, ...]:
    """Decode exact length-prefixed CP1252 embedded-board records."""

    if expected_count < 0:
        raise EmbeddedBoardStreamError("header count is negative")
    offset = 0
    records: list[AltiumPcbEmbeddedBoard] = []
    while offset < len(data):
        if offset + 4 > len(data):
            raise EmbeddedBoardStreamError("record length prefix is truncated")
        length = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if length == 0 or offset + length > len(data):
            raise EmbeddedBoardStreamError("record payload length is invalid")
        payload = data[offset : offset + length]
        offset += length
        records.append(_parse_embedded_board_record(payload, len(records)))
    if len(records) != expected_count:
        raise EmbeddedBoardStreamError(
            f"header declares {expected_count} records but Data contains {len(records)}"
        )
    return tuple(records)


def _parse_embedded_board_record(
    payload: bytes,
    record_index: int,
) -> AltiumPcbEmbeddedBoard:
    content = payload.rstrip(b"\x00\r\n")
    if not content or b"\x00" in content:
        raise EmbeddedBoardStreamError(
            f"record {record_index} has invalid terminator placement"
        )
    try:
        text = content.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise EmbeddedBoardStreamError(
            f"record {record_index} is not valid CP1252"
        ) from exc
    fields: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token in text.split("|"):
        if not token:
            continue
        key, separator, value = token.partition("=")
        if not separator or not key:
            raise EmbeddedBoardStreamError(
                f"record {record_index} contains a malformed property"
            )
        if key in seen:
            raise EmbeddedBoardStreamError(
                f"record {record_index} repeats property {key!r}"
            )
        seen.add(key)
        fields.append((key, value))
    if not fields:
        raise EmbeddedBoardStreamError(f"record {record_index} has no properties")
    return AltiumPcbEmbeddedBoard.from_fields(tuple(fields))


__all__ = (
    "AltiumPcbEmbeddedBoard",
    "EmbeddedBoardStreamError",
    "parse_embedded_boards6_stream",
)
