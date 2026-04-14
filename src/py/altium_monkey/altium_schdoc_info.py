"""
High-level SchDoc wrapper types.

These wrappers provide user-facing access to resolved component, pin, port,
power, harness, and sheet-symbol information without requiring manual
owner-index traversal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .altium_api_markers import public_api
from .altium_common_enums import ComponentKind
from .altium_record_sch__component import AltiumSchComponent
from .altium_record_sch__designator import AltiumSchDesignator
from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
from .altium_record_sch__harness_entry import AltiumSchHarnessEntry
from .altium_record_sch__implementation import (
    AltiumSchImplementation,
    AltiumSchImplementationList,
)
from .altium_record_sch__net_label import AltiumSchNetLabel
from .altium_record_sch__parameter import AltiumSchParameter
from .altium_record_sch__pin import AltiumSchPin
from .altium_record_sch__port import AltiumSchPort
from .altium_record_sch__cross_sheet_connector import AltiumSchCrossSheetConnector
from .altium_record_sch__power_port import AltiumSchPowerPort
from .altium_record_sch__sheet_entry import AltiumSchSheetEntry
from .altium_record_sch__sheet_symbol import AltiumSchSheetSymbol
from .altium_sch_enums import OffSheetConnectorStyle, PinElectrical


def _get_all_children(record: Any) -> list[Any]:
    """
    Get all direct and indirect children of a record.
    """
    children: list[Any] = []

    for attr in ["pins", "parameters", "graphics", "children", "entries"]:
        child_list = getattr(record, attr, None)
        if child_list:
            children.extend(child_list)
            for child in child_list:
                children.extend(_get_all_children(child))

    return children


class _RecordLocationInfoMixin:
    """Shared location accessor for record-backed SchDoc info wrappers."""

    record: Any

    @property
    def location(self) -> tuple[int, int]:
        """Record location as an `(x, y)` tuple."""
        loc = getattr(self.record, "location", None)
        if loc and hasattr(loc, "x") and hasattr(loc, "y"):
            return (loc.x, loc.y)
        return (0, 0)


@public_api
@dataclass
class SchComponentInfo(_RecordLocationInfoMixin):
    """
    High-level component wrapper with resolved children.
    """

    record: AltiumSchComponent

    @property
    def designator(self) -> str:
        """
        Component designator (e.g., 'U1', 'R1').
        """
        for obj in _get_all_children(self.record):
            if isinstance(obj, AltiumSchDesignator):
                return obj.text or ""
        return ""

    @property
    def pins(self) -> list[AltiumSchPin]:
        """
        Pins for this component, filtered for the active part.
        """
        current_part = getattr(self.record, "current_part_id", 1)
        result: list[AltiumSchPin] = []
        for pin in self.record.pins:
            owner_part = getattr(pin, "owner_part_id", None)
            if owner_part is None or owner_part <= 0 or owner_part == current_part:
                result.append(pin)
        return result

    @property
    def parameters(self) -> list[AltiumSchParameter]:
        """
        All parameter records for this component.
        """
        return [p for p in self.record.parameters if isinstance(p, AltiumSchParameter)]

    @property
    def value(self) -> str:
        """
        Component value from the Value parameter.
        """
        for param in self.parameters:
            if param.name == "Value":
                return param.text or ""
        return ""

    @property
    def description(self) -> str:
        """
        Component description.
        """
        desc = self.get_parameter("Description")
        if desc:
            return desc
        if (
            hasattr(self.record, "component_description")
            and self.record.component_description
        ):
            return self.record.component_description
        return getattr(self.record, "design_item_id", "") or ""

    @property
    def comment(self) -> str:
        """
        Component comment from the Comment parameter or design_item_id.
        """
        for param in self.parameters:
            if param.name == "Comment":
                return param.text or ""
        return getattr(self.record, "design_item_id", "") or ""

    @property
    def library_ref(self) -> str:
        """
        Library reference (symbol name).
        """
        return self.record.lib_reference or ""

    @property
    def footprint(self) -> str:
        """
        PCB footprint from the current implementation.
        """
        for child in _get_all_children(self.record):
            if isinstance(child, AltiumSchImplementationList):
                for impl in getattr(child, "children", []):
                    if (
                        isinstance(impl, AltiumSchImplementation)
                        and getattr(impl, "is_current", False)
                        and getattr(impl, "model_type", "").upper() == "PCBLIB"
                    ):
                        return getattr(impl, "model_name", "") or ""
        return ""

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""

    @property
    def component_kind(self) -> ComponentKind:
        """
        Component kind classification.
        """
        return getattr(self.record, "component_kind", ComponentKind.STANDARD)

    def includes_in_netlist(self) -> bool:
        """
        Check if this component should be included in netlist generation.
        """
        from .altium_component_kind import component_kind_includes_in_netlist

        return component_kind_includes_in_netlist(self.component_kind)

    def get_parameter(self, name: str) -> str | None:
        """
        Get a parameter value by name.
        """
        for param in self.parameters:
            if param.name == name:
                return param.text
        return None


@public_api
@dataclass
class SchPinInfo:
    """
    Pin information with component context.
    """

    pin: AltiumSchPin
    component: SchComponentInfo

    @property
    def designator(self) -> str:
        """
        Pin designator (e.g., '1', 'VCC').
        """
        return self.pin.designator or ""

    @property
    def name(self) -> str:
        """
        Pin name.
        """
        return self.pin.name or ""

    @property
    def component_designator(self) -> str:
        """
        Parent component designator.
        """
        return self.component.designator

    @property
    def component_unique_id(self) -> str:
        """
        Parent component unique ID.
        """
        return self.component.unique_id

    @property
    def unique_id(self) -> str:
        """
        Pin unique ID for SVG and IR references.
        """
        return self.pin.unique_id or ""

    @property
    def electrical(self) -> PinElectrical:
        """
        Pin electrical type.
        """
        return self.pin.electrical

    @property
    def location(self) -> tuple[int, int]:
        """
        Pin location on the component side.
        """
        loc = getattr(self.pin, "location", None)
        if loc and hasattr(loc, "x") and hasattr(loc, "y"):
            return (loc.x, loc.y)
        return (0, 0)

    @property
    def connection_point(self) -> tuple[int, int]:
        """
        Pin connection point on the wire-facing side.
        """
        return self.pin.connection_point


@public_api
@dataclass
class SchPortInfo(_RecordLocationInfoMixin):
    """
    Port wrapper with connection points.
    """

    record: AltiumSchPort

    @property
    def name(self) -> str:
        """
        Port name used for cross-sheet connectivity.
        """
        return self.record.name or ""

    @property
    def width(self) -> int:
        """
        Port width.
        """
        return getattr(self.record, "width", 0) or 0

    @property
    def connection_points(self) -> list[tuple[int, int]]:
        """
        Connection points on the left and right edges.
        """
        x, y = self.location
        points = [(x, y)]
        if self.width > 0:
            points.append((x + self.width, y))
        return points

    @property
    def io_type(self) -> int:
        """
        Port I/O type.
        """
        return getattr(self.record, "io_type", 0) or 0

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""


@public_api
@dataclass
class SchPowerPortInfo(_RecordLocationInfoMixin):
    """
    Power port wrapper with connection point.
    """

    record: AltiumSchPowerPort

    @property
    def text(self) -> str:
        """
        Power net name (e.g., 'VCC', 'GND', '3V3').
        """
        return self.record.text or ""

    @property
    def connection_point(self) -> tuple[int, int]:
        """
        Connection point.
        """
        return self.location

    @property
    def style(self) -> int:
        """
        Power port style value.
        """
        return getattr(self.record, "style", 0) or 0

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""


@public_api
@dataclass
class SchCrossSheetConnectorInfo(_RecordLocationInfoMixin):
    """
    Off-sheet connector wrapper with connection point.
    """

    record: AltiumSchCrossSheetConnector

    @property
    def text(self) -> str:
        """
        Connector name.
        """
        return self.record.text or ""

    @property
    def connection_point(self) -> tuple[int, int]:
        """
        Connection point.
        """
        return self.location

    @property
    def style(self) -> OffSheetConnectorStyle | int:
        """
        Connector style enum.
        """
        return getattr(self.record, "style", OffSheetConnectorStyle.LEFT)

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""


@public_api
@dataclass
class SchNetLabelInfo(_RecordLocationInfoMixin):
    """
    Net label wrapper with connection point.
    """

    record: AltiumSchNetLabel

    @property
    def text(self) -> str:
        """
        Net name.
        """
        return self.record.text or ""

    @property
    def connection_point(self) -> tuple[int, int]:
        """
        Connection point.
        """
        return self.location

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""


@public_api
@dataclass
class SchHarnessInfo(_RecordLocationInfoMixin):
    """
    Harness connector wrapper with entries.
    """

    record: AltiumSchHarnessConnector
    entries: list[AltiumSchHarnessEntry] = field(default_factory=list)

    @property
    def name(self) -> str:
        """
        Harness connector name.
        """
        return getattr(self.record, "name", "") or ""

    @property
    def unique_id(self) -> str:
        """
        Unique ID for graphical reference.
        """
        return self.record.unique_id or ""

    def get_entry_names(self) -> list[str]:
        """
        Get names of all entries in this harness.
        """
        return [e.name for e in self.entries if hasattr(e, "name") and e.name]


@public_api
@dataclass
class SchSheetSymbolInfo:
    """
    Sheet symbol wrapper with resolved entries.
    """

    record: AltiumSchSheetSymbol
    entries: list[AltiumSchSheetEntry] = field(default_factory=list)

    @property
    def designator(self) -> str:
        """
        Sheet symbol display name.

        Newer symbols expose this through the owned ``sheet_name`` child
        record. Legacy in-memory fixtures may still carry a direct
        ``designator`` attribute.
        """
        sheet_name = getattr(self.record, "sheet_name", None)
        if sheet_name is not None and hasattr(sheet_name, "text"):
            return getattr(sheet_name, "text", "") or ""
        return getattr(self.record, "designator", "") or ""

    @property
    def file_name(self) -> str:
        """
        Referenced schematic file name.
        """
        file_name = getattr(self.record, "file_name", None)
        if file_name is None:
            return ""
        if hasattr(file_name, "text"):
            return getattr(file_name, "text", "") or ""
        return str(file_name or "")

    @property
    def sheet_name_text(self) -> str:
        """
        Explicit access to the owned sheet-name text.
        """
        return self.designator

    @property
    def child_filename(self) -> str:
        """
        Explicit access to the owned child schematic filename.
        """
        return self.file_name

    @property
    def unique_id(self) -> str:
        """
        Unique ID.
        """
        return self.record.unique_id or ""
