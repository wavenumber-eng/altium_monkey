"""
Altium SchLib Merger

Merges multiple SchLib files into a single multi-symbol SchLib file.

Uses the object model directly. Font tables are preserved via
FontIDManager, and embedded images are copied with their symbol data.
"""

import logging
from copy import deepcopy
from pathlib import Path

from .altium_schlib import (
    AltiumSchLib,
    AltiumSymbol,
    _copy_symbol_storage_dialect,
)

log = logging.getLogger(__name__)

_IMPLEMENTATION_LIST_RECORD = "44"
_IMPLEMENTATION_RECORD = "45"
_IMPLEMENTATION_CHILD_RECORDS = {"46", "47", "48"}


def _record_type(record: dict[str, object]) -> str:
    return str(record.get("RECORD", ""))


def _normalize_merge_raw_records(
    records: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    Normalize orphan implementation children the way Altium's merge exporter does.

    Some legacy single-symbol SchLib inputs contain a trailing implementation
    child record without a corresponding Implementation List/Implementation
    group. Altium's merge output replaces that orphan tail with an
    Implementation List marker.
    """
    has_implementation_list = any(
        _record_type(record) == _IMPLEMENTATION_LIST_RECORD for record in records
    )
    has_implementation = any(
        _record_type(record) == _IMPLEMENTATION_RECORD for record in records
    )
    has_orphan_child = any(
        _record_type(record) in _IMPLEMENTATION_CHILD_RECORDS for record in records
    )
    if has_implementation_list or has_implementation or not has_orphan_child:
        return records

    normalized = [
        record
        for record in records
        if _record_type(record) not in _IMPLEMENTATION_CHILD_RECORDS
    ]
    normalized.append({"RECORD": _IMPLEMENTATION_LIST_RECORD})
    return normalized


def _resolve_storage_name(
    name: str,
    seen_names: set[str],
    conflict_policy: str,
) -> str | None:
    folded = name.casefold()
    if folded not in seen_names:
        seen_names.add(folded)
        return name
    if conflict_policy == "skip":
        return None
    if conflict_policy == "error":
        raise ValueError(f"Duplicate symbol name: {name}")

    suffix = 1
    candidate = f"{name}_{suffix}"
    while candidate.casefold() in seen_names:
        suffix += 1
        candidate = f"{name}_{suffix}"
    seen_names.add(candidate.casefold())
    return candidate


def _resolve_original_name(
    original_name: str,
    storage_name: str,
    seen_librefs: set[str],
) -> str:
    candidate = original_name
    suffix = 1
    while candidate.casefold() in seen_librefs:
        candidate = storage_name if suffix == 1 else f"{storage_name}_{suffix - 1}"
        suffix += 1
    seen_librefs.add(candidate.casefold())
    return candidate


def _copy_symbol(
    merged: AltiumSchLib,
    source: AltiumSchLib,
    symbol: AltiumSymbol,
    storage_name: str,
    original_name: str,
) -> None:
    new_symbol = merged.add_symbol(
        storage_name,
        symbol.description,
        original_name=original_name,
    )
    new_symbol.part_count = symbol.part_count
    new_symbol.component_record = deepcopy(symbol.component_record)
    new_symbol._copy_objects_from(symbol)
    new_symbol.raw_records = _normalize_merge_raw_records(deepcopy(symbol.raw_records))
    new_symbol._additional_raw_records = deepcopy(symbol._additional_raw_records)
    new_symbol._additional_terminal_record = deepcopy(
        symbol._additional_terminal_record
    )
    _copy_symbol_storage_dialect(symbol, new_symbol)
    if original_name != str(symbol.original_name or symbol.name):
        new_symbol._uses_implicit_storage_mapping = False
        new_symbol._header_display_name = None
        new_symbol._set_component_identity(original_name)
    new_symbol._original_streams = dict(symbol._original_streams)

    for image in symbol.images:
        filename = getattr(image, "filename", None)
        if filename and filename in source.embedded_images:
            merged.embedded_images[filename] = source.embedded_images[filename]


def _load_merge_source(path: Path) -> AltiumSchLib | None:
    if not path.exists():
        log.warning(f"Skipping missing file: {path}")
        return None
    try:
        return AltiumSchLib(path)
    except Exception as exc:
        log.error(f"Failed to parse {path.name}: {exc}")
        return None


def _has_additional_stream(library: AltiumSchLib) -> bool:
    return any(
        stream_name.casefold() == "additional"
        for symbol in library.symbols
        for stream_name in symbol._original_streams
    )


def _validate_additional_announcement_merge(
    merged: AltiumSchLib,
    source: AltiumSchLib,
) -> None:
    merged_has_header = merged._lib_additional_header is not None
    source_has_header = source._lib_additional_header is not None
    merged_has_opaque = not merged_has_header and _has_additional_stream(merged)
    source_has_opaque = not source_has_header and _has_additional_stream(source)
    if (merged_has_header and source_has_opaque) or (
        source_has_header and merged_has_opaque
    ):
        raise ValueError(
            "cannot merge announced and unannounced SchLib Additional streams"
        )


def _merge_source(
    merged: AltiumSchLib,
    source: AltiumSchLib,
    seen_names: set[str],
    seen_librefs: set[str],
    conflict_policy: str,
    verbose: bool,
) -> None:
    _validate_additional_announcement_merge(merged, source)
    if merged.font_manager is None and source.font_manager:
        merged.font_manager = source.font_manager
    if merged._lib_additional_header is None and source._lib_additional_header:
        merged._lib_additional_header = deepcopy(source._lib_additional_header)

    for symbol in source.symbols:
        storage_name = _resolve_storage_name(
            symbol.name,
            seen_names,
            conflict_policy,
        )
        if storage_name is None:
            if verbose:
                log.info(f"  SKIP: {symbol.name} (duplicate)")
            continue
        if verbose and storage_name != symbol.name:
            log.info(f"  Renamed: {symbol.name} -> {storage_name}")
        original_name = _resolve_original_name(
            str(symbol.original_name or symbol.name),
            storage_name,
            seen_librefs,
        )
        _copy_symbol(merged, source, symbol, storage_name, original_name)


def merge_schlibs(
    input_paths: list[Path],
    output_path: Path,
    *,
    handle_conflicts: str = "rename",
    verbose: bool = True,
) -> bool:
    """
    Merge multiple SchLib files into one.

        Args:
            input_paths: List of SchLib file paths.
            output_path: Output merged SchLib path.
            handle_conflicts: "rename" (append suffix), "skip", or "error".
            verbose: Print progress.

        Returns:
            True if successful.
    """
    output_path = Path(output_path)

    if verbose:
        log.info(f"Merging {len(input_paths)} SchLib files -> {output_path.name}")

    merged = AltiumSchLib()
    seen_names: set[str] = set()
    seen_librefs: set[str] = set()

    for path in input_paths:
        path = Path(path)
        source = _load_merge_source(path)
        if source is None:
            continue
        _merge_source(
            merged,
            source,
            seen_names,
            seen_librefs,
            handle_conflicts,
            verbose,
        )

        if verbose:
            log.info(f"  Added {len(source.symbols)} symbols from {path.name}")

    merged._weight_policy = "serialized_data_records"
    merged.save(output_path, sync_pin_text_data=True)

    if verbose:
        log.info(f"Merge complete: {len(merged.symbols)} symbols -> {output_path.name}")

    return True


def merge_directory(
    input_dir: Path,
    output_path: Path,
    *,
    pattern: str = "*.SchLib",
    handle_conflicts: str = "rename",
    verbose: bool = True,
) -> bool:
    """
    Merge all SchLib files in a directory.

        Args:
            input_dir: Directory containing SchLib files.
            output_path: Output merged SchLib path.
            pattern: Glob pattern for SchLib files.
            handle_conflicts: How to handle name conflicts.
            verbose: Print progress.

        Returns:
            True if successful.
    """
    input_dir = Path(input_dir)
    files = sorted(input_dir.glob(pattern))

    # Also check lowercase extension
    if pattern == "*.SchLib":
        files.extend(sorted(input_dir.glob("*.Schlib")))
        files.extend(sorted(input_dir.glob("*.schlib")))
        # Deduplicate (case-insensitive on Windows)
        seen: set[str] = set()
        unique: list[Path] = []
        for f in files:
            key = f.name.lower()
            if key not in seen:
                seen.add(key)
                unique.append(f)
        files = unique

    if not files:
        log.warning(f"No SchLib files found in {input_dir}")
        return False

    return merge_schlibs(
        files,
        output_path,
        handle_conflicts=handle_conflicts,
        verbose=verbose,
    )
