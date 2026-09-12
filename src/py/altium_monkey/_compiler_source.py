"""Private, request-local schematic input views for compiler admission."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ._sch_source_projection import (
    _ignored_source_object_ids,
    _source_compile_mask_bounds,
)
from .altium_compiled_design_support import (
    _CompilerComponentSource,
    _CompilerSheetSymbolSource,
    _compiler_component_sources,
    _compiler_sheet_symbol_sources,
)
from .altium_schdoc_info import SchHarnessInfo, SchPinInfo, SchPortInfo
from .altium_record_types import SchPrimitive

if TYPE_CHECKING:
    from .altium_schdoc import AltiumSchDoc
    from .altium_record_sch__bus import AltiumSchBus
    from .altium_record_sch__bus_entry import AltiumSchBusEntry
    from .altium_record_sch__harness_connector import AltiumSchHarnessConnector
    from .altium_record_sch__harness_entry import AltiumSchHarnessEntry
    from .altium_record_sch__junction import AltiumSchJunction
    from .altium_record_sch__parameter import AltiumSchParameter
    from .altium_record_sch__pin import AltiumSchPin
    from .altium_record_sch__sheet import AltiumSchSheet
    from .altium_record_sch__signal_harness import AltiumSchSignalHarness
    from .altium_record_sch__wire import AltiumSchWire
    from .altium_schdoc_info import (
        SchCrossSheetConnectorInfo,
        SchNetLabelInfo,
        SchPowerPortInfo,
    )


@dataclass
class _AdmittedComponentSource(_CompilerComponentSource):
    _source_ordinal: int = 0
    _source_pins: tuple[AltiumSchPin, ...] = ()
    _source_footprint: str = ""
    _source_hidden_net_names: Mapping[int, str] = field(default_factory=dict)
    _source_ignored: frozenset[int] = frozenset()
    _source_graphics: tuple[object, ...] = ()

    @property
    def pins(self) -> list[AltiumSchPin]:
        return list(self._source_pins)

    @property
    def footprint(self) -> str:
        return self._source_footprint

    def display_body_element_ids(self) -> list[str]:
        admitted = {
            str(getattr(record, "unique_id", "") or "").strip()
            for record in self.record._display_body_records()
            if id(record) not in self._source_ignored
        }
        return [
            value
            for value in self.record.display_body_element_ids()
            if value in admitted
        ]


@dataclass
class _AdmittedSheetSymbolSource(_CompilerSheetSymbolSource):
    _source_ordinal: int = 0
    _source_entry_ordinals: tuple[int, ...] = ()
    _source_parameters: tuple[AltiumSchParameter, ...] = ()


@dataclass
class _AdmittedHarnessSource(SchHarnessInfo):
    _source_ordinal: int = 0
    _source_entry_ordinals: tuple[int, ...] = ()


@dataclass
class _AdmittedPortSource(SchPortInfo):
    _source_ordinal: int = 0


class _CompilerDocumentSource:
    def __init__(self, document: AltiumSchDoc) -> None:
        self._document = document
        self._parents = {
            id(row): self._source_parent(row) for row in document.all_objects
        }
        self._ignored = _ignored_source_object_ids(
            document.all_objects, parents=self._parents
        )
        self._hidden_net_names = self._prepare_hidden_net_names()
        self._unattached = self._prepare_parameter_membership()
        self._excluded = self._ignored | self._unattached
        self._parameters_by_owner = self._prepare_parameters()
        self._footprints_by_owner = self._prepare_footprints()
        self._harnesses = self._prepare_harnesses()
        self._harness_entries = {id(row.record): row.entries for row in self._harnesses}

    def admits(self, record: object) -> bool:
        return id(record) not in self._excluded

    def _source_parent(self, record: object) -> SchPrimitive | None:
        if not isinstance(record, SchPrimitive):
            return None
        if record.parent is not None:
            return record.parent
        reference = self._document._normalized_owner_refs.get(id(record))
        if reference is None:
            return None
        stream, index = reference
        records = (
            self._document._fileheader_objects
            if stream == "FileHeader"
            else self._document._additional_objects
        )
        if not 0 <= index < len(records):
            return None
        parent = records[index]
        return parent if isinstance(parent, SchPrimitive) else None

    def _admitted[T](self, records: Iterable[T]) -> list[T]:
        return [record for record in records if self.admits(record)]

    @property
    def filepath(self) -> Path | None:
        return self._document.filepath

    @property
    def sheet(self) -> AltiumSchSheet | None:
        return self._document.sheet

    @property
    def all_objects(self) -> Iterator[object]:
        return (record for record in self._document.all_objects if self.admits(record))

    @property
    def harness_connectors(self) -> list[AltiumSchHarnessConnector]:
        return [row.record for row in self._harnesses]

    @property
    def signal_harnesses(self) -> list[AltiumSchSignalHarness]:
        return self.get_signal_harnesses()

    @property
    def bus_entries(self) -> list[AltiumSchBusEntry]:
        return self._admitted(self._document.bus_entries)

    def get_components(self) -> list[_AdmittedComponentSource]:
        result: list[_AdmittedComponentSource] = []
        for ordinal, component in enumerate(
            _compiler_component_sources(self._document)
        ):
            if not self.admits(component.record):
                continue
            result.append(
                _AdmittedComponentSource(
                    record=component.record,
                    _source_designator=component._source_designator,
                    _source_parameters=self._component_parameters(component),
                    _source_ordinal=ordinal,
                    _source_pins=tuple(self._admitted(component.pins)),
                    _source_footprint=self._footprints_by_owner.get(
                        id(component.record), ""
                    ),
                    _source_hidden_net_names=self._hidden_net_names,
                    _source_ignored=self._excluded,
                    _source_graphics=tuple(self._admitted(component.record.graphics)),
                )
            )
        return result

    def _prepare_hidden_net_names(self) -> dict[int, str]:
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
        from .altium_record_sch__pin import AltiumSchPin

        names: dict[int, str] = {}
        for parameter in self._document.parameters:
            parent = self._source_parent(parameter)
            if not isinstance(parent, AltiumSchPin):
                continue
            if dotnet_ordinal_ignore_case_key(parameter.name) not in {
                "DEFAULTNET",
                "HIDDENNETNAME",
            }:
                continue
            names.setdefault(id(parent), "")
            if id(parameter) not in self._ignored:
                names[id(parent)] = parameter.text
        return names

    def _prepare_parameter_membership(self) -> frozenset[int]:
        from ._sch_source_projection import _parameter_source_exclusions

        return _parameter_source_exclusions(
            tuple(self._document.all_objects), self._parents, self._ignored
        )

    def _prepare_parameters(self) -> dict[int, list[AltiumSchParameter]]:
        from .altium_record_types import SchRecordType

        parameters: dict[int, list[AltiumSchParameter]] = {}
        document_parameters = {id(row) for row in self._document._document_parameters()}
        for parameter in self._document.parameters:
            if parameter.record_type != SchRecordType.PARAMETER or not self.admits(
                parameter
            ):
                continue
            owner = self._source_parent(parameter)
            if owner is None and id(parameter) in document_parameters:
                owner = self.sheet
            owner = self._parameter_owner(parameter, owner)
            if owner is not None:
                parameters.setdefault(id(owner), []).append(parameter)
        return parameters

    def _parameter_owner(
        self, parameter: AltiumSchParameter, owner: SchPrimitive | None
    ) -> SchPrimitive | None:
        from ._sch_source_projection import _parameter_attachment_role
        from .altium_record_types import SchRecordType

        role = _parameter_attachment_role(parameter, owner)
        if role in {"unattached", "pin_state"}:
            return None
        if getattr(owner, "record_type", None) == SchRecordType.IMPL_PARAMS:
            return self._source_parent(owner)
        return owner

    def _component_parameters(
        self, component: _CompilerComponentSource
    ) -> tuple[AltiumSchParameter, ...]:
        return tuple(self._parameters_by_owner.get(id(component.record), ()))

    def _prepare_footprints(self) -> dict[int, str]:
        from .altium_record_sch__implementation import (
            AltiumSchImplementation,
            AltiumSchImplementationList,
        )

        footprints: dict[int, str] = {}
        for record in self._document.all_objects:
            if not isinstance(record, AltiumSchImplementation) or not self.admits(
                record
            ):
                continue
            if not record.is_current or record.model_type.upper() != "PCBLIB":
                continue
            owner = self._source_parent(record)
            if isinstance(owner, AltiumSchImplementationList):
                owner = self._source_parent(owner)
            if owner is not None:
                footprints.setdefault(id(owner), record.model_name or "")
        return footprints

    def get_all_pins(self) -> list[SchPinInfo]:
        return [
            SchPinInfo(pin=pin, component=component)
            for component in self.get_components()
            for pin in component.pins
        ]

    def get_sheet_symbols(self) -> list[_AdmittedSheetSymbolSource]:
        result: list[_AdmittedSheetSymbolSource] = []
        for ordinal, symbol in enumerate(
            _compiler_sheet_symbol_sources(self._document)
        ):
            if not self.admits(symbol.record):
                continue
            entries = [
                (index, entry)
                for index, entry in enumerate(symbol.entries)
                if self.admits(entry)
            ]
            result.append(
                _AdmittedSheetSymbolSource(
                    record=symbol.record,
                    entries=[entry for _, entry in entries],
                    _source_sheet_name=symbol.designator,
                    _source_file_name=symbol.file_name,
                    _source_parameters=tuple(
                        self._parameters_by_owner.get(id(symbol.record), ())
                    ),
                    _source_ordinal=ordinal,
                    _source_entry_ordinals=tuple(index for index, _ in entries),
                )
            )
        return result

    def _prepare_harnesses(self) -> list[_AdmittedHarnessSource]:
        result: list[_AdmittedHarnessSource] = []
        for ordinal, harness in enumerate(self._document.get_harness_connectors()):
            if not self.admits(harness.record):
                continue
            entries = [
                (index, entry)
                for index, entry in enumerate(harness.entries)
                if self.admits(entry)
            ]
            result.append(
                _AdmittedHarnessSource(
                    record=harness.record,
                    entries=[entry for _, entry in entries],
                    _source_ordinal=ordinal,
                    _source_entry_ordinals=tuple(index for index, _ in entries),
                )
            )
        return result

    def get_harness_connectors(self) -> list[_AdmittedHarnessSource]:
        return list(self._harnesses)

    def harness_entries(self, connector: object) -> list[AltiumSchHarnessEntry]:
        return self._harness_entries.get(id(connector), [])

    def get_ports(self) -> list[_AdmittedPortSource]:
        return [
            _AdmittedPortSource(record=port.record, _source_ordinal=ordinal)
            for ordinal, port in enumerate(self._document.get_ports())
            if self.admits(port.record)
        ]

    def get_wires(self) -> list[AltiumSchWire]:
        return self._admitted(self._document.get_wires())

    def get_buses(self) -> list[AltiumSchBus]:
        return self._admitted(self._document.get_buses())

    def get_signal_harnesses(self) -> list[AltiumSchSignalHarness]:
        return self._admitted(self._document.get_signal_harnesses())

    def get_junctions(self) -> list[AltiumSchJunction]:
        return self._admitted(self._document.get_junctions())

    def get_net_labels(self) -> list[SchNetLabelInfo]:
        return [
            row for row in self._document.get_net_labels() if self.admits(row.record)
        ]

    def get_power_ports(self) -> list[SchPowerPortInfo]:
        return [
            row for row in self._document.get_power_ports() if self.admits(row.record)
        ]

    def get_cross_sheet_connectors(self) -> list[SchCrossSheetConnectorInfo]:
        return [
            row
            for row in self._document.get_cross_sheet_connectors()
            if self.admits(row.record)
        ]

    def _document_parameters(self) -> Iterator[AltiumSchParameter]:
        return iter(self._parameters_by_owner.get(id(self.sheet), ()))

    def get_parameter_dict(self) -> dict[str, str]:
        return {row.name: row.text for row in self._document_parameters()}

    def _collect_compile_mask_bounds(self) -> list[tuple[int, int, int, int]]:
        return _source_compile_mask_bounds(self.all_objects, precise=False)

    def _collect_compile_mask_precise_bounds(self) -> list[tuple[int, int, int, int]]:
        return _source_compile_mask_bounds(self.all_objects, precise=True)


def _compiler_document_source(
    schdoc: AltiumSchDoc | _CompilerDocumentSource,
) -> AltiumSchDoc:
    from .altium_schdoc import AltiumSchDoc

    source = (
        _CompilerDocumentSource(schdoc) if isinstance(schdoc, AltiumSchDoc) else schdoc
    )
    # The filtered view intentionally implements the compiler-facing SchDoc API.
    return cast(AltiumSchDoc, source)
