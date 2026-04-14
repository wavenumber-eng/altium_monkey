"""Signal context and pin helpers for the Altium netlist pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import TYPE_CHECKING, Iterable

from .altium_netlist_model import HierarchyPath
from .altium_netlist_signal_objects import AltiumObjectInfo, AltiumObjectType

if TYPE_CHECKING:
    from .altium_netlist_signal_info import AltiumSignalInfo
    from .altium_netlist_visited import AltiumVisitedObjectsManager


class AltiumSignalType(Enum):
    """Signal shape classification used by the netlist pipeline."""

    NORMAL = "normal"
    SUB = "sub"
    WIDE = "wide"
    BUS = "bus"
    HARNESS = "harness"


@dataclass(slots=True)
class AltiumSignalPinInfo:
    """Pin wrapper owned by a signal context."""

    object_id: str
    component_designator: str
    pin_name: str
    part_unique_id: str = ""
    hierarchy_path: HierarchyPath = field(default_factory=HierarchyPath)
    owner_part_id: str | None = None
    is_multi_part: bool = False
    local_signal: AltiumSignalInfo | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    global_signal: AltiumSignalInfo | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )
    _default_designator: str | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def get_designator(self, separator: str = ".") -> str:
        """Return the component/pin designator string."""

        if separator != ".":
            return f"{self.component_designator}{separator}{self.pin_name}"
        if self._default_designator is None:
            self._default_designator = f"{self.component_designator}.{self.pin_name}"
        return self._default_designator

    def does_it_come_from_alternate_part(self) -> bool:
        """Return True when the part unique ID carries a variant suffix."""

        return "@" in self.part_unique_id

    def get_project_variant_description(self) -> str:
        """Return the variant description suffix from the part unique ID."""

        if "@" not in self.part_unique_id:
            return ""
        return self.part_unique_id.split("@", 1)[1]

    def set_signal(self, signal: AltiumSignalInfo) -> None:
        """Attach the most-preferred local/global signal reference."""

        range_value = _normalize_signal_range(signal)
        if range_value == "global":
            if self.global_signal is None or _signal_name_key(signal) < _signal_name_key(
                self.global_signal,
            ):
                self.global_signal = signal
            return
        if self.local_signal is None or _signal_name_key(signal) < _signal_name_key(
            self.local_signal,
        ):
            self.local_signal = signal

    attach_signal = set_signal

    def __str__(self) -> str:
        return self.get_designator()


class AltiumSignalUniquePinsQueryContext:
    """Reusable buffer for multipart unique-pin filtering."""

    def __init__(self) -> None:
        self._pins_buffer: list[AltiumSignalPinInfo] = []
        self._pins_first_occurrences: dict[str, AltiumSignalPinInfo] = {}
        self._pin_designator_to_owner_ids: dict[str, list[str]] = {}

    def begin_query(self, pins_buffer_capacity: int) -> None:
        """Reset internal buffers for a new unique-pin query."""

        del pins_buffer_capacity
        self._pins_buffer.clear()
        self._pins_first_occurrences.clear()
        self._clear_pin_designator_to_owner_ids()

    def add_first_occurrence(
        self,
        pin: AltiumSignalPinInfo,
        pin_designator: str,
    ) -> tuple[bool, AltiumSignalPinInfo]:
        """Track and return the first pin seen for a designator."""

        key = pin_designator.casefold()
        pin_first_occurrence = self._pins_first_occurrences.get(key)
        if pin_first_occurrence is None:
            self._pins_first_occurrences[key] = pin
            return True, pin
        return False, pin_first_occurrence

    def get_pin_designator_to_owner_ids_list(self, pin_designator: str) -> list[str]:
        """Return the owner-ID list tracked for one designator."""

        key = pin_designator.casefold()
        owner_ids = self._pin_designator_to_owner_ids.get(key)
        if owner_ids is None:
            owner_ids = []
            self._pin_designator_to_owner_ids[key] = owner_ids
        return owner_ids

    def add_pin(self, pin: AltiumSignalPinInfo) -> None:
        """Append a pin to the current unique-pin query result."""

        self._pins_buffer.append(pin)

    def get_pins(self) -> tuple[AltiumSignalPinInfo, ...]:
        """Return the pins captured during the current query."""

        return tuple(self._pins_buffer)

    def end_query(self) -> None:
        """Release scratch dictionaries while keeping the result buffer."""

        self._pins_first_occurrences.clear()
        self._clear_pin_designator_to_owner_ids()

    def _clear_pin_designator_to_owner_ids(self) -> None:
        for owner_ids in self._pin_designator_to_owner_ids.values():
            owner_ids.clear()
        self._pin_designator_to_owner_ids.clear()


class AltiumSignalContext:
    """Pins and trace objects owned by one signal."""

    def __init__(self) -> None:
        self._pins: list[AltiumSignalPinInfo] = []
        self._objects: list[AltiumObjectInfo] = []
        self._signal: AltiumSignalInfo | None = None
        self._can_contain_duplicated_pins = False

    @property
    def objects(self) -> tuple[AltiumObjectInfo, ...]:
        """Return the objects captured in this signal."""

        return tuple(self._objects)

    @property
    def content_count(self) -> int:
        """Return the combined object and pin count."""

        return len(self._objects) + len(self._pins)

    @property
    def content(self) -> tuple[AltiumObjectInfo | AltiumSignalPinInfo, ...]:
        """Return objects followed by pins in the signal context order."""

        return tuple(self._objects) + tuple(self._pins)

    def get_signal_type(self) -> AltiumSignalType:
        """Return the signal type for this context."""

        return AltiumSignalType.NORMAL

    def get_all_pins(self) -> tuple[AltiumSignalPinInfo, ...]:
        """Return all pins owned by the context."""

        return tuple(self._pins)

    def get_unique_pins(
        self,
        context: AltiumSignalUniquePinsQueryContext | None = None,
    ) -> tuple[AltiumSignalPinInfo, ...]:
        """Return de-duplicated pins for multipart components."""

        if not self._can_contain_duplicated_pins:
            return tuple(self._pins)
        if context is None:
            context = AltiumSignalUniquePinsQueryContext()
        context.begin_query(len(self._pins))
        for current in self._pins:
            designator = current.get_designator()
            is_first_occurrence, first_occurrence = context.add_first_occurrence(
                current,
                designator,
            )
            if is_first_occurrence:
                context.add_pin(current)
                continue
            owner_ids = context.get_pin_designator_to_owner_ids_list(designator)
            if not owner_ids:
                first_owner_id = _get_pin_owner_id(first_occurrence)
                if first_owner_id is not None:
                    owner_ids.append(first_owner_id)
            current_owner_id = _get_pin_owner_id(current)
            if current_owner_id is None or current_owner_id not in owner_ids:
                context.add_pin(current)
                if current_owner_id is not None:
                    owner_ids.append(current_owner_id)
        pins = context.get_pins()
        context.end_query()
        return pins

    def add_pin(self, new_pin: AltiumSignalPinInfo) -> None:
        """Attach and store one pin in the context."""

        signal = self._require_signal()
        _attach_signal(new_pin, signal)
        self._pins.append(new_pin)
        self._can_contain_duplicated_pins |= _pin_can_duplicate(new_pin)

    def add_objects(
        self,
        new_objects: Iterable[AltiumObjectInfo] | AltiumVisitedObjectsManager,
    ) -> None:
        """Attach and store objects from an iterable or visited manager."""

        signal = self._require_signal()
        if hasattr(new_objects, "get_visited_objects"):
            iterable = new_objects.get_visited_objects()
        else:
            iterable = new_objects
        for new_object in iterable:
            _attach_signal(new_object, signal)
            self._objects.append(new_object)

    def set_signal(self, signal: AltiumSignalInfo) -> None:
        """Attach the owning signal exactly once."""

        if self._signal is not None:
            raise InvalidOperationError(
                "There is already one SignalInfo attached to this SignalContext.",
            )
        self._signal = signal

    def sort_pins(self) -> None:
        """Sort pins by component designator, then pin name."""

        self._pins.sort(
            key=lambda pin: (
                _alpha_numeric_key(pin.component_designator),
                _alpha_numeric_key(pin.pin_name),
            ),
        )

    def sort_objects(self) -> None:
        """Sort objects deterministically by type and identity."""

        self._objects.sort(key=_object_sort_key)

    def _require_signal(self) -> AltiumSignalInfo:
        if self._signal is None:
            raise InvalidOperationError(
                "There is no SignalInfo attached to this SignalContext.",
            )
        return self._signal


class InvalidOperationError(RuntimeError):
    """Runtime error used for invalid signal-context lifecycle operations."""


def _attach_signal(target: AltiumObjectInfo | AltiumSignalPinInfo, signal: AltiumSignalInfo) -> None:
    setter = getattr(target, "set_signal", None)
    if callable(setter):
        setter(signal)
        return
    setter = getattr(target, "attach_signal", None)
    if callable(setter):
        setter(signal)


def _alpha_numeric_key(value: str) -> list[int | str]:
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value or "")
    ]


def _get_pin_owner_id(pin: AltiumSignalPinInfo) -> str | None:
    for attr_name in (
        "owner_part_id",
        "owner_part_uid",
        "owner_part_unique_id",
    ):
        attr_value = getattr(pin, attr_name, None)
        if attr_value not in (None, ""):
            return str(attr_value)
    part = getattr(pin, "part", None)
    if part is not None:
        owner_part = getattr(part, "owner_part", None)
        if owner_part is not None:
            owner_part_id = getattr(owner_part, "id", None)
            if owner_part_id not in (None, ""):
                return str(owner_part_id)
        get_owner_part = getattr(part, "get_owner_part", None)
        if callable(get_owner_part):
            owner_part = get_owner_part()
            owner_part_id = getattr(owner_part, "id", None)
            if owner_part_id not in (None, ""):
                return str(owner_part_id)
    if pin.part_unique_id:
        return pin.part_unique_id.split("@", 1)[0]
    component_unique_id = getattr(pin, "component_unique_id", None)
    if component_unique_id not in (None, ""):
        return str(component_unique_id)
    if pin.component_designator:
        return pin.component_designator
    return None


def _pin_can_duplicate(pin: AltiumSignalPinInfo) -> bool:
    for attr_name in ("can_duplicate", "is_multi_part"):
        attr_value = getattr(pin, attr_name, None)
        if callable(attr_value):
            attr_value = attr_value()
        if attr_value is not None:
            return bool(attr_value)
    value = getattr(pin, "value", None)
    owner_object = getattr(value, "owner_object", None)
    if owner_object is not None:
        attr_value = getattr(owner_object, "is_multi_part", None)
        if callable(attr_value):
            attr_value = attr_value()
        if attr_value is not None:
            return bool(attr_value)
    return False


def _object_sort_key(obj: AltiumObjectInfo) -> tuple:
    try:
        object_type_index = list(AltiumObjectType).index(obj.object_type)
    except ValueError:
        object_type_index = len(AltiumObjectType)
    location_key = _location_sort_key(obj)
    return (
        object_type_index,
        location_key,
        obj.schematic_id.casefold(),
        obj.object_id.casefold(),
        obj.hierarchy_path.unique_id_path.casefold(),
        -2 if obj.bus_signal_index is None else obj.bus_signal_index,
        tuple(part.casefold() for part in (obj.harness_entries_path or ())),
        -2 if obj.repeat_value is None else obj.repeat_value,
    )


def _location_sort_key(obj: AltiumObjectInfo) -> tuple:
    for candidate in (
        getattr(obj, "location", None),
        getattr(getattr(obj, "value", None), "location", None),
    ):
        if isinstance(candidate, tuple) and len(candidate) >= 2:
            return (0, candidate[0], candidate[1])
        if hasattr(candidate, "x") and hasattr(candidate, "y"):
            return (0, candidate.x, candidate.y)
    return (1, 0, 0)


def _signal_name_key(signal: AltiumSignalInfo) -> list[int | str]:
    full_name_info = getattr(signal, "full_name_info", None)
    full_name = getattr(full_name_info, "full_name", "")
    return _alpha_numeric_key(full_name)


def _normalize_signal_range(signal: AltiumSignalInfo) -> str:
    signal_range = signal.get_range()
    range_value = getattr(signal_range, "value", signal_range)
    return str(range_value).casefold()


__all__ = [
    "AltiumSignalContext",
    "AltiumSignalPinInfo",
    "AltiumSignalType",
    "AltiumSignalUniquePinsQueryContext",
    "InvalidOperationError",
]
