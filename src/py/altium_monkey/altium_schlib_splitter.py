"""
Altium SchLib Splitter

Splits a multi-symbol SchLib file into individual SchLib files, one per symbol.

Uses the object model directly. Font tables and embedded images are handled
automatically by the save() path.
"""

import logging
from pathlib import Path

from .altium_schlib import AltiumSchLib

log = logging.getLogger(__name__)


def split_schlib(
    input_path: Path | str,
    output_dir: Path | str,
    *,
    debug: bool = False,
) -> dict[str, Path]:
    """
    Split a multi-symbol SchLib into individual SchLib files.

    Args:
        input_path: Path to source SchLib file.
        output_dir: Directory for output files.
        debug: Enable debug output.

    Returns:
        Dict mapping symbol name to output file path.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source = AltiumSchLib(input_path)
    log.info(f"Splitting {input_path.name}: {len(source.symbols)} symbols")

    results: dict[str, Path] = {}

    for symbol in source.symbols:
        # Create a new single-symbol SchLib
        single = AltiumSchLib()

        # Copy font manager from source so font table is preserved
        single.font_manager = source.font_manager

        # Add the symbol with all its objects
        new_sym = single.add_symbol(symbol.name, symbol.description)
        new_sym.part_count = symbol.part_count
        new_sym.component_record = symbol.component_record
        for obj in symbol.objects:
            new_sym.objects.append(obj)

        # Copy raw_records for round-trip fidelity (parsed symbols have these)
        new_sym.raw_records = symbol.raw_records

        # Copy embedded images referenced by this symbol
        for img in symbol.images:
            filename = getattr(img, 'filename', None)
            if filename and filename in source.embedded_images:
                single.embedded_images[filename] = source.embedded_images[filename]

        # Save
        safe_name = _sanitize_filename(symbol.name)
        output_path = output_dir / f"{safe_name}.SchLib"
        single.save(output_path, debug=debug)
        results[symbol.name] = output_path

        if debug:
            log.info(f"  Split: {symbol.name} -> {output_path.name}")

    log.info(f"Split complete: {len(results)} files")
    return results


def _sanitize_filename(name: str) -> str:
    """
    Remove characters that are illegal in filenames or OLE storage names.
    """
    illegal = '<>:"/\\|?*'
    return ''.join(c if c not in illegal else '_' for c in name)
