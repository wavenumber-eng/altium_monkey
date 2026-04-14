"""Shared helper functions for the top-level netlist compilers."""

from __future__ import annotations

import re
from typing import Protocol, TypeAlias

from .altium_sch_enums import PinElectrical
from .altium_netlist_model import Net, PinType, UnionFind
from .altium_prjpcb import NetIdentifierScope


def _natural_sort_key(s: str) -> list:
    """Natural-sort key (`C9` < `C10`)."""

    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", s)
    ]


def _altium_net_sort_key(s: str) -> list:
    """Sort key matching Altium's wire-list net ordering."""

    result = []
    i = 0
    s_lower = s.lower()

    while i < len(s_lower):
        char = s_lower[i]
        if char.isdigit():
            num_start = i
            while i < len(s_lower) and s_lower[i].isdigit():
                i += 1
            result.append(("A", int(s_lower[num_start:i])))
        elif char.isalpha():
            result.append(("C", char))
            i += 1
        else:
            result.append(("B", char))
            i += 1

    return result


CHAR_REPLACEMENTS = {
    "Ω": "O",
}


POWER_PIN_NAMES = frozenset({
    "GND", "VCC", "VDD", "VSS", "VEE",
    "AVDD", "AVSS", "DVDD", "DVSS",
    "AGND", "DGND", "PGND", "VSS_PA",
})


CHASSIS_GND_MAPPINGS = frozenset({
    "CHASSI", "CHASSIS", "SHIELD", "EARTH", "GND_CHASSIS",
})


RootPoint: TypeAlias = tuple[int, int]


class _ParameterLike(Protocol):
    name: str
    text: str


class _DisplayValueComponent(Protocol):
    comment: str
    value: str
    parameters: list[_ParameterLike]

    def get_parameter(self, name: str) -> str | None:
        ...


class _NetPinLike(Protocol):
    component_designator: str
    designator: str


PinGroup: TypeAlias = list[_NetPinLike]
PinGroupsByRoot: TypeAlias = dict[RootPoint, PinGroup]
RootsByName: TypeAlias = dict[str, list[RootPoint]]


class _CreateNetFn(Protocol):
    def __call__(
        self,
        name: str,
        pins: PinGroup,
        root: RootPoint,
        is_auto_named: bool = False,
    ) -> Net:
        ...


def _normalize_text(text: str, strict: bool = True) -> str:
    """Normalize text for wire-list output."""

    if not strict:
        return text
    for char, replacement in CHAR_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text


def _evaluate_altium_expression(expr: str, params: dict[str, str]) -> str:
    """Evaluate a simple Altium parameter expression."""

    result_parts = []
    i = 0
    expr_len = len(expr)

    while i < expr_len:
        ch = expr[i]
        if ch in " \t":
            i += 1
            continue
        if ch == "+":
            i += 1
            continue
        if ch == "'":
            end = expr.find("'", i + 1)
            if end == -1:
                result_parts.append(expr[i + 1:])
                break
            result_parts.append(expr[i + 1:end])
            i = end + 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < expr_len and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            ident = expr[i:j]
            ident_lower = ident.lower()
            found = False
            for key, value in params.items():
                if key.lower() == ident_lower:
                    result_parts.append(value)
                    found = True
                    break
            if not found:
                result_parts.append("")
            i = j
            continue
        i += 1

    return "".join(result_parts)


def _resolve_component_display_value(
    comp: _DisplayValueComponent,
    project_params: dict[str, str] | None = None,
    sheet_params: dict[str, str] | None = None,
) -> str:
    """Resolve component display value for wire-list output."""

    comment = comp.comment
    if not comment:
        return ""
    if not comment.startswith("="):
        return comment

    expr = comment[1:]
    if "+" not in expr and "'" not in expr:
        param_name = expr
        if param_name.lower() == "value":
            return comp.value
        param_value = comp.get_parameter(param_name)
        if param_value is not None:
            return param_value
        if sheet_params:
            for key, value in sheet_params.items():
                if key.lower() == param_name.lower():
                    return value
        if project_params:
            for key, value in project_params.items():
                if key.lower() == param_name.lower():
                    return value
        return ""

    merged_params = {}
    if project_params:
        merged_params.update(project_params)
    if sheet_params:
        merged_params.update(sheet_params)
    merged_params["Value"] = comp.value
    for param in getattr(comp, "parameters", []):
        if hasattr(param, "name") and hasattr(param, "text"):
            merged_params[param.name] = param.text
    return _evaluate_altium_expression(expr, merged_params)


def _points_connected(
    p1: tuple[int, int],
    p2: tuple[int, int],
    tolerance: int = 0,
) -> bool:
    """Return True when two points are electrically connected."""

    if tolerance == 0:
        return p1[0] == p2[0] and p1[1] == p2[1]
    return abs(p1[0] - p2[0]) <= tolerance and abs(p1[1] - p2[1]) <= tolerance


_PIN_ELECTRICAL_TO_PIN_TYPE: dict[PinElectrical, PinType] = {
    PinElectrical.INPUT: PinType.INPUT,
    PinElectrical.IO: PinType.IO,
    PinElectrical.OUTPUT: PinType.OUTPUT,
    PinElectrical.OPEN_COLLECTOR: PinType.OPEN_COLLECTOR,
    PinElectrical.PASSIVE: PinType.PASSIVE,
    PinElectrical.HIZ: PinType.TRISTATE,
    PinElectrical.OPEN_EMITTER: PinType.OPEN_EMITTER,
    PinElectrical.POWER: PinType.POWER,
}


def _pin_electrical_to_pintype(electrical: PinElectrical | int | None) -> PinType:
    """Convert an Altium pin electrical kind to `PinType`."""

    if electrical is None:
        return PinType.PASSIVE

    if isinstance(electrical, int):
        try:
            electrical = PinElectrical(electrical)
        except ValueError:
            return PinType.PASSIVE

    return _PIN_ELECTRICAL_TO_PIN_TYPE.get(electrical, PinType.PASSIVE)


def _emit_port_named_nets(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    port_names_ordered: list[str],
    final_name_to_root: dict[str, RootPoint],
    final_pin_groups: PinGroupsByRoot,
) -> None:
    """Emit nets named by ports."""

    port_names_sorted = sorted(
        set(port_names_ordered),
        key=_altium_net_sort_key,
        reverse=True,
    )
    for name in port_names_sorted:
        if name in final_name_to_root:
            root = final_name_to_root[name]
            if root not in processed_roots and root in final_pin_groups:
                pins = final_pin_groups[root]
                if pins:
                    nets.append(create_net(name, pins, root))
                    processed_roots.add(root)


def _emit_named_roots(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    names_sorted: list[str],
    final_name_to_root: dict[str, RootPoint],
    final_pin_groups: PinGroupsByRoot,
    *,
    allow_empty_pins: bool = False,
) -> None:
    """Emit nets for a sorted list of explicit names."""

    for name in names_sorted:
        if name in final_name_to_root:
            root = final_name_to_root[name]
            if root not in processed_roots:
                if root in final_pin_groups and final_pin_groups[root]:
                    nets.append(create_net(name, final_pin_groups[root], root))
                elif allow_empty_pins:
                    nets.append(create_net(name, [], root))
                processed_roots.add(root)


def _find_root_name_in_map(
    uf: UnionFind[RootPoint],
    root: RootPoint,
    roots_map: RootsByName,
) -> str | None:
    """Find a net name in a roots map via union-find resolution."""

    for name, roots in roots_map.items():
        if any(uf.find(candidate) == root for candidate in roots):
            return name
    return None


def _emit_bridge_roots(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    uf: UnionFind[RootPoint],
    scope: NetIdentifierScope,
    final_net_names: dict[RootPoint, str],
    final_pin_groups: PinGroupsByRoot,
    final_port_ids: dict[RootPoint, list[str]],
    final_se_ids: dict[RootPoint, list[str]],
    port_roots: RootsByName,
    se_roots: RootsByName,
) -> None:
    """Emit hierarchy bridge roots that still need a named placeholder net."""

    if scope not in (
        NetIdentifierScope.HIERARCHICAL,
        NetIdentifierScope.STRICT_HIERARCHICAL,
    ):
        return

    bridge_roots: set[RootPoint] = set()
    for root in final_port_ids:
        if root not in processed_roots and root not in final_pin_groups:
            bridge_roots.add(root)
    for root in final_se_ids:
        if root not in processed_roots and root not in final_pin_groups:
            bridge_roots.add(root)

    for root in bridge_roots:
        name = final_net_names.get(root)
        if not name:
            name = _find_root_name_in_map(uf, root, port_roots)
        if not name:
            name = _find_root_name_in_map(uf, root, se_roots)
        if name:
            nets.append(create_net(name, [], root))
            processed_roots.add(root)


def _emit_auto_named_nets(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    uf: UnionFind[RootPoint],
    final_pin_groups: PinGroupsByRoot,
    floating_pin_roots: set[RootPoint],
) -> None:
    """Emit auto-named nets for the remaining pin groups."""

    auto_nets = []
    final_floating_roots = {uf.find(root) for root in floating_pin_roots}

    for root, pins in final_pin_groups.items():
        if root not in processed_roots and pins:
            if len(pins) == 1 and root in final_floating_roots:
                continue
            sorted_pins = sorted(
                pins,
                key=lambda pin: (
                    _natural_sort_key(pin.component_designator),
                    _natural_sort_key(pin.designator),
                ),
            )
            first_pin = sorted_pins[0]
            name = f"Net{first_pin.component_designator}_{first_pin.designator}"
            auto_nets.append((name, pins, root))

    auto_nets.sort(key=lambda item: _altium_net_sort_key(item[0]), reverse=True)
    for name, pins, root in auto_nets:
        nets.append(create_net(name, pins, root, is_auto_named=True))
        processed_roots.add(root)


__all__ = [
    "CHASSIS_GND_MAPPINGS",
    "POWER_PIN_NAMES",
    "_altium_net_sort_key",
    "_emit_auto_named_nets",
    "_emit_bridge_roots",
    "_emit_named_roots",
    "_emit_port_named_nets",
    "_normalize_text",
    "_pin_electrical_to_pintype",
    "_points_connected",
    "_resolve_component_display_value",
]
