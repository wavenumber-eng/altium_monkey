"""Component-kind parsing and policy helpers."""

from __future__ import annotations

from .altium_common_enums import ComponentKind


def parse_component_kind(record: dict[str, str]) -> ComponentKind:
    """Parse component kind from the versioned record fields."""
    v1 = int(record.get("COMPONENTKIND", record.get("ComponentKind", 0)))
    v2 = int(record.get("COMPONENTKINDVERSION2", record.get("ComponentKindVersion2", 0)))
    v3 = int(record.get("COMPONENTKINDVERSION3", record.get("ComponentKindVersion3", 0)))

    if v3 == 6:
        return ComponentKind(v3)
    if v2 >= 5:
        return ComponentKind(v2)
    return ComponentKind(v1)


def component_kind_includes_in_netlist(kind: ComponentKind) -> bool:
    """Return whether this component kind participates in netlist generation."""
    return kind != ComponentKind.GRAPHICAL


def component_kind_includes_in_bom(kind: ComponentKind) -> bool:
    """Return whether this component kind participates in BOM generation."""
    return kind not in (
        ComponentKind.GRAPHICAL,
        ComponentKind.NET_TIE_NO_BOM,
        ComponentKind.STANDARD_NO_BOM,
    )
