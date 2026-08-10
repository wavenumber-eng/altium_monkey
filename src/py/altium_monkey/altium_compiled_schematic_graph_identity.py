"""Stable source selectors for Altium compiled schematic graph projection.

This module owns the Altium-specific half of compiled-graph identity.  It does
not allocate canonical graph UUIDs; it converts Altium source evidence into the
governed selector vocabulary consumed by that allocator.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Mapping, Sequence

SOURCE_UUID_KEY = "sch.source_key.source_uuid"
SOURCE_PATH_KEY = "sch.source_key.source_path"
SOURCE_SUBOBJECT_KEY = "sch.source_key.source_subobject"
_SOURCE_RECORD_KEY = "sch.source_key.source_record"
_COMPILED_NET_KEY = "sch.source_key.compiled_net"
_ARTIFACT_ELEMENT_KEY = "sch.source_key.artifact_element"
_OMIT_NORMALIZED_VALUE = object()

SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE = "sch.compiled_schematic_graph.a0"
SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_EPOCH_MS = 1_786_060_800_000


class SchCompiledSchematicGraphIdentityAllocator:
    """Allocate stable UUIDv7 identities from governed source addresses.

    Keep this address shape byte-for-byte compatible with the governed generic
    schematic-graph allocator. Altium source-specific selector construction
    lives in the helper functions below.
    """

    def __init__(self, *, design_scope: Mapping[str, object]) -> None:
        scope = _normalized_mapping(design_scope)
        if not scope:
            raise ValueError("schematic identity design_scope must not be empty")
        self._design_scope = scope
        self._address_by_id: dict[str, str] = {}
        self._allocated_addresses: set[str] = set()

    def allocate_source(
        self,
        *,
        object_type: str,
        source_identity: Mapping[str, object],
        owner_refs: Sequence[str] = (),
    ) -> str:
        source = _stable_source_selector(
            object_type=object_type,
            source_identity=source_identity,
        )
        if not source:
            raise ValueError(
                f"{object_type} requires governed source identity for stable allocation"
            )
        return self._allocate(
            object_type=object_type,
            identity={
                "source_identity": source,
                "owner_refs": _normalized_refs(owner_refs),
            },
        )

    def allocate_derived(
        self,
        *,
        object_type: str,
        identity: Mapping[str, object],
    ) -> str:
        normalized = _normalized_mapping(identity)
        if not normalized:
            raise ValueError(f"{object_type} derived identity must not be empty")
        return self._allocate(object_type=object_type, identity=normalized)

    def _allocate(
        self,
        *,
        object_type: str,
        identity: Mapping[str, object],
    ) -> str:
        normalized_type = str(object_type or "").strip()
        if not normalized_type.startswith("sch."):
            raise ValueError(
                "schematic occurrence identity object_type must use the sch. namespace"
            )
        address = _canonical_json(
            {
                "namespace": SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE,
                "design_scope": self._design_scope,
                "object_type": normalized_type,
                "identity": identity,
            }
        )
        if address in self._allocated_addresses:
            raise ValueError(
                f"duplicate stable schematic identity address for {normalized_type}"
            )
        object_id = _deterministic_uuidv7(address)
        previous = self._address_by_id.get(object_id)
        if previous is not None and previous != address:
            raise ValueError(
                f"stable schematic identity collision for {normalized_type}: {object_id}"
            )
        self._allocated_addresses.add(address)
        self._address_by_id[object_id] = address
        return object_id


def compiled_schematic_graph_design_scope(
    *, source_cad: object, project: object
) -> dict[str, str]:
    """Return the portable Design namespace used by occurrence allocation."""

    project_row = project if isinstance(project, Mapping) else {}
    project_file = str(
        project_row.get("filename") or project_row.get("name") or ""
    ).strip()
    if not project_file:
        raise ValueError(
            "compiled schematic identity requires a source project filename or name"
        )
    return {
        "source_cad": str(source_cad or "unknown").strip().casefold(),
        "project_file": project_file.replace("\\", "/").casefold(),
    }


def compiled_schematic_unit_occurrence_source_identity(
    physical_instance_unique_id: str,
) -> dict[str, str]:
    """Return the realized, occurrence-specific hierarchy-path selector."""

    source_path = str(physical_instance_unique_id or "").strip()
    if not source_path:
        raise ValueError(
            "compiled schematic unit occurrence requires a physical instance path"
        )
    return {SOURCE_PATH_KEY: source_path}


def compiled_schematic_pin_source_identity(
    *,
    component_source_uid: str,
    owner_part_id: int | None,
    pin_designator: str,
    pin_name: str,
    pin_source_uid: str | None,
) -> dict[str, str]:
    """Return stable source identity for one pin terminal.

    Altium pin records normally carry their own UniqueID. Older source files
    can omit it. In that case the stable discriminator is scoped to the owning
    placed symbol body and uses only source-semantic pin fields. Record order
    and display position are deliberately absent.
    """

    pin_uid = str(pin_source_uid or "").strip()
    if pin_uid:
        return {SOURCE_UUID_KEY: pin_uid}

    component_uid = str(component_source_uid or "").strip()
    if not component_uid:
        raise ValueError(
            "UID-less compiled schematic pin requires an owning component source UID"
        )
    part_id = int(owner_part_id if owner_part_id is not None else 1)
    if part_id <= 0:
        part_id = 1
    subobject = json.dumps(
        ["pin", part_id, str(pin_designator or ""), str(pin_name or "")],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return {
        SOURCE_UUID_KEY: component_uid,
        SOURCE_SUBOBJECT_KEY: subobject,
    }


def require_unique_compiled_schematic_source_identities(
    identities: Iterable[Mapping[str, object]],
    *,
    object_type: str,
) -> None:
    """Fail closed when two source objects claim one stable selector."""

    seen: set[str] = set()
    for identity in identities:
        canonical = json.dumps(
            dict(identity),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        if canonical in seen:
            raise ValueError(
                f"duplicate stable Altium source identity for {object_type}: "
                f"{canonical}"
            )
        seen.add(canonical)


def _definition_source_selector(
    source_identity: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: source_identity[key]
        for key in (SOURCE_PATH_KEY, SOURCE_UUID_KEY)
        if source_identity.get(key)
    }


def _terminal_source_selector(
    source_identity: Mapping[str, object],
) -> dict[str, object]:
    source_uuid = source_identity.get(SOURCE_UUID_KEY)
    if not source_uuid:
        return {}
    selector = {SOURCE_UUID_KEY: source_uuid}
    source_subobject = source_identity.get(SOURCE_SUBOBJECT_KEY)
    if source_subobject:
        selector[SOURCE_SUBOBJECT_KEY] = source_subobject
    return selector


def _first_source_selector(
    source_identity: Mapping[str, object],
) -> dict[str, object]:
    for key in (
        SOURCE_UUID_KEY,
        _COMPILED_NET_KEY,
        SOURCE_PATH_KEY,
        _ARTIFACT_ELEMENT_KEY,
        _SOURCE_RECORD_KEY,
    ):
        value = source_identity.get(key)
        if value:
            return {key: value}
    return {}


def _stable_source_selector(
    *, object_type: str, source_identity: Mapping[str, object]
) -> dict[str, object]:
    normalized = _normalized_mapping(source_identity)
    if object_type == "sch.unit_occurrence" and normalized.get(SOURCE_PATH_KEY):
        return {SOURCE_PATH_KEY: normalized[SOURCE_PATH_KEY]}
    if object_type in {"sch.unit_definition", "sch.page_definition"}:
        return _definition_source_selector(normalized)
    if object_type == "sch.terminal_occurrence":
        return _terminal_source_selector(normalized)
    if object_type == "sch.local_net_occurrence":
        return {}
    return _first_source_selector(normalized)


def _deterministic_uuidv7(address: str) -> str:
    digest = hashlib.sha256(address.encode("utf-8")).digest()
    value = bytearray(16)
    value[:6] = SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_EPOCH_MS.to_bytes(6, "big")
    value[6] = 0x70 | (digest[0] & 0x0F)
    value[7] = digest[1]
    value[8] = 0x80 | (digest[2] & 0x3F)
    value[9:] = digest[3:10]
    return str(uuid.UUID(bytes=bytes(value)))


def _normalized_sequence(value: Sequence[object]) -> list[str]:
    normalized: list[str] = []
    for entry in value:
        text = str(entry or "").strip()
        if text:
            normalized.append(text)
    return normalized


def _normalized_value(value: object) -> object:
    if value is None:
        return _OMIT_NORMALIZED_VALUE
    if isinstance(value, Mapping):
        nested = _normalized_mapping(value)
        return nested or _OMIT_NORMALIZED_VALUE
    if isinstance(value, str):
        return value.strip() or _OMIT_NORMALIZED_VALUE
    if isinstance(value, bytes):
        return _OMIT_NORMALIZED_VALUE
    if isinstance(value, Sequence):
        return _normalized_sequence(value)
    if isinstance(value, bool | int | float):
        return value
    return _OMIT_NORMALIZED_VALUE


def _normalized_mapping(value: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key in sorted(value, key=str):
        text_key = str(key or "").strip()
        if not text_key:
            continue
        item = _normalized_value(value[key])
        if item is not _OMIT_NORMALIZED_VALUE:
            normalized[text_key] = item
    return normalized


def _normalized_refs(values: Sequence[str]) -> list[str]:
    refs = [str(value or "").strip() for value in values]
    return [value for value in refs if value]


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


__all__ = [
    "SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_EPOCH_MS",
    "SCH_COMPILED_SCHEMATIC_GRAPH_IDENTITY_NAMESPACE",
    "SOURCE_PATH_KEY",
    "SOURCE_SUBOBJECT_KEY",
    "SOURCE_UUID_KEY",
    "SchCompiledSchematicGraphIdentityAllocator",
    "compiled_schematic_graph_design_scope",
    "compiled_schematic_pin_source_identity",
    "compiled_schematic_unit_occurrence_source_identity",
    "require_unique_compiled_schematic_source_identities",
]
