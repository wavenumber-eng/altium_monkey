from __future__ import annotations

"""Typed PCB design-rule models parsed from Altium Rules6/Data."""

import re
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

from .altium_api_markers import public_api


RuleValueParser = Callable[[str], Any]
RuleValueSerializer = Callable[[Any], str]


def _parse_string(value: str) -> str:
    return str(value or "")


def _serialize_string(value: Any) -> str:
    return "" if value is None else str(value)


def _parse_bool(value: str) -> bool | None:
    text = str(value or "").strip()
    if not text:
        return None
    upper = text.upper()
    if upper in {"TRUE", "T", "1", "YES"}:
        return True
    if upper in {"FALSE", "F", "0", "NO"}:
        return False
    return None


def _serialize_bool(value: Any) -> str:
    return "TRUE" if bool(value) else "FALSE"


def _parse_int(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _serialize_int(value: Any) -> str:
    return "" if value is None else str(int(value))


@dataclass(frozen=True)
class AltiumRuleFieldSpec:
    raw_key: str
    attr_name: str
    parser: RuleValueParser = _parse_string
    serializer: RuleValueSerializer = _serialize_string


@dataclass
class AltiumRuleScopeExpression:
    raw_expression: str = ""
    predicate: str = ""
    arguments: tuple[str, ...] = ()
    is_all: bool = False

    @classmethod
    def from_expression(cls, expression: str) -> "AltiumRuleScopeExpression":
        text = str(expression or "").strip()
        if not text:
            return cls(raw_expression="")
        if text.upper() == "ALL":
            return cls(raw_expression=text, predicate="All", arguments=(), is_all=True)
        match = re.match(r"^\s*([A-Za-z0-9_]+)\((.*)\)\s*$", text)
        if not match:
            return cls(raw_expression=text, predicate=text)
        predicate = match.group(1)
        args_text = match.group(2).strip()
        args: list[str] = []
        token: list[str] = []
        in_quote = False
        i = 0
        while i < len(args_text):
            char = args_text[i]
            if char == "'":
                if in_quote and i + 1 < len(args_text) and args_text[i + 1] == "'":
                    token.append("'")
                    i += 2
                    continue
                in_quote = not in_quote
                i += 1
                continue
            if char == "," and not in_quote:
                part = "".join(token).strip()
                if part:
                    args.append(part)
                token = []
                i += 1
                continue
            token.append(char)
            i += 1
        part = "".join(token).strip()
        if part:
            args.append(part)
        return cls(raw_expression=text, predicate=predicate, arguments=tuple(args))


@dataclass
class AltiumLayerMetricRange:
    minimum: str = ""
    preferred: str = ""
    maximum: str = ""


@dataclass
class AltiumDiffPairLayerMetrics:
    minimum_width: str = ""
    preferred_width: str = ""
    maximum_width: str = ""
    minimum_gap: str = ""
    preferred_gap: str = ""
    maximum_gap: str = ""


@dataclass
class AltiumConnectStyleSettings:
    connect_style: str = ""
    relief_air_gap: str = ""
    air_gap_width: str = ""
    relief_conductor_width: str = ""
    relief_entries: str = ""
    relief_expansion: str = ""
    relief_angle: str = ""


@dataclass
class AltiumRoomOutlineVertex:
    kind: str = ""
    vx: str = ""
    vy: str = ""
    cx: str = ""
    cy: str = ""
    start_angle: str = ""
    end_angle: str = ""
    radius: str = ""


@dataclass
class AltiumClearanceObjectPair:
    raw_object_a_kind: str = ""
    raw_object_b_kind: str = ""
    object_a_kind: str = ""
    object_b_kind: str = ""
    clearance: str = ""


def _normalize_clearance_object_kind(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("ClearanceObj_"):
        return text[len("ClearanceObj_") :]
    return text


_COMMON_FIELD_SPECS: tuple[AltiumRuleFieldSpec, ...] = (
    AltiumRuleFieldSpec("SELECTION", "selection", _parse_bool, _serialize_bool),
    AltiumRuleFieldSpec("LAYER", "layer"),
    AltiumRuleFieldSpec("LOCKED", "locked", _parse_bool, _serialize_bool),
    AltiumRuleFieldSpec(
        "POLYGONOUTLINE", "polygon_outline", _parse_bool, _serialize_bool
    ),
    AltiumRuleFieldSpec("USERROUTED", "user_routed", _parse_bool, _serialize_bool),
    AltiumRuleFieldSpec("KEEPOUT", "keepout", _parse_bool, _serialize_bool),
    AltiumRuleFieldSpec("UNIONINDEX", "union_index", _parse_int, _serialize_int),
    AltiumRuleFieldSpec("RULEKIND", "rule_kind"),
    AltiumRuleFieldSpec("NETSCOPE", "net_scope"),
    AltiumRuleFieldSpec("LAYERKIND", "layer_kind"),
    AltiumRuleFieldSpec("SCOPE1EXPRESSION", "scope1_expression"),
    AltiumRuleFieldSpec("SCOPE2EXPRESSION", "scope2_expression"),
    AltiumRuleFieldSpec("NAME", "name"),
    AltiumRuleFieldSpec("ENABLED", "enabled", _parse_bool, _serialize_bool),
    AltiumRuleFieldSpec("PRIORITY", "priority", _parse_int, _serialize_int),
    AltiumRuleFieldSpec("COMMENT", "comment"),
    AltiumRuleFieldSpec("UNIQUEID", "unique_id"),
    AltiumRuleFieldSpec(
        "DEFINEDBYLOGICALDOCUMENT",
        "defined_by_logical_document",
        _parse_bool,
        _serialize_bool,
    ),
)

_SIMPLE_RULE_FIELDS: dict[str, tuple[AltiumRuleFieldSpec, ...]] = {
    "ROUTINGTOPOLOGY": (AltiumRuleFieldSpec("TOPOLOGY", "topology"),),
    "HEIGHT": (
        AltiumRuleFieldSpec("MINHEIGHT", "minimum_height"),
        AltiumRuleFieldSpec("PREFHEIGHT", "preferred_height"),
        AltiumRuleFieldSpec("MAXHEIGHT", "maximum_height"),
    ),
    "ROUTINGCORNERS": (
        AltiumRuleFieldSpec("CORNERSTYLE", "corner_style"),
        AltiumRuleFieldSpec("MINSETBACK", "minimum_setback"),
        AltiumRuleFieldSpec("MAXSETBACK", "maximum_setback"),
    ),
    "ROUTINGPRIORITY": (AltiumRuleFieldSpec("ROUTINGPRIORITY", "routing_priority"),),
    "FANOUTCONTROL": (
        AltiumRuleFieldSpec("FANOUTSTYLE", "fanout_style"),
        AltiumRuleFieldSpec("FANOUTDIRECTION", "fanout_direction"),
        AltiumRuleFieldSpec("BGADIR", "bga_direction"),
        AltiumRuleFieldSpec("BGAVIAMODE", "bga_via_mode"),
        AltiumRuleFieldSpec("VIAGRID", "via_grid"),
    ),
    "SUPPLYNETS": (AltiumRuleFieldSpec("VOLTAGE", "voltage"),),
    "MINIMUMSOLDERMASKSLIVER": (
        AltiumRuleFieldSpec("MINSOLDERMASKWIDTH", "minimum_solder_mask_width"),
    ),
    "NETANTENNAE": (
        AltiumRuleFieldSpec("NETANTENNAETOLERANCE", "net_antennae_tolerance"),
    ),
    "SILKTOSILKCLEARANCE": (
        AltiumRuleFieldSpec("SILKTOSILKCLEARANCE", "silk_to_silk_clearance"),
    ),
    "SILKTOSOLDERMASKCLEARANCE": (
        AltiumRuleFieldSpec("MINSILKSCREENTOMASKGAP", "minimum_silkscreen_to_mask_gap"),
        AltiumRuleFieldSpec("CLEARANCETOEXPOSEDCOPPER", "clearance_to_exposed_copper"),
    ),
    "SHORTCIRCUIT": (
        AltiumRuleFieldSpec("ALLOWED", "allowed", _parse_bool, _serialize_bool),
    ),
    "COMPONENTCLEARANCE": (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec("VERTICALGAP", "vertical_gap"),
        AltiumRuleFieldSpec("COLLISIONCHECKMODE", "collision_check_mode"),
        AltiumRuleFieldSpec(
            "SHOWDISTANCES", "show_distances", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "DONOTCHECKWITHOUT3DBODY",
            "do_not_check_without_3d_body",
            _parse_bool,
            _serialize_bool,
        ),
    ),
    "LENGTH": (
        AltiumRuleFieldSpec("MINLIMIT", "minimum_length"),
        AltiumRuleFieldSpec("MAXLIMIT", "maximum_length"),
        AltiumRuleFieldSpec("MINDELAY", "minimum_delay"),
        AltiumRuleFieldSpec("MAXDELAY", "maximum_delay"),
        AltiumRuleFieldSpec(
            "USEDELAYUNITS", "use_delay_units", _parse_bool, _serialize_bool
        ),
    ),
    "MATCHEDLENGTHS": (
        AltiumRuleFieldSpec("TOLERANCE", "tolerance"),
        AltiumRuleFieldSpec("DELAYTOLERANCE", "delay_tolerance"),
        AltiumRuleFieldSpec("TARGETSOURCENAME", "target_source_name"),
        AltiumRuleFieldSpec(
            "USEDELAYUNITS", "use_delay_units", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "CHECKNETSINDIFFPAIR",
            "check_nets_in_diff_pair",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "CHECKDIFFPAIRVSDIFFPAIR",
            "check_diff_pair_vs_diff_pair",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "CHECKOTHERS", "check_others", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "CHECKXSIGNALS", "check_xsignals", _parse_bool, _serialize_bool
        ),
    ),
    "MINIMUMANNULARRING": (AltiumRuleFieldSpec("MINIMUMRING", "minimum_ring"),),
    "SILKSCREENOVERCOMPONENTPADS": (
        AltiumRuleFieldSpec("MINSILKSCREENTOMASKGAP", "minimum_silkscreen_to_mask_gap"),
    ),
    "VIASUNDERSMD": (
        AltiumRuleFieldSpec("ALLOWED", "allowed", _parse_bool, _serialize_bool),
    ),
    "ASSEMBLYTESTPOINT": (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec("GRIDTOLERANCE", "grid_tolerance"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("USEGRID", "use_grid", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec(
            "ALLOWSIDETOP", "allow_side_top", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWSIDEBOTTOM", "allow_side_bottom", _parse_bool, _serialize_bool
        ),
    ),
    "FABRICATIONTESTPOINT": (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("SIDE", "side"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec("GRIDTOLERANCE", "grid_tolerance"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("USEGRID", "use_grid", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec(
            "ALLOWSIDETOP", "allow_side_top", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWSIDEBOTTOM", "allow_side_bottom", _parse_bool, _serialize_bool
        ),
    ),
    "ASSEMBLYTESTPOINTUSAGE": (),
    "FABRICATIONTESTPOINTUSAGE": (
        AltiumRuleFieldSpec(
            "ALLOWMULTIPLE", "allow_multiple", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("VALID", "valid", _parse_bool, _serialize_bool),
    ),
    "TESTPOINT": (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("SIDE", "side"),
        AltiumRuleFieldSpec("STYLE", "style"),
        AltiumRuleFieldSpec("ORDER", "order"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
    ),
    "TESTPOINTUSAGE": (
        AltiumRuleFieldSpec(
            "ALLOWMULTIPLE", "allow_multiple", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("VALID", "valid", _parse_bool, _serialize_bool),
    ),
    "UNPOUREDPOLYGON": (),
    "UNROUTEDNET": (),
    "SILKTOBOARDREGIONCLEARANCE": (),
}

_SIMPLE_RULE_FIELDS.update(
    {
        "MAXMINLENGTH": _SIMPLE_RULE_FIELDS["LENGTH"],
        "MATCHEDNLENGTH": _SIMPLE_RULE_FIELDS["MATCHEDLENGTHS"],
        "MAXMINHEIGHT": _SIMPLE_RULE_FIELDS["HEIGHT"],
        "ASSYTESTPOINTSTYLE": _SIMPLE_RULE_FIELDS["ASSEMBLYTESTPOINT"],
        "ASSYTESTPOINTUSAGE": _SIMPLE_RULE_FIELDS["ASSEMBLYTESTPOINTUSAGE"],
        "TESTPOINTSTYLE": _SIMPLE_RULE_FIELDS["FABRICATIONTESTPOINT"],
        "BROKENNET": _SIMPLE_RULE_FIELDS["UNROUTEDNET"],
        "ROUTINGCORNERSTYLE": _SIMPLE_RULE_FIELDS["ROUTINGCORNERS"],
        "SILKTOBOARDREGION": _SIMPLE_RULE_FIELDS["SILKTOBOARDREGIONCLEARANCE"],
    }
)

_RULE_KIND_REGISTRY: dict[str, type["AltiumPcbRule"]] = {}


def _register_rule(cls: type["AltiumPcbRule"]) -> type["AltiumPcbRule"]:
    _RULE_KIND_REGISTRY[cls.RULE_KIND_NAME.upper()] = cls
    for alias in getattr(cls, "RULE_KIND_ALIASES", ()):
        alias_text = str(alias or "").strip().upper()
        if alias_text:
            _RULE_KIND_REGISTRY[alias_text] = cls
    return cls


@public_api
@dataclass
class AltiumPcbRule:
    """Typed PCB rule parsed from an Altium Rules6/Data rule record."""

    index: int = 0
    selection: bool | None = None
    layer: str = ""
    locked: bool | None = None
    polygon_outline: bool | None = None
    user_routed: bool | None = None
    keepout: bool | None = None
    union_index: int | None = None
    rule_kind: str = ""
    net_scope: str = ""
    layer_kind: str = ""
    scope1_expression: str = ""
    scope2_expression: str = ""
    scope1: AltiumRuleScopeExpression = field(default_factory=AltiumRuleScopeExpression)
    scope2: AltiumRuleScopeExpression = field(default_factory=AltiumRuleScopeExpression)
    name: str = ""
    enabled: bool | None = None
    priority: int | None = None
    comment: str = ""
    unique_id: str = ""
    defined_by_logical_document: bool | None = None
    semantic_values: dict[str, object] = field(default_factory=dict)
    extra_fields: dict[str, str] = field(default_factory=dict)
    raw_record: dict[str, object] = field(default_factory=dict)
    raw_record_payload: bytes = field(default=b"", repr=False)
    record_leader: bytes = field(default=b"", repr=False)
    _raw_record_signature: tuple[tuple[str, str], ...] = field(
        default_factory=tuple, repr=False
    )
    _record_order: tuple[str, ...] = field(default_factory=tuple, repr=False)

    RULE_KIND_NAME: ClassVar[str] = ""
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ()
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = ()
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = True

    def __post_init__(self) -> None:
        if not self._raw_record_signature:
            self._raw_record_signature = self._state_signature()

    def __getattr__(self, name: str) -> Any:
        semantic_values = self.__dict__.get("semantic_values", {})
        if name in semantic_values:
            return semantic_values[name]
        raise AttributeError(name)

    @classmethod
    def from_record(
        cls,
        record: dict[str, object],
        *,
        index: int = 0,
        record_leader: bytes | None = None,
        record_payload: bytes | None = None,
    ) -> "AltiumPcbRule":
        normalized = {
            str(key or "").strip().upper(): _serialize_string(value)
            for key, value in (record or {}).items()
        }
        order = tuple(
            str(key or "").strip().upper()
            for key in (record or {}).keys()
            if str(key or "").strip()
        )
        rule_kind = normalized.get("RULEKIND", "")
        target_cls = _RULE_KIND_REGISTRY.get(rule_kind.upper(), cls)
        return target_cls._from_normalized_record(
            normalized,
            order=order,
            index=index,
            record_leader=record_leader,
            record_payload=record_payload,
        )

    @classmethod
    def _from_normalized_record(
        cls,
        record: dict[str, str],
        *,
        order: tuple[str, ...],
        index: int,
        record_leader: bytes | None = None,
        record_payload: bytes | None = None,
    ) -> "AltiumPcbRule":
        kwargs: dict[str, Any] = {"index": index}
        consumed: set[str] = set()
        for spec in _COMMON_FIELD_SPECS:
            if spec.raw_key in record:
                kwargs[spec.attr_name] = spec.parser(record[spec.raw_key])
                consumed.add(spec.raw_key)
        specific_kwargs, specific_consumed = cls._parse_specific_fields(record)
        kwargs.update(specific_kwargs)
        consumed.update(specific_consumed)
        semantic_values: dict[str, Any] = {}
        if cls.USE_SIMPLE_FIELD_PARSE:
            simple_specs = _SIMPLE_RULE_FIELDS.get(
                str(kwargs.get("rule_kind", "")).upper(), ()
            )
            for spec in simple_specs:
                if spec.raw_key in record:
                    semantic_values[spec.attr_name] = spec.parser(record[spec.raw_key])
                    consumed.add(spec.raw_key)
        kwargs["semantic_values"] = semantic_values
        kwargs["scope1"] = AltiumRuleScopeExpression.from_expression(
            kwargs.get("scope1_expression", "")
        )
        kwargs["scope2"] = AltiumRuleScopeExpression.from_expression(
            kwargs.get("scope2_expression", "")
        )
        kwargs["extra_fields"] = {
            key: value for key, value in record.items() if key not in consumed
        }
        kwargs["raw_record"] = dict(record)
        kwargs["raw_record_payload"] = bytes(record_payload or b"")
        kwargs["record_leader"] = bytes(record_leader or b"")
        kwargs["_record_order"] = order
        return cls(**kwargs)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        values: dict[str, Any] = {}
        consumed: set[str] = set()
        for spec in cls.RULE_FIELDS:
            if spec.raw_key in record:
                values[spec.attr_name] = spec.parser(record[spec.raw_key])
                consumed.add(spec.raw_key)
        return values, consumed

    def _serialize_field_specs(
        self, specs: tuple[AltiumRuleFieldSpec, ...]
    ) -> dict[str, str]:
        serialized: dict[str, str] = {}
        for spec in specs:
            if not hasattr(self, spec.attr_name):
                continue
            value = getattr(self, spec.attr_name)
            if value is None and spec.raw_key in self.raw_record:
                serialized[spec.raw_key] = _serialize_string(
                    self.raw_record[spec.raw_key]
                )
                continue
            if value is None:
                continue
            serialized[spec.raw_key] = spec.serializer(value)
        return serialized

    def _serialize_specific_fields(self) -> dict[str, str]:
        return self._serialize_field_specs(self.RULE_FIELDS)

    def to_record(self) -> dict[str, str]:
        field_map: dict[str, str] = {}
        field_map.update(self._serialize_field_specs(_COMMON_FIELD_SPECS))
        field_map.update(self._serialize_specific_fields())
        if self.USE_SIMPLE_FIELD_PARSE:
            for spec in _SIMPLE_RULE_FIELDS.get(self.rule_kind.upper(), ()):
                if spec.attr_name in self.semantic_values:
                    field_map[spec.raw_key] = spec.serializer(
                        self.semantic_values[spec.attr_name]
                    )
                elif spec.raw_key in self.raw_record:
                    field_map[spec.raw_key] = _serialize_string(
                        self.raw_record[spec.raw_key]
                    )
        field_map.update(
            {key: _serialize_string(value) for key, value in self.extra_fields.items()}
        )

        ordered_keys: list[str] = []
        for key in self._record_order:
            key_upper = str(key or "").strip().upper()
            if key_upper and key_upper in field_map and key_upper not in ordered_keys:
                ordered_keys.append(key_upper)
        trailing_specs = (
            _SIMPLE_RULE_FIELDS.get(self.rule_kind.upper(), ())
            if self.USE_SIMPLE_FIELD_PARSE
            else ()
        )
        for spec in _COMMON_FIELD_SPECS + self.RULE_FIELDS + trailing_specs:
            if spec.raw_key in field_map and spec.raw_key not in ordered_keys:
                ordered_keys.append(spec.raw_key)
        for key in sorted(field_map):
            if key not in ordered_keys:
                ordered_keys.append(key)

        record = {key: field_map[key] for key in ordered_keys}
        self.raw_record = dict(record)
        self._record_order = tuple(ordered_keys)
        return record

    def source_record(self) -> dict[str, str]:
        """Return a serialized record view for downstream passthrough consumers."""
        return dict(self.to_record())

    def _state_signature(self) -> tuple[tuple[str, str], ...]:
        record = self.to_record()
        return tuple((str(key), str(value)) for key, value in record.items())

    def can_passthrough_raw_payload(self) -> bool:
        return (
            bool(self.raw_record_payload)
            and self._raw_record_signature == self._state_signature()
        )


@_register_rule
@dataclass
class AltiumBackDrillingRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "BackDrilling"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXSTUBLENGTH", "max_stub_length"),
        AltiumRuleFieldSpec(
            "USETOPBACKDRILL", "use_top_backdrill", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "USEBOTTOMBACKDRILL", "use_bottom_backdrill", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("HOLEOVERSIZE", "hole_oversize"),
        AltiumRuleFieldSpec("HOLETOLPLUS", "hole_tolerance_plus"),
        AltiumRuleFieldSpec("HOLETOLMINUS", "hole_tolerance_minus"),
    )
    max_stub_length: str = ""
    use_top_backdrill: bool | None = None
    use_bottom_backdrill: bool | None = None
    hole_oversize: str = ""
    hole_tolerance_plus: str = ""
    hole_tolerance_minus: str = ""


@_register_rule
@dataclass
class AltiumLayerPairsRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "LayerPairs"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ENFORCE", "enforce_layer_pair_settings", _parse_bool, _serialize_bool
        ),
    )
    enforce_layer_pair_settings: bool | None = None


@_register_rule
@dataclass
class AltiumHoleSizeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "HoleSize"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("MaxMinHoleSize",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ABSOLUTEVALUES", "absolute_values", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("MINLIMIT", "minimum"),
        AltiumRuleFieldSpec("MAXLIMIT", "maximum"),
        AltiumRuleFieldSpec("MINPERCENT", "minimum_percent"),
        AltiumRuleFieldSpec("MAXPERCENT", "maximum_percent"),
    )
    absolute_values: bool | None = None
    minimum: str = ""
    maximum: str = ""
    minimum_percent: str = ""
    maximum_percent: str = ""


@_register_rule
@dataclass
class AltiumHoleToHoleClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "HoleToHoleClearance"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec(
            "ALLOWSTACKEDMICROVIAS",
            "allow_stacked_microvias",
            _parse_bool,
            _serialize_bool,
        ),
    )
    gap: str = ""
    allow_stacked_microvias: bool | None = None


@_register_rule
@dataclass
class AltiumRoutingViasRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingVias"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("RoutingViaStyle",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("VIASTYLE", "via_style"),
        AltiumRuleFieldSpec("MINWIDTH", "minimum_width"),
        AltiumRuleFieldSpec("WIDTH", "preferred_width"),
        AltiumRuleFieldSpec("MAXWIDTH", "maximum_width"),
        AltiumRuleFieldSpec("MINHOLEWIDTH", "minimum_hole_width"),
        AltiumRuleFieldSpec("HOLEWIDTH", "preferred_hole_width"),
        AltiumRuleFieldSpec("MAXHOLEWIDTH", "maximum_hole_width"),
    )
    via_style: str = ""
    minimum_width: str = ""
    preferred_width: str = ""
    maximum_width: str = ""
    minimum_hole_width: str = ""
    preferred_hole_width: str = ""
    maximum_hole_width: str = ""


@_register_rule
@dataclass
class AltiumSolderMaskExpansionRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SolderMaskExpansion"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("EXPANSION", "top_expansion"),
        AltiumRuleFieldSpec("EXPANSIONBOTTOM", "bottom_expansion"),
        AltiumRuleFieldSpec("FROMHOLEEDGE", "from_hole_edge"),
        AltiumRuleFieldSpec(
            "USESEPARATEEXPANSIONS",
            "use_separate_expansions",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "ISTENTINGTOP", "is_tenting_top", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ISTENTINGBOTTOM", "is_tenting_bottom", _parse_bool, _serialize_bool
        ),
    )
    top_expansion: str = ""
    bottom_expansion: str = ""
    from_hole_edge: str = ""
    use_separate_expansions: bool | None = None
    is_tenting_top: bool | None = None
    is_tenting_bottom: bool | None = None


@_register_rule
@dataclass
class AltiumPasteMaskExpansionRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "PasteMaskExpansion"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("EXPANSION", "expansion"),
        AltiumRuleFieldSpec("PERCENTS", "percents"),
        AltiumRuleFieldSpec("USEPASTE", "use_paste", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec(
            "USEPERCENTS", "use_percents", _parse_bool, _serialize_bool
        ),
    )
    expansion: str = ""
    percents: str = ""
    use_paste: bool | None = None
    use_percents: bool | None = None


@_register_rule
@dataclass
class AltiumClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Clearance"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec("GENERICCLEARANCE", "generic_clearance"),
        AltiumRuleFieldSpec(
            "IGNOREPADTOPADCLEARANCEINFOOTPRINT",
            "ignore_pad_to_pad_clearance_in_footprint",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("OBJECTCLEARANCES", "object_clearances_raw"),
    )
    gap: str = ""
    generic_clearance: str = ""
    ignore_pad_to_pad_clearance_in_footprint: bool | None = None
    object_clearances_raw: str = ""
    object_clearances: dict[str, str] = field(default_factory=dict)
    object_clearance_pairs: list[AltiumClearanceObjectPair] = field(
        default_factory=list
    )

    @classmethod
    def _from_normalized_record(
        cls,
        record: dict[str, str],
        *,
        order: tuple[str, ...],
        index: int,
        record_leader: bytes | None = None,
        record_payload: bytes | None = None,
    ) -> "AltiumPcbRule":
        rule = super()._from_normalized_record(
            record,
            order=order,
            index=index,
            record_leader=record_leader,
            record_payload=record_payload,
        )
        if isinstance(rule, AltiumClearanceRule):
            entries: dict[str, str] = {}
            pairs: list[AltiumClearanceObjectPair] = []
            for part in str(rule.object_clearances_raw or "").split(";"):
                if ":" not in part:
                    continue
                key, value = part.split(":", 1)
                entries[key] = value
                raw_object_a_kind, raw_object_b_kind = "", ""
                if "-" in key:
                    raw_object_a_kind, raw_object_b_kind = key.split("-", 1)
                pairs.append(
                    AltiumClearanceObjectPair(
                        raw_object_a_kind=raw_object_a_kind,
                        raw_object_b_kind=raw_object_b_kind,
                        object_a_kind=_normalize_clearance_object_kind(
                            raw_object_a_kind
                        ),
                        object_b_kind=_normalize_clearance_object_kind(
                            raw_object_b_kind
                        ),
                        clearance=value,
                    )
                )
            rule.object_clearances = entries
            rule.object_clearance_pairs = pairs
        return rule

    def _serialize_specific_fields(self) -> dict[str, str]:
        result = super()._serialize_specific_fields()
        if self.object_clearance_pairs:
            result["OBJECTCLEARANCES"] = ";".join(
                f"{pair.raw_object_a_kind}-{pair.raw_object_b_kind}:{pair.clearance}"
                for pair in self.object_clearance_pairs
                if pair.raw_object_a_kind and pair.raw_object_b_kind
            )
        return result


@_register_rule
@dataclass
class AltiumBoardOutlineClearanceRule(AltiumClearanceRule):
    RULE_KIND_NAME: ClassVar[str] = "BoardOutlineClearance"


@_register_rule
@dataclass
class AltiumPlaneClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "PlaneClearance"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("PowerPlaneClearance",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("CLEARANCE", "clearance"),
    )
    clearance: str = ""


@_register_rule
@dataclass
class AltiumPolygonConnectRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "PolygonConnect"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("PolygonConnectStyle",)
    connect_settings: dict[str, AltiumConnectStyleSettings] = field(
        default_factory=dict
    )

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        consumed: set[str] = set()
        settings: dict[str, AltiumConnectStyleSettings] = {}
        field_map = {
            "CONNECTSTYLE": "connect_style",
            "AIRGAPWIDTH": "air_gap_width",
            "POLYGONRELIEFANGLE": "relief_angle",
            "RELIEFCONDUCTORWIDTH": "relief_conductor_width",
            "RELIEFENTRIES": "relief_entries",
        }
        for raw_key, value in record.items():
            for prefix in ("DEFAULT", "THPAD", "SMDPAD", "VIA"):
                marker = "" if prefix == "DEFAULT" else prefix + "."
                if marker and not raw_key.startswith(marker):
                    continue
                field_key = raw_key[len(marker) :] if marker else raw_key
                attr_name = field_map.get(field_key)
                if not attr_name:
                    continue
                target = settings.setdefault(prefix, AltiumConnectStyleSettings())
                setattr(target, attr_name, value)
                consumed.add(raw_key)
                break
        return {"connect_settings": settings}, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        result: dict[str, str] = {}
        inverse = {
            "connect_style": "CONNECTSTYLE",
            "air_gap_width": "AIRGAPWIDTH",
            "relief_angle": "POLYGONRELIEFANGLE",
            "relief_conductor_width": "RELIEFCONDUCTORWIDTH",
            "relief_entries": "RELIEFENTRIES",
        }
        for prefix, settings in self.connect_settings.items():
            head = "" if prefix == "DEFAULT" else prefix + "."
            for attr_name, raw_key in inverse.items():
                value = getattr(settings, attr_name)
                if value:
                    result[head + raw_key] = value
        return result


@_register_rule
@dataclass
class AltiumPlaneConnectRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "PlaneConnect"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("PowerPlaneConnectStyle",)
    connect_settings: dict[str, AltiumConnectStyleSettings] = field(
        default_factory=dict
    )

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        consumed: set[str] = set()
        settings: dict[str, AltiumConnectStyleSettings] = {}
        field_map = {
            "PLANECONNECTSTYLE": "connect_style",
            "RELIEFAIRGAP": "relief_air_gap",
            "RELIEFCONDUCTORWIDTH": "relief_conductor_width",
            "RELIEFENTRIES": "relief_entries",
            "RELIEFEXPANSION": "relief_expansion",
        }
        for raw_key, value in record.items():
            for prefix in ("DEFAULT", "PAD", "VIA"):
                marker = "" if prefix == "DEFAULT" else prefix + "."
                if marker and not raw_key.startswith(marker):
                    continue
                field_key = raw_key[len(marker) :] if marker else raw_key
                attr_name = field_map.get(field_key)
                if not attr_name:
                    continue
                target = settings.setdefault(prefix, AltiumConnectStyleSettings())
                setattr(target, attr_name, value)
                consumed.add(raw_key)
                break
        return {"connect_settings": settings}, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        result: dict[str, str] = {}
        inverse = {
            "connect_style": "PLANECONNECTSTYLE",
            "relief_air_gap": "RELIEFAIRGAP",
            "relief_conductor_width": "RELIEFCONDUCTORWIDTH",
            "relief_entries": "RELIEFENTRIES",
            "relief_expansion": "RELIEFEXPANSION",
        }
        for prefix, settings in self.connect_settings.items():
            head = "" if prefix == "DEFAULT" else prefix + "."
            for attr_name, raw_key in inverse.items():
                value = getattr(settings, attr_name)
                if value:
                    result[head + raw_key] = value
        return result


@_register_rule
@dataclass
class AltiumWidthRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Width"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("MaxMinWidth",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINLIMIT", "minimum_width"),
        AltiumRuleFieldSpec("PREFEREDWIDTH", "preferred_width"),
        AltiumRuleFieldSpec("MAXLIMIT", "maximum_width"),
        AltiumRuleFieldSpec(
            "IMPEDANCEPROFILEDRIVEN",
            "impedance_profile_driven",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("IMPEDANCEPROFILEID", "impedance_profile_id"),
        AltiumRuleFieldSpec("IMPEDANCEPROFILEVALUE", "impedance_profile_value"),
        AltiumRuleFieldSpec("FAVIMP", "favorite_impedance"),
        AltiumRuleFieldSpec("MINIMP", "minimum_impedance"),
        AltiumRuleFieldSpec("MAXIMP", "maximum_impedance"),
    )
    minimum_width: str = ""
    preferred_width: str = ""
    maximum_width: str = ""
    impedance_profile_driven: bool | None = None
    impedance_profile_id: str = ""
    impedance_profile_value: str = ""
    favorite_impedance: str = ""
    minimum_impedance: str = ""
    maximum_impedance: str = ""
    layer_metrics: dict[str, AltiumLayerMetricRange] = field(default_factory=dict)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        values, consumed = super()._parse_specific_fields(record)
        metrics: dict[str, AltiumLayerMetricRange] = {}
        suffix_map = {
            "_MINWIDTH": "minimum",
            "_PREFWIDTH": "preferred",
            "_MAXWIDTH": "maximum",
        }
        for raw_key, value in record.items():
            for suffix, attr_name in suffix_map.items():
                if not raw_key.endswith(suffix):
                    continue
                prefix = raw_key[: -len(suffix)]
                target = metrics.setdefault(prefix, AltiumLayerMetricRange())
                setattr(target, attr_name, value)
                consumed.add(raw_key)
                break
        values["layer_metrics"] = metrics
        return values, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        result = super()._serialize_specific_fields()
        for prefix, metric in self.layer_metrics.items():
            if metric.minimum:
                result[prefix + "_MINWIDTH"] = metric.minimum
            if metric.preferred:
                result[prefix + "_PREFWIDTH"] = metric.preferred
            if metric.maximum:
                result[prefix + "_MAXWIDTH"] = metric.maximum
        return result


@_register_rule
@dataclass
class AltiumDiffPairsRoutingRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "DiffPairsRouting"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINLIMIT", "minimum_limit"),
        AltiumRuleFieldSpec("MAXLIMIT", "maximum_limit"),
        AltiumRuleFieldSpec("MOSTFREQGAP", "preferred_gap"),
        AltiumRuleFieldSpec("MAXUNCOUPLEDLENGTH", "max_uncoupled_length"),
        AltiumRuleFieldSpec(
            "IMPEDANCEPROFILEDRIVEN",
            "impedance_profile_driven",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("IMPEDANCEPROFILEID", "impedance_profile_id"),
        AltiumRuleFieldSpec("IMPEDANCEPROFILEVALUE", "impedance_profile_value"),
    )
    minimum_limit: str = ""
    maximum_limit: str = ""
    preferred_gap: str = ""
    max_uncoupled_length: str = ""
    impedance_profile_driven: bool | None = None
    impedance_profile_id: str = ""
    impedance_profile_value: str = ""
    layer_metrics: dict[str, AltiumDiffPairLayerMetrics] = field(default_factory=dict)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        values, consumed = super()._parse_specific_fields(record)
        metrics: dict[str, AltiumDiffPairLayerMetrics] = {}
        suffix_map = {
            "_MINWIDTH": "minimum_width",
            "_PREFWIDTH": "preferred_width",
            "_MAXWIDTH": "maximum_width",
            "_MINGAP": "minimum_gap",
            "_PREFGAP": "preferred_gap",
            "_MAXGAP": "maximum_gap",
        }
        for raw_key, value in record.items():
            for suffix, attr_name in suffix_map.items():
                if not raw_key.endswith(suffix):
                    continue
                prefix = raw_key[: -len(suffix)]
                target = metrics.setdefault(prefix, AltiumDiffPairLayerMetrics())
                setattr(target, attr_name, value)
                consumed.add(raw_key)
                break
        values["layer_metrics"] = metrics
        return values, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        result = super()._serialize_specific_fields()
        for prefix, metric in self.layer_metrics.items():
            for suffix, attr_name in {
                "_MINWIDTH": "minimum_width",
                "_PREFWIDTH": "preferred_width",
                "_MAXWIDTH": "maximum_width",
                "_MINGAP": "minimum_gap",
                "_PREFGAP": "preferred_gap",
                "_MAXGAP": "maximum_gap",
            }.items():
                value = getattr(metric, attr_name)
                if value:
                    result[prefix + suffix] = value
        return result


@_register_rule
@dataclass
class AltiumRoutingLayersRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingLayers"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("PermittedLayers", "PermitedLayers")
    allowed_layers: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        allowed: dict[str, bool] = {}
        consumed: set[str] = set()
        for raw_key, value in record.items():
            if not raw_key.endswith("_V5"):
                continue
            allowed[raw_key[:-3]] = bool(_parse_bool(value))
            consumed.add(raw_key)
        return {"allowed_layers": allowed}, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        return {
            f"{layer}_V5": _serialize_bool(enabled)
            for layer, enabled in self.allowed_layers.items()
        }


@_register_rule
@dataclass
class AltiumRoomDefinitionRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoomDefinition"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("CONFINEMENTSTYLE", "confinement_style"),
        AltiumRuleFieldSpec(
            "LOCKCOMPONENTS", "lock_components", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("FORMATCOPY", "format_copy", _parse_bool, _serialize_bool),
    )
    confinement_style: str = ""
    lock_components: bool | None = None
    format_copy: bool | None = None
    outline_vertices: list[AltiumRoomOutlineVertex] = field(default_factory=list)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        values, consumed = super()._parse_specific_fields(record)
        indices: set[int] = set()
        for key in record:
            match = re.match(r"^(?:KIND|VX|VY|CX|CY|SA|EA|R)(\d+)$", key)
            if match:
                indices.add(int(match.group(1)))
        outline = []
        for index in sorted(indices):
            outline.append(
                AltiumRoomOutlineVertex(
                    kind=record.get(f"KIND{index}", ""),
                    vx=record.get(f"VX{index}", ""),
                    vy=record.get(f"VY{index}", ""),
                    cx=record.get(f"CX{index}", ""),
                    cy=record.get(f"CY{index}", ""),
                    start_angle=record.get(f"SA{index}", ""),
                    end_angle=record.get(f"EA{index}", ""),
                    radius=record.get(f"R{index}", ""),
                )
            )
            consumed.update(
                {
                    f"KIND{index}",
                    f"VX{index}",
                    f"VY{index}",
                    f"CX{index}",
                    f"CY{index}",
                    f"SA{index}",
                    f"EA{index}",
                    f"R{index}",
                }
            )
        values["outline_vertices"] = outline
        return values, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        result = super()._serialize_specific_fields()
        for index, vertex in enumerate(self.outline_vertices):
            if vertex.kind:
                result[f"KIND{index}"] = vertex.kind
            if vertex.vx:
                result[f"VX{index}"] = vertex.vx
            if vertex.vy:
                result[f"VY{index}"] = vertex.vy
            if vertex.cx:
                result[f"CX{index}"] = vertex.cx
            if vertex.cy:
                result[f"CY{index}"] = vertex.cy
            if vertex.start_angle:
                result[f"SA{index}"] = vertex.start_angle
            if vertex.end_angle:
                result[f"EA{index}"] = vertex.end_angle
            if vertex.radius:
                result[f"R{index}"] = vertex.radius
        return result


@_register_rule
@dataclass
class AltiumComponentClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ComponentClearance"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec("VERTICALGAP", "vertical_gap"),
        AltiumRuleFieldSpec("COLLISIONCHECKMODE", "collision_check_mode"),
        AltiumRuleFieldSpec(
            "SHOWDISTANCES", "show_distances", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "DONOTCHECKWITHOUT3DBODY",
            "do_not_check_without_3d_body",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "CHECKBYBOUNDARY", "check_by_boundary", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("VERTICALMODE", "vertical_mode"),
    )
    gap: str = ""
    vertical_gap: str = ""
    collision_check_mode: str = ""
    show_distances: bool | None = None
    do_not_check_without_3d_body: bool | None = None
    check_by_boundary: bool | None = None
    vertical_mode: str = ""


@_register_rule
@dataclass
class AltiumFanoutControlRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "FanoutControl"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("FANOUTSTYLE", "fanout_style"),
        AltiumRuleFieldSpec("FANOUTDIRECTION", "fanout_direction"),
        AltiumRuleFieldSpec("BGADIR", "bga_direction"),
        AltiumRuleFieldSpec("BGAVIAMODE", "bga_via_mode"),
        AltiumRuleFieldSpec("VIAGRID", "via_grid"),
    )
    fanout_style: str = ""
    fanout_direction: str = ""
    bga_direction: str = ""
    bga_via_mode: str = ""
    via_grid: str = ""


@_register_rule
@dataclass
class AltiumLengthRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Length"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("MaxMinLength",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINLIMIT", "minimum_length"),
        AltiumRuleFieldSpec("MAXLIMIT", "maximum_length"),
        AltiumRuleFieldSpec("MINDELAY", "minimum_delay"),
        AltiumRuleFieldSpec("MAXDELAY", "maximum_delay"),
        AltiumRuleFieldSpec(
            "USEDELAYUNITS", "use_delay_units", _parse_bool, _serialize_bool
        ),
    )
    minimum_length: str = ""
    maximum_length: str = ""
    minimum_delay: str = ""
    maximum_delay: str = ""
    use_delay_units: bool | None = None


@_register_rule
@dataclass
class AltiumMatchedLengthsRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MatchedLengths"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("MatchednLength",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("TOLERANCE", "tolerance"),
        AltiumRuleFieldSpec("DELAYTOLERANCE", "delay_tolerance"),
        AltiumRuleFieldSpec("TARGETSOURCENAME", "target_source_name"),
        AltiumRuleFieldSpec(
            "USEDELAYUNITS", "use_delay_units", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "CHECKNETSINDIFFPAIR",
            "check_nets_in_diff_pair",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "CHECKDIFFPAIRVSDIFFPAIR",
            "check_diff_pair_vs_diff_pair",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "CHECKOTHERS", "check_others", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "CHECKXSIGNALS", "check_xsignals", _parse_bool, _serialize_bool
        ),
    )
    tolerance: str = ""
    delay_tolerance: str = ""
    target_source_name: str = ""
    use_delay_units: bool | None = None
    check_nets_in_diff_pair: bool | None = None
    check_diff_pair_vs_diff_pair: bool | None = None
    check_others: bool | None = None
    check_xsignals: bool | None = None


@_register_rule
@dataclass
class AltiumHeightRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Height"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("MaxMinHeight",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINHEIGHT", "minimum_height"),
        AltiumRuleFieldSpec("PREFHEIGHT", "preferred_height"),
        AltiumRuleFieldSpec("MAXHEIGHT", "maximum_height"),
    )
    minimum_height: str = ""
    preferred_height: str = ""
    maximum_height: str = ""


@_register_rule
@dataclass
class AltiumMinimumAnnularRingRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MinimumAnnularRing"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINIMUMRING", "minimum_ring"),
    )
    minimum_ring: str = ""


@_register_rule
@dataclass
class AltiumMinimumSolderMaskSliverRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MinimumSolderMaskSliver"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSOLDERMASKWIDTH", "minimum_solder_mask_width"),
    )
    minimum_solder_mask_width: str = ""


@_register_rule
@dataclass
class AltiumNetAntennaeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "NetAntennae"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("NETANTENNAETOLERANCE", "net_antennae_tolerance"),
    )
    net_antennae_tolerance: str = ""


@_register_rule
@dataclass
class AltiumRoutingTopologyRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingTopology"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("TOPOLOGY", "topology"),
    )
    topology: str = ""


@_register_rule
@dataclass
class AltiumRoutingPriorityRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingPriority"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ROUTINGPRIORITY", "routing_priority", _parse_int, _serialize_int
        ),
    )
    routing_priority: int | None = None


@_register_rule
@dataclass
class AltiumRoutingCornersRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingCorners"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("RoutingCornerStyle",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("CORNERSTYLE", "corner_style"),
        AltiumRuleFieldSpec("MINSETBACK", "minimum_setback"),
        AltiumRuleFieldSpec("MAXSETBACK", "maximum_setback"),
    )
    corner_style: str = ""
    minimum_setback: str = ""
    maximum_setback: str = ""


@_register_rule
@dataclass
class AltiumShortCircuitRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ShortCircuit"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("ALLOWED", "allowed", _parse_bool, _serialize_bool),
    )
    allowed: bool | None = None


@_register_rule
@dataclass
class AltiumViasUnderSmdRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ViasUnderSMD"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("ALLOWED", "allowed", _parse_bool, _serialize_bool),
    )
    allowed: bool | None = None


@_register_rule
@dataclass
class AltiumSilkToSilkClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SilkToSilkClearance"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("SILKTOSILKCLEARANCE", "silk_to_silk_clearance"),
    )
    silk_to_silk_clearance: str = ""


@_register_rule
@dataclass
class AltiumSilkToSolderMaskClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SilkToSolderMaskClearance"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSILKSCREENTOMASKGAP", "minimum_silkscreen_to_mask_gap"),
        AltiumRuleFieldSpec(
            "CLEARANCETOEXPOSEDCOPPER",
            "clearance_to_exposed_copper",
            _parse_bool,
            _serialize_bool,
        ),
    )
    minimum_silkscreen_to_mask_gap: str = ""
    clearance_to_exposed_copper: bool | None = None


@_register_rule
@dataclass
class AltiumSupplyNetsRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SupplyNets"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("VOLTAGE", "voltage"),
    )
    voltage: str = ""


@_register_rule
@dataclass
class AltiumSilkscreenOverComponentPadsRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SilkscreenOverComponentPads"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSILKSCREENTOMASKGAP", "minimum_silkscreen_to_mask_gap"),
    )
    minimum_silkscreen_to_mask_gap: str = ""


@_register_rule
@dataclass
class AltiumAssemblyTestpointRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "AssemblyTestpoint"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("AssyTestPointStyle",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec("GRIDTOLERANCE", "grid_tolerance"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("USEGRID", "use_grid", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec(
            "ALLOWSIDETOP", "allow_side_top", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWSIDEBOTTOM", "allow_side_bottom", _parse_bool, _serialize_bool
        ),
    )
    minimum_size: str = ""
    preferred_size: str = ""
    maximum_size: str = ""
    minimum_hole_size: str = ""
    preferred_hole_size: str = ""
    maximum_hole_size: str = ""
    testpoint_grid: str = ""
    grid_tolerance: str = ""
    testpoint_under_component: bool | None = None
    use_grid: bool | None = None
    allow_side_top: bool | None = None
    allow_side_bottom: bool | None = None


@_register_rule
@dataclass
class AltiumFabricationTestpointRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "FabricationTestpoint"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("SIDE", "side"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec("GRIDTOLERANCE", "grid_tolerance"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec("USEGRID", "use_grid", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec(
            "ALLOWSIDETOP", "allow_side_top", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWSIDEBOTTOM", "allow_side_bottom", _parse_bool, _serialize_bool
        ),
    )
    minimum_size: str = ""
    preferred_size: str = ""
    maximum_size: str = ""
    minimum_hole_size: str = ""
    preferred_hole_size: str = ""
    maximum_hole_size: str = ""
    side: str = ""
    testpoint_grid: str = ""
    grid_tolerance: str = ""
    testpoint_under_component: bool | None = None
    use_grid: bool | None = None
    allow_side_top: bool | None = None
    allow_side_bottom: bool | None = None


@_register_rule
@dataclass
class AltiumTestpointRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Testpoint"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("TestPointStyle",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINSIZE", "minimum_size"),
        AltiumRuleFieldSpec("PREFEREDSIZE", "preferred_size"),
        AltiumRuleFieldSpec("MAXSIZE", "maximum_size"),
        AltiumRuleFieldSpec("MINHOLESIZE", "minimum_hole_size"),
        AltiumRuleFieldSpec("PREFEREDHOLESIZE", "preferred_hole_size"),
        AltiumRuleFieldSpec("MAXHOLESIZE", "maximum_hole_size"),
        AltiumRuleFieldSpec("SIDE", "side"),
        AltiumRuleFieldSpec("STYLE", "style"),
        AltiumRuleFieldSpec("ORDER", "order"),
        AltiumRuleFieldSpec("TESTPOINTGRID", "testpoint_grid"),
        AltiumRuleFieldSpec(
            "TESTPOINTUNDERCOMPONENT",
            "testpoint_under_component",
            _parse_bool,
            _serialize_bool,
        ),
    )
    minimum_size: str = ""
    preferred_size: str = ""
    maximum_size: str = ""
    minimum_hole_size: str = ""
    preferred_hole_size: str = ""
    maximum_hole_size: str = ""
    side: str = ""
    style: str = ""
    order: str = ""
    testpoint_grid: str = ""
    testpoint_under_component: bool | None = None


@_register_rule
@dataclass
class AltiumAssemblyTestPointUsageRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "AssemblyTestPointUsage"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("AssyTestPointUsage",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False


@_register_rule
@dataclass
class AltiumFabricationTestPointUsageRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "FabricationTestPointUsage"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ALLOWMULTIPLE", "allow_multiple", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("VALID", "valid", _parse_bool, _serialize_bool),
    )
    allow_multiple: bool | None = None
    valid: bool | None = None


@_register_rule
@dataclass
class AltiumTestPointUsageRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "TestPointUsage"
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ALLOWMULTIPLE", "allow_multiple", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec("VALID", "valid", _parse_bool, _serialize_bool),
    )
    allow_multiple: bool | None = None
    valid: bool | None = None


@_register_rule
@dataclass
class AltiumUnpouredPolygonRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "UnpouredPolygon"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("ModifiedPolygon",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "ALLOWUNPOURED", "allow_unpoured", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWSHELVED", "allow_shelved", _parse_bool, _serialize_bool
        ),
        AltiumRuleFieldSpec(
            "ALLOWMODIFIED", "allow_modified", _parse_bool, _serialize_bool
        ),
    )
    allow_unpoured: bool | None = None
    allow_shelved: bool | None = None
    allow_modified: bool | None = None


@_register_rule
@dataclass
class AltiumUnroutedNetRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "UnRoutedNet"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("BrokenNet",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec(
            "CHKFORINCOMPLETECONNECTIONS",
            "check_for_incomplete_connections",
            _parse_bool,
            _serialize_bool,
        ),
    )
    check_for_incomplete_connections: bool | None = None


@_register_rule
@dataclass
class AltiumSilkToBoardRegionClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SilkToBoardRegionClearance"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("SilkToBoardRegion",)
    USE_SIMPLE_FIELD_PARSE: ClassVar[bool] = False


@_register_rule
@dataclass
class AltiumAcuteAngleRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "AcuteAngle"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINIMUMANGLE", "minimum_angle"),
        AltiumRuleFieldSpec(
            "CHECKTRACKSONLY", "check_tracks_only", _parse_bool, _serialize_bool
        ),
    )
    minimum_angle: str = ""
    check_tracks_only: bool | None = None


@_register_rule
@dataclass
class AltiumParallelSegmentRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ParallelSegment"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("DaisyChainStubLength",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec("LIMIT", "limit"),
        AltiumRuleFieldSpec("CHECKINGTYPE", "checking_type"),
    )
    gap: str = ""
    limit: str = ""
    checking_type: str = ""


@_register_rule
@dataclass
class AltiumMaximumViaCountRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MaximumViaCount"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("VIACOUNT", "via_count", _parse_int, _serialize_int),
    )
    via_count: int | None = None


@_register_rule
@dataclass
class AltiumReturnPathRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ReturnPath"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("GAP", "gap"),
        AltiumRuleFieldSpec("IMPEDANCE", "impedance"),
        AltiumRuleFieldSpec(
            "EXCLUDEPADORVIASVOIDS",
            "exclude_pad_or_vias_voids",
            _parse_bool,
            _serialize_bool,
        ),
    )
    gap: str = ""
    impedance: str = ""
    exclude_pad_or_vias_voids: bool | None = None


@_register_rule
@dataclass
class AltiumCreepageRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Creepage"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("DISTANCE", "distance"),
        AltiumRuleFieldSpec(
            "IGNOREINTERNALLAYERS",
            "ignore_internal_layers",
            _parse_bool,
            _serialize_bool,
        ),
        AltiumRuleFieldSpec(
            "APPLYTOPOLYGONPOUR",
            "apply_to_polygon_pour",
            _parse_bool,
            _serialize_bool,
        ),
    )
    distance: str = ""
    ignore_internal_layers: bool | None = None
    apply_to_polygon_pour: bool | None = None


@_register_rule
@dataclass
class AltiumSmdToCornerRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SMDToCorner"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("DISTANCE", "distance"),
    )
    distance: str = ""


@_register_rule
@dataclass
class AltiumSmdToPlaneRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SMDToPlane"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("DISTANCE", "distance"),
        AltiumRuleFieldSpec("PADSSCOPE", "pads_scope"),
    )
    distance: str = ""
    pads_scope: str = ""


@_register_rule
@dataclass
class AltiumSmdNeckDownRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SMDNeckDown"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("NECKDOWN", "neck_down"),
        AltiumRuleFieldSpec("PADSSCOPE", "pads_scope"),
    )
    neck_down: str = ""
    pads_scope: str = ""


@_register_rule
@dataclass
class AltiumSmdPadEntryRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SMDPADEntry"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("SMDEntry",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("ANYANGEL", "any_angle", _parse_bool, _serialize_bool),
        AltiumRuleFieldSpec("CORNER", "corner"),
        AltiumRuleFieldSpec("SIDE", "side"),
    )
    any_angle: bool | None = None
    corner: str = ""
    side: str = ""


@_register_rule
@dataclass
class AltiumSignalStimulusRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SignalStimulus"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("KIND", "stimulus_kind"),
        AltiumRuleFieldSpec("STARTLEVEL", "start_level"),
        AltiumRuleFieldSpec("STARTTIME", "start_time"),
        AltiumRuleFieldSpec("STOPTIME", "stop_time"),
        AltiumRuleFieldSpec("PERIODTIME", "period_time"),
    )
    stimulus_kind: str = ""
    start_level: str = ""
    start_time: str = ""
    stop_time: str = ""
    period_time: str = ""


@_register_rule
@dataclass
class AltiumOvershootFallingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "OvershootFallingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumOvershootRisingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "OvershootRisingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumUndershootFallingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "UndershootFallingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumUndershootRisingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "UndershootRisingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumSignalTopValueRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SignalTopValue"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINIMUM", "minimum"),
    )
    minimum: str = ""


@_register_rule
@dataclass
class AltiumSignalBaseValueRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "SignalBaseValue"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumFlightTimeRisingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "FlightTimeRisingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumFlightTimeFallingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "FlightTimeFallingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumMaxSlopeRisingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MaxSlopeRisingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumMaxSlopeFallingEdgeRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MaxSlopeFallingEdge"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    maximum: str = ""


@_register_rule
@dataclass
class AltiumMaxMinImpedanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "MaxMinImpedance"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("MINIMUM", "minimum"),
        AltiumRuleFieldSpec("MAXIMUM", "maximum"),
    )
    minimum: str = ""
    maximum: str = ""


@_register_rule
@dataclass
class AltiumUnConnectedPinRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "UnConnectedPin"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("UnconnectedPin",)


@_register_rule
@dataclass
class AltiumNetsToIgnoreRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "NetsToIgnore"


@_register_rule
@dataclass
class AltiumComponentOrientationsRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ComponentOrientations"
    RULE_KIND_ALIASES: ClassVar[tuple[str, ...]] = ("ComponentRotations",)
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("ORIENTATION", "orientation"),
    )
    orientation: str = ""


@_register_rule
@dataclass
class AltiumRoutingNeckDownRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "RoutingNeckDown"
    layer_lengths: dict[str, str] = field(default_factory=dict)

    @classmethod
    def _parse_specific_fields(
        cls, record: dict[str, str]
    ) -> tuple[dict[str, Any], set[str]]:
        lengths: dict[str, str] = {}
        consumed: set[str] = set()
        for raw_key, value in record.items():
            if raw_key.endswith("_LENGTH"):
                lengths[raw_key[:-7]] = value
                consumed.add(raw_key)
        return {"layer_lengths": lengths}, consumed

    def _serialize_specific_fields(self) -> dict[str, str]:
        return {
            f"{layer}_LENGTH": length
            for layer, length in self.layer_lengths.items()
            if length
        }


@_register_rule
@dataclass
class AltiumWirebondRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "Wirebond"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("BONDFINGERSPACE", "bond_finger_space"),
        AltiumRuleFieldSpec("BONDFINGERMARGIN", "bond_finger_margin"),
        AltiumRuleFieldSpec("WIRETOWIRE", "wire_to_wire"),
        AltiumRuleFieldSpec("MINWIRELENGTH", "minimum_wire_length"),
        AltiumRuleFieldSpec("MAXWIRELENGTH", "maximum_wire_length"),
    )
    bond_finger_space: str = ""
    bond_finger_margin: str = ""
    wire_to_wire: str = ""
    minimum_wire_length: str = ""
    maximum_wire_length: str = ""


@_register_rule
@dataclass
class AltiumZAxisClearanceRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ZAxisClearance"
    RULE_FIELDS: ClassVar[tuple[AltiumRuleFieldSpec, ...]] = (
        AltiumRuleFieldSpec("DISTANCE", "distance"),
    )
    distance: str = ""


@_register_rule
@dataclass
class AltiumClearanceMatrixRule(AltiumPcbRule):
    RULE_KIND_NAME: ClassVar[str] = "ClearanceMatrix"
