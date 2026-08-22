"""Build reusable source authority for manufacturing rule queries."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias

from altium_monkey.altium_pcb_rule import AltiumPcbRule
from altium_monkey.altium_resolved_layer_stack import (
    ResolvedLayerStack,
    resolved_layer_stack_from_pcbdoc,
)

from .output_layers import (
    ManufacturingLayerOccurrence,
    walk_manufacturing_output_layers,
)
from .generated import LocatedSource
from .resolved_inputs import (
    PcbResolvedInputError,
    ResolvedArcInput,
    ResolvedComponentClassInput,
    ResolvedComponentOccurrenceInput,
    ResolvedFillInput,
    ResolvedNetClassInput,
    ResolvedNetInput,
    ResolvedPadInput,
    ResolvedPcbInputs,
    ResolvedPolygonConnectRuleAuthority,
    ResolvedPolygonInput,
    ResolvedTrackInput,
    ResolvedViaInput,
    resolve_pcb_net_class_inputs,
    resolve_pcb_component_class_inputs,
    resolve_pcb_stored_inputs,
)
from .source_provenance import PcbDocSourceIndex, PcbSourceProvenanceError

if TYPE_CHECKING:
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc

_ResolvedRuleFeature: TypeAlias = (
    ResolvedTrackInput
    | ResolvedArcInput
    | ResolvedFillInput
    | ResolvedPadInput
    | ResolvedPolygonInput
    | ResolvedViaInput
)
_CapturedRule: TypeAlias = tuple[AltiumPcbRule, str]
_ActivePolygonDefinition: TypeAlias = tuple[str, str | None]
_AUTHORITY_CONSTRUCTION_TOKEN = object()


class ManufacturingRuleQueryAuthority:
    """Immutable, document-scoped authority reused by binary rule queries."""

    _document_revision_sha256: str
    _active_polygon_definitions: tuple[_ActivePolygonDefinition, ...]
    _active_polygon_names: tuple[str, ...]
    _layer_occurrences: tuple[ManufacturingLayerOccurrence, ...]
    _nets: tuple[ResolvedNetInput, ...]
    _components: tuple[ResolvedComponentOccurrenceInput, ...]
    _component_classes: tuple[ResolvedComponentClassInput, ...] | None
    _net_classes: tuple[ResolvedNetClassInput, ...] | None
    _pcbdoc: AltiumPcbDoc
    _polygon_connect_authority: ResolvedPolygonConnectRuleAuthority | None
    _polygon_connect_rules: tuple[_CapturedRule, ...]
    _source_index: PcbDocSourceIndex
    _features_by_id: Mapping[str, _ResolvedRuleFeature]

    __slots__ = (
        "_components",
        "_component_classes",
        "_document_revision_sha256",
        "_active_polygon_definitions",
        "_active_polygon_names",
        "_features_by_id",
        "_layer_occurrences",
        "_net_classes",
        "_nets",
        "_pcbdoc",
        "_polygon_connect_authority",
        "_polygon_connect_rules",
        "_source_index",
    )

    def __init__(
        self,
        *,
        construction_token: object,
        document_revision_sha256: str,
        active_polygon_definitions: tuple[_ActivePolygonDefinition, ...],
        layer_occurrences: tuple[ManufacturingLayerOccurrence, ...],
        nets: tuple[ResolvedNetInput, ...],
        components: tuple[ResolvedComponentOccurrenceInput, ...],
        component_classes: tuple[ResolvedComponentClassInput, ...] | None,
        net_classes: tuple[ResolvedNetClassInput, ...] | None,
        polygon_connect_authority: ResolvedPolygonConnectRuleAuthority | None,
        polygon_connect_rules: tuple[_CapturedRule, ...],
        pcbdoc: AltiumPcbDoc,
        source_index: PcbDocSourceIndex,
        features_by_id: Mapping[str, _ResolvedRuleFeature],
    ) -> None:
        if construction_token is not _AUTHORITY_CONSTRUCTION_TOKEN:
            raise TypeError(
                "ManufacturingRuleQueryAuthority must be created by its resolver"
            )
        object.__setattr__(self, "_document_revision_sha256", document_revision_sha256)
        object.__setattr__(
            self, "_active_polygon_definitions", active_polygon_definitions
        )
        object.__setattr__(
            self,
            "_active_polygon_names",
            tuple(
                name
                for _occurrence_ref, name in active_polygon_definitions
                if name is not None
            ),
        )
        object.__setattr__(self, "_layer_occurrences", layer_occurrences)
        object.__setattr__(self, "_nets", nets)
        object.__setattr__(self, "_components", components)
        object.__setattr__(self, "_component_classes", component_classes)
        object.__setattr__(self, "_net_classes", net_classes)
        object.__setattr__(
            self, "_polygon_connect_authority", polygon_connect_authority
        )
        object.__setattr__(self, "_polygon_connect_rules", polygon_connect_rules)
        object.__setattr__(self, "_pcbdoc", pcbdoc)
        object.__setattr__(self, "_source_index", source_index)
        object.__setattr__(self, "_features_by_id", features_by_id)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    @property
    def document_revision_sha256(self) -> str:
        """Exact source-document revision represented by this authority."""

        return self._document_revision_sha256

    @property
    def layer_occurrences(self) -> tuple[ManufacturingLayerOccurrence, ...]:
        """Exact walked output layers captured from the source document."""

        return self._layer_occurrences

    @property
    def nets(self) -> tuple[ResolvedNetInput, ...]:
        """Exact ordered source-net authority."""

        return self._nets

    @property
    def components(self) -> tuple[ResolvedComponentOccurrenceInput, ...]:
        """Exact ordered source-component authority."""

        return self._components

    @property
    def component_classes(self) -> tuple[ResolvedComponentClassInput, ...] | None:
        """Optional exact component-class authority requested at construction."""

        return self._component_classes

    @property
    def net_classes(self) -> tuple[ResolvedNetClassInput, ...] | None:
        """Optional exact net-class authority requested at construction."""

        return self._net_classes

    @property
    def polygon_connect_authority(
        self,
    ) -> ResolvedPolygonConnectRuleAuthority | None:
        """Exact PolygonConnect rule authority captured for this batch."""

        return self._polygon_connect_authority

    def validate_current(self) -> None:
        """Fail if the parsed source changed since this batch was opened."""

        self._source_index.assert_current()

    def _assert_compatible(
        self,
        pcbdoc: AltiumPcbDoc | None,
        source_index: PcbDocSourceIndex | None,
    ) -> None:
        if pcbdoc is not None and pcbdoc is not self._pcbdoc:
            raise PcbSourceProvenanceError(
                "foreign_source_identity",
                "rule-query authority does not belong to the supplied PcbDoc",
            )
        if source_index is not None and source_index is not self._source_index:
            raise PcbSourceProvenanceError(
                "foreign_source_identity",
                "rule-query authority does not belong to the supplied source index",
            )

    def _feature(self, occurrence_ref: str) -> _ResolvedRuleFeature | None:
        return self._features_by_id.get(occurrence_ref)

    def _polygon_rules(self) -> tuple[_CapturedRule, ...]:
        return self._polygon_connect_rules

    def _polygon_definition_authority(
        self,
    ) -> tuple[_ActivePolygonDefinition, ...]:
        return self._active_polygon_definitions

    def _polygon_definition_names(self) -> tuple[str, ...]:
        return self._active_polygon_names


def resolve_manufacturing_rule_query_authority(
    pcbdoc: AltiumPcbDoc,
    *,
    source_index: PcbDocSourceIndex,
    layer_stack: ResolvedLayerStack | None = None,
    include_net_classes: bool = False,
    include_component_classes: bool = False,
) -> ManufacturingRuleQueryAuthority:
    """Resolve immutable source facts once for repeated manufacturing queries."""

    source_index._assert_current_pcbdoc(pcbdoc)
    exact_layer_stack = resolved_layer_stack_from_pcbdoc(pcbdoc)
    if layer_stack is not None and layer_stack != exact_layer_stack:
        raise PcbResolvedInputError(
            "corrupt_identity",
            "supplied layer stack does not match the current PcbDoc source revision",
        )
    resolved = resolve_pcb_stored_inputs(
        pcbdoc,
        source_index=source_index,
        layer_stack=exact_layer_stack,
        include_routes=True,
        include_physical_features=True,
        include_profile=False,
        strictness="strict",
    )
    classes = (
        resolve_pcb_net_class_inputs(
            pcbdoc,
            source_index=source_index,
            nets=resolved.nets,
        )
        if include_net_classes
        else None
    )
    component_classes = (
        resolve_pcb_component_class_inputs(
            pcbdoc,
            source_index=source_index,
            components=resolved.components,
        )
        if include_component_classes
        else None
    )
    features = (
        *resolved.tracks,
        *resolved.arcs,
        *resolved.fills,
        *resolved.pads,
        *resolved.vias,
        *resolved.polygons,
    )
    features_by_id: dict[str, _ResolvedRuleFeature] = {}
    for feature in features:
        if feature.id in features_by_id:
            raise PcbResolvedInputError(
                "corrupt_identity",
                f"duplicate rule-query feature occurrence {feature.id!r}",
            )
        features_by_id[feature.id] = feature
    polygon_connect_rules = _capture_polygon_connect_rules(
        pcbdoc,
        source_index,
        resolved,
    )
    return ManufacturingRuleQueryAuthority(
        construction_token=_AUTHORITY_CONSTRUCTION_TOKEN,
        document_revision_sha256=source_index.document_revision_sha256,
        active_polygon_definitions=_active_polygon_definition_authority(features),
        layer_occurrences=walk_manufacturing_output_layers(exact_layer_stack),
        nets=resolved.nets,
        components=resolved.components,
        component_classes=component_classes,
        net_classes=classes,
        polygon_connect_authority=resolved.polygon_connect_rule_authority,
        polygon_connect_rules=polygon_connect_rules,
        pcbdoc=pcbdoc,
        source_index=source_index,
        features_by_id=MappingProxyType(features_by_id),
    )


def _active_polygon_definition_authority(
    features: tuple[_ResolvedRuleFeature, ...],
) -> tuple[_ActivePolygonDefinition, ...]:
    return tuple(
        (feature.id, feature.definition_name)
        for feature in features
        if isinstance(feature, ResolvedPolygonInput)
        and feature.polygon_type.strip().casefold() == "polygon"
        and feature.is_keepout is False
        and feature.is_shelved is False
        and feature.is_polygon_outline is False
    )


def _capture_polygon_connect_rules(
    pcbdoc: AltiumPcbDoc,
    source_index: PcbDocSourceIndex,
    resolved: ResolvedPcbInputs,
) -> tuple[_CapturedRule, ...]:
    authority = resolved.polygon_connect_rule_authority
    if authority is None:
        return ()
    captured: list[_CapturedRule] = []
    for candidate in authority.rules:
        source = candidate.source
        if not isinstance(source, LocatedSource):
            raise PcbResolvedInputError(
                "unresolved_source_identity",
                f"{candidate.id} has no exact source rule locator",
            )
        if not 0 <= source.record_index < len(pcbdoc.rules):
            raise PcbResolvedInputError(
                "foreign_source_identity",
                f"{candidate.id} source rule index is out of range",
            )
        source_rule = pcbdoc.rules[source.record_index]
        if source_index.source_for(source_rule) != source:
            raise PcbResolvedInputError(
                "foreign_source_identity",
                f"{candidate.id} is not bound to its current source rule",
            )
        captured.append((deepcopy(source_rule), candidate.id))
    return tuple(captured)


__all__ = (
    "ManufacturingRuleQueryAuthority",
    "resolve_manufacturing_rule_query_authority",
)
