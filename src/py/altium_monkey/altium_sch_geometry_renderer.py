from __future__ import annotations

import base64
import html
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .altium_sch_geometry_oracle import (
    SchGeometryDocument,
    SchGeometryOp,
    SchGeometryOpKind,
    SchGeometryRecord,
)
from .altium_sch_harness_patterns import (
    HarnessCoveringPatternResource,
    harness_covering_pattern_by_id,
)
from .altium_font_resolver import (
    FontResolution,
    FontResolutionSource,
    canonical_package_bundled_font_path,
    resolve_font_with_style,
)
from .altium_sch_svg_renderer import (
    SchCompileMaskRenderMode,
    SchSvgRenderContext,
    SchSvgRenderOptions,
    _format_svg_number as _fmt_native_text_coord,
    build_compile_mask_visual_overlay_svg,
    svg_arc,
    svg_ellipse,
    svg_text_or_poly,
)

FontDiagnosticPayload = dict[str, object]
type _BundledFontFaceKey = tuple[str, bool, bool, str]
type _SvgRenderChunk = str | list[_SvgRenderChunk]


@dataclass(frozen=True, slots=True)
class _PortablePatternState:
    resource: HarnessCoveringPatternResource
    origin_x: float
    origin_y: float
    tile_width: float
    tile_height: float
    screen_degrees: float
    opacity: float
    color: str


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _portable_pattern_numbers(
    value: dict[object, object],
) -> tuple[float, float, float, float, float, float] | None:
    numbers: list[float] = []
    for name in (
        "origin_x",
        "origin_y",
        "tile_width",
        "tile_height",
        "slope_radians",
        "opacity",
    ):
        number = _finite_float(value.get(name))
        if number is None:
            return None
        numbers.append(number)
    return (
        numbers[0],
        numbers[1],
        numbers[2],
        numbers[3],
        numbers[4],
        numbers[5],
    )


def _portable_pattern_resource(
    value: object,
) -> tuple[dict[object, object], HarnessCoveringPatternResource] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schema") != "altium.harness_covering_pattern.v1":
        return None
    resource_id = value.get("resource_id")
    if not isinstance(resource_id, str):
        return None
    resource = harness_covering_pattern_by_id(resource_id)
    return None if resource is None else (value, resource)


def _portable_pattern_state(
    value: object,
    *,
    units_per_px: float,
) -> _PortablePatternState | None:
    if not math.isfinite(units_per_px):
        return None
    if units_per_px <= 0.0:
        return None
    resolved = _portable_pattern_resource(value)
    if resolved is None:
        return None
    pattern, resource = resolved
    numbers = _portable_pattern_numbers(pattern)
    if numbers is None:
        return None
    color = pattern.get("color_hex")
    if not isinstance(color, str):
        return None
    origin_x, origin_y, tile_width, tile_height, slope_radians, opacity = numbers
    tile_width /= units_per_px
    tile_height /= units_per_px
    if tile_width <= 0.0 or tile_height <= 0.0:
        return None
    return _PortablePatternState(
        resource=resource,
        origin_x=origin_x,
        origin_y=origin_y,
        tile_width=tile_width,
        tile_height=tile_height,
        screen_degrees=math.degrees(slope_radians),
        opacity=max(0.0, min(1.0, opacity)),
        color=html.escape(color, quote=True),
    )


def _identity_affine() -> tuple[float, float, float, float, float, float]:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose_affine(
    left: tuple[float, float, float, float, float, float],
    right: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply_affine(
    matrix: tuple[float, float, float, float, float, float],
    x: float,
    y: float,
) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) <= 1e-9:
        return str(int(round(value)))
    nearest_half = round(value * 2.0) / 2.0
    if abs(value - nearest_half) <= 1e-4:
        value = nearest_half
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _fmt_native_text_rotation_coord(value: float) -> str:
    return f"{value:.4f}"


def _to_svg_point(
    x: float,
    y: float,
    *,
    units_per_px: float,
    canvas_height_px: float,
) -> tuple[float, float]:
    return (x / units_per_px, y / units_per_px + canvas_height_px)


def _pen_width_to_svg(pen: dict[str, Any], *, units_per_px: float) -> float:
    width = float(pen.get("width", 0) or 0.0)
    min_width = float(pen.get("min_width", 1) or 0.0)
    if width <= 0.0:
        return 0.5 if min_width > 0.0 else 0.0
    return max(width / units_per_px, 0.5 if min_width > 0.0 else 0.0)


def _style_alpha(style: dict[str, object]) -> int | None:
    raw = style.get("color_raw")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        return (int(raw) & 0xFFFFFFFF) >> 24
    except (TypeError, ValueError, OverflowError):
        return None


def _stroke_opacity_attr(pen: dict[str, object]) -> str:
    alpha = _style_alpha(pen)
    if alpha is None or alpha >= 0xFF:
        return ""
    return f' stroke-opacity="{alpha / 255.0}"'


# Onscreen dash patterns follow the screen renderer's built-in dash styles,
# which are defined in multiples of the stroke width with the pattern unit
# floored at the 1px minimum pen width. Dots are zero-length dashes made
# visible by the round line cap. Every pen-bearing op kind must consume this
# map so dash pens survive serialization.
_PEN_DASH_PATTERNS = {
    "pdsDash": (2.0, 2.0),
    "pdsDot": (0.0, 2.0),
    "pdsDashDot": (2.0, 2.0, 0.0, 2.0),
    "pdsDashDotDot": (2.0, 2.0, 0.0, 2.0, 0.0, 2.0),
}

# Native (metafile-parity) SVG export keeps the fixed legacy dash arrays for
# shape pens; metafile line dashes are pre-segmented upstream, so this map
# never applies to exploded line segments.
_PEN_DASH_ARRAYS_NATIVE = {
    "pdsDash": "4",
    "pdsDot": "2",
    "pdsDashDot": "4 2",
}


def _pen_dasharray(
    pen: dict[str, object] | None, stroke_width: float, *, native: bool = False
) -> str | None:
    if not pen:
        return None
    dash_style = str(pen.get("dash_style", "") or "")
    if native:
        return _PEN_DASH_ARRAYS_NATIVE.get(dash_style)
    pattern = _PEN_DASH_PATTERNS.get(dash_style)
    if pattern is None:
        return None
    unit = max(stroke_width, 1.0)
    return " ".join(_fmt_num(value * unit) for value in pattern)


@dataclass(frozen=True)
class SchGeometrySvgRenderOptions:
    """Rendering options for SVG generated from schematic geometry IR."""

    include_workspace_background: bool = True
    workspace_background_color: str = "#E3E3E3"
    include_xml_declaration: bool = True
    text_mode: str = "onscreen"
    compile_mask_render_mode: SchCompileMaskRenderMode | None = None
    text_as_polygons: bool = False
    polygon_text_tolerance: float = 0.5
    include_view_box: bool = True
    embed_bundled_fallback_fonts: bool = False


class SchGeometrySvgRenderer:
    """Pure geometry-to-SVG renderer for schematic geometry documents."""

    def __init__(self, options: SchGeometrySvgRenderOptions | None = None) -> None:
        self.options = options or SchGeometrySvgRenderOptions()
        self._clip_rect_counter = 0
        self._pattern_counter = 0
        self._painted_bundled_font_faces: set[_BundledFontFaceKey] = set()
        self._font_face_candidates: list[FontDiagnosticPayload] = []

    @property
    def _native_dash(self) -> bool:
        return self.options.text_mode == "native_svg_export"

    def _next_clip_rect_id(self) -> str:
        self._clip_rect_counter += 1
        return f"ClipRect{self._clip_rect_counter}"

    def _effective_compile_mask_render_mode(
        self,
        document: SchGeometryDocument,
    ) -> SchCompileMaskRenderMode:
        if self.options.compile_mask_render_mode is not None:
            return self.options.compile_mask_render_mode

        compile_mask_hints = (
            document.render_hints.get("compile_mask") if document.render_hints else None
        )
        if isinstance(compile_mask_hints, dict):
            render_mode = (
                str(compile_mask_hints.get("render_mode", "") or "").strip().lower()
            )
            if render_mode == "compiled_visual":
                return SchCompileMaskRenderMode.COMPILED_VISUAL

        return SchCompileMaskRenderMode.ORACLE_RAW

    def _render_manual_junction_status_overlays(
        self,
        document: SchGeometryDocument,
    ) -> list[str]:
        manual_hints = (
            document.render_hints.get("manual_junction_status")
            if document.render_hints
            else None
        )
        if not isinstance(manual_hints, list):
            return []

        elements: list[str] = []
        for hint in manual_hints:
            if not isinstance(hint, dict):
                continue
            x = float(hint.get("x", 0.0) or 0.0)
            y = float(hint.get("y", 0.0) or 0.0)
            width = float(hint.get("width", 4.0) or 4.0)
            height = float(hint.get("height", 4.0) or 4.0)
            rx = float(hint.get("rx", 2.0) or 0.0)
            ry = float(hint.get("ry", 2.0) or 0.0)
            fill = html.escape(str(hint.get("fill", "#000080") or "#000080"))
            stroke = html.escape(str(hint.get("stroke", fill) or fill))
            elements.append(
                f'<rect x = "{_fmt_num(x)}" y="{_fmt_num(y)}" '
                f'width="{_fmt_num(width)}" height="{_fmt_num(height)}" '
                f'rx="{_fmt_num(rx)}" ry="{_fmt_num(ry)}" '
                f'fill="{fill}" fill-opacity="1"/>'
            )
            elements.append(
                f'<rect x = "{_fmt_num(x)}" y="{_fmt_num(y)}" '
                f'width="{_fmt_num(width)}" height="{_fmt_num(height)}" '
                f'stroke="{stroke}" stroke-width="0.5px" '
                f'vector-effect="non-scaling-stroke" '
                f'rx="{_fmt_num(rx)}" ry="{_fmt_num(ry)}"/>'
            )

        return elements

    def _resolve_render_frame(
        self,
        document: SchGeometryDocument,
    ) -> tuple[float, float, float, str, str]:
        canvas = document.canvas or {}
        width_px = float(canvas.get("width_px", 0) or 0.0)
        height_px = float(canvas.get("height_px", 0) or 0.0)
        if width_px <= 0.0 or height_px <= 0.0:
            raise ValueError(
                "Geometry document must provide positive canvas dimensions"
            )

        coordinate_space = document.coordinate_space or {}
        units_per_px = float(coordinate_space.get("units_per_px", 64) or 64.0)
        doc_id = document.document_id or next(
            (record.unique_id for record in document.records if record.unique_id),
            "AAAAAAAA",
        )
        workspace_bg = (
            document.workspace_background_color
            or self.options.workspace_background_color
        )
        return width_px, height_px, units_per_px, doc_id, workspace_bg

    def _collect_sheet_and_nested_record_ids(
        self,
        document: SchGeometryDocument,
    ) -> tuple[SchGeometryRecord | None, set[str], set[str]]:
        sheet_record = next(
            (record for record in document.records if record.kind == "sheet"), None
        )
        all_record_ids = {
            self._record_render_group_id(record)
            for record in document.records
            if self._record_render_group_id(record)
        }
        all_record_identities = {
            self._record_source_key(record)
            for record in document.records
            if self._record_render_group_id(record)
        }
        nested_record_identities: set[str] = set()
        legacy_nested_record_ids: set[str] = set()
        for record in document.records:
            for op in record.operations:
                group_id = self._nested_operation_group_id(record, op)
                if not group_id:
                    continue
                group_identity = (
                    f"source:{op.render_source_id}"
                    if op.render_source_id is not None
                    else str(op.render_group_identity or group_id)
                )
                if group_identity in all_record_identities:
                    nested_record_identities.add(group_identity)
                elif (
                    op.render_source_id is None
                    and not op.render_group_identity
                    and group_id in all_record_ids
                ):
                    legacy_nested_record_ids.add(group_id)
        return sheet_record, nested_record_identities, legacy_nested_record_ids

    def _nested_operation_group_id(
        self, record: SchGeometryRecord, operation: SchGeometryOp
    ) -> str:
        if operation.kind_str() != SchGeometryOpKind.BEGIN_GROUP.value:
            return ""
        group_id = str(
            operation.render_group_id or operation.payload.get("parameters", "") or ""
        )
        if group_id in {"", "DocumentMainGroup", "DocumentItemsGroup"}:
            return ""
        if self._operation_is_record_wrapper(record, operation, group_id):
            return ""
        return group_id

    def _record_source_key(self, record: SchGeometryRecord) -> str:
        if record.render_source_id is not None:
            return f"source:{record.render_source_id}"
        return self._record_render_group_identity(record)

    def _operation_is_record_wrapper(
        self, record: SchGeometryRecord, operation: SchGeometryOp, group_id: str
    ) -> bool:
        if (
            operation.render_source_id is not None
            and record.render_source_id is not None
        ):
            return operation.render_source_id == record.render_source_id
        if operation.render_group_identity:
            return (
                operation.render_group_identity
                == self._record_render_group_identity(record)
            )
        return group_id in {str(record.unique_id), str(record.unique_id or "")}

    @staticmethod
    def _record_render_group_id(record: SchGeometryRecord) -> str:
        return str(record.render_group_id or record.unique_id or "")

    @classmethod
    def _record_render_group_identity(cls, record: SchGeometryRecord) -> str:
        return str(
            record.render_group_identity
            or f"{record.object_id}\0{cls._record_render_group_id(record)}"
        )

    def _record_group_key(self, record: SchGeometryRecord) -> str:
        if self.options.text_mode == "native_svg_export":
            return self._record_render_group_id(record)
        return self._record_render_group_identity(record)

    def _operation_group_key(self, operation: SchGeometryOp, group_id: str) -> str:
        if self.options.text_mode == "native_svg_export":
            return group_id
        return str(operation.render_group_identity or group_id)

    def _render_document_items_group(
        self,
        document: SchGeometryDocument,
        *,
        sheet_record: SchGeometryRecord | None,
        nested_record_identities: set[str],
        legacy_nested_record_ids: set[str],
        units_per_px: float,
        canvas_height_px: float,
        compiled_visual_masks: bool,
    ) -> tuple[list[str], list[str]]:
        lines: list[str] = ['<g id = "DocumentItemsGroup" >']
        foreground_compile_mask_records: list[str] = []
        transparent_back_elements: list[str] = []
        if (
            self.options.text_mode == "native_svg_export"
            or self._has_explicit_transparent_back(document)
        ):
            transparent_back_elements = self._render_transparent_back_elements(
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        if transparent_back_elements:
            lines.append('<g id = "TransparentBackGroup" >')
            lines.extend(transparent_back_elements)
            lines.append("</g>")

        claimed_group_ids: set[str] = set()
        group_slots: dict[str, int] = {}
        item_chunks: list[list[str]] = []
        for record in document.records:
            group_id = self._record_render_group_id(record)
            group_identity = self._record_group_key(record)
            if (
                record is sheet_record
                or self._record_source_key(record) in nested_record_identities
                or group_id in legacy_nested_record_ids
            ):
                continue
            if group_id and group_identity in claimed_group_ids:
                continue
            rendered_record = self._render_item_record(
                record,
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            if compiled_visual_masks and record.kind == "compilemask":
                foreground_compile_mask_records.extend(rendered_record)
                continue
            if not group_id:
                item_chunks.append(rendered_record)
                continue
            self._place_document_group(
                group_identity,
                rendered_record,
                item_chunks,
                group_slots,
                claimed_group_ids,
            )
        lines.extend(line for chunk in item_chunks for line in chunk)
        lines.append("</g>")
        return lines, foreground_compile_mask_records

    @staticmethod
    def _place_document_group(
        group_identity: str,
        rendered_record: list[str],
        item_chunks: list[list[str]],
        group_slots: dict[str, int],
        claimed_group_ids: set[str],
    ) -> None:
        slot = group_slots.get(group_identity)
        if slot is None:
            group_slots[group_identity] = len(item_chunks)
            item_chunks.append(rendered_record)
            if rendered_record:
                claimed_group_ids.add(group_identity)
        elif rendered_record and group_identity not in claimed_group_ids:
            item_chunks[slot] = rendered_record
            claimed_group_ids.add(group_identity)

    def _resolve_compile_mask_overlay(
        self,
        document: SchGeometryDocument,
    ) -> tuple[list[tuple[float, float, float, float]], str, float | None]:
        compile_mask_hints = (
            document.render_hints.get("compile_mask") if document.render_hints else None
        )
        compile_mask_bounds: list[tuple[float, float, float, float]] = []
        sheet_background_color = "#FFFCF8"
        overlay_opacity: float | None = None
        if isinstance(compile_mask_hints, dict):
            compile_mask_bounds = [
                tuple(bounds)
                for bounds in list(compile_mask_hints.get("bounds", []) or [])
            ]
            sheet_background_color = str(
                compile_mask_hints.get("background_color") or sheet_background_color
            )
            raw_overlay_opacity = compile_mask_hints.get("overlay_opacity")
            overlay_opacity = (
                None if raw_overlay_opacity is None else float(raw_overlay_opacity)
            )
        if not compile_mask_bounds:
            compile_mask_bounds = [
                tuple(bounds)
                for bounds in document.extras.get("compile_mask_bounds", [])
            ]
        if not sheet_background_color:
            sheet_background_color = str(
                document.extras.get("sheet_background_color") or "#FFFCF8"
            )
        return compile_mask_bounds, sheet_background_color, overlay_opacity

    def _font_resolution_diagnostics_from_hints(
        self,
        document: SchGeometryDocument,
    ) -> list[FontDiagnosticPayload]:
        font_hints = (
            document.render_hints.get("font_resolution")
            if document.render_hints
            else None
        )
        if not isinstance(font_hints, dict):
            return []
        diagnostics = font_hints.get("diagnostics")
        if not isinstance(diagnostics, list):
            return []
        return [
            {str(key): value for key, value in item.items()}
            for item in diagnostics
            if isinstance(item, dict)
        ]

    def _font_resolution_diagnostics_from_text_ops(
        self,
        document: SchGeometryDocument,
    ) -> list[FontDiagnosticPayload]:
        diagnostics: list[FontDiagnosticPayload] = []
        for record in document.records:
            for op in record.operations:
                if op.kind_str() != SchGeometryOpKind.STRING.value:
                    continue
                font = op.payload.get("font")
                if not isinstance(font, dict):
                    continue
                font_name = str(font.get("name", "") or "")
                if not font_name:
                    continue
                resolution = resolve_font_with_style(
                    font_name,
                    bold=bool(font.get("bold", False)),
                    italic=bool(font.get("italic", False)),
                )
                diagnostics.append(resolution.to_dict())
        return diagnostics

    def _font_face_candidate_diagnostics(
        self,
        document: SchGeometryDocument,
    ) -> list[FontDiagnosticPayload]:
        return [
            *self._font_resolution_diagnostics_from_hints(document),
            *self._font_resolution_diagnostics_from_text_ops(document),
        ]

    def _bundled_font_face_key(
        self,
        diagnostic: FontDiagnosticPayload,
    ) -> _BundledFontFaceKey | None:
        if str(diagnostic.get("source", "")) != FontResolutionSource.BUNDLED_FONT.value:
            return None
        path_value = diagnostic.get("path")
        resolved_family = str(diagnostic.get("resolved_family", "") or "").strip()
        if not resolved_family or not isinstance(path_value, str):
            return None
        font_path = canonical_package_bundled_font_path(path_value)
        if font_path is None:
            return None
        return (
            resolved_family,
            bool(diagnostic.get("requested_bold", False)),
            bool(diagnostic.get("requested_italic", False)),
            str(font_path),
        )

    def _render_font_face_style(
        self,
        diagnostics: list[FontDiagnosticPayload],
        *,
        painted_faces: set[_BundledFontFaceKey],
    ) -> list[str]:
        if not self.options.embed_bundled_fallback_fonts or not painted_faces:
            return []

        rules: list[str] = []
        seen: set[_BundledFontFaceKey] = set()
        for diagnostic in diagnostics:
            key = self._bundled_font_face_key(diagnostic)
            if key is None or key not in painted_faces or key in seen:
                continue
            seen.add(key)
            resolved_family, bold, italic, font_path_value = key
            font_path = Path(font_path_value)
            encoded_font = base64.b64encode(font_path.read_bytes()).decode("ascii")
            font_style = "italic" if italic else "normal"
            font_weight = "700" if bold else "400"
            rules.append(
                "@font-face { "
                f"font-family: {json.dumps(resolved_family)}; "
                f"font-style: {font_style}; "
                f"font-weight: {font_weight}; "
                "font-display: block; "
                f"src: url('data:font/ttf;base64,{encoded_font}') format('truetype'); "
                "}"
            )
        if not rules:
            return []
        return ["<defs>", "<style>", *rules, "</style>", "</defs>"]

    def _attach_bundled_font_faces(
        self,
        document: SchGeometryDocument,
        lines: list[str],
        *,
        svg_root_line_index: int,
    ) -> None:
        if not self.options.embed_bundled_fallback_fonts:
            return
        font_face_style = self._render_font_face_style(
            self._font_face_candidates,
            painted_faces=self._painted_bundled_font_faces,
        )
        if font_face_style:
            lines[svg_root_line_index + 1 : svg_root_line_index + 1] = font_face_style

    def _resolve_svg_font(
        self,
        font: FontDiagnosticPayload,
    ) -> tuple[str, FontResolution | None]:
        font_name = str(font.get("name", "") or "")
        if not font_name:
            return "", None
        resolution = resolve_font_with_style(
            font_name,
            bold=bool(font.get("bold", False)),
            italic=bool(font.get("italic", False)),
        )
        return resolution.resolved_family or font_name, resolution

    def render(self, document: SchGeometryDocument) -> str:
        self._clip_rect_counter = 0
        self._pattern_counter = 0
        self._painted_bundled_font_faces.clear()
        # The historical diagnostic scan includes nonpainted operations. Keep it
        # separate from the later paint-time set that authorizes payload bytes.
        self._font_face_candidates = self._font_face_candidate_diagnostics(document)
        width_px, height_px, units_per_px, doc_id, workspace_bg = (
            self._resolve_render_frame(document)
        )

        lines: list[str] = []
        if self.options.include_xml_declaration:
            lines.append('<?xml version="1.0"  encoding="UTF-8" standalone="no"?>')
        svg_attrs = [
            'version="1.1"',
            'xmlns="http://www.w3.org/2000/svg"',
            'xmlns:xlink="http://www.w3.org/1999/xlink"',
            'stroke-linecap="round"',
            'stroke-linejoin="round"',
            'fill="none"',
            f'width="{_fmt_num(width_px)}"',
            f'height="{_fmt_num(height_px)}"',
        ]
        if self.options.include_view_box:
            svg_attrs.append(
                f'viewBox="0 0 {_fmt_num(width_px)} {_fmt_num(height_px)}"'
            )
        svg_attrs.extend(
            [
                f'data-doc-id="{html.escape(doc_id)}"',
                'data-doc-ver="2"',
            ]
        )
        render_hints = document.render_hints or {}
        page_occurrence_ref = str(render_hints.get("page_occurrence_ref", "") or "")
        artifact_key = str(render_hints.get("artifact_key", "") or "")
        if page_occurrence_ref:
            svg_attrs.append(
                f'data-page-occurrence-ref="{html.escape(page_occurrence_ref)}"'
            )
        if artifact_key:
            svg_attrs.append(f'data-artifact-key="{html.escape(artifact_key)}"')
        lines.append(f"<svg {' '.join(svg_attrs)}>")
        svg_root_line_index = len(lines) - 1
        lines.append('<g id = "scene" >')

        lines.append('<g id = "DocumentMainGroup" >')
        if self.options.include_workspace_background:
            lines.append('<g id = "BackgroundGroup" >')
            lines.append(
                f'<rect x = "0" y="0" width="{_fmt_num(width_px)}" height="{_fmt_num(height_px)}" '
                f'fill="{workspace_bg}" fill-opacity="1"/>'
            )
            lines.append("</g>")

        lines.append(f'<g id = "{html.escape(doc_id)}" >')

        (
            sheet_record,
            nested_record_identities,
            legacy_nested_record_ids,
        ) = self._collect_sheet_and_nested_record_ids(document)
        if sheet_record is not None:
            lines.extend(
                self._render_record_primitives(
                    sheet_record,
                    document=document,
                    units_per_px=units_per_px,
                    canvas_height_px=height_px,
                )
            )
        lines.extend(self._render_manual_junction_status_overlays(document))

        compiled_visual_masks = (
            self._effective_compile_mask_render_mode(document)
            == SchCompileMaskRenderMode.COMPILED_VISUAL
        )
        item_lines, foreground_compile_mask_records = self._render_document_items_group(
            document,
            sheet_record=sheet_record,
            nested_record_identities=nested_record_identities,
            legacy_nested_record_ids=legacy_nested_record_ids,
            units_per_px=units_per_px,
            canvas_height_px=height_px,
            compiled_visual_masks=compiled_visual_masks,
        )
        lines.extend(item_lines)
        compile_mask_bounds, sheet_background_color, overlay_opacity = (
            self._resolve_compile_mask_overlay(document)
        )
        if compiled_visual_masks and compile_mask_bounds:
            lines.extend(
                build_compile_mask_visual_overlay_svg(
                    canvas_width_px=width_px,
                    canvas_height_px=height_px,
                    compile_mask_bounds=compile_mask_bounds,
                    background_color=sheet_background_color,
                    opacity=overlay_opacity,
                )
            )
            if foreground_compile_mask_records:
                lines.extend(foreground_compile_mask_records)
        lines.append("</g>")
        lines.append("</g>")
        lines.append("</g>")
        lines.append("</svg>")
        self._attach_bundled_font_faces(
            document,
            lines,
            svg_root_line_index=svg_root_line_index,
        )
        return "\n".join(lines)

    def _build_text_render_context(
        self, document: SchGeometryDocument
    ) -> SchSvgRenderContext:
        return SchSvgRenderContext(
            options=SchSvgRenderOptions(
                text_as_polygons=self.options.text_as_polygons,
                polygon_text_tolerance=self.options.polygon_text_tolerance,
            )
        )

    def _render_item_record(
        self,
        record: SchGeometryRecord,
        *,
        document: SchGeometryDocument,
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        if bool(getattr(record, "extras", {}).get("skip_svg", False)):
            return []
        primitives = self._render_record_primitives(
            record,
            document=document,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
            suppress_transparent_back=(
                self._record_has_explicit_transparent_back(record)
                or (
                    self.options.text_mode == "native_svg_export"
                    and str(getattr(record, "kind", "") or "")
                    in {"compilemask", "blanket"}
                )
            ),
        )
        if not primitives:
            return []
        group_id = self._record_render_group_id(record)
        if group_id:
            graph_attrs = self._compiled_graph_group_attrs(document, group_id)
            return [
                f'<g id = "{html.escape(group_id)}"{graph_attrs} >',
                *primitives,
                "</g>",
            ]
        return primitives

    @staticmethod
    def _compiled_graph_group_attrs(
        document: SchGeometryDocument, element_id: str
    ) -> str:
        """Return scoped compiled-graph attributes for a retained record group."""

        raw_links = (document.extras or {}).get(
            "compiled_schematic_graphical_links", []
        )
        if not isinstance(raw_links, list):
            return ""
        link = next(
            (
                value
                for value in raw_links
                if isinstance(value, dict)
                and str(value.get("element_id", "")) == element_id
            ),
            None,
        )
        if link is None:
            return ""
        attrs = {
            "data-page-occurrence-ref": link.get("page_occurrence_ref", ""),
            "data-artifact-key": link.get("artifact_key", ""),
            "data-element-id": element_id,
            "data-graph-target-type": link.get("target_type", ""),
            "data-graph-target-ref": link.get("target_ref", ""),
        }
        return "".join(
            f' {name}="{html.escape(str(value))}"'
            for name, value in attrs.items()
            if value
        )

    def _render_record_primitives(
        self,
        record: SchGeometryRecord,
        *,
        document: SchGeometryDocument,
        units_per_px: float,
        canvas_height_px: float,
        suppress_transparent_back: bool = False,
        transparent_back_only: bool = False,
    ) -> list[str]:
        rendered_stack: list[dict[str, Any]] = [
            {
                "frame_type": "root",
                "group_id": None,
                "group_identity": self._record_group_key(record),
                "content": [],
                "groups": [],
                "claimed_group_ids": set(),
                "group_slots": {},
                "has_paint": False,
            }
        ]
        transform_stack: list[tuple[float, float, float, float, float, float]] = [
            _identity_affine()
        ]
        polygon_op_index = -1
        skipped_group_depth = 0

        for op in record.operations:
            kind = op.kind_str()
            payload = op.payload
            if skipped_group_depth:
                if kind == SchGeometryOpKind.BEGIN_GROUP.value:
                    skipped_group_depth += 1
                elif kind == SchGeometryOpKind.END_GROUP.value:
                    skipped_group_depth -= 1
                continue
            if (
                kind == SchGeometryOpKind.BEGIN_GROUP.value
                and self._record_group_is_populated(record, op, rendered_stack[-1])
            ):
                skipped_group_depth = 1
                continue

            if self._skip_record_op_for_transparent_back(
                kind,
                payload=payload,
                record_kind=str(getattr(record, "kind", "") or ""),
                transparent_back_only=transparent_back_only,
                suppress_transparent_back=suppress_transparent_back,
            ):
                continue

            if self._handle_record_stack_operation(
                kind,
                operation=op,
                payload=payload,
                record=record,
                document=document,
                rendered_stack=rendered_stack,
                transform_stack=transform_stack,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ):
                continue

            polygon_op_index, rendered = self._render_record_draw_operation(
                kind,
                payload=payload,
                record=record,
                document=document,
                rendered_stack=rendered_stack,
                transform_stack=transform_stack,
                polygon_op_index=polygon_op_index,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
                suppress_transparent_back=suppress_transparent_back,
                transparent_back_only=transparent_back_only,
            )
            rendered_stack[-1]["content"].extend(rendered)
            if rendered:
                rendered_stack[-1]["has_paint"] = True
        while len(rendered_stack) > 1:
            self._pop_render_frame(
                rendered_stack,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        content = self._frame_content(rendered_stack[0])
        if not content and rendered_stack[0]["has_paint"]:
            # An owner with an empty child group is structurally nonempty.
            # Retain its outer wrapper even though the child draws nothing.
            return [""]
        return content

    def _skip_record_op_for_transparent_back(
        self,
        kind: str,
        *,
        payload: dict[str, object],
        record_kind: str,
        transparent_back_only: bool,
        suppress_transparent_back: bool,
    ) -> bool:
        targets_transparent_back = payload.get("transparent_back") is True
        if not transparent_back_only:
            return suppress_transparent_back and targets_transparent_back
        if kind in {
            SchGeometryOpKind.PUSH_TRANSFORM.value,
            SchGeometryOpKind.POP_TRANSFORM.value,
        }:
            return False
        if targets_transparent_back:
            return False
        if payload.get("transparent_back") is False:
            return True
        return not (
            record_kind in {"compilemask", "blanket"}
            and kind == SchGeometryOpKind.POLYGONS.value
        )

    def _pop_render_frame(
        self,
        rendered_stack: list[dict[str, Any]],
        *,
        units_per_px: float,
        canvas_height_px: float,
    ) -> None:
        if len(rendered_stack) <= 1:
            return
        completed = rendered_stack.pop()
        if completed is rendered_stack[-1]:
            return
        has_paint = bool(completed.get("has_paint", False))
        group_id = str(completed.get("group_id", "") or "")
        group_identity = str(completed.get("group_identity", group_id) or group_id)
        wrapped = self._wrap_completed_frame(
            completed,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        parent = rendered_stack[-1]
        if group_id:
            self._place_completed_group(
                parent,
                group_identity,
                wrapped,
                has_paint,
            )
        else:
            parent["content"].append(wrapped)
        if group_id or has_paint:
            parent["has_paint"] = True

    @staticmethod
    def _place_completed_group(
        parent: dict[str, object],
        group_identity: str,
        wrapped: list[_SvgRenderChunk],
        has_paint: bool,
    ) -> None:
        claimed_group_ids = cast(set[str], parent["claimed_group_ids"])
        group_slots = cast(dict[str, int], parent["group_slots"])
        groups = cast(list[list[_SvgRenderChunk]], parent["groups"])
        slot = group_slots.get(group_identity)
        if slot is None:
            group_slots[group_identity] = len(groups)
            groups.append(wrapped)
            if has_paint:
                claimed_group_ids.add(group_identity)
            return
        if not has_paint or group_identity in claimed_group_ids:
            return
        groups[slot] = wrapped
        claimed_group_ids.add(group_identity)

    def _handle_record_stack_operation(
        self,
        kind: str,
        *,
        operation: SchGeometryOp,
        payload: dict[str, Any],
        record: SchGeometryRecord,
        document: SchGeometryDocument,
        rendered_stack: list[dict[str, Any]],
        transform_stack: list[tuple[float, float, float, float, float, float]],
        units_per_px: float,
        canvas_height_px: float,
    ) -> bool:
        if kind == SchGeometryOpKind.PUSH_TRANSFORM.value:
            matrix = payload.get("matrix") or []
            if len(matrix) == 6:
                pushed = (
                    float(matrix[0]),
                    float(matrix[1]),
                    float(matrix[2]),
                    float(matrix[3]),
                    float(matrix[4]),
                    float(matrix[5]),
                )
                transform_stack.append(_compose_affine(transform_stack[-1], pushed))
            return True
        if kind == SchGeometryOpKind.POP_TRANSFORM.value:
            if len(transform_stack) > 1:
                transform_stack.pop()
            return True
        if kind == SchGeometryOpKind.BEGIN_GROUP.value:
            group_id, group_identity = self._render_operation_group(record, operation)
            parent = rendered_stack[-1]
            if not group_id:
                rendered_stack.append(parent)
                return True
            self_match = group_identity == parent.get("group_identity")
            if self_match and not parent["has_paint"]:
                rendered_stack.append(parent)
                return True
            rendered_stack.append(
                {
                    "frame_type": "group",
                    "group_id": group_id,
                    "group_identity": group_identity,
                    "graph_attrs": self._compiled_graph_group_attrs(document, group_id),
                    "content": [],
                    "groups": [],
                    "claimed_group_ids": set(),
                    "group_slots": {},
                    "has_paint": False,
                }
            )
            return True
        if kind == SchGeometryOpKind.END_GROUP.value:
            self._pop_render_frame(
                rendered_stack,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            return True
        if kind == SchGeometryOpKind.PUSH_CLIP.value:
            rendered_stack.append(
                {
                    "frame_type": "clip",
                    "group_id": None,
                    "clip_id": self._next_clip_rect_id(),
                    "clip_payload": dict(payload),
                    "clip_transform": transform_stack[-1],
                    "content": [],
                    "groups": [],
                    "claimed_group_ids": set(),
                    "group_slots": {},
                    "has_paint": False,
                }
            )
            return True
        if kind == SchGeometryOpKind.POP_CLIP.value:
            self._pop_render_frame(
                rendered_stack,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            return True
        return False

    def _render_operation_group(
        self, record: SchGeometryRecord, operation: SchGeometryOp
    ) -> tuple[str, str]:
        persisted_group_id = str(operation.payload.get("parameters", "") or "")
        group_id = str(operation.render_group_id or persisted_group_id)
        group_identity = self._operation_group_key(operation, group_id)
        if self._operation_is_record_wrapper(record, operation, persisted_group_id):
            return "", ""
        if group_id in {"", "DocumentMainGroup", "DocumentItemsGroup"}:
            return "", ""
        return group_id, group_identity

    def _record_group_is_populated(
        self,
        record: SchGeometryRecord,
        operation: SchGeometryOp,
        parent: dict[str, object],
    ) -> bool:
        group_id, group_identity = self._render_operation_group(record, operation)
        if not group_id:
            return False
        if group_identity == parent.get("group_identity"):
            return bool(parent.get("has_paint"))
        claimed = parent.get("claimed_group_ids")
        return isinstance(claimed, set) and group_identity in claimed

    def _record_clip_path(
        self,
        rendered_stack: list[dict[str, Any]],
    ) -> str | None:
        if rendered_stack[-1].get("frame_type") != "clip":
            return None
        clip_id = str(rendered_stack[-1].get("clip_id", "") or "")
        if not clip_id:
            return None
        return f"url(#{clip_id})"

    def _record_image_unique_id(
        self,
        record: object,
        rendered_stack: list[dict[str, Any]],
    ) -> str:
        if str(getattr(record, "kind", "") or "") == "image":
            extras = getattr(record, "extras", None)
            if isinstance(extras, dict):
                image_key = str(extras.get("image_key", "") or "")
                if image_key:
                    return image_key
        record_unique_id = str(getattr(record, "unique_id", "") or "")
        for frame in reversed(rendered_stack):
            group_id = str(frame.get("group_id", "") or "")
            if group_id:
                return group_id
        return record_unique_id

    def _render_record_draw_operation(
        self,
        kind: str,
        *,
        payload: dict[str, Any],
        record: object,
        document: SchGeometryDocument,
        rendered_stack: list[dict[str, Any]],
        transform_stack: list[tuple[float, float, float, float, float, float]],
        polygon_op_index: int,
        units_per_px: float,
        canvas_height_px: float,
        suppress_transparent_back: bool,
        transparent_back_only: bool,
    ) -> tuple[int, list[str]]:
        transform = transform_stack[-1]
        if kind == SchGeometryOpKind.ROUNDED_RECTANGLE.value:
            return polygon_op_index, self._render_rounded_rectangle(
                payload,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        if kind == SchGeometryOpKind.LINES.value:
            return polygon_op_index, self._render_lines(
                payload,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        if kind == SchGeometryOpKind.POLYGONS.value:
            polygon_op_index += 1
            return polygon_op_index, self._render_polygons(
                payload,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
                record_kind=str(getattr(record, "kind", "") or ""),
                op_index=polygon_op_index,
                suppress_transparent_back=suppress_transparent_back,
                transparent_back_only=transparent_back_only,
            )
        if kind == SchGeometryOpKind.ARC.value:
            return polygon_op_index, self._render_arc(
                payload,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        if kind == SchGeometryOpKind.STRING.value:
            return polygon_op_index, self._render_string(
                payload,
                document=document,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
                record_kind=str(getattr(record, "kind", "") or ""),
                clip_path=self._record_clip_path(rendered_stack),
            )
        if kind == SchGeometryOpKind.IMAGE.value:
            return polygon_op_index, self._render_image(
                payload,
                record_unique_id=self._record_image_unique_id(record, rendered_stack),
                document=document,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        return polygon_op_index, []

    def _render_transparent_back_elements(
        self,
        *,
        document: SchGeometryDocument,
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        rendered: list[str] = []
        claimed_group_ids: set[str] = set()
        for record in document.records:
            if not self._record_has_transparent_back_pass(record):
                continue
            group_id = self._transparent_back_group_id(record)
            if group_id and group_id in claimed_group_ids:
                continue
            fill_only = self._render_record_transparent_back(
                record,
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            if not fill_only:
                continue
            if group_id:
                claimed_group_ids.add(group_id)
                rendered.append(f'<g id = "{html.escape(group_id)}" >')
                rendered.extend(fill_only)
                rendered.append("</g>")
            else:
                rendered.extend(fill_only)
        return rendered

    def _record_has_transparent_back_pass(self, record: SchGeometryRecord) -> bool:
        record_kind = str(getattr(record, "kind", "") or "")
        return self._record_has_explicit_transparent_back(record) or (
            self.options.text_mode == "native_svg_export"
            and record_kind in {"compilemask", "blanket"}
        )

    def _transparent_back_group_id(self, record: SchGeometryRecord) -> str:
        unique_id = self._record_render_group_id(record)
        if not unique_id:
            return ""
        if self.options.text_mode == "native_svg_export":
            return f"{unique_id}TransparentBackSuffix"
        object_id = str(getattr(record, "object_id", "") or "")
        prefix = f"{object_id}_" if object_id else ""
        return f"{prefix}{unique_id}TransparentBackSuffix"

    @staticmethod
    def _record_has_explicit_transparent_back(record: SchGeometryRecord) -> bool:
        return any(
            operation.payload.get("transparent_back") is True
            for operation in record.operations
        )

    @classmethod
    def _has_explicit_transparent_back(cls, document: SchGeometryDocument) -> bool:
        return any(
            cls._record_has_explicit_transparent_back(record)
            for record in document.records
        )

    def _render_record_transparent_back(
        self,
        record: SchGeometryRecord,
        *,
        document: SchGeometryDocument,
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        return self._render_record_primitives(
            record,
            document=document,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
            transparent_back_only=True,
        )

    def _resolve_image_href(
        self,
        *,
        document: SchGeometryDocument,
        record_unique_id: str,
    ) -> str:
        runtime_hrefs = getattr(document, "_runtime_image_hrefs", None)
        if isinstance(runtime_hrefs, dict):
            href = runtime_hrefs.get(record_unique_id)
            if href:
                return str(href)
        return ""

    def _wrap_completed_frame(
        self,
        frame: dict[str, Any],
        *,
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[_SvgRenderChunk]:
        frame_type = str(frame.get("frame_type", "group") or "group")
        content: list[_SvgRenderChunk] = [
            frame.get("content", []),
            frame.get("groups", []),
        ]
        if not frame.get("has_paint", False):
            return []
        if frame_type == "clip":
            clip_id = str(frame.get("clip_id", "") or "")
            if clip_id:
                return self._render_clip_wrapper(
                    clip_id=clip_id,
                    payload=dict(frame.get("clip_payload", {}) or {}),
                    transform=tuple(frame.get("clip_transform", _identity_affine())),
                    content=content,
                    units_per_px=units_per_px,
                    canvas_height_px=canvas_height_px,
                )
        group_id = str(frame.get("group_id", "") or "")
        if group_id:
            graph_attrs = str(frame.get("graph_attrs", "") or "")
            return [
                f'<g id = "{html.escape(group_id)}"{graph_attrs} >',
                content,
                "</g>",
            ]
        return content

    @staticmethod
    def _frame_content(frame: dict[str, object]) -> list[str]:
        lines: list[str] = []
        pending: list[_SvgRenderChunk] = [
            cast(list[_SvgRenderChunk], frame.get("groups", [])),
            cast(list[_SvgRenderChunk], frame.get("content", [])),
        ]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(reversed(item))
                continue
            lines.append(item)
        return lines

    def _render_clip_wrapper(
        self,
        *,
        clip_id: str,
        payload: dict[str, Any],
        transform: tuple[float, float, float, float, float, float],
        content: list[_SvgRenderChunk],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[_SvgRenderChunk]:
        x1 = float(payload.get("x1", 0) or 0.0)
        y1 = float(payload.get("y1", 0) or 0.0)
        x2 = float(payload.get("x2", 0) or 0.0)
        y2 = float(payload.get("y2", 0) or 0.0)
        p1 = _to_svg_point(
            *_apply_affine(transform, x1, y1),
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        p2 = _to_svg_point(
            *_apply_affine(transform, x2, y2),
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        clip_x = min(p1[0], p2[0])
        clip_y = min(p1[1], p2[1])
        clip_width = abs(p2[0] - p1[0])
        clip_height = abs(p2[1] - p1[1])
        return [
            f'<g> <clipPath id="{html.escape(clip_id, quote=True)}"> '
            f'<rect x="{_fmt_num(clip_x)}" y="{_fmt_num(clip_y)}" '
            f'width="{_fmt_num(clip_width)}" height="{_fmt_num(clip_height)}"/>'
            f"</clipPath>",
            content,
            "</g>",
        ]

    def _render_rounded_rectangle(
        self,
        payload: dict[str, Any],
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        x1 = float(payload.get("x1", 0) or 0.0)
        y1 = float(payload.get("y1", 0) or 0.0)
        x2 = float(payload.get("x2", 0) or 0.0)
        y2 = float(payload.get("y2", 0) or 0.0)
        corners = [
            _to_svg_point(
                *_apply_affine(transform, x1, y1),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x2, y1),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x2, y2),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x1, y2),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
        ]
        min_x = min(p[0] for p in corners)
        max_x = max(p[0] for p in corners)
        min_y = min(p[1] for p in corners)
        max_y = max(p[1] for p in corners)
        width = max_x - min_x
        height = max_y - min_y
        rx_units = float(payload.get("corner_x_radius", 0) or 0.0)
        ry_units = float(payload.get("corner_y_radius", 0) or 0.0)
        rx = rx_units / units_per_px
        ry = ry_units / units_per_px
        if self.options.text_mode == "native_svg_export":
            rx = min(rx, width / 2.0)
            ry = min(ry, height / 2.0)

        attrs = [
            f'x = "{_fmt_num(min_x)}"',
            f'y="{_fmt_num(min_y)}"',
            f'width="{_fmt_num(width)}"',
            f'height="{_fmt_num(height)}"',
        ]
        if rx > 0.0:
            attrs.append(f'rx="{_fmt_num(rx)}"')
        if ry > 0.0:
            attrs.append(f'ry="{_fmt_num(ry)}"')

        brush = payload.get("brush")
        pen = payload.get("pen")
        if brush is not None:
            attrs.append(f'fill="{brush.get("color_hex", "#000000")}"')
            brush_color_raw = int(brush.get("color_raw", -1))
            brush_alpha = (
                (brush_color_raw >> 24) & 0xFF
                if brush_color_raw >= 0
                else ((brush_color_raw + (1 << 32)) >> 24) & 0xFF
            )
            if brush_alpha >= 0xFF:
                attrs.append('fill-opacity="1"')
            else:
                attrs.append(f'fill-opacity="{brush_alpha / 255.0}"')

        if pen is not None:
            attrs.append(f'stroke="{pen.get("color_hex", "#000000")}"')
            alpha = _style_alpha(pen)
            if alpha is not None and alpha < 0xFF:
                attrs.append(f'stroke-opacity="{alpha / 255.0}"')
            stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
            attrs.append(f'stroke-width="{_fmt_num(stroke_width)}px"')
            dasharray = _pen_dasharray(pen, stroke_width, native=self._native_dash)
            if dasharray:
                attrs.append(f'stroke-dasharray="{dasharray}"')
            if stroke_width <= 0.5 + 1e-9:
                attrs.append('vector-effect="non-scaling-stroke"')
        return [f"<rect {' '.join(attrs)}/>"]

    def _render_lines(
        self,
        payload: dict[str, Any],
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        points = payload.get("points") or []
        svg_points: list[tuple[float, float]] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x, y = _apply_affine(transform, float(point[0]), float(point[1]))
            svg_points.append(
                _to_svg_point(
                    x,
                    y,
                    units_per_px=units_per_px,
                    canvas_height_px=canvas_height_px,
                )
            )
        if len(svg_points) < 2:
            return []

        pen = payload.get("pen") or {}
        stroke = pen.get("color_hex", "#000000")
        stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
        stroke_opacity = _stroke_opacity_attr(pen)
        dasharray = _pen_dasharray(pen, stroke_width, native=self._native_dash)
        dash_attr = f' stroke-dasharray="{dasharray}"' if dasharray else ""
        vector_effect = (
            ' vector-effect="non-scaling-stroke"' if stroke_width <= 0.5 + 1e-9 else ""
        )
        if len(svg_points) == 2:
            (x1, y1), (x2, y2) = svg_points
            return [
                f'<line x1="{_fmt_num(x1)}" y1="{_fmt_num(y1)}" '
                f'x2="{_fmt_num(x2)}" y2="{_fmt_num(y2)}" '
                f'stroke="{stroke}" stroke-width="{_fmt_num(stroke_width)}px"'
                f"{stroke_opacity}{dash_attr}{vector_effect}/>"
            ]

        rendered: list[str] = []
        for start, end in zip(svg_points, svg_points[1:], strict=False):
            rendered.append(
                f'<line x1="{_fmt_num(start[0])}" y1="{_fmt_num(start[1])}" '
                f'x2="{_fmt_num(end[0])}" y2="{_fmt_num(end[1])}" '
                f'stroke="{stroke}" stroke-width="{_fmt_num(stroke_width)}px"'
                f"{stroke_opacity}{dash_attr}{vector_effect}/>"
            )
        return rendered

    def _render_arc(
        self,
        payload: dict[str, Any],
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        center_x, center_y = _apply_affine(
            transform,
            float(payload.get("center_x", 0) or 0.0),
            float(payload.get("center_y", 0) or 0.0),
        )
        svg_cx, svg_cy = _to_svg_point(
            center_x,
            center_y,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        rx = float(payload.get("width", 0) or 0.0) / units_per_px / 2.0
        ry = float(payload.get("height", 0) or 0.0) / units_per_px / 2.0
        start_angle = -float(payload.get("start_angle", 0) or 0.0)
        end_angle = -float(payload.get("end_angle", 0) or 0.0)

        pen = payload.get("pen") or {}
        stroke = pen.get("color_hex", "#000000")
        stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
        stroke_alpha = _style_alpha(pen)
        stroke_opacity = (
            stroke_alpha / 255.0
            if stroke_alpha is not None and stroke_alpha < 0xFF
            else None
        )
        vector_effect = "non-scaling-stroke" if stroke_width <= 0.5 + 1e-9 else None
        dasharray = _pen_dasharray(pen, stroke_width, native=self._native_dash)

        angle_diff = end_angle - start_angle
        if angle_diff < 0:
            angle_diff += 360.0
        is_full_circle = abs(angle_diff) <= 1e-9 or abs(angle_diff - 360.0) <= 1e-9

        if is_full_circle:
            return [
                svg_ellipse(
                    svg_cx,
                    svg_cy,
                    rx,
                    ry,
                    stroke=stroke,
                    stroke_width=stroke_width,
                    fill=None,
                    stroke_opacity=stroke_opacity,
                    stroke_dasharray=dasharray,
                    vector_effect=vector_effect,
                )
            ]

        return [
            svg_arc(
                svg_cx,
                svg_cy,
                rx,
                ry,
                start_angle,
                end_angle,
                stroke=stroke,
                stroke_width=stroke_width,
                fill=None,
                stroke_opacity=stroke_opacity,
                stroke_dasharray=dasharray,
                vector_effect=vector_effect,
            )
        ]

    def _render_polygons(
        self,
        payload: dict[str, Any],
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
        record_kind: str = "",
        op_index: int = -1,
        suppress_transparent_back: bool = False,
        transparent_back_only: bool = False,
    ) -> list[str]:
        polygons = payload.get("polygons") or []
        brush = payload.get("brush")
        pen = payload.get("pen")
        rendered: list[str] = []
        brush_alpha = self._polygon_brush_alpha(brush)
        portable_pattern = self._portable_pattern_definition(
            payload.get("portable_pattern"),
            transform=transform,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        if portable_pattern is not None:
            rendered.append(portable_pattern[0])
        native_transparent_back_kind, transparent_back_fill = (
            self._polygon_transparent_back_state(
                record_kind,
                brush=brush,
                pen=pen,
                op_index=op_index,
                transparent_back=payload.get("transparent_back"),
            )
        )

        for polygon in polygons:
            svg_points = self._polygon_svg_points(
                polygon,
                transform=transform,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            if len(svg_points) < 3:
                continue

            points_attr = " ".join(
                f"{_fmt_num(x)},{_fmt_num(y)}" for x, y in svg_points
            )
            attrs = self._polygon_attrs(
                points_attr,
                brush=brush,
                pen=pen,
                brush_alpha=brush_alpha,
                native_transparent_back_kind=native_transparent_back_kind,
                transparent_back_fill=transparent_back_fill,
                suppress_transparent_back=suppress_transparent_back,
                transparent_back_only=transparent_back_only,
                units_per_px=units_per_px,
                pattern_fill=(
                    None if portable_pattern is None else portable_pattern[1]
                ),
                pattern_opacity=(
                    None if portable_pattern is None else portable_pattern[2]
                ),
            )
            if attrs is None:
                continue
            rendered.append(f"<polygon {' '.join(attrs)}/>")

        return rendered

    def _portable_pattern_definition(
        self,
        value: object,
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> tuple[str, str, float] | None:
        state = _portable_pattern_state(value, units_per_px=units_per_px)
        if state is None:
            return None
        origin_x, origin_y = _apply_affine(
            transform,
            state.origin_x,
            state.origin_y,
        )
        svg_origin_x, svg_origin_y = _to_svg_point(
            origin_x,
            origin_y,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        self._pattern_counter += 1
        pattern_id = f"HarnessCoveringPattern{self._pattern_counter}"
        path_attrs = [
            f'd="{html.escape(state.resource.path_data, quote=True)}"',
            f'fill="{state.color}"',
        ]
        if state.resource.fill_rule is not None:
            path_attrs.append(f'fill-rule="{state.resource.fill_rule}"')
        if state.resource.clip_rule is not None:
            path_attrs.append(f'clip-rule="{state.resource.clip_rule}"')
        definition = (
            f'<defs><pattern id="{pattern_id}" '
            f'x="{_fmt_num(svg_origin_x)}" y="{_fmt_num(svg_origin_y)}" '
            f'width="{_fmt_num(state.tile_width)}" '
            f'height="{_fmt_num(state.tile_height)}" '
            f'patternUnits="userSpaceOnUse" '
            f'viewBox="0 0 {state.resource.view_box_width} '
            f'{state.resource.view_box_height}" '
            f'preserveAspectRatio="none" '
            f'patternTransform="rotate({_fmt_num(state.screen_degrees)} '
            f'{_fmt_num(svg_origin_x)} {_fmt_num(svg_origin_y)})">'
            f"<path {' '.join(path_attrs)}/></pattern></defs>"
        )
        return definition, f"url(#{pattern_id})", state.opacity

    def _polygon_brush_alpha(self, brush: dict[str, Any] | None) -> int | None:
        if brush is None:
            return None
        brush_color_raw = int(brush.get("color_raw", -1))
        if brush_color_raw >= 0:
            return (brush_color_raw >> 24) & 0xFF
        return ((brush_color_raw + (1 << 32)) >> 24) & 0xFF

    def _polygon_transparent_back_state(
        self,
        record_kind: str,
        *,
        brush: dict[str, Any] | None,
        pen: dict[str, Any] | None,
        op_index: int,
        transparent_back: object = None,
    ) -> tuple[bool, bool]:
        native_transparent_back_kind = record_kind in {"compilemask", "blanket"}
        # Explicit routing overrides the legacy kind/position heuristic.
        if transparent_back is True:
            return True, True
        if transparent_back is False:
            return native_transparent_back_kind, False
        transparent_back_fill = False
        if native_transparent_back_kind and brush is not None and pen is None:
            if record_kind == "compilemask":
                # Native compile-mask SVG splits only the body fill into the
                # TransparentBackSuffix group. The collapse triangle fill stays
                # in the main record group.
                transparent_back_fill = op_index == 1
            else:
                transparent_back_fill = True
        return native_transparent_back_kind, transparent_back_fill

    def _polygon_svg_points(
        self,
        polygon: object,
        *,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[tuple[float, float]]:
        raw_points = polygon.get("points") if isinstance(polygon, dict) else None
        if not isinstance(raw_points, list):
            return []
        svg_points: list[tuple[float, float]] = []
        for point in raw_points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            x, y = _apply_affine(transform, float(point[0]), float(point[1]))
            svg_points.append(
                _to_svg_point(
                    x,
                    y,
                    units_per_px=units_per_px,
                    canvas_height_px=canvas_height_px,
                )
            )
        return svg_points

    def _polygon_attrs(
        self,
        points_attr: str,
        *,
        brush: dict[str, Any] | None,
        pen: dict[str, Any] | None,
        brush_alpha: int | None,
        native_transparent_back_kind: bool,
        transparent_back_fill: bool,
        suppress_transparent_back: bool,
        transparent_back_only: bool,
        units_per_px: float,
        pattern_fill: str | None,
        pattern_opacity: float | None,
    ) -> list[str] | None:
        attrs = [f'points="{points_attr}"']
        if transparent_back_only:
            if (
                brush is None
                or not native_transparent_back_kind
                or not transparent_back_fill
            ):
                return None
            attrs.append(f'fill="{brush.get("color_hex", "#000000")}"')
            if brush_alpha is not None:
                attrs.append(f'fill-opacity="{brush_alpha / 255.0}"')
            return attrs

        fill_attrs = self._polygon_fill_attrs(
            brush=brush,
            brush_alpha=brush_alpha,
            native_transparent_back_kind=native_transparent_back_kind,
            transparent_back_fill=transparent_back_fill,
            suppress_transparent_back=suppress_transparent_back,
            pen=pen,
            pattern_fill=pattern_fill,
            pattern_opacity=pattern_opacity,
        )
        if fill_attrs is None:
            return None
        attrs.extend(fill_attrs)

        if pen is not None:
            attrs.extend(self._polygon_stroke_attrs(pen, units_per_px=units_per_px))
        elif brush is None:
            attrs.append('fill="none"')
        return attrs

    def _polygon_fill_attrs(
        self,
        *,
        brush: dict[str, Any] | None,
        brush_alpha: int | None,
        native_transparent_back_kind: bool,
        transparent_back_fill: bool,
        suppress_transparent_back: bool,
        pen: dict[str, Any] | None,
        pattern_fill: str | None,
        pattern_opacity: float | None,
    ) -> list[str] | None:
        if brush is None:
            return ['fill="none"']
        if (
            native_transparent_back_kind
            and suppress_transparent_back
            and pen is None
            and transparent_back_fill
        ):
            return None
        if (
            native_transparent_back_kind
            and suppress_transparent_back
            and transparent_back_fill
        ):
            return ['fill="none"']

        if pattern_fill is not None:
            attrs = [f'fill="{pattern_fill}"']
            if pattern_opacity is not None:
                attrs.append(f'fill-opacity="{pattern_opacity}"')
            return attrs
        attrs = [f'fill="{brush.get("color_hex", "#000000")}"']
        if brush_alpha is None:
            return attrs
        if brush_alpha >= 0xFF:
            attrs.append('fill-opacity="1"')
        else:
            attrs.append(f'fill-opacity="{brush_alpha / 255.0}"')
        return attrs

    def _polygon_stroke_attrs(
        self,
        pen: dict[str, Any],
        *,
        units_per_px: float,
    ) -> list[str]:
        attrs = [f'stroke="{pen.get("color_hex", "#000000")}"']
        alpha = _style_alpha(pen)
        if alpha is not None and alpha < 0xFF:
            attrs.append(f'stroke-opacity="{alpha / 255.0}"')
        stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
        attrs.append(f'stroke-width="{_fmt_num(stroke_width)}px"')
        dasharray = _pen_dasharray(pen, stroke_width, native=self._native_dash)
        if dasharray:
            attrs.append(f'stroke-dasharray="{dasharray}"')
        if stroke_width <= 0.5 + 1e-9:
            attrs.append('vector-effect="non-scaling-stroke"')
        return attrs

    def _render_string(
        self,
        payload: dict[str, Any],
        *,
        document: SchGeometryDocument,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
        record_kind: str,
        clip_path: str | None = None,
    ) -> list[str]:
        x, y = _apply_affine(
            transform,
            float(payload.get("x", 0) or 0.0),
            float(payload.get("y", 0) or 0.0),
        )
        svg_x, svg_y = _to_svg_point(
            x,
            y,
            units_per_px=units_per_px,
            canvas_height_px=canvas_height_px,
        )
        font = payload.get("font") or {}
        brush = payload.get("brush") or {}
        raw_font_size_px = float(font.get("size", 0) or 0.0) / units_per_px
        baseline_font_size = float(int(raw_font_size_px))
        if self.options.text_mode == "native_svg_export":
            font_size = float(int(raw_font_size_px))
        else:
            font_size = raw_font_size_px
        rotation = float(font.get("rotation", 0) or 0.0)
        if record_kind == "sheet":
            baseline_step = font_size
        else:
            baseline_step = baseline_font_size

        theta = math.radians(rotation)
        baseline_x = svg_x - baseline_step * math.sin(theta)
        baseline_y = svg_y + baseline_step * math.cos(theta)

        transform_attr = None
        if abs(rotation) > 1e-9:
            rotation_coord_formatter = (
                _fmt_native_text_rotation_coord
                if self.options.text_mode == "native_svg_export"
                else _fmt_num
            )
            transform_attr = (
                f"rotate({_fmt_num(rotation)} "
                f"{rotation_coord_formatter(baseline_x)} "
                f"{rotation_coord_formatter(baseline_y)})"
            )

        raw_text = str(payload.get("text", ""))
        if self.options.text_mode != "native_svg_export":
            raw_text = raw_text.rstrip("\r\n")
        text = html.escape(raw_text)
        resolved_font_family, font_resolution = self._resolve_svg_font(font)
        if self.options.text_as_polygons:
            poly_ctx = self._build_text_render_context(document)
            return [
                svg_text_or_poly(
                    poly_ctx,
                    baseline_x,
                    baseline_y,
                    raw_text,
                    font_size=font_size,
                    font_family=resolved_font_family,
                    fill=str(brush.get("color_hex", "#000000")),
                    transform=transform_attr,
                    font_weight="bold" if font.get("bold") else None,
                    font_style="italic" if font.get("italic") else None,
                    clip_path=clip_path,
                    xml_space="preserve",
                )
            ]

        if raw_text and font_resolution is not None:
            bundled_face = self._bundled_font_face_key(font_resolution.to_dict())
            if bundled_face is not None:
                self._painted_bundled_font_faces.add(bundled_face)

        coord_formatter = (
            _fmt_native_text_coord
            if self.options.text_mode == "native_svg_export"
            else _fmt_num
        )
        attrs = [
            f'x="{coord_formatter(baseline_x)}"',
            f'y="{coord_formatter(baseline_y)}"',
            f'font-size="{_fmt_num(font_size)}px"',
            f'font-family="{html.escape(resolved_font_family)}"',
            f'fill="{brush.get("color_hex", "#000000")}"',
        ]
        if font.get("bold"):
            attrs.append('font-weight="bold"')
        if font.get("italic"):
            attrs.append('font-style="italic"')
        if font.get("underline"):
            attrs.append('text-decoration="underline"')
        attrs.append('xml:space="preserve"')
        if clip_path is not None:
            attrs.append(f'clip-path="{html.escape(clip_path, quote=True)}"')
        if transform_attr is not None:
            attrs.append(f'transform="{transform_attr}"')
        return [f"<text {' '.join(attrs)}>{text}</text>"]

    def _render_image(
        self,
        payload: dict[str, Any],
        *,
        record_unique_id: str,
        document: SchGeometryDocument,
        transform: tuple[float, float, float, float, float, float],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
        if self.options.text_mode == "native_svg_export":
            hidden_image_ids = document.extras.get("native_svg_hidden_image_ids", [])
            if record_unique_id in set(
                hidden_image_ids if isinstance(hidden_image_ids, list) else []
            ):
                return []
        x1 = float(payload.get("dest_x1", 0) or 0.0)
        y1 = float(payload.get("dest_y1", 0) or 0.0)
        x2 = float(payload.get("dest_x2", 0) or 0.0)
        y2 = float(payload.get("dest_y2", 0) or 0.0)
        corners = [
            _to_svg_point(
                *_apply_affine(transform, x1, y1),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x2, y1),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x2, y2),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
            _to_svg_point(
                *_apply_affine(transform, x1, y2),
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            ),
        ]
        min_x = min(point[0] for point in corners)
        max_x = max(point[0] for point in corners)
        min_y = min(point[1] for point in corners)
        max_y = max(point[1] for point in corners)
        href = self._resolve_image_href(
            document=document,
            record_unique_id=record_unique_id,
        )
        return [
            f'<image x = "{_fmt_num(min_x)}" y="{_fmt_num(min_y)}" '
            f'width="{_fmt_num(max_x - min_x)}" height="{_fmt_num(max_y - min_y)}" '
            f'xlink:href="{html.escape(href, quote=True)}"/>'
        ]
