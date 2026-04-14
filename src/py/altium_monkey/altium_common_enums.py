"""Shared enum definitions used across schematic and PCB workflows."""

from enum import IntEnum


class ComponentKind(IntEnum):
    """Component kind classification used by BOM and netlist helpers."""

    STANDARD = 0
    MECHANICAL = 1
    GRAPHICAL = 2
    NET_TIE_BOM = 3
    NET_TIE_NO_BOM = 4
    STANDARD_NO_BOM = 5
    JUMPER = 6


COMPONENT_KIND_NAMES = {
    ComponentKind.STANDARD: "Standard",
    ComponentKind.MECHANICAL: "Mechanical",
    ComponentKind.GRAPHICAL: "Graphical",
    ComponentKind.NET_TIE_BOM: "Net Tie (BOM)",
    ComponentKind.NET_TIE_NO_BOM: "Net Tie (No BOM)",
    ComponentKind.STANDARD_NO_BOM: "Standard (No BOM)",
    ComponentKind.JUMPER: "Jumper",
}
