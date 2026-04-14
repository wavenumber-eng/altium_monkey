"""Internal typed models for PcbLib file-level sections."""

from __future__ import annotations

import random
import string
import struct
import uuid
from dataclasses import dataclass

from .altium_pcb_stream_helpers import (
    build_length_prefixed_ascii as _build_length_prefixed_ascii,
)
from .altium_pcb_stream_helpers import (
    extract_length_prefixed_ascii as _extract_length_prefixed_ascii,
)


def _parse_pipe_properties(body: str) -> dict[str, str]:
    return {
        key: value
        for part in body.split("|")
        if "=" in part
        for key, value in [part.split("=", 1)]
    }


def _decode_ascii_codes(text: str) -> str:
    if not text:
        return ""
    return "".join(chr(int(part)) for part in text.split(",") if part)


def _encode_ascii_codes(text: str) -> str:
    return ",".join(str(ord(ch)) for ch in text) if text else ""


@dataclass(frozen=True)
class PcbLibCountHeader:
    count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibCountHeader":
        if len(data) != 4:
            raise ValueError("Invalid count header stream")
        return cls(count=struct.unpack("<I", data)[0])

    @classmethod
    def zero(cls) -> "PcbLibCountHeader":
        return cls(count=0)

    @classmethod
    def one(cls) -> "PcbLibCountHeader":
        return cls(count=1)

    def to_bytes(self) -> bytes:
        return struct.pack("<I", self.count)


@dataclass(frozen=True)
class PcbLibFileHeader:
    file_id: str
    version: float
    magic: str

    @classmethod
    def default(cls, magic: str) -> "PcbLibFileHeader":
        return cls(
            file_id="PCB 6.0 Binary Library File",
            version=5.01,
            magic=magic,
        )

    @classmethod
    def random_default(cls) -> "PcbLibFileHeader":
        return cls.default("".join(random.choices(string.ascii_uppercase, k=8)))

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibFileHeader":
        if len(data) < 4:
            raise ValueError("Invalid FileHeader stream")
        file_id_len = struct.unpack("<I", data[:4])[0]
        offset = 4
        if len(data) < offset + 1 + file_id_len + 8 + 4:
            raise ValueError("Invalid FileHeader stream")
        pascal_len = data[offset]
        file_id = data[offset + 1 : offset + 1 + pascal_len].decode("ascii")
        offset += 1 + file_id_len
        version = struct.unpack("<d", data[offset : offset + 8])[0]
        offset += 8
        magic_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if len(data) < offset + 1 + magic_len:
            raise ValueError("Invalid FileHeader stream")
        pascal_len = data[offset]
        magic = data[offset + 1 : offset + 1 + pascal_len].decode("ascii")
        return cls(file_id=file_id, version=version, magic=magic)

    def to_bytes(self) -> bytes:
        file_id_bytes = self.file_id.encode("ascii")
        magic_bytes = self.magic.encode("ascii")
        buf = bytearray()
        buf.extend(struct.pack("<I", len(file_id_bytes)))
        buf.append(len(file_id_bytes))
        buf.extend(file_id_bytes)
        buf.extend(struct.pack("<d", self.version))
        buf.extend(struct.pack("<I", len(magic_bytes)))
        buf.append(len(magic_bytes))
        buf.extend(magic_bytes)
        return bytes(buf)


@dataclass(frozen=True)
class PcbLibComponentParamsTocEntry:
    name: str
    pad_count: int
    height: str
    description: str

    @classmethod
    def from_text(cls, text: str) -> "PcbLibComponentParamsTocEntry":
        values = _parse_pipe_properties(text)
        return cls(
            name=values.get("Name", ""),
            pad_count=int(values.get("Pad Count", 0)),
            height=values.get("Height", ""),
            description=values.get("Description", "").rstrip("\r\n"),
        )

    def to_text(self) -> str:
        return (
            f"Name={self.name}|Pad Count={self.pad_count}|"
            f"Height={self.height}|Description={self.description}\r\n\x00"
        )


@dataclass(frozen=True)
class PcbLibComponentParamsToc:
    entries: tuple[PcbLibComponentParamsTocEntry, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibComponentParamsToc":
        entries: list[PcbLibComponentParamsTocEntry] = []
        offset = 0
        while offset + 4 <= len(data):
            chunk_len = struct.unpack("<I", data[offset : offset + 4])[0]
            offset += 4
            if offset + chunk_len > len(data):
                raise ValueError("Invalid ComponentParamsTOC stream")
            text = data[offset : offset + chunk_len].decode("latin-1").rstrip("\x00")
            offset += chunk_len
            entries.append(PcbLibComponentParamsTocEntry.from_text(text))
        return cls(entries=tuple(entries))

    def to_bytes(self) -> bytes:
        buf = bytearray()
        for entry in self.entries:
            text_bytes = entry.to_text().encode("ascii", errors="replace")
            buf.extend(struct.pack("<I", len(text_bytes)))
            buf.extend(text_bytes)
        return bytes(buf)


@dataclass(frozen=True)
class PcbLibPadViaLibrary:
    library_id: uuid.UUID
    library_name: str = "<Local>"
    display_units: int = 1

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibPadViaLibrary":
        values = _parse_pipe_properties(_extract_length_prefixed_ascii(data))
        guid_text = values.get("PADVIALIBRARY.LIBRARYID", "").strip("{}")
        return cls(
            library_id=uuid.UUID(guid_text),
            library_name=values.get("PADVIALIBRARY.LIBRARYNAME", "<Local>"),
            display_units=int(values.get("PADVIALIBRARY.DISPLAYUNITS", 1)),
        )

    def to_bytes(self) -> bytes:
        body = (
            f"|PADVIALIBRARY.LIBRARYID={{{str(self.library_id).upper()}}}"
            f"|PADVIALIBRARY.LIBRARYNAME={self.library_name}"
            f"|PADVIALIBRARY.DISPLAYUNITS={self.display_units}\x00"
        )
        return _build_length_prefixed_ascii(body)


@dataclass(frozen=True)
class PcbLibLayerKindMapping:
    format_version: str = "1.0"
    reserved_tail: bytes = b"\x00" * 8

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibLayerKindMapping":
        if len(data) < 4:
            raise ValueError("Invalid LayerKindMapping stream")
        text_len = struct.unpack("<I", data[:4])[0]
        if len(data) < 4 + text_len:
            raise ValueError("Invalid LayerKindMapping stream")
        text = data[4 : 4 + text_len].decode("utf-16le").rstrip("\x00")
        return cls(
            format_version=text,
            reserved_tail=data[4 + text_len :],
        )

    def to_bytes(self) -> bytes:
        text_bytes = (self.format_version + "\x00").encode("utf-16le")
        return struct.pack("<I", len(text_bytes)) + text_bytes + self.reserved_tail


@dataclass(frozen=True)
class PcbLibFileVersionInfoEntry:
    version_name: str
    forward_message: str
    backward_message: str


@dataclass(frozen=True)
class PcbLibFileVersionInfo:
    entries: tuple[PcbLibFileVersionInfoEntry, ...]

    @classmethod
    def default(cls) -> "PcbLibFileVersionInfo":
        return cls(
            entries=(
                PcbLibFileVersionInfoEntry(
                    version_name="Winter 09",
                    forward_message="",
                    backward_message=(
                        "<b>CAUTION</b> - Vias support varying diameters across "
                        "layerstack. If this feature is used in design, extra values will be discarded."
                    ),
                ),
                PcbLibFileVersionInfoEntry(
                    version_name="Winter 09",
                    forward_message="",
                    backward_message=(
                        "<b>CAUTION</b> - File may contain pads with hole offsets. "
                        "Hole offset information will be discarded."
                    ),
                ),
                PcbLibFileVersionInfoEntry(
                    version_name="Winter 09",
                    forward_message="",
                    backward_message=(
                        "<b>CAUTION</b> - 3D models now support texturing."
                        "If used in design these textures will be discarded."
                    ),
                ),
                PcbLibFileVersionInfoEntry(
                    version_name="Summer 09",
                    forward_message="",
                    backward_message=(
                        "<b>CAUTION</b> - Support was added for 32 Mechanical Layers. "
                        "Objects on mechanical layers beyond 16 have been moved to Mechanical Layer 16."
                    ),
                ),
                PcbLibFileVersionInfoEntry(
                    version_name="Release 10",
                    forward_message="",
                    backward_message=(
                        "<b>CAUTION</b> - New Custom Grids and Guides were introduced. "
                        "Be aware that your design might contain Custom Grids and Guides that cannot "
                        "be read in previous versions. "
                    ),
                ),
            )
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibFileVersionInfo":
        values = _parse_pipe_properties(_extract_length_prefixed_ascii(data))
        count = int(values.get("COUNT", 0))
        entries: list[PcbLibFileVersionInfoEntry] = []
        for index in range(count):
            entries.append(
                PcbLibFileVersionInfoEntry(
                    version_name=_decode_ascii_codes(values.get(f"VER{index}", "")),
                    forward_message=_decode_ascii_codes(
                        values.get(f"FWDMSG{index}", "")
                    ),
                    backward_message=_decode_ascii_codes(
                        values.get(f"BKMSG{index}", "")
                    ),
                )
            )
        return cls(entries=tuple(entries))

    def to_bytes(self) -> bytes:
        parts = [f"COUNT={len(self.entries)}"]
        for index, entry in enumerate(self.entries):
            parts.append(f"VER{index}={_encode_ascii_codes(entry.version_name)}")
            parts.append(f"FWDMSG{index}={_encode_ascii_codes(entry.forward_message)}")
            parts.append(f"BKMSG{index}={_encode_ascii_codes(entry.backward_message)}")
        return _build_length_prefixed_ascii("|" + "|".join(parts) + "\x00")


@dataclass(frozen=True)
class PcbLibSectionKeyEntry:
    full_name: str
    ole_key: str


@dataclass(frozen=True)
class PcbLibSectionKeys:
    entries: tuple[PcbLibSectionKeyEntry, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibSectionKeys":
        if len(data) < 4:
            return cls(entries=())
        count = struct.unpack("<I", data[0:4])[0]
        offset = 4
        entries: list[PcbLibSectionKeyEntry] = []
        for _ in range(count):
            full_name, offset = cls._read_pascal_subrecord(data, offset)
            ole_key, offset = cls._read_pascal_subrecord(data, offset)
            entries.append(PcbLibSectionKeyEntry(full_name=full_name, ole_key=ole_key))
        return cls(entries=tuple(entries))

    @staticmethod
    def _read_pascal_subrecord(data: bytes, offset: int) -> tuple[str, int]:
        if offset + 4 > len(data):
            raise ValueError("Invalid SectionKeys stream")
        sr_len = struct.unpack("<I", data[offset : offset + 4])[0]
        offset += 4
        if offset + sr_len > len(data) or sr_len < 1:
            raise ValueError("Invalid SectionKeys stream")
        pascal_len = data[offset]
        value = data[offset + 1 : offset + 1 + pascal_len].decode("latin-1")
        return value, offset + sr_len

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(struct.pack("<I", len(self.entries)))
        for entry in self.entries:
            buf.extend(self._build_pascal_subrecord(entry.full_name))
            buf.extend(self._build_pascal_subrecord(entry.ole_key))
        return bytes(buf)

    @staticmethod
    def _build_pascal_subrecord(text: str) -> bytes:
        encoded = text.encode("latin-1")
        subrecord = bytearray([len(encoded)]) + encoded
        return struct.pack("<I", len(subrecord)) + subrecord

    def to_mapping(self) -> dict[str, str]:
        return {entry.full_name: entry.ole_key for entry in self.entries}
