"""Immutable project-variant state prepared for schematic component painting."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_netlist_common import _evaluate_altium_expression

if TYPE_CHECKING:
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_schdoc import AltiumSchDoc
    from .altium_schlib import AltiumSchLib, AltiumSymbol


@dataclass(frozen=True, slots=True)
class _ParameterRenderOverride:
    """Managed physical-display text plus its default variant-style gate."""

    text: str
    use_variant_style: bool
    apply_show_name: bool = False


@dataclass(frozen=True, slots=True)
class _ComponentProjectRenderState:
    """One physical component's already-selected project variation."""

    source: AltiumSchComponent
    variation_kind: int
    has_variant_component: bool
    alternate: AltiumSchComponent | None
    alternate_document: AltiumSchDoc | None
    show_alternate_symbols: bool
    parameter_overrides: Mapping[int, _ParameterRenderOverride]


class _ProjectVariantsLibraryIndex:
    """Load and index a project's generated variants SchLib at most once."""

    def __init__(
        self,
        project_path: Path | None,
        *,
        max_symbols: int = 100_000,
        max_identity_characters: int = 1_000_000,
    ) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (max_symbols, max_identity_characters)
        ):
            raise ValueError("variant library limits must be nonnegative integers")
        self._path = Path(str(project_path) + "Variants") if project_path else None
        self._max_symbols = max_symbols
        self._max_identity_characters = max_identity_characters
        self._loaded = False
        self._library: AltiumSchLib | None = None
        self._by_vault: dict[tuple[str, str, str], AltiumSymbol] = {}
        self._by_database: dict[tuple[str, str, str], AltiumSymbol] = {}

    def find(
        self, link: Mapping[str, object]
    ) -> tuple[AltiumSymbol, AltiumSchLib] | None:
        self._load()
        if self._library is None:
            return None
        key = _link_identity(link)
        symbol = (self._by_vault if key[0] else self._by_database).get(key[1])
        return (symbol, self._library) if symbol is not None else None

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self._path is None or not self._path.is_file():
            return
        from .altium_schlib import AltiumSchLib

        library = AltiumSchLib(self._path)
        if len(library.symbols) > self._max_symbols:
            raise ValueError("variant library symbol limit exceeded")
        remaining = self._max_identity_characters
        for symbol in library.symbols:
            record = symbol.component_record
            if not isinstance(record, dict):
                continue
            from .altium_record_sch__component import AltiumSchComponent

            component = AltiumSchComponent()
            component.parse_from_record(record, library.font_manager)
            vault_key = _component_identity(component, vault=True)
            database_key = _component_identity(component, vault=False)
            remaining -= sum(len(value) for value in (*vault_key, *database_key))
            if remaining < 0:
                raise ValueError("variant library identity character limit exceeded")
            self._by_vault.setdefault(vault_key, symbol)
            self._by_database.setdefault(database_key, symbol)
        self._library = library


def _link_value(link: Mapping[str, object], name: str) -> str:
    key = dotnet_ordinal_ignore_case_key(name)
    for candidate, value in link.items():
        if dotnet_ordinal_ignore_case_key(str(candidate)) == key:
            return str(value or "")
    return ""


def _component_identity(
    component: AltiumSchComponent, *, vault: bool
) -> tuple[str, str, str]:
    values = (
        (component.vault_guid, component.item_guid, component.revision_guid)
        if vault
        else (
            component.design_item_id,
            component.source_library_name,
            component.database_table_name,
        )
    )
    return (
        dotnet_ordinal_ignore_case_key(values[0] or ""),
        dotnet_ordinal_ignore_case_key(values[1] or ""),
        dotnet_ordinal_ignore_case_key(values[2] or ""),
    )


def _link_identity(
    link: Mapping[str, object],
) -> tuple[bool, tuple[str, str, str]]:
    vault = bool(_link_value(link, "VaultGUID"))
    names = (
        ("VaultGUID", "ItemGUID", "RevisionGUID")
        if vault
        else ("DesignItemID", "SourceLibraryName", "DatabaseTableName")
    )
    return vault, (
        dotnet_ordinal_ignore_case_key(_link_value(link, names[0])),
        dotnet_ordinal_ignore_case_key(_link_value(link, names[1])),
        dotnet_ordinal_ignore_case_key(_link_value(link, names[2])),
    )


def _source_designator(component: AltiumSchComponent) -> str:
    from .altium_record_sch__designator import AltiumSchDesignator

    for child in component.children or component.parameters:
        if isinstance(child, AltiumSchDesignator):
            return child.text
    return ""


def _component_from_variant_symbol(
    source: AltiumSchComponent,
    link: Mapping[str, object],
    library_index: _ProjectVariantsLibraryIndex,
) -> tuple[AltiumSchComponent, AltiumSchDoc] | None:
    from .altium_record_sch__component import AltiumSchComponent
    from .altium_sch_component_insert_helpers import (
        clone_symbol_children,
        merge_schlib_fonts,
    )
    from .altium_schdoc import AltiumSchDoc

    match = library_index.find(link)
    if match is None:
        return None
    symbol, library = match
    if symbol is None or not isinstance(symbol.component_record, dict):
        return None

    document = AltiumSchDoc()
    font_map = merge_schlib_fonts(document.font_manager, library)
    alternate = AltiumSchComponent()
    alternate.parse_from_record(symbol.component_record, document.font_manager)
    alternate.current_part_id = source.current_part_id
    alternate.display_field_names = source.display_field_names
    alternate.show_hidden_fields = source.show_hidden_fields
    alternate.display_mode = source.display_mode
    alternate.show_hidden_pins = source.show_hidden_pins
    alternate.orientation = source.orientation
    alternate.is_mirrored = source.is_mirrored
    alternate.location = source.location
    children = clone_symbol_children(
        symbol,
        alternate,
        designator=_source_designator(source),
        part_id=source.current_part_id,
        font_id_map=font_map,
    )
    document.add_object(alternate)
    for child in children.ordered_children:
        document.add_object(child, owner=alternate)
    return alternate, document


def _replicate_source_component(
    source: AltiumSchComponent,
) -> tuple[AltiumSchComponent, AltiumSchDoc]:
    from .altium_font_manager import FontIDManager
    from .altium_schdoc import AltiumSchDoc

    alternate = deepcopy(source)
    _detach_replicated_tree(alternate)
    document = AltiumSchDoc()
    context = getattr(source, "_bound_schematic_context", None)
    get_font_manager = getattr(context, "get_font_manager", None)
    if callable(get_font_manager) and isinstance(
        font_manager := get_font_manager(), FontIDManager
    ):
        document._font_manager = font_manager
    document.add_object(alternate)
    return alternate, document


def _detach_replicated_tree(root: object) -> None:
    pending = [root]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        if hasattr(current, "_bound_schematic_context"):
            setattr(current, "_bound_schematic_context", None)
        pending.extend(getattr(current, "children", ()) or ())


def _calculated_variant_value(varied: str, parameters: Mapping[str, str]) -> str:
    if not varied.startswith("="):
        return varied
    return _evaluate_altium_expression(varied[1:], dict(parameters))


def _parameter_project_overrides(
    source: AltiumSchComponent,
    alternate: AltiumSchComponent | None,
    variation: Mapping[str, object],
    component_parameters: Mapping[str, str],
    project_parameters: Mapping[str, str],
    *,
    is_dnp: bool,
) -> dict[int, _ParameterRenderOverride]:
    raw = variation.get("_parameters")
    varied = raw if isinstance(raw, dict) else {}
    effective = dict(project_parameters)
    effective.update(component_parameters)
    effective.update((str(name), str(value)) for name, value in varied.items())
    varied_by_name = {
        dotnet_ordinal_ignore_case_key(str(name)): str(value)
        for name, value in varied.items()
    }
    result = _parameter_owner_overrides(
        source,
        varied_by_name,
        effective,
        is_dnp=is_dnp,
        is_variant_parameter=False,
    )
    if alternate is not None:
        result.update(
            _parameter_owner_overrides(
                alternate,
                varied_by_name,
                effective,
                is_dnp=is_dnp,
                is_variant_parameter=True,
            )
        )
    return result


def _parameter_owner_overrides(
    owner: AltiumSchComponent,
    varied_by_name: Mapping[str, str],
    effective: Mapping[str, str],
    *,
    is_dnp: bool,
    is_variant_parameter: bool,
) -> dict[int, _ParameterRenderOverride]:
    from .altium_record_sch__parameter import AltiumSchParameter

    result: dict[int, _ParameterRenderOverride] = {}
    for parameter in owner.children or owner.parameters:
        if type(parameter) is not AltiumSchParameter:
            continue
        value = varied_by_name.get(dotnet_ordinal_ignore_case_key(parameter.name))
        if value is None:
            continue
        calculated = _calculated_variant_value(value, effective)
        changed = dotnet_ordinal_ignore_case_key(
            calculated
        ) != dotnet_ordinal_ignore_case_key(value) or dotnet_ordinal_ignore_case_key(
            parameter.text
        ) != dotnet_ordinal_ignore_case_key(value)
        if is_variant_parameter and value:
            result[id(parameter)] = _ParameterRenderOverride(
                value,
                changed and not is_dnp,
                apply_show_name=True,
            )
            continue
        if changed:
            result[id(parameter)] = _ParameterRenderOverride(calculated, not is_dnp)
    return result


def _variation_kind(variation: Mapping[str, object] | None) -> int:
    if variation is None:
        return 0
    key = dotnet_ordinal_ignore_case_key("Kind")
    raw = next(
        (
            value
            for name, value in reversed(tuple(variation.items()))
            if dotnet_ordinal_ignore_case_key(str(name)) == key
        ),
        0,
    )
    try:
        return int(str(raw))
    except ValueError:
        return 0


def _link_is_valid(link: Mapping[str, object]) -> bool:
    return bool(
        (_link_value(link, "LibraryIdentifier") and _link_value(link, "DesignItemID"))
        or (_link_value(link, "VaultGUID") and _link_value(link, "ItemGUID"))
    )


def _resolve_alternate_component(
    source: AltiumSchComponent,
    link: object,
    project_path: Path | None,
    library_index: _ProjectVariantsLibraryIndex | None,
) -> tuple[bool, tuple[AltiumSchComponent, AltiumSchDoc | None] | None]:
    if not isinstance(link, Mapping):
        return False, None
    if not _link_is_valid(link):
        return True, _replicate_source_component(source)
    if project_path is None:
        return False, None
    resolver = library_index or _ProjectVariantsLibraryIndex(project_path)
    alternate = _component_from_variant_symbol(source, link, resolver)
    return alternate is not None, alternate


def _prepare_component_project_render_state(
    source: AltiumSchComponent,
    variation: Mapping[str, object] | None,
    *,
    project_path: Path | None,
    component_parameters: Mapping[str, str],
    project_parameters: Mapping[str, str],
    show_alternate_symbols: bool,
    library_index: _ProjectVariantsLibraryIndex | None = None,
) -> _ComponentProjectRenderState:
    """Project the managed display update without mutating the source SchDoc."""
    kind = _variation_kind(variation)
    link = (variation or {}).get("_alternate_library_link")
    has_variant, alternate_pair = _resolve_alternate_component(
        source,
        link,
        project_path,
        library_index,
    )
    alternate, alternate_document = alternate_pair or (None, None)
    return _ComponentProjectRenderState(
        source=source,
        variation_kind=kind,
        has_variant_component=has_variant,
        alternate=alternate,
        alternate_document=alternate_document,
        show_alternate_symbols=show_alternate_symbols,
        parameter_overrides=_parameter_project_overrides(
            source,
            alternate,
            variation or {},
            component_parameters,
            project_parameters,
            is_dnp=kind == 1,
        ),
    )
