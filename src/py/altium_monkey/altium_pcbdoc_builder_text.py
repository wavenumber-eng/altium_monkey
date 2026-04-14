"""
Text/WideStrings helpers for `PcbDocBuilder`.

`Texts6/Data` depends on `WideStrings6/Data`, so builder-owned PCB text
authoring needs a small typed helper layer rather than raw stream assembly.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

from .altium_pcb_enums import (
    PcbBarcodeKind,
    PcbBarcodeRenderMode,
    PcbTextJustification,
    PcbTextKind,
)
from .altium_record_pcb__text import AltiumPcbText
from .altium_record_types import PcbLayer
from .altium_resolved_layer_stack import legacy_layer_to_v7_save_id
from .altium_utilities import parse_widestrings6

# The 93-byte barcode block is reused for ordinary text records too. These
# defaults are the stable builder values for simple authored PCB text records.
PCB_TEXT_BARCODE_FULL_WIDTH_MILS = 1050.0
PCB_TEXT_BARCODE_FULL_HEIGHT_MILS = 210.0
PCB_TEXT_BARCODE_MARGIN_MILS = 20.0
PCB_TEXT_BARCODE_MIN_WIDTH_MILS = 0.0

_TT_RENDERER = None

PcbTextKindInput = Literal["stroke", "truetype", "barcode"] | PcbTextKind


def _normalize_text_kind(font_kind: PcbTextKindInput) -> PcbTextKind:
    if isinstance(font_kind, PcbTextKind):
        return font_kind
    try:
        return PcbTextKind(str(font_kind).strip().lower())
    except ValueError as exc:
        raise ValueError("font_kind must be one of: stroke, truetype, barcode") from exc


@dataclass(frozen=True)
class PcbDocWideStringEntry:
    index: int
    value: str


@dataclass(frozen=True)
class PcbDocWideStringsData:
    entries: tuple[PcbDocWideStringEntry, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbDocWideStringsData":
        mapping = parse_widestrings6_stream(data)
        return cls.from_mapping(mapping)

    @classmethod
    def from_mapping(cls, table: Mapping[int, str]) -> "PcbDocWideStringsData":
        return cls(
            entries=tuple(
                PcbDocWideStringEntry(index=int(index), value=value)
                for index, value in sorted(table.items())
            )
        )

    @classmethod
    def empty(cls) -> "PcbDocWideStringsData":
        return cls(entries=())

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def get_value(self, index: int) -> str | None:
        return next(
            (entry.value for entry in self.entries if entry.index == index), None
        )

    def to_mapping(self) -> dict[int, str]:
        return {entry.index: entry.value for entry in self.entries}

    def with_value(self, index: int, value: str) -> "PcbDocWideStringsData":
        table = self.to_mapping()
        table[int(index)] = value
        return PcbDocWideStringsData.from_mapping(table)

    def build_header(self) -> bytes:
        return struct.pack("<I", self.entry_count)

    def build_stream(self) -> bytes:
        return build_widestrings6_stream(self.to_mapping())


@dataclass(frozen=True)
class PcbDocTexts6Data:
    texts: tuple[AltiumPcbText, ...]

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        widestrings_table: Mapping[int, str] | None = None,
    ) -> "PcbDocTexts6Data":
        return cls(texts=parse_text_stream(data, widestrings_table))

    @classmethod
    def from_texts(cls, texts: Sequence[AltiumPcbText]) -> "PcbDocTexts6Data":
        return cls(texts=tuple(texts))

    @classmethod
    def empty(cls) -> "PcbDocTexts6Data":
        return cls(texts=())

    @property
    def record_count(self) -> int:
        return len(self.texts)

    def build_header(self) -> bytes:
        return struct.pack("<I", self.record_count)

    def build_stream(self) -> bytes:
        return build_text_stream(self.texts)


def parse_widestrings6_stream(data: bytes) -> dict[int, str]:
    """
    Parse raw `WideStrings6/Data` bytes into an index->text mapping.
    """

    # Reuse the production parser by feeding it through a tiny temporary OLE-free
    # shim object that exposes the same `exists/openstream` surface.
    class _WideStringsStreamAdapter:
        def __init__(self, payload: bytes) -> None:
            self.payload = bytes(payload)

        def exists(self, entry: Iterable[str]) -> bool:
            return list(entry) == ["WideStrings6", "Data"]

        def openstream(self, entry: Iterable[str]) -> bytes:
            if not self.exists(entry):
                raise KeyError(entry)
            return self.payload

    return dict(parse_widestrings6(_WideStringsStreamAdapter(data)))


def build_widestrings6_stream(table: Mapping[int, str]) -> bytes:
    """
    Serialize a WideStrings index table back into `WideStrings6/Data` bytes.
    """
    out = bytearray()
    for index in sorted(int(key) for key in table.keys()):
        value = table[index]
        out.extend(struct.pack("<I", index))
        if value == "":
            out.extend(struct.pack("<I", 2))
            continue
        payload = value.encode("utf-16le", errors="replace") + b"\x00\x00"
        out.extend(struct.pack("<I", len(payload)))
        out.extend(payload)
    return bytes(out)


def parse_text_stream(
    data: bytes,
    widestrings_table: Mapping[int, str] | None = None,
) -> tuple[AltiumPcbText, ...]:
    """
    Parse `Texts6/Data` into TEXT objects, resolving WideStrings content.
    """
    table = dict(widestrings_table or {})
    texts: list[AltiumPcbText] = []
    offset = 0
    while offset < len(data):
        text = AltiumPcbText()
        consumed = text.parse_from_binary(data, offset)
        text.resolve_text_content(table)
        text._raw_binary_signature = text._state_signature()
        texts.append(text)
        offset += consumed
    if offset != len(data):
        raise ValueError("Unexpected trailing bytes in Texts6/Data")
    return tuple(texts)


def build_text_stream(texts: Sequence[AltiumPcbText]) -> bytes:
    """
    Serialize TEXT objects back into `Texts6/Data`.
    """
    return b"".join(text.serialize_to_binary() for text in texts)


def allocate_missing_widestring_indices(
    texts: Sequence[AltiumPcbText],
    table: dict[int, str],
) -> None:
    """
    Assign deterministic WideStrings indices for any authored text records.
    """
    next_index = max(table.keys(), default=0) + 1
    for text in texts:
        if text.widestring_index is not None:
            continue
        while next_index in table:
            next_index += 1
        text.widestring_index = next_index
        next_index += 1


def build_authored_text(
    *,
    text: str,
    position_mils: tuple[float, float],
    height_mils: float,
    layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
    rotation_degrees: float = 0.0,
    stroke_width_mils: float = 10.0,
    font_kind: PcbTextKindInput = PcbTextKind.STROKE,
    font_name: str = "Arial",
    bold: bool = False,
    italic: bool = False,
    is_comment: bool = False,
    is_designator: bool = False,
    is_mirrored: bool = False,
    is_inverted: bool = False,
    inverted_margin_mils: float = 0.0,
    use_inverted_rectangle: bool = False,
    inverted_rectangle_size_mils: tuple[float, float] | None = None,
    is_frame: bool = False,
    frame_size_mils: tuple[float, float] | None = None,
    text_justification: int | PcbTextJustification | None = None,
    barcode_kind: int | PcbBarcodeKind = PcbBarcodeKind.CODE_39,
    barcode_render_mode: int
    | PcbBarcodeRenderMode = PcbBarcodeRenderMode.BY_FULL_WIDTH,
    barcode_full_size_mils: tuple[float, float] | None = None,
    barcode_margin_mils: tuple[float, float] = (
        PCB_TEXT_BARCODE_MARGIN_MILS,
        PCB_TEXT_BARCODE_MARGIN_MILS,
    ),
    barcode_min_width_mils: float = PCB_TEXT_BARCODE_MIN_WIDTH_MILS,
    barcode_show_text: bool = True,
    barcode_inverted: bool = True,
) -> AltiumPcbText:
    """
    Create a modern authored TEXT record from first principles.

    Builder-authored text follows the same ordinary PCB text defaults used by
    parsed boards. Supported variants are:
    - stroke-font flavor (`font_type=0`, `stroke_font_type=1`)
    - TrueType flavor (`font_type=1`, `stroke_font_type=1`)
    """
    text_kind = _normalize_text_kind(font_kind)
    layer_id = int(layer)
    text_record = AltiumPcbText()
    text_record.layer = layer_id
    text_record.net_index = None
    text_record.component_index = None
    text_record.polygon_index = 0xFFFF
    text_record.union_index = 0xFFFFFFFF
    text_record.text_union_index = 0
    text_record.user_routed = True
    text_record.is_locked = False
    text_record.is_keepout = False
    text_record.x = text_record._to_internal_units(position_mils[0])
    text_record.y = text_record._to_internal_units(position_mils[1])
    text_record.height = text_record._to_internal_units(height_mils)
    text_record.rotation = float(rotation_degrees)
    text_record.stroke_width = text_record._to_internal_units(stroke_width_mils)
    text_record.width = text_record.stroke_width
    text_record.is_mirrored = bool(is_mirrored)
    text_record.is_comment = bool(is_comment)
    text_record.is_designator = bool(is_designator)
    text_record.text_content = text
    text_record._subrecord2_pascal = True
    text_record.font_name = font_name
    text_record.barcode_font_name = font_name
    barcode_full_width_mils, barcode_full_height_mils = (
        barcode_full_size_mils
        if barcode_full_size_mils is not None
        else (PCB_TEXT_BARCODE_FULL_WIDTH_MILS, PCB_TEXT_BARCODE_FULL_HEIGHT_MILS)
    )
    if text_kind == PcbTextKind.BARCODE:
        barcode_full_width_mils, barcode_min_width_mils = _resolve_barcode_widths_mils(
            text=text,
            barcode_kind=int(barcode_kind),
            barcode_render_mode=int(barcode_render_mode),
            barcode_full_width_mils=float(barcode_full_width_mils),
            barcode_min_width_mils=float(barcode_min_width_mils),
        )
    barcode_x_margin_mils, barcode_y_margin_mils = barcode_margin_mils
    text_record.barcode_full_width = text_record._to_internal_units(
        barcode_full_width_mils
    )
    text_record.barcode_full_height = text_record._to_internal_units(
        barcode_full_height_mils
    )
    text_record.barcode_x_margin = text_record._to_internal_units(barcode_x_margin_mils)
    text_record.barcode_y_margin = text_record._to_internal_units(barcode_y_margin_mils)
    text_record.barcode_min_width = text_record._to_internal_units(
        barcode_min_width_mils
    )
    text_record.barcode_kind = int(barcode_kind)
    text_record.barcode_render_mode = int(barcode_render_mode)
    text_record.barcode_inverted = bool(barcode_inverted)
    text_record.barcode_show_text = bool(barcode_show_text)
    text_record.textbox_rect_justification = 3
    text_record.is_justification_valid = False
    text_record.advance_snapping = False
    text_record.snap_point_x = text_record.x
    text_record.snap_point_y = text_record.y
    text_record.barcode_layer_v7 = legacy_layer_to_v7_save_id(layer_id)
    text_record._original_sr1_len = 252
    text_record.is_inverted = bool(is_inverted)
    text_record.margin_border_width = text_record._to_internal_units(
        inverted_margin_mils
    )
    text_record.use_inverted_rectangle = bool(use_inverted_rectangle)
    if inverted_rectangle_size_mils is not None:
        rect_width_mils, rect_height_mils = inverted_rectangle_size_mils
        text_record.textbox_rect_width = text_record._to_internal_units(rect_width_mils)
        text_record.textbox_rect_height = text_record._to_internal_units(
            rect_height_mils
        )
    text_record.is_frame = bool(is_frame)
    if frame_size_mils is not None:
        frame_width_mils, frame_height_mils = frame_size_mils
        if frame_width_mils <= 0.0 or frame_height_mils <= 0.0:
            raise ValueError("frame_size_mils must contain positive width and height")
        text_record.textbox_rect_width = text_record._to_internal_units(
            frame_width_mils
        )
        text_record.textbox_rect_height = text_record._to_internal_units(
            frame_height_mils
        )
    elif text_record.is_frame:
        raise ValueError("frame_size_mils is required when is_frame is True")
    if text_justification is not None:
        text_record.textbox_rect_justification = int(text_justification)
        text_record.is_justification_valid = True

    if text_kind == PcbTextKind.TRUETYPE:
        text_record.font_type = 1
        text_record._font_type_offset43 = 1
        text_record.stroke_font_type = 1
        text_record.is_bold = bool(bold)
        text_record.is_italic = bool(italic)
        textbox_width_mils, textbox_height_mils = _measure_truetype_textbox_mils(
            text=text,
            font_name=font_name,
            height_mils=height_mils,
            bold=bool(bold),
            italic=bool(italic),
        )
        if inverted_rectangle_size_mils is None and frame_size_mils is None:
            text_record.textbox_rect_width = text_record._to_internal_units(
                textbox_width_mils
            )
            text_record.textbox_rect_height = text_record._to_internal_units(
                textbox_height_mils
            )
    elif text_kind == PcbTextKind.BARCODE:
        text_record.font_type = 2
        # Native saves keep the legacy font-type byte as stroke text. The
        # actual barcode discriminator is stored in the barcode block's
        # TextKind byte.
        text_record._font_type_offset43 = 0
        text_record.stroke_font_type = 1
        text_record.is_bold = bool(bold)
        text_record.is_italic = bool(italic)
        text_record.textbox_rect_width = text_record.barcode_full_width
        text_record.textbox_rect_height = text_record.barcode_full_height
    else:
        text_record.font_type = 0
        text_record._font_type_offset43 = 0
        text_record.stroke_font_type = 1
        if text_justification is None:
            text_record.textbox_rect_justification = 5
        text_record.is_bold = False
        text_record.is_italic = False
    return text_record


def _resolve_barcode_widths_mils(
    *,
    text: str,
    barcode_kind: int,
    barcode_render_mode: int,
    barcode_full_width_mils: float,
    barcode_min_width_mils: float,
) -> tuple[float, float]:
    module_count = _barcode_module_count(text=text, barcode_kind=barcode_kind)
    if module_count <= 0:
        return barcode_full_width_mils, barcode_min_width_mils

    if barcode_render_mode == int(PcbBarcodeRenderMode.BY_MIN_WIDTH):
        if barcode_min_width_mils > 0.0:
            barcode_full_width_mils = barcode_min_width_mils * module_count
    elif barcode_min_width_mils <= 0.0:
        barcode_min_width_mils = (
            round(barcode_full_width_mils * 10000.0 / module_count) / 10000.0
        )
    return barcode_full_width_mils, barcode_min_width_mils


def _barcode_module_count(*, text: str, barcode_kind: int) -> int:
    if barcode_kind == int(PcbBarcodeKind.CODE_39):
        return max(1, 13 * (len(text) + 2) - 1)
    if barcode_kind == int(PcbBarcodeKind.CODE_128):
        data_codewords = (
            len(text) // 2 if text.isdigit() and len(text) % 2 == 0 else len(text)
        )
        return max(1, (data_codewords + 2) * 11 + 13)
    return max(1, len(text))


def _get_truetype_renderer() -> object:
    global _TT_RENDERER
    if _TT_RENDERER is None:
        from .altium_text_to_polygon import TrueTypeTextRenderer

        _TT_RENDERER = TrueTypeTextRenderer()
    return _TT_RENDERER


def _measure_truetype_textbox_mils(
    *,
    text: str,
    font_name: str,
    height_mils: float,
    bold: bool,
    italic: bool,
) -> tuple[float, float]:
    """
    Measure the auto-sized TT text box from the existing polygon renderer.

    This keeps builder-authored TrueType text aligned with the existing text
    polygon and metrics pipeline without adding builder-specific heuristics.
    """
    if not text:
        return 0.0, 0.0
    renderer = _get_truetype_renderer()
    result = renderer.render(
        text=text,
        font_name=font_name,
        height_mm=height_mils * 0.0254,
        is_bold=bold,
        is_italic=italic,
    )
    xs: list[float] = []
    ys: list[float] = []
    for glyph in result.characters:
        for polygon in glyph:
            xs.extend(x for x, _ in polygon.outline)
            ys.extend(y for _, y in polygon.outline)
            for hole in polygon.holes:
                xs.extend(x for x, _ in hole)
                ys.extend(y for _, y in hole)
    if not xs or not ys:
        return 0.0, 0.0
    return ((max(xs) - min(xs)) / 0.0254, (max(ys) - min(ys)) / 0.0254)


def make_authored_text_guid(text: AltiumPcbText, ordinal: int) -> uuid.UUID:
    """
    Generate a deterministic GUID for a builder-authored TEXT.
    """
    seed = (
        "pcbdoc-builder-text|"
        f"{ordinal}|{text.layer}|{text.x}|{text.y}|{text.height}|"
        f"{text.rotation}|{text.text_content}|{text.is_designator}|{text.is_comment}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, seed)
