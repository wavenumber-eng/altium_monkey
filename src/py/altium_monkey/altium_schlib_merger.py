"""
Altium SchLib Merger

Merges multiple SchLib files into a single multi-symbol SchLib file.

Uses the object model directly. Font tables are preserved via
FontIDManager, and embedded images are copied with their symbol data.
"""

import logging
from pathlib import Path

from .altium_schlib import AltiumSchLib

log = logging.getLogger(__name__)


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
    seen_names: dict[str, int] = {}

    for path in input_paths:
        path = Path(path)
        if not path.exists():
            log.warning(f"Skipping missing file: {path}")
            continue

        try:
            source = AltiumSchLib(path)
        except Exception as e:
            log.error(f"Failed to parse {path.name}: {e}")
            continue

        # Merge font manager: use the first source's font manager,
        # then subsequent sources reuse it (font IDs are preserved
        # since save() writes the complete font table)
        if merged.font_manager is None and source.font_manager:
            merged.font_manager = source.font_manager

        for symbol in source.symbols:
            name = symbol.name

            # Handle name conflicts
            if name in seen_names:
                if handle_conflicts == "skip":
                    if verbose:
                        log.info(f"  SKIP: {name} (duplicate)")
                    continue
                elif handle_conflicts == "error":
                    raise ValueError(f"Duplicate symbol name: {name}")
                else:  # rename
                    seen_names[name] += 1
                    name = f"{name}_{seen_names[name]}"
                    if verbose:
                        log.info(f"  Renamed: {symbol.name} -> {name}")
            else:
                seen_names[name] = 0

            new_sym = merged.add_symbol(
                name,
                symbol.description,
                original_name=symbol.original_name,
            )
            new_sym.part_count = symbol.part_count
            new_sym.component_record = symbol.component_record
            for obj in symbol.objects:
                new_sym.objects.append(obj)
            new_sym.raw_records = symbol.raw_records
            new_sym._original_streams = dict(symbol._original_streams)

            # Copy embedded images
            for img in symbol.images:
                filename = getattr(img, "filename", None)
                if filename and filename in source.embedded_images:
                    merged.embedded_images[filename] = source.embedded_images[filename]

        if verbose:
            log.info(f"  Added {len(source.symbols)} symbols from {path.name}")

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
