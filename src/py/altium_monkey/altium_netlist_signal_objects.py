"""Signal/object identity types for the Altium netlist pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .altium_netlist_model import HierarchyPath


class AltiumObjectType(Enum):
    """Traceable schematic object kinds used by the signal engine."""

    WIRE = "wire"
    BUS = "bus"
    SIGNAL_HARNESS = "signal_harness"
    BUS_ENTRY = "bus_entry"
    NET_LABEL = "net_label"
    CROSS_SHEET_CONNECTOR = "cross_sheet_connector"
    PORT = "port"
    POWER_OBJECT = "power_object"
    SHEET_ENTRY = "sheet_entry"
    PIN = "pin"
    HARNESS_ENTRY = "harness_entry"
    HARNESS_CONNECTOR = "harness_connector"
    DIRECTIVE = "directive"


@dataclass(frozen=True, slots=True)
class AltiumObjectInfo:
    """Composite object identity used by visited-state and signal tracing."""

    object_type: AltiumObjectType
    object_id: str
    schematic_id: str
    hierarchy_path: HierarchyPath = field(default_factory=HierarchyPath)
    bus_signal_index: int | None = None
    harness_entries_path: tuple[str, ...] | None = None
    repeat_value: int | None = None

    def __post_init__(self) -> None:
        if self.harness_entries_path is not None and not isinstance(
            self.harness_entries_path,
            tuple,
        ):
            object.__setattr__(
                self,
                "harness_entries_path",
                tuple(self.harness_entries_path),
            )

    @property
    def identity_key(self) -> tuple[
        AltiumObjectType,
        str,
        str,
        HierarchyPath,
        int | None,
        tuple[str, ...] | None,
        int | None,
    ]:
        """Stable identity tuple used by visited-state and signal tracing."""

        return (
            self.object_type,
            self.object_id,
            self.schematic_id,
            self.hierarchy_path,
            self.bus_signal_index,
            self.harness_entries_path,
            self.repeat_value,
        )

    @property
    def is_object_from_bus_signal(self) -> bool:
        """True when the object belongs to a bus-signal tracing context."""

        return self.bus_signal_index == -1


@dataclass(frozen=True, slots=True)
class AltiumOutLinkInfo(AltiumObjectInfo):
    """Trace edge discovered while walking the signal graph."""

    name: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class AltiumPortInfo(AltiumOutLinkInfo):
    """Out-link wrapper for port traversal."""

    object_type: AltiumObjectType = field(
        default=AltiumObjectType.PORT,
        init=False,
    )
    link_sheet_entry_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class AltiumSheetEntryInfo(AltiumOutLinkInfo):
    """Out-link wrapper for sheet-entry traversal."""

    object_type: AltiumObjectType = field(
        default=AltiumObjectType.SHEET_ENTRY,
        init=False,
    )
    link_port_id: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class AltiumPowerObjectInfo(AltiumOutLinkInfo):
    """Out-link wrapper for power-object traversal."""

    object_type: AltiumObjectType = field(
        default=AltiumObjectType.POWER_OBJECT,
        init=False,
    )
