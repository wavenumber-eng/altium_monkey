from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import Any

from .altium_sch_geometry_oracle import SchGeometryDocument, SchGeometryOpKind
from .altium_sch_svg_renderer import (
    SchCompileMaskRenderMode,
    SchSvgRenderContext,
    SchSvgRenderOptions,
    build_compile_mask_visual_overlay_svg,
    svg_arc,
    svg_ellipse,
    svg_text_or_poly,
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


class SchGeometrySvgRenderer:
    """Pure geometry-to-SVG renderer for schematic geometry documents."""

    def __init__(self, options: SchGeometrySvgRenderOptions | None = None) -> None:
        self.options = options or SchGeometrySvgRenderOptions()
        self._clip_rect_counter = 0

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
    ) -> tuple[object | None, set[str]]:
        sheet_record = next(
            (record for record in document.records if record.kind == "sheet"), None
        )
        all_record_ids = {
            str(getattr(record, "unique_id", "") or "")
            for record in document.records
            if str(getattr(record, "unique_id", "") or "")
        }
        nested_record_ids: set[str] = set()
        for record in document.records:
            for op in record.operations:
                if op.kind_str() != SchGeometryOpKind.BEGIN_GROUP.value:
                    continue
                group_id = str(op.payload.get("parameters", "") or "")
                if group_id in {"", "DocumentMainGroup", record.unique_id}:
                    continue
                if group_id not in all_record_ids:
                    continue
                nested_record_ids.add(group_id)
        return sheet_record, nested_record_ids

    def _render_document_items_group(
        self,
        document: SchGeometryDocument,
        *,
        sheet_record: object | None,
        nested_record_ids: set[str],
        units_per_px: float,
        canvas_height_px: float,
        compiled_visual_masks: bool,
    ) -> tuple[list[str], list[str]]:
        lines: list[str] = ['<g id = "DocumentItemsGroup" >']
        foreground_compile_mask_records: list[str] = []
        transparent_back_elements: list[str] = []
        if self.options.text_mode == "native_svg_export":
            transparent_back_elements = self._render_transparent_back_elements(
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        if transparent_back_elements:
            lines.append('<g id = "TransparentBackGroup" >')
            lines.extend(transparent_back_elements)
            lines.append("</g>")

        for record in document.records:
            if record is sheet_record or record.unique_id in nested_record_ids:
                continue
            rendered_record = self._render_item_record(
                record,
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            if compiled_visual_masks and record.kind == "compilemask":
                foreground_compile_mask_records.extend(rendered_record)
            else:
                lines.extend(rendered_record)
        lines.append("</g>")
        return lines, foreground_compile_mask_records

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

    def render(self, document: SchGeometryDocument) -> str:
        self._clip_rect_counter = 0
        width_px, height_px, units_per_px, doc_id, workspace_bg = (
            self._resolve_render_frame(document)
        )

        lines: list[str] = []
        if self.options.include_xml_declaration:
            lines.append('<?xml version="1.0"  encoding="UTF-8" standalone="no"?>')
        lines.append(
            '<svg version="1.1" xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'stroke-linecap="round" stroke-linejoin="round" fill="none" '
            f'width="{_fmt_num(width_px)}" height="{_fmt_num(height_px)}" '
            f'data-doc-id="{html.escape(doc_id)}" data-doc-ver="2">'
        )
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

        sheet_record, nested_record_ids = self._collect_sheet_and_nested_record_ids(
            document
        )
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
            nested_record_ids=nested_record_ids,
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
        record: object,
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
                self.options.text_mode == "native_svg_export"
                and str(getattr(record, "kind", "") or "") in {"compilemask", "blanket"}
            ),
        )
        if not primitives:
            return []
        if record.unique_id:
            return [
                f'<g id = "{html.escape(record.unique_id)}" >',
                *primitives,
                "</g>",
            ]
        return primitives

    def _render_record_primitives(
        self,
        record: object,
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
                "content": [],
            }
        ]
        transform_stack: list[tuple[float, float, float, float, float, float]] = [
            _identity_affine()
        ]
        polygon_op_index = -1

        for op in record.operations:
            kind = op.kind_str()
            payload = op.payload

            if self._skip_record_op_for_transparent_back(
                kind, transparent_back_only=transparent_back_only
            ):
                continue

            if self._handle_record_stack_operation(
                kind,
                payload=payload,
                record=record,
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
        while len(rendered_stack) > 1:
            completed = rendered_stack.pop()
            rendered_stack[-1]["content"].extend(
                self._wrap_completed_frame(
                    completed,
                    units_per_px=units_per_px,
                    canvas_height_px=canvas_height_px,
                )
            )
        return list(rendered_stack[0]["content"])

    def _skip_record_op_for_transparent_back(
        self,
        kind: str,
        *,
        transparent_back_only: bool,
    ) -> bool:
        if not transparent_back_only:
            return False
        return kind not in {
            SchGeometryOpKind.PUSH_TRANSFORM.value,
            SchGeometryOpKind.POP_TRANSFORM.value,
            SchGeometryOpKind.POLYGONS.value,
        }

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
        rendered_stack[-1]["content"].extend(
            self._wrap_completed_frame(
                completed,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
        )

    def _handle_record_stack_operation(
        self,
        kind: str,
        *,
        payload: dict[str, Any],
        record: object,
        rendered_stack: list[dict[str, Any]],
        transform_stack: list[tuple[float, float, float, float, float, float]],
        units_per_px: float,
        canvas_height_px: float,
    ) -> bool:
        if kind == SchGeometryOpKind.PUSH_TRANSFORM.value:
            matrix = payload.get("matrix") or []
            if len(matrix) == 6:
                pushed = tuple(float(v) for v in matrix)
                transform_stack.append(_compose_affine(transform_stack[-1], pushed))
            return True
        if kind == SchGeometryOpKind.POP_TRANSFORM.value:
            if len(transform_stack) > 1:
                transform_stack.pop()
            return True
        if kind == SchGeometryOpKind.BEGIN_GROUP.value:
            group_id = str(payload.get("parameters", "") or "")
            if group_id in {
                "",
                "DocumentMainGroup",
                "DocumentItemsGroup",
                str(getattr(record, "unique_id", "") or ""),
            }:
                group_id = ""
            rendered_stack.append(
                {
                    "frame_type": "group",
                    "group_id": group_id,
                    "content": [],
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
        for record in document.records:
            record_kind = str(getattr(record, "kind", "") or "")
            if record_kind not in {"compilemask", "blanket"}:
                continue
            fill_only = self._render_record_transparent_back(
                record,
                document=document,
                units_per_px=units_per_px,
                canvas_height_px=canvas_height_px,
            )
            if not fill_only:
                continue
            group_id = str(getattr(record, "unique_id", "") or "")
            if group_id:
                group_id = f"{group_id}TransparentBackSuffix"
                rendered.append(f'<g id = "{html.escape(group_id)}" >')
                rendered.extend(fill_only)
                rendered.append("</g>")
            else:
                rendered.extend(fill_only)
        return rendered

    def _render_record_transparent_back(
        self,
        record: object,
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
    ) -> list[str]:
        frame_type = str(frame.get("frame_type", "group") or "group")
        content = list(frame.get("content", []))
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
            return [
                f'<g id = "{html.escape(group_id)}" >',
                *content,
                "</g>",
            ]
        return content

    def _render_clip_wrapper(
        self,
        *,
        clip_id: str,
        payload: dict[str, Any],
        transform: tuple[float, float, float, float, float, float],
        content: list[str],
        units_per_px: float,
        canvas_height_px: float,
    ) -> list[str]:
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
            *content,
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
            stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
            attrs.append(f'stroke-width="{_fmt_num(stroke_width)}px"')
            dash_style = str(pen.get("dash_style", "") or "")
            dash_map = {
                "pdsDash": "4",
                "pdsDot": "2",
                "pdsDashDot": "4 2",
            }
            dasharray = dash_map.get(dash_style)
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
        vector_effect = (
            ' vector-effect="non-scaling-stroke"' if stroke_width <= 0.5 + 1e-9 else ""
        )
        if len(svg_points) == 2:
            (x1, y1), (x2, y2) = svg_points
            return [
                f'<line x1="{_fmt_num(x1)}" y1="{_fmt_num(y1)}" '
                f'x2="{_fmt_num(x2)}" y2="{_fmt_num(y2)}" '
                f'stroke="{stroke}" stroke-width="{_fmt_num(stroke_width)}px"{vector_effect}/>'
            ]

        rendered: list[str] = []
        for start, end in zip(svg_points, svg_points[1:], strict=False):
            rendered.append(
                f'<line x1="{_fmt_num(start[0])}" y1="{_fmt_num(start[1])}" '
                f'x2="{_fmt_num(end[0])}" y2="{_fmt_num(end[1])}" '
                f'stroke="{stroke}" stroke-width="{_fmt_num(stroke_width)}px"{vector_effect}/>'
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
        vector_effect = "non-scaling-stroke" if stroke_width <= 0.5 + 1e-9 else None

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
        native_transparent_back_kind, transparent_back_fill = (
            self._polygon_transparent_back_state(
                record_kind,
                brush=brush,
                pen=pen,
                op_index=op_index,
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
            )
            if attrs is None:
                continue
            rendered.append(f"<polygon {' '.join(attrs)}/>")

        return rendered

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
    ) -> tuple[bool, bool]:
        native_transparent_back_kind = record_kind in {"compilemask", "blanket"}
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
        stroke_width = _pen_width_to_svg(pen, units_per_px=units_per_px)
        attrs.append(f'stroke-width="{_fmt_num(stroke_width)}px"')
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
            transform_attr = f"rotate({_fmt_num(rotation)} {_fmt_num(baseline_x)} {_fmt_num(baseline_y)})"

        raw_text = str(payload.get("text", ""))
        if self.options.text_mode != "native_svg_export":
            raw_text = raw_text.rstrip("\r\n")
        text = html.escape(raw_text)
        if self.options.text_as_polygons:
            poly_ctx = self._build_text_render_context(document)
            return [
                svg_text_or_poly(
                    poly_ctx,
                    baseline_x,
                    baseline_y,
                    raw_text,
                    font_size=font_size,
                    font_family=str(font.get("name", "")),
                    fill=str(brush.get("color_hex", "#000000")),
                    transform=transform_attr,
                    font_weight="bold" if font.get("bold") else None,
                    font_style="italic" if font.get("italic") else None,
                    clip_path=clip_path,
                    xml_space="preserve",
                )
            ]

        attrs = [
            f'x="{_fmt_num(baseline_x)}"',
            f'y="{_fmt_num(baseline_y)}"',
            f'font-size="{_fmt_num(font_size)}px"',
            f'font-family="{html.escape(str(font.get("name", "")))}"',
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
