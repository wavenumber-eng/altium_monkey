from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = PUBLIC_ROOT / "examples"
MANIFEST_PATH = EXAMPLES_ROOT / "manifest.toml"
CONTRACTS_ROOT = PUBLIC_ROOT / "docs" / "schemas" / "altium_monkey"
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\((?P<target>[^)]+)\)")


def _load_examples() -> list[dict[str, object]]:
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    examples = manifest.get("examples", [])
    if not isinstance(examples, list):
        raise TypeError("examples/manifest.toml must contain an examples list")
    return examples


def test_schema_contract_docs_list_public_schema_ids() -> None:
    docs_path = PUBLIC_ROOT / "docs" / "schemas" / "index.md"
    docs_text = docs_path.read_text(encoding="utf-8")

    for schema_id in (
        "altium_monkey.design.a0",
        "altium_monkey.netlist.a0",
        "altium_monkey.pcb.svg.enrichment.a0",
    ):
        assert schema_id in docs_text

    assert "AltiumDesign.to_json" in docs_text
    assert "`pnp` is optional" in docs_text
    assert "Netlist.to_json" in docs_text
    assert 'data-enrichment-schema="' in docs_text
    assert "pcb-enrichment-a0" in docs_text
    assert "Moving from `a` to `b`" in docs_text
    assert "Moving from `a0` to `a1`" in docs_text
    assert "docs/schemas/altium_monkey" in docs_text
    assert "design_a0.schema.json" in docs_text
    assert "netlist_a0.schema.json" in docs_text
    assert "pcb_svg_enrichment_a0.schema.json" in docs_text


def test_altium_monkey_contract_schemas_are_parseable() -> None:
    schemas = {
        "design_a0.schema.json": "altium_monkey.design.a0",
        "netlist_a0.schema.json": "altium_monkey.netlist.a0",
        "pcb_svg_enrichment_a0.schema.json": (
            "altium_monkey.pcb.svg.enrichment.a0"
        ),
    }

    spec_text = (CONTRACTS_ROOT / "SPEC.md").read_text(encoding="utf-8")
    for filename, schema_id in schemas.items():
        schema_path = CONTRACTS_ROOT / filename
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["schema"]["const"] == schema_id
        assert schema_id in spec_text


def test_public_gitignore_tracks_lockfile_and_ignores_example_outputs() -> None:
    gitignore_lines = {
        line.strip()
        for line in (PUBLIC_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert (PUBLIC_ROOT / "uv.lock").exists()
    assert "uv.lock" not in gitignore_lines
    assert "examples/**/output/" in gitignore_lines


def test_public_lockfile_matches_release_dependency_shape() -> None:
    lock_data = tomllib.loads((PUBLIC_ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = lock_data.get("package", [])
    altium_package = next(
        package for package in packages if package.get("name") == "altium-monkey"
    )
    if "version" in altium_package:
        assert altium_package["version"] != "1.0.0"

    dependency_names = {
        dependency["name"] for dependency in altium_package.get("dependencies", [])
    }
    assert {"cadquery", "freetype-py", "numpy", "pillow", "uharfbuzz"}.issubset(
        dependency_names
    )

    optional_dependency_names = {
        dependency["name"]
        for dependencies in altium_package.get("optional-dependencies", {}).values()
        for dependency in dependencies
    }
    assert "mkdocs" not in optional_dependency_names


def test_domain_docs_list_public_workflow_examples() -> None:
    docs_text = "\n".join(
        (PUBLIC_ROOT / "docs" / filename).read_text(encoding="utf-8")
        for filename in (
            "schdoc.md",
            "schlib.md",
            "pcbdoc.md",
            "pcblib.md",
            "prjpcb.md",
            "altium_design.md",
        )
    )

    for token in (
        "AltiumSchDoc",
        "AltiumSchLib",
        "AltiumDesign",
        "AltiumPcbDoc",
        "AltiumPcbLib",
        "AltiumPrjPcb",
        "SchPointMils",
        "SchRectMils",
        "schdoc_add_note",
        "schdoc_add_sheet_symbol",
        "schdoc_svg",
        "schlib_svg",
        "pcbdoc_add_pad",
        "pcblib_split",
        "prjpcb_make_project",
        "outjob_runner",
        "pcbdoc_bom",
        "pcbdoc_pick_n_place",
        "altium_monkey.design.a0",
        "altium_monkey.netlist.a0",
    ):
        assert token in docs_text


def test_generated_docs_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(PUBLIC_ROOT / "tools" / "generate_docs.py"), "--check"],
        cwd=PUBLIC_ROOT,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_public_markdown_links_resolve() -> None:
    docs_root = PUBLIC_ROOT / "docs"
    public_root = PUBLIC_ROOT.resolve()
    markdown_paths = [
        PUBLIC_ROOT / "README.md",
        PUBLIC_ROOT / "RELEASE_NOTES.md",
        *docs_root.rglob("*.md"),
    ]
    missing: list[str] = []
    for markdown_path in markdown_paths:
        text = markdown_path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = match.group("target").split("#", 1)[0].strip()
            if (
                not target
                or target.startswith(("http://", "https://", "mailto:"))
                or "://" in target
            ):
                continue

            target_path = (markdown_path.parent / target).resolve()
            if target_path.is_dir():
                if (target_path / "index.md").exists():
                    target_path = target_path / "index.md"
                else:
                    continue
            try:
                target_path.relative_to(public_root)
            except ValueError:
                missing.append(f"{markdown_path.relative_to(public_root)} -> {target}")
                continue
            if not target_path.exists():
                missing.append(f"{markdown_path.relative_to(public_root)} -> {target}")

    assert not missing, "\n".join(missing[:50])


@pytest.fixture(scope="session")
def check_examples_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp_root = tmp_path_factory.mktemp("example_asset_check")
    copied_examples = tmp_root / "examples"
    shutil.copytree(
        EXAMPLES_ROOT,
        copied_examples,
        ignore=shutil.ignore_patterns("History", "__Previews", "*.Zip"),
    )
    return copied_examples


def _declared_outputs(example: dict[str, object]) -> Iterator[str]:
    outputs = example.get("outputs", [])
    if not isinstance(outputs, list):
        raise TypeError(f"{example.get('id', '<unknown>')}: outputs must be a list")
    for output in outputs:
        if not isinstance(output, str):
            raise TypeError(
                f"{example.get('id', '<unknown>')}: output must be a string"
            )
        yield output


def _declared_paths(example: dict[str, object], key: str) -> Iterator[str]:
    paths = example.get(key, [])
    if not isinstance(paths, list):
        raise TypeError(f"{example.get('id', '<unknown>')}: {key} must be a list")
    for path in paths:
        if not isinstance(path, str):
            raise TypeError(
                f"{example.get('id', '<unknown>')}: {key} path must be a string"
            )
        yield path


def _example_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    source_path = str(PUBLIC_ROOT / "src" / "py")
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{source_path}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else source_path
    )
    return env


def _run_example_entrypoint(
    example: dict[str, object],
    check_examples_root: Path,
) -> subprocess.CompletedProcess[str]:
    entrypoint = example.get("entrypoint")
    if not isinstance(entrypoint, str):
        raise TypeError(
            f"{example.get('id', '<unknown>')}: entrypoint must be a string"
        )

    script_path = check_examples_root / entrypoint
    assert script_path.exists(), f"missing entrypoint: {entrypoint}"
    timeout_seconds = int(example.get("timeout_seconds", 120))

    return subprocess.run(
        [sys.executable, str(script_path)],
        cwd=check_examples_root.parent,
        env=_example_subprocess_env(),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


@pytest.mark.parametrize(
    "example",
    _load_examples(),
    ids=lambda example: str(example["id"]),
)
def test_asset_example_runs_and_writes_declared_outputs(
    example: dict[str, object],
    check_examples_root: Path,
) -> None:
    for key in ("inputs", "assets"):
        for declared_path in _declared_paths(example, key):
            assert (check_examples_root / declared_path.rstrip("/\\")).exists(), (
                f"{example['id']} missing declared {key[:-1]}: {declared_path}"
            )

    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, (
        f"{example['id']} failed with code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    for output in _declared_outputs(example):
        output_path = check_examples_root / output.rstrip("/\\")
        assert output_path.exists(), (
            f"{example['id']} missing declared output: {output}"
        )


def test_schdoc_apply_dynamic_template_inherits_template_sheet_context(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "schdoc_apply_dynamic_template"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumSchDoc, SheetStyle

    output_root = check_examples_root / "schdoc_apply_dynamic_template" / "output"
    expected_sheet_styles = {
        "B": SheetStyle.B,
        "D": SheetStyle.D,
    }
    for sheet_size, sheet_style in expected_sheet_styles.items():
        schdoc = AltiumSchDoc(
            output_root / f"blank_with_dynamic_ansi_{sheet_size}.SchDoc"
        )
        assert schdoc.sheet is not None
        assert schdoc.sheet.sheet_style == int(sheet_style)
        assert schdoc.sheet.use_custom_sheet is False
        assert schdoc.sheet.border_on is True
        assert schdoc.sheet.title_block_on is False
        assert schdoc.sheet.reference_zones_on is True
        assert schdoc.sheet.reference_zone_style == 1
        assert schdoc.sheet.custom_x_zones == 0
        assert schdoc.sheet.custom_y_zones == 0
        assert (
            schdoc.sheet.template_filename
            == f"dynamic_title_block_ansi_{sheet_size}.SchDot"
        )
        system_font = schdoc.font_manager.get_font_info(schdoc.sheet.system_font)
        assert system_font is not None
        assert system_font["name"] == "Arial"
        assert system_font["size"] == 10


def test_prjpcb_make_project_writes_project_container(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "prjpcb_make_project"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumSchDoc, SheetStyle
    from altium_monkey.altium_outjob import AltiumOutJob
    from altium_monkey.altium_prjpcb import AltiumPrjPcb

    project_root = (
        check_examples_root / "prjpcb_make_project" / "output" / "ultra-monkey"
    )
    project_path = project_root / "ultra-monkey.PrjPcb"
    schdoc_path = project_root / "ultra-monkey.SchDoc"
    pcbdoc_path = project_root / "ultra-monkey.PcbDoc"
    outjob_path = project_root / "ultra-monkey.OutJob"

    for path in (project_path, schdoc_path, pcbdoc_path, outjob_path):
        assert path.exists(), path

    schdoc = AltiumSchDoc(schdoc_path)
    assert schdoc.sheet is not None
    assert schdoc.sheet.sheet_style == int(SheetStyle.D)
    assert schdoc.sheet.use_custom_sheet is False
    assert schdoc.sheet.border_on is True
    assert schdoc.sheet.title_block_on is False
    assert schdoc.sheet.reference_zones_on is True
    assert schdoc.sheet.reference_zone_style == 1
    assert schdoc.sheet.template_filename == "generated_ansi_d_title_block.SchDot"
    system_font = schdoc.font_manager.get_font_info(schdoc.sheet.system_font)
    assert system_font is not None
    assert system_font["name"] == "Arial"
    assert system_font["size"] == 10

    project = AltiumPrjPcb(project_path)
    assert project.get_parameter("PROJECT_TITLE") == "ULTRA-MONKEY"
    assert project.get_parameter("CCA_PART_NUMBER") == "10078"
    assert project.get_parameter("PCB_PART_NUMBER") == "10079"
    assert project.get_current_variant() == "A"
    assert "A" in project.variants

    document_names = {Path(doc["path"]).name for doc in project.documents}
    assert {
        "ultra-monkey.SchDoc",
        "ultra-monkey.PcbDoc",
        "ultra-monkey.OutJob",
    }.issubset(document_names)

    assert project.get_outjob_paths() == [outjob_path.resolve()]

    outjob = AltiumOutJob.from_outjob(outjob_path)
    outputs = outjob.get_output_types()
    documentation_outputs = [
        output for output in outputs if output["category"] == "Documentation"
    ]
    assert [output["type"] for output in documentation_outputs] == [
        "Schematic Print"
    ]
    assert "PCBDrawing" not in {output["type"] for output in outputs}

    output_group = outjob.config["OutputGroup1"]
    assert output_group.get("VariantName") == "A"
    output_count = 0
    enabled_count = 0
    index = 1
    while output_group.get(f"OutputType{index}") is not None:
        output_count += 1
        if output_group.get(f"OutputEnabled{index}") == "1":
            enabled_count += 1
        index += 1

    assert output_count >= 10
    assert enabled_count == output_count
    assert outjob.config.get(
        "GeneratedFilesSettings", "RelativeOutputPath1"
    ).startswith("outputs\\")
    assert outjob.config.get("PublishSettings", "OutputBasePath2").startswith(
        "outputs\\"
    )


def test_outjob_runner_prepares_static_rt_super_c1_outjob(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "outjob_runner")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey.altium_prjpcb import AltiumPrjPcb

    source_project_path = (
        check_examples_root
        / "assets"
        / "projects"
        / "rt_super_c1"
        / "RT_SUPER_C1.PrjPcb"
    )
    source_outjob_path = (
        check_examples_root
        / "assets"
        / "projects"
        / "rt_super_c1"
        / "reference_gen.OutJob"
    )
    summary_path = (
        check_examples_root / "outjob_runner" / "output" / "outjob_runner_summary.json"
    )

    source_project = AltiumPrjPcb(source_project_path)
    assert source_project.outjob().path == source_outjob_path.resolve()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["run_requested"] is False
    assert summary["run_status"] == "not_requested"
    assert summary["api_pattern"] == "prj.outjob().run(...)"
    assert summary["current_variant"] == "1v8-2x3USON"
    working_project = check_examples_root / summary["working_project"]
    assert working_project.exists()

    project = AltiumPrjPcb(working_project)
    assert project.outjob().path.name == "reference_gen.OutJob"


def test_schdoc_clean_applies_key_style_rules(check_examples_root: Path) -> None:
    example = next(item for item in _load_examples() if item["id"] == "schdoc_clean")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import (
        AltiumSchArc,
        AltiumSchDoc,
        ColorValue,
        NoErcSymbol,
        SchSheetEntryArrowKind,
    )
    from altium_monkey.altium_record_sch__designator import AltiumSchDesignator
    from altium_monkey.altium_record_sch__parameter import AltiumSchParameter

    output_root = check_examples_root / "schdoc_clean" / "output" / "hydroscope_clean"
    black = ColorValue.from_hex("#000000").win32
    white = ColorValue.from_hex("#FFFFFF").win32
    note_fill = ColorValue.from_hex("#F3F3F3").win32
    counts: dict[str, int] = {
        "component_arcs": 0,
        "designators": 0,
        "component_parameters": 0,
        "net_labels": 0,
        "ports": 0,
        "power_ports": 0,
        "text_strings": 0,
        "notes": 0,
        "no_ercs": 0,
        "signal_harnesses": 0,
        "harness_types": 0,
        "sheet_symbols": 0,
        "sheet_entries": 0,
    }

    module_path = check_examples_root / "schdoc_clean" / "schdoc_clean.py"
    spec = importlib.util.spec_from_file_location("schdoc_clean_example", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._normalize_power_name("+3.3V") == "+3v3"

    def assert_font(
        record_label: str,
        font: object,
        *,
        size: int | None = None,
        bold: bool | None = None,
    ) -> None:
        assert font is not None, f"{record_label}: missing font"
        assert getattr(font, "name") == "Courier New", record_label
        if size is not None:
            assert getattr(font, "size") == size, record_label
        if bold is not None:
            assert getattr(font, "bold") is bold, record_label

    for schdoc_path in sorted(output_root.glob("*.SchDoc")):
        schdoc = AltiumSchDoc(schdoc_path)
        assert schdoc.sheet is not None
        assert schdoc.sheet.area_color == white
        system_font = schdoc.font_manager.get_font_info(schdoc.sheet.system_font)
        assert system_font is not None
        assert system_font["name"] == "Courier New"
        assert system_font["size"] == 10

        for component in schdoc.components:
            for graphic in getattr(component, "graphics", []):
                if isinstance(graphic, AltiumSchArc):
                    counts["component_arcs"] += 1
                    assert graphic.color == black

            for parameter in getattr(component, "parameters", []):
                label = f"{schdoc_path.name}:{getattr(parameter, 'name', '')}"
                if isinstance(parameter, AltiumSchDesignator):
                    counts["designators"] += 1
                    assert_font(label, parameter.font, bold=True)
                    assert parameter.color == black, label
                elif (
                    isinstance(parameter, AltiumSchParameter)
                    and not parameter.is_hidden
                ):
                    counts["component_parameters"] += 1
                    assert_font(label, parameter.font, size=10, bold=False)
                    assert parameter.color == black, label

        for net_label in schdoc.net_labels:
            counts["net_labels"] += 1
            assert "_" not in net_label.text
            assert net_label.color == black
            assert_font(net_label.text, net_label.font, size=9, bold=True)

        for port in schdoc.ports:
            counts["ports"] += 1
            assert "_" not in port.name
            assert "_" not in port.text
            assert "_" not in port.override_display_string
            assert (port.color, port.area_color, port.text_color) == (
                black,
                black,
                white,
            )
            assert_font(port.name, port.font, size=10, bold=True)

        for power_port in schdoc.power_ports:
            counts["power_ports"] += 1
            assert "_" not in power_port.text
            assert "_" not in power_port.override_display_string
            assert "." not in power_port.text
            assert "." not in power_port.override_display_string
            assert power_port.color == black
            assert_font(power_port.text, power_port.font, size=10, bold=True)

        for text_string in schdoc.text_strings:
            counts["text_strings"] += 1
            assert_font(text_string.text, text_string.font)

        for note in schdoc.notes:
            counts["notes"] += 1
            assert note.area_color == note_fill
            assert note.is_solid is True
            assert_font(f"{schdoc_path.name}:note", note.font, size=10)

        for no_erc in schdoc.no_ercs:
            counts["no_ercs"] += 1
            assert no_erc.color == black
            assert no_erc.symbol == NoErcSymbol.CROSS_SMALL

        for signal_harness in schdoc.signal_harnesses:
            counts["signal_harnesses"] += 1
            assert signal_harness.color == black

        for harness_type in schdoc.harness_types:
            counts["harness_types"] += 1
            assert_font(harness_type.text, harness_type.font, size=10, bold=True)
            assert harness_type.color in (None, black)

        for sheet_symbol in schdoc.sheet_symbols:
            counts["sheet_symbols"] += 1
            assert sheet_symbol.area_color == white
            for entry in getattr(sheet_symbol, "entries", []):
                counts["sheet_entries"] += 1
                assert "_" not in entry.name
                assert (entry.color, entry.area_color, entry.text_color) == (
                    black,
                    black,
                    black,
                )
                assert entry.arrow_kind == SchSheetEntryArrowKind.TRIANGLE.value
                assert_font(entry.name, entry.font, size=10, bold=True)

    assert all(value > 0 for value in counts.values()), counts


def test_schdoc_svg_writes_project_page_svgs(check_examples_root: Path) -> None:
    example = next(item for item in _load_examples() if item["id"] == "schdoc_svg")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    manifest_path = check_examples_root / "schdoc_svg" / "output" / "svg_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schematic_documents"] == [
        "CPU.SchDoc",
        "POWER_SUPPLY.SchDoc",
        "US_IF.SchDoc",
        "TOP_LEVEL.SchDoc",
    ]
    assert manifest["variant_handling"] == "not_applied"
    assert manifest["hierarchical_channel_handling"] == "not_applied"
    assert "VariantName" not in manifest["project_parameters"]
    assert "PROJECT_TITLE_LINE_1" in manifest["project_parameters"]
    assert manifest["svg_count"] == len(manifest["schematic_documents"])

    svgs = manifest["svgs"]
    assert len(svgs) == len(manifest["schematic_documents"])
    for svg_entry in svgs:
        svg_path = check_examples_root / "schdoc_svg" / svg_entry["svg"]
        assert svg_path.exists()
        assert svg_path.stat().st_size == svg_entry["byte_count"]
        assert "<svg" in svg_path.read_text(encoding="utf-8")[:200]


def test_schlib_svg_writes_symbol_part_svgs(check_examples_root: Path) -> None:
    example = next(item for item in _load_examples() if item["id"] == "schlib_svg")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    manifest_path = (
        check_examples_root / "schlib_svg" / "output" / ("schlib_svg_manifest.json")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_schdoc"] == "assets/projects/rt_super_c1/RT_SUPER_C1.SchDoc"
    assert manifest["combined_schlib"] == "output/combined/RT_SUPER_C1.SchLib"
    assert manifest["symbol_count"] >= 20
    assert manifest["svg_count"] > manifest["symbol_count"]

    symbols = manifest["symbols"]
    rt685 = next(symbol for symbol in symbols if symbol["name"] == "MIMXRT685SFVKB")
    assert rt685["part_count"] == 8
    assert [part["part_id"] for part in rt685["parts"]] == list(range(1, 9))

    combined_path = check_examples_root / "schlib_svg" / manifest["combined_schlib"]
    assert combined_path.exists()

    for symbol in symbols:
        for svg_entry in symbol["parts"]:
            svg_path = check_examples_root / "schlib_svg" / svg_entry["svg"]
            assert svg_path.exists()
            assert svg_path.stat().st_size == svg_entry["byte_count"]
            assert "<svg" in svg_path.read_text(encoding="utf-8")[:200]


def test_schlib_library_examples_write_parseable_outputs(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumSchLib

    find_example = next(
        item for item in _load_examples() if item["id"] == "schlib_find_symbol"
    )
    find_result = _run_example_entrypoint(find_example, check_examples_root)
    assert find_result.returncode == 0, find_result.stderr
    assert "Indexed" in find_result.stdout

    find_manifest_path = (
        check_examples_root / "schlib_find_symbol" / "output" / "symbol_index.json"
    )
    find_manifest = json.loads(find_manifest_path.read_text(encoding="utf-8"))
    assert find_manifest["schlib_dir"] == "assets/schlib"
    assert find_manifest["library_count"] >= 50
    assert find_manifest["symbol_count"] >= 80
    assert "RT_SUPER_C1.SCHLIB" in find_manifest["libraries"]
    assert find_manifest["libraries"]["Bugsly.SchLib"] == ["Bugsly"]
    assert "MIMXRT685SFVKB" in find_manifest["libraries"]["RT_SUPER_C1.SCHLIB"]
    assert find_manifest["queries"]["rt685"][0]["symbol"] == "MIMXRT685SFVKB"

    split_example = next(
        item for item in _load_examples() if item["id"] == "schlib_split"
    )
    split_result = _run_example_entrypoint(split_example, check_examples_root)
    assert split_result.returncode == 0, split_result.stderr
    assert "Symbols split: 29" in split_result.stdout

    split_manifest_path = (
        check_examples_root / "schlib_split" / "output" / "schlib_split_manifest.json"
    )
    split_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
    assert split_manifest["input_schlib"] == "assets/schlib/RT_SUPER_C1.SCHLIB"
    assert split_manifest["symbol_count"] == 29
    symbols_by_name = {symbol["name"]: symbol for symbol in split_manifest["symbols"]}
    assert {"MIMXRT685SFVKB", "PCA9420BSAZ", "W25Q64JWSSIQ"}.issubset(symbols_by_name)
    assert symbols_by_name["MIMXRT685SFVKB"]["part_count"] == 8

    for symbol in split_manifest["symbols"]:
        split_path = check_examples_root / "schlib_split" / symbol["split_schlib"]
        assert split_path.exists()
        assert split_path.stat().st_size == symbol["byte_count"]
        split_schlib = AltiumSchLib(split_path)
        assert len(split_schlib.symbols) == 1
        assert split_schlib.symbols[0].name == symbol["name"]

    merge_example = next(
        item for item in _load_examples() if item["id"] == "schlib_merge"
    )
    merge_result = _run_example_entrypoint(merge_example, check_examples_root)
    assert merge_result.returncode == 0, merge_result.stderr
    assert "Merged symbols:" in merge_result.stdout

    merge_manifest_path = (
        check_examples_root / "schlib_merge" / "output" / "schlib_merge_manifest.json"
    )
    merge_manifest = json.loads(merge_manifest_path.read_text(encoding="utf-8"))
    assert merge_manifest["input_schlib_dir"] == "assets/schlib"
    assert merge_manifest["input_library_count"] == find_manifest["library_count"]
    assert merge_manifest["input_symbol_count"] == find_manifest["symbol_count"]
    assert merge_manifest["merged_symbol_count"] == merge_manifest["input_symbol_count"]
    assert {"MIMXRT685SFVKB", "R_2P", "L_2P"}.issubset(
        set(merge_manifest["merged_symbols"])
    )

    merged_path = check_examples_root / "schlib_merge" / merge_manifest["merged_schlib"]
    assert merged_path.exists()
    assert merged_path.stat().st_size == merge_manifest["merged_byte_count"]
    merged_schlib = AltiumSchLib(merged_path)
    assert len(merged_schlib.symbols) == merge_manifest["merged_symbol_count"]


def test_pcbdoc_stats_reports_loz_old_man_board_metrics(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "pcbdoc_stats")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Drill Size Table" in result.stdout
    assert "Minimum Copper Track/Arc Widths" in result.stdout
    assert "ASCII Stack" in result.stdout
    assert "Total thickness" in result.stdout
    assert "copper wt" in result.stdout

    summary_path = check_examples_root / "pcbdoc_stats" / "output" / "pcbdoc_stats.json"
    text_path = check_examples_root / "pcbdoc_stats" / "output" / "pcbdoc_stats.txt"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    text_report = text_path.read_text(encoding="utf-8")

    assert summary["project"] == "loz-old-man.PrjPcb"
    assert summary["pcbdoc"] == "SB0037A.PcbDoc"
    assert "Loaded PCB: SB0037A.PcbDoc" in text_report
    assert "Minimum Copper Track/Arc Widths" in text_report
    assert "Total thickness: 31.068 mil (0.789 mm)" in text_report
    assert summary["board_outline"]["width_mils"] == pytest.approx(2872.5)
    assert summary["board_outline"]["height_mils"] == pytest.approx(4560.0)
    assert summary["holes"]["plated_through_holes"] == 749
    assert summary["holes"]["non_plated_pad_holes"] == 23
    assert summary["holes"]["plated_slotted_pad_holes"] == 2
    assert summary["holes"]["non_plated_slotted_pad_holes"] == 0
    assert summary["drill_table"] == [
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 10.0,
            "diameter_mm": pytest.approx(0.254),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 0,
            "via_count": 490,
            "total_count": 490,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 15.0,
            "diameter_mm": pytest.approx(0.381),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 0,
            "via_count": 197,
            "total_count": 197,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 31.0,
            "diameter_mm": pytest.approx(0.7874),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 20,
            "via_count": 0,
            "total_count": 20,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 39.3701,
            "diameter_mm": pytest.approx(1.000001),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 22,
            "via_count": 0,
            "total_count": 22,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 40.0,
            "diameter_mm": pytest.approx(1.016),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 4,
            "via_count": 0,
            "total_count": 4,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 55.1181,
            "diameter_mm": pytest.approx(1.400),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 9,
            "via_count": 0,
            "total_count": 9,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 92.0,
            "diameter_mm": pytest.approx(2.3368),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 2,
            "via_count": 0,
            "total_count": 2,
        },
        {
            "plating": "plated",
            "type": "round",
            "diameter_mils": 118.0,
            "diameter_mm": pytest.approx(2.9972),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 3,
            "via_count": 0,
            "total_count": 3,
        },
        {
            "plating": "plated",
            "type": "slot",
            "diameter_mils": 62.9921,
            "diameter_mm": pytest.approx(1.6),
            "slot_length_mils": 118.1102,
            "slot_length_mm": pytest.approx(3.0),
            "pad_count": 2,
            "via_count": 0,
            "total_count": 2,
        },
        {
            "plating": "non-plated",
            "type": "round",
            "diameter_mils": 35.0,
            "diameter_mm": pytest.approx(0.889),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 6,
            "via_count": 0,
            "total_count": 6,
        },
        {
            "plating": "non-plated",
            "type": "round",
            "diameter_mils": 39.0,
            "diameter_mm": pytest.approx(0.9906),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 3,
            "via_count": 0,
            "total_count": 3,
        },
        {
            "plating": "non-plated",
            "type": "round",
            "diameter_mils": 40.0,
            "diameter_mm": pytest.approx(1.016),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 2,
            "via_count": 0,
            "total_count": 2,
        },
        {
            "plating": "non-plated",
            "type": "round",
            "diameter_mils": 93.0,
            "diameter_mm": pytest.approx(2.3622),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 8,
            "via_count": 0,
            "total_count": 8,
        },
        {
            "plating": "non-plated",
            "type": "round",
            "diameter_mils": 95.0,
            "diameter_mm": pytest.approx(2.413),
            "slot_length_mils": None,
            "slot_length_mm": None,
            "pad_count": 4,
            "via_count": 0,
            "total_count": 4,
        },
    ]
    assert summary["minimum_widths"]["track_mils"] == pytest.approx(5.0)
    assert summary["minimum_widths"]["arc_mils"] is None
    assert summary["minimum_widths"]["overall_track_or_arc_mils"] == pytest.approx(5.0)
    assert summary["layers"]["physical_stack_layer_count"] == 17
    assert summary["layers"]["copper_layer_count"] == 6
    assert summary["layers"]["total_thickness_mils"] == pytest.approx(31.068)
    assert summary["layers"]["total_thickness_mm"] == pytest.approx(0.789127)
    assert (
        summary["layers"]["resolved_layer_count"]
        >= summary["layers"]["physical_stack_layer_count"]
    )
    physical_stack_by_name = {item["name"]: item for item in summary["physical_stack"]}
    assert physical_stack_by_name["Top Layer"]["copper_weight_oz"] == pytest.approx(1.0)
    assert physical_stack_by_name["L2"]["copper_weight_oz"] == pytest.approx(1.0)
    assert physical_stack_by_name["PP1"]["dk"] == pytest.approx(4.1)
    assert physical_stack_by_name["PP1"]["df"] == pytest.approx(0.02)
    assert physical_stack_by_name["CORE1"]["dk"] == pytest.approx(4.1)
    assert physical_stack_by_name["CORE1"]["df"] == pytest.approx(0.02)
    assert physical_stack_by_name["Top Solder"]["dk"] == pytest.approx(3.5)


def test_pcbdoc_bom_writes_resolved_component_rows(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "pcbdoc_bom")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "PCB components:" in result.stdout
    assert "BOM groups:" in result.stdout

    output_root = check_examples_root / "pcbdoc_bom" / "output"
    all_components = json.loads(
        (output_root / "pcbdoc_components.json").read_text(encoding="utf-8")
    )
    bom = json.loads((output_root / "pcbdoc_bom.json").read_text(encoding="utf-8"))

    assert all_components["source"]["component_source"] == (
        "PcbDoc Components6/Data with resolved designator text"
    )
    assert all_components["component_count"] > bom["component_count"] > 0
    assert all_components["skipped_no_bom_component_count"] > 0
    assert bom["group_count"] > 0

    rows_by_designator = {
        row["designator"]: row for row in all_components["components"]
    }
    assert {"U6", "R23", "T58"}.issubset(rows_by_designator)
    assert rows_by_designator["U6"]["footprint"] == "SOIC127P830X200-8L"
    assert rows_by_designator["U6"]["manufacturer"] == "Winbond"
    assert rows_by_designator["U6"]["include_in_bom"] is True
    assert rows_by_designator["T58"]["component_kind"] == "standard_no_bom"
    assert rows_by_designator["T58"]["include_in_bom"] is False

    bom_designators = {
        designator for group in bom["groups"] for designator in group["designators"]
    }
    assert "U6" in bom_designators
    assert "T58" not in bom_designators


def test_pcbdoc_svg_writes_board_outline_and_layer_svgs(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "pcbdoc_svg")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Layer SVG files written" in result.stdout

    manifest_path = (
        check_examples_root / "pcbdoc_svg" / "output" / "pcbdoc_svg_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["project"] == "assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb"
    assert manifest["pcbdoc"] == "assets/projects/rt_super_c1/RT_SUPER_C1.PCBdoc"
    assert manifest["scale_factor"] == pytest.approx(10.0)
    assert manifest["render_style"] == {
        "copper_layer_color": "#000000",
        "drill_hole_color": "#00A000",
        "drill_hole_mode": "overlay",
    }
    assert manifest["selector_demo"]["resolved_key"] == "TOP"
    assert manifest["selector_demo"]["resolved_legacy_id"] == 1
    assert manifest["keepout"]["selector_resolves_to"] == "KEEPOUT"
    assert manifest["keepout"]["selector_legacy_id"] == 56
    assert manifest["keepout"]["written_for_this_board"] is True

    board_outline_path = (
        check_examples_root / "pcbdoc_svg" / manifest["board_outline_svg"]
    )
    assert board_outline_path.exists()
    board_outline_text = board_outline_path.read_text(encoding="utf-8")
    assert "<svg" in board_outline_text[:200]
    assert 'data-view-kind="board_outline_only"' in board_outline_text

    layers = manifest["layers"]
    assert manifest["layer_count"] == len(layers)
    assert len(layers) >= 10
    layers_by_key = {layer["key"]: layer for layer in layers}
    assert layers_by_key["TOP"]["display_name"] == "Top Layer"
    assert layers_by_key["TOP"]["legacy_id"] == 1
    assert layers_by_key["MECHANICAL8"]["display_name"] == "[8] Board Outline"
    assert layers_by_key["DRILLS"]["display_name"] == "Drill Holes"
    assert layers_by_key["KEEPOUT"]["display_name"] == "Keep-Out Layer"
    assert layers_by_key["KEEPOUT"]["legacy_id"] == 56

    for layer in layers:
        svg_path = check_examples_root / "pcbdoc_svg" / layer["svg"]
        assert svg_path.exists()
        assert svg_path.stat().st_size == layer["byte_count"]
        svg_text = svg_path.read_text(encoding="utf-8")
        assert "<svg" in svg_text[:200]
        assert "data-view-kind=" in svg_text
        assert '"includes_board_outline":true' in svg_text

    top_svg_path = check_examples_root / "pcbdoc_svg" / layers_by_key["TOP"]["svg"]
    top_svg_text = top_svg_path.read_text(encoding="utf-8")
    assert 'data-color="#000000"' in top_svg_text

    drills_svg_path = (
        check_examples_root / "pcbdoc_svg" / layers_by_key["DRILLS"]["svg"]
    )
    drills_svg_text = drills_svg_path.read_text(encoding="utf-8")
    assert "#00A000" in drills_svg_text

    keepout_svg_path = (
        check_examples_root / "pcbdoc_svg" / layers_by_key["KEEPOUT"]["svg"]
    )
    keepout_svg_text = keepout_svg_path.read_text(encoding="utf-8")
    assert 'id="layer-KEEPOUT"' in keepout_svg_text
    assert 'data-primitive="track"' in keepout_svg_text


def test_pcbdoc_netclass_svg_writes_highlighted_html_reviews(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_netclass_svg"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Net classes rendered: 2" in result.stdout

    manifest_path = (
        check_examples_root
        / "pcbdoc_netclass_svg"
        / "output"
        / "pcbdoc_netclass_svg_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["project"] == "assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb"
    assert manifest["pcbdoc"] == "assets/projects/rt_super_c1/RT_SUPER_C1.PCBdoc"
    assert manifest["scale_factor"] == pytest.approx(10.0)
    assert manifest["minimum_routing_length_mils"] == pytest.approx(10.0)
    assert manifest["render_style"]["routing_grey"] == "#B8B8B8"
    assert manifest["render_style"]["pad_via_black"] == "#000000"
    assert manifest["render_style"]["highlight_red"] == "#D00000"

    classes_by_name = {item["name"]: item for item in manifest["net_classes"]}
    assert sorted(classes_by_name) == ["90-OHM", "SDIO"]
    assert classes_by_name["90-OHM"]["members"] == ["USB_D_P", "USB_D_N"]
    assert classes_by_name["SDIO"]["members"] == [
        "SD-D3",
        "SD-D2",
        "SD-D1",
        "SD-D0",
        "SD-CMD",
        "SD-CLK",
    ]

    assert [item["key"] for item in classes_by_name["90-OHM"]["layers"]] == ["TOP"]
    assert [item["key"] for item in classes_by_name["SDIO"]["layers"]] == [
        "TOP",
        "BOTTOM",
    ]

    sdio_has_red_via_hole = False
    for net_class in classes_by_name.values():
        html_path = check_examples_root / "pcbdoc_netclass_svg" / net_class["html"]
        html_text = html_path.read_text(encoding="utf-8")
        assert f"<h1>{net_class['name']}</h1>" in html_text
        assert "<svg" in html_text

        for layer in net_class["layers"]:
            svg_path = check_examples_root / "pcbdoc_netclass_svg" / layer["svg"]
            svg_text = svg_path.read_text(encoding="utf-8")
            assert svg_path.stat().st_size == layer["byte_count"]
            assert "<svg" in svg_text[:200]
            assert '"includes_board_outline":true' in svg_text
            assert 'data-primitive="track"' in svg_text
            assert "#D00000" in svg_text
            assert 'data-net-classes="' in svg_text
            assert 'data-primitive="pad"' in svg_text
            assert 'fill="#000000"' in svg_text
            assert "MULTILAYER" not in layer["key"]

            track_index = svg_text.find('data-primitive="track"')
            pad_index = svg_text.find('data-primitive="pad"')
            region_index = svg_text.find('data-primitive="shapebased-region"')
            if track_index >= 0 and pad_index >= 0:
                assert track_index < pad_index
            if track_index >= 0 and region_index >= 0:
                assert track_index < region_index

            if (
                net_class["name"] == "SDIO"
                and 'data-primitive="via-hole"' in svg_text
                and 'fill="#D00000"' in svg_text
            ):
                sdio_has_red_via_hole = True

    assert sdio_has_red_via_hole


def test_pcbdoc_extraction_examples_write_parseable_assets(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbLib

    pcblib_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_extract_pcblib"
    )
    pcblib_result = _run_example_entrypoint(pcblib_example, check_examples_root)
    assert pcblib_result.returncode == 0, pcblib_result.stderr
    assert "Extracted footprints: 27" in pcblib_result.stdout

    pcblib_root = check_examples_root / "pcbdoc_extract_pcblib"
    pcblib_manifest = json.loads(
        (pcblib_root / "output" / "pcblib_extraction_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert pcblib_manifest["project"] == (
        "assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb"
    )
    assert pcblib_manifest["pcbdoc"] == (
        "assets/projects/rt_super_c1/RT_SUPER_C1.PCBdoc"
    )
    assert pcblib_manifest["footprint_count"] == 27

    combined_path = pcblib_root / pcblib_manifest["combined_pcblib"]
    combined = AltiumPcbLib.from_file(combined_path)
    assert len(combined.footprints) == pcblib_manifest["footprint_count"]
    footprint_names = {footprint.name for footprint in combined.footprints}
    assert {"R0402_0.40MM_HD", "PCA9420", "VFBGA176"}.issubset(footprint_names)

    for footprint_entry in pcblib_manifest["footprints"]:
        split_path = pcblib_root / footprint_entry["split_pcblib"]
        split_pcblib = AltiumPcbLib.from_file(split_path)
        assert split_pcblib.footprints[0].name == footprint_entry["name"]

    fonts_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_extract_embedded_fonts"
    )
    fonts_result = _run_example_entrypoint(fonts_example, check_examples_root)
    assert fonts_result.returncode == 0, fonts_result.stderr
    assert "Extracted embedded fonts: 5" in fonts_result.stdout

    fonts_root = check_examples_root / "pcbdoc_extract_embedded_fonts"
    fonts_manifest = json.loads(
        (fonts_root / "output" / "fonts.json").read_text(encoding="utf-8")
    )
    assert fonts_manifest["font_count"] == 5
    assert {font["source_filename"] for font in fonts_manifest["fonts"]} == {
        "Arial.ttf",
        "Arial Bold.ttf",
        "Monkey.ttf",
        "SNES Italic.ttf",
        "Wavenumber.ttf",
    }
    for font in fonts_manifest["fonts"]:
        font_path = fonts_root / font["path"]
        assert font_path.exists()
        assert font_path.stat().st_size == font["byte_count"]
        assert font_path.read_bytes()[:4] in (b"\x00\x01\x00\x00", b"OTTO")

    models_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_extract_embedded_3d_models"
    )
    models_result = _run_example_entrypoint(models_example, check_examples_root)
    assert models_result.returncode == 0, models_result.stderr
    assert "Extracted embedded 3D models: 24" in models_result.stdout

    models_root = check_examples_root / "pcbdoc_extract_embedded_3d_models"
    models_manifest = json.loads(
        (models_root / "output" / "models.json").read_text(encoding="utf-8")
    )
    assert models_manifest["model_count"] == 24
    assert len(models_manifest["models"]) == models_manifest["model_count"]
    for model in models_manifest["models"]:
        model_path = models_root / model["path"]
        assert model_path.exists()
        assert model_path.stat().st_size == model["byte_count"]
        assert model_path.read_text(encoding="utf-8", errors="ignore").startswith(
            "ISO-10303-21;"
        )


def test_pcbdoc_add_primitive_examples_write_expected_records(
    check_examples_root: Path,
) -> None:
    from altium_monkey import (
        AltiumPcbDoc,
        PadShape,
        PcbBarcodeKind,
        PcbBarcodeRenderMode,
        PcbLayer,
        PcbTextJustification,
    )

    for example_id in (
        "pcbdoc_add_text",
        "pcbdoc_add_track",
        "pcbdoc_add_arc",
        "pcbdoc_add_pad",
        "pcbdoc_add_filled_region",
    ):
        example = next(item for item in _load_examples() if item["id"] == example_id)
        result = _run_example_entrypoint(example, check_examples_root)
        assert result.returncode == 0, result.stderr

    def assert_demo_board_outline(pcbdoc: AltiumPcbDoc) -> None:
        assert pcbdoc.board is not None
        assert pcbdoc.board.outline is not None
        assert pcbdoc.board.outline.bounding_box == pytest.approx(
            (0.0, 0.0, 6000.0, 6000.0)
        )

    text_doc = AltiumPcbDoc.from_file(
        check_examples_root / "pcbdoc_add_text" / "output" / "pcbdoc_add_text.PcbDoc"
    )
    assert_demo_board_outline(text_doc)
    texts_by_content = {text.text_content: text for text in text_doc.texts}
    assert len(texts_by_content) == 10
    assert texts_by_content["Stroke font"].font_type == 0
    assert texts_by_content["TrueType Arial Bold Italic"].font_type == 1
    assert texts_by_content["TrueType Arial Bold Italic"].is_bold is True
    assert texts_by_content["TrueType Arial Bold Italic"].is_italic is True
    assert texts_by_content["Mirrored bottom text"].is_mirrored is True
    assert texts_by_content["INV margin 10"].is_inverted is True
    assert texts_by_content["INV margin 10"].margin_border_width == 100000
    assert texts_by_content["INV rect centered"].use_inverted_rectangle is True
    assert (
        texts_by_content["INV rect centered"].textbox_rect_justification
        == PcbTextJustification.CENTER_CENTER
    )
    multiline_frame = texts_by_content[
        "Multiline text frame\r\nLine two inside frame\r\nLine three"
    ]
    assert multiline_frame.is_frame is True
    assert multiline_frame.font_type == 1
    assert multiline_frame.textbox_rect_width == 20000000
    assert multiline_frame.textbox_rect_height == 9000000
    assert multiline_frame.textbox_rect_justification == PcbTextJustification.LEFT_TOP
    assert texts_by_content["AM-12345"].font_type == 2
    assert texts_by_content["AM-12345"]._font_type_offset43 == 0
    assert texts_by_content["AM-12345"].barcode_kind == PcbBarcodeKind.CODE_39
    assert texts_by_content["AM-12345"].barcode_show_text is True
    assert texts_by_content["AM-12345"].barcode_min_width == 100775
    assert texts_by_content["12345678"].barcode_kind == PcbBarcodeKind.CODE_128
    assert texts_by_content["12345678"]._font_type_offset43 == 0
    assert (
        texts_by_content["12345678"].barcode_render_mode
        == PcbBarcodeRenderMode.BY_MIN_WIDTH
    )
    assert texts_by_content["12345678"].barcode_full_width == 2370000
    assert texts_by_content["12345678"].barcode_show_text is False
    assert texts_by_content["LOT-2026-04"].font_type == 2
    assert texts_by_content["LOT-2026-04"]._font_type_offset43 == 0
    assert texts_by_content["LOT-2026-04"].barcode_inverted is True
    assert texts_by_content["LOT-2026-04"].barcode_min_width == 115385

    track_doc = AltiumPcbDoc.from_file(
        check_examples_root / "pcbdoc_add_track" / "output" / "pcbdoc_add_track.PcbDoc"
    )
    assert_demo_board_outline(track_doc)
    assert len(track_doc.tracks) == 9
    assert {round(track.width / 10000.0, 4) for track in track_doc.tracks} == {
        6.0,
        8.0,
        10.0,
        12.0,
        24.0,
        40.0,
    }
    assert {track.layer for track in track_doc.tracks} == {
        PcbLayer.TOP,
        PcbLayer.BOTTOM,
        PcbLayer.TOP_OVERLAY,
        PcbLayer.MECHANICAL_1,
        PcbLayer.KEEPOUT,
    }
    assert {net.name for net in track_doc.nets} == {
        "TRACE_TOP_6MIL",
        "TRACE_TOP_12MIL",
        "TRACE_BOTTOM_24MIL",
        "TRACE_POLYLINE",
    }

    arc_doc = AltiumPcbDoc.from_file(
        check_examples_root / "pcbdoc_add_arc" / "output" / "pcbdoc_add_arc.PcbDoc"
    )
    assert_demo_board_outline(arc_doc)
    assert len(arc_doc.arcs) == 14
    assert sum(1 for arc in arc_doc.arcs if arc.start_angle == 0.0) >= 2
    assert any(arc.end_angle == 45.0 for arc in arc_doc.arcs)
    assert {net.name for net in arc_doc.nets} == {
        "ARC_0_90",
        "ARC_90_180",
        "ARC_180_270",
        "ARC_WRAP",
    }

    pad_doc = AltiumPcbDoc.from_file(
        check_examples_root / "pcbdoc_add_pad" / "output" / "pcbdoc_add_pad.PcbDoc"
    )
    assert_demo_board_outline(pad_doc)
    pads_by_designator = {pad.designator: pad for pad in pad_doc.pads}
    assert len(pads_by_designator) == 8
    assert pads_by_designator["SMT1"].shape == PadShape.RECTANGLE
    assert pads_by_designator["SMT2"].shape == PadShape.CIRCLE
    assert pads_by_designator["SMT3"].shape == PadShape.CIRCLE
    assert pads_by_designator["SMT3"].effective_top_shape == PadShape.ROUNDED_RECTANGLE
    assert pads_by_designator["SMT3"].alt_shape[0] == 9
    assert pads_by_designator["SMT3"].corner_radius_percentage == 50
    assert pads_by_designator["BOT1"].layer == PcbLayer.BOTTOM
    assert pads_by_designator["TH1"].is_plated is True
    assert pads_by_designator["TH1"].hole_size == 400000
    assert pads_by_designator["NPTH"].is_plated is False
    assert pads_by_designator["NPTH"].hole_size == 700000
    assert pads_by_designator["NPTH"].soldermask_expansion_mode == 2
    assert pads_by_designator["NPTH"].soldermask_expansion_manual == 0
    assert pads_by_designator["NPTH"].pastemask_expansion_mode == 2
    assert pads_by_designator["NPTH"].pastemask_expansion_manual == 0
    assert pads_by_designator["SLOT"].is_plated is True
    assert pads_by_designator["SLOT"].hole_shape == 2
    assert pads_by_designator["SLOT"].hole_size == 450000
    assert pads_by_designator["SLOT"].slot_size == 1500000
    assert pads_by_designator["SLOT"].slot_rotation == pytest.approx(90.0)

    region_doc = AltiumPcbDoc.from_file(
        check_examples_root
        / "pcbdoc_add_filled_region"
        / "output"
        / "pcbdoc_add_filled_region.PcbDoc"
    )
    assert_demo_board_outline(region_doc)
    assert len(region_doc.regions) == 4
    assert len(region_doc.shapebased_regions) == 4
    assert any(region.hole_count == 1 for region in region_doc.regions)
    keepout = next(region for region in region_doc.regions if region.is_keepout)
    assert keepout.layer == PcbLayer.KEEPOUT
    assert keepout.keepout_restrictions == 31


def test_pcbdoc_insert_nets_route_writes_routed_board(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_insert_nets_route"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbDoc

    output_path = (
        check_examples_root
        / "pcbdoc_insert_nets_route"
        / "output"
        / "pcbdoc_insert_nets_route.PcbDoc"
    )
    pcbdoc = AltiumPcbDoc.from_file(output_path)

    assert {component.designator for component in pcbdoc.components} == {"J1", "R1"}
    nets_by_name = {net.name: index for index, net in enumerate(pcbdoc.nets)}
    assert {"VIN", "SENSE", "GND"}.issubset(nets_by_name)
    assert len(pcbdoc.tracks) >= 5
    assert len(pcbdoc.vias) == 1

    routed_net_indices = {track.net_index for track in pcbdoc.tracks}
    routed_net_indices.add(pcbdoc.vias[0].net_index)
    assert nets_by_name["VIN"] in routed_net_indices
    assert nets_by_name["SENSE"] in routed_net_indices
    assert nets_by_name["GND"] in routed_net_indices


def test_pcbdoc_free_3d_examples_write_component_bodies(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbDoc

    extruded_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_add_free_3d_extruded"
    )
    extruded_result = _run_example_entrypoint(extruded_example, check_examples_root)
    assert extruded_result.returncode == 0, extruded_result.stderr

    extruded_path = (
        check_examples_root
        / "pcbdoc_add_free_3d_extruded"
        / "output"
        / "pcbdoc_add_free_3d_extruded.PcbDoc"
    )
    extruded = AltiumPcbDoc.from_file(extruded_path)
    assert len(extruded.models) == 0
    assert len(extruded.component_bodies) == 1
    assert len(extruded.shapebased_component_bodies) == 1
    extruded_body = extruded.component_bodies[0]
    assert int(extruded_body.model_type) == 0
    assert extruded_body.model_name == ""
    assert round(extruded_body.standoff_height / 10000.0, 4) == 20.0
    assert round(extruded_body.overall_height / 10000.0, 4) == 180.0
    assert round(extruded_body.model_extruded_min_z / 10000.0, 4) == 20.0
    assert round(extruded_body.model_extruded_max_z / 10000.0, 4) == 180.0
    assert len(extruded_body.outline) == 6

    step_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_add_free_3d_step"
    )
    step_result = _run_example_entrypoint(step_example, check_examples_root)
    assert step_result.returncode == 0, step_result.stderr

    step_path = (
        check_examples_root
        / "pcbdoc_add_free_3d_step"
        / "output"
        / "pcbdoc_add_free_3d_step.PcbDoc"
    )
    step_board = AltiumPcbDoc.from_file(step_path)
    assert len(step_board.models) == 1
    assert len(step_board.component_bodies) == 1
    assert len(step_board.shapebased_component_bodies) == 1
    step_body = step_board.component_bodies[0]
    assert step_body.model_is_embedded is True
    assert step_body.model_name == "RESC1608X06N.step"
    assert step_body.model_id == step_board.models[0].id
    assert (
        int(step_body.model_checksum) == int(step_board.models[0].checksum) & 0xFFFFFFFF
    )
    assert len(step_body.outline) == 4


def test_pcblib_footprint_svg_writes_footprint_svgs(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "pcblib_footprint_svg"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Footprints rendered: 23" in result.stdout

    manifest_path = (
        check_examples_root
        / "pcblib_footprint_svg"
        / "output"
        / "pcblib_footprint_svg_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["input_pcblib"] == "assets/pcblib/RT_SUPER_C1.PcbLib"
    assert manifest["scale_factor"] == pytest.approx(10.0)
    assert manifest["footprint_count"] >= 20
    assert manifest["svg_count"] > manifest["footprint_count"]

    footprints_by_name = {
        footprint["name"]: footprint for footprint in manifest["footprints"]
    }
    assert {"R0402_0.40MM_HD", "PCA9420", "VFBGA176"}.issubset(footprints_by_name)
    footprint = footprints_by_name["R0402_0.40MM_HD"]
    assert footprint["pads"] >= 2

    composed_svg = (
        check_examples_root / "pcblib_footprint_svg" / footprint["composed_svg"]
    )
    composed_text = composed_svg.read_text(encoding="utf-8")
    assert composed_svg.stat().st_size == footprint["composed_byte_count"]
    assert "<svg" in composed_text[:200]
    assert "<metadata" not in composed_text
    assert "pcb-enrichment-a0" not in composed_text

    layer_names = {layer["layer"] for layer in footprint["layers"]}
    assert "TOP" in layer_names
    assert "TOPSOLDER" in layer_names
    for layer in footprint["layers"]:
        layer_svg = check_examples_root / "pcblib_footprint_svg" / layer["svg"]
        layer_text = layer_svg.read_text(encoding="utf-8")
        assert layer_svg.stat().st_size == layer["byte_count"]
        assert "<svg" in layer_text[:200]
        assert "<metadata" not in layer_text
        assert "pcb-enrichment-a0" not in layer_text


def test_pcblib_split_writes_parseable_single_footprint_libraries(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "pcblib_split")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Footprints split: 23" in result.stdout

    from altium_monkey import AltiumPcbLib

    manifest_path = (
        check_examples_root / "pcblib_split" / "output" / "pcblib_split_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["input_pcblib"] == "assets/pcblib/RT_SUPER_C1.PcbLib"
    assert manifest["footprint_count"] == 23

    footprints_by_name = {
        footprint["name"]: footprint for footprint in manifest["footprints"]
    }
    assert {"R0402_0.40MM_HD", "PCA9420", "VFBGA176"}.issubset(footprints_by_name)

    for footprint in manifest["footprints"]:
        split_path = check_examples_root / "pcblib_split" / footprint["split_pcblib"]
        assert split_path.exists()
        assert split_path.stat().st_size == footprint["byte_count"]
        split_lib = AltiumPcbLib.from_file(split_path)
        assert len(split_lib.footprints) == 1
        assert split_lib.footprints[0].name == footprint["name"]


def test_pcblib_add_free_3d_extruded_writes_component_body(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "pcblib_add_free_3d_extruded"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbLib

    output_path = (
        check_examples_root
        / "pcblib_add_free_3d_extruded"
        / "output"
        / "pcblib_add_free_3d_extruded.PcbLib"
    )
    pcblib = AltiumPcbLib.from_file(output_path)
    assert len(pcblib.footprints) == 1

    footprint = pcblib.footprints[0]
    assert footprint.name == "FREE_EXTRUDED_BODY_DEMO"
    assert len(footprint.pads) == 2
    assert len(footprint.tracks) == 2
    assert len(footprint.component_bodies) == 1
    assert len(pcblib.get_embedded_model_entries()) == 0

    body = footprint.component_bodies[0]
    assert int(body.model_type) == 0
    assert body.model_name == ""
    assert round(body.standoff_height / 10000.0, 4) == 0.0
    assert round(body.overall_height / 10000.0, 4) == 120.0
    assert round(body.model_extruded_min_z / 10000.0, 4) == 0.0
    assert round(body.model_extruded_max_z / 10000.0, 4) == 120.0
    assert len(body.outline) == 6


def test_pcblib_power_resistor_synthesis_writes_parseable_libraries(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcblib_synthesize_power_resistor_lib"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbLib

    output_root = check_examples_root / "pcblib_synthesize_power_resistor_lib"
    manifest_path = output_root / "output" / "synthesis_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["series"] == "SQP20A"
    assert manifest["generated_count"] == 3
    assert manifest["available_full_series_count"] >= manifest["generated_count"]

    for part in manifest["parts"]:
        pcblib = AltiumPcbLib.from_file(output_root / part["pcblib"])
        footprint = pcblib.footprints[0]
        model, _payload = pcblib.get_embedded_model_entries()[0]
        body = footprint.component_bodies[0]

        assert footprint.name == part["part_number"]
        assert len(footprint.pads) == 2
        assert all(pad.is_plated for pad in footprint.pads)
        assert {pad.designator for pad in footprint.pads} == {"1", "2"}
        assert int(model.checksum) & 0xFFFFFFFF == part["model_checksum_unsigned"]
        assert int(body.model_checksum) & 0xFFFFFFFF == part["model_checksum_unsigned"]
