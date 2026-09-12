"""Typed public inventory objects for embedded PCB assets."""

from __future__ import annotations

import copy
import hashlib
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .altium_json_helpers import json_ready as _json_ready
from typing import Literal, cast

from .altium_api_markers import public_api
from .altium_embedded_files import (
    EmbeddedFont,
    classify_embedded_model_format,
    sanitize_embedded_asset_name,
)

_EMBEDDED_ASSET_SCHEMA = "altium_monkey.pcb.embedded_assets.a0"


@public_api
@dataclass(frozen=True)
class EmbeddedAssetReference:
    """Reference from a PCB object to an embedded asset inventory entry."""

    source_object_kind: Literal[
        "component_body",
        "footprint",
        "pcb_component",
        "unknown",
    ]
    source_object_index: int | None
    asset_kind: Literal["model", "font", "opaque"]
    asset_index: int
    asset_id: str | None = None
    role: str | None = None


@public_api
@dataclass(frozen=True)
class EmbeddedOpaqueAssetSummary:
    """Summary for preserved embedded bytes that are not parsed as typed assets."""

    kind: Literal["opaque"]
    index: int
    source_kind: Literal["pcbdoc", "pcblib"]
    source_path: str | None
    stream_name: str
    extraction_filename: str | None
    raw_size: int | None
    payload_available: bool
    payload_sha256: str | None = None
    support_status: Literal["opaque", "unsupported"] = "opaque"
    reason: str | None = None


@public_api
@dataclass(frozen=True)
class EmbeddedPcbModelSummary:
    """Read-only summary for an embedded PCB 3D model payload."""

    kind: Literal["model"]
    index: int
    source_kind: Literal["pcbdoc", "pcblib"]
    source_path: str | None
    name: str
    id: str
    extraction_filename: str
    model_format: Literal[
        "step",
        "parasolid_text",
        "parasolid_binary",
        "solidworks",
        "unknown",
    ]
    is_embedded: bool
    compressed_size: int
    decompressed_size: int | None
    payload_available: bool
    payload_sha256: str | None = None
    altium_checksum: int | None = None
    references: tuple[EmbeddedAssetReference, ...] = ()


@public_api
@dataclass(frozen=True)
class EmbeddedPcbFontSummary:
    """Read-only summary for an embedded PCB TrueType font payload."""

    kind: Literal["font"]
    index: int
    source_kind: Literal["pcbdoc", "pcblib"]
    source_path: str | None
    family_name: str
    style: str
    extraction_filename: str
    compressed_size: int | None
    decompressed_size: int | None
    raw_size: int | None
    payload_available: bool
    payload_sha256: str | None = None
    support_status: Literal["supported", "opaque", "unsupported"] = "supported"


@public_api
@dataclass(frozen=True)
class EmbeddedAssetInventory:
    """Aggregate read-only embedded PCB asset inventory."""

    source_kind: Literal["pcbdoc", "pcblib"]
    source_path: str | None
    models: tuple[EmbeddedPcbModelSummary, ...]
    fonts: tuple[EmbeddedPcbFontSummary, ...]
    opaque_assets: tuple[EmbeddedOpaqueAssetSummary, ...] = ()

    def to_dict(self, *, include_schema: bool = True) -> dict[str, object]:
        """Return the stable downstream JSON-ready inventory shape."""
        data = cast(dict[str, object], _json_ready(asdict(self)))
        if include_schema:
            return {"schema": _EMBEDDED_ASSET_SCHEMA, **data}
        return data


def embedded_asset_schema_id() -> str:
    """Return the current embedded asset inventory JSON schema id."""
    return _EMBEDDED_ASSET_SCHEMA


def source_path_text(path: Path | str | None) -> str | None:
    """Normalize optional document path metadata for inventory summaries."""
    if path is None:
        return None
    return str(path)


def decompress_zlib_payload(payload: bytes, *, label: str) -> bytes:
    """Decompress a zlib payload or raise a clear asset-specific error."""
    try:
        return zlib.decompress(payload)
    except Exception as exc:
        raise ValueError(
            f"embedded asset payload could not be decompressed: {label}"
        ) from exc


def try_decompress_zlib_payload(payload: bytes) -> bytes | None:
    """Return decompressed zlib bytes, or None when the payload is invalid."""
    try:
        return zlib.decompress(payload)
    except Exception:
        return None


def payload_sha256(payload: bytes, *, include_hashes: bool) -> str | None:
    """Return a SHA-256 digest only when the caller requested hashes."""
    if not include_hashes:
        return None
    return hashlib.sha256(payload).hexdigest()


def model_extraction_filename(index: int, name: str) -> str:
    """Return the filename used by existing embedded-model extraction helpers."""
    filename = sanitize_embedded_asset_name(name, f"model_{index:03d}.bin")
    return f"{index:03d}__{filename}"


def font_extraction_filename(index: int, font: EmbeddedFont) -> str:
    """Return the filename used by existing embedded-font extraction helpers."""
    filename = sanitize_embedded_asset_name(font.filename, f"font_{index:03d}.ttf")
    return f"{index:03d}__{filename}"


def embedded_font_payload(font: EmbeddedFont, *, index: int) -> bytes:
    """Return decompressed font payload bytes or raise a clear error."""
    return decompress_zlib_payload(font.compressed_data, label=f"font[{index}]")


def embedded_model_payload(compressed_payload: bytes, *, index: int) -> bytes:
    """Return decompressed model payload bytes or raise a clear error."""
    return decompress_zlib_payload(compressed_payload, label=f"model[{index}]")


def embedded_model_summary(
    *,
    index: int,
    source_kind: Literal["pcbdoc", "pcblib"],
    source_path: Path | str | None,
    model: object,
    compressed_payload: bytes,
    references: tuple[EmbeddedAssetReference, ...] = (),
    include_hashes: bool = False,
) -> EmbeddedPcbModelSummary:
    """Build a typed model summary from a model record and compressed stream.

    Summaries intentionally inflate payloads to report `payload_available` and
    `decompressed_size`. `include_hashes` only controls digest computation.
    """
    name = str(getattr(model, "name", "") or "")
    decompressed_payload = try_decompress_zlib_payload(compressed_payload)
    payload_available = decompressed_payload is not None
    checksum = getattr(model, "checksum", None)
    return EmbeddedPcbModelSummary(
        kind="model",
        index=index,
        source_kind=source_kind,
        source_path=source_path_text(source_path),
        name=name,
        id=str(getattr(model, "id", "") or ""),
        extraction_filename=model_extraction_filename(index, name),
        model_format=classify_embedded_model_format(name),  # type: ignore[arg-type]
        is_embedded=bool(getattr(model, "is_embedded", True)),
        compressed_size=len(compressed_payload),
        decompressed_size=(
            len(decompressed_payload) if decompressed_payload is not None else None
        ),
        payload_available=payload_available,
        payload_sha256=payload_sha256(
            decompressed_payload or b"", include_hashes=include_hashes
        )
        if payload_available
        else None,
        altium_checksum=(int(checksum) & 0xFFFFFFFF) if checksum is not None else None,
        references=references,
    )


def embedded_font_summary(
    *,
    index: int,
    source_kind: Literal["pcbdoc", "pcblib"],
    source_path: Path | str | None,
    font: EmbeddedFont,
    include_hashes: bool = False,
) -> EmbeddedPcbFontSummary:
    """Build a typed font summary from an embedded font record.

    Summaries intentionally inflate payloads to report `payload_available` and
    `decompressed_size`. `include_hashes` only controls digest computation.
    """
    decompressed_payload = try_decompress_zlib_payload(font.compressed_data)
    payload_available = decompressed_payload is not None
    return EmbeddedPcbFontSummary(
        kind="font",
        index=index,
        source_kind=source_kind,
        source_path=source_path_text(source_path),
        family_name=font.name,
        style=font.style,
        extraction_filename=font_extraction_filename(index, font),
        compressed_size=len(font.compressed_data),
        decompressed_size=(
            len(decompressed_payload) if decompressed_payload is not None else None
        ),
        raw_size=len(font.raw_data),
        payload_available=payload_available,
        payload_sha256=payload_sha256(
            decompressed_payload or b"", include_hashes=include_hashes
        )
        if payload_available
        else None,
        support_status="supported",
    )


def opaque_pcblib_embedded_fonts_summary(
    *,
    raw_embedded_fonts: bytes | None,
    source_path: Path | str | None,
    include_hashes: bool = False,
) -> tuple[EmbeddedOpaqueAssetSummary, ...]:
    """Represent the PcbLib raw embedded-font stream without implying parsing support."""
    if not _has_nonempty_pcblib_embedded_font_stream(raw_embedded_fonts):
        return ()
    payload = raw_embedded_fonts or b""
    return (
        EmbeddedOpaqueAssetSummary(
            kind="opaque",
            index=0,
            source_kind="pcblib",
            source_path=source_path_text(source_path),
            stream_name="Library/EmbeddedFonts",
            extraction_filename=None,
            raw_size=len(payload),
            payload_available=True,
            payload_sha256=payload_sha256(payload, include_hashes=include_hashes),
            support_status="opaque",
            reason=(
                "PcbLib Library/EmbeddedFonts is preserved as raw bytes; typed font "
                "parsing is not enabled until the stream shape is proven compatible."
            ),
        ),
    )


def _has_nonempty_pcblib_embedded_font_stream(raw_embedded_fonts: bytes | None) -> bool:
    if not raw_embedded_fonts:
        return False
    if len(raw_embedded_fonts) <= 4:
        try:
            count = int.from_bytes(raw_embedded_fonts[:4].ljust(4, b"\x00"), "little")
        except Exception:
            count = 0
        return count != 0
    if raw_embedded_fonts[:4] == b"\x00\x00\x00\x00":
        return any(byte != 0 for byte in raw_embedded_fonts[4:])
    return True


def live_embedded_model_entries_from_builder(
    builder: object | None,
) -> tuple[tuple[object, bytes], ...]:
    """Return embedded model entries retained by a live authoring builder."""
    specs = getattr(builder, "_embedded_models", None)
    if not specs:
        return ()
    entries: list[tuple[object, bytes]] = []
    for spec in specs:
        model = getattr(spec, "model", None)
        payload = getattr(spec, "embedded_payload", None)
        if model is None or payload is None:
            continue
        model_copy = copy.deepcopy(model)
        try:
            model_copy.embedded_data = zlib.decompress(bytes(payload))
        except zlib.error:
            model_copy.embedded_data = bytes(payload)
        entries.append((model_copy, bytes(payload)))
    return tuple(entries)


__all__ = [
    "EmbeddedAssetInventory",
    "EmbeddedAssetReference",
    "EmbeddedOpaqueAssetSummary",
    "EmbeddedPcbFontSummary",
    "EmbeddedPcbModelSummary",
    "embedded_asset_schema_id",
]
