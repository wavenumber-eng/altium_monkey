from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from altium_monkey.altium_prjpcb import AltiumPrjPcb


SAMPLE_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SAMPLE_DIR.parent
SOURCE_ASSETS_DIR = EXAMPLES_DIR / "assets"
SOURCE_PROJECT_DIR = SOURCE_ASSETS_DIR / "projects" / "rt_super_c1"
SOURCE_PROJECT_FILE = SOURCE_PROJECT_DIR / "RT_SUPER_C1.PrjPcb"
SOURCE_OUTJOB_FILE = SOURCE_PROJECT_DIR / "reference_gen.OutJob"
SOURCE_SCHLIB_FILE = SOURCE_ASSETS_DIR / "schlib" / "RT_SUPER_C1.SCHLIB"

OUTPUT_DIR = SAMPLE_DIR / "output"
WORK_ASSETS_DIR = OUTPUT_DIR / "assets"
WORK_PROJECT_DIR = WORK_ASSETS_DIR / "projects" / "rt_super_c1"
WORK_PROJECT_FILE = WORK_PROJECT_DIR / SOURCE_PROJECT_FILE.name
WORK_SCHLIB_DIR = WORK_ASSETS_DIR / "schlib"
RUN_ARTIFACTS_DIR = OUTPUT_DIR / "run_artifacts"
SUMMARY_PATH = OUTPUT_DIR / "outjob_runner_summary.json"

GENERATED_OUTPUT_RELATIVE_TO_PROJECT = r"outputs\generated"
GENERATED_OUTPUT_DIR = WORK_PROJECT_DIR / GENERATED_OUTPUT_RELATIVE_TO_PROJECT


def _relative_to_examples(path: Path) -> str:
    return path.resolve().relative_to(EXAMPLES_DIR.resolve()).as_posix()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(EXAMPLES_DIR.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _sanitize_text(text: str) -> str:
    examples_root = str(EXAMPLES_DIR.resolve())
    return text.replace(examples_root, "examples")


def _result_to_jsonable(result: object) -> dict[str, Any]:
    payload = asdict(result)
    for key, value in list(payload.items()):
        if isinstance(value, Path):
            payload[key] = _display_path(value)
        elif isinstance(value, str):
            payload[key] = _sanitize_text(value)
    return payload


def open_output_folder(path: Path) -> bool:
    if not path.exists():
        return False

    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        return False
    return True


def prepare_working_project() -> None:
    WORK_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_SCHLIB_DIR.mkdir(parents=True, exist_ok=True)

    for source in SOURCE_PROJECT_DIR.iterdir():
        if source.is_file() and source.suffix.lower() != ".prjpcbstructure":
            shutil.copy2(source, WORK_PROJECT_DIR / source.name)

    shutil.copy2(SOURCE_SCHLIB_FILE, WORK_SCHLIB_DIR / SOURCE_SCHLIB_FILE.name)


def build_summary(
    *,
    run_requested: bool,
    timeout_seconds: float,
    open_output: bool,
) -> dict[str, Any]:
    prepare_working_project()
    prj = AltiumPrjPcb(WORK_PROJECT_FILE)
    outjob = prj.outjob()

    summary: dict[str, Any] = {
        "source_project": _relative_to_examples(SOURCE_PROJECT_FILE),
        "working_project": _relative_to_examples(WORK_PROJECT_FILE),
        "outjob": _relative_to_examples(outjob.path),
        "api_pattern": "prj.outjob().run(...)",
        "project_pcbdocs": [
            _relative_to_examples(path) for path in prj.get_pcbdoc_paths()
        ],
        "current_variant": prj.get_current_variant(),
        "run_requested": run_requested,
        "timeout_seconds": timeout_seconds,
        "default_generated_output_path": GENERATED_OUTPUT_RELATIVE_TO_PROJECT,
        "generated_output_dir": _relative_to_examples(GENERATED_OUTPUT_DIR),
    }

    if not run_requested:
        summary["run_status"] = "not_requested"
        summary["run_hint"] = (
            "Run this example with --run to launch Altium Designer and execute "
            "the OutJob through prj.outjob(...).run(...)."
        )
        return summary

    result = outjob.run(
        timeout_seconds=timeout_seconds,
        script_directory=RUN_ARTIFACTS_DIR,
        keep_script_artifacts=True,
        stage_outjob_copy=False,
    )
    summary["run_status"] = "completed" if result.success else "failed"
    summary["run_result"] = _result_to_jsonable(result)
    summary["output_folder_opened"] = (
        open_output_folder(GENERATED_OUTPUT_DIR)
        if result.success and open_output
        else False
    )
    return summary


def write_summary(summary: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare or run the RT Super C1 OutJob through Altium."
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Launch Altium Designer and run the static reference_gen.OutJob.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the Altium runner completion marker.",
    )
    parser.add_argument(
        "--no-open-output",
        action="store_true",
        help="Do not open the generated output folder after a successful run.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_summary(
        run_requested=bool(args.run),
        timeout_seconds=float(args.timeout),
        open_output=not bool(args.no_open_output),
    )
    write_summary(summary)

    print(f"Project: {_relative_to_examples(WORK_PROJECT_FILE)}")
    print(
        f"OutJob: {_relative_to_examples(WORK_PROJECT_DIR / SOURCE_OUTJOB_FILE.name)}"
    )
    print(f"Summary: {SUMMARY_PATH.relative_to(SAMPLE_DIR)}")
    print(f"Generated output: {_relative_to_examples(GENERATED_OUTPUT_DIR)}")
    print(f"Run status: {summary['run_status']}")

    if args.run and summary["run_status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
