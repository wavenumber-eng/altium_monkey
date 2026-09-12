"""Shared helper functions for the top-level netlist compilers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import cmp_to_key
from typing import Protocol, TypeAlias

from .altium_sch_enums import PinElectrical
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_netlist_model import Net, PinType, UnionFind
from .altium_managed_alpha_numeric import managed_alpha_numeric_compare
from .altium_prjpcb import NetIdentifierScope


def _natural_sort_key(s: str) -> list:
    """Natural-sort key (`C9` < `C10`)."""

    return [
        int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", s)
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


def _altium_net_total_sort_key(s: str) -> tuple[list, str]:
    """Deterministic net sort key with raw-name tie-break for case variants."""

    return (_altium_net_sort_key(s), s)


def _component_part_alpha_suffix(*, part_count: int, current_part_id: int) -> str:
    if part_count <= 1 or current_part_id <= 0:
        return ""
    index = current_part_id
    letters = ""
    while index > 0:
        index -= 1
        letters = chr(ord("A") + (index % 26)) + letters
        index //= 26
    return letters


def _sheet_entry_display_name(entry: object) -> str:
    return str(getattr(entry, "display_name", "") or getattr(entry, "name", "") or "")


def _unique_nonempty_strings(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


CHAR_REPLACEMENTS = {
    "Ω": "O",
}


POWER_PIN_NAMES = frozenset(
    {
        "GND",
        "VCC",
        "VDD",
        "VSS",
        "VEE",
        "AVDD",
        "AVSS",
        "DVDD",
        "DVSS",
        "AGND",
        "DGND",
        "PGND",
        "VSS_PA",
    }
)


CHASSIS_GND_MAPPINGS = frozenset(
    {
        "CHASSI",
        "CHASSIS",
        "SHIELD",
        "EARTH",
        "GND_CHASSIS",
    }
)


RootPoint: TypeAlias = tuple[int, int, int, int]


class _ParameterLike(Protocol):
    @property
    def name(self) -> str:
        raise NotImplementedError("parameter name")

    @property
    def text(self) -> str:
        raise NotImplementedError("parameter text")


class _DisplayValueComponent(Protocol):
    @property
    def comment(self) -> str:
        raise NotImplementedError("component comment")

    @property
    def value(self) -> str:
        raise NotImplementedError("component value")

    @property
    def parameters(self) -> Sequence[_ParameterLike]:
        raise NotImplementedError("component parameters")

    def get_parameter(self, name: str) -> str | None: ...


class _NetPinLike(Protocol):
    @property
    def component_designator(self) -> str:
        raise NotImplementedError("pin component designator")

    @property
    def designator(self) -> str:
        raise NotImplementedError("pin designator")

    @property
    def name(self) -> str:
        raise NotImplementedError("pin name")

    @property
    def electrical(self) -> PinElectrical:
        raise NotImplementedError("pin electrical")

    @property
    def unique_id(self) -> str:
        raise NotImplementedError("pin unique id")

    @property
    def connection_point(self) -> tuple[int, int]:
        raise NotImplementedError("pin connection point")


PinGroup: TypeAlias = Sequence[_NetPinLike]
PinGroupsByRoot: TypeAlias = dict[RootPoint, PinGroup]
RootsByName: TypeAlias = dict[str, list[RootPoint]]


class _CreateNetFn(Protocol):
    def __call__(
        self,
        name: str,
        pins: PinGroup,
        root: RootPoint,
        is_auto_named: bool = False,
    ) -> Net: ...


def _normalize_text(text: str, strict: bool = True) -> str:
    """Normalize text for wire-list output."""

    if not strict:
        return text
    for char, replacement in CHAR_REPLACEMENTS.items():
        text = text.replace(char, replacement)
    return text


def _evaluate_altium_expression(
    expr: str,
    params: dict[str, str],
    *,
    preserve_unresolved_formula: bool = False,
) -> str:
    """Evaluate a simple Altium parameter expression."""

    result_parts = []
    has_unresolved_identifier = False
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
                result_parts.append(expr[i + 1 :])
                break
            result_parts.append(expr[i + 1 : end])
            i = end + 1
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < expr_len and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            ident = expr[i:j]
            ident_key = dotnet_ordinal_ignore_case_key(ident)
            found = False
            for key, value in params.items():
                if dotnet_ordinal_ignore_case_key(key) == ident_key:
                    result_parts.append(value)
                    found = True
                    break
            if not found:
                has_unresolved_identifier = True
            i = j
            continue
        i += 1

    if preserve_unresolved_formula and has_unresolved_identifier:
        return f"={expr}"
    return "".join(result_parts)


def _resolve_component_display_value(
    comp: _DisplayValueComponent,
    project_params: dict[str, str] | None = None,
    sheet_params: dict[str, str] | None = None,
    *,
    component_description: str = "",
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
        param_key = dotnet_ordinal_ignore_case_key(param_name)
        if param_key == dotnet_ordinal_ignore_case_key("value"):
            return comp.value
        param_value = comp.get_parameter(param_name)
        if param_value is not None:
            return param_value
        if (
            param_key == dotnet_ordinal_ignore_case_key("description")
            and component_description
        ):
            return component_description
        if sheet_params:
            for key, value in sheet_params.items():
                if dotnet_ordinal_ignore_case_key(key) == param_key:
                    return value
        if project_params:
            for key, value in project_params.items():
                if dotnet_ordinal_ignore_case_key(key) == param_key:
                    return value
        return comment

    merged_params = {}
    if project_params:
        merged_params.update(project_params)
    if sheet_params:
        merged_params.update(sheet_params)
    merged_params["Value"] = comp.value
    if component_description:
        merged_params["Description"] = component_description
    for param in getattr(comp, "parameters", []):
        if hasattr(param, "name") and hasattr(param, "text"):
            merged_params[param.name] = param.text
    return _evaluate_altium_expression(
        expr,
        merged_params,
        preserve_unresolved_formula=True,
    )


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
    port_net_names: dict[RootPoint, str],
    final_pin_groups: PinGroupsByRoot,
) -> None:
    """Emit nets named by ports."""

    port_rows = sorted(
        port_net_names.items(),
        key=lambda row: _altium_net_total_sort_key(row[1]),
        reverse=True,
    )
    for root, name in port_rows:
        if root not in processed_roots:
            nets.append(create_net(name, final_pin_groups.get(root, []), root))
            processed_roots.add(root)


def _emit_named_roots(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    selected_names: dict[RootPoint, str],
    final_pin_groups: PinGroupsByRoot,
    *,
    allow_empty_pins: bool = False,
) -> None:
    """Emit nets for a sorted list of explicit names."""

    rows = sorted(
        selected_names.items(),
        key=lambda row: _altium_net_total_sort_key(row[1]),
        reverse=True,
    )
    for root, name in rows:
        if root in processed_roots:
            continue
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
    exact_name_by_root: Mapping[RootPoint, str] | None = None,
    eligible_interface_ids: frozenset[str] | None = None,
) -> None:
    """Emit hierarchy bridge roots that still need a named placeholder net."""

    if scope not in (
        NetIdentifierScope.HIERARCHICAL,
        NetIdentifierScope.STRICT_HIERARCHICAL,
    ):
        return

    bridge_roots = _unprocessed_bridge_roots(
        processed_roots,
        final_pin_groups,
        final_port_ids,
        final_se_ids,
    )
    for root in sorted(bridge_roots):
        if not _bridge_root_is_eligible(
            root,
            final_port_ids,
            final_se_ids,
            eligible_interface_ids,
        ):
            continue
        name = _bridge_root_name(
            root,
            uf,
            final_net_names,
            port_roots,
            se_roots,
            exact_name_by_root,
        )
        if name:
            nets.append(create_net(name, [], root))
            processed_roots.add(root)


def _unprocessed_bridge_roots(
    processed_roots: set[RootPoint],
    final_pin_groups: PinGroupsByRoot,
    final_port_ids: Mapping[RootPoint, Sequence[str]],
    final_se_ids: Mapping[RootPoint, Sequence[str]],
) -> set[RootPoint]:
    return {
        root
        for root in {*final_port_ids, *final_se_ids}
        if root not in processed_roots and root not in final_pin_groups
    }


def _bridge_root_is_eligible(
    root: RootPoint,
    final_port_ids: Mapping[RootPoint, Sequence[str]],
    final_se_ids: Mapping[RootPoint, Sequence[str]],
    eligible_interface_ids: frozenset[str] | None,
) -> bool:
    if eligible_interface_ids is None:
        return True
    return any(
        dotnet_ordinal_ignore_case_key(identity) in eligible_interface_ids
        for identity in (*final_port_ids.get(root, ()), *final_se_ids.get(root, ()))
    )


def _bridge_root_name(
    root: RootPoint,
    uf: UnionFind[RootPoint],
    final_net_names: Mapping[RootPoint, str],
    port_roots: RootsByName,
    se_roots: RootsByName,
    exact_name_by_root: Mapping[RootPoint, str] | None,
) -> str | None:
    exact_name = exact_name_by_root.get(root) if exact_name_by_root else None
    return (
        final_net_names.get(root)
        or exact_name
        or _find_root_name_in_map(uf, root, port_roots)
        or _find_root_name_in_map(uf, root, se_roots)
    )


def _emit_auto_named_nets(
    nets: list[Net],
    processed_roots: set[RootPoint],
    create_net: _CreateNetFn,
    uf: UnionFind[RootPoint],
    final_pin_groups: PinGroupsByRoot,
    floating_pin_roots: set[RootPoint],
    *,
    include_single_pin_nets: bool,
) -> None:
    """Emit auto-named nets for the remaining pin groups."""

    auto_nets = []
    final_floating_roots = {uf.find(root) for root in floating_pin_roots}

    for root, pins in final_pin_groups.items():
        if root not in processed_roots and pins:
            if (
                not include_single_pin_nets
                and len(pins) == 1
                and root in final_floating_roots
            ):
                continue

            def compare_pins(left: _NetPinLike, right: _NetPinLike) -> int:
                component_order = managed_alpha_numeric_compare(
                    left.component_designator,
                    right.component_designator,
                )
                if component_order:
                    return component_order
                return managed_alpha_numeric_compare(left.designator, right.designator)

            sorted_pins = sorted(pins, key=cmp_to_key(compare_pins))
            first_pin = sorted_pins[0]
            name = f"Net{first_pin.component_designator}_{first_pin.designator}"
            auto_nets.append(
                (
                    name,
                    pins,
                    root,
                    len(pins) == 1 and root in final_floating_roots,
                )
            )

    auto_nets.sort(key=lambda item: _altium_net_total_sort_key(item[0]), reverse=True)
    for name, pins, root, single_pin_retention_only in auto_nets:
        net = create_net(name, pins, root, is_auto_named=True)
        net._single_pin_retention_only = single_pin_retention_only
        nets.append(net)
        processed_roots.add(root)


__all__ = [
    "CHASSIS_GND_MAPPINGS",
    "POWER_PIN_NAMES",
    "_altium_net_sort_key",
    "_altium_net_total_sort_key",
    "_emit_auto_named_nets",
    "_emit_bridge_roots",
    "_emit_named_roots",
    "_emit_port_named_nets",
    "_normalize_text",
    "_pin_electrical_to_pintype",
    "_points_connected",
    "_resolve_component_display_value",
]
