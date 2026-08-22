"""Immutable source-revision evidence captured while parsing a PcbDoc."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import struct
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from .altium_pcbdoc import AltiumPcbDoc


@dataclass(frozen=True)
class PcbDocSourceRecordSnapshot:
    """Parse-time identity and state for one source record."""

    collection_name: str
    stream_name: str
    record_index: int
    object_id: int
    state_sha256: str
    persistent_uid: str | None


@dataclass(frozen=True)
class PcbDocSourceIdentityDefect:
    """Identity evidence rejected by opt-in manufacturing provenance."""

    code: str
    detail: str


@dataclass(frozen=True)
class PcbDocSourceRevisionSnapshot:
    """Exact parsed bytes plus immutable record-order/state evidence."""

    document_revision_sha256: str
    source_path: Path | None
    collection_lengths: tuple[tuple[str, int], ...]
    records: tuple[PcbDocSourceRecordSnapshot, ...]
    identity_defects: tuple[PcbDocSourceIdentityDefect, ...] = ()

    def evidence_by_object_id(self) -> dict[int, PcbDocSourceRecordSnapshot]:
        return {record.object_id: record for record in self.records}

    def verify_all(self, pcbdoc: AltiumPcbDoc) -> None:
        """Reject collection, order, identity, or state changes since parsing."""

        current = _collection_records(pcbdoc)
        expected_lengths = dict(self.collection_lengths)
        if set(current) != set(expected_lengths):
            raise ValueError("parsed PcbDoc source collection set changed")
        for collection_name, expected_length in expected_lengths.items():
            if len(current[collection_name]) != expected_length:
                raise ValueError(f"parsed {collection_name} collection length changed")
        expected_by_collection: dict[str, list[PcbDocSourceRecordSnapshot]] = {}
        for record in self.records:
            expected_by_collection.setdefault(record.collection_name, []).append(record)
        for collection_name, expected in expected_by_collection.items():
            records = current[collection_name]
            for snapshot, record in zip(expected, records, strict=True):
                _verify_record(snapshot, record)

    def verify_record(self, pcbdoc: AltiumPcbDoc, object_id: int) -> None:
        """Verify one record remains at its parse-time collection position/state."""

        evidence = self.evidence_by_object_id().get(object_id)
        if evidence is None:
            raise KeyError(object_id)
        records = _collection_records(pcbdoc)[evidence.collection_name]
        if evidence.record_index >= len(records):
            raise ValueError(f"parsed {evidence.collection_name} record was removed")
        _verify_record(evidence, records[evidence.record_index])


@dataclass(frozen=True)
class _CollectionSpec:
    collection_name: str
    stream_name: str
    guid_type: int | None
    records: tuple[object, ...]


def capture_pcbdoc_source_revision(
    pcbdoc: AltiumPcbDoc,
    *,
    source_bytes: bytes,
    source_path: Path | None,
) -> PcbDocSourceRevisionSnapshot:
    """Capture immutable provenance only after parsing the supplied exact bytes."""

    guid_by_key, defects = _primitive_guid_map(pcbdoc._raw_streams)
    records: list[PcbDocSourceRecordSnapshot] = []
    seen_object_ids: set[int] = set()
    collections = _collection_specs(pcbdoc)
    duplicate_direct_uids = _duplicate_direct_uid_keys(collections)
    defects.extend(
        PcbDocSourceIdentityDefect(
            code="duplicate_source_uid",
            detail=f"{stream_name} contains duplicate source UID {uid!r}",
        )
        for stream_name, uid in sorted(duplicate_direct_uids)
    )
    for collection in collections:
        records.extend(
            _snapshot_collection(
                collection,
                guid_by_key=guid_by_key,
                seen_object_ids=seen_object_ids,
                duplicate_direct_uids=duplicate_direct_uids,
                defects=defects,
            )
        )
    return PcbDocSourceRevisionSnapshot(
        document_revision_sha256=hashlib.sha256(source_bytes).hexdigest(),
        source_path=source_path.resolve() if source_path is not None else None,
        collection_lengths=tuple(
            (collection.collection_name, len(collection.records))
            for collection in collections
        ),
        records=tuple(records),
        identity_defects=tuple(defects),
    )


def _snapshot_collection(
    collection: _CollectionSpec,
    *,
    guid_by_key: Mapping[tuple[int, int], str],
    seen_object_ids: set[int],
    duplicate_direct_uids: set[tuple[str, str]],
    defects: list[PcbDocSourceIdentityDefect],
) -> tuple[PcbDocSourceRecordSnapshot, ...]:
    result: list[PcbDocSourceRecordSnapshot] = []
    for record_index, record in enumerate(collection.records):
        object_id = id(record)
        if object_id in seen_object_ids:
            defects.append(
                PcbDocSourceIdentityDefect(
                    code="duplicate_object_membership",
                    detail="one PCB object occurs in more than one source collection",
                )
            )
        seen_object_ids.add(object_id)
        direct_uid = str(getattr(record, "unique_id", "") or "").strip()
        direct_key = (collection.stream_name, direct_uid)
        trusted_direct_uid = (
            direct_uid if direct_key not in duplicate_direct_uids else ""
        )
        guid = (
            guid_by_key.get((collection.guid_type, record_index))
            if collection.guid_type is not None
            else None
        )
        result.append(
            PcbDocSourceRecordSnapshot(
                collection_name=collection.collection_name,
                stream_name=collection.stream_name,
                record_index=record_index,
                object_id=object_id,
                state_sha256=_record_state_sha256(record),
                persistent_uid=trusted_direct_uid or guid,
            )
        )
    return tuple(result)


def _duplicate_direct_uid_keys(
    collections: tuple[_CollectionSpec, ...],
) -> set[tuple[str, str]]:
    counts: dict[tuple[str, str], int] = {}
    for collection in collections:
        for record in collection.records:
            uid = str(getattr(record, "unique_id", "") or "").strip()
            if uid:
                key = (collection.stream_name, uid)
                counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _collection_specs(pcbdoc: AltiumPcbDoc) -> tuple[_CollectionSpec, ...]:
    from .altium_pcb_enums import PcbGuidType

    board_records: tuple[object, ...] = () if pcbdoc.board is None else (pcbdoc.board,)
    return (
        _CollectionSpec("board", "Board6/Data", None, board_records),
        _CollectionSpec(
            "board_regions", "BoardRegions/Data", None, tuple(pcbdoc.board_regions)
        ),
        _CollectionSpec(
            "components",
            "Components6/Data",
            int(PcbGuidType.COMPONENT),
            tuple(pcbdoc.components),
        ),
        _CollectionSpec("nets", "Nets6/Data", None, tuple(pcbdoc.nets)),
        _CollectionSpec(
            "net_classes", "Classes6/Data", None, tuple(pcbdoc.net_classes)
        ),
        _CollectionSpec(
            "embedded_boards",
            "EmbeddedBoards6/Data",
            None,
            tuple(pcbdoc.embedded_boards),
        ),
        _CollectionSpec("polygons", "Polygons6/Data", None, tuple(pcbdoc.polygons)),
        _CollectionSpec("rules", "Rules6/Data", None, tuple(pcbdoc.rules)),
        _CollectionSpec("pads", "Pads6/Data", int(PcbGuidType.PAD), tuple(pcbdoc.pads)),
        _CollectionSpec("vias", "Vias6/Data", int(PcbGuidType.VIA), tuple(pcbdoc.vias)),
        _CollectionSpec(
            "tracks", "Tracks6/Data", int(PcbGuidType.TRACK), tuple(pcbdoc.tracks)
        ),
        _CollectionSpec("arcs", "Arcs6/Data", int(PcbGuidType.ARC), tuple(pcbdoc.arcs)),
        _CollectionSpec(
            "fills", "Fills6/Data", int(PcbGuidType.FILL), tuple(pcbdoc.fills)
        ),
        _CollectionSpec(
            "regions", "Regions6/Data", int(PcbGuidType.REGION), tuple(pcbdoc.regions)
        ),
        _CollectionSpec(
            "shapebased_regions",
            "ShapeBasedRegions6/Data",
            int(PcbGuidType.SHAPEBASED_REGION),
            tuple(pcbdoc.shapebased_regions),
        ),
    )


def _collection_records(pcbdoc: AltiumPcbDoc) -> dict[str, tuple[object, ...]]:
    return {spec.collection_name: spec.records for spec in _collection_specs(pcbdoc)}


def _verify_record(snapshot: PcbDocSourceRecordSnapshot, record: object) -> None:
    if id(record) != snapshot.object_id:
        raise ValueError(
            f"parsed {snapshot.collection_name} record order or identity changed"
        )
    if _record_state_sha256(record) != snapshot.state_sha256:
        raise ValueError(f"parsed {snapshot.collection_name} record state changed")


def _record_state_sha256(record: object) -> str:
    state_method = getattr(record, "_state_signature", None)
    if callable(state_method):
        state = state_method()
    else:
        state = {
            key: value for key, value in vars(record).items() if not key.startswith("_")
        }
    payload = json.dumps(
        _normalized_state(state),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _normalized_state(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Enum):
        return {
            "enum": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": value.value,
        }
    if isinstance(value, Mapping):
        return _normalized_mapping(value)
    if isinstance(value, Sequence):
        return [_normalized_state(item) for item in value]
    return _normalized_object(value)


def _normalized_mapping(value: Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): _normalized_state(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _normalized_object(value: object) -> object:
    public_state = {
        key: item for key, item in vars(value).items() if not key.startswith("_")
    }
    if public_state:
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            "state": _normalized_state(public_state),
        }
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}"}


def _primitive_guid_map(
    raw_streams: Mapping[str, bytes],
) -> tuple[dict[tuple[int, int], str], list[PcbDocSourceIdentityDefect]]:
    data = raw_streams.get("PrimitiveGuids/Data", b"")
    defects: list[PcbDocSourceIdentityDefect] = []
    if len(data) % 24 != 0:
        defects.append(
            PcbDocSourceIdentityDefect(
                code="malformed_primitive_guid_stream",
                detail="PrimitiveGuids/Data length is not divisible by 24",
            )
        )
        return {}, defects
    result: dict[tuple[int, int], str] = {}
    seen_by_type: set[tuple[int, uuid.UUID]] = set()
    for offset in range(0, len(data), 24):
        type_id, record_index = struct.unpack("<II", data[offset : offset + 8])
        guid = uuid.UUID(bytes_le=data[offset + 8 : offset + 24])
        key = (type_id, record_index)
        typed_guid = (type_id, guid)
        if key in result or typed_guid in seen_by_type:
            defects.append(
                PcbDocSourceIdentityDefect(
                    code="duplicate_primitive_guid",
                    detail="PrimitiveGuids/Data contains duplicate identity evidence",
                )
            )
            return {}, defects
        result[key] = str(guid)
        seen_by_type.add(typed_guid)
    return result, defects


__all__ = (
    "PcbDocSourceRecordSnapshot",
    "PcbDocSourceIdentityDefect",
    "PcbDocSourceRevisionSnapshot",
    "capture_pcbdoc_source_revision",
)
