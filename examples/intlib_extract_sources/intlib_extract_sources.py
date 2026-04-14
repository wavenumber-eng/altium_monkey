from __future__ import annotations

import json
import shutil
from pathlib import Path

from altium_monkey import AltiumIntLib, AltiumPcbLib, AltiumSchLib


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
ASSETS_DIR = EXAMPLES_DIR / "assets"
INPUT_INTLIB = ASSETS_DIR / "projects" / "rt_super_c1" / "RT_SUPER_C1.IntLib"
OUTPUT_DIR = SAMPLE_DIR / "output"
EXTRACT_DIR = OUTPUT_DIR / "extracted_sources"
SPLIT_DIR = OUTPUT_DIR / "split_sources"
OUTPUT_MANIFEST = OUTPUT_DIR / "intlib_extract_sources_manifest.json"


def _example_relative(path: Path) -> str:
    return str(path.relative_to(EXAMPLES_DIR)).replace("\\", "/")


def _sample_relative(path: Path) -> str:
    return str(path.relative_to(SAMPLE_DIR)).replace("\\", "/")


def _reset_output_dirs() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)


def _summarize_extracted_source(path: Path, kind: str) -> dict[str, object]:
    summary: dict[str, object] = {
        "path": _sample_relative(path),
        "byte_count": path.stat().st_size,
    }
    if kind == "SchLib":
        summary["symbol_count"] = len(AltiumSchLib.get_symbol_names(path))
    elif kind == "PCBLib":
        summary["footprint_count"] = len(AltiumPcbLib.get_footprint_names(path))
    return summary


def _split_schlib(path: Path) -> dict[str, object]:
    schlib = AltiumSchLib(path)
    if len(schlib.symbols) <= 1:
        return {"split_dir": None, "split_count": 0, "split_outputs": []}

    output_dir = SPLIT_DIR / "SchLib"
    split_outputs = schlib.split(output_dir, verbose=False)
    return {
        "split_dir": _sample_relative(output_dir),
        "split_count": len(split_outputs),
        "split_outputs": [
            {"symbol": name, "path": _sample_relative(split_path)}
            for name, split_path in split_outputs.items()
            if split_path is not None
        ],
    }


def _split_pcblib(path: Path) -> dict[str, object]:
    pcblib = AltiumPcbLib.from_file(path)
    if len(pcblib.footprints) <= 1:
        return {"split_dir": None, "split_count": 0, "split_outputs": []}

    output_dir = SPLIT_DIR / "PCBLib"
    split_outputs = pcblib.split(output_dir)
    return {
        "split_dir": _sample_relative(output_dir),
        "split_count": len(split_outputs),
        "split_outputs": [
            {"footprint": name, "path": _sample_relative(split_path)}
            for name, split_path in split_outputs.items()
        ],
    }


def _split_extracted_source(path: Path, kind: str) -> dict[str, object]:
    if kind == "SchLib":
        return _split_schlib(path)
    if kind == "PCBLib":
        return _split_pcblib(path)
    return {"split_dir": None, "split_count": 0, "split_outputs": []}


def extract_sources() -> dict[str, object]:
    _reset_output_dirs()

    with AltiumIntLib(INPUT_INTLIB) as intlib:
        source_entries = intlib.get_source_entries()
        result = intlib.extract_sources(EXTRACT_DIR)

        extracted_sources = []
        for source in result.sources:
            if source.output_path is None:
                raise RuntimeError(f"Source was not extracted: {source.stream_path}")
            source_summary = _summarize_extracted_source(
                source.output_path, source.kind
            )
            split_summary = _split_extracted_source(source.output_path, source.kind)
            extracted_sources.append(
                {
                    "kind": source.kind,
                    "stream_path": source.stream_path,
                    "original_filename": Path(source.suggested_filename).name,
                    **source_summary,
                    **split_summary,
                }
            )

        manifest: dict[str, object] = {
            "input_intlib": _example_relative(INPUT_INTLIB),
            "component_count": len(intlib.components),
            "source_count": len(source_entries),
            "source_kinds": sorted({source.kind for source in source_entries}),
            "extracted_dir": _sample_relative(EXTRACT_DIR),
            "split_dir": _sample_relative(SPLIT_DIR),
            "libpkg": (
                _sample_relative(result.libpkg_path)
                if result.libpkg_path is not None
                else None
            ),
            "extracted_sources": extracted_sources,
        }

    OUTPUT_MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    manifest = extract_sources()
    print(f"Input IntLib: {manifest['input_intlib']}")
    print(f"Components: {manifest['component_count']}")
    print(f"Extracted source kinds: {', '.join(manifest['source_kinds'])}")
    print(f"Wrote sources: {manifest['extracted_dir']}")
    print(f"Wrote split sources: {manifest['split_dir']}")
    print(f"Wrote manifest: {_sample_relative(OUTPUT_MANIFEST)}")


if __name__ == "__main__":
    main()
