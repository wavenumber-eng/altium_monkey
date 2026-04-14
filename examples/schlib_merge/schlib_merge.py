from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from altium_monkey import AltiumSchLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHLIB_DIR = ASSETS_DIR / "schlib"
OUTPUT_DIR = SAMPLE_DIR / "output"
MERGED_DIR = OUTPUT_DIR / "merged"
MERGED_SCHLIB = MERGED_DIR / "merged_schlib_assets.SchLib"
OUTPUT_MANIFEST = OUTPUT_DIR / "schlib_merge_manifest.json"


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _schlib_paths(schlib_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in schlib_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".schlib"
    )


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    MERGED_DIR.mkdir(parents=True, exist_ok=True)


def _read_symbol_names(path: Path) -> list[str]:
    symbol_names = AltiumSchLib.get_symbol_names(path)
    if symbol_names:
        return symbol_names
    return [symbol.name for symbol in AltiumSchLib(path).symbols]


def merge_libraries() -> dict[str, object]:
    _reset_output_dirs()

    input_paths = _schlib_paths(INPUT_SCHLIB_DIR)
    if not input_paths:
        raise FileNotFoundError(f"No SchLib files found in {INPUT_SCHLIB_DIR}")

    inputs = [
        {
            "schlib": _example_relative(path),
            "symbol_count": len(symbol_names),
            "symbols": symbol_names,
        }
        for path in input_paths
        for symbol_names in [_read_symbol_names(path)]
    ]
    source_symbol_names = [
        symbol_name for item in inputs for symbol_name in item["symbols"]
    ]
    duplicate_names = sorted(
        name for name, count in Counter(source_symbol_names).items() if count > 1
    )

    merged = AltiumSchLib.merge(
        input_paths,
        MERGED_SCHLIB,
        handle_conflicts="rename",
        verbose=False,
    )

    manifest: dict[str, object] = {
        "input_schlib_dir": _example_relative(INPUT_SCHLIB_DIR),
        "input_library_count": len(input_paths),
        "input_symbol_count": len(source_symbol_names),
        "duplicate_source_symbol_names": duplicate_names,
        "merged_schlib": _sample_relative(MERGED_SCHLIB),
        "merged_byte_count": MERGED_SCHLIB.stat().st_size,
        "merged_symbol_count": len(merged.symbols),
        "inputs": inputs,
        "merged_symbols": [symbol.name for symbol in merged.symbols],
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = merge_libraries()
    print(f"Input SchLib folder: {_example_relative(INPUT_SCHLIB_DIR)}")
    print(f"Input libraries: {manifest['input_library_count']}")
    print(f"Input symbols: {manifest['input_symbol_count']}")
    print(f"Merged symbols: {manifest['merged_symbol_count']}")
    print(f"Wrote merged SchLib: {_sample_relative(MERGED_SCHLIB)}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
