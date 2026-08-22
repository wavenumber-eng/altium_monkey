"""Resolve an exact PolygonConnect winner without lowering polygon geometry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Literal

from altium_monkey.altium_pcb_rule import AltiumPcbRule

from .binary_rule_query import build_manufacturing_binary_rule_query
from .generated import LocatedSource, SourceProvenance, UnresolvedSource
from .output_layers import ManufacturingLayerOccurrence
from .resolved_inputs import (
    ResolvedNetInput,
    ResolvedPadInput,
    ResolvedPolygonConnectRuleAuthority,
    ResolvedPolygonConnectRuleCandidateAuthority,
    ResolvedPolygonConnectRuleSelection,
    ResolvedPolygonConnectSettingsAuthority,
    ResolvedPolygonInput,
    ResolvedViaInput,
    resolve_pcb_polygon_connect_rule_authority,
)
from .rule_query_authority import ManufacturingRuleQueryAuthority
from .rule_resolution import (
    ManufacturingBinaryRuleQuery,
    PcbRuleResolutionError,
    select_manufacturing_binary_rule,
)
from .source_provenance import PcbDocSourceIndex, source_occurrence_ref

if TYPE_CHECKING:
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc


def resolve_pcb_polygon_connect_rule_selection(
    pcbdoc: AltiumPcbDoc,
    primitive: ResolvedPadInput | ResolvedViaInput,
    polygon: ResolvedPolygonInput,
    *,
    authority: ResolvedPolygonConnectRuleAuthority,
    source_index: PcbDocSourceIndex,
    layer_occurrences: Iterable[ManufacturingLayerOccurrence],
    nets: Iterable[ResolvedNetInput],
    source_authority: ManufacturingRuleQueryAuthority | None = None,
) -> ResolvedPolygonConnectRuleSelection | None:
    """Select exact source rule/settings for one eligible primitive and polygon."""

    if not isinstance(primitive, (ResolvedPadInput, ResolvedViaInput)):
        raise PcbRuleResolutionError(
            "unsupported_polygon_connect_primitive",
            f"PolygonConnect does not support {type(primitive).__name__}",
        )
    _validate_polygon_peer_batch_sources(
        primitive,
        polygon,
        source_index=source_index,
        source_authority=source_authority,
    )
    query = build_manufacturing_binary_rule_query(
        primitive,
        polygon,
        layer_occurrences=layer_occurrences,
        nets=nets,
        pcbdoc=pcbdoc,
        source_index=source_index,
        source_authority=source_authority,
    )
    primitive_kind = _polygon_connect_primitive_kind(query)
    exact_authority, candidate_rules, captured_refs = _polygon_rule_batch(
        pcbdoc,
        source_index=source_index,
        source_authority=source_authority,
    )
    if authority != exact_authority:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "PolygonConnect authority does not match the current source revision",
        )
    winner = select_manufacturing_binary_rule(
        candidate_rules,
        rule_kind="PolygonConnect",
        query=query,
    )
    if winner is None:
        return None
    winner_ref = _polygon_winner_ref(winner, captured_refs, source_index)
    matches = tuple(rule for rule in authority.rules if rule.id == winner_ref)
    if len(matches) != 1:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"PolygonConnect winner {winner_ref!r} has {len(matches)} authority rows",
        )
    selected, selected_from = _select_polygon_connect_settings(
        matches[0], primitive_kind
    )
    return ResolvedPolygonConnectRuleSelection(
        query=query,
        rule=matches[0],
        primitive_kind=primitive_kind,
        settings=selected,
        settings_selected_from=selected_from,
    )


def _validate_polygon_peer_batch_sources(
    primitive: ResolvedPadInput | ResolvedViaInput,
    polygon: ResolvedPolygonInput,
    *,
    source_index: PcbDocSourceIndex,
    source_authority: ManufacturingRuleQueryAuthority | None,
) -> None:
    if source_authority is not None:
        return
    source_index.assert_current()
    primitive_source_kind = "pad" if isinstance(primitive, ResolvedPadInput) else "via"
    _validate_peer_source(
        source_index,
        primitive_source_kind,
        primitive.id,
        primitive.source,
        expected_stream=f"{primitive_source_kind.title()}s6/Data",
    )
    _validate_peer_source(
        source_index,
        "polygon",
        polygon.id,
        polygon.source,
        expected_stream="Polygons6/Data",
    )


def _polygon_rule_batch(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    source_authority: ManufacturingRuleQueryAuthority | None,
) -> tuple[
    ResolvedPolygonConnectRuleAuthority,
    tuple[AltiumPcbRule, ...],
    dict[int, str] | None,
]:
    if source_authority is None:
        exact = resolve_pcb_polygon_connect_rule_authority(
            pcbdoc,
            source_index=source_index,
            strictness="strict",
        )
        return exact, tuple(pcbdoc.rules), None
    exact = source_authority.polygon_connect_authority
    if exact is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "PolygonConnect authority is absent from the source batch",
        )
    captured = source_authority._polygon_rules()
    rules = tuple(rule for rule, _ref in captured)
    return exact, rules, {id(rule): ref for rule, ref in captured}


def _polygon_winner_ref(
    winner: AltiumPcbRule,
    captured_refs: dict[int, str] | None,
    source_index: PcbDocSourceIndex,
) -> str:
    if captured_refs is None:
        winner_source = source_index.source_for(winner)
        return source_occurrence_ref("rule", winner_source)
    try:
        return captured_refs[id(winner)]
    except KeyError as exc:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "PolygonConnect winner is outside captured batch authority",
        ) from exc


def _polygon_connect_primitive_kind(
    query: ManufacturingBinaryRuleQuery,
) -> Literal["th_pad", "smd_pad", "via"]:
    kinds = (query.first.object_kind, query.second.object_kind)
    if kinds.count("polygon") != 1:
        raise PcbRuleResolutionError(
            "unsupported_polygon_connect_primitive",
            "PolygonConnect selection requires exactly one polygon occurrence",
        )
    peer_kind = kinds[1] if kinds[0] == "polygon" else kinds[0]
    if peer_kind == "th_pad":
        return "th_pad"
    if peer_kind == "smd_pad":
        return "smd_pad"
    if peer_kind == "via":
        return "via"
    raise PcbRuleResolutionError(
        "unsupported_polygon_connect_primitive",
        f"PolygonConnect does not support peer kind {peer_kind!r}",
    )


def _select_polygon_connect_settings(
    rule: ResolvedPolygonConnectRuleCandidateAuthority,
    primitive_kind: Literal["th_pad", "smd_pad", "via"],
) -> tuple[
    ResolvedPolygonConnectSettingsAuthority,
    Literal["exact_primitive", "default_inheritance"],
]:
    _validate_rule_identity(rule)
    exact = tuple(
        settings
        for settings in rule.settings
        if settings.primitive_kind == primitive_kind
    )
    if len(exact) > 1:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"{rule.id} has duplicate {primitive_kind} settings blocks",
        )
    if exact:
        return exact[0], "exact_primitive"
    defaults = tuple(
        settings for settings in rule.settings if settings.primitive_kind == "default"
    )
    if len(defaults) != 1:
        raise PcbRuleResolutionError(
            "missing_polygon_connect_settings",
            f"{rule.id} has no unique {primitive_kind} or DEFAULT settings block",
        )
    return defaults[0], "default_inheritance"


def _validate_rule_identity(rule: ResolvedPolygonConnectRuleCandidateAuthority) -> None:
    if isinstance(rule.source, UnresolvedSource):
        raise PcbRuleResolutionError(
            "unresolved_source_identity",
            f"{rule.id} has no exact source rule identity",
        )
    expected = source_occurrence_ref("rule", rule.source)
    if rule.id != expected:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"PolygonConnect rule {rule.id!r} does not match source occurrence {expected!r}",
        )


def _validate_peer_source(
    source_index: PcbDocSourceIndex,
    kind: Literal["pad", "via", "polygon"],
    occurrence_ref: str,
    source: SourceProvenance,
    *,
    expected_stream: str,
) -> None:
    if not isinstance(source, LocatedSource):
        raise PcbRuleResolutionError(
            "unresolved_source_identity",
            f"{occurrence_ref} has no exact file-backed source identity",
        )
    expected_ref = source_occurrence_ref(kind, source)
    if occurrence_ref != expected_ref:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"{occurrence_ref!r} does not match source occurrence {expected_ref!r}",
        )
    if (
        source.stream_name != expected_stream
        or not source_index._contains_located_source(source)
    ):
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"{occurrence_ref} is not bound to the current PcbDoc source index",
        )


__all__ = ("resolve_pcb_polygon_connect_rule_selection",)
