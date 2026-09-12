"""Prepared alternate-symbol selection for component primitive rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_record_types import SchRecordType

if TYPE_CHECKING:
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_sch_geometry_oracle import SchGeometryOp
    from .altium_sch_svg_renderer import SchSvgRenderContext


_KEEP_ORIGINAL = frozenset(
    (
        SchRecordType.DESIGNATOR,
        SchRecordType.PARAMETER,
        SchRecordType.PARAMETER_SET,
        SchRecordType.SHEET_NAME,
        SchRecordType.FILE_NAME,
    )
)


@dataclass(frozen=True, slots=True)
class _ComponentVariantCapture:
    """Already-placed alternate and its owner-document rendering context.

    The caller owns library resolution and UpdateVariantComponent transforms;
    this capture never moves or rebinds either source tree.
    """

    source: AltiumSchComponent
    has_variant_component: bool
    alternate: AltiumSchComponent | None
    is_multi_variant_export: bool
    document_show_alternate_symbols: bool
    project_show_alternate_symbols: bool
    alternate_context: SchSvgRenderContext | None = None
    max_source_references: int = 100_000
    max_parameter_characters: int = 1_000_000
    max_sort_work: int = 1_000_000


def _validate_variant_capture(
    source: AltiumSchComponent, capture: _ComponentVariantCapture
) -> None:
    if capture.source is not source:
        raise ValueError("component variant capture has a different source")
    for value in (
        capture.max_source_references,
        capture.max_parameter_characters,
        capture.max_sort_work,
    ):
        if type(value) is not int or value < 0:
            raise ValueError("component variant limits must be nonnegative integers")
    owners = _variant_source_owners(source, capture)
    # Charge all source categories before existing traversal helpers allocate
    # their temporary lists, including legacy category-only components.
    count = sum(
        len(owner.children)
        + len(owner.graphics)
        + len(owner.pins)
        + len(owner.parameters)
        for owner in owners
    )
    if count > capture.max_source_references:
        raise ValueError("component variant source reference limit exceeded")


def _variant_source_owners(
    source: AltiumSchComponent, capture: _ComponentVariantCapture
) -> tuple[AltiumSchComponent, ...]:
    from .altium_record_sch__component import AltiumSchComponent

    owners = (source,)
    if capture.has_variant_component and capture.alternate is not None:
        owners += (capture.alternate,)
    if any(type(owner) is not AltiumSchComponent for owner in owners):
        raise NotImplementedError("variant capture requires ordinary component records")
    return owners


def _shows_alternate(capture: _ComponentVariantCapture) -> bool:
    show_symbols = (
        capture.project_show_alternate_symbols
        if capture.is_multi_variant_export
        else capture.document_show_alternate_symbols
    )
    return (
        capture.has_variant_component and capture.alternate is not None and show_symbols
    )


def _alternate_parameter_names(
    capture: _ComponentVariantCapture, original: list[tuple[str, object]]
) -> set[str]:
    from .altium_sch_paint_order import _component_bound_children

    alternate = capture.alternate
    if alternate is None:
        return set()
    remaining = capture.max_parameter_characters
    for _, child in original:
        if getattr(child, "record_type", None) == SchRecordType.PARAMETER:
            remaining -= len(str(getattr(child, "name", "")))
    if remaining < 0:
        raise ValueError("component variant parameter character limit exceeded")
    names: set[str] = set()
    candidates = list(alternate.children) or list(alternate.parameters)
    for parameter in _component_bound_children(alternate, candidates):
        if getattr(parameter, "record_type", None) != SchRecordType.PARAMETER:
            continue
        name = str(getattr(parameter, "name", ""))
        if len(name) > remaining:
            raise ValueError("component variant parameter character limit exceeded")
        remaining -= len(name)
        names.add(dotnet_ordinal_ignore_case_key(name))
    return names


def _original_variant_children(
    source: AltiumSchComponent,
    capture: _ComponentVariantCapture,
    show_alternate: bool,
    ctx: SchSvgRenderContext,
) -> list[tuple[str, object]]:
    children = source._ordered_geometry_children(
        sort_transparency=not show_alternate,
        record_filter=_KEEP_ORIGINAL if show_alternate else None,
        max_sort_work=capture.max_sort_work,
        source_admission=ctx._source_admission,
    )
    if not capture.has_variant_component or capture.alternate is None:
        return children
    names = _alternate_parameter_names(capture, children)
    return [
        (kind, child)
        for kind, child in children
        if getattr(child, "record_type", None) != SchRecordType.PARAMETER
        or dotnet_ordinal_ignore_case_key(str(getattr(child, "name", ""))) in names
    ]


def _component_variant_geometry_operations(
    source: AltiumSchComponent,
    ctx: SchSvgRenderContext,
    capture: _ComponentVariantCapture,
    *,
    document_id: str,
    units_per_px: int,
) -> tuple[list[SchGeometryOp], list[SchGeometryOp]]:
    _validate_variant_capture(source, capture)
    alternate = capture.alternate
    show_alternate = _shows_alternate(capture)
    children = _original_variant_children(source, capture, show_alternate, ctx)
    alternate_operations: list[SchGeometryOp] = []
    if show_alternate and alternate is not None:
        if capture.alternate_context is None:
            raise NotImplementedError(
                "alternate symbol requires prepared owner context"
            )
        alternate_ctx = capture.alternate_context.with_color_overrides(
            area_color=ctx.area_color_override,
            line_color=ctx.line_color_override,
            pin_color=ctx.pin_color_override,
        )
        alternate_ctx._component_variant_capture = None
        alternate_ctx._component_overlay_capture = None
        _, alternate_operations = alternate._render_child_geometry_operations(
            alternate_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            children=alternate._ordered_geometry_children(
                exclude_records=_KEEP_ORIGINAL,
                max_sort_work=capture.max_sort_work,
                source_admission=alternate_ctx._source_admission,
            ),
            include_component_junctions=False,
        )
    component_operations, original_operations = (
        source._render_child_geometry_operations(
            ctx, document_id=document_id, units_per_px=units_per_px, children=children
        )
    )
    return component_operations, [*alternate_operations, *original_operations]


def _component_variant_junction_operations(
    source: AltiumSchComponent,
    ctx: SchSvgRenderContext,
    capture: _ComponentVariantCapture,
    *,
    document_id: str,
    units_per_px: int,
) -> list[SchGeometryOp]:
    if not (
        _shows_alternate(capture) and source.part_count > 1 and ctx.native_svg_export
    ):
        return []
    # DrawGroup calls DrawJunctions on the original component after InternalDraw,
    # even when alternate primitives replaced its pins. Reuse the native-export
    # hotspot shim; never hoist the alternate component's junction copies.
    operations: list[SchGeometryOp] = []
    for kind, pin in source._ordered_geometry_children(
        sort_transparency=False,
        record_filter=frozenset((SchRecordType.PIN,)),
        source_admission=ctx._source_admission,
    ):
        geometry = source._child_geometry_operations(
            kind, pin, ctx, document_id=document_id, units_per_px=units_per_px
        )
        operations.extend(
            source._native_export_component_junction_ops(
                pin, geometry, ctx, units_per_px=units_per_px
            )
        )
    return operations
