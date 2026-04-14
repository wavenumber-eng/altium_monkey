from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from altium_monkey import AltiumSchDoc
from altium_monkey.altium_schlib import AltiumSchLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_SCHDOC = ASSETS_DIR / "projects" / "rt_super_c1" / "RT_SUPER_C1.SchDoc"
OUTPUT_DIR = SAMPLE_DIR / "output"
COMBINED_DIR = OUTPUT_DIR / "combined"
SYMBOL_SVG_DIR = OUTPUT_DIR / "symbol_svgs"
COMBINED_SCHLIB = COMBINED_DIR / f"{INPUT_SCHDOC.stem}.SchLib"
OUTPUT_MANIFEST = OUTPUT_DIR / "schlib_svg_manifest.json"


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "symbol"


def _symbol_svg_filename(symbol_name: str, *, part_id: int, multipart: bool) -> str:
    safe_name = _safe_filename_part(symbol_name)
    if multipart:
        return f"{safe_name}_part{part_id}.svg"
    return f"{safe_name}.svg"


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    COMBINED_DIR.mkdir(parents=True, exist_ok=True)
    SYMBOL_SVG_DIR.mkdir(parents=True, exist_ok=True)


def _require_all_success(results: dict[str, bool]) -> None:
    failed = [symbol for symbol, ok in results.items() if not ok]
    if failed:
        raise RuntimeError(f"Symbol extraction failed for: {', '.join(failed)}")


def _extract_combined_schlib() -> Path:
    schdoc = AltiumSchDoc(INPUT_SCHDOC)
    results = schdoc.extract_symbols(
        COMBINED_DIR,
        combined_schlib=True,
        split_schlibs=False,
        strip_parameters=False,
        strip_implementations=False,
    )
    _require_all_success(results)
    if not COMBINED_SCHLIB.exists():
        raise RuntimeError(f"Combined SchLib was not written: {COMBINED_SCHLIB}")
    return COMBINED_SCHLIB


def _render_symbol_parts(schlib: AltiumSchLib) -> list[dict[str, object]]:
    symbol_entries: list[dict[str, object]] = []

    for symbol in schlib.symbols:
        part_count = int(getattr(symbol, "part_count", 1) or 1)
        multipart = part_count > 1
        part_entries: list[dict[str, object]] = []

        for part_id in range(1, part_count + 1):
            svg = schlib.symbol_to_svg(
                symbol.name,
                part_id=part_id if multipart else None,
            )
            output_path = SYMBOL_SVG_DIR / _symbol_svg_filename(
                symbol.name,
                part_id=part_id,
                multipart=multipart,
            )
            output_path.write_text(svg, encoding="utf-8")

            part_entries.append(
                {
                    "part_id": part_id,
                    "svg": _sample_relative(output_path),
                    "byte_count": output_path.stat().st_size,
                }
            )

        symbol_entries.append(
            {
                "name": symbol.name,
                "description": symbol.description,
                "part_count": part_count,
                "parts": part_entries,
            }
        )

    return symbol_entries


def main() -> None:
    _reset_output_dirs()

    combined_schlib = _extract_combined_schlib()
    schlib = AltiumSchLib(combined_schlib)
    if not schlib.symbols:
        raise RuntimeError(f"No symbols found in {combined_schlib.name}")

    symbol_entries = _render_symbol_parts(schlib)
    svg_count = sum(len(symbol["parts"]) for symbol in symbol_entries)

    manifest = {
        "source_schdoc": _example_relative(INPUT_SCHDOC),
        "combined_schlib": _sample_relative(combined_schlib),
        "symbol_count": len(symbol_entries),
        "svg_count": svg_count,
        "symbols": symbol_entries,
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Extracted library: {_sample_relative(combined_schlib)}")
    print(f"Symbols rendered: {manifest['symbol_count']}")
    print(f"SVG files written: {manifest['svg_count']}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
