from __future__ import annotations

import json
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
SPLIT_DBLIB_READY_DIR = OUTPUT_DIR / "split_dblib_ready"
SPLIT_PRESERVED_DIR = OUTPUT_DIR / "split_preserved"
OUTPUT_MANIFEST = OUTPUT_DIR / "schlib_extraction_manifest.json"


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _implementation_child_count(symbol: object) -> int:
    return sum(
        len(getattr(implementation, "children", []))
        for implementation in symbol.implementations
    )


def _summarize_schlib(path: Path) -> dict[str, object]:
    schlib = AltiumSchLib(path)
    return {
        "file": str(path.relative_to(SAMPLE_DIR)).replace("\\", "/"),
        "symbol_count": len(schlib.symbols),
        "parameter_count": sum(len(symbol.parameters) for symbol in schlib.symbols),
        "implementation_count": sum(
            len(symbol.implementations) for symbol in schlib.symbols
        ),
        "implementation_child_count": sum(
            _implementation_child_count(symbol) for symbol in schlib.symbols
        ),
    }


def _summarize_split_dir(path: Path) -> list[dict[str, object]]:
    return [
        _summarize_schlib(schlib_path) for schlib_path in sorted(path.glob("*.SchLib"))
    ]


def _require_all_success(results: dict[str, bool], mode: str) -> None:
    failed = [symbol for symbol, ok in results.items() if not ok]
    if failed:
        raise RuntimeError(f"{mode} extraction failed for: {', '.join(failed)}")


def _total(summary: list[dict[str, object]], key: str) -> int:
    return sum(int(item[key]) for item in summary)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _reset_dir(COMBINED_DIR)
    _reset_dir(SPLIT_DBLIB_READY_DIR)
    _reset_dir(SPLIT_PRESERVED_DIR)

    schdoc = AltiumSchDoc(INPUT_SCHDOC)

    combined_results = schdoc.extract_symbols(
        COMBINED_DIR,
        combined_schlib=True,
        split_schlibs=False,
        strip_parameters=False,
        strip_implementations=False,
    )
    _require_all_success(combined_results, "combined")

    dblib_ready_results = schdoc.extract_symbols(
        SPLIT_DBLIB_READY_DIR,
        combined_schlib=False,
        split_schlibs=True,
        strip_parameters=True,
        strip_implementations=True,
    )
    _require_all_success(dblib_ready_results, "DBLib-ready split")

    preserved_results = schdoc.extract_symbols(
        SPLIT_PRESERVED_DIR,
        combined_schlib=False,
        split_schlibs=True,
        strip_parameters=False,
        strip_implementations=False,
    )
    _require_all_success(preserved_results, "preserved split")

    combined_path = COMBINED_DIR / f"{INPUT_SCHDOC.stem}.SchLib"
    combined_summary = _summarize_schlib(combined_path)
    dblib_ready_summary = _summarize_split_dir(SPLIT_DBLIB_READY_DIR)
    preserved_summary = _summarize_split_dir(SPLIT_PRESERVED_DIR)

    if int(combined_summary["symbol_count"]) != len(combined_results):
        raise RuntimeError(
            "Combined SchLib symbol count does not match extracted symbols"
        )
    if len(dblib_ready_summary) != len(dblib_ready_results):
        raise RuntimeError(
            "DBLib-ready split SchLib count does not match extracted symbols"
        )
    if len(preserved_summary) != len(preserved_results):
        raise RuntimeError(
            "Preserved split SchLib count does not match extracted symbols"
        )
    if _total(dblib_ready_summary, "parameter_count") != 0:
        raise RuntimeError("DBLib-ready split output still contains parameters")
    if _total(dblib_ready_summary, "implementation_count") != 0:
        raise RuntimeError("DBLib-ready split output still contains implementations")
    if _total(preserved_summary, "parameter_count") == 0:
        raise RuntimeError("Preserved split output did not keep parameters")
    if _total(preserved_summary, "implementation_count") == 0:
        raise RuntimeError("Preserved split output did not keep implementations")

    manifest = {
        "source": str(INPUT_SCHDOC.relative_to(EXAMPLES_DIR)).replace("\\", "/"),
        "symbol_count": len(combined_results),
        "combined": combined_summary,
        "split_dblib_ready": {
            "directory": str(SPLIT_DBLIB_READY_DIR.relative_to(SAMPLE_DIR)).replace(
                "\\", "/"
            ),
            "file_count": len(dblib_ready_summary),
            "parameter_count": _total(dblib_ready_summary, "parameter_count"),
            "implementation_count": _total(dblib_ready_summary, "implementation_count"),
        },
        "split_preserved": {
            "directory": str(SPLIT_PRESERVED_DIR.relative_to(SAMPLE_DIR)).replace(
                "\\", "/"
            ),
            "file_count": len(preserved_summary),
            "parameter_count": _total(preserved_summary, "parameter_count"),
            "implementation_count": _total(preserved_summary, "implementation_count"),
            "implementation_child_count": _total(
                preserved_summary, "implementation_child_count"
            ),
        },
    }
    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Loaded: {INPUT_SCHDOC.relative_to(EXAMPLES_DIR)}")
    print(f"Extracted symbols: {len(combined_results)}")
    print(f"Wrote combined library: {combined_path.relative_to(SAMPLE_DIR)}")
    print(
        f"Wrote DBLib-ready split libraries: {SPLIT_DBLIB_READY_DIR.relative_to(SAMPLE_DIR)}"
    )
    print(
        f"Wrote preserved split libraries: {SPLIT_PRESERVED_DIR.relative_to(SAMPLE_DIR)}"
    )
    print(f"Wrote manifest: {OUTPUT_MANIFEST.relative_to(SAMPLE_DIR)}")


if __name__ == "__main__":
    main()
