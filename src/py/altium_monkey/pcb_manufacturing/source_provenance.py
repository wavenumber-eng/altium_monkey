"""Build governed manufacturing provenance from PCB source records."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING, Literal, Mapping, cast

from msgspec import UNSET

from .generated import (
    Diagnostic,
    LocatedSource,
    RuntimeSource,
    SourceProvenance,
    UnresolvedSource,
)
from .units import PCB_SOURCE_UNIT_NM_DENOMINATOR, PCB_SOURCE_UNIT_NM_NUMERATOR

if TYPE_CHECKING:
    from altium_monkey.altium_pcbdoc import AltiumPcbDoc
    from altium_monkey.altium_pcb_source_snapshot import (
        PcbDocSourceRevisionSnapshot,
    )


@dataclass(frozen=True)
class PcbSourceProvenanceError(ValueError):
    """Stable failure while associating a PCB object with source evidence."""

    code: str
    detail: str


@dataclass(frozen=True)
class PcbSourceResolution:
    """Resolved provenance plus any diagnostic required to retain geometry."""

    source: SourceProvenance
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class _RecordEvidence:
    stream_name: str
    record_index: int
    persistent_uid: str | None


class PcbDocSourceIndex:
    """Immutable object-identity index for one exact file-backed PcbDoc revision."""

    def __init__(
        self,
        *,
        document_revision_sha256: str,
        logical_path: str,
        evidence_by_object_id: Mapping[int, _RecordEvidence],
        pcbdoc: AltiumPcbDoc,
        snapshot: PcbDocSourceRevisionSnapshot,
    ) -> None:
        self.document_revision_sha256 = _require_sha256(document_revision_sha256)
        self.logical_path = _normalize_logical_path(logical_path)
        self._evidence_by_object_id = dict(evidence_by_object_id)
        self._pcbdoc = pcbdoc
        self._snapshot = snapshot

    @classmethod
    def from_pcbdoc(
        cls,
        pcbdoc: AltiumPcbDoc,
        *,
        logical_path: str,
        source_path: Path | str | None = None,
    ) -> PcbDocSourceIndex:
        """Index records against the SHA-256 of the exact compound document bytes."""

        from altium_monkey.altium_pcb_source_snapshot import (
            PcbDocSourceRevisionSnapshot,
        )

        snapshot = cast(
            PcbDocSourceRevisionSnapshot | None,
            getattr(pcbdoc, "_source_revision_snapshot", None),
        )
        if snapshot is None:
            raise PcbSourceProvenanceError(
                "unverified_source_document",
                "file-backed provenance requires AltiumPcbDoc.from_file/from_bytes",
            )
        _verify_requested_source_path(snapshot, source_path)
        if snapshot.identity_defects:
            detail = "; ".join(defect.detail for defect in snapshot.identity_defects)
            raise PcbSourceProvenanceError("corrupt_identity", detail)
        try:
            snapshot.verify_all(pcbdoc)
        except ValueError as exc:
            raise PcbSourceProvenanceError("mutated_source_document", str(exc)) from exc
        evidence = {
            object_id: _RecordEvidence(
                stream_name=item.stream_name,
                record_index=item.record_index,
                persistent_uid=item.persistent_uid,
            )
            for object_id, item in snapshot.evidence_by_object_id().items()
        }
        return cls(
            document_revision_sha256=snapshot.document_revision_sha256,
            logical_path=logical_path,
            evidence_by_object_id=evidence,
            pcbdoc=pcbdoc,
            snapshot=snapshot,
        )

    def source_for(
        self,
        record: object,
        *,
        subrecord_index: int | None = None,
    ) -> LocatedSource:
        """Return exact file/stream/record provenance or fail without guessing."""

        evidence = self._evidence_by_object_id.get(id(record))
        if evidence is None:
            raise PcbSourceProvenanceError(
                "unresolved_source",
                "object is not indexed in the file-backed PcbDoc revision",
            )
        try:
            self._snapshot.verify_record(self._pcbdoc, id(record))
        except ValueError as exc:
            raise PcbSourceProvenanceError("mutated_source_document", str(exc)) from exc
        return _located_source(
            self.document_revision_sha256,
            self.logical_path,
            evidence,
            subrecord_index,
        )

    def assert_current(self) -> None:
        """Fail if any parsed source collection, order, or record state changed."""

        try:
            self._snapshot.verify_all(self._pcbdoc)
        except ValueError as exc:
            raise PcbSourceProvenanceError("mutated_source_document", str(exc)) from exc

    def _assert_current_pcbdoc(self, pcbdoc: AltiumPcbDoc) -> None:
        """Bind an internal replay operation to this index's parsed document."""

        if pcbdoc is not self._pcbdoc:
            raise PcbSourceProvenanceError(
                "foreign_source_identity",
                "source index does not belong to the supplied PcbDoc",
            )
        self.assert_current()

    def _contains_located_source(self, source: LocatedSource) -> bool:
        """Return whether an exact top-level locator belongs to this current index."""

        self.assert_current()
        if (
            source.document_revision_sha256 != self.document_revision_sha256
            or source.logical_path != self.logical_path
        ):
            return False
        return any(
            source
            == _located_source(
                self.document_revision_sha256,
                self.logical_path,
                evidence,
                None,
            )
            for evidence in self._evidence_by_object_id.values()
        )

    def resolve(
        self,
        record: object,
        *,
        strictness: Literal["strict", "permissive"],
        affected_ref: str,
        subrecord_index: int | None = None,
    ) -> PcbSourceResolution:
        """Resolve provenance, retaining geometry with a diagnostic in permissive mode."""

        if strictness not in {"strict", "permissive"}:
            raise ValueError(f"unknown manufacturing strictness: {strictness!r}")
        try:
            return PcbSourceResolution(
                source=self.source_for(record, subrecord_index=subrecord_index)
            )
        except PcbSourceProvenanceError as exc:
            if strictness == "strict" or exc.code != "unresolved_source":
                raise
        reason = "object is not indexed in the file-backed PcbDoc revision"
        diagnostic_id = _unresolved_diagnostic_id(affected_ref, reason)
        source = UnresolvedSource(diagnostic_ref=diagnostic_id, reason=reason)
        diagnostic = Diagnostic(
            id=diagnostic_id,
            code="unresolved_source",
            severity="warning",
            message="Source provenance is degraded; physical geometry is retained.",
            affected_ref=affected_ref,
        )
        return PcbSourceResolution(source=source, diagnostics=(diagnostic,))


def runtime_source(
    *,
    document_ref: str,
    object_ref: str,
    persistent_uid: str | None = None,
) -> RuntimeSource:
    """Create provenance for an object owned by a governed in-memory mutation API."""

    common = {
        "document_ref": _require_stable_ref(document_ref, "document_ref"),
        "object_ref": _require_stable_ref(object_ref, "object_ref"),
        "source_unit_nm_numerator": PCB_SOURCE_UNIT_NM_NUMERATOR,
        "source_unit_nm_denominator": PCB_SOURCE_UNIT_NM_DENOMINATOR,
    }
    if persistent_uid is None:
        return RuntimeSource(**common)
    return RuntimeSource(
        **common,
        persistent_uid=_require_stable_ref(persistent_uid, "persistent_uid"),
    )


def source_occurrence_ref(kind: str, source: SourceProvenance) -> str:
    """Build a deterministic occurrence ref from typed source evidence."""

    normalized_kind = _require_stable_ref(kind, "kind").lower()
    if isinstance(source, LocatedSource):
        parts = (
            "pcb.manufacturing.source.located",
            source.document_revision_sha256,
            source.logical_path,
            source.stream_name,
            str(source.record_index),
            _optional_source_text(source.subrecord_index),
            _optional_source_text(source.persistent_uid),
        )
    elif isinstance(source, RuntimeSource):
        parts = (
            "pcb.manufacturing.source.runtime",
            source.document_ref,
            source.object_ref,
            _optional_source_text(source.persistent_uid),
        )
    else:
        parts = (
            "pcb.manufacturing.source.unresolved",
            source.diagnostic_ref,
            source.reason,
        )
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{normalized_kind}.{digest}"


def _located_source(
    revision: str,
    logical_path: str,
    evidence: _RecordEvidence,
    subrecord_index: int | None,
) -> LocatedSource:
    if subrecord_index is not None and subrecord_index < 0:
        raise ValueError("subrecord_index must be nonnegative")
    common = {
        "document_revision_sha256": revision,
        "logical_path": logical_path,
        "stream_name": evidence.stream_name,
        "record_index": evidence.record_index,
        "source_unit_nm_numerator": PCB_SOURCE_UNIT_NM_NUMERATOR,
        "source_unit_nm_denominator": PCB_SOURCE_UNIT_NM_DENOMINATOR,
    }
    if evidence.persistent_uid is None and subrecord_index is None:
        return LocatedSource(**common)
    if evidence.persistent_uid is None:
        assert subrecord_index is not None
        return LocatedSource(**common, subrecord_index=subrecord_index)
    if subrecord_index is None:
        return LocatedSource(**common, persistent_uid=evidence.persistent_uid)
    return LocatedSource(
        **common,
        persistent_uid=evidence.persistent_uid,
        subrecord_index=subrecord_index,
    )


def _verify_requested_source_path(
    snapshot: PcbDocSourceRevisionSnapshot,
    source_path: Path | str | None,
) -> None:
    if source_path is None:
        return
    requested = Path(source_path).resolve()
    if snapshot.source_path is None or requested != snapshot.source_path:
        raise PcbSourceProvenanceError(
            "source_revision_mismatch",
            "source_path does not identify the bytes used to parse this PcbDoc",
        )


def _normalize_logical_path(value: str) -> str:
    raw = str(value)
    if not raw or "\x00" in raw:
        raise ValueError("logical_path must be nonempty and contain no NUL")
    portable = raw.replace("\\", "/")
    if portable.startswith("/") or re.match(r"^[A-Za-z]:", portable):
        raise ValueError("logical_path must be project-relative")
    normalized = str(PurePosixPath(portable))
    if normalized == "." or ".." in PurePosixPath(normalized).parts:
        raise ValueError("logical_path must identify a project-relative document")
    return normalized


def _require_sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("document_revision_sha256 must be lowercase SHA-256")
    return value


def _require_stable_ref(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be nonempty")
    return normalized


def _optional_source_text(value: object) -> str:
    return "" if value is UNSET else str(value)


def _unresolved_diagnostic_id(affected_ref: str, reason: str) -> str:
    stable_ref = _require_stable_ref(affected_ref, "affected_ref")
    suffix = hashlib.sha256(f"{stable_ref}\0{reason}".encode("utf-8")).hexdigest()[:20]
    return f"diagnostic.unresolved_source.{suffix}"


__all__ = (
    "PCB_SOURCE_UNIT_NM_DENOMINATOR",
    "PCB_SOURCE_UNIT_NM_NUMERATOR",
    "PcbDocSourceIndex",
    "PcbSourceProvenanceError",
    "PcbSourceResolution",
    "runtime_source",
    "source_occurrence_ref",
)
