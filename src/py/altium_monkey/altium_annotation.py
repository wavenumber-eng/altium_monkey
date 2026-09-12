"""Parse Altium project .Annotation files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .altium_compiled_design_model import AltiumCompileDiagnostic


@dataclass(frozen=True, slots=True)
class DesignatorAnnotation:
    """One physical designator annotation record."""

    logical_designator: str
    logical_part_id: int
    document_name: str
    channel_name: str
    unique_id_path: str
    physical_designator: str
    locked: bool
    source_index: int


@dataclass(frozen=True, slots=True)
class SheetNumberAnnotation:
    """One physical sheet-number annotation record."""

    document_name: str
    unique_id_path: str
    sheet_number: str
    source_index: int


@dataclass(frozen=True, slots=True)
class NetNameAnnotation:
    """One net-name override annotation record."""

    original_net_name: str
    override_net_name: str
    source_index: int


@dataclass(frozen=True, slots=True)
class AnnotationFile:
    """Parsed project annotation file."""

    status: str
    path: str | None
    designators: tuple[DesignatorAnnotation, ...] = ()
    sheet_numbers: tuple[SheetNumberAnnotation, ...] = ()
    net_names: tuple[NetNameAnnotation, ...] = ()
    sheet_number_order: str = ""
    sheet_number_method: str = ""
    diagnostics: tuple[AltiumCompileDiagnostic, ...] = ()


_SECTION_DESIGNATORS = "designatormanager"
_SECTION_SHEET_NUMBERS = "sheetnumbermanager"
_SECTION_NET_NAMES = "netnamemanager"

_DESIGNATOR_FIELDS = {
    "logicaldesignator": "logical_designator",
    "logicalpartid": "logical_part_id",
    "documentname": "document_name",
    "channelname": "channel_name",
    "uniqueid": "unique_id_path",
    "physicaldesignatorlocked": "locked",
    "physicaldesignator": "physical_designator",
}
_SHEET_NUMBER_FIELDS = {
    "documentname": "document_name",
    "uniqueidpath": "unique_id_path",
    "sheetnumber": "sheet_number",
}
_NET_NAME_FIELDS = {
    "originalnetname": "original_net_name",
    "overridenetname": "override_net_name",
}


def _read_annotation_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp1252")


def _section_kind(header_text: str) -> str | None:
    normalized = header_text.strip().lower()
    if normalized.startswith(_SECTION_DESIGNATORS):
        return _SECTION_DESIGNATORS
    if normalized.startswith(_SECTION_SHEET_NUMBERS):
        return _SECTION_SHEET_NUMBERS
    if normalized.startswith(_SECTION_NET_NAMES):
        return _SECTION_NET_NAMES
    return None


def _indexed_field(
    key: str,
    field_map: dict[str, str],
) -> tuple[str, int] | None:
    normalized = key.strip().lower()
    for prefix, field_name in sorted(
        field_map.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if not normalized.startswith(prefix):
            continue
        index_text = normalized[len(prefix) :]
        if index_text.isdecimal():
            return field_name, int(index_text)
    return None


def _parse_int(value: str, default: int = 1) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_annotation_text(
    text: str,
    *,
    path: str | None = None,
) -> AnnotationFile:
    """Parse annotation text with modern gap-tolerant read semantics."""
    current_section: str | None = None
    designator_rows: dict[int, dict[str, str]] = {}
    sheet_number_rows: dict[int, dict[str, str]] = {}
    net_name_rows: dict[int, dict[str, str]] = {}
    sheet_number_order = ""
    sheet_number_method = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and "]" in line:
            current_section = _section_kind(line[1 : line.index("]")])
            continue
        if current_section is None or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if current_section == _SECTION_DESIGNATORS:
            field = _indexed_field(key, _DESIGNATOR_FIELDS)
            if field is not None:
                field_name, index = field
                designator_rows.setdefault(index, {})[field_name] = value
        elif current_section == _SECTION_SHEET_NUMBERS:
            field = _indexed_field(key, _SHEET_NUMBER_FIELDS)
            if field is not None:
                field_name, index = field
                sheet_number_rows.setdefault(index, {})[field_name] = value
            elif key.lower() == "sheetnumberorder":
                sheet_number_order = value
            elif key.lower() == "sheetnumbermethod":
                sheet_number_method = value
        elif current_section == _SECTION_NET_NAMES:
            field = _indexed_field(key, _NET_NAME_FIELDS)
            if field is not None:
                field_name, index = field
                net_name_rows.setdefault(index, {})[field_name] = value

    designators = tuple(
        DesignatorAnnotation(
            logical_designator=row.get("logical_designator", ""),
            logical_part_id=_parse_int(row.get("logical_part_id", "1")),
            document_name=row.get("document_name", ""),
            channel_name=row.get("channel_name", ""),
            unique_id_path=row.get("unique_id_path", ""),
            physical_designator=row.get("physical_designator", ""),
            locked=_parse_bool(row.get("locked", "")),
            source_index=index,
        )
        for index, row in sorted(designator_rows.items())
        if row.get("unique_id_path", "")
    )
    sheet_numbers = tuple(
        SheetNumberAnnotation(
            document_name=row.get("document_name", ""),
            unique_id_path=row.get("unique_id_path", ""),
            sheet_number=row.get("sheet_number", ""),
            source_index=index,
        )
        for index, row in sorted(sheet_number_rows.items())
        if row.get("document_name", "") or row.get("unique_id_path", "")
    )
    net_names = tuple(
        NetNameAnnotation(
            original_net_name=row.get("original_net_name", ""),
            override_net_name=row.get("override_net_name", ""),
            source_index=index,
        )
        for index, row in sorted(net_name_rows.items())
        if row.get("original_net_name", "") and "override_net_name" in row
    )
    return AnnotationFile(
        status="loaded",
        path=path,
        designators=designators,
        sheet_numbers=sheet_numbers,
        net_names=net_names,
        sheet_number_order=sheet_number_order,
        sheet_number_method=sheet_number_method,
    )


def load_project_annotation(project_path: Path | None) -> AnnotationFile:
    """Load a project sibling .Annotation file, or return an absent state."""
    if project_path is None:
        return AnnotationFile(status="absent", path=None)
    annotation_path = project_path.with_suffix(".Annotation")
    if not annotation_path.exists():
        return AnnotationFile(status="absent", path=str(annotation_path))
    try:
        return parse_annotation_text(
            _read_annotation_text(annotation_path),
            path=str(annotation_path),
        )
    except OSError as exc:
        diagnostic = AltiumCompileDiagnostic(
            severity="error",
            code="annotation_parse_error",
            message=f"Could not read .Annotation file: {exc}",
            source_id=str(annotation_path),
        )
        return AnnotationFile(
            status="error",
            path=str(annotation_path),
            diagnostics=(diagnostic,),
        )


__all__ = [
    "AnnotationFile",
    "DesignatorAnnotation",
    "NetNameAnnotation",
    "SheetNumberAnnotation",
    "load_project_annotation",
    "parse_annotation_text",
]
