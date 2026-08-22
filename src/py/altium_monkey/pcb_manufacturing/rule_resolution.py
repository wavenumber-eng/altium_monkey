"""Evaluate the bounded PCB rule subset used by manufacturing resolution."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from altium_monkey.altium_pcb_rule import AltiumPcbRule
from altium_monkey.pcb_manufacturing.scope_expression import (
    _ScopeAnd,
    _ScopeExpression,
    _ScopeExpressionSyntaxError,
    _ScopeNot,
    _ScopePredicate,
    _parse_scope_expression,
)


ManufacturingRuleObjectKind = Literal[
    "arc",
    "track",
    "pad",
    "smd_pad",
    "th_pad",
    "via",
    "fill",
    "polygon",
    "region",
    "text",
    "hole",
    "plane",
    "outline_edge",
    "cavity_edge",
    "cutout_edge",
    "split_barrier",
    "split_continuation",
]
ManufacturingRuleNetRelationship = Literal[
    "same_net",
    "different_nets",
    "same_diff_pair",
    "different_diff_pairs",
    "not_applicable",
]
ManufacturingRuleLayerRelationship = Literal["same_layer", "adjacent_layer"]

_MANUFACTURING_RULE_OBJECT_KINDS = frozenset(
    {
        "arc",
        "track",
        "pad",
        "smd_pad",
        "th_pad",
        "via",
        "fill",
        "polygon",
        "region",
        "text",
        "hole",
        "plane",
        "outline_edge",
        "cavity_edge",
        "cutout_edge",
        "split_barrier",
        "split_continuation",
    }
)
_MANUFACTURING_RULE_NET_RELATIONSHIPS = frozenset(
    {
        "same_net",
        "different_nets",
        "same_diff_pair",
        "different_diff_pairs",
        "not_applicable",
    }
)
_MANUFACTURING_RULE_LAYER_RELATIONSHIPS = frozenset({"same_layer", "adjacent_layer"})


@dataclass
class PcbRuleResolutionError(ValueError):
    """Stable failure while selecting a manufacturing rule."""

    code: str
    detail: str


@dataclass(frozen=True)
class ManufacturingRuleQuery:
    """Primitive facts supported by the first manufacturing rule evaluator."""

    object_kind: ManufacturingRuleObjectKind
    locked: bool = False
    testpoint_top: bool = False
    testpoint_bottom: bool = False
    net_name: str | None = None
    layer_name: str | None = None
    net_class_names: tuple[str, ...] | None = None
    net_class_authority_names: tuple[str, ...] | None = None
    component_class_names: tuple[str, ...] | None = None
    component_class_authority_names: tuple[str, ...] | None = None
    polygon_definition_name: str | None = None
    polygon_definition_authority_names: tuple[str, ...] | None = None
    component_ownership_exact: bool = False
    component_designator: str | None = None
    component_footprint: str | None = None

    def __post_init__(self) -> None:
        _validate_rule_query(self)

    @property
    def is_testpoint(self) -> bool:
        """Return whether either board side marks the primitive as a testpoint."""

        return self.testpoint_top or self.testpoint_bottom


@dataclass(frozen=True)
class ManufacturingBinaryRuleQuery:
    """Complete two-occurrence context for bounded binary rule selection."""

    first_occurrence_ref: str
    first: ManufacturingRuleQuery
    first_layer_occurrence_ref: str
    second_occurrence_ref: str
    second: ManufacturingRuleQuery
    second_layer_occurrence_ref: str
    region_or_substack_ref: str
    net_relationship: ManufacturingRuleNetRelationship
    layer_relationship: ManufacturingRuleLayerRelationship

    def __post_init__(self) -> None:
        _validate_binary_query_references(self)
        _validate_binary_query_primitives(self)
        _validate_binary_query_relationships(self)
        _validate_binary_query_layer_consistency(self)


def _validate_rule_query(query: ManufacturingRuleQuery) -> None:
    if (
        not isinstance(query.object_kind, str)
        or query.object_kind not in _MANUFACTURING_RULE_OBJECT_KINDS
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"manufacturing rule query has invalid object kind {query.object_kind!r}",
        )
    boolean_facts = {
        "locked": query.locked,
        "testpoint_top": query.testpoint_top,
        "testpoint_bottom": query.testpoint_bottom,
        "component_ownership_exact": query.component_ownership_exact,
    }
    invalid = [name for name, value in boolean_facts.items() if type(value) is not bool]
    if invalid:
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"manufacturing rule query has invalid boolean facts: {', '.join(invalid)}",
        )
    _validate_optional_query_names(query)


def _validate_optional_query_names(query: ManufacturingRuleQuery) -> None:
    optional_names = {
        "net_name": query.net_name,
        "layer_name": query.layer_name,
        "component_designator": query.component_designator,
        "component_footprint": query.component_footprint,
        "polygon_definition_name": query.polygon_definition_name,
    }
    invalid_names = [
        name
        for name, value in optional_names.items()
        if value is not None and (type(value) is not str or not value.strip())
    ]
    if invalid_names:
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            f"manufacturing rule query has invalid named facts: {', '.join(invalid_names)}",
        )
    if not query.component_ownership_exact and (
        query.component_designator is not None or query.component_footprint is not None
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "component names require exact component ownership evidence",
        )
    _validate_net_class_query_names(query)
    _validate_component_class_query_names(query)
    _validate_polygon_definition_query_names(query)


def _validate_polygon_definition_query_names(query: ManufacturingRuleQuery) -> None:
    name = query.polygon_definition_name
    authority = query.polygon_definition_authority_names
    if authority is None:
        if name is None:
            return
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "polygon definition name requires exact name authority",
        )
    if query.object_kind != "polygon":
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "polygon definition names require a polygon query occurrence",
        )
    if type(authority) is not tuple or any(
        type(value) is not str or not value.strip() for value in authority
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "manufacturing rule query has invalid polygon definition authority",
        )
    if name is not None and name not in set(authority):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "polygon definition name must belong to the supplied name authority",
        )


def _validate_net_class_query_names(query: ManufacturingRuleQuery) -> None:
    memberships = query.net_class_names
    authority = query.net_class_authority_names
    if (memberships is None) != (authority is None):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "net-class memberships and authority names must be supplied together",
        )
    if memberships is None or authority is None:
        return
    if not _valid_query_name_tuple(memberships) or not _valid_query_name_tuple(
        authority
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query", "manufacturing rule query has invalid net-class facts"
        )
    authority_names = {name.casefold() for name in authority}
    if any(name.casefold() not in authority_names for name in memberships):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "net-class memberships must belong to the supplied class authority",
        )


def _validate_component_class_query_names(query: ManufacturingRuleQuery) -> None:
    memberships = query.component_class_names
    authority = query.component_class_authority_names
    if (memberships is None) != (authority is None):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "component-class memberships and authority names must be supplied together",
        )
    if memberships is None or authority is None:
        return
    if not query.component_ownership_exact:
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "component-class facts require exact component ownership evidence",
        )
    if not _valid_query_name_tuple(memberships) or not _valid_query_name_tuple(
        authority
    ):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "manufacturing rule query has invalid component-class facts",
        )
    authority_names = {name.casefold() for name in authority}
    if any(name.casefold() not in authority_names for name in memberships):
        raise PcbRuleResolutionError(
            "invalid_rule_query",
            "component-class memberships must belong to the supplied class authority",
        )


def _valid_query_name_tuple(values: tuple[str, ...]) -> bool:
    if type(values) is not tuple:
        return False
    if any(type(value) is not str or not value.strip() for value in values):
        return False
    normalized = tuple(value.casefold() for value in values)
    return len(set(normalized)) == len(normalized)


def _validate_binary_query_references(query: ManufacturingBinaryRuleQuery) -> None:
    refs = {
        "first occurrence": query.first_occurrence_ref,
        "first layer occurrence": query.first_layer_occurrence_ref,
        "second occurrence": query.second_occurrence_ref,
        "second layer occurrence": query.second_layer_occurrence_ref,
        "region or substack": query.region_or_substack_ref,
    }
    missing = [
        label
        for label, value in refs.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            f"binary rule query is missing {', '.join(missing)}",
        )
    if query.first_occurrence_ref == query.second_occurrence_ref:
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            "binary rule query requires two distinct occurrences",
        )


def _validate_binary_query_primitives(query: ManufacturingBinaryRuleQuery) -> None:
    if not isinstance(query.first, ManufacturingRuleQuery) or not isinstance(
        query.second, ManufacturingRuleQuery
    ):
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            "binary rule query requires two validated primitive queries",
        )


def _validate_binary_query_relationships(query: ManufacturingBinaryRuleQuery) -> None:
    if (
        not isinstance(query.net_relationship, str)
        or query.net_relationship not in _MANUFACTURING_RULE_NET_RELATIONSHIPS
    ):
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            f"binary rule query has invalid net relationship {query.net_relationship!r}",
        )
    if (
        not isinstance(query.layer_relationship, str)
        or query.layer_relationship not in _MANUFACTURING_RULE_LAYER_RELATIONSHIPS
    ):
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            f"binary rule query has invalid layer relationship {query.layer_relationship!r}",
        )


def _validate_binary_query_layer_consistency(
    query: ManufacturingBinaryRuleQuery,
) -> None:
    layers_are_same = (
        query.first_layer_occurrence_ref == query.second_layer_occurrence_ref
    )
    if layers_are_same != (query.layer_relationship == "same_layer"):
        raise PcbRuleResolutionError(
            "invalid_binary_rule_query",
            "binary rule layer relationship contradicts its exact layer occurrences",
        )


_ScopeFact = Literal[
    "all",
    "component_class",
    "component_designator",
    "component_footprint",
    "is_pad",
    "is_testpoint",
    "is_via",
    "layer_name",
    "locked",
    "net_class",
    "net_name",
    "polygon_definition_name",
    "testpoint_bottom",
    "testpoint_top",
]


@dataclass(frozen=True)
class _ScopePredicateDefinition:
    form: Literal["bare", "call", "equals"]
    fact: _ScopeFact
    standalone: bool


_SCOPE_PREDICATES = {
    "all": _ScopePredicateDefinition("bare", "all", True),
    "ispad": _ScopePredicateDefinition("bare", "is_pad", True),
    "isvia": _ScopePredicateDefinition("bare", "is_via", True),
    "istestpoint": _ScopePredicateDefinition("bare", "is_testpoint", False),
    "locked": _ScopePredicateDefinition("bare", "locked", False),
    "testpointtop": _ScopePredicateDefinition("equals", "testpoint_top", False),
    "testpointbottom": _ScopePredicateDefinition("equals", "testpoint_bottom", False),
    "innet": _ScopePredicateDefinition("call", "net_name", True),
    "onlayer": _ScopePredicateDefinition("call", "layer_name", True),
    "innetclass": _ScopePredicateDefinition("call", "net_class", True),
    "incomponentclass": _ScopePredicateDefinition("call", "component_class", True),
    "isnamedpolygon": _ScopePredicateDefinition(
        "call", "polygon_definition_name", True
    ),
    "incomponent": _ScopePredicateDefinition("call", "component_designator", True),
    "hasfootprint": _ScopePredicateDefinition("call", "component_footprint", True),
}

_MAX_SCOPE_DIAGNOSTIC_SOURCE_CHARS = 240


def select_manufacturing_rule(
    rules: Iterable[AltiumPcbRule],
    *,
    rule_kind: str,
    query: ManufacturingRuleQuery,
) -> AltiumPcbRule | None:
    """Select one enabled applicable rule using Altium's ascending priority."""

    expected_kind = rule_kind.strip().casefold()
    candidates = tuple(
        candidate
        for rule in rules
        if (candidate := _candidate(rule, expected_kind, query)) is not None
    )
    return _unique_priority_winner(candidates, rule_kind)


def select_manufacturing_binary_rule(
    rules: Iterable[AltiumPcbRule],
    *,
    rule_kind: str,
    query: ManufacturingBinaryRuleQuery,
) -> AltiumPcbRule | None:
    """Select one bounded binary rule from a complete two-occurrence query."""

    expected_kind = rule_kind.strip().casefold()
    candidates = tuple(
        candidate
        for rule in rules
        if (candidate := _binary_candidate(rule, expected_kind, query)) is not None
    )
    return _unique_priority_winner(candidates, rule_kind)


def _unique_priority_winner(
    candidates: tuple[tuple[int, AltiumPcbRule], ...],
    rule_kind: str,
) -> AltiumPcbRule | None:
    if not candidates:
        return None
    winning_priority = min(priority for priority, _rule in candidates)
    winners = [rule for priority, rule in candidates if priority == winning_priority]
    if len(winners) != 1:
        labels = ", ".join(_rule_label(rule) for rule in winners)
        raise PcbRuleResolutionError(
            "ambiguous_rule_priority",
            f"{rule_kind} priority {winning_priority} has multiple applicable rules: {labels}",
        )
    return winners[0]


def _candidate(
    rule: AltiumPcbRule,
    expected_kind: str,
    query: ManufacturingRuleQuery,
) -> tuple[int, AltiumPcbRule] | None:
    if rule.rule_kind.strip().casefold() != expected_kind:
        return None
    if rule.enabled is None:
        raise PcbRuleResolutionError(
            "invalid_rule_enabled",
            f"{_rule_label(rule)} has no valid enabled state",
        )
    if rule.enabled is False or not _rule_applies(rule, query):
        return None
    if rule.priority is None or rule.priority < 1:
        raise PcbRuleResolutionError(
            "invalid_rule_priority",
            f"{_rule_label(rule)} has no positive priority",
        )
    return rule.priority, rule


def _binary_candidate(
    rule: AltiumPcbRule,
    expected_kind: str,
    query: ManufacturingBinaryRuleQuery,
) -> tuple[int, AltiumPcbRule] | None:
    if rule.rule_kind.strip().casefold() != expected_kind:
        return None
    if rule.enabled is None:
        raise PcbRuleResolutionError(
            "invalid_rule_enabled",
            f"{_rule_label(rule)} has no valid enabled state",
        )
    if rule.enabled is False or not _binary_rule_applies(rule, query):
        return None
    if rule.priority is None or rule.priority < 1:
        raise PcbRuleResolutionError(
            "invalid_rule_priority",
            f"{_rule_label(rule)} has no positive priority",
        )
    return rule.priority, rule


def _rule_applies(rule: AltiumPcbRule, query: ManufacturingRuleQuery) -> bool:
    if rule.scope2_expression.strip().casefold() != "all":
        raise PcbRuleResolutionError(
            "unsupported_rule_scope",
            f"{_rule_label(rule)} has unsupported second scope {rule.scope2_expression!r}",
        )
    return _evaluate_scope(rule.scope1_expression, query, rule)


def _binary_rule_applies(
    rule: AltiumPcbRule,
    query: ManufacturingBinaryRuleQuery,
) -> bool:
    scope1_first = _evaluate_scope(rule.scope1_expression, query.first, rule)
    scope2_second = _evaluate_scope(rule.scope2_expression, query.second, rule)
    scope1_second = _evaluate_scope(rule.scope1_expression, query.second, rule)
    scope2_first = _evaluate_scope(rule.scope2_expression, query.first, rule)
    net_scope_applies = _net_scope_applies(rule, query)
    layer_kind_applies = _layer_kind_applies(rule, query)
    return (
        net_scope_applies
        and layer_kind_applies
        and ((scope1_first and scope2_second) or (scope1_second and scope2_first))
    )


def _net_scope_applies(
    rule: AltiumPcbRule,
    query: ManufacturingBinaryRuleQuery,
) -> bool:
    normalized = "".join(rule.net_scope.strip().casefold().split())
    accepted: dict[str, ManufacturingRuleNetRelationship | None] = {
        "anynet": None,
        "enetscope_anynet": None,
        "differentnets": "different_nets",
        "enetscope_differentnetsonly": "different_nets",
        "samenet": "same_net",
        "enetscope_samenetonly": "same_net",
        "differentdiffpairs": "different_diff_pairs",
        "enetscope_differentdiffpairsonly": "different_diff_pairs",
        "samediffpairs": "same_diff_pair",
        "samediffpair": "same_diff_pair",
        "enetscope_samediffpaironly": "same_diff_pair",
    }
    try:
        required = accepted[normalized]
    except KeyError as exc:
        raise PcbRuleResolutionError(
            "unsupported_rule_subscope",
            f"{_rule_label(rule)} has unsupported net scope {rule.net_scope!r}",
        ) from exc
    return required is None or query.net_relationship == required


def _layer_kind_applies(
    rule: AltiumPcbRule,
    query: ManufacturingBinaryRuleQuery,
) -> bool:
    normalized = "".join(rule.layer_kind.strip().casefold().split())
    accepted: dict[str, ManufacturingRuleLayerRelationship] = {
        "samelayer": "same_layer",
        "erulelayerkind_samelayer": "same_layer",
        "adjacentlayer": "adjacent_layer",
        "erulelayerkind_adjacentlayer": "adjacent_layer",
    }
    try:
        required = accepted[normalized]
    except KeyError as exc:
        raise PcbRuleResolutionError(
            "unsupported_rule_subscope",
            f"{_rule_label(rule)} has unsupported layer kind {rule.layer_kind!r}",
        ) from exc
    return query.layer_relationship == required


def _evaluate_scope(
    expression: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    try:
        parsed = _parse_scope_expression(expression)
    except _ScopeExpressionSyntaxError as exc:
        raise PcbRuleResolutionError(
            "invalid_rule_scope_syntax",
            f"{_rule_label(rule)} has malformed scope expression at offset "
            f"{exc.position}: {exc.detail}; "
            f"{_scope_source_context(expression, exc.position)}",
        ) from exc
    return _evaluate_parsed_scope(parsed, expression, query, rule)


def _evaluate_parsed_scope(
    parsed: _ScopeExpression,
    source_text: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    if isinstance(parsed, _ScopePredicate):
        definition = _predicate_definition(parsed)
        if definition is not None and definition.standalone:
            return _predicate_applies(parsed, definition, query, rule)
    via_negative = _via_negative_pair_applies(parsed, query, rule)
    if via_negative is not None:
        return via_negative
    testpoint_pair = _testpoint_pair(parsed)
    if testpoint_pair is not None:
        return all(
            _predicate_applies(predicate, definition, query, rule)
            for predicate, definition in testpoint_pair
        )
    raise PcbRuleResolutionError(
        "unsupported_rule_scope",
        f"{_rule_label(rule)} has unsupported scope expression; "
        f"{_scope_source_context(source_text)}",
    )


def _scope_source_context(source_text: str, position: int | None = None) -> str:
    if len(source_text) <= _MAX_SCOPE_DIAGNOSTIC_SOURCE_CHARS:
        excerpt = source_text
    else:
        center = position if position is not None else 0
        start = max(0, center - _MAX_SCOPE_DIAGNOSTIC_SOURCE_CHARS // 2)
        end = min(len(source_text), start + _MAX_SCOPE_DIAGNOSTIC_SOURCE_CHARS)
        start = max(0, end - _MAX_SCOPE_DIAGNOSTIC_SOURCE_CHARS)
        excerpt = source_text[start:end]
        if start:
            excerpt = "..." + excerpt
        if end < len(source_text):
            excerpt += "..."
    return f"source_length={len(source_text)}; source_excerpt={excerpt!r}"


def _predicate_definition(
    predicate: _ScopePredicate,
) -> _ScopePredicateDefinition | None:
    definition = _SCOPE_PREDICATES.get(predicate.name)
    if definition is None or predicate.form != definition.form:
        return None
    if definition.form == "bare":
        return (
            definition
            if predicate.argument is None and predicate.quote_style is None
            else None
        )
    return (
        definition
        if predicate.argument is not None and predicate.quote_style == "single"
        else None
    )


def _predicate_applies(
    predicate: _ScopePredicate,
    definition: _ScopePredicateDefinition,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    expected = predicate.argument
    if definition.fact in {"all", "is_pad", "is_via", "is_testpoint", "locked"}:
        return _simple_predicate_applies(definition.fact, query)
    if definition.fact in {"testpoint_top", "testpoint_bottom"}:
        return _testpoint_predicate_applies(
            definition.fact,
            _scope_boolean(expected, predicate, rule),
            query,
        )
    assert expected is not None
    return _named_predicate_applies(
        predicate,
        definition.fact,
        expected,
        query,
        rule,
    )


def _simple_predicate_applies(
    fact: _ScopeFact,
    query: ManufacturingRuleQuery,
) -> bool:
    values = {
        "all": True,
        "is_pad": query.object_kind in {"pad", "smd_pad", "th_pad"},
        "is_via": query.object_kind == "via",
        "is_testpoint": query.is_testpoint,
        "locked": query.locked,
    }
    return values[fact]


def _testpoint_predicate_applies(
    fact: _ScopeFact,
    expected: bool,
    query: ManufacturingRuleQuery,
) -> bool:
    actual = query.testpoint_top if fact == "testpoint_top" else query.testpoint_bottom
    return actual == expected


def _named_predicate_applies(
    predicate: _ScopePredicate,
    fact: _ScopeFact,
    expected: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    if fact == "net_class":
        return _net_class_scope_applies(expected, query, rule)
    if fact == "component_class":
        return _component_class_scope_applies(expected, query, rule)
    if fact == "polygon_definition_name":
        return _polygon_definition_scope_applies(expected, query, rule)
    if fact in {"component_footprint", "component_designator"}:
        return _component_scope_applies(fact, expected, query, rule)
    actual = query.net_name if fact == "net_name" else query.layer_name
    if actual is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires an exact {predicate.name} query fact",
        )
    return actual.casefold() == expected.casefold()


def _polygon_definition_scope_applies(
    expected: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    if rule.rule_kind.strip().casefold() != "polygonconnect":
        raise PcbRuleResolutionError(
            "unsupported_rule_scope",
            f"{_rule_label(rule)} uses isnamedpolygon outside proved PolygonConnect authority",
        )
    if query.object_kind != "polygon":
        return False
    actual = query.polygon_definition_name
    authority = query.polygon_definition_authority_names
    if authority is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires exact isnamedpolygon definition authority",
        )
    expected_count = authority.count(expected)
    if expected_count == 0:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} names an unresolved polygon definition {expected!r}",
        )
    if expected_count != 1:
        raise PcbRuleResolutionError(
            "corrupt_identity",
            f"{_rule_label(rule)} encounters ambiguous polygon definition identity",
        )
    return actual == expected


def _scope_boolean(
    value: str | None,
    predicate: _ScopePredicate,
    rule: AltiumPcbRule,
) -> bool:
    normalized = value.casefold() if value is not None else ""
    if normalized not in {"true", "false"}:
        raise PcbRuleResolutionError(
            "unsupported_rule_scope",
            f"{_rule_label(rule)} has unsupported boolean comparison "
            f"{predicate.source_text!r}",
        )
    return normalized == "true"


def _via_negative_predicates(
    expression: _ScopeExpression,
) -> tuple[_ScopePredicate, _ScopePredicate] | None:
    if not isinstance(expression, _ScopeAnd):
        return None
    positive = expression.left
    if not isinstance(positive, _ScopePredicate) or not _is_predicate(
        positive, "isvia"
    ):
        return None
    if not isinstance(expression.right, _ScopeNot):
        return None
    negative = expression.right.operand
    if not isinstance(negative, _ScopePredicate):
        return None
    if not (
        _is_predicate(negative, "istestpoint") or _is_predicate(negative, "locked")
    ):
        return None
    return positive, negative


def _via_negative_pair_applies(
    expression: _ScopeExpression,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool | None:
    predicates = _via_negative_predicates(expression)
    if predicates is None:
        return None
    positive, negative = predicates
    return _predicate_applies(
        positive,
        _SCOPE_PREDICATES["isvia"],
        query,
        rule,
    ) and not _predicate_applies(
        negative,
        _SCOPE_PREDICATES[negative.name],
        query,
        rule,
    )


def _testpoint_pair(
    expression: _ScopeExpression,
) -> (
    tuple[
        tuple[_ScopePredicate, _ScopePredicateDefinition],
        tuple[_ScopePredicate, _ScopePredicateDefinition],
    ]
    | None
):
    predicates = _binary_predicates(expression)
    if predicates is None:
        return None
    if {predicate.name for predicate in predicates} != {
        "testpointtop",
        "testpointbottom",
    }:
        return None
    definitions = tuple(_predicate_definition(predicate) for predicate in predicates)
    if any(definition is None for definition in definitions):
        return None
    first_definition = definitions[0]
    second_definition = definitions[1]
    assert first_definition is not None
    assert second_definition is not None
    return (
        (predicates[0], first_definition),
        (predicates[1], second_definition),
    )


def _binary_predicates(
    expression: _ScopeExpression,
) -> tuple[_ScopePredicate, _ScopePredicate] | None:
    if not isinstance(expression, _ScopeAnd):
        return None
    if not isinstance(expression.left, _ScopePredicate):
        return None
    if not isinstance(expression.right, _ScopePredicate):
        return None
    return expression.left, expression.right


def _is_predicate(expression: _ScopeExpression, name: str) -> bool:
    return (
        isinstance(expression, _ScopePredicate)
        and expression.name == name
        and _predicate_definition(expression) is not None
    )


def _net_class_scope_applies(
    expected: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    memberships = query.net_class_names
    authority = query.net_class_authority_names
    if memberships is None or authority is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires exact innetclass query authority",
        )
    expected_name = expected.casefold()
    if expected_name not in {name.casefold() for name in authority}:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} names an unresolved net class {expected!r}",
        )
    return expected_name in {name.casefold() for name in memberships}


def _component_class_scope_applies(
    expected: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    if not query.component_ownership_exact:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires exact incomponentclass ownership authority",
        )
    memberships = query.component_class_names
    authority = query.component_class_authority_names
    if memberships is None or authority is None:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires exact incomponentclass query authority",
        )
    expected_name = expected.casefold()
    if expected_name not in {name.casefold() for name in authority}:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} names an unresolved component class {expected!r}",
        )
    return expected_name in {name.casefold() for name in memberships}


def _component_scope_applies(
    fact: _ScopeFact,
    expected: str,
    query: ManufacturingRuleQuery,
    rule: AltiumPcbRule,
) -> bool:
    if not query.component_ownership_exact:
        raise PcbRuleResolutionError(
            "unresolved_rule_query_facts",
            f"{_rule_label(rule)} requires exact {fact} ownership facts",
        )
    actual = (
        query.component_footprint
        if fact == "component_footprint"
        else query.component_designator
    )
    return actual is not None and actual.casefold() == expected.casefold()


def _rule_label(rule: AltiumPcbRule) -> str:
    identity = rule.unique_id.strip() or rule.name.strip() or f"index {rule.index}"
    return f"{rule.rule_kind} rule {identity!r}"


__all__ = (
    "ManufacturingBinaryRuleQuery",
    "ManufacturingRuleLayerRelationship",
    "ManufacturingRuleNetRelationship",
    "ManufacturingRuleObjectKind",
    "ManufacturingRuleQuery",
    "PcbRuleResolutionError",
    "select_manufacturing_binary_rule",
    "select_manufacturing_rule",
)
