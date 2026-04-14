"""Utilities for extracting embedded PCB fonts and 3D model payloads."""

import logging
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def classify_embedded_model_format(filename: str) -> str:
    """
    Classify embedded 3D model format by file extension (case-insensitive).

    Supported classes:
    - step: .step / .stp
    - solidworks: .sldprt / .sldasm
    - parasolid_text: .x_t
    - parasolid_binary: .x_b
    - unknown: everything else
    """
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix in {".step", ".stp"}:
        return "step"
    if suffix in {".sldprt", ".sldasm"}:
        return "solidworks"
    if suffix == ".x_t":
        return "parasolid_text"
    if suffix == ".x_b":
        return "parasolid_binary"
    return "unknown"


def sanitize_embedded_asset_name(name: str, fallback: str) -> str:
    """
    Sanitize embedded asset names for stable filesystem extraction.
    """
    text = str(name or "").strip()
    if not text:
        text = fallback
    text = text.replace("\x00", "").strip()
    text = re.sub(r'[<>:"/\\|?*]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def _parse_altium_bool(value: object, default: bool = True) -> bool:
    """
    Parse Altium boolean-like values (TRUE/FALSE, 1/0, ON/OFF).
    """
    if value is None:
        return default
    text = str(value).strip().upper()
    if not text:
        return default
    return text not in {"FALSE", "0", "NO", "OFF"}


@dataclass
class EmbeddedFont:
    """
    Embedded TrueType font file.

    Attributes:
        name: Font family name (e.g., "Bunny")
        style: Font style (e.g., "Regular", "Bold")
        filename: Reconstructed filename (e.g., "Bunny-Regular.ttf")
        compressed_data: Zlib-compressed TTF file data
        raw_data: Complete raw binary data
    """

    name: str
    style: str
    compressed_data: bytes
    raw_data: bytes

    @property
    def filename(self) -> str:
        """
        Get reconstructed filename.
        """
        if self.style and self.style.lower() != "regular":
            if self.style.lower() in self.name.lower():
                return f"{self.name}.ttf"
            return f"{self.name}-{self.style}.ttf"
        return f"{self.name}.ttf"

    def decompress(self) -> bytes:
        """
        Decompress the TTF file data.
        """
        try:
            return zlib.decompress(self.compressed_data)
        except Exception as e:
            log.error(f"Failed to decompress font data: {e}")
            return b""

    def save_to_file(self, output_path: Path) -> None:
        """
        Save decompressed TTF to file.
        """
        output_path = Path(output_path)
        ttf_data = self.decompress()
        if ttf_data:
            output_path.write_bytes(ttf_data)
            log.info(f"Saved font to {output_path}")
        else:
            log.error("Failed to save font - no data")


@dataclass
class EmbeddedModel:
    """
    Embedded 3D model file.

    Attributes:
        index: Model index (0, 1, 2, ...)
        name: Model filename from Models/Data properties (if available)
        id: Model ID/GUID from Models/Data
        compressed_data: Zlib-compressed model data
        is_embedded: True if model data is embedded
        model_format: Classified format from filename extension
    """

    index: int
    name: str
    id: str
    compressed_data: bytes
    is_embedded: bool = True
    model_format: str = "unknown"

    @property
    def filename(self) -> str:
        """
        Get filename (from name or generated).
        """
        if self.name:
            return self.name
        ext_by_format = {
            "step": "step",
            "solidworks": "sldprt",
            "parasolid_text": "x_t",
            "parasolid_binary": "x_b",
            "unknown": "bin",
        }
        ext = ext_by_format.get(self.model_format, "bin")
        return f"model_{self.index}.{ext}"

    @property
    def is_step(self) -> bool:
        """
        True when classified as STEP by extension.
        """
        return self.model_format == "step"

    def decompress(self) -> bytes:
        """
        Decompress embedded model data.
        """
        try:
            return zlib.decompress(self.compressed_data)
        except Exception as e:
            log.error(f"Failed to decompress model data: {e}")
            return b""

    def save_to_file(self, output_path: Path) -> None:
        """
        Save decompressed model data to file.
        """
        output_path = Path(output_path)
        model_data = self.decompress()
        if model_data:
            output_path.write_bytes(model_data)
            log.info(f"Saved model to {output_path}")
        else:
            log.error("Failed to save model - no data")


def parse_embedded_fonts(data: bytes) -> list[EmbeddedFont]:
    """
    Parse embedded font records from an ``EmbeddedFonts6/Data`` stream.
    """

    def _read_utf16_field(buf: bytes, pos: int) -> tuple[str | None, int]:
        """
        Read a [u32 length][utf16le bytes] field.
        """
        if pos + 4 > len(buf):
            return None, pos
        field_len = struct.unpack_from("<I", buf, pos)[0]
        pos += 4
        if field_len > 4096 or pos + field_len > len(buf):
            return None, pos
        raw = buf[pos : pos + field_len]
        pos += field_len
        text = raw.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
        return text, pos

    def _infer_style(name1: str, name2: str, style: str) -> str:
        """
        Normalize style text, inferring from names when absent.
        """
        if style:
            return style
        lower = f"{name1} {name2}".lower()
        has_bold = "bold" in lower
        has_italic = "italic" in lower or "oblique" in lower
        if has_bold and has_italic:
            return "Bold Italic"
        if has_bold:
            return "Bold"
        if has_italic:
            return "Italic"
        return ""

    fonts: list[EmbeddedFont] = []
    offset = 0
    seen_streams: set[tuple[str, str, int]] = set()

    while offset < len(data) - 16:
        record_start = offset
        try:
            name1, offset = _read_utf16_field(data, offset)
            if not name1:
                break
            name2, offset = _read_utf16_field(data, offset)
            if name2 is None:
                break
            style, offset = _read_utf16_field(data, offset)
            if style is None:
                break

            style = _infer_style(name1, name2, style)

            # Zlib header can be offset by a few metadata bytes.
            zlib_start = -1
            search_end = min(offset + 256, len(data) - 1)
            for i in range(offset, search_end):
                if data[i] == 0x78 and data[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
                    zlib_start = i
                    break

            if zlib_start < 0:
                log.debug(
                    "Embedded font parse stopped: zlib header not found at %d", offset
                )
                break

            # Decompress just one zlib stream to find exact record boundary.
            decompress = zlib.decompressobj()
            _ = decompress.decompress(data[zlib_start:])
            consumed = len(data[zlib_start:]) - len(decompress.unused_data)
            if consumed <= 0:
                log.debug(
                    "Embedded font parse stopped: invalid zlib stream at %d", zlib_start
                )
                break

            compressed = data[zlib_start : zlib_start + consumed]
            record_end = zlib_start + consumed
            name = name1 or name2 or "EmbeddedFont"

            stream_sig = (name, style, len(compressed))
            if stream_sig not in seen_streams:
                seen_streams.add(stream_sig)
                font = EmbeddedFont(
                    name=name,
                    style=style,
                    compressed_data=compressed,
                    raw_data=data[record_start:record_end],
                )
                fonts.append(font)
                log.info("Found embedded font: %s", font.filename)

            offset = record_end

        except Exception as e:
            log.warning(f"Error parsing embedded font at offset {record_start}: {e}")
            break

    return fonts


def extract_embedded_models(
    ole: Any,
    models_data_properties: list[dict[str, object]],
) -> list[EmbeddedModel]:
    """
    Extract embedded 3D models from Models/0, Models/1, etc.

    Args:
        ole: AltiumOleFile instance
        models_data_properties: List of parsed MODEL property dicts from Models/Data

    Returns:
        List of EmbeddedModel objects
    """
    models = []

    for idx, props in enumerate(models_data_properties):
        try:
            # Try to read Models/{idx} stream
            model_data = ole.openstream(["Models", str(idx)])
            model_name = str(props.get("NAME", "") or "")
            model_format = classify_embedded_model_format(model_name)

            model = EmbeddedModel(
                index=idx,
                name=model_name,
                id=props.get("ID", ""),
                compressed_data=model_data,
                is_embedded=_parse_altium_bool(props.get("EMBED", True), default=True),
                model_format=model_format,
            )
            models.append(model)

            log.info(f"Found embedded model: {model.filename} ({model.model_format})")

        except Exception as e:
            log.debug(f"No embedded model at index {idx}: {e}")

    return models
