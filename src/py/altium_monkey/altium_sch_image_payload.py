"""Helpers for schematic embedded image payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

MAX_DECODED_BMP_RGBA_BYTES = 256 * 1024 * 1024
MAX_EMBEDDED_SVG_MARKUP_DELIMITERS = 65_536
MAX_EMBEDDED_SVG_FIELD_MARKERS = 65_536


class SchEmbeddedImageFormat(str, Enum):
    BMP = "BMP"
    PNG = "PNG"
    JPEG = "JPEG"
    GIF = "GIF"
    SVG = "SVG"
    WEBP = "WEBP"
    EMF = "EMF"
    WMF = "WMF"


ALTIUM_IMAGE_CLASS_FORMATS: dict[str, SchEmbeddedImageFormat] = {
    "TBitmap": SchEmbeddedImageFormat.BMP,
    "TdxPNGImage": SchEmbeddedImageFormat.PNG,
    "TdxPNGRotatableImage": SchEmbeddedImageFormat.PNG,
    "TJPEGImage": SchEmbeddedImageFormat.JPEG,
    "TGifImage": SchEmbeddedImageFormat.GIF,
    "TSVGImage": SchEmbeddedImageFormat.SVG,
    "TMetafile": SchEmbeddedImageFormat.EMF,
}


@dataclass(frozen=True)
class SchBmpInfo:
    file_size: int
    pixel_offset: int
    dib_header_size: int
    width: int
    height: int
    planes: int
    bits_per_pixel: int
    compression: int
    image_size: int

    @property
    def size_px(self) -> tuple[int, int]:
        return (abs(self.width), abs(self.height))


@dataclass(frozen=True)
class SchEmbeddedImagePayload:
    raw_data: bytes
    raw_format: SchEmbeddedImageFormat | None
    preview_data: bytes | None = None
    native_class: str | None = None
    native_data: bytes | None = None
    native_format: SchEmbeddedImageFormat | None = None

    @property
    def is_altium_wrapper(self) -> bool:
        return self.preview_data is not None and self.native_data is not None

    @property
    def preferred_data(self) -> bytes:
        return self.native_data if self.native_data else self.raw_data

    @property
    def preferred_format(self) -> SchEmbeddedImageFormat | None:
        return self.native_format if self.native_format else self.raw_format

    @property
    def preferred_size_px(self) -> tuple[int, int] | None:
        return image_size_px_from_data(self.preferred_data)

    @property
    def altium_source_size_px(self) -> tuple[int, int] | None:
        """
        Return the source size Altium reports in GeometryMaker image ops.
        """
        if (
            self.native_format == SchEmbeddedImageFormat.SVG
            and self.preview_data is not None
        ):
            preview = parse_bmp_info(self.preview_data)
            if preview is not None:
                return preview.size_px
        return self.preferred_size_px


class SchEmbeddedImageLinkError(ValueError):
    """An embedded IMAGE record cannot be linked or stored unambiguously."""


class SchEmbeddedImagePayloadError(ValueError):
    """A payload has recognizable wrapper framing but invalid metadata."""


@dataclass(frozen=True)
class SchEmbeddedImageEntry:
    """Typed input for deterministic IMAGE storage synchronization."""

    filename: str
    orientation: int
    data: bytes


def detect_image_format(data: bytes) -> SchEmbeddedImageFormat | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return SchEmbeddedImageFormat.PNG
    if data.startswith(b"BM"):
        return SchEmbeddedImageFormat.BMP
    if data.startswith(b"\xff\xd8\xff"):
        return SchEmbeddedImageFormat.JPEG
    if data.startswith((b"GIF87a", b"GIF89a")):
        return SchEmbeddedImageFormat.GIF
    if data.startswith(b"\x01\x00\x00\x00"):
        return SchEmbeddedImageFormat.EMF
    if data.startswith(b"\xd7\xcd\xc6\x9a"):
        return SchEmbeddedImageFormat.WMF
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return SchEmbeddedImageFormat.WEBP
    head = data[:512].lstrip()
    if head.startswith((b"<svg", b"<?xml")) and b"<svg" in head[:256]:
        return SchEmbeddedImageFormat.SVG
    return None


def parse_bmp_info(data: bytes) -> SchBmpInfo | None:
    if len(data) < 54 or not data.startswith(b"BM"):
        return None
    file_size = int.from_bytes(data[2:6], "little", signed=False)
    pixel_offset = int.from_bytes(data[10:14], "little", signed=False)
    dib_header_size = int.from_bytes(data[14:18], "little", signed=False)
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    planes = int.from_bytes(data[26:28], "little", signed=False)
    bits_per_pixel = int.from_bytes(data[28:30], "little", signed=False)
    compression = int.from_bytes(data[30:34], "little", signed=False)
    image_size = int.from_bytes(data[34:38], "little", signed=False)
    info = SchBmpInfo(
        file_size=file_size,
        pixel_offset=pixel_offset,
        dib_header_size=dib_header_size,
        width=width,
        height=height,
        planes=planes,
        bits_per_pixel=bits_per_pixel,
        compression=compression,
        image_size=image_size,
    )
    return info if _valid_bmp_info(info, len(data)) else None


def image_size_px_from_data(data: bytes) -> tuple[int, int] | None:
    image_format = detect_image_format(data)
    if image_format == SchEmbeddedImageFormat.PNG and len(data) >= 24:
        return (
            int.from_bytes(data[16:20], "big", signed=False),
            int.from_bytes(data[20:24], "big", signed=False),
        )
    if image_format == SchEmbeddedImageFormat.BMP:
        bmp = parse_bmp_info(data)
        return bmp.size_px if bmp else None
    if image_format == SchEmbeddedImageFormat.GIF and len(data) >= 10:
        return (
            int.from_bytes(data[6:8], "little", signed=False),
            int.from_bytes(data[8:10], "little", signed=False),
        )
    if image_format == SchEmbeddedImageFormat.SVG:
        return _svg_size_px(data)
    return _pillow_size_px(data)


def decode_sch_embedded_image_payload(data: bytes) -> SchEmbeddedImagePayload:
    raw_format = detect_image_format(data)
    if raw_format == SchEmbeddedImageFormat.BMP and parse_bmp_info(data) is None:
        raise SchEmbeddedImagePayloadError("invalid embedded BMP metadata")
    wrapper = _decode_altium_wrapper(data)
    if wrapper is None:
        return SchEmbeddedImagePayload(raw_data=data, raw_format=raw_format)
    native_class, native_data, preview_data = wrapper
    native_format = detect_image_format(native_data)
    return SchEmbeddedImagePayload(
        raw_data=data,
        raw_format=raw_format,
        preview_data=preview_data,
        native_class=native_class,
        native_data=native_data,
        native_format=native_format,
    )


def _validate_schdoc_embedded_image_payload(data: bytes) -> None:
    """Validate one SchDoc payload, including its exact preview-only fallback."""
    try:
        decode_sch_embedded_image_payload(data)
    except SchEmbeddedImagePayloadError:
        bmp = parse_bmp_info(data)
        if bmp is not None and data[bmp.file_size :] == b"\x0bTDibGraphic":
            return
        raise


def decode_bmp_rgba(data: bytes) -> tuple[int, int, bytes] | None:
    bmp = parse_bmp_info(data)
    if bmp is None or bmp.bits_per_pixel not in (24, 32) or bmp.compression != 0:
        return None
    width = abs(bmp.width)
    height = abs(bmp.height)
    if width <= 0 or height <= 0:
        return None

    output_size = width * height * 4
    if output_size > MAX_DECODED_BMP_RGBA_BYTES:
        return None

    bytes_per_pixel = bmp.bits_per_pixel // 8
    row_bytes = ((width * 3 + 3) // 4) * 4 if bmp.bits_per_pixel == 24 else width * 4
    if bmp.pixel_offset + (row_bytes * height) > len(data):
        return None

    rgba = bytearray(output_size)
    top_down = bmp.height < 0
    for source_row in range(height):
        dest_row = source_row if top_down else (height - 1 - source_row)
        source_offset = bmp.pixel_offset + source_row * row_bytes
        dest_offset = dest_row * width * 4
        for pixel in range(width):
            source_index = source_offset + pixel * bytes_per_pixel
            dest_index = dest_offset + pixel * 4
            b = data[source_index]
            g = data[source_index + 1]
            r = data[source_index + 2]
            a = data[source_index + 3] if bmp.bits_per_pixel == 32 else 0xFF
            rgba[dest_index : dest_index + 4] = bytes((r, g, b, a))
    return (width, height, bytes(rgba))


def decode_32bit_bmp_rgba(data: bytes) -> tuple[int, int, bytes] | None:
    bmp = parse_bmp_info(data)
    if bmp is None or bmp.bits_per_pixel != 32:
        return None
    return decode_bmp_rgba(data)


def bmp_alpha_extrema(data: bytes) -> tuple[int, int] | None:
    """
    Return alpha-channel extrema for an uncompressed 32-bit BMP.
    """
    bmp = parse_bmp_info(data)
    if bmp is None or bmp.bits_per_pixel != 32 or bmp.compression != 0:
        return None
    width = abs(bmp.width)
    height = abs(bmp.height)
    if width <= 0 or height <= 0:
        return None
    row_bytes = width * 4
    if bmp.pixel_offset + (row_bytes * height) > len(data):
        return None

    alpha_bytes = data[bmp.pixel_offset + 3 : bmp.pixel_offset + row_bytes * height : 4]
    if not alpha_bytes:
        return None
    return (min(alpha_bytes), max(alpha_bytes))


def _decode_altium_wrapper(data: bytes) -> tuple[str, bytes, bytes] | None:
    bmp = parse_bmp_info(data)
    if bmp is None:
        return None
    preview_len = bmp.file_size
    if preview_len <= 0 or preview_len >= len(data):
        return None
    native_class, class_end = _decode_wrapper_class(data, preview_len)
    native_data = data[class_end:]
    if not native_data:
        raise SchEmbeddedImagePayloadError("embedded image native payload is empty")
    if not _native_payload_matches_class(native_class, native_data):
        raise SchEmbeddedImagePayloadError(
            f"native payload does not match embedded image class {native_class!r}"
        )
    return native_class, native_data, data[:preview_len]


def _decode_wrapper_class(data: bytes, preview_len: int) -> tuple[str, int]:
    class_len = data[preview_len]
    class_start = preview_len + 1
    class_end = class_start + class_len
    if class_len == 0 or class_end > len(data):
        raise SchEmbeddedImagePayloadError("invalid embedded image class length")
    try:
        native_class = data[class_start:class_end].decode("utf-8")
    except UnicodeDecodeError:
        raise SchEmbeddedImagePayloadError(
            "embedded image class is not valid UTF-8"
        ) from None
    if native_class not in ALTIUM_IMAGE_CLASS_FORMATS:
        raise SchEmbeddedImagePayloadError(
            f"unknown embedded image class: {native_class!r}"
        )
    return native_class, class_end


def image_storage_key(filename: str, orientation: int) -> str:
    """Return the managed Storage key for an IMAGE filename/orientation pair."""
    if orientation not in range(4):
        raise SchEmbeddedImageLinkError(f"invalid IMAGE orientation: {orientation}")
    if orientation == 0:
        return filename
    return f"FileName={filename}|Orientation={orientation * 90} Degrees"


def build_embedded_image_storage(
    entries: Iterable[SchEmbeddedImageEntry],
) -> dict[str, bytes]:
    """Build deterministic Storage entries without losing conflicting payloads."""
    groups: dict[str, tuple[str, dict[int, bytes]]] = {}
    for entry in entries:
        _add_storage_entry(groups, entry)

    storage: dict[str, bytes] = {}
    for filename, orientations in groups.values():
        if not orientations:
            continue
        base_data = orientations.get(0, next(iter(orientations.values())))
        storage[filename] = base_data
        for orientation in (1, 2, 3):
            data = orientations.get(orientation)
            if data is not None:
                storage[image_storage_key(filename, orientation)] = data
    return storage


def _add_storage_entry(
    groups: dict[str, tuple[str, dict[int, bytes]]],
    entry: SchEmbeddedImageEntry,
) -> None:
    if not entry.filename or not entry.data:
        return
    folded = entry.filename.lower()
    if folded not in groups:
        groups[folded] = (entry.filename, {})
    filename, orientations = groups[folded]
    existing = orientations.get(entry.orientation)
    if existing is not None and existing != entry.data:
        key = image_storage_key(filename, entry.orientation)
        raise SchEmbeddedImageLinkError(
            f"conflicting embedded IMAGE payloads for {key!r}"
        )
    image_storage_key(filename, entry.orientation)
    orientations.setdefault(entry.orientation, entry.data)


def resolve_embedded_image_data(
    storage: Mapping[str, bytes], filename: str, orientation: int
) -> bytes | None:
    """Resolve IMAGE bytes by its case-insensitive managed Storage key."""
    index = _build_storage_index(storage)
    return index.get(image_storage_key(filename, orientation).lower())


def resolve_embedded_image_group(
    storage: Mapping[str, bytes], records: Iterable[tuple[str, int]]
) -> list[bytes | None]:
    """Resolve an IMAGE record sequence with the managed rotated-only fallback."""
    record_list = list(records)
    storage_index = _build_storage_index(storage)
    fallback_orientations: dict[str, int | None] = {}
    grouped_orientations: dict[str, list[int]] = {}
    for filename, orientation in record_list:
        image_storage_key(filename, orientation)
        grouped_orientations.setdefault(filename.lower(), []).append(orientation)
    for filename, orientations in grouped_orientations.items():
        fallback_orientations[filename] = None if 0 in orientations else orientations[0]

    resolved: list[bytes | None] = []
    for filename, orientation in record_list:
        exact = storage_index.get(image_storage_key(filename, orientation).lower())
        fallback_orientation = fallback_orientations[filename.lower()]
        if orientation != fallback_orientation:
            resolved.append(exact)
            continue
        bare = storage_index.get(filename.lower())
        if exact is not None and bare is not None and exact != bare:
            raise SchEmbeddedImageLinkError(
                f"conflicting bare and rotated IMAGE payloads for {filename!r}"
            )
        resolved.append(bare if bare is not None else exact)
    return resolved


def _build_storage_index(storage: Mapping[str, bytes]) -> dict[str, bytes]:
    index: dict[str, bytes] = {}
    for key, data in storage.items():
        folded = key.lower()
        existing = index.get(folded)
        if existing is not None and existing != data:
            raise SchEmbeddedImageLinkError(
                f"ambiguous embedded IMAGE linkage for {key!r}"
            )
        index.setdefault(folded, data)
    return index


def _valid_bmp_compression(bits_per_pixel: int, compression: int) -> bool:
    if compression == 0:
        return True
    return (compression, bits_per_pixel) in {
        (1, 8),
        (2, 4),
        (3, 16),
        (3, 32),
        (4, 24),
        (5, 24),
        (6, 16),
        (6, 32),
    }


def _valid_bmp_info(info: SchBmpInfo, data_len: int) -> bool:
    header_end = 14 + info.dib_header_size
    if not (
        54 <= info.file_size <= data_len
        and info.dib_header_size >= 40
        and header_end <= info.pixel_offset <= info.file_size
    ):
        return False
    if info.width <= 0 or info.height in (0, -(1 << 31)) or info.planes != 1:
        return False
    if info.bits_per_pixel not in {1, 4, 8, 16, 24, 32}:
        return False
    if not _valid_bmp_compression(info.bits_per_pixel, info.compression):
        return False
    return _valid_bmp_pixel_extent(
        file_size=info.file_size,
        pixel_offset=info.pixel_offset,
        width=info.width,
        height=info.height,
        bits_per_pixel=info.bits_per_pixel,
        compression=info.compression,
        image_size=info.image_size,
    )


def _valid_bmp_pixel_extent(
    *,
    file_size: int,
    pixel_offset: int,
    width: int,
    height: int,
    bits_per_pixel: int,
    compression: int,
    image_size: int,
) -> bool:
    if compression == 0:
        row_bytes = ((width * bits_per_pixel + 31) // 32) * 4
        required = row_bytes * abs(height)
    else:
        if image_size == 0:
            return False
        required = image_size
    return required <= file_size - pixel_offset


def _native_payload_matches_class(native_class: str, native_data: bytes) -> bool:
    detected = detect_image_format(native_data)
    expected = ALTIUM_IMAGE_CLASS_FORMATS[native_class]
    if native_class == "TMetafile":
        return _valid_metafile_payload(native_data, detected)
    if detected != expected:
        return False
    if detected == SchEmbeddedImageFormat.BMP:
        return parse_bmp_info(native_data) is not None
    if detected == SchEmbeddedImageFormat.PNG:
        return _valid_png_payload(native_data)
    if detected == SchEmbeddedImageFormat.JPEG:
        return len(native_data) >= 4 and native_data.endswith(b"\xff\xd9")
    if detected == SchEmbeddedImageFormat.GIF:
        return _valid_gif_payload(native_data)
    if detected == SchEmbeddedImageFormat.SVG:
        return _svg_is_well_formed(native_data)
    return False


def _valid_png_payload(data: bytes) -> bool:
    return (
        len(data) >= 33
        and data[8:16] == b"\x00\x00\x00\rIHDR"
        and int.from_bytes(data[16:20], "big") > 0
        and int.from_bytes(data[20:24], "big") > 0
    )


def _valid_gif_payload(data: bytes) -> bool:
    return (
        len(data) >= 10
        and int.from_bytes(data[6:8], "little") > 0
        and int.from_bytes(data[8:10], "little") > 0
    )


def _valid_metafile_payload(
    data: bytes, detected: SchEmbeddedImageFormat | None
) -> bool:
    if detected == SchEmbeddedImageFormat.WMF:
        return len(data) >= 22
    if detected != SchEmbeddedImageFormat.EMF or len(data) < 88:
        return False
    declared_size = int.from_bytes(data[48:52], "little")
    return data[40:44] == b" EMF" and 88 <= declared_size <= len(data)


class _SvgUnsafeDoctype(Exception):
    pass


def _svg_is_well_formed(
    data: bytes,
    delimiter_limit: int = MAX_EMBEDDED_SVG_MARKUP_DELIMITERS,
    field_marker_limit: int = MAX_EMBEDDED_SVG_FIELD_MARKERS,
) -> bool:
    from xml.parsers import expat

    # roxmltree sizes its node and attribute buffers from these delimiters before
    # parsing.  Apply the same conservative portable preflight in both languages.
    if data.count(b"<") > delimiter_limit or data.count(b"=") > field_marker_limit:
        return False
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    root_name: str | None = None

    def start_element(name: str, _attributes: dict[str, str]) -> None:
        nonlocal root_name
        if root_name is None:
            root_name = name.rsplit("}", 1)[-1]

    def reject_doctype(
        _name: str,
        _system_id: str | None,
        _public_id: str | None,
        _has_internal_subset: bool,
    ) -> None:
        raise _SvgUnsafeDoctype

    parser.StartElementHandler = start_element
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.ExternalEntityRefHandler = lambda *_args: 0
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, _SvgUnsafeDoctype):
        return False
    return root_name is not None and root_name.lower() == "svg"


def _pillow_size_px(data: bytes) -> tuple[int, int] | None:
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            return (int(image.width), int(image.height))
    except Exception:
        return None


def _svg_size_px(data: bytes) -> tuple[int, int] | None:
    import re
    from xml.etree import ElementTree

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="ignore")

    def parse_dimension(value: str | None) -> int | None:
        if not value:
            return None
        match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", value)
        if not match:
            return None
        return int(float(match.group(1)))

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        root = None
    if root is None:
        return None
    width = parse_dimension(root.attrib.get("width"))
    height = parse_dimension(root.attrib.get("height"))
    if width is not None and height is not None:
        return (width, height)
    view_box = root.attrib.get("viewBox")
    if not view_box:
        return None
    parts = re.split(r"[,\s]+", view_box.strip())
    if len(parts) != 4:
        return None
    try:
        return (int(float(parts[2])), int(float(parts[3])))
    except ValueError:
        return None
