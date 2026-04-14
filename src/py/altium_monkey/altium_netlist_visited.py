"""Visited-object tracking for the Altium netlist pipeline."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterable

from .altium_netlist_signal_objects import (
    AltiumObjectInfo,
    AltiumPortInfo,
    AltiumSheetEntryInfo,
)


class AltiumVisitedObjectsManager:
    """Track visited trace objects using composite object identity."""

    def __init__(self) -> None:
        self._global_objects: dict[tuple, AltiumObjectInfo] = {}
        self._current_order: dict[tuple, None] = {}

    def clear(self) -> None:
        """Reset the current traversal without dropping canonical objects."""

        if len(self._current_order) > 16:
            self._current_order = {}
        else:
            self._current_order.clear()

    def visit(self, obj: AltiumObjectInfo) -> bool:
        """Visit an object for the current traversal."""

        key = obj.identity_key
        canonical = self._global_objects.get(key)
        if canonical is None:
            canonical = obj
        else:
            canonical = self._prefer_object(canonical, obj)
        self._global_objects[key] = canonical
        if key in self._current_order:
            return False
        self._current_order[key] = None
        return True

    def visit_core(self, **kwargs: object) -> bool:
        """Construct and visit a generic `AltiumObjectInfo` in one step."""

        return self.visit(AltiumObjectInfo(**kwargs))

    def merge(self, other: "AltiumVisitedObjectsManager") -> None:
        """Merge another manager's current traversal objects into this one."""

        for obj in other.get_visited_objects():
            self.visit(obj)

    def update_if_needed(self, obj: AltiumObjectInfo) -> None:
        """Upgrade a canonical object if a richer equivalent arrives later."""

        key = obj.identity_key
        canonical = self._global_objects.get(key)
        if canonical is None:
            return
        self._global_objects[key] = self._prefer_object(canonical, obj)

    def any(self, predicate: Callable[[AltiumObjectInfo], bool]) -> bool:
        """Return True if any current visited object matches `predicate`."""

        return any(predicate(obj) for obj in self.get_visited_objects())

    def contains(self, obj: AltiumObjectInfo) -> bool:
        """Return True if the current traversal has visited `obj`."""

        return obj.identity_key in self._current_order

    def first_visited_object_or_default(self) -> AltiumObjectInfo | None:
        """Return the first visited object for the current traversal."""

        for key in self._current_order:
            return self._global_objects[key]
        return None

    def get_visited_objects(self) -> tuple[AltiumObjectInfo, ...]:
        """Return the current traversal's canonical visited objects."""

        return tuple(self._global_objects[key] for key in self._current_order)

    def _prefer_object(
        self,
        current: AltiumObjectInfo,
        candidate: AltiumObjectInfo,
    ) -> AltiumObjectInfo:
        """Keep richer port/sheet-entry link information when it appears."""

        if isinstance(current, AltiumPortInfo) and isinstance(candidate, AltiumPortInfo):
            if current.link_sheet_entry_id or not candidate.link_sheet_entry_id:
                return current
            return replace(current, link_sheet_entry_id=candidate.link_sheet_entry_id)
        if isinstance(current, AltiumSheetEntryInfo) and isinstance(
            candidate,
            AltiumSheetEntryInfo,
        ):
            if current.link_port_id or not candidate.link_port_id:
                return current
            return replace(current, link_port_id=candidate.link_port_id)
        return current
