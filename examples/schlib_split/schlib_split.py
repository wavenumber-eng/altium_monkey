from __future__ import annotations

import json
import shutil
from pathlib import Path

from altium_monkey import AltiumSchLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHLIB = ASSETS_DIR / "schlib" / "RT_SUPER_C1.SCHLIB"
OUTPUT_DIR = SAMPLE_DIR / "output"
SPLIT_DIR = OUTPUT_DIR / "split"
OUTPUT_MANIFEST = OUTPUT_DIR / "schlib_split_manifest.json"


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)


def split_library() -> dict[str, object]:
    _reset_output_dirs()

    schlib = AltiumSchLib(INPUT_SCHLIB)
    split_outputs = schlib.split(SPLIT_DIR, verbose=False)

    symbols: list[dict[str, object]] = []
    for symbol in schlib.symbols:
        split_path = split_outputs[symbol.name]
        if split_path is None:
            raise RuntimeError(f"Split failed for symbol: {symbol.name}")
        symbols.append(
            {
                "name": symbol.name,
                "split_schlib": _sample_relative(split_path),
                "byte_count": split_path.stat().st_size,
                "part_count": symbol.part_count,
                "pin_count": len(symbol.pins),
                "graphic_count": len(symbol.graphic_primitives),
                "parameter_count": len(symbol.parameters),
                "implementation_count": len(symbol.implementations),
            }
        )

    manifest: dict[str, object] = {
        "input_schlib": _example_relative(INPUT_SCHLIB),
        "split_dir": _sample_relative(SPLIT_DIR),
        "symbol_count": len(symbols),
        "symbols": symbols,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = split_library()
    print(f"Input SchLib: {_example_relative(INPUT_SCHLIB)}")
    print(f"Symbols split: {manifest['symbol_count']}")
    print(f"Wrote split SchLib folder: {_sample_relative(SPLIT_DIR)}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
