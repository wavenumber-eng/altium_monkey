"""Load explicitly authorized child PcbDocs for manufacturing resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import BinaryIO, Literal

from altium_monkey.altium_pcbdoc import AltiumPcbDoc
from altium_monkey.altium_prjpcb import AltiumPrjPcb


_PcbChildDocumentErrorCode = Literal[
    "invalid_child_path",
    "denied_child",
    "missing_child",
    "ambiguous_child",
    "changed_child_revision",
    "cyclic_child_reference",
    "child_resource_limit",
    "invalid_child_document",
]


@dataclass(frozen=True, slots=True)
class PcbChildRevisionIdentity:
    """Content-bound identity for one provider-authorized child document."""

    provider_id: str
    logical_path: str
    document_revision_sha256: str


@dataclass(frozen=True, slots=True)
class PcbChildDocumentLoad:
    """Fresh typed parse backed by an immutable cached child revision."""

    identity: PcbChildRevisionIdentity
    requested_document_path: str
    source_path: Path
    document: AltiumPcbDoc


@dataclass(frozen=True, slots=True)
class PcbChildDocumentProviderError(ValueError):
    """Stable child-provider failure before panel instance expansion."""

    code: _PcbChildDocumentErrorCode
    detail: str
    requested_document_path: str
    logical_path: str | None = None
    expected_revision_sha256: str | None = None
    observed_revision_sha256: str | None = None
    cycle: tuple[PcbChildRevisionIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class _AllowedChildDocument:
    logical_path: str
    source_path: Path


class PcbChildDocumentProvider:
    """Resolve child requests only through project entries or explicit mappings."""

    def __init__(
        self,
        *,
        provider_id: str,
        project: AltiumPrjPcb | None = None,
        allowed_documents: Mapping[str, Path] | None = None,
        allowed_roots: Sequence[Path] = (),
        max_depth: int = 16,
        max_documents: int = 256,
        max_document_bytes: int = 256 * 1024 * 1024,
        max_total_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        _validate_provider_configuration(
            provider_id=provider_id,
            max_depth=max_depth,
            max_documents=max_documents,
            max_document_bytes=max_document_bytes,
            max_total_bytes=max_total_bytes,
        )
        project_root = _project_root(project)
        self.provider_id = provider_id
        self.max_depth = max_depth
        self.max_documents = max_documents
        self.max_document_bytes = max_document_bytes
        self.max_total_bytes = max_total_bytes
        self._allowed_roots = _provider_roots(project_root, allowed_roots)
        self._documents: dict[str, list[_AllowedChildDocument]] = {}
        self._content_cache: dict[str, bytes] = {}
        self._logical_revisions: dict[str, str] = {}
        self._revision_identities: set[PcbChildRevisionIdentity] = set()
        self._total_cached_bytes = 0

        for logical_path, source_path in _project_pcbdoc_bindings(
            project,
            project_root,
        ):
            self._register_document(logical_path, source_path)
        for logical_path, source_path in (allowed_documents or {}).items():
            self._register_document(logical_path, source_path)

    def load(
        self,
        *,
        owner_logical_path: str,
        requested_document_path: str,
        expected_revision_sha256: str | None = None,
        active_revisions: Sequence[PcbChildRevisionIdentity] = (),
    ) -> PcbChildDocumentLoad:
        """Load one exact authorized child revision without expanding instances."""

        _validate_expected_revision(expected_revision_sha256)
        logical_path = _resolve_requested_logical_path(
            owner_logical_path,
            requested_document_path,
        )
        key = logical_path.casefold()
        candidates = self._documents.get(key, ())
        if not candidates:
            raise _provider_error(
                "denied_child",
                "requested child is not declared by the project or explicit allowlist",
                requested_document_path,
                logical_path,
            )
        if len(candidates) != 1:
            raise _provider_error(
                "ambiguous_child",
                "requested child has multiple authorized path bindings",
                requested_document_path,
                logical_path,
            )
        candidate = candidates[0]
        payload = self._read_candidate(candidate, requested_document_path)
        revision_sha256 = hashlib.sha256(payload).hexdigest()
        self._assert_revision(
            key,
            revision_sha256,
            expected_revision_sha256=expected_revision_sha256,
            requested_document_path=requested_document_path,
            logical_path=logical_path,
        )
        identity = PcbChildRevisionIdentity(
            provider_id=self.provider_id,
            logical_path=candidate.logical_path,
            document_revision_sha256=revision_sha256,
        )
        self._assert_expansion_bounds(
            identity,
            active_revisions=active_revisions,
            requested_document_path=requested_document_path,
        )
        self._assert_cache_capacity(identity, payload, requested_document_path)
        cached_payload = self._content_cache.get(revision_sha256)
        if cached_payload is not None and cached_payload != payload:
            raise _provider_error(
                "changed_child_revision",
                "content hash collision produced nonidentical child bytes",
                requested_document_path,
                logical_path,
            )
        parse_payload = payload if cached_payload is None else cached_payload
        try:
            document = AltiumPcbDoc.from_bytes(
                parse_payload,
                filename=candidate.source_path,
            )
        except Exception as exc:
            raise _provider_error(
                "invalid_child_document",
                "authorized child bytes are not a parseable PcbDoc",
                requested_document_path,
                candidate.logical_path,
            ) from exc
        self._cache_revision(identity, payload, requested_document_path)
        self._logical_revisions[key] = revision_sha256
        self._revision_identities.add(identity)
        return PcbChildDocumentLoad(
            identity=identity,
            requested_document_path=requested_document_path,
            source_path=candidate.source_path,
            document=document,
        )

    def _register_document(self, logical_path: str, source_path: Path) -> None:
        normalized = _normalize_declared_logical_path(logical_path)
        resolved_source = source_path.resolve(strict=False)
        if not _path_is_within_any_root(resolved_source, self._allowed_roots):
            raise ValueError(
                f"authorized child path is outside allowed roots: {source_path}"
            )
        self._documents.setdefault(normalized.casefold(), []).append(
            _AllowedChildDocument(normalized, resolved_source)
        )

    def _read_candidate(
        self,
        candidate: _AllowedChildDocument,
        requested_document_path: str,
    ) -> bytes:
        try:
            with _open_binary(candidate.source_path) as stream:
                opened_stat = _assert_open_file_is_authorized(
                    stream,
                    candidate.source_path,
                    self._allowed_roots,
                    requested_document_path,
                    candidate.logical_path,
                )
                payload = stream.read(self.max_document_bytes + 1)
                closed_stat = os.fstat(stream.fileno())
        except PcbChildDocumentProviderError:
            raise
        except FileNotFoundError as exc:
            raise _provider_error(
                "missing_child",
                "authorized child file is missing",
                requested_document_path,
                candidate.logical_path,
            ) from exc
        except PermissionError as exc:
            raise _provider_error(
                "denied_child",
                "authorized child file cannot be read",
                requested_document_path,
                candidate.logical_path,
            ) from exc
        except OSError as exc:
            raise _provider_error(
                "denied_child",
                "authorized child file cannot be read",
                requested_document_path,
                candidate.logical_path,
            ) from exc
        if _file_changed_during_read(opened_stat, closed_stat):
            raise _provider_error(
                "changed_child_revision",
                "authorized child changed while its revision was being read",
                requested_document_path,
                candidate.logical_path,
            )
        if len(payload) > self.max_document_bytes:
            raise _provider_error(
                "child_resource_limit",
                "authorized child exceeds the per-document byte limit",
                requested_document_path,
                candidate.logical_path,
            )
        return payload

    def _assert_revision(
        self,
        key: str,
        actual_sha256: str,
        *,
        expected_revision_sha256: str | None,
        requested_document_path: str,
        logical_path: str,
    ) -> None:
        if (
            expected_revision_sha256 is not None
            and actual_sha256 != expected_revision_sha256
        ):
            raise _provider_error(
                "changed_child_revision",
                "authorized child bytes do not match the requested revision",
                requested_document_path,
                logical_path,
                expected_revision_sha256=expected_revision_sha256,
                observed_revision_sha256=actual_sha256,
            )
        pinned_sha256 = self._logical_revisions.get(key)
        if pinned_sha256 is not None and actual_sha256 != pinned_sha256:
            raise _provider_error(
                "changed_child_revision",
                "authorized child bytes changed after the logical path was pinned",
                requested_document_path,
                logical_path,
                expected_revision_sha256=pinned_sha256,
                observed_revision_sha256=actual_sha256,
            )

    def _assert_expansion_bounds(
        self,
        identity: PcbChildRevisionIdentity,
        *,
        active_revisions: Sequence[PcbChildRevisionIdentity],
        requested_document_path: str,
    ) -> None:
        if identity in active_revisions:
            cycle_start = active_revisions.index(identity)
            cycle = (*active_revisions[cycle_start:], identity)
            raise PcbChildDocumentProviderError(
                code="cyclic_child_reference",
                detail="child revision is already active in the expansion stack",
                requested_document_path=requested_document_path,
                logical_path=identity.logical_path,
                cycle=cycle,
            )
        if len(active_revisions) >= self.max_depth:
            raise _provider_error(
                "child_resource_limit",
                "child expansion exceeds the configured depth limit",
                requested_document_path,
                identity.logical_path,
            )

    def _assert_cache_capacity(
        self,
        identity: PcbChildRevisionIdentity,
        payload: bytes,
        requested_document_path: str,
    ) -> None:
        if identity not in self._revision_identities and (
            len(self._revision_identities) >= self.max_documents
        ):
            raise _provider_error(
                "child_resource_limit",
                "child provider exceeds the configured document limit",
                requested_document_path,
                identity.logical_path,
            )
        digest = identity.document_revision_sha256
        if digest in self._content_cache:
            return
        if self._total_cached_bytes + len(payload) > self.max_total_bytes:
            raise _provider_error(
                "child_resource_limit",
                "child provider exceeds the configured total byte limit",
                requested_document_path,
                identity.logical_path,
            )

    def _cache_revision(
        self,
        identity: PcbChildRevisionIdentity,
        payload: bytes,
        requested_document_path: str,
    ) -> None:
        self._assert_cache_capacity(identity, payload, requested_document_path)
        digest = identity.document_revision_sha256
        if digest in self._content_cache:
            return
        self._content_cache[digest] = payload
        self._total_cached_bytes += len(payload)


def _project_root(project: AltiumPrjPcb | None) -> Path | None:
    if project is None:
        return None
    if project.filepath is None:
        raise ValueError("project must have filepath context")
    return project.filepath.resolve(strict=False).parent


def _validate_provider_configuration(
    *,
    provider_id: str,
    max_depth: int,
    max_documents: int,
    max_document_bytes: int,
    max_total_bytes: int,
) -> None:
    if not provider_id or "\x00" in provider_id:
        raise ValueError("provider_id must be nonempty and contain no NUL")
    _require_positive_limit("max_depth", max_depth)
    _require_positive_limit("max_documents", max_documents)
    _require_positive_limit("max_document_bytes", max_document_bytes)
    _require_positive_limit("max_total_bytes", max_total_bytes)


def _provider_roots(
    project_root: Path | None,
    allowed_roots: Sequence[Path],
) -> tuple[Path, ...]:
    root_candidates = (
        *(() if project_root is None else (project_root,)),
        *allowed_roots,
    )
    return tuple(dict.fromkeys(root.resolve(strict=False) for root in root_candidates))


def _project_pcbdoc_bindings(
    project: AltiumPrjPcb | None,
    project_root: Path | None,
) -> tuple[tuple[str, Path], ...]:
    if project is None:
        return ()
    if project_root is None:
        raise ValueError("project must have filepath context")
    return tuple(
        (logical_path, project_root / _host_path(logical_path))
        for document in project.documents
        if (logical_path := str(document["path"])).casefold().endswith(".pcbdoc")
    )


def _require_positive_limit(name: str, value: int) -> None:
    if value < 1:
        raise ValueError(f"{name} must be positive")


def _validate_expected_revision(expected_revision_sha256: str | None) -> None:
    if expected_revision_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_revision_sha256
    ):
        raise ValueError("expected_revision_sha256 must be lowercase SHA-256")


def _host_path(logical_path: str) -> Path:
    return Path(logical_path.replace("\\", "/"))


def _normalize_declared_logical_path(logical_path: str) -> str:
    return _normalize_logical_path((), logical_path)


def _resolve_requested_logical_path(
    owner_logical_path: str,
    requested_document_path: str,
) -> str:
    owner = _normalize_declared_logical_path(owner_logical_path)
    owner_parts = tuple(owner.split("/"))[:-1]
    return _normalize_logical_path(owner_parts, requested_document_path)


def _normalize_logical_path(base_parts: Sequence[str], value: str) -> str:
    text = value.replace("\\", "/")
    if (
        not text
        or "\x00" in text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
    ):
        raise PcbChildDocumentProviderError(
            code="invalid_child_path",
            detail="child path must be a nonempty relative Altium path",
            requested_document_path=value,
        )
    parts = list(base_parts)
    for part in text.split("/"):
        _append_logical_path_part(parts, part)
    if not parts:
        raise PcbChildDocumentProviderError(
            code="invalid_child_path",
            detail="child path does not identify a document",
            requested_document_path=value,
        )
    return "/".join(parts)


def _append_logical_path_part(parts: list[str], part: str) -> None:
    if part in {"", "."}:
        return
    if part != "..":
        parts.append(part)
        return
    if parts and parts[-1] != "..":
        parts.pop()
        return
    parts.append(part)


def _path_is_within_any_root(path: Path, roots: Sequence[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _open_binary(path: Path) -> BinaryIO:
    return path.open("rb")


def _assert_open_file_is_authorized(
    stream: BinaryIO,
    source_path: Path,
    allowed_roots: Sequence[Path],
    requested_document_path: str,
    logical_path: str,
) -> os.stat_result:
    resolved_target = source_path.resolve(strict=True)
    opened_stat = os.fstat(stream.fileno())
    target_stat = resolved_target.stat()
    if not _path_is_within_any_root(resolved_target, allowed_roots):
        raise _provider_error(
            "denied_child",
            "authorized child resolved outside the physical root policy",
            requested_document_path,
            logical_path,
        )
    if not os.path.samestat(opened_stat, target_stat):
        raise _provider_error(
            "denied_child",
            "authorized child path changed while its file was being opened",
            requested_document_path,
            logical_path,
        )
    return opened_stat


def _file_changed_during_read(
    opened_stat: os.stat_result,
    closed_stat: os.stat_result,
) -> bool:
    return (
        opened_stat.st_size != closed_stat.st_size
        or opened_stat.st_mtime_ns != closed_stat.st_mtime_ns
        or opened_stat.st_ctime_ns != closed_stat.st_ctime_ns
    )


def _provider_error(
    code: _PcbChildDocumentErrorCode,
    detail: str,
    requested_document_path: str,
    logical_path: str | None,
    *,
    expected_revision_sha256: str | None = None,
    observed_revision_sha256: str | None = None,
) -> PcbChildDocumentProviderError:
    return PcbChildDocumentProviderError(
        code=code,
        detail=detail,
        requested_document_path=requested_document_path,
        logical_path=logical_path,
        expected_revision_sha256=expected_revision_sha256,
        observed_revision_sha256=observed_revision_sha256,
    )


__all__ = (
    "PcbChildDocumentLoad",
    "PcbChildDocumentProvider",
    "PcbChildDocumentProviderError",
    "PcbChildRevisionIdentity",
)
