"""Shared netlist option model for the Python netlist compilers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .altium_prjpcb import NetIdentifierScope

if TYPE_CHECKING:
    from .altium_prjpcb import AltiumPrjPcb


@dataclass
class NetlistOptions:
    """Options that affect netlist generation behavior."""

    net_identifier_scope: NetIdentifierScope = NetIdentifierScope.GLOBAL
    allow_ports_to_name_nets: bool = False
    allow_sheet_entries_to_name_nets: bool = True
    allow_single_pin_nets: bool = False
    append_sheet_numbers_to_local_nets: bool = False
    power_port_names_take_priority: bool = False
    higher_level_names_take_priority: bool = False
    auto_sheet_numbering: bool = False
    channel_designator_format: str = ""

    # Parameter dictionaries for expression evaluation
    project_parameters: dict[str, str] = field(default_factory=dict)
    sheet_parameters: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_prjpcb(cls, prjpcb: AltiumPrjPcb) -> NetlistOptions:
        """Create options from an `AltiumPrjPcb` instance."""

        opts = prjpcb.netlist_options
        return cls(
            net_identifier_scope=opts.get(
                "net_identifier_scope",
                NetIdentifierScope.AUTOMATIC,
            ),
            allow_ports_to_name_nets=opts.get("allow_ports_to_name_nets", False),
            allow_sheet_entries_to_name_nets=opts.get(
                "allow_sheet_entries_to_name_nets",
                True,
            ),
            allow_single_pin_nets=opts.get("allow_single_pin_nets", False),
            append_sheet_numbers_to_local_nets=opts.get(
                "append_sheet_numbers_to_local_nets",
                False,
            ),
            power_port_names_take_priority=opts.get(
                "power_port_names_take_priority",
                False,
            ),
            higher_level_names_take_priority=opts.get(
                "name_nets_hierarchically",
                False,
            ),
            auto_sheet_numbering=opts.get("auto_sheet_numbering", False),
            channel_designator_format=opts.get("channel_designator_format", ""),
            project_parameters=dict(prjpcb.parameters),
        )


__all__ = ["NetlistOptions"]
