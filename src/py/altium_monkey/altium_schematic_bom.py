"""Versioned schematic BOM transport."""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

import msgspec

from .altium_schematic_contract import (
    JsonPreflightResult,
    SchematicContractError,
    SchematicContractLimits,
    enforce_collection_limit,
    enforce_output_limit,
    pointer_child,
    preflight_json_bytes,
    preflight_json_text,
    preflight_mapping,
    resolve_limits,
)
from .sch_compiled_design.generated.models import (
    SchematicBomA0,
    SchematicBomA0Component,
    SchematicSourcePage,
)


SCHEMATIC_BOM_SCHEMA = "altium_monkey.schematic_bom.a0"


class SchematicBomPayload:
    """Immutable public wrapper around the generated schematic BOM DTO."""

    __slots__ = ("_dto",)

    def __init__(self) -> None:
        raise TypeError(
            "create SchematicBomPayload with AltiumDesign.to_bom_payload() "
            "or SchematicBomPayload.from_json*()"
        )

    @property
    def selected_variant(self) -> str | None:
        """Return the exact selected variant, or None for the base projection."""
        return self._dto.selected_variant

    @property
    def rows(self) -> tuple[Mapping[str, object], ...]:
        """Return deeply immutable component mappings in canonical order."""
        return tuple(_immutable_row(row) for row in self._dto.components)

    def to_json(
        self, *, limits: SchematicContractLimits | None = None
    ) -> dict[str, object]:
        """Return the canonical schematic BOM mapping."""
        dto = _canonical_dto(self._dto, resolve_limits(limits))
        value = msgspec.to_builtins(dto)
        if not isinstance(value, dict):
            raise RuntimeError("generated schematic BOM root is not an object")
        mapping = cast(dict[str, object], value)
        preflight_mapping(mapping, limits)
        return mapping

    def to_json_text(self, *, limits: SchematicContractLimits | None = None) -> str:
        """Return canonical compact schematic BOM JSON text."""
        return self.to_json_bytes(limits=limits).decode("utf-8")

    def to_json_bytes(self, *, limits: SchematicContractLimits | None = None) -> bytes:
        """Return canonical compact schematic BOM UTF-8 bytes."""
        dto = _canonical_dto(self._dto, resolve_limits(limits))
        return enforce_output_limit(msgspec.json.encode(dto), limits)

    @classmethod
    def from_json(
        cls,
        mapping: Mapping[str, object],
        *,
        limits: SchematicContractLimits | None = None,
    ) -> SchematicBomPayload:
        """Validate and wrap one already-materialized BOM mapping."""
        route = preflight_mapping(mapping, limits)
        _require_schema(route)
        try:
            dto = msgspec.convert(mapping, type=SchematicBomA0, strict=True)
        except msgspec.ValidationError as error:
            raise _validation_error(error) from error
        return cls._from_dto(dto, resolve_limits(limits))

    @classmethod
    def from_json_text(
        cls,
        text: str,
        *,
        limits: SchematicContractLimits | None = None,
    ) -> SchematicBomPayload:
        """Validate and wrap one bounded BOM JSON text."""
        route = preflight_json_text(text, limits)
        _require_schema(route)
        return cls._decode(text, resolve_limits(limits))

    @classmethod
    def from_json_bytes(
        cls,
        raw: bytes,
        *,
        limits: SchematicContractLimits | None = None,
    ) -> SchematicBomPayload:
        """Validate and wrap one bounded BOM UTF-8 JSON value."""
        _text, route = preflight_json_bytes(raw, limits)
        _require_schema(route)
        return cls._decode(raw, resolve_limits(limits))

    @classmethod
    def _decode(
        cls, raw: bytes | str, limits: SchematicContractLimits
    ) -> SchematicBomPayload:
        try:
            dto = msgspec.json.decode(raw, type=SchematicBomA0)
        except msgspec.ValidationError as error:
            raise _validation_error(error) from error
        return cls._from_dto(dto, limits)

    @classmethod
    def _from_dto(
        cls, dto: SchematicBomA0, limits: SchematicContractLimits
    ) -> SchematicBomPayload:
        instance = cls.__new__(cls)
        instance._dto = _canonical_dto(dto, limits)
        return instance


def _canonical_dto(
    dto: SchematicBomA0, limits: SchematicContractLimits
) -> SchematicBomA0:
    if dto.selected_variant == "":
        raise SchematicContractError(
            "invalid_literal", "/selected_variant", "variant must be non-empty"
        )
    enforce_collection_limit(
        len(dto.components), limits.max_components, "/components", "component limit"
    )
    enforce_collection_limit(
        len(dto.components),
        limits.max_source_pages,
        "/components",
        "component source-page limit",
    )
    ids: set[str] = set()
    parameter_count = 0
    pages: dict[str, str] = {}
    rows: list[SchematicBomA0Component] = []
    for index, row in enumerate(dto.components):
        path = pointer_child("/components", index)
        if not row.component_id:
            raise SchematicContractError(
                "invariant", f"{path}/component_id", "identity must be non-empty"
            )
        if not row.designator:
            raise SchematicContractError(
                "invariant", f"{path}/designator", "identity must be non-empty"
            )
        if row.component_id in ids:
            raise SchematicContractError(
                "duplicate_identity",
                f"{path}/component_id",
                f"duplicate identity {row.component_id!r}",
            )
        ids.add(row.component_id)
        if row.dnp == row.fitted:
            raise SchematicContractError(
                "invariant", path, "dnp must be the inverse of fitted"
            )
        _validate_page(row.source_page, pages, f"{path}/source_page")
        parameter_count += len(row.parameters)
        rows.append(
            msgspec.structs.replace(
                row, parameters=dict(sorted(row.parameters.items()))
            )
        )
    enforce_collection_limit(
        parameter_count,
        limits.max_parameter_pairs,
        "/components",
        "total parameter-pair limit",
    )
    return msgspec.structs.replace(
        dto, components=sorted(rows, key=lambda row: (row.component_id, row.designator))
    )


def _validate_page(
    page: SchematicSourcePage, observed: dict[str, str], path: str
) -> None:
    physical = page.physical_document_id
    filename = page.source_sheet_file
    if physical == "" or filename == "" or (physical is None and filename is None):
        raise SchematicContractError(
            "invariant", path, "source page requires one non-empty identity"
        )
    if physical is None or filename is None:
        return
    previous = observed.setdefault(physical, filename)
    if previous != filename:
        raise SchematicContractError(
            "invariant", path, "physical document maps to two filenames"
        )


def _immutable_row(row: SchematicBomA0Component) -> Mapping[str, object]:
    parameters: Mapping[str, str] = MappingProxyType(dict(row.parameters))
    values: dict[str, object] = {
        "component_id": row.component_id,
        "designator": row.designator,
        "logical_designator": row.logical_designator,
        "physical_designator": row.physical_designator,
        "source_page": MappingProxyType(
            {
                "physical_document_id": row.source_page.physical_document_id,
                "source_sheet_file": row.source_page.source_sheet_file,
            }
        ),
        "value": row.value,
        "footprint": row.footprint,
        "library_ref": row.library_ref,
        "description": row.description,
        "parameters": parameters,
        "fitted": row.fitted,
        "dnp": row.dnp,
    }
    return MappingProxyType(values)


def _require_schema(route: JsonPreflightResult) -> None:
    if not route.schema_present:
        raise SchematicContractError(
            "unsupported_schema", "", "schematic BOM schema marker is required"
        )
    if route.schema is None:
        raise SchematicContractError(
            "type_mismatch", "/schema", "schema must be a string"
        )
    if route.schema != SCHEMATIC_BOM_SCHEMA:
        raise SchematicContractError(
            "unsupported_schema", "/schema", f"unsupported schema {route.schema!r}"
        )


def _validation_error(error: msgspec.ValidationError) -> SchematicContractError:
    detail = str(error)
    path = _msgspec_path(detail)
    missing = re.search(r"missing required field `([^`]+)`", detail, re.IGNORECASE)
    if missing:
        return SchematicContractError(
            "missing_field", pointer_child(path, missing.group(1)), detail
        )
    unknown = re.search(r"unknown field `([^`]+)`", detail, re.IGNORECASE)
    if unknown:
        return SchematicContractError(
            "unknown_field", pointer_child(path, unknown.group(1)), detail
        )
    if "Expected `altium_monkey." in detail:
        return SchematicContractError("invalid_literal", path, detail)
    return SchematicContractError("type_mismatch", path, detail)


def _msgspec_path(detail: str) -> str:
    match = re.search(r" - at `\$([^`]*)`", detail)
    if match is None:
        return ""
    path = ""
    for field_name, index in re.findall(r"\.([^\.\[]+)|\[([0-9]+)\]", match.group(1)):
        path = pointer_child(path, field_name or index)
    return path


__all__ = ("SCHEMATIC_BOM_SCHEMA", "SchematicBomPayload")
