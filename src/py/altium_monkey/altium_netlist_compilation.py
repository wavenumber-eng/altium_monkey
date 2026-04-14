"""
Top-level netlist compilation entrypoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .altium_netlist_options import NetlistOptions
    from .altium_netlist_model import Netlist
    from .altium_prjpcb import AltiumPrjPcb
    from .altium_schdoc import AltiumSchDoc


def compile_netlist(
    schdocs: list["AltiumSchDoc"],
    project: "AltiumPrjPcb | None" = None,
    options: "NetlistOptions | None" = None,
) -> "Netlist":
    """
    Compile a design netlist through the primary entrypoint.
    
        Single-sheet and multi-sheet designs compile through the top-level
        netlist compilers.
    """

    from .altium_netlist_options import NetlistOptions
    from .altium_netlist_multi_sheet import AltiumNetlistMultiSheetCompiler
    from .altium_netlist_single_sheet import AltiumNetlistSingleSheetCompiler

    if not schdocs:
        raise ValueError("compile_netlist() requires at least one schematic document")

    effective_options = options or NetlistOptions()
    if len(schdocs) == 1:
        return AltiumNetlistSingleSheetCompiler(
            schdocs[0],
            options=effective_options,
        ).generate()

    return AltiumNetlistMultiSheetCompiler(
        schdocs,
        project,
        effective_options,
    ).build()


__all__ = ["compile_netlist"]
