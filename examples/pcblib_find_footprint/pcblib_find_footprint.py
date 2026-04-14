from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path

from altium_monkey import AltiumPcbLib

SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_ROOT = SAMPLE_DIR.parent
DEFAULT_PCBLIB_DIR = EXAMPLES_ROOT / "assets" / "pcblib"
OUTPUT_DIR = SAMPLE_DIR / "output"
INDEX_PATH = OUTPUT_DIR / "footprint_index.json"
DEFAULT_QUERIES = ("r0603", "tc2030", "msop", "tp smt")


@dataclass(frozen=True)
class FootprintMatch:
    library: str
    footprint: str
    score: float


def _normalized_search_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def build_footprint_index(pcblib_dir: Path) -> dict[str, list[str]]:
    """
    Build a `{library_filename: [footprint_name, ...]}` index for a PcbLib folder.
    """
    pcblib_paths = sorted(pcblib_dir.glob("*.PcbLib"))
    if not pcblib_paths:
        raise FileNotFoundError(f"No .PcbLib files found in {pcblib_dir}")

    index: dict[str, list[str]] = {}
    for pcblib_path in pcblib_paths:
        pcblib = AltiumPcbLib.from_file(pcblib_path)
        index[pcblib_path.name] = [footprint.name for footprint in pcblib.footprints]
    return index


def find_footprints(
    index: dict[str, list[str]],
    query: str,
    *,
    limit: int = 8,
    cutoff: float = 0.35,
) -> list[FootprintMatch]:
    """
    Demonstrate fuzzy footprint-name matching against a PcbLib folder index.

    This intentionally uses only the Python standard library. Production code
    can swap in a stronger fuzzy matcher while keeping the same index shape.
    """
    query_text = _normalized_search_text(query)
    if not query_text:
        return []

    matches: list[FootprintMatch] = []
    for library_name, footprint_names in index.items():
        for footprint_name in footprint_names:
            searchable_text = _normalized_search_text(
                f"{footprint_name} {library_name}"
            )
            score = SequenceMatcher(None, query_text, searchable_text).ratio()
            if query_text in searchable_text:
                score = max(score, 0.95)
            if score >= cutoff:
                matches.append(
                    FootprintMatch(
                        library=library_name,
                        footprint=footprint_name,
                        score=round(score, 3),
                    )
                )

    matches.sort(key=lambda item: (-item.score, item.library, item.footprint))
    return matches[:limit]


def write_index_report(
    pcblib_dir: Path,
    *,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
) -> dict[str, object]:
    index = build_footprint_index(pcblib_dir)
    query_results = {
        query: [asdict(match) for match in find_footprints(index, query)]
        for query in queries
    }
    report: dict[str, object] = {
        "pcblib_dir": pcblib_dir.relative_to(EXAMPLES_ROOT).as_posix()
        if pcblib_dir.is_relative_to(EXAMPLES_ROOT)
        else str(pcblib_dir),
        "library_count": len(index),
        "footprint_count": sum(len(footprints) for footprints in index.values()),
        "libraries": index,
        "queries": query_results,
    }

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index PcbLib footprint names and demonstrate fuzzy search."
    )
    parser.add_argument(
        "--pcblib-dir",
        type=Path,
        default=DEFAULT_PCBLIB_DIR,
        help="Folder containing .PcbLib files. Defaults to examples/assets/pcblib.",
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
    report = write_index_report(args.pcblib_dir, queries=queries)

    print(
        f"Indexed {report['footprint_count']} footprint(s) "
        f"from {report['library_count']} PcbLib file(s)."
    )
    print("Footprints:")
    for library_name, footprint_names in report["libraries"].items():
        for footprint_name in footprint_names:
            print(f"  {library_name}: {footprint_name}")

    print("\nFuzzy search examples:")
    for query, matches in report["queries"].items():
        print(f"  {query!r}:")
        for match in matches[:5]:
            print(
                "    "
                f"{match['footprint']} in {match['library']} "
                f"(score={match['score']:.3f})"
            )
    print(f"\nWrote {INDEX_PATH}")


if __name__ == "__main__":
    main()
