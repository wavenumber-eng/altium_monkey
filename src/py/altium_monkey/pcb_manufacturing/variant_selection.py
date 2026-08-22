"""Resolve explicit manufacturing variant selection and fitted decisions."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Literal

from altium_monkey.altium_prjpcb import (
    _altium_path_for_host,
    _decode_prjpcb_text,
)

from .generated import LocatedSource, SourceProvenance, UnresolvedSource
from .source_provenance import PcbDocSourceIndex, source_occurrence_ref

VariantSelectionKind = Literal["no_variations", "project_variant"]
_PROJECT_VARIANT_SECTION = re.compile(r"ProjectVariant[0-9]+")
_VARIATION_KEY = re.compile(r"Variation([0-9]+)")
_SOURCE_UNIQUE_ID = re.compile(r"(?:\\[A-Za-z0-9_]+)+")


@dataclass(frozen=True)
class PcbManufacturingVariantSelectionError(ValueError):
    """One stable failure while resolving fitted-state authority."""

    code: str
    detail: str


@dataclass(frozen=True)
class PcbComponentVariationDecision:
    """One exact ProjectVariant component decision keyed by source identity."""

    source_component_unique_id: str
    kind: Literal[0, 1]
    source: LocatedSource

    @property
    def fitted(self) -> bool:
        """Return the fitted state represented by the supported Altium kind."""

        return self.kind == 0


class PcbManufacturingVariantSelection:
    """Immutable output selection bound to one exact PcbDoc revision."""

    __slots__ = (
        "_board_logical_path",
        "_board_revision_sha256",
        "_decisions",
        "_display_name",
        "_id",
        "_kind",
        "_project_variant_unique_id",
        "_source",
    )
    _kind: VariantSelectionKind

    def __init__(
        self,
        *,
        construction_token: object,
        selection_id: str,
        source: SourceProvenance,
        kind: VariantSelectionKind,
        display_name: str,
        board_revision_sha256: str,
        board_logical_path: str,
        project_variant_unique_id: str | None,
        decisions: tuple[PcbComponentVariationDecision, ...],
    ) -> None:
        if construction_token is not _SELECTION_CONSTRUCTION_TOKEN:
            raise TypeError(
                "PcbManufacturingVariantSelection must be created by its resolver"
            )
        self._id = selection_id
        self._source = source
        self._kind = kind
        self._display_name = display_name
        self._board_revision_sha256 = board_revision_sha256
        self._board_logical_path = board_logical_path
        self._project_variant_unique_id = project_variant_unique_id
        self._decisions = decisions

    @property
    def id(self) -> str:
        return self._id

    @property
    def source(self) -> SourceProvenance:
        return self._source

    @property
    def kind(self) -> VariantSelectionKind:
        return self._kind

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def project_variant_unique_id(self) -> str | None:
        return self._project_variant_unique_id

    @property
    def decisions(self) -> tuple[PcbComponentVariationDecision, ...]:
        return self._decisions

    def assert_applies_to(self, source_index: PcbDocSourceIndex) -> None:
        """Fail unless this selection is bound to the current exact PcbDoc."""

        source_index.assert_current()
        if (
            source_index.document_revision_sha256 != self._board_revision_sha256
            or source_index.logical_path != self._board_logical_path
        ):
            raise PcbManufacturingVariantSelectionError(
                "variant_selection_board_mismatch",
                "variant selection is bound to a different PcbDoc revision",
            )

    def decision_for(
        self,
        source_component_unique_id: str,
    ) -> PcbComponentVariationDecision | None:
        """Return the exact named-variant row for one source component."""

        if self.kind == "no_variations":
            return None
        if not source_component_unique_id:
            raise PcbManufacturingVariantSelectionError(
                "unresolved_component_identity",
                "named variant selection requires component source_unique_id",
            )
        _require_source_unique_id(source_component_unique_id, "component")
        return next(
            (
                row
                for row in self._decisions
                if row.source_component_unique_id == source_component_unique_id
            ),
            None,
        )


_SELECTION_CONSTRUCTION_TOKEN = object()


def resolve_no_variations_selection(
    source_index: PcbDocSourceIndex,
    *,
    authority_source: SourceProvenance,
) -> PcbManufacturingVariantSelection:
    """Bind an explicit `[No Variations]` decision to one exact PcbDoc."""

    source_index.assert_current()
    if isinstance(authority_source, UnresolvedSource):
        raise PcbManufacturingVariantSelectionError(
            "unresolved_variant_authority",
            "[No Variations] requires located or governed runtime authority",
        )
    return PcbManufacturingVariantSelection(
        construction_token=_SELECTION_CONSTRUCTION_TOKEN,
        selection_id=source_occurrence_ref("variant_selection", authority_source),
        source=authority_source,
        kind="no_variations",
        display_name="[No Variations]",
        board_revision_sha256=source_index.document_revision_sha256,
        board_logical_path=source_index.logical_path,
        project_variant_unique_id=None,
        decisions=(),
    )


def resolve_project_variant_selection(
    source_index: PcbDocSourceIndex,
    *,
    project_path: Path | str,
    pcbdoc_path: Path | str,
    variant_name: str,
    project_logical_path: str | None = None,
) -> PcbManufacturingVariantSelection:
    """Resolve one named project variant with exact file and UniqueId evidence."""

    source_index.assert_current()
    project_file = Path(project_path).resolve()
    board_file = Path(pcbdoc_path).resolve()
    if not project_file.is_file():
        raise FileNotFoundError(f"project file not found: {project_file}")
    if not board_file.is_file():
        raise FileNotFoundError(f"PcbDoc file not found: {board_file}")
    project_bytes = project_file.read_bytes()
    config = _parse_project_config(project_bytes, project_file)
    _require_project_board_membership(config, project_file, board_file)
    if hashlib.sha256(board_file.read_bytes()).hexdigest() != (
        source_index.document_revision_sha256
    ):
        raise PcbManufacturingVariantSelectionError(
            "variant_selection_board_mismatch",
            "project PcbDoc bytes do not match the manufacturing source revision",
        )
    sections = _matching_variant_sections(config, variant_name)
    if len(sections) != 1:
        code = "unknown_project_variant" if not sections else "corrupt_identity"
        raise PcbManufacturingVariantSelectionError(
            code,
            f"expected one exact ProjectVariant named {variant_name!r}",
        )
    section = sections[0]
    variant_unique_id = _require_stable_text(
        config.get(section, "UniqueId", fallback=""),
        f"{section}.UniqueId",
    )
    _assert_unique_variant_authority(config)
    project_sha256 = hashlib.sha256(project_bytes).hexdigest()
    logical_path = project_logical_path or project_file.name
    selection_source = LocatedSource(
        document_revision_sha256=project_sha256,
        logical_path=logical_path,
        stream_name=section,
        record_index=0,
        persistent_uid=variant_unique_id,
    )
    decisions = _component_decisions(
        config,
        section=section,
        project_sha256=project_sha256,
        project_logical_path=logical_path,
    )
    return PcbManufacturingVariantSelection(
        construction_token=_SELECTION_CONSTRUCTION_TOKEN,
        selection_id=source_occurrence_ref("variant_selection", selection_source),
        source=selection_source,
        kind="project_variant",
        display_name=variant_name,
        board_revision_sha256=source_index.document_revision_sha256,
        board_logical_path=source_index.logical_path,
        project_variant_unique_id=variant_unique_id,
        decisions=decisions,
    )


def _parse_project_config(
    project_bytes: bytes,
    project_path: Path,
) -> configparser.ConfigParser:
    text, _encoding = _decode_prjpcb_text(project_bytes, project_path)
    config = _CaseSensitiveConfigParser(interpolation=None)
    try:
        config.read_string(text, source=str(project_path))
    except configparser.Error as exc:
        raise PcbManufacturingVariantSelectionError(
            "invalid_project_variant_authority",
            str(exc),
        ) from exc
    return config


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _require_project_board_membership(
    config: configparser.ConfigParser,
    project_path: Path,
    board_path: Path,
) -> None:
    candidates = [
        (project_path.parent / _altium_path_for_host(path)).resolve()
        for section in config.sections()
        if re.fullmatch(r"Document[0-9]+", section)
        for path in [config.get(section, "DocumentPath", fallback="")]
        if path.lower().endswith(".pcbdoc")
    ]
    matching_count = candidates.count(board_path)
    if matching_count == 0:
        raise PcbManufacturingVariantSelectionError(
            "variant_selection_board_mismatch",
            "PcbDoc is not an exact document member of the selected project",
        )
    if matching_count != 1:
        raise PcbManufacturingVariantSelectionError(
            "corrupt_identity",
            "project contains duplicate membership for the selected PcbDoc",
        )


def _matching_variant_sections(
    config: configparser.ConfigParser,
    variant_name: str,
) -> list[str]:
    clean_name = _require_stable_text(variant_name, "variant_name")
    if clean_name == "[No Variations]":
        raise PcbManufacturingVariantSelectionError(
            "invalid_project_variant_authority",
            "[No Variations] is an output sentinel, not a named ProjectVariant",
        )
    return [
        section
        for section in config.sections()
        if _PROJECT_VARIANT_SECTION.fullmatch(section)
        and config.get(section, "Description", fallback="") == clean_name
    ]


def _assert_unique_variant_authority(config: configparser.ConfigParser) -> None:
    names: set[str] = set()
    unique_ids: set[str] = set()
    for section in config.sections():
        if not _PROJECT_VARIANT_SECTION.fullmatch(section):
            continue
        name = _require_stable_text(
            config.get(section, "Description", fallback=""),
            f"{section}.Description",
        )
        unique_id = _require_stable_text(
            config.get(section, "UniqueId", fallback=""),
            f"{section}.UniqueId",
        )
        if name in names or unique_id in unique_ids:
            raise PcbManufacturingVariantSelectionError(
                "corrupt_identity",
                "project contains duplicate variant name or UniqueId authority",
            )
        names.add(name)
        unique_ids.add(unique_id)


def _component_decisions(
    config: configparser.ConfigParser,
    *,
    section: str,
    project_sha256: str,
    project_logical_path: str,
) -> tuple[PcbComponentVariationDecision, ...]:
    count_text = config.get(section, "VariationCount", fallback="")
    if not count_text.isdecimal():
        raise PcbManufacturingVariantSelectionError(
            "invalid_project_variant_authority",
            f"{section}.VariationCount is not a nonnegative integer",
        )
    count = int(count_text)
    observed_indices = {
        int(match.group(1))
        for key, _value in config.items(section)
        for match in [_VARIATION_KEY.fullmatch(key)]
        if match is not None
    }
    if observed_indices != set(range(1, count + 1)):
        raise PcbManufacturingVariantSelectionError(
            "invalid_project_variant_authority",
            f"{section} VariationCount does not match its rows",
        )
    decisions: list[PcbComponentVariationDecision] = []
    seen: set[str] = set()
    for index in range(1, count + 1):
        fields = _parse_variation_fields(config.get(section, f"Variation{index}"))
        unique_id = _require_source_unique_id(
            fields.get("UniqueId", ""),
            f"{section}.Variation{index}.UniqueId",
        )
        if unique_id in seen:
            raise PcbManufacturingVariantSelectionError(
                "corrupt_identity",
                f"{section} repeats component source UniqueId {unique_id!r}",
            )
        kind_text = fields.get("Kind", "")
        if kind_text not in {"0", "1"}:
            raise PcbManufacturingVariantSelectionError(
                "unsupported_variant_kind",
                f"{section}.Variation{index} has unsupported Kind={kind_text!r}",
            )
        seen.add(unique_id)
        decisions.append(
            PcbComponentVariationDecision(
                source_component_unique_id=unique_id,
                kind=0 if kind_text == "0" else 1,
                source=LocatedSource(
                    document_revision_sha256=project_sha256,
                    logical_path=project_logical_path,
                    stream_name=section,
                    record_index=index,
                    persistent_uid=unique_id,
                ),
            )
        )
    return tuple(decisions)


def _parse_variation_fields(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in value.split("|"):
        if "=" not in item:
            raise PcbManufacturingVariantSelectionError(
                "invalid_project_variant_authority",
                "variation row contains a field without '='",
            )
        key, field_value = item.split("=", 1)
        if not key or key in fields:
            raise PcbManufacturingVariantSelectionError(
                "invalid_project_variant_authority",
                "variation row contains an empty or duplicate field name",
            )
        fields[key] = field_value
    return fields


def _require_source_unique_id(value: str, field_name: str) -> str:
    if value != value.strip() or not _is_source_unique_id(value):
        raise PcbManufacturingVariantSelectionError(
            "corrupt_identity",
            f"{field_name} is not an exact hierarchical source UniqueId",
        )
    return value


def _is_source_unique_id(value: str) -> bool:
    return _SOURCE_UNIQUE_ID.fullmatch(value) is not None


def _require_stable_text(value: str, field_name: str) -> str:
    if not value or value != value.strip() or "\x00" in value:
        raise PcbManufacturingVariantSelectionError(
            "invalid_project_variant_authority",
            f"{field_name} must be nonempty, trimmed text",
        )
    return value


__all__ = (
    "PcbComponentVariationDecision",
    "PcbManufacturingVariantSelection",
    "PcbManufacturingVariantSelectionError",
    "VariantSelectionKind",
    "resolve_no_variations_selection",
    "resolve_project_variant_selection",
)
