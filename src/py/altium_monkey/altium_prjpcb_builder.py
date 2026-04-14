"""
Altium PrjPcb Builder - Programmatically Generate Project Files

This is the typed builder-oriented companion to `AltiumPrjPcb`.

Design goals:
- Keep the public bootstrap API narrow and explicit.
- Reuse the existing `AltiumPrjPcb` parser/writer for actual serialization.
- Preserve relative document paths instead of forcing filename-only behavior.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .altium_api_markers import public_api

from .altium_prjpcb import AltiumPrjPcb, NetIdentifierScope


def _normalize_project_document_path(path: Path | str) -> str:
    """
    Normalize a project document path to Altium's expected separator style.
    """
    return str(path).replace("/", "\\")


def _generate_document_unique_id() -> str:
    """
    Generate an Altium-style project document unique ID.
    """
    return str(uuid.uuid4()).replace("-", "").upper()[:8]


class AltiumPrjPcbDocumentKind(StrEnum):
    """
    Supported project document kinds for builder convenience helpers.
    """

    SCHDOC = ".SchDoc"
    PCDOC = ".PcbDoc"
    SCHLIB = ".SchLib"
    PCBLIB = ".PcbLib"
    OUTJOB = ".OutJob"


@dataclass(frozen=True, slots=True)
class AltiumPrjPcbDocumentEntry:
    """
    Typed project document entry.

    The `options` tuple preserves the full `DocumentN` payload shape when callers
    need to carry additional Altium-owned fields beyond the required path/ID pair.
    """

    path: str
    unique_id: str
    options: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @classmethod
    def create(
        cls,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbDocumentEntry:
        normalized_path = _normalize_project_document_path(path)
        resolved_unique_id = unique_id or _generate_document_unique_id()
        resolved_options = tuple(options or ())
        return cls(
            path=normalized_path,
            unique_id=resolved_unique_id,
            options=resolved_options,
        )


@public_api
class AltiumPrjPcbBuilder:
    """
    Builder for minimal project/bootstrap `.PrjPcb` files.
    """

    def __init__(
        self,
        name: str = "project",
        *,
        net_identifier_scope: NetIdentifierScope = NetIdentifierScope.AUTOMATIC,
        open_outputs: bool = True,
    ) -> None:
        self.name = name
        self.net_identifier_scope = net_identifier_scope
        self.open_outputs = open_outputs
        self.documents: list[AltiumPrjPcbDocumentEntry] = []

    def clear_documents(self) -> AltiumPrjPcbBuilder:
        """
        Remove all queued document entries from the builder.
        """
        self.documents.clear()
        return self

    def set_net_identifier_scope(
        self,
        scope: NetIdentifierScope | int,
    ) -> AltiumPrjPcbBuilder:
        """
        Set the project-wide net identifier scope written to `HierarchyMode`.
        """
        self.net_identifier_scope = NetIdentifierScope(int(scope))
        return self

    def add_document(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one document entry using the provided relative project path.
        """
        self.documents.append(
            AltiumPrjPcbDocumentEntry.create(
                path,
                unique_id=unique_id,
                options=options,
            )
        )
        return self

    def _add_typed_document(
        self,
        path: Path | str,
        *,
        kind: AltiumPrjPcbDocumentKind,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        suffix = Path(path).suffix.lower()
        if suffix != kind.value.lower():
            raise ValueError(f"Expected {kind.value} path, got: {path}")
        return self.add_document(path, unique_id=unique_id, options=options)

    def add_schdoc(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one `.SchDoc` entry.
        """
        return self._add_typed_document(
            path,
            kind=AltiumPrjPcbDocumentKind.SCHDOC,
            unique_id=unique_id,
            options=options,
        )

    def add_pcbdoc(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one `.PcbDoc` entry.
        """
        return self._add_typed_document(
            path,
            kind=AltiumPrjPcbDocumentKind.PCDOC,
            unique_id=unique_id,
            options=options,
        )

    def add_schlib(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one `.SchLib` entry.
        """
        return self._add_typed_document(
            path,
            kind=AltiumPrjPcbDocumentKind.SCHLIB,
            unique_id=unique_id,
            options=options,
        )

    def add_pcblib(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one `.PcbLib` entry.
        """
        return self._add_typed_document(
            path,
            kind=AltiumPrjPcbDocumentKind.PCBLIB,
            unique_id=unique_id,
            options=options,
        )

    def add_outjob(
        self,
        path: Path | str,
        *,
        unique_id: str | None = None,
        options: Sequence[tuple[str, str]] | None = None,
    ) -> AltiumPrjPcbBuilder:
        """
        Append one `.OutJob` entry.
        """
        return self._add_typed_document(
            path,
            kind=AltiumPrjPcbDocumentKind.OUTJOB,
            unique_id=unique_id,
            options=options,
        )

    def extend_documents(
        self,
        entries: Iterable[AltiumPrjPcbDocumentEntry],
    ) -> AltiumPrjPcbBuilder:
        """
        Append multiple pre-built document entries in order.
        """
        self.documents.extend(entries)
        return self

    def build(self) -> AltiumPrjPcb:
        """
        Materialize the queued builder state into an `AltiumPrjPcb`.
        """
        project = AltiumPrjPcb.create_minimal(self.name)
        project.config.set(
            "Design", "HierarchyMode", str(int(self.net_identifier_scope))
        )
        project.config.set("Design", "OpenOutputs", "1" if self.open_outputs else "0")
        project.documents = [
            {
                "path": entry.path,
                "unique_id": entry.unique_id,
                "options": list(entry.options)
                if entry.options
                else [
                    ("DocumentPath", entry.path),
                    ("DocumentUniqueId", entry.unique_id),
                ],
            }
            for entry in self.documents
        ]
        return project

    def save(self, filepath: Path | str) -> Path:
        """
        Build and save the project file, returning the output path.

        This is the canonical public write path for builder output.
        """
        output_path = Path(filepath)
        self.build().save(output_path)
        return output_path
