"""Resolve project harness definitions into ordered member paths."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .altium_dotnet_ordinal import (
    dotnet_ordinal_ignore_case_key,
    dotnet_ordinal_ignore_case_sort_key,
)
from .altium_netlist_single_sheet import _parse_bus_range


@dataclass(frozen=True, slots=True)
class HarnessDefinitionEntry:
    """One ordered scalar or nested harness-definition entry."""

    name: str
    nested_type: str = ""
    source_identity: str = ""


@dataclass(frozen=True, slots=True)
class HarnessDefinitionCandidate:
    """One connector- or file-backed definition candidate."""

    type_name: str
    entries: tuple[HarnessDefinitionEntry, ...]
    is_locked: bool = False
    source_identity: str = ""


@dataclass(frozen=True, slots=True)
class HarnessPathSegment:
    """One structured member value and its owning harness type."""

    value: str
    harness_type: str
    source_identity: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedHarnessMember:
    """One scalar or bus member selected from an unwound definition path."""

    path: tuple[HarnessPathSegment, ...]
    bus_signal_index: int | None = None
    signal_name: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedHarnessDefinition:
    """One merged project definition with deterministic unwound members."""

    type_name: str
    entries: tuple[HarnessDefinitionEntry, ...]
    members: tuple[ResolvedHarnessMember, ...]


def resolve_harness_definitions(
    candidates: Iterable[HarnessDefinitionCandidate],
    *,
    max_depth: int = 64,
    max_members_per_definition: int = 65_536,
    max_total_members: int = 1_000_000,
) -> dict[str, ResolvedHarnessDefinition]:
    """Merge and unwind project definitions using managed ordering rules."""
    if max_depth < 1 or max_members_per_definition < 1 or max_total_members < 1:
        raise ValueError("harness definition limits must be positive")
    merged = _merge_candidates(tuple(candidates))
    return _resolve_merged_definitions(
        merged,
        max_depth=max_depth,
        max_members_per_definition=max_members_per_definition,
        max_total_members=max_total_members,
    )


def resolve_project_harness_definitions(
    candidate_groups: Iterable[Iterable[HarnessDefinitionCandidate]],
    *,
    max_depth: int = 64,
    max_members_per_definition: int = 65_536,
    max_total_members: int = 1_000_000,
) -> dict[str, ResolvedHarnessDefinition]:
    """Merge per-document definitions in managed project-provider order."""
    if max_depth < 1 or max_members_per_definition < 1 or max_total_members < 1:
        raise ValueError("harness definition limits must be positive")
    merged = _merge_project_definition_groups(
        tuple(_merge_candidates(tuple(group)) for group in candidate_groups)
    )
    return _resolve_merged_definitions(
        merged,
        max_depth=max_depth,
        max_members_per_definition=max_members_per_definition,
        max_total_members=max_total_members,
    )


def _resolve_merged_definitions(
    merged: dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]],
    *,
    max_depth: int,
    max_members_per_definition: int,
    max_total_members: int,
) -> dict[str, ResolvedHarnessDefinition]:
    resolved: dict[str, ResolvedHarnessDefinition] = {}
    total_members = 0
    for key, (type_name, entries) in merged.items():
        members = _unwind_definition(
            key,
            merged,
            max_depth=max_depth,
            max_members=max_members_per_definition,
        )
        total_members += len(members)
        if total_members > max_total_members:
            raise ValueError("total harness definition member limit exceeded")
        resolved[key] = ResolvedHarnessDefinition(type_name, entries, members)
    return resolved


def _merge_project_definition_groups(
    groups: Sequence[dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]]],
) -> dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]]:
    project: dict[str, tuple[str, list[HarnessDefinitionEntry]]] = {}
    for group in groups:
        for key, (type_name, entries) in group.items():
            current = project.get(key)
            if current is None:
                current = (type_name, [])
                project[key] = current
            elif _dotnet_ordinal_sort_key(type_name) > _dotnet_ordinal_sort_key(
                current[0]
            ):
                current = (type_name, current[1])
                project[key] = current
            _merge_project_entries(key, current[1], entries, project)
    return {
        key: (type_name, tuple(entries))
        for key, (type_name, entries) in project.items()
    }


def _merge_project_entries(
    owner_key: str,
    target: list[HarnessDefinitionEntry],
    entries: Sequence[HarnessDefinitionEntry],
    project: dict[str, tuple[str, list[HarnessDefinitionEntry]]],
) -> None:
    for entry in entries:
        definition_key = dotnet_ordinal_ignore_case_key(_entry_definition(entry))
        if any(
            dotnet_ordinal_ignore_case_key(_entry_definition(current)) == definition_key
            for current in target
        ):
            _try_update_same_list_entry(target, entry)
            continue
        if entry.nested_type and _project_cross_reference_exists(
            owner_key,
            dotnet_ordinal_ignore_case_key(entry.nested_type),
            project,
            frozenset(),
        ):
            continue
        target.append(entry)


def _project_cross_reference_exists(
    owner_key: str,
    nested_key: str,
    project: dict[str, tuple[str, list[HarnessDefinitionEntry]]],
    seen: frozenset[str],
) -> bool:
    if owner_key == nested_key:
        return True
    if nested_key in seen:
        return False
    definition = project.get(nested_key)
    if definition is None:
        return False
    next_seen = seen | {nested_key}
    return any(
        entry.nested_type
        and _project_cross_reference_exists(
            owner_key,
            dotnet_ordinal_ignore_case_key(entry.nested_type),
            project,
            next_seen,
        )
        for entry in definition[1]
    )


def _merge_candidates(
    candidates: Sequence[HarnessDefinitionCandidate],
) -> dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]]:
    grouped: defaultdict[str, list[HarnessDefinitionCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.type_name:
            normalized = HarnessDefinitionCandidate(
                type_name=candidate.type_name,
                entries=_dedupe_candidate_entries(candidate.entries),
                is_locked=candidate.is_locked,
                source_identity=candidate.source_identity,
            )
            grouped[dotnet_ordinal_ignore_case_key(candidate.type_name)].append(
                normalized
            )
    result: dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]] = {}
    for key, group in grouped.items():
        locked = [candidate for candidate in group if candidate.is_locked]
        selected = tuple(locked or group)
        result[key] = _merge_candidate_group(selected)
    return result


def _merge_candidate_group(
    candidates: Sequence[HarnessDefinitionCandidate],
) -> tuple[str, tuple[HarnessDefinitionEntry, ...]]:
    type_name = max(
        (candidate.type_name for candidate in candidates),
        key=_dotnet_ordinal_sort_key,
    )
    if _candidate_entry_orders_match(candidates):
        entries = list(candidates[0].entries)
        for candidate in candidates[1:]:
            for entry in candidate.entries:
                _try_update_same_list_entry(entries, entry)
        return type_name, tuple(entries)
    return type_name, _merge_conflicting_entries(candidates)


def _dedupe_candidate_entries(
    entries: Sequence[HarnessDefinitionEntry],
) -> tuple[HarnessDefinitionEntry, ...]:
    rows: list[HarnessDefinitionEntry] = []
    seen: set[str] = set()
    for entry in entries:
        key = dotnet_ordinal_ignore_case_key(_entry_definition(entry))
        if not entry.name or key in seen:
            continue
        seen.add(key)
        rows.append(entry)
    return tuple(rows)


def _try_update_same_list_entry(
    entries: list[HarnessDefinitionEntry],
    candidate: HarnessDefinitionEntry,
) -> None:
    candidate_definition = _entry_definition(candidate)
    candidate_key = dotnet_ordinal_ignore_case_key(candidate_definition)
    for index, current in enumerate(entries):
        current_definition = _entry_definition(current)
        if dotnet_ordinal_ignore_case_key(current_definition) != candidate_key:
            continue
        if _dotnet_ordinal_sort_key(candidate_definition) > _dotnet_ordinal_sort_key(
            current_definition
        ):
            entries.pop(index)
            entries.append(candidate)
        return


def _merge_conflicting_entries(
    candidates: Sequence[HarnessDefinitionCandidate],
) -> tuple[HarnessDefinitionEntry, ...]:
    winners: dict[str, HarnessDefinitionEntry] = {}
    for candidate in candidates:
        for entry in candidate.entries:
            key = dotnet_ordinal_ignore_case_key(entry.name)
            current = winners.get(key)
            if current is None or _conflicting_entry_rank(
                entry
            ) > _conflicting_entry_rank(current):
                winners[key] = entry
    ordered = tuple(
        winners[key] for key in sorted(winners, key=dotnet_ordinal_ignore_case_sort_key)
    )
    return ordered


def _candidate_entry_orders_match(
    candidates: Sequence[HarnessDefinitionCandidate],
) -> bool:
    expected = tuple(
        (
            dotnet_ordinal_ignore_case_key(entry.name),
            dotnet_ordinal_ignore_case_key(entry.nested_type),
        )
        for entry in candidates[0].entries
    )
    return all(
        tuple(
            (
                dotnet_ordinal_ignore_case_key(entry.name),
                dotnet_ordinal_ignore_case_key(entry.nested_type),
            )
            for entry in candidate.entries
        )
        == expected
        for candidate in candidates[1:]
    )


def _entry_spelling_rank(entry: HarnessDefinitionEntry) -> bytes:
    return _dotnet_ordinal_sort_key(_entry_definition(entry))


def _entry_definition(entry: HarnessDefinitionEntry) -> str:
    if entry.nested_type:
        return f"{{{entry.name}:{entry.nested_type}}}"
    return entry.name


def _conflicting_entry_rank(
    entry: HarnessDefinitionEntry,
) -> tuple[bool, bytes, bytes]:
    return (
        bool(entry.nested_type),
        _dotnet_ordinal_sort_key(entry.nested_type),
        _dotnet_ordinal_sort_key(entry.name),
    )


def _unwind_definition(
    definition_key: str,
    definitions: dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]],
    *,
    max_depth: int,
    max_members: int,
) -> tuple[ResolvedHarnessMember, ...]:
    rows: list[ResolvedHarnessMember] = []
    _append_unwound_members(
        definition_key,
        definitions,
        path=(),
        active_types=frozenset(),
        rows=rows,
        max_depth=max_depth,
        max_members=max_members,
    )
    return tuple(rows)


def _append_unwound_members(
    definition_key: str,
    definitions: dict[str, tuple[str, tuple[HarnessDefinitionEntry, ...]]],
    *,
    path: tuple[HarnessPathSegment, ...],
    active_types: frozenset[str],
    rows: list[ResolvedHarnessMember],
    max_depth: int,
    max_members: int,
) -> None:
    if definition_key in active_types:
        return
    definition = definitions.get(definition_key)
    if definition is None:
        return
    type_name, entries = definition
    next_active = active_types | {definition_key}
    for entry in entries:
        next_path = (
            *path,
            HarnessPathSegment(entry.name, type_name, entry.source_identity),
        )
        if len(next_path) > max_depth:
            raise ValueError("harness definition depth limit exceeded")
        if entry.nested_type:
            _append_unwound_members(
                dotnet_ordinal_ignore_case_key(entry.nested_type),
                definitions,
                path=next_path,
                active_types=next_active,
                rows=rows,
                max_depth=max_depth,
                max_members=max_members,
            )
            continue
        _append_leaf_members(next_path, rows, max_members=max_members)


def _append_leaf_members(
    path: tuple[HarnessPathSegment, ...],
    rows: list[ResolvedHarnessMember],
    *,
    max_members: int,
) -> None:
    bus_range = _parse_bus_range(path[-1].value)
    if bus_range is None:
        _append_member(rows, ResolvedHarnessMember(path), max_members)
        return
    width = abs(bus_range.end - bus_range.start) + 1
    if width > max_members - len(rows):
        raise ValueError("harness definition member limit exceeded")
    step = 1 if bus_range.start <= bus_range.end else -1
    for signal_index, value in enumerate(
        range(bus_range.start, bus_range.end + step, step)
    ):
        _append_member(
            rows,
            ResolvedHarnessMember(
                path,
                signal_index,
                f"{bus_range.prefix}{value}",
            ),
            max_members,
        )


def _append_member(
    rows: list[ResolvedHarnessMember],
    member: ResolvedHarnessMember,
    max_members: int,
) -> None:
    if len(rows) >= max_members:
        raise ValueError("harness definition member limit exceeded")
    rows.append(member)


def _dotnet_ordinal_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")
