from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

from altium_monkey import AltiumSchLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
DEFAULT_SCHLIB_DIR = EXAMPLES_ROOT / "assets" / "schlib"
OUTPUT_DIR = SAMPLE_DIR / "output"
INDEX_PATH = OUTPUT_DIR / "symbol_index.json"
DEFAULT_QUERIES = ("rt685", "r_2p", "mosfet", "smt tp")


@dataclass(frozen=True)
class SymbolMatch:
    library: str
    symbol: str
    score: float


def _normalized_search_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _schlib_paths(schlib_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in schlib_dir.iterdir()
        if path.is_file() and path.suffix.casefold() == ".schlib"
    )


def _relative_to_examples(path: Path) -> str:
    if path.is_relative_to(EXAMPLES_ROOT):
        return path.relative_to(EXAMPLES_ROOT).as_posix()
    return str(path)


def _console_text(value: object) -> str:
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


def build_symbol_index(schlib_dir: Path) -> dict[str, list[str]]:
    """
    Build a `{library_filename: [symbol_name, ...]}` index for a SchLib folder.
    """
    schlib_paths = _schlib_paths(schlib_dir)
    if not schlib_paths:
        raise FileNotFoundError(f"No .SchLib files found in {schlib_dir}")

    index: dict[str, list[str]] = {}
    for schlib_path in schlib_paths:
        symbol_names = AltiumSchLib.get_symbol_names(schlib_path)
        if not symbol_names:
            symbol_names = [symbol.name for symbol in AltiumSchLib(schlib_path).symbols]
        index[schlib_path.name] = symbol_names
    return index


def find_symbols(
    index: dict[str, list[str]],
    query: str,
    *,
    limit: int = 8,
    cutoff: float = 0.35,
) -> list[SymbolMatch]:
    """
    Demonstrate fuzzy symbol-name matching against a SchLib folder index.

    This intentionally uses only the Python standard library. Production code
    can swap in a stronger fuzzy matcher while keeping the same index shape.
    """
    query_text = _normalized_search_text(query)
    if not query_text:
        return []

    matches: list[SymbolMatch] = []
    for library_name, symbol_names in index.items():
        for symbol_name in symbol_names:
            searchable_text = _normalized_search_text(f"{symbol_name} {library_name}")
            score = SequenceMatcher(None, query_text, searchable_text).ratio()
            if query_text in searchable_text:
                score = max(score, 0.95)
            if score >= cutoff:
                matches.append(
                    SymbolMatch(
                        library=library_name,
                        symbol=symbol_name,
                        score=round(score, 3),
                    )
                )

    matches.sort(key=lambda item: (-item.score, item.library, item.symbol))
    return matches[:limit]


def write_index_report(
    schlib_dir: Path,
    *,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
) -> dict[str, object]:
    index = build_symbol_index(schlib_dir)
    query_results = {
        query: [asdict(match) for match in find_symbols(index, query)]
        for query in queries
    }
    report: dict[str, object] = {
        "schlib_dir": _relative_to_examples(schlib_dir),
        "library_count": len(index),
        "symbol_count": sum(len(symbols) for symbols in index.values()),
        "libraries": index,
        "queries": query_results,
    }

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index SchLib symbol names and demonstrate fuzzy search."
    )
    parser.add_argument(
        "--schlib-dir",
        type=Path,
        default=DEFAULT_SCHLIB_DIR,
        help="Folder containing .SchLib files. Defaults to examples/assets/schlib.",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=None,
        help="Fuzzy query to run. Can be supplied more than once.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    queries = tuple(args.query) if args.query else DEFAULT_QUERIES
    report = write_index_report(args.schlib_dir, queries=queries)

    print(
        f"Indexed {report['symbol_count']} symbol(s) "
        f"from {report['library_count']} SchLib file(s)."
    )
    print("Symbols:")
    for library_name, symbol_names in report["libraries"].items():
        for symbol_name in symbol_names:
            print(f"  {_console_text(library_name)}: {_console_text(symbol_name)}")

    print("\nFuzzy search examples:")
    for query, matches in report["queries"].items():
        print(f"  {query!r}:")
        for match in matches[:5]:
            print(
                "    "
                f"{_console_text(match['symbol'])} in {_console_text(match['library'])} "
                f"(score={match['score']:.3f})"
            )
    print(f"\nWrote {INDEX_PATH}")


if __name__ == "__main__":
    main()
