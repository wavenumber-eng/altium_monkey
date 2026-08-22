"""Deterministic manufacturing output-layer walk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from altium_monkey.altium_pcb_layer_ref import PcbLayerFamily, PcbLayerRef
from altium_monkey.altium_resolved_layer_stack import (
    ResolvedLayer,
    ResolvedLayerStack,
    ResolvedSubstack,
)

from .units import pcb_mils_to_nm

ManufacturingLayerRole = Literal[
    "conductor", "solder_mask", "paste", "silkscreen", "mechanical"
]
ManufacturingLayerSide = Literal["top", "internal", "bottom", "both", "none"]

_AUTOMATIC_FAMILY_ROLES: dict[str, ManufacturingLayerRole] = {
    "copper": "conductor",
    "solder_mask": "solder_mask",
    "paste": "paste",
    "overlay": "silkscreen",
}
_REQUESTED_FAMILY_ROLES: dict[str, ManufacturingLayerRole] = {
    "mechanical": "mechanical",
}


@dataclass(frozen=True)
class ManufacturingLayerWalkError(ValueError):
    """Stable failure while selecting an exact manufacturing layer context."""

    code: str
    detail: str


@dataclass(frozen=True)
class ManufacturingLayerOccurrence:
    """One output-layer occurrence backed by the resolved stack authority."""

    id: str
    layer: ResolvedLayer
    source_stackup_ref: str
    board_region_stack_ref: str | None
    material_role: ManufacturingLayerRole
    side: ManufacturingLayerSide
    z_min_nm: int
    z_max_nm: int
    film_baseline: Literal["empty", "full", "profile"]


def walk_manufacturing_output_layers(
    layer_stack: ResolvedLayerStack,
    *,
    source_stackup_ref: str | None = None,
    board_region_stack_ref: str | None = None,
    requested_layer_refs: tuple[PcbLayerRef, ...] = (),
) -> tuple[ManufacturingLayerOccurrence, ...]:
    """Walk exact output layers for a board, substack, or board-region context."""

    if source_stackup_ref is not None and board_region_stack_ref is not None:
        raise ValueError("select either source_stackup_ref or board_region_stack_ref")
    contexts = _selected_contexts(
        layer_stack,
        source_stackup_ref=source_stackup_ref,
        board_region_stack_ref=board_region_stack_ref,
    )
    requested = frozenset(requested_layer_refs)
    occurrences: list[ManufacturingLayerOccurrence] = []
    for context in contexts:
        occurrences.extend(
            _walk_context(
                layer_stack,
                context,
                board_region_stack_ref=board_region_stack_ref,
                requested=requested,
            )
        )
    return tuple(occurrences)


def _selected_contexts(
    layer_stack: ResolvedLayerStack,
    *,
    source_stackup_ref: str | None,
    board_region_stack_ref: str | None,
) -> tuple[ResolvedSubstack, ...]:
    if board_region_stack_ref is not None:
        matches = tuple(
            substack
            for substack in layer_stack.substacks
            if substack.source_stackup_ref == board_region_stack_ref
        )
        return _require_single_context(
            matches, "board_region_stack_ref", board_region_stack_ref
        )
    if source_stackup_ref is not None:
        matches = tuple(
            substack
            for substack in layer_stack.substacks
            if substack.source_stackup_ref == source_stackup_ref
        )
        return _require_single_context(
            matches, "source_stackup_ref", source_stackup_ref
        )
    if layer_stack.substacks:
        return tuple(sorted(layer_stack.substacks, key=_substack_order))
    return (
        ResolvedSubstack(
            source_stackup_ref="",
            name="Whole board",
            layers=tuple(
                sorted(
                    (layer for layer in layer_stack.layers if layer.physical_row),
                    key=_layer_order,
                )
            ),
        ),
    )


def _require_single_context(
    matches: tuple[ResolvedSubstack, ...],
    field_name: str,
    value: str,
) -> tuple[ResolvedSubstack, ...]:
    if len(matches) == 1:
        return matches
    code = "unresolved_layer_context" if not matches else "corrupt_identity"
    raise ManufacturingLayerWalkError(
        code,
        f"{field_name} {value!r} resolved to {len(matches)} substacks",
    )


def _substack_order(substack: ResolvedSubstack) -> tuple[int, str]:
    return (
        0 if substack.used_by_primitives is True else 1,
        substack.source_stackup_ref,
    )


def _layer_order(layer: ResolvedLayer) -> tuple[int, str]:
    return (
        layer.stack_index if layer.stack_index is not None else 2**31 - 1,
        layer.layer_key,
    )


def _walk_context(
    layer_stack: ResolvedLayerStack,
    context: ResolvedSubstack,
    *,
    board_region_stack_ref: str | None,
    requested: frozenset[PcbLayerRef],
) -> list[ManufacturingLayerOccurrence]:
    ordered = sorted(context.layers, key=_layer_order)
    included_keys = {layer.layer_key for layer in ordered}
    requested_products = sorted(
        (
            layer
            for layer in layer_stack.layers
            if layer.layer_key not in included_keys
            and layer.layer_ref in requested
            and layer.family in _REQUESTED_FAMILY_ROLES
        ),
        key=_requested_product_order,
    )
    ordered.extend(requested_products)
    z_by_layer = _layer_z_envelopes(ordered)
    result: list[ManufacturingLayerOccurrence] = []
    for layer in ordered:
        occurrence = _walked_occurrence(
            layer,
            context=context,
            board_region_stack_ref=board_region_stack_ref,
            requested=requested,
            z_by_layer=z_by_layer,
        )
        if occurrence is not None:
            result.append(occurrence)
    return result


def _walked_occurrence(
    layer: ResolvedLayer,
    *,
    context: ResolvedSubstack,
    board_region_stack_ref: str | None,
    requested: frozenset[PcbLayerRef],
    z_by_layer: dict[str, tuple[int, int]],
) -> ManufacturingLayerOccurrence | None:
    role = _material_role(layer, requested)
    if role is None:
        return None
    if layer.layer_ref is None:
        raise ManufacturingLayerWalkError(
            "unresolved_layer",
            f"resolved layer {layer.layer_key!r} has no exact PcbLayerRef",
        )
    if layer.layer_ref.family == PcbLayerFamily.INTERNAL_PLANE:
        raise ManufacturingLayerWalkError(
            "unsupported_plane_film",
            f"plane layer {layer.layer_ref.token!r} requires an explicit material baseline",
        )
    z_min_nm, z_max_nm = z_by_layer[layer.layer_key]
    context_ref = context.source_stackup_ref or "whole_board"
    return ManufacturingLayerOccurrence(
        id=f"layer.{context_ref}.{layer.layer_key}",
        layer=layer,
        source_stackup_ref=context.source_stackup_ref,
        board_region_stack_ref=board_region_stack_ref,
        material_role=role,
        side=_layer_side(layer, role),
        z_min_nm=z_min_nm,
        z_max_nm=z_max_nm,
        film_baseline="empty",
    )


def _material_role(
    layer: ResolvedLayer,
    requested: frozenset[PcbLayerRef],
) -> ManufacturingLayerRole | None:
    role = _AUTOMATIC_FAMILY_ROLES.get(layer.family)
    if role is not None:
        return role
    if layer.layer_ref not in requested:
        return None
    return _REQUESTED_FAMILY_ROLES.get(layer.family)


def _requested_product_order(layer: ResolvedLayer) -> tuple[str, str, str]:
    if layer.layer_ref is None:
        raise ManufacturingLayerWalkError(
            "unresolved_layer",
            f"requested layer {layer.layer_key!r} has no exact PcbLayerRef",
        )
    return (layer.family, layer.layer_ref.token, layer.layer_key)


def _layer_side(
    layer: ResolvedLayer,
    role: ManufacturingLayerRole,
) -> ManufacturingLayerSide:
    if layer.side == "top":
        return "top"
    if layer.side == "internal":
        return "internal"
    if layer.side == "bottom":
        return "bottom"
    if layer.side == "both":
        return "both"
    if layer.side == "none":
        return "none"
    if role == "conductor":
        return "internal"
    return "none"


def _layer_z_envelopes(
    layers: list[ResolvedLayer],
) -> dict[str, tuple[int, int]]:
    total_mils = sum(max(layer.thickness_mils, 0.0) for layer in layers)
    cursor_mils = total_mils / 2.0
    envelopes: dict[str, tuple[int, int]] = {}
    for layer in layers:
        top_mils = cursor_mils
        cursor_mils -= max(layer.thickness_mils, 0.0)
        top_nm = pcb_mils_to_nm(top_mils)
        bottom_nm = pcb_mils_to_nm(cursor_mils)
        envelopes[layer.layer_key] = (min(top_nm, bottom_nm), max(top_nm, bottom_nm))
    return envelopes


__all__ = (
    "ManufacturingLayerOccurrence",
    "ManufacturingLayerWalkError",
    "walk_manufacturing_output_layers",
)
