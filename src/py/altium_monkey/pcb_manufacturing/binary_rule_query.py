"""Build bounded binary rule queries from resolved manufacturing inputs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, TypeAlias

from .generated import LocatedSource, SourceProvenance, UnresolvedSource
from .output_layers import ManufacturingLayerOccurrence
from .resolved_inputs import (
    PcbResolvedInputError,
    ResolvedArcInput,
    ResolvedComponentClassInput,
    ResolvedComponentOccurrenceInput,
    ResolvedFillInput,
    ResolvedLayerBinding,
    ResolvedNetClassInput,
    ResolvedNetInput,
    ResolvedPadInput,
    ResolvedPolygonInput,
    ResolvedTrackInput,
    ResolvedViaInput,
)
from .rule_query_authority import (
    ManufacturingRuleQueryAuthority,
    resolve_manufacturing_rule_query_authority,
)
from .rule_resolution import (
    ManufacturingBinaryRuleQuery,
    ManufacturingRuleNetRelationship,
    ManufacturingRuleObjectKind,
    ManufacturingRuleQuery,
    PcbRuleResolutionError,
)
from .source_provenance import (
    PcbDocSourceIndex,
    PcbSourceProvenanceError,
    source_occurrence_ref,
)

if TYPE_CHECKING:
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc

_ResolvedDirectFeature: TypeAlias = (
    ResolvedTrackInput
    | ResolvedArcInput
    | ResolvedFillInput
    | ResolvedPadInput
    | ResolvedPolygonInput
    | ResolvedViaInput
)


@dataclass(frozen=True)
class _DirectFeatureFacts:
    occurrence_ref: str
    query: ManufacturingRuleQuery
    layers: tuple[ResolvedLayerBinding, ...]
    source_net_ref: str | None
    component_occurrence_ref: str | None
    component_ownership_supported: bool
    polygon_definition_name: str | None = None


def build_manufacturing_binary_rule_query(
    first: _ResolvedDirectFeature,
    second: _ResolvedDirectFeature,
    *,
    layer_occurrences: Iterable[ManufacturingLayerOccurrence],
    nets: Iterable[ResolvedNetInput],
    net_classes: Iterable[ResolvedNetClassInput] | None = None,
    components: Iterable[ResolvedComponentOccurrenceInput] | None = None,
    component_classes: Iterable[ResolvedComponentClassInput] | None = None,
    pcbdoc: AltiumPcbDoc | None = None,
    source_index: PcbDocSourceIndex | None = None,
    source_authority: ManufacturingRuleQueryAuthority | None = None,
) -> ManufacturingBinaryRuleQuery:
    """Derive one same-layer query from exact resolved feature authority."""

    replay_authority = _validate_source_replay_context(
        net_classes is not None
        or components is not None
        or component_classes is not None,
        pcbdoc,
        source_index,
        source_authority,
        first,
        second,
        include_net_classes=net_classes is not None,
        include_component_classes=component_classes is not None,
    )
    first_facts = _direct_feature_facts(first)
    second_facts = _direct_feature_facts(second)
    available_layers = _validated_layer_occurrences(tuple(layer_occurrences))
    if replay_authority is not None:
        _replay_layer_occurrences(replay_authority, available_layers)
    first_layers = _feature_layer_occurrences(first_facts.layers, available_layers)
    second_layers = _feature_layer_occurrences(second_facts.layers, available_layers)
    shared_ids = frozenset(first_layers).intersection(second_layers)
    if len(shared_ids) != 1:
        raise PcbRuleResolutionError(
            "unresolved_layer_context",
            f"binary rule features share {len(shared_ids)} exact layer occurrences",
        )
    shared_id = next(iter(shared_ids))
    shared_layer = first_layers[shared_id]
    shared_context = _layer_context_ref(shared_layer)
    available_nets = tuple(nets)
    net_names = _validated_net_names(available_nets)
    named_net_names = (
        _replayed_named_net_authority(replay_authority, available_nets, net_names)
        if replay_authority is not None
        else None
    )
    available_classes = _replayed_net_class_authority(
        net_classes,
        source_authority=replay_authority,
        nets=available_nets,
        net_names=net_names,
    )
    available_components = _replayed_component_authority(
        components,
        source_authority=replay_authority,
    )
    available_component_classes = _replayed_component_class_authority(
        component_classes,
        source_authority=replay_authority,
        components=available_components,
    )
    layer_name = (
        _layer_display_name(shared_layer) if replay_authority is not None else None
    )
    polygon_definition_names = (
        replay_authority._polygon_definition_names()
        if replay_authority is not None
        else None
    )
    return ManufacturingBinaryRuleQuery(
        first_occurrence_ref=first_facts.occurrence_ref,
        first=_query_with_named_facts(
            first_facts,
            named_net_names,
            available_classes,
            available_components,
            available_component_classes,
            layer_name,
            polygon_definition_names,
        ),
        first_layer_occurrence_ref=shared_id,
        second_occurrence_ref=second_facts.occurrence_ref,
        second=_query_with_named_facts(
            second_facts,
            named_net_names,
            available_classes,
            available_components,
            available_component_classes,
            layer_name,
            polygon_definition_names,
        ),
        second_layer_occurrence_ref=shared_id,
        region_or_substack_ref=shared_context,
        net_relationship=_net_relationship(first_facts, second_facts, net_names),
        layer_relationship="same_layer",
    )


def _query_with_named_facts(
    facts: _DirectFeatureFacts,
    net_names: dict[str, str] | None,
    net_classes: tuple[ResolvedNetClassInput, ...] | None,
    components: dict[str, tuple[str | None, str | None]] | None,
    component_classes: tuple[ResolvedComponentClassInput, ...] | None,
    layer_name: str | None,
    polygon_definition_names: tuple[str, ...] | None,
) -> ManufacturingRuleQuery:
    source_net_ref = facts.source_net_ref
    if net_names is None or source_net_ref is None:
        net_name = None
    else:
        try:
            net_name = net_names[source_net_ref]
        except KeyError as exc:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"{facts.occurrence_ref} references a net outside resolved net authority",
            ) from exc
    class_names = (
        None
        if net_classes is None
        else tuple(
            row.display_name
            for row in net_classes
            if source_net_ref in row.member_net_refs
        )
    )
    class_authority_names = (
        None if net_classes is None else tuple(row.display_name for row in net_classes)
    )
    component_designator, component_footprint = _component_named_facts(
        facts, components
    )
    component_class_names, component_class_authority_names = (
        _component_class_named_facts(facts, component_classes)
    )
    polygon_definition_name, polygon_definition_authority_names = _polygon_named_facts(
        facts, polygon_definition_names
    )
    return replace(
        facts.query,
        net_name=net_name,
        layer_name=layer_name,
        net_class_names=class_names,
        net_class_authority_names=class_authority_names,
        component_ownership_exact=(
            components is not None and facts.component_ownership_supported
        ),
        component_designator=component_designator,
        component_footprint=component_footprint,
        component_class_names=component_class_names,
        component_class_authority_names=component_class_authority_names,
        polygon_definition_name=polygon_definition_name,
        polygon_definition_authority_names=polygon_definition_authority_names,
    )


def _polygon_named_facts(
    facts: _DirectFeatureFacts,
    authority_names: tuple[str, ...] | None,
) -> tuple[str | None, tuple[str, ...] | None]:
    if facts.query.object_kind != "polygon" or authority_names is None:
        return None, None
    return facts.polygon_definition_name, authority_names


def _component_named_facts(
    facts: _DirectFeatureFacts,
    components: dict[str, tuple[str | None, str | None]] | None,
) -> tuple[str | None, str | None]:
    component_ref = facts.component_occurrence_ref
    if components is None or component_ref is None:
        return None, None
    try:
        return components[component_ref]
    except KeyError as exc:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"{facts.occurrence_ref} references a component outside exact authority",
        ) from exc


def _component_class_named_facts(
    facts: _DirectFeatureFacts,
    classes: tuple[ResolvedComponentClassInput, ...] | None,
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    if classes is None or not facts.component_ownership_supported:
        return None, None
    component_ref = facts.component_occurrence_ref
    memberships = tuple(
        row.display_name
        for row in classes
        if component_ref is not None and component_ref in row.member_component_refs
    )
    return memberships, tuple(row.display_name for row in classes)


def _direct_feature_facts(feature: _ResolvedDirectFeature) -> _DirectFeatureFacts:
    if isinstance(feature, ResolvedTrackInput):
        return _linear_feature_facts(feature, "track", "track")
    if isinstance(feature, ResolvedArcInput):
        return _linear_feature_facts(feature, "arc", "arc")
    if isinstance(feature, ResolvedFillInput):
        return _linear_feature_facts(feature, "fill", "fill")
    if isinstance(feature, ResolvedPadInput):
        return _pad_facts(feature)
    if isinstance(feature, ResolvedPolygonInput):
        return _polygon_facts(feature)
    if isinstance(feature, ResolvedViaInput):
        return _via_facts(feature)
    raise PcbRuleResolutionError(
        "unsupported_rule_query_feature",
        f"unsupported resolved binary-rule feature {type(feature).__name__}",
    )


def _linear_feature_facts(
    feature: ResolvedTrackInput | ResolvedArcInput | ResolvedFillInput,
    source_kind: str,
    object_kind: ManufacturingRuleObjectKind,
) -> _DirectFeatureFacts:
    if feature.is_keepout or feature.is_polygon_outline:
        raise PcbRuleResolutionError(
            "unsupported_rule_query_feature",
            f"{feature.id} has an unsupported classified-feature role",
        )
    _validate_feature_occurrence(source_kind, feature.id, feature.source)
    return _DirectFeatureFacts(
        occurrence_ref=feature.id,
        query=ManufacturingRuleQuery(object_kind=object_kind),
        layers=(feature.layer,),
        source_net_ref=feature.source_net_ref,
        component_occurrence_ref=feature.component_occurrence_ref,
        component_ownership_supported=True,
    )


def _pad_facts(feature: ResolvedPadInput) -> _DirectFeatureFacts:
    hole_size = feature.hole_size_source_units.selected_value
    if hole_size < 0 or not feature.lands:
        raise PcbRuleResolutionError(
            "unsupported_rule_query_feature",
            f"{feature.id} has unsupported pad land or hole semantics",
        )
    _validate_feature_occurrence("pad", feature.id, feature.source)
    if feature.rule_query is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{feature.id} has no exact primitive rule-query facts",
        )
    object_kind: ManufacturingRuleObjectKind
    if hole_size == 0:
        if len(feature.lands) != 1:
            raise PcbRuleResolutionError(
                "unsupported_rule_query_feature",
                f"{feature.id} is a zero-hole multilayer pad",
            )
        object_kind = "smd_pad"
    else:
        object_kind = "th_pad"
    return _DirectFeatureFacts(
        occurrence_ref=feature.id,
        query=replace(feature.rule_query, object_kind=object_kind),
        layers=tuple(land.layer for land in feature.lands),
        source_net_ref=feature.source_net_ref,
        component_occurrence_ref=feature.component_occurrence_ref,
        component_ownership_supported=True,
    )


def _via_facts(feature: ResolvedViaInput) -> _DirectFeatureFacts:
    _validate_feature_occurrence("via", feature.id, feature.source)
    if feature.rule_query is None or not feature.lands:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{feature.id} has no exact via rule-query or land facts",
        )
    return _DirectFeatureFacts(
        occurrence_ref=feature.id,
        query=replace(feature.rule_query, object_kind="via"),
        layers=tuple(land.layer for land in feature.lands),
        source_net_ref=feature.source_net_ref,
        component_occurrence_ref=feature.component_occurrence_ref,
        component_ownership_supported=True,
    )


def _polygon_facts(feature: ResolvedPolygonInput) -> _DirectFeatureFacts:
    _validate_feature_occurrence("polygon", feature.id, feature.source)
    _validate_polygon_runtime_facts(feature)
    if feature.polygon_type.strip().casefold() != "polygon":
        raise PcbRuleResolutionError(
            "unsupported_rule_query_feature",
            f"{feature.id} is not an active ordinary polygon definition",
        )
    if (
        feature.rule_query is None
        or feature.is_keepout is None
        or feature.is_shelved is None
        or feature.is_polygon_outline is None
    ):
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{feature.id} has incomplete polygon classification facts",
        )
    if not feature.net_identity_exact:
        raise PcbRuleResolutionError(
            "unresolved_net_relationship",
            f"{feature.id} has no exact polygon net identity",
        )
    if feature.is_keepout or feature.is_shelved or feature.is_polygon_outline:
        raise PcbRuleResolutionError(
            "unsupported_rule_query_feature",
            f"{feature.id} is not an active ordinary polygon definition",
        )
    return _DirectFeatureFacts(
        occurrence_ref=feature.id,
        query=replace(feature.rule_query, object_kind="polygon"),
        layers=(feature.layer,),
        source_net_ref=feature.source_net_ref,
        component_occurrence_ref=None,
        component_ownership_supported=False,
        polygon_definition_name=feature.definition_name,
    )


def _validate_polygon_runtime_facts(feature: ResolvedPolygonInput) -> None:
    if not isinstance(feature.polygon_type, str) or not feature.polygon_type.strip():
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"{feature.id} has invalid polygon-type evidence",
        )
    if type(feature.net_identity_exact) is not bool:
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"{feature.id} has invalid polygon net-identity evidence",
        )
    optional_boolean_facts: dict[str, object | None] = {
        "is_keepout": feature.is_keepout,
        "is_shelved": feature.is_shelved,
        "is_polygon_outline": feature.is_polygon_outline,
    }
    invalid = [
        name
        for name, value in optional_boolean_facts.items()
        if value is not None and type(value) is not bool
    ]
    if invalid:
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"{feature.id} has invalid polygon Boolean facts: {', '.join(invalid)}",
        )
    if feature.rule_query is not None and not isinstance(
        feature.rule_query, ManufacturingRuleQuery
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"{feature.id} has invalid polygon primitive-query evidence",
        )


def _validated_layer_occurrences(
    occurrences: tuple[ManufacturingLayerOccurrence, ...],
) -> tuple[ManufacturingLayerOccurrence, ...]:
    ids: set[str] = set()
    named_layers: set[tuple[str, str]] = set()
    for occurrence in occurrences:
        _validate_layer_occurrence_identity(occurrence)
        if occurrence.id in ids:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"duplicate manufacturing layer occurrence {occurrence.id!r}",
            )
        ids.add(occurrence.id)
        layer_name = _layer_display_name(occurrence)
        named_identity = (_layer_context_ref(occurrence), layer_name.casefold())
        if named_identity in named_layers:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"duplicate layer name {layer_name!r} in context {named_identity[0]!r}",
            )
        named_layers.add(named_identity)
    return occurrences


def _feature_layer_occurrences(
    bindings: tuple[ResolvedLayerBinding, ...],
    occurrences: tuple[ManufacturingLayerOccurrence, ...],
) -> dict[str, ManufacturingLayerOccurrence]:
    result: dict[str, ManufacturingLayerOccurrence] = {}
    for binding in bindings:
        context_ref = _binding_context_ref(binding)
        occurrence = _exact_layer_occurrence(binding, context_ref, occurrences)
        if occurrence.id in result:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"feature has duplicate land occurrence {occurrence.id!r}",
            )
        result[occurrence.id] = occurrence
    return result


def _validate_feature_occurrence(
    kind: str,
    occurrence_ref: str,
    source: SourceProvenance,
) -> None:
    if isinstance(source, UnresolvedSource):
        raise PcbRuleResolutionError(
            "unresolved_source_identity",
            f"{occurrence_ref} has no source-backed occurrence identity",
        )
    expected = source_occurrence_ref(kind, source)
    if occurrence_ref != expected:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"{occurrence_ref!r} does not match source occurrence {expected!r}",
        )


def _exact_layer_occurrence(
    binding: ResolvedLayerBinding,
    context_ref: str,
    occurrences: tuple[ManufacturingLayerOccurrence, ...],
) -> ManufacturingLayerOccurrence:
    matches = tuple(
        occurrence
        for occurrence in occurrences
        if _layer_occurrence_matches(binding, context_ref, occurrence)
    )
    if len(matches) != 1:
        raise PcbRuleResolutionError(
            "unresolved_layer_context",
            f"layer binding {binding.layer_key!r} resolved to {len(matches)} output occurrences",
        )
    return matches[0]


def _layer_occurrence_matches(
    binding: ResolvedLayerBinding,
    context_ref: str,
    occurrence: ManufacturingLayerOccurrence,
) -> bool:
    _validate_layer_occurrence_identity(occurrence)
    if (
        occurrence.layer.layer_key != binding.layer_key
        or occurrence.layer.layer_ref != binding.layer_ref
    ):
        return False
    return _layer_context_ref(occurrence) == context_ref


def _binding_context_ref(binding: ResolvedLayerBinding) -> str:
    contexts = frozenset(
        (*binding.applicable_substack_refs, *binding.applicable_region_stack_refs)
    )
    if not contexts:
        return "whole_board"
    if len(contexts) != 1:
        raise PcbRuleResolutionError(
            "unresolved_layer_context",
            f"layer binding {binding.layer_key!r} has {len(contexts)} applicable contexts",
        )
    return next(iter(contexts))


def _validate_layer_occurrence_identity(
    occurrence: ManufacturingLayerOccurrence,
) -> None:
    if (
        occurrence.board_region_stack_ref is not None
        and occurrence.board_region_stack_ref != occurrence.source_stackup_ref
    ):
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"layer occurrence {occurrence.id!r} has contradictory region and source-stack refs",
        )
    expected_id = (
        f"layer.{occurrence.source_stackup_ref or 'whole_board'}."
        f"{occurrence.layer.layer_key}"
    )
    if occurrence.id != expected_id:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"layer occurrence {occurrence.id!r} does not match {expected_id!r}",
        )


def _layer_context_ref(occurrence: ManufacturingLayerOccurrence) -> str:
    return (
        occurrence.board_region_stack_ref
        or occurrence.source_stackup_ref
        or "whole_board"
    )


def _layer_display_name(occurrence: ManufacturingLayerOccurrence) -> str:
    name = occurrence.layer.display_name
    if type(name) is not str or not name.strip():
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"layer occurrence {occurrence.id!r} has no exact display name",
        )
    return name


def _net_relationship(
    first: _DirectFeatureFacts,
    second: _DirectFeatureFacts,
    net_names: dict[str, str],
) -> ManufacturingRuleNetRelationship:
    first_ref = first.source_net_ref
    second_ref = second.source_net_ref
    if first_ref is None and second_ref is None:
        return "not_applicable"
    if first_ref is None or second_ref is None:
        raise PcbRuleResolutionError(
            "unresolved_net_relationship",
            "binary rule features have incomplete net relationship evidence",
        )
    if first_ref not in net_names or second_ref not in net_names:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "binary rule feature references a net outside resolved net authority",
        )
    return "same_net" if first_ref == second_ref else "different_nets"


def _validated_net_names(nets: tuple[ResolvedNetInput, ...]) -> dict[str, str]:
    names_by_id: dict[str, str] = {}
    ids_by_name: dict[str, str] = {}
    for net in nets:
        _validate_feature_occurrence("net", net.id, net.source)
        if net.id in names_by_id:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"duplicate resolved net occurrence {net.id!r}",
            )
        if type(net.display_name) is not str or not net.display_name.strip():
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"resolved net occurrence {net.id!r} has no exact display name",
            )
        display_name = net.display_name
        normalized_name = display_name.casefold()
        if normalized_name in ids_by_name:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"net name {display_name!r} identifies multiple source occurrences",
            )
        names_by_id[net.id] = display_name
        ids_by_name[normalized_name] = net.id
    return names_by_id


def _validated_net_classes(
    classes: tuple[ResolvedNetClassInput, ...],
    nets: tuple[ResolvedNetInput, ...],
    net_names: dict[str, str],
) -> tuple[ResolvedNetClassInput, ...]:
    if classes and not nets:
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            "net-class evaluation requires resolved net source authority",
        )
    expected_source_key = _validated_net_source_key(nets)
    class_ids: set[str] = set()
    class_names: set[str] = set()
    for row in classes:
        _validate_net_class_identity(row, class_ids, class_names)
        _validate_net_class_members(row, net_names)
        _validate_net_class_source_revision(row, expected_source_key)
    return classes


def _validate_source_replay_context(
    required: bool,
    pcbdoc: AltiumPcbDoc | None,
    source_index: PcbDocSourceIndex | None,
    source_authority: ManufacturingRuleQueryAuthority | None,
    first: _ResolvedDirectFeature,
    second: _ResolvedDirectFeature,
    *,
    include_net_classes: bool,
    include_component_classes: bool,
) -> ManufacturingRuleQueryAuthority | None:
    requested = _source_replay_requested(
        required, pcbdoc, source_index, source_authority
    )
    if not requested:
        return None
    try:
        authority = _source_replay_authority(
            pcbdoc,
            source_index,
            source_authority,
            include_net_classes=include_net_classes,
            include_component_classes=include_component_classes,
        )
        authority._assert_compatible(pcbdoc, source_index)
        _validate_query_feature_sources(authority, first, second)
    except (PcbResolvedInputError, PcbSourceProvenanceError) as exc:
        raise PcbRuleResolutionError(exc.code, exc.detail) from exc
    return authority


def _source_replay_requested(
    required: bool,
    pcbdoc: AltiumPcbDoc | None,
    source_index: PcbDocSourceIndex | None,
    source_authority: ManufacturingRuleQueryAuthority | None,
) -> bool:
    return (
        required
        or pcbdoc is not None
        or source_index is not None
        or source_authority is not None
    )


def _source_replay_authority(
    pcbdoc: AltiumPcbDoc | None,
    source_index: PcbDocSourceIndex | None,
    source_authority: ManufacturingRuleQueryAuthority | None,
    *,
    include_net_classes: bool,
    include_component_classes: bool,
) -> ManufacturingRuleQueryAuthority:
    if source_authority is not None:
        return source_authority
    if pcbdoc is None or source_index is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "named source facts require current PcbDoc replay authority",
        )
    return resolve_manufacturing_rule_query_authority(
        pcbdoc,
        source_index=source_index,
        include_net_classes=include_net_classes,
        include_component_classes=include_component_classes,
    )


def _replayed_named_net_authority(
    source_authority: ManufacturingRuleQueryAuthority,
    nets: tuple[ResolvedNetInput, ...],
    net_names: dict[str, str],
) -> dict[str, str]:
    if nets != source_authority.nets:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "net authority does not match the current PcbDoc source revision",
        )
    return net_names


def _replay_layer_occurrences(
    source_authority: ManufacturingRuleQueryAuthority,
    supplied: tuple[ManufacturingLayerOccurrence, ...],
) -> None:
    if supplied != source_authority.layer_occurrences:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "walked layer authority does not match the current PcbDoc source revision",
        )


def _replayed_net_class_authority(
    classes: Iterable[ResolvedNetClassInput] | None,
    *,
    source_authority: ManufacturingRuleQueryAuthority | None,
    nets: tuple[ResolvedNetInput, ...],
    net_names: dict[str, str],
) -> tuple[ResolvedNetClassInput, ...] | None:
    if classes is None:
        return None
    if source_authority is None or source_authority.net_classes is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "net-class facts require source authority built with net classes",
        )
    supplied = tuple(classes)
    replayed = source_authority.net_classes
    if supplied != replayed:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "net-class authority does not match the current Classes6 source revision",
        )
    return _validated_net_classes(replayed, nets, net_names)


def _validate_query_feature_sources(
    source_authority: ManufacturingRuleQueryAuthority,
    first: _ResolvedDirectFeature,
    second: _ResolvedDirectFeature,
) -> None:
    for feature in (first, second):
        expected = source_authority._feature(feature.id)
        if expected is None:
            raise PcbRuleResolutionError(
                "foreign_source_identity",
                f"{feature.id} has no current resolved source row",
            )
        if feature != expected:
            raise PcbRuleResolutionError(
                "corrupt_identity",
                f"{feature.id} does not match its complete current resolved input",
            )


def _replayed_component_authority(
    components: Iterable[ResolvedComponentOccurrenceInput] | None,
    *,
    source_authority: ManufacturingRuleQueryAuthority | None,
) -> dict[str, tuple[str | None, str | None]] | None:
    if components is None:
        return None
    if source_authority is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "component facts require current PcbDoc source replay authority",
        )
    supplied = tuple(components)
    if supplied != source_authority.components:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "component authority does not match the current PcbDoc source revision",
        )
    authority: dict[str, tuple[str | None, str | None]] = {}
    for row in supplied:
        if row.id in authority:
            raise PcbRuleResolutionError(
                "corrupt_identity", f"duplicate component occurrence {row.id!r}"
            )
        authority[row.id] = (
            row.display_designator or None,
            row.footprint or None,
        )
    return authority


def _replayed_component_class_authority(
    classes: Iterable[ResolvedComponentClassInput] | None,
    *,
    source_authority: ManufacturingRuleQueryAuthority | None,
    components: dict[str, tuple[str | None, str | None]] | None,
) -> tuple[ResolvedComponentClassInput, ...] | None:
    if classes is None:
        return None
    if components is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "component-class facts require exact component occurrence authority",
        )
    if source_authority is None or source_authority.component_classes is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            "component-class facts require source authority built with component classes",
        )
    supplied = tuple(classes)
    replayed = source_authority.component_classes
    if supplied != replayed:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            "component-class authority does not match the current Classes6 source revision",
        )
    return _validated_component_classes(replayed, set(components))


def _validated_component_classes(
    classes: tuple[ResolvedComponentClassInput, ...],
    occurrence_refs: set[str],
) -> tuple[ResolvedComponentClassInput, ...]:
    class_ids: set[str] = set()
    class_names: set[str] = set()
    for row in classes:
        normalized_name = _validate_component_class_identity(
            row,
            class_ids,
            class_names,
        )
        _validate_component_class_members(row, occurrence_refs)
        class_ids.add(row.id)
        class_names.add(normalized_name)
    return classes


def _validate_component_class_identity(
    row: ResolvedComponentClassInput,
    class_ids: set[str],
    class_names: set[str],
) -> str:
    _validate_feature_occurrence("component_class", row.id, row.source)
    if not isinstance(row.source, LocatedSource):
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"component class {row.id!r} has no exact source locator",
        )
    if row.source.stream_name != "Classes6/Data":
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"component class {row.id!r} is not bound to Classes6/Data",
        )
    if row.id in class_ids or not row.display_name.strip():
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"component class {row.id!r} has duplicate or invalid identity",
        )
    normalized_name = row.display_name.casefold()
    if normalized_name in class_names:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"component class {row.id!r} has duplicate or invalid identity",
        )
    return normalized_name


def _validate_component_class_members(
    row: ResolvedComponentClassInput,
    occurrence_refs: set[str],
) -> None:
    if len(set(row.member_component_refs)) != len(row.member_component_refs):
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"component class {row.id!r} has duplicate component members",
        )
    if any(ref not in occurrence_refs for ref in row.member_component_refs):
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"component class {row.id!r} references a component outside exact authority",
        )


def _validated_net_source_key(
    nets: tuple[ResolvedNetInput, ...],
) -> tuple[str, ...] | None:
    if any(
        not isinstance(net.source, LocatedSource)
        or net.source.stream_name != "Nets6/Data"
        for net in nets
    ):
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            "net-class evaluation requires exact Nets6/Data source authority",
        )
    net_source_keys = {_source_authority_key(net.source) for net in nets}
    if len(net_source_keys) > 1:
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            "resolved net authority spans multiple source revisions",
        )
    return next(iter(net_source_keys), None)


def _validate_net_class_identity(
    row: ResolvedNetClassInput,
    class_ids: set[str],
    class_names: set[str],
) -> None:
    _validate_feature_occurrence("net_class", row.id, row.source)
    _validate_net_class_source(row)
    if row.id in class_ids:
        raise PcbRuleResolutionError(
            "corrupt_identity", f"duplicate net class occurrence {row.id!r}"
        )
    class_ids.add(row.id)
    normalized_name = _validated_net_class_name(row)
    if normalized_name in class_names:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"net class name {row.display_name!r} identifies multiple occurrences",
        )
    class_names.add(normalized_name)


def _validate_net_class_source(row: ResolvedNetClassInput) -> None:
    if not isinstance(row.source, LocatedSource) or row.source.stream_name != (
        "Classes6/Data"
    ):
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"net class {row.id!r} has no exact Classes6/Data source authority",
        )


def _validated_net_class_name(row: ResolvedNetClassInput) -> str:
    if type(row.display_name) is not str or not row.display_name.strip():
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"net class occurrence {row.id!r} has no exact display name",
        )
    return row.display_name.casefold()


def _validate_net_class_source_revision(
    row: ResolvedNetClassInput,
    expected_source_key: tuple[str, ...] | None,
) -> None:
    source_key = _source_authority_key(row.source)
    if expected_source_key is not None and source_key != expected_source_key:
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"net class {row.id!r} is not bound to the resolved net revision",
        )


def _validate_net_class_members(
    row: ResolvedNetClassInput,
    net_names: dict[str, str],
) -> None:
    if len(set(row.member_net_refs)) != len(row.member_net_refs):
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"net class {row.id!r} contains duplicate member occurrences",
        )
    foreign = tuple(ref for ref in row.member_net_refs if ref not in net_names)
    if foreign:
        raise PcbRuleResolutionError(
            "foreign_source_identity",
            f"net class {row.id!r} contains members outside resolved net authority",
        )


def _source_authority_key(source: SourceProvenance) -> tuple[str, ...]:
    if isinstance(source, LocatedSource):
        return (
            "located",
            source.document_revision_sha256,
            source.logical_path,
        )
    raise PcbRuleResolutionError(
        "unresolved_source_identity",
        "named class authority has unresolved source provenance",
    )


__all__ = ("build_manufacturing_binary_rule_query",)
