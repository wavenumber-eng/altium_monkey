from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from altium_monkey import PcbLayerRef


PUBLIC_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = PUBLIC_ROOT / "examples"
MANIFEST_PATH = EXAMPLES_ROOT / "manifest.toml"
PCB_LAYER_API_AUDIT_PATH = EXAMPLES_ROOT / "pcb_layer_api_audit.toml"
CONTRACTS_ROOT = PUBLIC_ROOT / "docs" / "schemas" / "altium_monkey"
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\((?P<target>[^)]+)\)")
EXPECTED_REQUIRES_PYTHON = ">=3.12,<3.15"
EXPECTED_RUNTIME_DEPENDENCIES = {
    "freetype-py",
    "jsonschema-rs",
    "lxml",
    "lz4",
    "msgspec",
    "pillow",
    "uharfbuzz",
    "wn-geometer",
}
REMOVED_DIRECT_DEPENDENCIES = {
    "cascadio",
    "colorama",
    "easyeda-monkey",
    "json-with-comments",
    "mapbox-earcut",
    "openpyxl",
    "rtree",
    "trimesh",
}


def _load_examples() -> list[dict[str, object]]:
    manifest = tomllib.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    examples = manifest.get("examples", [])
    if not isinstance(examples, list):
        raise TypeError("examples/manifest.toml must contain an examples list")
    return examples


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    if match is None:
        raise AssertionError(f"invalid dependency requirement: {requirement!r}")
    return re.sub(r"[-_.]+", "-", match.group(0)).lower()


def _dependency_names(requirements: object) -> set[str]:
    return {_requirement_name(value) for value in _as_str_list(requirements)}


def _extractable_asset_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (CONTRACTS_ROOT / "extractable_assets_a0.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _embedded_asset_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (CONTRACTS_ROOT / "embedded_assets_a0.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _design_b0_schema_validator() -> Draft202012Validator:
    schema = json.loads(
        (CONTRACTS_ROOT / "design_b0.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _assert_schema_valid(
    validator: Draft202012Validator, payload: object, context: str
) -> None:
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    assert not errors, f"{context} schema errors: {[error.message for error in errors]}"


def _assert_schema_invalid(
    validator: Draft202012Validator, payload: object, context: str
) -> None:
    assert list(validator.iter_errors(payload)), f"{context} unexpectedly validated"


def test_schema_contract_docs_list_public_schema_ids() -> None:
    docs_path = PUBLIC_ROOT / "docs" / "schemas" / "index.md"
    docs_text = docs_path.read_text(encoding="utf-8")

    for schema_id in (
        "altium_monkey.design.b0",
        "altium_monkey.design.a2",
        "altium_monkey.design.a1",
        "altium_monkey.design.a0",
        "altium_monkey.netlist.a0",
        "altium_monkey.pcb.svg.enrichment.a0",
        "altium_monkey.pcb.embedded_assets.a0",
        "altium_monkey.extractable_assets.a0",
    ):
        assert schema_id in docs_text

    assert "AltiumDesign.to_json" in docs_text
    assert "`pnp` is optional" in docs_text
    assert "Netlist.to_json" in docs_text
    assert 'data-enrichment-schema="' in docs_text
    assert "pcb-enrichment-a0" in docs_text
    assert "Moving from `a` to `b`" in docs_text
    assert "Moving from `a0` to `a1`" in docs_text
    assert "Moving from `a1` to `a2`" in docs_text
    assert "Moving from `a2` to `b0`" in docs_text
    assert "docs/schemas/altium_monkey" in docs_text
    assert "design_b0.schema.json" in docs_text
    assert "design_a2.schema.json" in docs_text
    assert "design_a1.schema.json" in docs_text
    assert "design_a0.schema.json" in docs_text
    assert "netlist_a0.schema.json" in docs_text
    assert "pcb_svg_enrichment_a0.schema.json" in docs_text
    assert "embedded_assets_a0.schema.json" in docs_text
    assert "extractable_assets_a0.schema.json" in docs_text
    assert "EmbeddedAssetInventory.to_dict" in docs_text
    assert "AltiumAssetInventory.to_dict" in docs_text


def test_altium_monkey_contract_schemas_are_parseable() -> None:
    schemas = {
        "design_b0.schema.json": "altium_monkey.design.b0",
        "design_a2.schema.json": "altium_monkey.design.a2",
        "design_a1.schema.json": "altium_monkey.design.a1",
        "design_a0.schema.json": "altium_monkey.design.a0",
        "netlist_a0.schema.json": "altium_monkey.netlist.a0",
        "pcb_svg_enrichment_a0.schema.json": ("altium_monkey.pcb.svg.enrichment.a0"),
        "embedded_assets_a0.schema.json": "altium_monkey.pcb.embedded_assets.a0",
        "extractable_assets_a0.schema.json": "altium_monkey.extractable_assets.a0",
    }

    spec_text = (CONTRACTS_ROOT / "SPEC.md").read_text(encoding="utf-8")
    for filename, schema_id in schemas.items():
        schema_path = CONTRACTS_ROOT / filename
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["properties"]["schema"]["const"] == schema_id
        assert schema_id in spec_text


@pytest.mark.parametrize(
    ("project_path", "case_id"),
    [
        (
            "examples/assets/projects/bunny_brain/bunny_brain_D.PrjPcb",
            "bunny_brain",
        ),
        (
            "examples/assets/projects/hydroscope/Hydroscope.PrjPcb",
            "hydroscope",
        ),
    ],
)
@pytest.mark.parametrize(
    "include_compile_metadata",
    [False, True],
    ids=["compact", "compile_metadata"],
)
def test_altium_design_b0_schema_validates_real_to_json_payloads(
    project_path: str,
    case_id: str,
    include_compile_metadata: bool,
) -> None:
    from altium_monkey import AltiumDesign

    validator = _design_b0_schema_validator()
    payload = AltiumDesign.from_prjpcb(PUBLIC_ROOT / project_path).to_json(
        include_compile_metadata=include_compile_metadata
    )

    assert payload["schema"] == "altium_monkey.design.b0"
    assert ("compile" in payload) is include_compile_metadata
    assert ("diagnostics" in payload) is include_compile_metadata
    assert "physical_pages" not in payload
    assert payload["compiled_schematic_graph"]["schema"] == (
        "altium_monkey.compiled_schematic_graph.a0"
    )
    assert len(payload["physical_page_metadata"]) == len(
        payload["compiled_schematic_graph"]["page_occurrences"]
    )
    _assert_schema_valid(validator, payload, case_id)


def test_public_gitignore_tracks_lockfile_and_ignores_example_outputs() -> None:
    gitignore_lines = {
        line.strip()
        for line in (PUBLIC_ROOT / ".gitignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.strip().startswith("#")
    }

    assert (PUBLIC_ROOT / "uv.lock").exists()
    assert "uv.lock" not in gitignore_lines
    assert "examples/**/output/" in gitignore_lines


def test_public_python_and_dependency_metadata_contract() -> None:
    metadata = tomllib.loads((PUBLIC_ROOT / "pyproject.toml").read_text("utf-8"))
    project = metadata["project"]

    assert project["requires-python"] == EXPECTED_REQUIRES_PYTHON
    assert _dependency_names(project["dependencies"]) == EXPECTED_RUNTIME_DEPENDENCIES
    assert _dependency_names(project["optional-dependencies"]["examples"]) == {
        "cadquery"
    }
    direct_names = _dependency_names(project["dependencies"])
    optional_names = {
        name
        for requirements in project["optional-dependencies"].values()
        for name in _dependency_names(requirements)
    }
    assert not REMOVED_DIRECT_DEPENDENCIES & (direct_names | optional_names)
    classifiers = set(project["classifiers"])
    assert {
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    } <= classifiers


def test_python_314_workflows_build_before_installed_wheel_tests() -> None:
    workflows = PUBLIC_ROOT / ".github" / "workflows"
    validation = (workflows / "python-314.yml").read_text(encoding="utf-8")
    release = (workflows / "release.yml").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in validation
    assert not re.search(
        r"^\s+(?:push|pull_request|release):", validation, re.MULTILINE
    )
    assert "matrix:" not in validation
    assert 'python-version: "3.14"' in validation
    assert "uv run --locked --python 3.14" in validation
    assert validation.index("python -m build") < validation.index(
        "validate_wheel.py --mode test"
    )
    assert "validate_wheel.py --mode core" in validation

    assert "release:" in release
    assert "types: [published]" in release
    assert "workflow_dispatch:" not in release
    assert release.count("pypa/gh-action-pypi-publish") == 1
    assert 'python-version: "3.14"' in release
    assert (
        release.index("python -m build")
        < release.index("validate_wheel.py --mode test")
        < release.index("pypa/gh-action-pypi-publish")
    )
    assert "validate_wheel.py --mode core" in release


def test_manifest_inputs_and_assets_do_not_use_ignored_output_dirs() -> None:
    offenders: list[str] = []
    for example in _load_examples():
        for key in ("inputs", "assets"):
            for declared_path in _declared_paths(example, key):
                normalized = declared_path.replace("\\", "/")
                if "/output/" in f"/{normalized}":
                    offenders.append(f"{example['id']} {key[:-1]}: {declared_path}")

    assert not offenders, "\n".join(offenders)


def test_public_pcb_layer_examples_have_audit_classification() -> None:
    allowed_classifications = {
        "asset_or_project_no_layer_contract",
        "legacy_layer_example",
        "legacy_svg_report_example",
        "mechanical_kind_registry_example",
        "readback_report_example",
        "stack_model_example",
        "v7_issue_sample",
    }
    examples = _load_examples()
    manifest_ids = {str(example["id"]) for example in examples}
    candidate_ids = {
        str(example["id"])
        for example in examples
        if {"pcbdoc", "pcblib"} & set(_as_str_list(example.get("areas")))
    }

    audit = tomllib.loads(PCB_LAYER_API_AUDIT_PATH.read_text(encoding="utf-8"))
    assert audit["audit_version"] == 1
    assert "PcbLayerRef" in audit["accepted_layer_model"]
    groups = audit.get("groups", [])
    assert isinstance(groups, list)

    seen: dict[str, str] = {}
    for group in groups:
        classification = str(group.get("classification", ""))
        assert classification in allowed_classifications
        assert _as_str_list(group.get("layer_surfaces"))
        assert str(group.get("notes", "")).strip()
        group_ids = _as_str_list(group.get("example_ids"))
        assert group_ids
        for example_id in group_ids:
            assert example_id in manifest_ids
            assert example_id not in seen, f"duplicate audit entry: {example_id}"
            seen[example_id] = classification

    assert set(seen) == candidate_ids
    assert seen["pcbdoc_v7_128_signal_track_row"] == "v7_issue_sample"
    assert seen["pcb_v7_mechanical_layer_track_rows"] == "v7_issue_sample"
    assert seen["pcbdoc_create_layer_stack"] == "stack_model_example"
    assert seen["pcbdoc_add_pad"] == "legacy_layer_example"
    assert seen["pcblib_footprint_svg"] == "legacy_svg_report_example"


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
    assert {
        "freetype-py",
        "lxml",
        "lz4",
        "pillow",
        "uharfbuzz",
        "wn-geometer",
    }.issubset(dependency_names)
    assert "cadquery" not in dependency_names

    optional_dependency_names = {
        dependency["name"]
        for dependencies in altium_package.get("optional-dependencies", {}).values()
        for dependency in dependencies
    }
    assert "cadquery" in optional_dependency_names
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
            "intlib.md",
        )
    )

    for token in (
        "AltiumSchDoc",
        "AltiumSchLib",
        "AltiumDesign",
        "AltiumPcbDoc",
        "AltiumPcbLib",
        "AltiumPrjPcb",
        "AltiumIntLib",
        "SchPointMils",
        "SchRectMils",
        "schdoc_add_note",
        "schdoc_add_sheet_symbol",
        "schdoc_vertical_pin_svg",
        "schdoc_svg",
        "schlib_svg",
        "pcbdoc_add_pad",
        "pcblib_split",
        "prjpcb_make_project",
        "outjob_runner",
        "pcbdoc_bom",
        "pcbdoc_pick_n_place",
        "pcbdoc_add_differential_pairs",
        "pcbdoc_user_union",
        "pcbdoc_diff_pair_report",
        "pcbdoc_flex_topology_report",
        "pcbdoc_create_flex_stiffener",
        "pcbdoc_create_rigid_flex_split_lines",
        "pcbdoc_create_flex_in_cutout",
        "pcbdoc_create_rigid_flex_branch",
        "pcbdoc_create_rigid_flex_branch_intrusion",
        "pcbdoc_create_rigid_flex_two_branch",
        "pcbdoc_create_rigid_flex_impedance_backdrill",
        "pcbdoc_create_cavity_placements",
        "pcbdoc_create_rigid_flex_multibranch",
        "AltiumLayerStackDocument",
        "AltiumStackBranch",
        "source_stackup_ref",
        "layers_for_board_region",
        "intlib_extract_sources",
        "altium_monkey.design.b0",
        "altium_monkey.design.a2",
        "altium_monkey.design.a1",
        "altium_monkey.netlist.a0",
        "altium_monkey.pcb.embedded_assets.a0",
        "altium_monkey.extractable_assets.a0",
        "embedded_asset_inventory",
        "extractable_asset_inventory",
    ):
        assert token in docs_text


def test_pcbdoc_domain_docs_list_manifest_pcbdoc_examples() -> None:
    docs_text = (PUBLIC_ROOT / "docs" / "pcbdoc.md").read_text(encoding="utf-8")
    pcbdoc_example_ids = sorted(
        str(example["id"])
        for example in _load_examples()
        if str(example["id"]).startswith("pcbdoc")
    )

    for example_id in ["hello_pcbdoc", *pcbdoc_example_ids]:
        assert f"`{example_id}`" in docs_text or f"/{example_id}/" in docs_text


def test_format_contract_docs_are_published() -> None:
    contracts_root = PUBLIC_ROOT / "docs" / "format_contracts"
    expected_docs = {
        "index.md",
        "schdoc.md",
        "schlib.md",
        "pcbdoc.md",
        "pcblib.md",
        "prjpcb.md",
        "altium_design.md",
        "intlib.md",
        "svg.md",
        "draftsman.md",
    }

    assert expected_docs == {
        path.name for path in contracts_root.glob("*.md") if path.is_file()
    }

    index_text = (contracts_root / "index.md").read_text(encoding="utf-8")
    public_docs_index = (PUBLIC_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    public_readme = (PUBLIC_ROOT / "README.md").read_text(encoding="utf-8")
    svg_contract = (contracts_root / "svg.md").read_text(encoding="utf-8")
    pcbdoc_contract = (contracts_root / "pcbdoc.md").read_text(encoding="utf-8")

    assert "Format Contracts" in index_text
    assert "format_contracts/index.md" in public_docs_index
    assert "docs/format_contracts/index.md" in public_readme
    assert "include_view_box=False" in svg_contract
    assert "components[].svg_id" in svg_contract
    assert "nets[].graphical" in svg_contract
    assert "data-doc-id" in svg_contract
    assert "data-element-key" in svg_contract
    assert "altium_monkey.pcb.svg.enrichment.a0" in svg_contract
    assert "data-layer-display-name" in svg_contract
    assert "IPC-4761" in pcbdoc_contract
    assert "source_stackup_ref" in pcbdoc_contract
    assert "layerstack_id" in pcbdoc_contract
    assert "branches_for_stack_ref" in pcbdoc_contract
    assert "with or without braces" in pcbdoc_contract


def test_public_v7_layer_docs_match_current_contract() -> None:
    docs = {
        "pcb layers guide": PUBLIC_ROOT / "docs" / "api_patterns" / "pcb_layers.md",
        "pcbdoc guide": PUBLIC_ROOT / "docs" / "pcbdoc.md",
        "pcblib guide": PUBLIC_ROOT / "docs" / "pcblib.md",
        "pcbdoc contract": PUBLIC_ROOT / "docs" / "format_contracts" / "pcbdoc.md",
        "pcblib contract": PUBLIC_ROOT / "docs" / "format_contracts" / "pcblib.md",
        "svg contract": PUBLIC_ROOT / "docs" / "format_contracts" / "svg.md",
    }
    combined = "\n".join(path.read_text(encoding="utf-8") for path in docs.values())
    pcbdoc_contract = docs["pcbdoc contract"].read_text(encoding="utf-8")

    for required in (
        "PcbLayerRef",
        "Mechanical53",
        "MID126",
        "data-layer-token",
        "data-layer-family",
        "data-layer-v7-saved-id",
        "0x01020021",
        "0x0100FFFF",
    ):
        assert required in combined

    stale_phrases = (
        "future V7-aware layer-reference work",
        "deferred to future V7-aware layer-reference work",
        "Current SVG output is not a fidelity contract for parsed extended V7 layers",
        "current public SVG layer contract is legacy-layer-token oriented",
        "only use default display labels",
        "documented legacy layer boundary",
    )
    for label, path in docs.items():
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, f"{label}: stale V7 wording: {phrase}"

    assert (
        "Track, arc, fill, text, region, and component-body helpers can author\n"
        "ordinary numbered mechanical layers through Mechanical53."
    ) in pcbdoc_contract
    assert (
        "Track, arc, fill,\ntext, and region helpers can author V7-only signal refs"
    ) in pcbdoc_contract
    assert (
        "component-body helpers can author V7-only signal refs" not in pcbdoc_contract
    )


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


def test_public_docs_style_foundation_is_present() -> None:
    assets_root = PUBLIC_ROOT / "assets"
    stylesheet = assets_root / "altium-monkey-docs.css"
    theme_css = assets_root / "altium-monkey-theme.css"
    theme_json = assets_root / "altium-monkey-theme.json"
    font_ttf = assets_root / "fonts" / "altium-stroke.ttf"
    font_otf = assets_root / "fonts" / "altium-stroke.otf"
    font_woff = assets_root / "fonts" / "altium-stroke.woff"
    font_woff2 = assets_root / "fonts" / "altium-stroke.woff2"
    font_bold_ttf = assets_root / "fonts" / "altium-stroke-bold.ttf"
    font_css = assets_root / "fonts" / "altium-monkey-stroke-fonts.css"
    demo_page = assets_root / "fonts" / "demo.html"
    style_doc = PUBLIC_ROOT / "docs" / "style.md"
    docs_index = (PUBLIC_ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    public_readme = (PUBLIC_ROOT / "README.md").read_text(encoding="utf-8")
    css = stylesheet.read_text(encoding="utf-8")
    font_css_text = font_css.read_text(encoding="utf-8")
    theme = json.loads(theme_json.read_text(encoding="utf-8"))
    style_text = style_doc.read_text(encoding="utf-8")

    assert stylesheet.exists()
    assert theme_css.exists()
    assert theme_json.exists()
    assert font_ttf.exists()
    assert font_otf.exists()
    assert font_woff.exists()
    assert font_woff2.exists()
    assert font_bold_ttf.exists()
    assert font_css.exists()
    assert demo_page.exists()
    assert style_doc.exists()
    assert "style.md" in docs_index
    assert "docs/style.md" in public_readme
    assert "--mk-accent: #f0a400" in css
    assert "--mk-bg: #050403" in css
    assert "--am-font-stroke" in css
    assert "font-family: 'Altium Stroke'" in css
    assert "fonts/altium-stroke.woff2" in css
    assert "fonts/altium-stroke.woff" in css
    assert "fonts/altium-stroke.otf" in css
    assert "font-family: 'Altium Stroke'" in font_css_text
    assert "altium-stroke.woff2" in font_css_text
    assert "altium-stroke.otf" in font_css_text
    assert theme["id"] == "amber"
    assert theme["tokens"]["--mk-accent"] == "#f0a400"
    assert "../assets/altium-monkey-docs.css" in style_text
    assert "../assets/fonts/altium-stroke.woff2" in style_text
    assert "../assets/fonts/altium-stroke.woff" in style_text
    assert "../assets/fonts/altium-stroke.otf" in style_text
    assert "../assets/fonts/demo.html" in style_text


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


def _mechanical_kind_sample_layer_set_1() -> list[str]:
    return [
        "MultiLayer",
        "TopPaste",
        "TopOverlay",
        "TopSolder",
        "TopLayer",
        "BottomLayer",
        "BottomSolder",
        "BottomOverlay",
        "BottomPaste",
        "DrillGuide",
        "KeepOutLayer",
        *[f"Mechanical{number}" for number in range(1, 17)],
        "DrillDrawing",
        *[f"Mechanical{number}" for number in range(17, 32)],
    ]


def _mechanical_kind_sample_layer_set_5() -> list[str]:
    return [f"Mechanical{number}" for number in range(1, 32)]


def _split_layer_set(value: object) -> list[str]:
    return [
        part.strip() for part in str(value or "").strip().split(",") if part.strip()
    ]


def _public_mechanical_kind_pairs() -> list[tuple[str, str]]:
    return [
        ("MECHANICAL10", "MECHANICAL11"),
        ("MECHANICAL12", "MECHANICAL13"),
        ("MECHANICAL14", "MECHANICAL15"),
        ("MECHANICAL16", "MECHANICAL17"),
        ("MECHANICAL18", "MECHANICAL19"),
        ("MECHANICAL20", "MECHANICAL21"),
        ("MECHANICAL22", "MECHANICAL23"),
        ("MECHANICAL24", "MECHANICAL25"),
        ("MECHANICAL26", "MECHANICAL27"),
        ("MECHANICAL28", "MECHANICAL29"),
        ("MECHANICAL30", "MECHANICAL31"),
    ]


def _mechanical_layer_number(layer: str) -> int:
    return int(layer.removeprefix("MECHANICAL"))


def _expected_mechanical_kind_fields(
    layer: str,
    token: str,
) -> dict[str, str]:
    expected = {
        "layer_v8": token,
        "v9_cache": token,
    }
    if _mechanical_layer_number(layer) <= 16:
        expected["legacy"] = token
    else:
        expected["v7"] = token
    return expected


def _indexed_raw_layer_field(
    raw_record: dict[str, object],
    key_re: re.Pattern[str],
    layer_id: int,
    field_name: str,
) -> str | None:
    groups: dict[int, dict[str, str]] = {}
    for key, value in raw_record.items():
        match = key_re.match(str(key))
        if match is None:
            continue
        groups.setdefault(int(match.group(1)), {})[match.group(2).upper()] = str(value)
    for values in groups.values():
        if values.get("LAYERID") == str(int(layer_id)):
            return values.get(field_name.upper())
    return None


def _assert_mechanical_mirror_layer_names(
    raw_record: dict[str, object],
    *,
    expected_display_names: dict[str, str],
) -> None:
    layer_v8_re = re.compile(r"^LAYER_V8_(\d+)(.+)$", re.IGNORECASE)
    v9_cache_re = re.compile(r"^V9_CACHE_LAYER(\d+)_(.+)$", re.IGNORECASE)
    for token in ("MECHANICAL1", "MECHANICAL17", "MECHANICAL33", "MECHANICAL53"):
        layer_id = 0x01020000 + _mechanical_layer_number(token)
        expected_name = expected_display_names[token]
        assert _indexed_raw_layer_field(raw_record, layer_v8_re, layer_id, "NAME") == (
            expected_name
        )
        assert _indexed_raw_layer_field(raw_record, v9_cache_re, layer_id, "NAME") == (
            expected_name
        )
        assert (
            _indexed_raw_layer_field(
                raw_record,
                layer_v8_re,
                layer_id,
                "MECHENABLED",
            )
            == "TRUE"
        )
        assert (
            _indexed_raw_layer_field(
                raw_record,
                v9_cache_re,
                layer_id,
                "MECHENABLED",
            )
            == "TRUE"
        )


def _v7_raw_layer_index(
    raw_record: dict[str, object],
    layer_id: int,
) -> int | None:
    for key, value in raw_record.items():
        match = re.fullmatch(r"LAYERV7_(\d+)LAYERID", str(key), re.IGNORECASE)
        if match is None or str(value) != str(int(layer_id)):
            continue
        return int(match.group(1))
    return None


def _pcbdoc_mechanical_kind_fields(
    raw_record: dict[str, object],
    layer: str,
) -> dict[str, str | None]:
    mechanical_number = _mechanical_layer_number(layer)
    v7_layer_id = 0x01020000 + mechanical_number
    fields: dict[str, str | None] = {}
    if mechanical_number <= 16:
        legacy_value = raw_record.get(f"LAYER{56 + mechanical_number}MECHKIND")
        fields["legacy"] = str(legacy_value) if legacy_value else None
    else:
        v7_index = _v7_raw_layer_index(raw_record, v7_layer_id)
        v7_value = (
            None if v7_index is None else raw_record.get(f"LAYERV7_{v7_index}MECHKIND")
        )
        fields["v7"] = str(v7_value) if v7_value else None
    fields["layer_v8"] = _indexed_raw_layer_field(
        raw_record,
        re.compile(r"^LAYER_V8_(\d+)(.+)$", re.IGNORECASE),
        v7_layer_id,
        "MECHKIND",
    )
    fields["v9_cache"] = _indexed_raw_layer_field(
        raw_record,
        re.compile(r"^V9_CACHE_LAYER(\d+)_(.+)$", re.IGNORECASE),
        v7_layer_id,
        "MECHKIND",
    )
    return fields


def _assert_mechanical_kind_manifest(manifest: dict[str, object]) -> None:
    assert manifest["registry_layer_count"] == 32
    assert manifest["assignment_count"] == 30
    assert manifest["pair_count"] == 11
    assert manifest["route_tool_path_layer"] == "MECHANICAL3"
    assert manifest["layer_set_1_layers"] == _mechanical_kind_sample_layer_set_1()
    assert manifest["layer_set_5_layers"] == _mechanical_kind_sample_layer_set_5()
    assert manifest["primitive_counts"] == {
        key: 0 for key in manifest["primitive_counts"]
    }
    assignments = manifest["assignments"]
    assert assignments[0]["layer"] == "MECHANICAL2"
    assert assignments[0]["kind"] == "BOARD_SHAPE"
    assert assignments[1]["layer"] == "MECHANICAL3"
    assert assignments[1]["kind"] == "ROUTE_TOOL_PATH"
    assert assignments[-1]["layer"] == "MECHANICAL31"
    assert assignments[-1]["kind"] == "VALUE_BOTTOM"
    if "mechanical_kind_fields" in manifest:
        assert len(manifest["mechanical_kind_fields"]) == manifest["assignment_count"]
    assert [
        (pair["layer_1"], pair["layer_2"]) for pair in manifest["component_layer_pairs"]
    ] == _public_mechanical_kind_pairs()


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


def test_pcbdoc_create_mechanical_layer_kinds_is_metadata_only_and_visible(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_mechanical_layer_kinds"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbDoc, MechanicalLayerKind
    from altium_monkey.altium_pcb_layer_kind_mapping import (
        mechanical_layer_kind_to_data_token,
    )

    output_root = check_examples_root / "pcbdoc_create_mechanical_layer_kinds"
    output_path = output_root / "output" / "pcbdoc_create_mechanical_layer_kinds.PcbDoc"
    manifest = json.loads(
        (
            output_root / "output" / "pcbdoc_create_mechanical_layer_kinds.json"
        ).read_text(encoding="utf-8")
    )
    pcbdoc = AltiumPcbDoc.from_file(output_path)

    _assert_mechanical_kind_manifest(manifest)
    assert pcbdoc.board is not None
    assert pcbdoc.get_mechanical_layer_kind("MECHANICAL2") == (
        MechanicalLayerKind.BOARD_SHAPE
    )
    assert pcbdoc.get_mechanical_layer_kind("MECHANICAL3") == (
        MechanicalLayerKind.ROUTE_TOOL_PATH
    )
    assert pcbdoc.get_mechanical_layer_kind("MECHANICAL31") == (
        MechanicalLayerKind.VALUE_BOTTOM
    )
    raw_board = pcbdoc.board.raw_record or {}
    assert str(raw_board.get("ROUTETOOLPATHLAYER", "")).strip() == "MECHANICAL3"
    assert _split_layer_set(raw_board.get("LAYERSET1LAYERS")) == (
        _mechanical_kind_sample_layer_set_1()
    )
    assert _split_layer_set(raw_board.get("LAYERSET5LAYERS")) == (
        _mechanical_kind_sample_layer_set_5()
    )
    for pair_index, (layer_1, layer_2) in enumerate(_public_mechanical_kind_pairs()):
        assert str(raw_board.get(f"MECHPAIR{pair_index}L1", "")).strip() == layer_1
        assert str(raw_board.get(f"MECHPAIR{pair_index}L2", "")).strip() == layer_2
    for assignment in manifest["assignments"]:
        layer = str(assignment["layer"])
        kind = MechanicalLayerKind[str(assignment["kind"])]
        token = mechanical_layer_kind_to_data_token(kind)
        assert _pcbdoc_mechanical_kind_fields(raw_board, layer) == (
            _expected_mechanical_kind_fields(layer, token)
        )
    assert len(pcbdoc.components) == 0
    assert len(pcbdoc.pads) == 0
    assert len(pcbdoc.tracks) == 0
    assert len(pcbdoc.vias) == 0


def test_pcblib_create_mechanical_layer_kinds_is_metadata_only_and_visible(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcblib_create_mechanical_layer_kinds"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbLib, MechanicalLayerKind
    from altium_monkey.altium_pcb_layer_kind_mapping import (
        mechanical_layer_kind_to_data_token,
    )
    from altium_monkey.altium_pcblib_builder import PcbLibBuildProfile

    output_root = check_examples_root / "pcblib_create_mechanical_layer_kinds"
    output_path = output_root / "output" / "pcblib_create_mechanical_layer_kinds.PcbLib"
    manifest = json.loads(
        (
            output_root / "output" / "pcblib_create_mechanical_layer_kinds.json"
        ).read_text(encoding="utf-8")
    )
    pcblib = AltiumPcbLib.from_file(output_path)
    library_data = PcbLibBuildProfile.from_pcblib(output_path).library_data

    _assert_mechanical_kind_manifest(manifest)
    assert pcblib.get_mechanical_layer_kind("MECHANICAL2") == (
        MechanicalLayerKind.BOARD_SHAPE
    )
    assert pcblib.get_mechanical_layer_kind("MECHANICAL3") == (
        MechanicalLayerKind.ROUTE_TOOL_PATH
    )
    assert pcblib.get_mechanical_layer_kind("MECHANICAL31") == (
        MechanicalLayerKind.VALUE_BOTTOM
    )
    route_record = library_data.get_board_record("ROUTETOOLPATHLAYER")
    assert route_record is not None
    assert str(route_record.get_value("ROUTETOOLPATHLAYER", "")).strip() == (
        "MECHANICAL3"
    )
    layer_set_1 = library_data.layer_sets.layer_set(1)
    layer_set_5 = library_data.layer_sets.layer_set(5)
    assert layer_set_1 is not None
    assert layer_set_5 is not None
    assert list(layer_set_1.layers) == _mechanical_kind_sample_layer_set_1()
    assert list(layer_set_5.layers) == _mechanical_kind_sample_layer_set_5()
    pair_values: dict[int, dict[int, str]] = {}
    for segment in library_data.segments:
        key = segment.key or ""
        match = re.fullmatch(r"MECHPAIR(\d+)L([12])", key, re.IGNORECASE)
        if match is None or segment.value is None:
            continue
        pair_values.setdefault(int(match.group(1)), {})[int(match.group(2))] = (
            segment.value
        )
    assert [
        (pair_values[index][1], pair_values[index][2])
        for index in range(len(_public_mechanical_kind_pairs()))
    ] == _public_mechanical_kind_pairs()
    for assignment in manifest["assignments"]:
        layer = str(assignment["layer"])
        kind = MechanicalLayerKind[str(assignment["kind"])]
        token = mechanical_layer_kind_to_data_token(kind)
        assert library_data.mechanical_layer_kind_field_values(layer) == (
            _expected_mechanical_kind_fields(layer, token)
        )
    assert len(pcblib.footprints) == 1
    footprint = pcblib.footprints[0]
    assert len(footprint.pads) == 0
    assert len(footprint.tracks) == 0
    assert len(footprint.vias) == 0


def _expected_128_signal_refs() -> list["PcbLayerRef"]:
    from altium_monkey import PcbLayerRef

    return [
        PcbLayerRef.top(),
        *(PcbLayerRef.mid_layer(number) for number in range(1, 127)),
        PcbLayerRef.bottom(),
    ]


def _expected_128_signal_saved_ids() -> dict[str, int]:
    expected: dict[str, int] = {}
    for ref in _expected_128_signal_refs():
        saved_id = ref.v7_saved_layer_id
        if saved_id is None:
            raise AssertionError(f"{ref.token} is missing a saved-layer id")
        expected[ref.token] = int(saved_id)
    return expected


def _expected_128_signal_display_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for ref in _expected_128_signal_refs():
        if ref.token == "TOP":
            names[ref.token] = "Top Layer"
        elif ref.token == "BOTTOM":
            names[ref.token] = "Bottom Layer"
        else:
            names[ref.token] = f"SIG{int(ref.number or 0) + 1:02d}"
    return names


def _assert_svg_v7_token_without_legacy_id(
    svg_text: str,
    *,
    token: str,
    saved_id: int,
) -> None:
    root = ET.fromstring(svg_text)
    elements = [
        elem
        for elem in root.iter()
        if (elem.get("data-layer-token") or "").strip().upper() == token
    ]
    assert elements, f"Expected SVG elements for {token}"
    for elem in elements:
        assert not (elem.get("data-layer-id") or "").strip()
        assert elem.get("data-layer-family") == "signal"
        assert elem.get("data-layer-role") == "copper"
        assert elem.get("data-layer-v7-saved-id") == str(saved_id)


def _expected_mechanical_track_row_refs() -> list["PcbLayerRef"]:
    from altium_monkey import PcbLayerRef
    from altium_monkey.altium_pcb_enums import PCB_USER_MECHANICAL_LAYER_AUTHORING_MAX

    return [
        PcbLayerRef.mechanical(number)
        for number in range(1, PCB_USER_MECHANICAL_LAYER_AUTHORING_MAX + 1)
    ]


def _expected_mechanical_track_row_saved_ids() -> dict[str, int]:
    expected: dict[str, int] = {}
    for ref in _expected_mechanical_track_row_refs():
        saved_id = ref.v7_saved_layer_id
        if saved_id is None:
            raise AssertionError(f"{ref.token} is missing a saved-layer id")
        expected[ref.token] = int(saved_id)
    return expected


def _expected_mechanical_track_row_display_names() -> dict[str, str]:
    return {
        ref.token: f"Sample Mechanical {int(ref.number or 0):02d}"
        for ref in _expected_mechanical_track_row_refs()
    }


def _assert_svg_mechanical_v7_token_without_legacy_id(
    svg_text: str,
    *,
    token: str,
    saved_id: int,
    display_name: str,
) -> None:
    root = ET.fromstring(svg_text)
    elements = [
        elem
        for elem in root.iter()
        if (elem.get("data-layer-token") or "").strip().upper() == token
    ]
    assert elements, f"Expected SVG elements for {token}"
    for elem in elements:
        assert not (elem.get("data-layer-id") or "").strip()
        assert elem.get("data-layer-family") == "mechanical"
        assert elem.get("data-layer-role") == "mechanical"
        assert elem.get("data-layer-v7-saved-id") == str(saved_id)
        assert elem.get("data-layer-display-name") == display_name


def _assert_128_signal_sample_stdout(stdout: str) -> None:
    assert "Signal layers: 128" in stdout
    assert "Tracks: 128" in stdout
    assert "All tracks match expected layers: True" in stdout


def _assert_128_signal_sample_manifest(
    manifest: dict[str, object],
    *,
    expected_saved_ids: dict[str, int],
) -> None:
    assert manifest["schema"] == (
        "altium_monkey.examples.pcbdoc_v7_128_signal_track_row.a0"
    )
    assert manifest["signal_layer_count"] == 128
    assert manifest["track_count"] == 128
    assert manifest["stackupx_generated"] is True
    assert manifest["stackupx_reimported"] is True
    assert manifest["all_track_tokens_match"] is True
    assert manifest["all_track_saved_ids_match"] is True
    assert manifest["all_tracks_match_expected"] is True
    assert manifest["sentinel_saved_ids"] == {
        key: expected_saved_ids[key] for key in ("TOP", "MID31", "MID126", "BOTTOM")
    }


def _assert_128_signal_sample_stack_manifest(manifest: dict[str, object]) -> None:
    stack = manifest["stack"]
    assert stack["physical_copper_layer_count"] == 128
    assert stack["inner_signal_layer_count"] == 126
    assert stack["inner_signal_layers"][30] == "SIG32"
    assert stack["inner_signal_layers"][-1] == "SIG127"


def _assert_128_signal_sample_layer_manifest(
    manifest: dict[str, object],
    *,
    expected_tokens: list[str],
    expected_saved_ids: dict[str, int],
    expected_display_names: dict[str, str],
) -> None:
    signal_layers = manifest["signal_layers"]
    assert len(signal_layers) == 128
    assert [row["token"] for row in signal_layers] == expected_tokens
    assert [row["display_name"] for row in signal_layers] == [
        expected_display_names[token] for token in expected_tokens
    ]
    assert [row["v7_saved_layer_id"] for row in signal_layers] == [
        expected_saved_ids[token] for token in expected_tokens
    ]
    assert [row["expected_v7_saved_layer_id"] for row in signal_layers] == [
        expected_saved_ids[token] for token in expected_tokens
    ]


def _assert_128_signal_sample_track_manifest(
    manifest: dict[str, object],
    *,
    expected_tokens: list[str],
    expected_saved_ids: dict[str, int],
    expected_display_names: dict[str, str],
) -> None:
    tracks = manifest["tracks"]
    assert [row["token"] for row in tracks] == expected_tokens
    assert [row["expected_token"] for row in tracks] == expected_tokens
    assert [row["stack_display_name"] for row in tracks] == [
        expected_display_names[token] for token in expected_tokens
    ]
    assert [row["expected_stack_display_name"] for row in tracks] == [
        expected_display_names[token] for token in expected_tokens
    ]
    assert [row["v7_saved_layer_id"] for row in tracks] == [
        expected_saved_ids[token] for token in expected_tokens
    ]
    assert [row["expected_v7_saved_layer_id"] for row in tracks] == [
        expected_saved_ids[token] for token in expected_tokens
    ]


def _assert_128_signal_sample_pcbdoc(
    output_root: Path,
    *,
    expected_tokens: list[str],
    expected_saved_ids: dict[str, int],
    expected_display_names: dict[str, str],
) -> None:
    from altium_monkey import AltiumLayerStackDocument, AltiumPcbDoc

    pcbdoc_path = output_root / "pcbdoc_v7_128_signal_track_row.PcbDoc"
    pcbdoc = AltiumPcbDoc.from_file(pcbdoc_path)
    assert len(pcbdoc.tracks) == 128
    assert [track.layer_ref().token for track in pcbdoc.tracks] == expected_tokens

    stack = AltiumLayerStackDocument.from_pcbdoc(pcbdoc)
    copper_layers = [
        layer for layer in stack.physical_stacks[0].layers if layer.family == "copper"
    ]
    assert len(copper_layers) == 128
    assert [layer.display_name for layer in copper_layers] == [
        expected_display_names[token] for token in expected_tokens
    ]
    assert copper_layers[126].display_name == "SIG127"
    assert copper_layers[126].layer_id == expected_saved_ids["MID126"]


def test_pcbdoc_v7_128_signal_track_row_sample_preserves_layer_identity(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_v7_128_signal_track_row"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    _assert_128_signal_sample_stdout(result.stdout)

    output_root = check_examples_root / "pcbdoc_v7_128_signal_track_row" / "output"
    manifest = json.loads(
        (output_root / "pcbdoc_v7_128_signal_track_row.json").read_text(
            encoding="utf-8"
        )
    )
    expected_refs = _expected_128_signal_refs()
    expected_tokens = [ref.token for ref in expected_refs]
    expected_saved_ids = _expected_128_signal_saved_ids()
    expected_display_names = _expected_128_signal_display_names()
    _assert_128_signal_sample_manifest(
        manifest,
        expected_saved_ids=expected_saved_ids,
    )
    _assert_128_signal_sample_stack_manifest(manifest)
    _assert_128_signal_sample_layer_manifest(
        manifest,
        expected_tokens=expected_tokens,
        expected_saved_ids=expected_saved_ids,
        expected_display_names=expected_display_names,
    )
    _assert_128_signal_sample_track_manifest(
        manifest,
        expected_tokens=expected_tokens,
        expected_saved_ids=expected_saved_ids,
        expected_display_names=expected_display_names,
    )
    _assert_128_signal_sample_pcbdoc(
        output_root,
        expected_tokens=expected_tokens,
        expected_saved_ids=expected_saved_ids,
        expected_display_names=expected_display_names,
    )

    svg_text = (output_root / "pcbdoc_v7_128_signal_track_row.svg").read_text(
        encoding="utf-8"
    )
    _assert_svg_v7_token_without_legacy_id(
        svg_text,
        token="MID126",
        saved_id=expected_saved_ids["MID126"],
    )


def _assert_mechanical_track_row_stdout(stdout: str) -> None:
    assert f"Mechanical layers: {len(_expected_mechanical_track_row_refs())}" in stdout
    assert "PcbDoc tracks match expected layers: True" in stdout
    assert "PcbLib tracks match expected layers: True" in stdout


def _expected_mechanical_track_row_storage_ids() -> list[int]:
    from altium_monkey import PcbLayer

    return [
        ref.legacy_layer_id
        if ref.legacy_layer_id is not None
        else int(PcbLayer.MECHANICAL_16)
        for ref in _expected_mechanical_track_row_refs()
    ]


def _assert_mechanical_track_row_tokens(
    rows: list[dict[str, object]],
    *,
    expected_tokens: list[str],
) -> None:
    assert [row["token"] for row in rows] == expected_tokens
    assert [row["expected_token"] for row in rows] == expected_tokens


def _assert_mechanical_track_row_display_names(
    rows: list[dict[str, object]],
    *,
    expected_tokens: list[str],
    expected_display_names: dict[str, str],
) -> None:
    assert [row["display_name"] for row in rows] == [
        expected_display_names[token] for token in expected_tokens
    ]
    assert [row["expected_display_name"] for row in rows] == [
        expected_display_names[token] for token in expected_tokens
    ]


def _assert_mechanical_track_row_saved_ids(
    rows: list[dict[str, object]],
    *,
    expected_tokens: list[str],
    expected_saved_ids: dict[str, int],
) -> None:
    assert [row["v7_saved_layer_id"] for row in rows] == [
        expected_saved_ids[token] for token in expected_tokens
    ]
    assert [row["expected_v7_saved_layer_id"] for row in rows] == [
        expected_saved_ids[token] for token in expected_tokens
    ]


def _assert_mechanical_track_row_storage(rows: list[dict[str, object]]) -> None:
    assert [row["stored_legacy_layer_id"] for row in rows] == (
        _expected_mechanical_track_row_storage_ids()
    )
    assert [row["v7_only"] for row in rows] == [
        (ref.legacy_layer_id is None) for ref in _expected_mechanical_track_row_refs()
    ]


def _assert_mechanical_track_rows(
    rows: list[dict[str, object]],
    *,
    expected_tokens: list[str],
    expected_saved_ids: dict[str, int],
    expected_display_names: dict[str, str],
) -> None:
    assert len(rows) == len(expected_tokens)
    _assert_mechanical_track_row_tokens(rows, expected_tokens=expected_tokens)
    _assert_mechanical_track_row_display_names(
        rows,
        expected_tokens=expected_tokens,
        expected_display_names=expected_display_names,
    )
    _assert_mechanical_track_row_saved_ids(
        rows,
        expected_tokens=expected_tokens,
        expected_saved_ids=expected_saved_ids,
    )
    _assert_mechanical_track_row_storage(rows)
    assert all(row["matches_expected"] for row in rows)


def _assert_mechanical_track_row_manifest(manifest: dict[str, object]) -> None:
    expected_refs = _expected_mechanical_track_row_refs()
    expected_tokens = [ref.token for ref in expected_refs]
    expected_saved_ids = _expected_mechanical_track_row_saved_ids()
    expected_display_names = _expected_mechanical_track_row_display_names()

    assert manifest["schema"] == (
        "altium_monkey.examples.pcb_v7_mechanical_layer_track_rows.a0"
    )
    assert manifest["supported_mechanical_layer_count"] == len(expected_refs)

    for section in ("pcbdoc", "pcblib"):
        data = manifest[section]
        assert data["track_count"] == len(expected_refs)
        assert data["all_tracks_match_expected"] is True
        _assert_mechanical_track_rows(
            data["tracks"],
            expected_tokens=expected_tokens,
            expected_saved_ids=expected_saved_ids,
            expected_display_names=expected_display_names,
        )


def _assert_mechanical_track_row_outputs(output_root: Path) -> None:
    from altium_monkey import AltiumPcbDoc, AltiumPcbLib

    expected_tokens = [ref.token for ref in _expected_mechanical_track_row_refs()]
    expected_saved_ids = _expected_mechanical_track_row_saved_ids()
    expected_display_names = _expected_mechanical_track_row_display_names()

    pcbdoc = AltiumPcbDoc.from_file(
        output_root / "pcb_v7_mechanical_layer_track_rows.PcbDoc"
    )
    assert len(pcbdoc.tracks) == len(expected_tokens)
    assert [track.layer_ref().token for track in pcbdoc.tracks] == expected_tokens
    assert pcbdoc.board is not None
    assert pcbdoc.board.v9_layer_cache[expected_saved_ids["MECHANICAL53"]] == (
        "Sample Mechanical 53"
    )
    _assert_mechanical_mirror_layer_names(
        pcbdoc.board.raw_record or {},
        expected_display_names=expected_display_names,
    )

    pcblib = AltiumPcbLib.from_file(
        output_root / "pcb_v7_mechanical_layer_track_rows.PcbLib"
    )
    assert len(pcblib.footprints) == 1
    assert pcblib.footprints[0].name == "V7_MECHANICAL_LAYER_TRACK_ROWS"
    assert [track.layer_ref().token for track in pcblib.footprints[0].tracks] == (
        expected_tokens
    )
    from altium_monkey.altium_pcblib_builder import PcbLibBuildProfile

    pcblib_profile = PcbLibBuildProfile.from_pcblib(
        output_root / "pcb_v7_mechanical_layer_track_rows.PcbLib"
    )
    library_raw_record = {
        str(segment.key): str(segment.value)
        for segment in pcblib_profile.library_data.segments
        if segment.key is not None and segment.value is not None
    }
    _assert_mechanical_mirror_layer_names(
        library_raw_record,
        expected_display_names=expected_display_names,
    )

    for svg_name in (
        "pcbdoc_mechanical_layer_track_rows.svg",
        "pcblib_mechanical_layer_track_rows.svg",
    ):
        svg_text = (output_root / svg_name).read_text(encoding="utf-8")
        for token in ("MECHANICAL17", "MECHANICAL53"):
            _assert_svg_mechanical_v7_token_without_legacy_id(
                svg_text,
                token=token,
                saved_id=expected_saved_ids[token],
                display_name=expected_display_names[token],
            )


def test_pcb_v7_mechanical_layer_track_rows_sample_preserves_layer_identity(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcb_v7_mechanical_layer_track_rows"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    _assert_mechanical_track_row_stdout(result.stdout)

    output_root = check_examples_root / "pcb_v7_mechanical_layer_track_rows" / "output"
    manifest = json.loads(
        (output_root / "pcb_v7_mechanical_layer_track_rows.json").read_text(
            encoding="utf-8"
        )
    )
    _assert_mechanical_track_row_manifest(manifest)
    _assert_mechanical_track_row_outputs(output_root)


def test_draftsman_blank_project_example_writes_parseable_drawing(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "draftsman_create_blank_project"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumDraftsmanDocument
    from altium_monkey.altium_prjpcb import AltiumPrjPcb

    output_root = (
        check_examples_root
        / "draftsman_create_blank_project"
        / "output"
        / "RT_SUPER_C1_BLANK_DRAFTSMAN"
    )
    draftsman_path = output_root / "RT_SUPER_C1_Blank.PCBDwf"
    summary_path = (
        check_examples_root
        / "draftsman_create_blank_project"
        / "output"
        / "draftsman_create_blank_project.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    document = AltiumDraftsmanDocument.from_file(draftsman_path)
    prjpcb = AltiumPrjPcb(output_root / "RT_SUPER_C1.PrjPcb")
    project_documents = [str(document["path"]) for document in prjpcb.documents]

    assert (output_root / "RT_SUPER_C1.PCBdoc").exists()
    assert summary["source_document_name"] == "RT_SUPER_C1.PCBdoc"
    assert document.source_document_name == "RT_SUPER_C1.PCBdoc"
    assert document.format_version == 24
    assert len(document.pages) == 1
    assert len(document.items) == 0
    assert summary["is_blank_drawing"] is True
    assert summary["item_count"] == 0
    assert summary["note_count"] == 0
    assert summary["text_count"] == 0
    assert summary["picture_count"] == 0
    assert "RT_SUPER_C1_Blank.PCBDwf" in summary["project_documents"]
    assert "RT_SUPER_C1_Blank.PCBDwf" in project_documents


def test_hello_draftsman_example_writes_project_and_upper_left_note(
    check_examples_root: Path,
) -> None:
    example = next(item for item in _load_examples() if item["id"] == "hello_draftsman")
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumDraftsmanDocument, AltiumPcbDoc, AltiumSchDoc
    from altium_monkey.altium_prjpcb import AltiumPrjPcb

    project_root = check_examples_root / "hello_draftsman" / "output"
    project_path = project_root / "HELLO-DRAFTSMAN.PrjPcb"
    schdoc_path = project_root / "HELLO-DRAFTSMAN.SchDoc"
    pcbdoc_path = project_root / "HELLO-DRAFTSMAN.PcbDoc"
    draftsman_path = project_root / "HELLO-DRAFTSMAN.PCBDwf"

    for path in (project_path, schdoc_path, pcbdoc_path, draftsman_path):
        assert path.exists(), path

    project = AltiumPrjPcb(project_path)
    document_names = {Path(doc["path"]).name for doc in project.documents}
    assert {
        "HELLO-DRAFTSMAN.SchDoc",
        "HELLO-DRAFTSMAN.PcbDoc",
        "HELLO-DRAFTSMAN.PCBDwf",
    }.issubset(document_names)

    AltiumSchDoc(schdoc_path)
    pcbdoc = AltiumPcbDoc.from_file(pcbdoc_path)
    assert pcbdoc.board is not None
    assert pcbdoc.board.outline is not None

    document = AltiumDraftsmanDocument.from_file(draftsman_path)
    assert document.source_document_name == "HELLO-DRAFTSMAN.PcbDoc"
    assert len(document.notes) == 1
    assert len(document.pictures) == 1
    page = document.pages[0]
    assert page.size is not None
    note = document.notes[0]
    assert note.title == "altium monkey wuz here"
    assert note.title_font_style is not None
    assert note.title_font_style.family_name == "Comic Sans MS"
    assert note.title_font_style.size == pytest.approx(60.0)
    assert note.title_font_style.bold is True
    assert note.use_document_font_for_title is False
    assert note.element_font_style is not None
    assert note.element_font_style.family_name == "Comic Sans MS"
    assert note.element_font_style.size == pytest.approx(14.0)
    assert note.use_document_font_for_elements is False
    assert note.start_point is not None
    assert note.start_point.x_mm == pytest.approx(6.0)
    assert note.start_point.y_mm == pytest.approx(page.size.height_mm - 31.0)
    assert note.rect is not None
    assert note.rect.x_mm == pytest.approx(6.0)
    assert note.rect.y_mm == pytest.approx(page.size.height_mm - 31.0)
    assert note.rect.width_mm == pytest.approx(250.0)
    assert note.rect.height_mm == pytest.approx(0.0)

    picture = document.pictures[0]
    assert picture.maintain_aspect_ratio is True
    assert (
        picture.image_bytes
        == (
            check_examples_root / "hello_draftsman" / "assets" / "monkey.png"
        ).read_bytes()
    )
    assert picture.rect is not None
    assert picture.rect.x_mm == pytest.approx(3.0)
    assert picture.rect.y_mm == pytest.approx(113.64583333333331)
    assert picture.rect.width_mm == pytest.approx(115.85416666666663)
    assert picture.rect.height_mm == pytest.approx(115.85416666666663)


def test_draftsman_add_image_example_writes_text_and_picture(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "draftsman_add_image"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import (
        AltiumDraftsmanDocument,
        DraftsmanHorizontalAlignment,
        DraftsmanStandardSheetSize,
        DraftsmanVerticalAlignment,
    )

    output_root = check_examples_root / "draftsman_add_image" / "output"
    document = AltiumDraftsmanDocument.from_file(
        output_root / "draftsman_add_image.PCBDwf"
    )
    summary = json.loads(
        (output_root / "draftsman_add_image.json").read_text(encoding="utf-8")
    )
    asset_bytes = (
        check_examples_root / "draftsman_add_image" / "assets" / "monkey.png"
    ).read_bytes()

    assert document.pages[0].standard_sheet_size is DraftsmanStandardSheetSize.A3
    assert document.pages[0].margin is not None
    assert document.pages[0].margin.left_mm == pytest.approx(1.5)
    assert document.pages[0].margin.top_mm == pytest.approx(1.5)
    assert document.pages[0].margin.right_mm == pytest.approx(1.5)
    assert document.pages[0].margin.bottom_mm == pytest.approx(1.5)
    assert document.document_options.font_style is not None
    assert document.document_options.font_style.family_name == "Arial"
    assert document.document_options.font_style.size == pytest.approx(8.0)
    assert len(document.texts) == 1
    assert len(document.pictures) == 1
    assert summary["text_count"] == 1
    assert summary["picture_count"] == 1
    assert summary["border_zone_margin_mm"] == pytest.approx(1.5)

    text = document.texts[0]
    assert text.text == "altium-monkey wuz here"
    assert text.fill_style_id == 0
    assert text.use_document_font is False
    assert text.font_style is not None
    assert text.font_style.family_name == "Comic Sans MS"
    assert text.font_style.size == pytest.approx(24.0)
    assert text.font_style.bold is True
    assert text.horizontal_alignment is DraftsmanHorizontalAlignment.CENTER
    assert text.vertical_alignment is DraftsmanVerticalAlignment.CENTER

    picture = document.pictures[0]
    expected_image_height_mm = (
        80.0
        * summary["source_image_px"]["height"]
        / summary["source_image_px"]["width"]
    )
    assert picture.maintain_aspect_ratio is True
    assert picture.image_bytes == asset_bytes
    assert picture.rect is not None
    assert picture.rect.x_mm == pytest.approx((420.0 - 80.0) / 2.0)
    assert picture.rect.y_mm == pytest.approx((297.0 - expected_image_height_mm) / 2.0)
    assert picture.rect.width_mm == pytest.approx(80.0)
    assert picture.rect.height_mm == pytest.approx(expected_image_height_mm)


def test_draftsman_multipage_notes_example_writes_two_page_drawing(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "draftsman_multipage_notes"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumDraftsmanDocument, DraftsmanStandardSheetSize
    from altium_monkey.altium_prjpcb import AltiumPrjPcb

    output_root = check_examples_root / "draftsman_multipage_notes" / "output"
    draftsman_path = output_root / "draftsman_multipage_notes.PCBDwf"
    summary_path = (
        check_examples_root
        / "draftsman_multipage_notes"
        / "output"
        / "draftsman_multipage_notes.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    document = AltiumDraftsmanDocument.from_file(draftsman_path)
    project = AltiumPrjPcb(output_root / "draftsman_multipage_notes.PrjPcb")
    project_documents = {str(document["path"]) for document in project.documents}

    assert (output_root / "draftsman_multipage_notes.SchDoc").exists()
    assert (output_root / "draftsman_multipage_notes.PcbDoc").exists()
    assert document.source_document_name == "draftsman_multipage_notes.PcbDoc"
    assert summary["source_document_name"] == "draftsman_multipage_notes.PcbDoc"
    assert len(document.pages) == 2
    assert len(document.notes) == 2
    assert summary["page_ids"] == [page.id for page in document.pages]
    assert summary["item_ids"] == [note.id for note in document.notes]
    assert summary["note_titles"] == ["PAGE 1 NOTES", "PAGE 2 NOTES"]
    assert summary["lookup_demo"]["page_by_id"] == summary["page_ids"][1]
    assert summary["lookup_demo"]["note_by_id"] == "PAGE 2 NOTES"
    assert summary["lookup_demo"]["page_note_by_title"] == summary["item_ids"][1]
    assert {
        "draftsman_multipage_notes.SchDoc",
        "draftsman_multipage_notes.PcbDoc",
        "draftsman_multipage_notes.PCBDwf",
    }.issubset(project_documents)

    page_2 = document.page_by_id(summary["page_ids"][1])
    assert page_2 is not None
    assert page_2.standard_sheet_size is DraftsmanStandardSheetSize.A3
    page_2_note = page_2.note_by_title("page 2 notes")
    assert page_2_note is not None
    assert page_2_note.id == summary["item_ids"][1]
    assert document.note_by_id(summary["item_ids"][0]).page.id == summary["page_ids"][0]
    assert document.note_by_id(summary["item_ids"][1]).page.id == summary["page_ids"][1]
    assert len(summary["notes"][0]["row_ids"]) == 1
    assert len(summary["notes"][1]["row_ids"]) == 1


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
        check_examples_root / "prjpcb_make_project" / "output" / "ULTRA-MONKEY"
    )
    project_path = project_root / "ULTRA-MONKEY.PrjPcb"
    schdoc_path = project_root / "ULTRA-MONKEY.SchDoc"
    pcbdoc_path = project_root / "ULTRA-MONKEY.PcbDoc"
    outjob_path = project_root / "ULTRA-MONKEY.OutJob"

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
        "ULTRA-MONKEY.SchDoc",
        "ULTRA-MONKEY.PcbDoc",
        "ULTRA-MONKEY.OutJob",
    }.issubset(document_names)

    assert project.get_outjob_paths() == [outjob_path.resolve()]

    outjob = AltiumOutJob.from_outjob(outjob_path)
    outputs = outjob.get_output_types()
    documentation_outputs = [
        output for output in outputs if output["category"] == "Documentation"
    ]
    assert [output["type"] for output in documentation_outputs] == ["Schematic Print"]
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

    assert output_count >= 9
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


def test_schdoc_vertical_pin_svg_example_writes_rotation_proof(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "schdoc_vertical_pin_svg"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Vertical pin SVG proof: True" in result.stdout

    manifest_path = (
        check_examples_root
        / "schdoc_vertical_pin_svg"
        / "output"
        / "vertical_pin_svg_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "altium_monkey.examples.schdoc_vertical_pin_svg.a0"
    assert manifest["schlib"] == "output/schdoc_vertical_pin_svg.SchLib"
    assert manifest["case_count"] == 6
    assert manifest["symbol_name"] == "PIN_ROTATION_SYMBOL"
    assert manifest["placed_component"] == {
        "lib_reference": "PIN_ROTATION_SYMBOL",
        "source_library_name": "schdoc_vertical_pin_svg.SchLib",
        "design_item_id": "PIN_ROTATION_SYMBOL",
        "pin_count": 6,
    }
    assert manifest["placed_pin_record_count"] == 6
    assert manifest["placed_pin_records_are_text"] is True
    assert manifest["vertical_pin_names_rotated"] is True
    assert manifest["vertical_pin_designators_rotated"] is True
    assert manifest["component_horizontal_names_not_rotated"] is True
    assert manifest["component_horizontal_designators_not_rotated"] is True
    assert manifest["custom_vertical_names_rotated"] is True
    assert manifest["custom_vertical_designators_rotated"] is True
    assert manifest["all_cases_match_expected"] is True

    cases_by_id = {case["id"]: case for case in manifest["cases"]}
    assert cases_by_id["up_default"]["pin_orientation"] == "DEG_90"
    assert cases_by_id["down_default"]["pin_orientation"] == "DEG_270"
    assert cases_by_id["up_default"]["uses_custom_pin_text_settings"] is False
    assert cases_by_id["down_default"]["uses_custom_pin_text_settings"] is False
    assert cases_by_id["up_default"]["pin_record_has_custom_text_fields"] is False
    assert cases_by_id["down_default"]["pin_record_has_custom_text_fields"] is False
    assert cases_by_id["up_component_horizontal"]["name_rotated"] is False
    assert cases_by_id["down_component_horizontal"]["designator_rotated"] is False
    assert (
        cases_by_id["up_component_horizontal"]["pin_record_has_custom_text_fields"]
        is True
    )
    assert cases_by_id["right_custom_vertical"]["name_rotated"] is True
    assert cases_by_id["left_custom_vertical"]["designator_rotated"] is True
    assert (
        cases_by_id["right_custom_vertical"]["pin_record_has_custom_text_fields"]
        is True
    )
    assert (
        cases_by_id["left_custom_vertical"]["pin_record_has_custom_text_fields"] is True
    )

    svg_path = check_examples_root / "schdoc_vertical_pin_svg" / manifest["svg"]
    svg_text = svg_path.read_text(encoding="utf-8")
    assert "<svg" in svg_text[:200]
    assert 'transform="rotate(-90 ' in svg_text
    assert "UP_DEFAULT" in svg_text
    assert "DOWN_DEFAULT" in svg_text

    schlib_path = check_examples_root / "schdoc_vertical_pin_svg" / manifest["schlib"]
    schdoc_path = check_examples_root / "schdoc_vertical_pin_svg" / manifest["schdoc"]
    assert schlib_path.exists()
    assert schdoc_path.exists()

    from altium_monkey import AltiumSchDoc

    schdoc = AltiumSchDoc(schdoc_path)
    component = schdoc.components[0]
    assert component.lib_reference == "PIN_ROTATION_SYMBOL"
    assert component.source_library_name == "schdoc_vertical_pin_svg.SchLib"
    assert component.design_item_id == "PIN_ROTATION_SYMBOL"
    assert len(component.pins) == 6

    pin_json = [
        item for item in schdoc.to_json()["Objects"] if item.get("ObjectType") == "Pin"
    ]
    assert len(pin_json) == 6
    assert all("BinaryData" not in item for item in pin_json)
    assert {str(item["Name"]) for item in pin_json} == {
        "UP_DEFAULT",
        "DOWN_DEFAULT",
        "UP_COMPONENT_HORIZONTAL",
        "DOWN_COMPONENT_HORIZONTAL",
        "RIGHT_CUSTOM_VERTICAL",
        "LEFT_CUSTOM_VERTICAL",
    }


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


def test_pcbdoc_layer_stack_examples_write_semantic_manifests(
    check_examples_root: Path,
) -> None:
    inspect_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_inspect_layer_stack"
    )
    inspect_result = _run_example_entrypoint(inspect_example, check_examples_root)
    assert inspect_result.returncode == 0, inspect_result.stderr
    assert "Physical layers:" in inspect_result.stdout

    inspect_payload = json.loads(
        (
            check_examples_root
            / "pcbdoc_inspect_layer_stack"
            / "output"
            / "layer_stack.json"
        ).read_text(encoding="utf-8")
    )
    inspect_summary = inspect_payload["summary"]
    assert inspect_payload["input_kind"] == "pcbdoc"
    assert inspect_payload["input_path"] == "assets/pcbdoc/blank.PcbDoc"
    assert inspect_payload["input_pcbdoc"] == "assets/pcbdoc/blank.PcbDoc"
    assert inspect_summary["physical_layer_count"] > 0
    assert (
        inspect_summary["registry_entry_count"]
        >= (inspect_summary["physical_layer_count"])
    )
    assert inspect_payload["layer_stack"]["physical_stacks"]
    _assert_inspect_layer_stack_interchange_input(
        inspect_example,
        check_examples_root,
        suffix=".stackupx",
        source_text=_public_stackupx_fixture(),
        expected_kind="stackupx",
    )
    _assert_inspect_layer_stack_interchange_input(
        inspect_example,
        check_examples_root,
        suffix=".stackup",
        source_text=_public_stackup_text_fixture(),
        expected_kind="stackup",
    )

    create_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_create_layer_stack"
    )
    create_result = _run_example_entrypoint(create_example, check_examples_root)
    assert create_result.returncode == 0, create_result.stderr
    assert "Semantic match: True" in create_result.stdout

    manifest_path = (
        check_examples_root
        / "pcbdoc_create_layer_stack"
        / "output"
        / "layer_stack_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["semantic_match"] is True
    assert manifest["output_pcbdoc"] == (
        "pcbdoc_create_layer_stack/output/pcbdoc_create_layer_stack.PcbDoc"
    )
    assert manifest["authored"] == manifest["readback"]
    assert manifest["authored_stack_entry_count"] > 0
    assert manifest["authored"]["physical_stacks"][0]["layers"]

    from altium_monkey import AltiumPcbDoc
    from altium_monkey.altium_layer_stack_document import AltiumLayerStackDocument

    generated_pcbdoc = (
        check_examples_root
        / "pcbdoc_create_layer_stack"
        / "output"
        / "pcbdoc_create_layer_stack.PcbDoc"
    )
    stack = AltiumLayerStackDocument.from_pcbdoc(
        AltiumPcbDoc.from_file(generated_pcbdoc)
    )
    assert _semantic_physical_layer_names(stack) == [
        layer["display_name"]
        for layer in manifest["readback"]["physical_stacks"][0]["layers"]
    ]

    stackup_files_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_from_stackup_files"
    )
    stackup_files_result = _run_example_entrypoint(
        stackup_files_example,
        check_examples_root,
    )
    assert stackup_files_result.returncode == 0, stackup_files_result.stderr
    assert "Semantic matches: True" in stackup_files_result.stdout
    stackup_files_manifest_path = (
        check_examples_root
        / "pcbdoc_create_from_stackup_files"
        / "output"
        / "from_stackup_files_manifest.json"
    )
    stackup_files_manifest = json.loads(
        stackup_files_manifest_path.read_text(encoding="utf-8")
    )
    assert stackup_files_manifest["all_semantic_matches"] is True
    cases_by_kind = {
        case["source_kind"]: case for case in stackup_files_manifest["cases"]
    }
    assert set(cases_by_kind) == {"stackup", "stackupx"}
    assert cases_by_kind["stackup"]["source_origin"] == "stackup"
    assert cases_by_kind["stackupx"]["source_origin"] == "stackupx"
    assert cases_by_kind["stackup"]["output_pcbdoc"] == (
        "pcbdoc_create_from_stackup_files/output/from_stackup.PcbDoc"
    )
    assert cases_by_kind["stackupx"]["output_pcbdoc"] == (
        "pcbdoc_create_from_stackup_files/output/from_stackupx.PcbDoc"
    )
    for case in cases_by_kind.values():
        assert case["semantic_match"] is True
        assert case["families"].count("solder_mask") == 2
        assert [layer["display_name"] for layer in case["readback"]] == [
            "Top Overlay",
            "Top Solder",
            "Top Layer",
            "Dielectric 1",
            "GND",
            "Dielectric 2",
            "PWR",
            "Dielectric 3",
            "Bottom Layer",
            "Bottom Solder",
            "Bottom Overlay",
        ]

    custom_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_custom_rigid_stack"
    )
    custom_result = _run_example_entrypoint(custom_example, check_examples_root)
    assert custom_result.returncode == 0, custom_result.stderr
    assert "Semantic match: True" in custom_result.stdout
    assert "Copper layers: TOP_SIG, L2_GND, L3_PWR, BOT_SIG" in (custom_result.stdout)

    custom_manifest_path = (
        check_examples_root
        / "pcbdoc_create_custom_rigid_stack"
        / "output"
        / "custom_rigid_stack_manifest.json"
    )
    custom_manifest = json.loads(custom_manifest_path.read_text(encoding="utf-8"))
    assert custom_manifest["semantic_match"] is True
    assert custom_manifest["output_pcbdoc"] == (
        "pcbdoc_create_custom_rigid_stack/output/"
        "pcbdoc_create_custom_rigid_stack.PcbDoc"
    )
    assert custom_manifest["output_stackup"] == (
        "pcbdoc_create_custom_rigid_stack/output/"
        "pcbdoc_create_custom_rigid_stack.stackup"
    )
    assert custom_manifest["output_stackupx"] == (
        "pcbdoc_create_custom_rigid_stack/output/"
        "pcbdoc_create_custom_rigid_stack.stackupx"
    )
    assert (
        custom_manifest["authored"]
        == custom_manifest["readback"]
        == custom_manifest["stackup_readback"]
        == custom_manifest["stackupx_readback"]
    )
    custom_layers = custom_manifest["readback"]["physical_stacks"][0]["layers"]
    assert [
        layer["display_name"] for layer in custom_layers if layer["family"] == "copper"
    ] == ["TOP_SIG", "L2_GND", "L3_PWR", "BOT_SIG"]
    assert [
        layer["dielectric_material"]
        for layer in custom_layers
        if layer["family"] == "dielectric"
    ] == ["PP-TEST", "PP-TEST", "FR4-TEST", "PP-TEST", "PP-TEST"]
    assert custom_manifest["resolved"]["inner_signal_layers"] == [
        "L2_GND",
        "L3_PWR",
    ]

    custom_pcbdoc = (
        check_examples_root
        / "pcbdoc_create_custom_rigid_stack"
        / "output"
        / "pcbdoc_create_custom_rigid_stack.PcbDoc"
    )
    custom_stack = AltiumLayerStackDocument.from_pcbdoc(
        AltiumPcbDoc.from_file(custom_pcbdoc)
    )
    assert _semantic_physical_layer_names(custom_stack)[:4] == [
        "Top Overlay",
        "Top Solder",
        "TOP_SIG",
        "PP_TOP_1080",
    ]

    jlcpcb_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_jlcpcb_rigid_stack"
    )
    jlcpcb_source = (
        check_examples_root
        / "pcbdoc_create_jlcpcb_rigid_stack"
        / "pcbdoc_create_jlcpcb_rigid_stack.py"
    ).read_text(encoding="utf-8")
    assert "LayerSchema.ComponentPlacement" not in jlcpcb_source
    assert "stackupx_type_id" not in jlcpcb_source
    assert "legacy_layer_id" not in jlcpcb_source
    assert "STACKUP_ATTRIBUTES" not in jlcpcb_source
    assert "stackup_attributes=" not in jlcpcb_source
    assert "AltiumStackupSettings" in jlcpcb_source
    assert "c7ef040e-8d00-490b-b00c-a7e7823ff174" not in jlcpcb_source.lower()
    assert "f4eccd87-2cfb-4f37-be50-4f3a272b4d01" not in jlcpcb_source.lower()

    jlcpcb_result = _run_example_entrypoint(jlcpcb_example, check_examples_root)
    assert jlcpcb_result.returncode == 0, jlcpcb_result.stderr
    assert "Semantic match: True" in jlcpcb_result.stdout
    assert (
        "Copper layers: Top Layer, Inner Layer 1, Inner Layer 2, Inner Layer 3, "
        "Inner Layer 4, Inner Layer 5, Inner Layer 6, Bottom Layer"
    ) in jlcpcb_result.stdout
    assert "Dielectric rows: 9" in jlcpcb_result.stdout

    jlcpcb_manifest_path = (
        check_examples_root
        / "pcbdoc_create_jlcpcb_rigid_stack"
        / "output"
        / "jlcpcb_rigid_stack_manifest.json"
    )
    jlcpcb_manifest = json.loads(jlcpcb_manifest_path.read_text(encoding="utf-8"))
    assert jlcpcb_manifest["source_reference"] == (
        "JLCPCB JLC081211-1080 8-layer 1.2mm stackup"
    )
    assert jlcpcb_manifest["semantic_match"] is True
    assert jlcpcb_manifest["output_pcbdoc"] == (
        "pcbdoc_create_jlcpcb_rigid_stack/output/"
        "pcbdoc_create_jlcpcb_rigid_stack.PcbDoc"
    )
    assert jlcpcb_manifest["output_stackup"] == (
        "pcbdoc_create_jlcpcb_rigid_stack/output/"
        "pcbdoc_create_jlcpcb_rigid_stack.stackup"
    )
    assert jlcpcb_manifest["output_stackupx"] == (
        "pcbdoc_create_jlcpcb_rigid_stack/output/"
        "pcbdoc_create_jlcpcb_rigid_stack.stackupx"
    )
    assert (
        jlcpcb_manifest["authored"]
        == jlcpcb_manifest["pcbdoc_readback"]
        == jlcpcb_manifest["stackup_readback"]
        == jlcpcb_manifest["stackupx_readback"]
    )
    assert jlcpcb_manifest["copper_layers"] == [
        "Top Layer",
        "Inner Layer 1",
        "Inner Layer 2",
        "Inner Layer 3",
        "Inner Layer 4",
        "Inner Layer 5",
        "Inner Layer 6",
        "Bottom Layer",
    ]
    assert jlcpcb_manifest["dielectric_layers"] == [
        "Dielectric 1",
        "Dielectric 2",
        "Dielectric 3",
        "Dielectric 4",
        "Dielectric 5",
        "Dielectric 6",
        "Dielectric 7",
        "Dielectric 8",
        "Dielectric 9",
    ]
    assert (
        jlcpcb_manifest["stackup_attributes"]["authored"]["ViaPlatingThickness"]
        == "18um"
    )
    assert (
        jlcpcb_manifest["stackup_attributes"]["stackupx_readback"]["Type"] == "Standard"
    )
    assert jlcpcb_manifest["stackup_attributes"]["pcbdoc_readback"] == {}
    assert jlcpcb_manifest["stack_names"]["stackup_readback"] == "Master layer stack"
    assert jlcpcb_manifest["pcbdoc_readback"]["layers"][7]["display_name"] == (
        "Dielectric 3"
    )
    assert jlcpcb_manifest["pcbdoc_readback"]["layers"][8]["display_name"] == (
        "Dielectric 4"
    )
    assert jlcpcb_manifest["pcbdoc_readback"]["layer_pairs"] == [
        {
            "pair_index": 0,
            "low_layer_token": "TOP",
            "high_layer_token": "BOTTOM",
            "drill_pair_type_raw": 0,
            "is_backdrill": False,
        }
    ]

    jlcpcb_pcbdoc = (
        check_examples_root
        / "pcbdoc_create_jlcpcb_rigid_stack"
        / "output"
        / "pcbdoc_create_jlcpcb_rigid_stack.PcbDoc"
    )
    jlcpcb_stack = AltiumLayerStackDocument.from_pcbdoc(
        AltiumPcbDoc.from_file(jlcpcb_pcbdoc)
    )
    assert _semantic_physical_layer_names(jlcpcb_stack)[:5] == [
        "Top Overlay",
        "Top Solder",
        "Top Layer",
        "Dielectric 1",
        "Inner Layer 1",
    ]

    impedance_example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_impedance_rigid_stack"
    )
    impedance_result = _run_example_entrypoint(impedance_example, check_examples_root)
    assert impedance_result.returncode == 0, impedance_result.stderr
    assert "Semantic match: True" in impedance_result.stdout
    assert (
        "Copper layers: TOP_SIG, L2_GND, L3_SIG, L4_PWR, "
        "L5_GND, L6_SIG, L7_GND, BOT_SIG"
    ) in impedance_result.stdout
    assert (
        "Impedance profiles: SE50_TOP, SE50_L3, DIFF90_L6, SE50_BOTTOM"
    ) in impedance_result.stdout

    impedance_manifest_path = (
        check_examples_root
        / "pcbdoc_create_impedance_rigid_stack"
        / "output"
        / "impedance_rigid_stack_manifest.json"
    )
    impedance_manifest = json.loads(impedance_manifest_path.read_text(encoding="utf-8"))
    assert impedance_manifest["semantic_match"] is True
    assert impedance_manifest["output_pcbdoc"] == (
        "pcbdoc_create_impedance_rigid_stack/output/"
        "pcbdoc_create_impedance_rigid_stack.PcbDoc"
    )
    assert impedance_manifest["output_stackup"] == (
        "pcbdoc_create_impedance_rigid_stack/output/"
        "pcbdoc_create_impedance_rigid_stack.stackup"
    )
    assert impedance_manifest["output_stackupx"] == (
        "pcbdoc_create_impedance_rigid_stack/output/"
        "pcbdoc_create_impedance_rigid_stack.stackupx"
    )
    assert (
        impedance_manifest["authored"]
        == impedance_manifest["pcbdoc_readback"]
        == impedance_manifest["stackup_readback"]
        == impedance_manifest["stackupx_readback"]
    )
    assert impedance_manifest["pcbdoc_readback"]["copper_layers"] == [
        "TOP_SIG",
        "L2_GND",
        "L3_SIG",
        "L4_PWR",
        "L5_GND",
        "L6_SIG",
        "L7_GND",
        "BOT_SIG",
    ]
    assert [
        profile["name"]
        for profile in impedance_manifest["pcbdoc_readback"]["impedance_profiles"]
    ] == ["SE50_TOP", "SE50_L3", "DIFF90_L6", "SE50_BOTTOM"]
    assert [
        (line["layer_token"], line["top_ref_token"], line["bottom_ref_token"])
        for line in impedance_manifest["pcbdoc_readback"]["transmission_lines"]
    ] == [
        ("TOP", "", "MID1"),
        ("MID2", "MID1", "MID3"),
        ("MID5", "MID4", "MID6"),
        ("BOTTOM", "MID6", ""),
    ]
    impedance_board_entries = _pcbdoc_board_data_entries(
        check_examples_root / impedance_manifest["output_pcbdoc"]
    )
    assert any(
        entry.startswith("V9_STACKCUSTOMDATA=") for entry in impedance_board_entries
    )
    assert not any(
        entry.startswith(("IMPEDANCEPROFILE_V8_", "TRACEIMPEDANCE_V8_"))
        for entry in impedance_board_entries
    )

    rigid_flex_pcbdoc = (
        check_examples_root / "assets" / "pcbdoc" / "rigid_flex_topology_fixture.PcbDoc"
    )
    rigid_flex_stack = AltiumLayerStackDocument.from_pcbdoc(
        AltiumPcbDoc.from_file(rigid_flex_pcbdoc)
    )
    assert len(rigid_flex_stack.board_regions) == 9
    assert (
        sum(region.bending_line_count for region in rigid_flex_stack.board_regions) == 4
    )
    flex_regions_from_asset = [
        region
        for region in rigid_flex_stack.board_regions
        if region.layerstack_id == "{11111111-2222-4333-8444-555555555555}"
    ]
    assert [region.name for region in flex_regions_from_asset] == [
        "Flex West Arm",
        "Flex East Arm",
        "Flex South Arm",
        "Flex North Arm",
    ]
    assert [
        line.radius_mils
        for region in flex_regions_from_asset
        for line in region.bending_lines
    ] == [
        300.0,
        400.0,
        500.0,
        600.0,
    ]
    flex_dielectric = next(
        layer
        for layer in rigid_flex_stack.layers_for_substack(
            "{11111111-2222-4333-8444-555555555555}"
        )
        if layer.display_name == "Dielectric 2"
    )
    assert flex_dielectric.dielectric_material == "Kapton / Polyimide"
    assert flex_dielectric.dielectric_height_mils == pytest.approx(0.5)
    assert flex_dielectric.dielectric_constant == pytest.approx(3.4)
    assert flex_dielectric.dielectric_type == 4
    rigid_flex_resolved = rigid_flex_stack.to_resolved_layer_stack()
    assert [substack.name for substack in rigid_flex_resolved.substacks] == [
        "Board Layer Stack",
        "Research Flex",
    ]
    assert rigid_flex_resolved.substacks[1].layer_names == (
        "GND",
        "Dielectric 2",
        "PWR",
    )
    rigid_envelope = rigid_flex_resolved.stack_envelope_for_substack(
        rigid_flex_resolved.substacks[0].source_stackup_ref
    )
    flex_envelope = rigid_flex_resolved.stack_envelope_for_substack(
        rigid_flex_resolved.substacks[1].source_stackup_ref
    )
    assert rigid_envelope is not None
    assert flex_envelope is not None
    assert rigid_envelope.z_zero == "substack.local_midplane"
    assert flex_envelope.z_zero == "substack.local_midplane"
    assert rigid_envelope.is_flex is False
    assert flex_envelope.is_flex is True
    assert rigid_envelope.total_thickness_mils > flex_envelope.total_thickness_mils
    assert rigid_envelope.top_z_mils == pytest.approx(
        rigid_envelope.total_thickness_mils / 2.0
    )
    assert rigid_envelope.bottom_z_mils == pytest.approx(
        -rigid_envelope.total_thickness_mils / 2.0
    )
    assert [row.display_name for row in flex_envelope.layers] == [
        "GND",
        "Dielectric 2",
        "PWR",
    ]
    assert [row.family for row in flex_envelope.layers] == [
        "copper",
        "dielectric",
        "copper",
    ]
    assert flex_envelope.layers[0].z_top_mils == pytest.approx(flex_envelope.top_z_mils)
    assert flex_envelope.layers[-1].z_bottom_mils == pytest.approx(
        flex_envelope.bottom_z_mils
    )
    flex_region_envelope = rigid_flex_resolved.stack_envelope_for_board_region(
        "{11111111-2222-4333-8444-555555555555}"
    )
    assert flex_region_envelope == flex_envelope
    rigid_envelope_with_zero_rows = rigid_flex_resolved.stack_envelope_for_substack(
        rigid_flex_resolved.substacks[0].source_stackup_ref,
        include_zero_thickness_layers=True,
    )
    assert rigid_envelope_with_zero_rows is not None
    assert rigid_envelope_with_zero_rows.total_thickness_mils == pytest.approx(
        rigid_envelope.total_thickness_mils
    )
    assert [row.display_name for row in rigid_envelope.layers] != [
        row.display_name for row in rigid_envelope_with_zero_rows.layers
    ]
    assert "Top Paste" in [
        row.display_name for row in rigid_envelope_with_zero_rows.layers
    ]

    flex_topology_example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_flex_topology_report"
    )
    flex_topology_result = _run_example_entrypoint(
        flex_topology_example,
        check_examples_root,
    )
    assert flex_topology_result.returncode == 0, flex_topology_result.stderr
    assert "Rigid-flex mode: standard" in flex_topology_result.stdout
    assert "Substacks: 2" in flex_topology_result.stdout
    assert "Board regions: 9" in flex_topology_result.stdout
    assert "Bend lines: 4" in flex_topology_result.stdout
    assert "Branches: 0" in flex_topology_result.stdout

    flex_topology_root = check_examples_root / "pcbdoc_flex_topology_report"
    flex_topology_report = json.loads(
        (flex_topology_root / "output" / "flex_topology_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert flex_topology_report["uses_packaged_fixture"] is True
    assert flex_topology_report["input_pcbdoc"] == (
        "assets/pcbdoc/rigid_flex_topology_fixture.PcbDoc"
    )
    assert flex_topology_report["summary"] == {
        "active_stack_ref": "{E9E4740F-6231-4A54-9FA5-2B8E8601B7B2}",
        "rigid_flex_mode": "standard",
        "physical_stack_count": 1,
        "substack_count": 2,
        "flex_substack_count": 1,
        "board_region_count": 9,
        "bend_line_count": 4,
        "branch_count": 0,
        "layer_pair_count": 1,
        "resolved_substack_count": 2,
        "resolved_region_context_count": 9,
    }
    flex_substack_report = next(
        item
        for item in flex_topology_report["substacks"]
        if item["name"] == "Research Flex"
    )
    assert flex_substack_report["source_stackup_ref"] == (
        "{11111111-2222-4333-8444-555555555555}"
    )
    assert flex_substack_report["enabled_layer_names"] == [
        "GND",
        "Dielectric 2",
        "PWR",
    ]
    flex_substack_envelope = flex_substack_report["stack_envelope"]
    assert flex_substack_envelope["z_zero"] == "substack.local_midplane"
    assert flex_substack_envelope["is_flex"] is True
    assert flex_substack_envelope["total_thickness_mils"] == pytest.approx(
        flex_envelope.total_thickness_mils
    )
    assert [row["display_name"] for row in flex_substack_envelope["layers"]] == [
        "GND",
        "Dielectric 2",
        "PWR",
    ]
    assert flex_substack_report["region_names"] == [
        "Flex West Arm",
        "Flex East Arm",
        "Flex South Arm",
        "Flex North Arm",
    ]
    flex_regions = [
        region
        for region in flex_topology_report["board_regions"]
        if region["substack_name"] == "Research Flex"
    ]
    assert [region["name"] for region in flex_regions] == [
        "Flex West Arm",
        "Flex East Arm",
        "Flex South Arm",
        "Flex North Arm",
    ]
    assert [region["bend_lines"][0]["radius_mils"] for region in flex_regions] == [
        300.0,
        400.0,
        500.0,
        600.0,
    ]
    assert all(
        region["stack_envelope"]["total_thickness_mils"]
        == pytest.approx(flex_envelope.total_thickness_mils)
        for region in flex_regions
    )
    rigid_region = next(
        region
        for region in flex_topology_report["board_regions"]
        if region["substack_name"] == "Board Layer Stack"
    )
    assert rigid_region["stack_envelope"]["is_flex"] is False
    assert rigid_region["stack_envelope"]["total_thickness_mils"] == pytest.approx(
        rigid_envelope.total_thickness_mils
    )
    assert flex_topology_report["branches"] == []
    assert flex_topology_report["query_examples"]["stable_join_key"] == (
        "AltiumStackSubstack.source_stackup_ref == AltiumStackRegion.layerstack_id"
    )
    assert flex_topology_report["query_examples"]["regions_by_flex_substack_id"] == {
        "{11111111-2222-4333-8444-555555555555}": [
            "Flex West Arm",
            "Flex East Arm",
            "Flex South Arm",
            "Flex North Arm",
        ]
    }
    flex_topology_text = (
        flex_topology_root / "output" / "flex_topology_report.txt"
    ).read_text(encoding="utf-8")
    assert "Research Flex (flex)" in flex_topology_text
    assert "Flex West Arm -> Research Flex" in flex_topology_text
    assert "Substack Local Z Envelopes" in flex_topology_text
    assert "Research Flex (flex):" in flex_topology_text
    assert "(none in this standard rigid-flex fixture)" in flex_topology_text


def test_pcbdoc_rigid_flex_create_examples_verify_native_readback(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbDoc
    from altium_monkey.altium_layer_stack_document import AltiumLayerStackDocument

    cases = (
        {
            "id": "pcbdoc_create_flex_stiffener",
            "manifest": "pcbdoc_create_flex_stiffener/output/flex_stiffener_manifest.json",
            "spec": "assets/pcbdoc_layer_stack_specs/flex_stiffener.json",
            "exact_interchange_reference": True,
            "substack_count": 3,
            "region_count": 3,
            "branch_names": ["Board"],
            "stiffener_layers": ["Stiffener 1", "bottom_stiffener"],
            "bend_radii": [250.0],
            "geometry": {
                "board_outline_vertex_count": 13,
                "board_cutout_count": 1,
                "board_cutout_vertex_counts": [16],
                "polygon_count": 6,
                "polygon_outline_vertex_counts": [5, 5, 6, 6, 45, 45],
                "region_count": 7,
                "shapebased_region_count": 7,
                "shapebased_outline_vertex_counts": [17, 5, 5, 6, 6, 45, 45],
                "shapebased_layers": [56, 0, 0, 0, 0, 0, 0],
            },
        },
        {
            "id": "pcbdoc_create_rigid_flex_split_lines",
            "manifest": (
                "pcbdoc_create_rigid_flex_split_lines/output/"
                "rigid_flex_split_lines_manifest.json"
            ),
            "spec": "assets/pcbdoc_layer_stack_specs/rigid_flex_split_lines.json",
            "exact_interchange_reference": True,
            "substack_count": 2,
            "region_count": 3,
            "branch_names": ["Board"],
            "stiffener_layers": [],
            "bend_radii": [380.0002, 260.0001],
            "board_region_extended_tails": False,
            "region_v7_layers": [
                "MECHANICAL64532",
                "MECHANICAL64532",
                "MECHANICAL64533",
                "MECHANICAL64533",
            ],
        },
        {
            "id": "pcbdoc_create_flex_in_cutout",
            "manifest": (
                "pcbdoc_create_flex_in_cutout/output/flex_in_cutout_manifest.json"
            ),
            "spec": "assets/pcbdoc_layer_stack_specs/flex_in_cutout.json",
            "exact_interchange_reference": True,
            "substack_count": 2,
            "region_count": 3,
            "branch_names": ["Board"],
            "stiffener_layers": [],
            "bend_radii": [196.8504],
            "stream_sha256": {
                "Tracks6/Data": (
                    "656076e50c3ea1fc4e169d64ab282ae4d028cecfa65373ffaf9be16df7585f13"
                ),
                "Regions6/Data": (
                    "5af544734d1fd737e5d008cf2994adf8cdd4e3398aa3db454b4cc24c294130e4"
                ),
                "ShapeBasedRegions6/Data": (
                    "b2b132cb7c07039ab96d442da503b213520c4183d0d3af9856e0f1434adb3331"
                ),
                "BoardRegions/Data": (
                    "d703b8c4b6437603dfa9437e33f636c9d92adbff605b4fc0a032f7da60cad967"
                ),
            },
            "v9_layer_cache_names": {
                16908289: "Cutout",
                16908290: "InnerFlex",
                16908291: "InnerRigid",
                16908292: "Mechanical 4",
            },
            "enabled_mechanical_v7_save_ids": [
                16908289,
                16908290,
                16908291,
                16908292,
            ],
        },
        {
            "id": "pcbdoc_create_rigid_flex_branch",
            "manifest": (
                "pcbdoc_create_rigid_flex_branch/output/rigid_flex_branch_manifest.json"
            ),
            "spec": "assets/pcbdoc_layer_stack_specs/rigid_flex_branch_chain.json",
            "exact_interchange_reference": True,
            "substack_count": 3,
            "region_count": 2,
            "branch_names": ["MAIN_RIGID"],
            "stiffener_layers": [],
            "bend_radii": [500.0],
            "geometry": {
                "board_outline_vertex_count": 12,
                "board_cutout_count": 0,
                "board_cutout_vertex_counts": [],
                "polygon_count": 2,
                "polygon_outline_vertex_counts": [5, 5],
                "track_count": 20,
                "track_layers": [
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    39,
                    39,
                    39,
                    39,
                    40,
                    40,
                    40,
                    40,
                ],
                "region_count": 0,
                "shapebased_region_count": 0,
                "shapebased_outline_vertex_counts": [],
            },
        },
        {
            "id": "pcbdoc_create_rigid_flex_branch_intrusion",
            "manifest": (
                "pcbdoc_create_rigid_flex_branch_intrusion/output/"
                "rigid_flex_branch_intrusion_manifest.json"
            ),
            "spec": (
                "assets/pcbdoc_layer_stack_specs/rigid_flex_branch_intrusion.json"
            ),
            "exact_interchange_reference": True,
            "substack_count": 3,
            "region_count": 2,
            "branch_names": ["MAIN_RIGID"],
            "stiffener_layers": [],
            "bend_radii": [500.0],
        },
        {
            "id": "pcbdoc_create_rigid_flex_two_branch",
            "manifest": (
                "pcbdoc_create_rigid_flex_two_branch/output/"
                "rigid_flex_two_branch_manifest.json"
            ),
            "spec": (
                "assets/pcbdoc_layer_stack_specs/rigid_flex_two_branch_extensions.json"
            ),
            "exact_interchange_reference": True,
            "substack_count": 5,
            "region_count": 1,
            "branch_names": ["EAST_BRANCH", "WEST_BRANCH"],
            "stiffener_layers": [],
            "bend_radii": [500.0, 500.0],
        },
        {
            "id": "pcbdoc_create_rigid_flex_impedance_backdrill",
            "manifest": (
                "pcbdoc_create_rigid_flex_impedance_backdrill/output/"
                "rigid_flex_impedance_backdrill_manifest.json"
            ),
            "spec": (
                "assets/pcbdoc_layer_stack_specs/rigid_flex_impedance_backdrill.json"
            ),
            "exact_interchange_reference": True,
            "substack_count": 7,
            "region_count": 7,
            "branch_names": ["EAST_BRANCH", "WEST_BRANCH", "WEST_EXTENTION_BRANCH"],
            "stiffener_layers": [],
            "bend_radii": [250.0, 200.0, 250.0],
            "impedance_profile_count": 2,
            "transmission_line_count": 63,
            "layer_pair_count": 16,
            "backdrill_pair_count": 5,
            "region_v7_layers": [
                "MECHANICAL64540",
                "MECHANICAL64532",
                "MECHANICAL64541",
                "MECHANICAL64533",
            ],
            "stream_sha256": {
                "Regions6/Data": (
                    "c19bd501ff0db0e55c2da8afd64919035de7517532010bb02993cc01231d85a5"
                ),
                "ShapeBasedRegions6/Data": (
                    "3a71958346f8345fa234d362b8627d51083e687e499a0d508b95d7dcf9b7bfa8"
                ),
                "BoardRegions/Data": (
                    "e7c57d4f49fbfcb7e6f70a08b62442c1afb160cd0224acd9f9fd0898cf7b59d6"
                ),
            },
        },
        {
            "id": "pcbdoc_create_rigid_flex_multibranch",
            "manifest": (
                "pcbdoc_create_rigid_flex_multibranch/output/"
                "rigid_flex_multibranch_manifest.json"
            ),
            "spec": (
                "assets/pcbdoc_layer_stack_specs/rigid_flex_nested_multibranch.json"
            ),
            "exact_interchange_reference": True,
            "substack_count": 7,
            "region_count": 7,
            "branch_names": ["EAST_BRANCH", "WEST_BRANCH", "WEST_EXTENTION_BRANCH"],
            "stiffener_layers": [],
            "bend_radii": [250.0, 200.0, 250.0],
            "geometry": {
                "board_outline_vertex_count": 28,
                "board_cutout_count": 0,
                "board_cutout_vertex_counts": [],
                "polygon_count": 6,
                "polygon_outline_vertex_counts": [5, 5, 5, 5, 9, 21],
                "track_count": 28,
                "track_layers": [
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                    33,
                ],
                "region_count": 4,
                "shapebased_region_count": 4,
                "shapebased_outline_vertex_counts": [5, 5, 5, 5],
                "shapebased_layers": [0, 0, 0, 0],
            },
        },
    )

    examples = {str(item["id"]): item for item in _load_examples()}
    for case in cases:
        example = examples[str(case["id"])]
        result = _run_example_entrypoint(example, check_examples_root)
        assert result.returncode == 0, result.stderr
        assert "Semantic match: True" in result.stdout

        manifest = json.loads(
            (check_examples_root / str(case["manifest"])).read_text(encoding="utf-8")
        )
        assert manifest["semantic_match"] is True
        readback = manifest["pcbdoc_readback"]
        assert readback == manifest["authored"]
        spec: dict[str, object] | None = None
        if "spec" in case:
            spec = json.loads(
                (check_examples_root / str(case["spec"])).read_text(encoding="utf-8")
            )
            assert readback == spec["expected_native_signature"]
            assert manifest["pcbdoc_geometry"] == spec["source_geometry_counts"]
        assert len(readback["substacks"]) == case["substack_count"]
        assert len(readback["regions"]) == case["region_count"]
        assert [branch["name"] for branch in readback["branches"]] == case[
            "branch_names"
        ]
        assert readback["stiffener_layers"] == case["stiffener_layers"]
        assert [
            radius
            for region in readback["regions"]
            for radius in region["bend_radii_mils"]
        ] == case["bend_radii"]
        assert manifest["stackup_readback"]["branch_names"] == case["branch_names"]
        assert manifest["stackupx_readback"]["branch_names"] == case["branch_names"]
        stackupx_reference: Path | None = None
        if case.get("exact_interchange_reference"):
            assert manifest["stackup_exact_reference_match"] is True
            assert manifest["stackupx_exact_reference_match"] is True
            assert spec is not None
            spec_root = check_examples_root / "assets" / "pcbdoc_layer_stack_specs"
            stackup_reference = spec_root / str(spec["reference_stackup"])
            stackupx_reference = spec_root / str(spec["reference_stackupx"])
            assert (check_examples_root / manifest["output_stackup"]).read_bytes() == (
                stackup_reference.read_bytes()
            )
            assert (check_examples_root / manifest["output_stackupx"]).read_bytes() == (
                stackupx_reference.read_bytes()
            )
        if "geometry" in case:
            geometry = manifest["pcbdoc_geometry"]
            for key, expected in case["geometry"].items():
                assert geometry[key] == expected
        if "stream_sha256" in case:
            assert (
                _pcbdoc_stream_sha256(
                    check_examples_root / manifest["output_pcbdoc"],
                    case["stream_sha256"],
                )
                == case["stream_sha256"]
            )

        output_pcbdoc = AltiumPcbDoc.from_file(
            check_examples_root / manifest["output_pcbdoc"]
        )
        if "v9_layer_cache_names" in case:
            assert output_pcbdoc.board is not None
            expected_names = dict(case["v9_layer_cache_names"])
            for layer_id, expected_name in expected_names.items():
                assert output_pcbdoc.board.v9_layer_cache[layer_id] == expected_name
        if "enabled_mechanical_v7_save_ids" in case:
            assert output_pcbdoc.board is not None
            assert list(output_pcbdoc.board.enabled_mechanical_v7_save_ids) == list(
                case["enabled_mechanical_v7_save_ids"]
            )
        if "region_v7_layers" in case:
            expected_v7_layers = list(case["region_v7_layers"])
            assert [
                region.properties.get("V7_LAYER", "")
                for region in output_pcbdoc.regions
            ] == expected_v7_layers
            assert [
                region.properties.get("V7_LAYER", "")
                for region in output_pcbdoc.shapebased_regions
            ] == expected_v7_layers

        pcbdoc_stack = AltiumLayerStackDocument.from_pcbdoc(output_pcbdoc)
        assert len(pcbdoc_stack.substacks) == case["substack_count"]
        assert len(pcbdoc_stack.board_regions) == case["region_count"]
        assert [branch.name for branch in pcbdoc_stack.branches] == case["branch_names"]
        if "impedance_profile_count" in case:
            assert (
                len(pcbdoc_stack.impedance_profiles) == case["impedance_profile_count"]
            )
            assert (
                len(pcbdoc_stack.transmission_lines) == case["transmission_line_count"]
            )
            assert len(pcbdoc_stack.layer_pairs) == case["layer_pair_count"]
            assert (
                sum(1 for pair in pcbdoc_stack.layer_pairs if pair.is_backdrill is True)
                == case["backdrill_pair_count"]
            )
        board_data_entries = _pcbdoc_board_data_entries(
            check_examples_root / manifest["output_pcbdoc"]
        )
        if spec is not None:
            _assert_top_level_bend_cache_matches_spec(
                check_examples_root / manifest["output_pcbdoc"],
                spec,
            )
        _assert_v9_cache_contexts_cover_substacks(
            board_data_entries,
            tuple(pcbdoc_stack.substacks),
        )
        _assert_v8_board_rows_cover_authored_stack(
            board_data_entries,
            pcbdoc_stack,
        )
        _assert_native_board6_omits_stackupx_only_layers(
            board_data_entries,
            pcbdoc_stack,
        )
        if stackupx_reference is not None and case.get("exact_interchange_reference"):
            assert spec is not None
            if _spec_has_board_stack_source_entries(spec):
                _assert_board6_stack_source_entries_match_spec(
                    check_examples_root / manifest["output_pcbdoc"],
                    spec,
                )
            else:
                _assert_embedded_stackupx_stack_layer_signature_matches_reference(
                    check_examples_root / manifest["output_pcbdoc"],
                    stackupx_reference,
                )
            _assert_embedded_stackupx_branch_stack_refs_are_defined(
                check_examples_root / manifest["output_pcbdoc"],
            )
        if case.get("board_region_extended_tails", True):
            _assert_board_regions_have_extended_outline_tails(
                check_examples_root / manifest["output_pcbdoc"],
                expected_region_count=int(case["region_count"]),
            )
        else:
            _assert_board_regions_have_no_extended_outline_tails(
                check_examples_root / manifest["output_pcbdoc"],
                expected_region_count=int(case["region_count"]),
            )


def _assert_inspect_layer_stack_interchange_input(
    example: dict[str, object],
    check_examples_root: Path,
    *,
    suffix: str,
    source_text: str,
    expected_kind: str,
) -> None:
    entrypoint = example.get("entrypoint")
    if not isinstance(entrypoint, str):
        raise TypeError("pcbdoc_inspect_layer_stack entrypoint must be a string")
    sample_root = check_examples_root / "pcbdoc_inspect_layer_stack"
    input_path = sample_root / "output" / f"public_fixture{suffix}"
    output_path = sample_root / "output" / f"public_fixture{suffix}.json"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(source_text, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(check_examples_root / entrypoint),
            str(input_path),
            "--output",
            str(output_path),
        ],
        cwd=check_examples_root.parent,
        env=_example_subprocess_env(),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert f"Input kind: {expected_kind}" in result.stdout
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["input_kind"] == expected_kind
    assert payload["input_path"].endswith(f"public_fixture{suffix}")
    assert "input_pcbdoc" not in payload
    summary = payload["summary"]
    assert summary["physical_layer_count"] == 3
    assert summary["layer_pair_count"] == 1
    assert summary["top_layer"] == "Top Layer"
    assert summary["bottom_layer"] == "Bottom Layer"
    assert summary["impedance_profile_count"] == 0
    assert payload["layer_stack"]["physical_stacks"][0]["layer_names"] == [
        "Top Layer",
        "Dielectric 1",
        "Bottom Layer",
    ]


def _public_stackupx_fixture() -> str:
    return """\
<StackupDocument SerializerVersion="1.1.0.0" Version="2.1.0.0" Id="5e23cf90-7f1e-56c4-9d7f-f9848caa1f57" RevisionId="655af9a0-2a77-594c-8702-23a8344da31f" RevisionDate="2026-06-02T00:00:00Z" xmlns="http://altium.com/ns/LayerStackManager">
  <FeatureSet>
    <Feature Id="c8939e8a-fd0e-4d52-8860-b7a98f452016">Standard Stackup</Feature>
  </FeatureSet>
  <Stackup Type="Standard">
    <Stacks>
      <Stack Id="158443b6-0e30-5d76-ba89-f5afd97568c2" Name="Board Layer Stack" IsFlex="False">
        <Layers>
          <Layer Id="7f143fce-4b68-5bb8-8618-c0a15c02f350" TypeId="f4eccd87-2cfb-4f37-be50-4f3a272b4d01" Name="Top Layer" IsShared="True">
            <Properties>
              <Property Name="Thickness" Type="LengthValue">1.4mil</Property>
            </Properties>
          </Layer>
          <Layer Id="ade33e16-15de-5c22-aaab-44a390808b71" TypeId="92b02d5e-8d69-48a8-880e-ac4b77db099d" Name="Dielectric 1" IsShared="True">
            <Properties>
              <Property Name="Thickness" Type="LengthValue">10mil</Property>
              <Property Name="Material" Type="String">FR-4</Property>
              <Property Name="DielectricConstant" Type="DimensionlessValue">4.2</Property>
            </Properties>
          </Layer>
          <Layer Id="6432c9ac-9979-5781-bfde-55c5cf20a0db" TypeId="f4eccd87-2cfb-4f37-be50-4f3a272b4d01" Name="Bottom Layer" IsShared="True">
            <Properties>
              <Property Name="Thickness" Type="LengthValue">1.4mil</Property>
            </Properties>
          </Layer>
        </Layers>
        <ViaSpans>
          <ViaSpan Id="e46178bd-d5da-5888-9d19-57943d0b9b5f" AutoName="Thru 1:2" Type="ThruVia" StartLayerId="7f143fce-4b68-5bb8-8618-c0a15c02f350" StopLayerId="6432c9ac-9979-5781-bfde-55c5cf20a0db" />
        </ViaSpans>
      </Stack>
    </Stacks>
  </Stackup>
</StackupDocument>
"""


def _public_stackup_text_fixture() -> str:
    rows = [
        "STACKUPVERSION=1",
        "STACKUPDOCUMENTID={9A4F0AD9-5BCF-5836-94C9-FAA039353EC6}",
        "STACKUPDOCUMENTREVISIONID={655AF9A0-2A77-594C-8702-23A8344DA31F}",
        "STACKUPDOCUMENTREVISIONDATE=06/02/2026 00:00:00",
        "FEATURE_0ID={C8939E8A-FD0E-4D52-8860-B7A98F452016}",
        "FEATURE_0NAME=Standard Stackup",
        "LAYERMASTERSTACK_V8ID={9A4F0AD9-5BCF-5836-94C9-FAA039353EC6}",
        "LAYERMASTERSTACK_V8NAME=Master layer stack",
        "LAYERSUBSTACK_V8_0ID={158443B6-0E30-5D76-BA89-F5AFD97568C2}",
        "LAYERSUBSTACK_V8_0NAME=Board Layer Stack",
        "LAYERSUBSTACK_V8_0ISFLEX=False",
        "LAYERSUBSTACK_V8_0USEDBYPRIMS=False",
        "LAYER_V8_0LAYERID=16777217",
        "LAYER_V8_0ID={7F143FCE-4B68-5BB8-8618-C0A15C02F350}",
        "LAYER_V8_0NAME=Top Layer",
        "LAYER_V8_0COPTHICK=1.4mil",
        "LAYER_V8_1LAYERID=17039361",
        "LAYER_V8_1ID={ADE33E16-15DE-5C22-AAAB-44A390808B71}",
        "LAYER_V8_1NAME=Dielectric 1",
        "LAYER_V8_1DIELHEIGHT=10mil",
        "LAYER_V8_1DIELCONST=4.2",
        "LAYER_V8_1DIELMATERIAL=FR-4",
        "LAYER_V8_2LAYERID=16842751",
        "LAYER_V8_2ID={6432C9AC-9979-5781-BFDE-55C5CF20A0DB}",
        "LAYER_V8_2NAME=Bottom Layer",
        "LAYER_V8_2COPTHICK=1.4mil",
        "VIASPAN_V8_{158443B6-0E30-5D76-BA89-F5AFD97568C2}_0ID={E46178BD-D5DA-5888-9D19-57943D0B9B5F}",
        "VIASPAN_V8_{158443B6-0E30-5D76-BA89-F5AFD97568C2}_0NAME=Thru 1:2",
        "VIASPAN_V8_{158443B6-0E30-5D76-BA89-F5AFD97568C2}_0TYPE=0",
        "VIASPAN_V8_{158443B6-0E30-5D76-BA89-F5AFD97568C2}_0FIRSTLAYERID={7F143FCE-4B68-5BB8-8618-C0A15C02F350}",
        "VIASPAN_V8_{158443B6-0E30-5D76-BA89-F5AFD97568C2}_0LASTLAYERID={6432C9AC-9979-5781-BFDE-55C5CF20A0DB}",
    ]
    return "|" + "|".join(rows) + "\n"


def _semantic_physical_layer_names(
    stack: object,
) -> list[str]:
    physical_stacks = list(getattr(stack, "physical_stacks", ()) or ())
    if not physical_stacks:
        return []
    return [
        str(getattr(layer, "display_name", ""))
        for layer in tuple(getattr(physical_stacks[0], "layers", ()) or ())
    ]


def _pcbdoc_board_data_entries(path: Path) -> tuple[str, ...]:
    from altium_monkey.altium_ole import AltiumOleFile
    from altium_monkey.altium_pcbdoc_builder import PcbDocBoardData

    with AltiumOleFile(str(path)) as ole:
        board_data = PcbDocBoardData.from_bytes(ole.openstream(["Board6", "Data"]))
    return tuple(
        str(getattr(entry, "raw", "") or "")
        for segment in tuple(board_data.segments)
        for entry in tuple(segment.entries)
    )


def _pcbdoc_top_level_board_data_entries(path: Path) -> tuple[str, ...]:
    from altium_monkey.altium_ole import AltiumOleFile
    from altium_monkey.altium_pcbdoc_builder import PcbDocBoardData

    with AltiumOleFile(str(path)) as ole:
        board_data = PcbDocBoardData.from_bytes(ole.openstream(["Board6", "Data"]))
    return tuple(
        str(getattr(entry, "raw", "") or "")
        for entry in tuple(board_data.top_level_segment.entries)
    )


def _pcbdoc_stream_sha256(
    path: Path,
    expected_streams: object,
) -> dict[str, str]:
    from altium_monkey.altium_ole import AltiumOleFile

    if not isinstance(expected_streams, dict):
        raise TypeError("expected streams must be a mapping")
    result: dict[str, str] = {}
    with AltiumOleFile(str(path)) as ole:
        for stream_name in expected_streams:
            stream_parts = str(stream_name).split("/")
            result[str(stream_name)] = hashlib.sha256(
                ole.openstream(stream_parts)
            ).hexdigest()
    return result


def _assert_top_level_bend_cache_matches_spec(
    pcbdoc_path: Path,
    spec: dict[str, object],
) -> None:
    document = spec.get("layer_stack_document")
    assert isinstance(document, dict)
    board_split_lines = document.get("board_split_lines", [])
    board_bending_lines = document.get("board_bending_lines", [])
    board_arc_split_lines = document.get("board_arc_split_lines", [])
    assert isinstance(board_split_lines, list)
    assert isinstance(board_bending_lines, list)
    assert isinstance(board_arc_split_lines, list)
    if not board_split_lines and not board_bending_lines and not board_arc_split_lines:
        return

    expected: list[str] = []
    for index, value in enumerate(board_split_lines):
        expected.append(f"SPLITLINE{index}={value}")
    if board_split_lines:
        expected.append(f"SPLITLINECOUNT={len(board_split_lines)}")
    elif board_bending_lines or board_arc_split_lines:
        expected.append("SPLITLINECOUNT=0")
    if board_bending_lines or board_arc_split_lines:
        expected.append(f"BENDINGLINECOUNT={len(board_bending_lines)}")
        for index, line in enumerate(board_bending_lines):
            assert isinstance(line, dict)
            expected.append(f"BENDINGLINE{index}={line['raw_value']}")
    if board_arc_split_lines:
        expected.append(f"ARCSPLITLINESCOUNT={len(board_arc_split_lines)}")
        for index, value in enumerate(board_arc_split_lines):
            expected.append(f"ARCSPLITLINE{index}={value}")

    raw_entries = _pcbdoc_top_level_board_data_entries(pcbdoc_path)
    actual = tuple(
        raw
        for raw in raw_entries
        if raw.startswith("SPLITLINE")
        or raw.startswith("BENDINGLINE")
        or raw.startswith("ARCSPLITLINE")
    )
    assert actual == tuple(expected)


def _assert_board_regions_have_extended_outline_tails(
    path: Path,
    *,
    expected_region_count: int,
) -> None:
    from altium_monkey.altium_ole import AltiumOleFile

    with AltiumOleFile(str(path)) as ole:
        data = ole.openstream(["BoardRegions", "Data"])

    tail_lengths: list[int] = []
    offset = 0
    while offset < len(data) - 1:
        if data[offset] != 0x0B:
            offset += 1
            continue
        record_length = struct.unpack("<I", data[offset + 1 : offset + 5])[0]
        content = data[offset + 5 : offset + 5 + record_length]
        property_length = struct.unpack("<I", content[18:22])[0]
        cursor = 22 + property_length
        outline_count = struct.unpack("<I", content[cursor : cursor + 4])[0]
        cursor += 4 + outline_count * 16
        hole_count = struct.unpack("<H", content[14:16])[0]
        for _ in range(hole_count):
            hole_vertex_count = struct.unpack("<I", content[cursor : cursor + 4])[0]
            cursor += 4 + hole_vertex_count * 16
        tail = content[cursor:]
        tail_vertex_count = struct.unpack("<I", tail[:4])[0]
        assert 0 < tail_vertex_count <= outline_count
        assert len(tail) == 4 + (tail_vertex_count + 1) * 37
        tail_lengths.append(len(tail))
        offset += 5 + record_length

    assert len(tail_lengths) == expected_region_count


def _assert_board_regions_have_no_extended_outline_tails(
    path: Path,
    *,
    expected_region_count: int,
) -> None:
    tail_lengths = _board_region_trailing_byte_lengths(path)
    assert tail_lengths == [0] * expected_region_count


def _board_region_trailing_byte_lengths(path: Path) -> list[int]:
    from altium_monkey.altium_ole import AltiumOleFile

    with AltiumOleFile(str(path)) as ole:
        data = ole.openstream(["BoardRegions", "Data"])

    tail_lengths: list[int] = []
    offset = 0
    while offset < len(data) - 1:
        if data[offset] != 0x0B:
            offset += 1
            continue
        record_length = struct.unpack("<I", data[offset + 1 : offset + 5])[0]
        content = data[offset + 5 : offset + 5 + record_length]
        property_length = struct.unpack("<I", content[18:22])[0]
        cursor = 22 + property_length
        outline_count = struct.unpack("<I", content[cursor : cursor + 4])[0]
        cursor += 4 + outline_count * 16
        hole_count = struct.unpack("<H", content[14:16])[0]
        for _ in range(hole_count):
            hole_vertex_count = struct.unpack("<I", content[cursor : cursor + 4])[0]
            cursor += 4 + hole_vertex_count * 16
        tail_lengths.append(len(content[cursor:]))
        offset += 5 + record_length
    return tail_lengths


def _assert_v9_cache_contexts_cover_substacks(
    raw_entries: tuple[str, ...],
    substacks: tuple[object, ...],
) -> None:
    context_refs: set[str] = set()
    for raw in raw_entries:
        key = raw.split("=", 1)[0]
        match = re.match(r"^V9_CACHE_LAYER\d+_(.+)CONTEXT$", key, re.IGNORECASE)
        if match is None:
            continue
        context_refs.add(_normalized_native_ref(match.group(1)))

    expected_refs = {
        _normalized_native_ref(getattr(substack, "source_stackup_ref", ""))
        for substack in substacks
        if getattr(substack, "source_stackup_ref", "")
    }
    assert expected_refs
    assert expected_refs <= context_refs

    default_ref = _canonical_empty_substack_ref()
    if default_ref not in expected_refs:
        assert default_ref not in context_refs


def _assert_v8_board_rows_cover_authored_stack(
    raw_entries: tuple[str, ...],
    stack: object,
) -> None:
    substacks = tuple(getattr(stack, "substacks", ()) or ())
    physical_stacks = tuple(getattr(stack, "physical_stacks", ()) or ())
    assert substacks
    assert physical_stacks

    v8_substack_refs: set[str] = set()
    v8_context_refs: set[str] = set()
    v8_layer_names: dict[int, str] = {}
    for raw in raw_entries:
        key, _separator, value = raw.partition("=")
        substack_match = re.match(
            r"^LAYERSUBSTACK_V8_\d+ID$",
            key,
            re.IGNORECASE,
        )
        if substack_match is not None:
            v8_substack_refs.add(_normalized_native_ref(value))
            continue
        context_match = re.match(
            r"^LAYER_V8_\d+_(.+)CONTEXT$",
            key,
            re.IGNORECASE,
        )
        if context_match is not None:
            v8_context_refs.add(_normalized_native_ref(context_match.group(1)))
            continue
        name_match = re.match(
            r"^LAYER_V8_(\d+)NAME$",
            key,
            re.IGNORECASE,
        )
        if name_match is not None:
            v8_layer_names[int(name_match.group(1))] = value

    expected_refs = {
        _normalized_native_ref(getattr(substack, "source_stackup_ref", ""))
        for substack in substacks
        if getattr(substack, "source_stackup_ref", "")
    }
    assert expected_refs <= v8_substack_refs
    assert expected_refs <= v8_context_refs
    default_ref = _canonical_empty_substack_ref()
    if default_ref not in expected_refs:
        assert default_ref not in v8_substack_refs
        assert default_ref not in v8_context_refs

    authored_layer_names = [
        str(getattr(layer, "display_name", ""))
        for layer in tuple(getattr(physical_stacks[0], "layers", ()) or ())
        if _native_board6_emits_layer_row(layer)
    ]
    assert authored_layer_names
    ordered_v8_names = [v8_layer_names[index] for index in sorted(v8_layer_names)]
    assert ordered_v8_names[: len(authored_layer_names)] == authored_layer_names
    assert "Drill Guide" in ordered_v8_names
    assert "Keep-Out Layer" in ordered_v8_names
    assert "Mechanical 32" in ordered_v8_names

    if any(
        getattr(layer, "is_stiffener", None) is True
        for physical_stack in physical_stacks
        for layer in tuple(getattr(physical_stack, "layers", ()) or ())
    ):
        coverlay_names = [
            str(getattr(layer, "display_name", ""))
            for physical_stack in physical_stacks
            for layer in tuple(getattr(physical_stack, "layers", ()) or ())
            if getattr(layer, "coverlay_expansion", "")
        ]
        expected_tail_names = [
            f"{coverlay_name} ({getattr(substack, 'name', '')})"
            for substack in substacks
            for coverlay_name in coverlay_names
        ]
        assert expected_tail_names
        assert ordered_v8_names[-len(expected_tail_names) :] == expected_tail_names


def _assert_native_board6_omits_stackupx_only_layers(
    raw_entries: tuple[str, ...],
    stack: object,
) -> None:
    physical_stacks = tuple(getattr(stack, "physical_stacks", ()) or ())
    if not physical_stacks:
        return
    stackupx_only_names = {
        str(getattr(layer, "display_name", ""))
        for layer in tuple(getattr(physical_stacks[0], "layers", ()) or ())
        if not _native_board6_emits_layer_row(layer)
    }
    if not stackupx_only_names:
        return

    for raw in raw_entries:
        key, _separator, value = raw.partition("=")
        assert "ISSURFACEFINISH" not in key.upper()
        if re.search(
            r"^(V9_STACK_LAYER|LAYER_V8_|V9_CACHE_LAYER)\d+_?NAME$",
            key,
            re.IGNORECASE,
        ):
            assert value not in stackupx_only_names


def _assert_board6_stack_source_entries_match_spec(
    pcbdoc_path: Path,
    spec: dict[str, object],
) -> None:
    from altium_monkey.altium_layer_stack_document import (
        is_layer_stack_source_entry_key,
    )
    from altium_monkey.altium_ole import AltiumOleFile
    from altium_monkey.altium_pcbdoc_builder import PcbDocBoardData

    document = spec.get("layer_stack_document")
    assert isinstance(document, dict)
    source = document.get("source")
    assert isinstance(source, dict)
    expected_entries = source.get("board_stack_entry_texts")
    assert isinstance(expected_entries, list)
    assert expected_entries

    with AltiumOleFile(str(pcbdoc_path)) as ole:
        board_data = PcbDocBoardData.from_bytes(ole.openstream(["Board6", "Data"]))
    actual_entries = [
        str(getattr(entry, "raw", "") or "")
        for segment in tuple(board_data.segments)
        for entry in tuple(segment.entries)
        if is_layer_stack_source_entry_key(getattr(entry, "key", None))
    ]

    assert [_normalized_board_stack_entry(item) for item in actual_entries] == [
        _normalized_board_stack_entry(str(item)) for item in expected_entries
    ]


def _spec_has_board_stack_source_entries(spec: dict[str, object]) -> bool:
    document = spec.get("layer_stack_document")
    if not isinstance(document, dict):
        return False
    source = document.get("source")
    if not isinstance(source, dict):
        return False
    entries = source.get("board_stack_entry_texts")
    return isinstance(entries, list) and bool(entries)


def _normalized_board_stack_entry(raw: str) -> str:
    key, separator, value = raw.partition("=")
    if separator and key.upper() == "V9_STACKCUSTOMDATA":
        xml_bytes = zlib.decompress(base64.b64decode(value))
        if xml_bytes.startswith(b"?<"):
            xml_bytes = xml_bytes[1:]
        return f"{key}#xml-sha256={hashlib.sha256(xml_bytes).hexdigest()}"
    return raw


def _native_board6_emits_layer_row(layer: object) -> bool:
    return str(getattr(layer, "family", "") or "").strip().lower() != "surface_finish"


def _assert_embedded_stackupx_stack_layer_signature_matches_reference(
    pcbdoc_path: Path,
    stackupx_reference: Path,
) -> None:
    from altium_monkey.altium_stackupx import AltiumStackupXDocument

    embedded = _embedded_stackupx_from_pcbdoc(pcbdoc_path)
    reference = AltiumStackupXDocument.from_bytes(stackupx_reference.read_bytes())
    assert _stackupx_stack_layer_signature(embedded) == _stackupx_stack_layer_signature(
        reference
    )


def _assert_embedded_stackupx_bytes_match_reference(
    pcbdoc_path: Path,
    stackupx_reference: Path,
) -> None:
    embedded = _embedded_stackupx_bytes_from_pcbdoc(pcbdoc_path)
    reference = stackupx_reference.read_bytes()
    if reference.startswith(b"\xef\xbb\xbf"):
        reference = reference[3:]
    assert embedded == reference


def _assert_embedded_stackupx_branch_stack_refs_are_defined(pcbdoc_path: Path) -> None:
    embedded = _embedded_stackupx_from_pcbdoc(pcbdoc_path)
    stack_ids = {
        _normalized_native_ref(getattr(stack, "id", ""))
        for stack in tuple(getattr(embedded, "stacks", ()) or ())
        if _normalized_native_ref(getattr(stack, "id", ""))
    }
    branch_refs: set[str] = set()
    for branch in tuple(getattr(embedded, "branches", ()) or ()):
        for section in tuple(getattr(branch, "sections", ()) or ()):
            for stack in tuple(getattr(section, "stacks", ()) or ()):
                for value in (
                    getattr(stack, "layer_stack_id", ""),
                    getattr(stack, "source_layer_stack_id", ""),
                    getattr(stack, "parent_layer_stack_id", ""),
                ):
                    key = _normalized_native_ref(value)
                    if key:
                        branch_refs.add(key)
    assert branch_refs <= stack_ids


def _embedded_stackupx_from_pcbdoc(pcbdoc_path: Path) -> object:
    from altium_monkey.altium_stackupx import AltiumStackupXDocument

    return AltiumStackupXDocument.from_bytes(
        _embedded_stackupx_bytes_from_pcbdoc(pcbdoc_path)
    )


def _embedded_stackupx_bytes_from_pcbdoc(pcbdoc_path: Path) -> bytes:
    from altium_monkey import AltiumPcbDoc

    pcbdoc = AltiumPcbDoc.from_file(pcbdoc_path)
    if pcbdoc.board is None:
        raise AssertionError(f"{pcbdoc_path} does not contain Board6/Data")
    token = pcbdoc.board.raw_record["V9_STACKCUSTOMDATA"]
    xml_bytes = zlib.decompress(base64.b64decode(token))
    if xml_bytes.startswith(b"?<"):
        xml_bytes = xml_bytes[1:]
    return xml_bytes


def _stackupx_stack_layer_signature(
    stackupx: object,
) -> tuple[
    tuple[
        str,
        str,
        bool | None,
        str,
        tuple[
            tuple[
                str,
                bool | None,
                bool | None,
                tuple[tuple[str, str, str], ...],
            ],
            ...,
        ],
    ],
    ...,
]:
    return tuple(
        (
            _normalized_native_ref(getattr(stack, "id", "")),
            str(getattr(stack, "name", "")),
            getattr(stack, "is_flex", None),
            str(getattr(stack, "stack_type", "")),
            tuple(
                (
                    str(getattr(layer, "name", "")),
                    getattr(layer, "is_enabled", None),
                    getattr(layer, "is_shared", None),
                    tuple(
                        sorted(
                            (
                                str(getattr(prop, "name", "")),
                                str(getattr(prop, "type_name", "")),
                                str(getattr(prop, "value", "")),
                            )
                            for prop in tuple(getattr(layer, "properties", ()) or ())
                        )
                    ),
                )
                for layer in tuple(getattr(stack, "layers", ()) or ())
            ),
        )
        for stack in tuple(getattr(stackupx, "stacks", ()) or ())
    )


def _canonical_empty_substack_ref() -> str:
    from altium_monkey.altium_pcbdoc_builder import PcbDocBoardData

    return _normalized_native_ref(PcbDocBoardData.default().default_substack_guid())


def _normalized_native_ref(value: object) -> str:
    return str(value or "").strip().strip("{}").upper()


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


def test_embedded_asset_inventory_to_dict_matches_public_schema() -> None:
    from altium_monkey import (
        AltiumPcbDoc,
        AltiumPcbLib,
        EmbeddedFont,
        PcbLayer,
        embedded_asset_schema_id,
    )

    validator = _embedded_asset_schema_validator()
    model_payload = b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n"
    font_payload = b"\x00\x01\x00\x00embedded font payload"

    pcbdoc = AltiumPcbDoc()
    pcbdoc_model = pcbdoc.add_embedded_model(
        name="board-model.step",
        model_data=model_payload,
        model_id="{10101010-1010-1010-1010-101010101010}",
        checksum=1010,
    )
    pcbdoc.add_embedded_3d_model(
        pcbdoc_model,
        bounds_mils=(-10.0, -10.0, 10.0, 10.0),
        overall_height_mils=12.0,
    )
    pcbdoc.embedded_fonts = [
        EmbeddedFont(
            name="Preview Font",
            style="Regular",
            compressed_data=zlib.compress(font_payload),
            raw_data=b"font-record" + zlib.compress(font_payload),
        )
    ]
    pcbdoc_payload = pcbdoc.embedded_asset_inventory(include_hashes=True).to_dict()
    _assert_schema_valid(validator, pcbdoc_payload, "pcbdoc embedded inventory")
    assert pcbdoc_payload["schema"] == embedded_asset_schema_id()
    assert pcbdoc_payload["source_kind"] == "pcbdoc"
    assert pcbdoc_payload["source_path"] is None
    assert pcbdoc_payload["opaque_assets"] == []
    assert pcbdoc_payload["models"][0]["payload_sha256"] == (  # type: ignore[index]
        hashlib.sha256(model_payload).hexdigest()
    )
    assert pcbdoc_payload["fonts"][0]["payload_sha256"] == (  # type: ignore[index]
        hashlib.sha256(font_payload).hexdigest()
    )
    assert isinstance(pcbdoc_payload["models"][0]["references"], list)  # type: ignore[index]

    pcblib = AltiumPcbLib()
    pcblib.raw_embedded_fonts = b"opaque pcblib font stream"
    pcblib_model = pcblib.add_embedded_model(
        name="library-model.step",
        model_data=model_payload,
        model_id="{20202020-2020-2020-2020-202020202020}",
        checksum=2020,
    )
    footprint = pcblib.add_footprint("MODEL_SOURCE")
    footprint.add_embedded_3d_model(
        pcblib_model,
        bounds_mils=(-20.0, -10.0, 20.0, 10.0),
        layer=PcbLayer.MECHANICAL_1,
        overall_height_mils=15.0,
    )
    pcblib_payload = pcblib.embedded_asset_inventory(include_hashes=True).to_dict()
    _assert_schema_valid(validator, pcblib_payload, "pcblib embedded inventory")
    assert pcblib_payload["schema"] == embedded_asset_schema_id()
    assert pcblib_payload["source_kind"] == "pcblib"
    assert pcblib_payload["fonts"] == []
    assert pcblib_payload["models"][0]["source_kind"] == "pcblib"  # type: ignore[index]
    assert pcblib_payload["models"][0]["references"][0]["source_object_kind"] == (  # type: ignore[index]
        "footprint"
    )
    assert pcblib_payload["opaque_assets"][0]["stream_name"] == (  # type: ignore[index]
        "Library/EmbeddedFonts"
    )
    assert pcblib_payload["opaque_assets"][0]["support_status"] == "opaque"  # type: ignore[index]

    bad_extra_field = deepcopy(pcbdoc_payload)
    bad_extra_field["models"][0]["unexpected"] = True  # type: ignore[index]
    _assert_schema_invalid(validator, bad_extra_field, "unexpected model field")

    bad_source_kind = deepcopy(pcbdoc_payload)
    bad_source_kind["models"][0]["source_kind"] = "pcblib"  # type: ignore[index]
    _assert_schema_invalid(validator, bad_source_kind, "mismatched item source")

    bad_hash = deepcopy(pcbdoc_payload)
    bad_hash["models"][0]["payload_sha256"] = "not-a-sha256"  # type: ignore[index]
    _assert_schema_invalid(validator, bad_hash, "malformed model hash")

    bad_unavailable_model = deepcopy(pcbdoc_payload)
    bad_unavailable_model["models"][0]["payload_available"] = False  # type: ignore[index]
    bad_unavailable_model["models"][0]["decompressed_size"] = 99  # type: ignore[index]
    _assert_schema_invalid(
        validator, bad_unavailable_model, "unavailable model with decompressed size"
    )

    bad_unavailable_model_hash = deepcopy(pcbdoc_payload)
    bad_unavailable_model_hash["models"][0]["payload_available"] = False  # type: ignore[index]
    bad_unavailable_model_hash["models"][0]["decompressed_size"] = None  # type: ignore[index]
    _assert_schema_invalid(
        validator, bad_unavailable_model_hash, "unavailable model with hash"
    )

    bad_reference_kind = deepcopy(pcbdoc_payload)
    bad_reference_kind["models"][0]["references"][0]["asset_kind"] = "font"  # type: ignore[index]
    _assert_schema_invalid(validator, bad_reference_kind, "non-model reference kind")

    bad_unavailable_font = deepcopy(pcbdoc_payload)
    bad_unavailable_font["fonts"][0]["payload_available"] = False  # type: ignore[index]
    bad_unavailable_font["fonts"][0]["decompressed_size"] = 12  # type: ignore[index]
    _assert_schema_invalid(
        validator, bad_unavailable_font, "unavailable font with decompressed size"
    )

    bad_unavailable_font_hash = deepcopy(pcbdoc_payload)
    bad_unavailable_font_hash["fonts"][0]["payload_available"] = False  # type: ignore[index]
    bad_unavailable_font_hash["fonts"][0]["decompressed_size"] = None  # type: ignore[index]
    _assert_schema_invalid(
        validator, bad_unavailable_font_hash, "unavailable font with hash"
    )

    bad_pcblib_font = deepcopy(pcblib_payload)
    font_entry = deepcopy(pcbdoc_payload["fonts"][0])  # type: ignore[index]
    font_entry["source_kind"] = "pcblib"
    bad_pcblib_font["fonts"].append(font_entry)  # type: ignore[union-attr]
    _assert_schema_invalid(validator, bad_pcblib_font, "pcblib typed font")

    bad_pcbdoc_opaque = deepcopy(pcbdoc_payload)
    opaque_entry = deepcopy(pcblib_payload["opaque_assets"][0])  # type: ignore[index]
    opaque_entry["source_kind"] = "pcbdoc"
    bad_pcbdoc_opaque["opaque_assets"].append(opaque_entry)  # type: ignore[union-attr]
    _assert_schema_invalid(validator, bad_pcbdoc_opaque, "pcbdoc opaque asset")

    bad_unavailable_opaque_hash = deepcopy(pcblib_payload)
    bad_unavailable_opaque_hash["opaque_assets"][0]["payload_available"] = False  # type: ignore[index]
    _assert_schema_invalid(
        validator, bad_unavailable_opaque_hash, "unavailable opaque asset with hash"
    )


def test_extractable_asset_inventory_to_dict_handles_unavailable_embedded_payloads() -> (
    None
):
    from altium_monkey import (
        AltiumPcbDoc,
        AltiumPcbLib,
        EmbeddedFont,
        extractable_asset_schema_id,
    )

    validator = _extractable_asset_schema_validator()
    pcbdoc = AltiumPcbDoc()
    pcbdoc.add_embedded_model(
        name="corrupt_model.step",
        model_data=b"not a zlib stream",
        model_id="{30303030-3030-3030-3030-303030303030}",
        data_is_compressed=True,
    )
    pcbdoc.embedded_fonts = [
        EmbeddedFont(
            name="Corrupt Font",
            style="Regular",
            compressed_data=b"not a zlib font stream",
            raw_data=b"font-record-prefix",
        )
    ]

    payload = pcbdoc.asset_inventory(include_hashes=True).to_dict()
    _assert_schema_valid(validator, payload, "generic unavailable embedded payloads")
    assert payload["schema"] == extractable_asset_schema_id()

    assets_by_kind = {
        asset["kind"]: asset
        for asset in payload["assets"]  # type: ignore[index]
    }
    model = assets_by_kind["embedded_model"]
    assert model["can_extract"] is False
    assert model["payload_available"] is False
    assert model["payload_sha256"] is None
    assert model["details"]["decompressed_size"] == 0  # type: ignore[index]

    font = assets_by_kind["embedded_font"]
    assert font["can_extract"] is False
    assert font["payload_available"] is False
    assert font["payload_sha256"] is None
    assert font["details"]["decompressed_size"] == 0  # type: ignore[index]

    bad_model_size = deepcopy(payload)
    bad_model = next(
        asset
        for asset in bad_model_size["assets"]  # type: ignore[index]
        if asset["kind"] == "embedded_model"
    )
    bad_model["details"]["decompressed_size"] = None
    _assert_schema_invalid(
        validator, bad_model_size, "generic model unavailable size null"
    )

    bad_model_hash = deepcopy(payload)
    bad_model = next(
        asset
        for asset in bad_model_hash["assets"]  # type: ignore[index]
        if asset["kind"] == "embedded_model"
    )
    bad_model["payload_sha256"] = "0" * 64
    _assert_schema_invalid(
        validator, bad_model_hash, "generic unavailable model with hash"
    )

    bad_font_size = deepcopy(payload)
    bad_font = next(
        asset
        for asset in bad_font_size["assets"]  # type: ignore[index]
        if asset["kind"] == "embedded_font"
    )
    bad_font["details"]["decompressed_size"] = None
    _assert_schema_invalid(
        validator, bad_font_size, "generic font unavailable size null"
    )

    bad_font_hash = deepcopy(payload)
    bad_font = next(
        asset
        for asset in bad_font_hash["assets"]  # type: ignore[index]
        if asset["kind"] == "embedded_font"
    )
    bad_font["payload_sha256"] = "1" * 64
    _assert_schema_invalid(
        validator, bad_font_hash, "generic unavailable font with hash"
    )

    pcblib = AltiumPcbLib()
    pcblib.raw_embedded_fonts = b"opaque pcblib font stream"
    opaque_payload = pcblib.asset_inventory(include_hashes=True).to_dict()
    _assert_schema_valid(validator, opaque_payload, "generic opaque payload")
    opaque_asset = next(
        asset
        for asset in opaque_payload["assets"]  # type: ignore[index]
        if asset["kind"] == "opaque_embedded"
    )
    assert opaque_asset["payload_available"] is True
    assert (
        opaque_asset["payload_sha256"]
        == hashlib.sha256(b"opaque pcblib font stream").hexdigest()
    )

    bad_opaque_hash = deepcopy(opaque_payload)
    bad_opaque = next(
        asset
        for asset in bad_opaque_hash["assets"]  # type: ignore[index]
        if asset["kind"] == "opaque_embedded"
    )
    bad_opaque["payload_available"] = False
    _assert_schema_invalid(
        validator, bad_opaque_hash, "generic unavailable opaque with hash"
    )


def test_embedded_asset_inventory_example_writes_consumer_json(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "embedded_asset_inventory"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Inventoried embedded asset sources: 2" in result.stdout

    example_root = check_examples_root / "embedded_asset_inventory"
    manifest = json.loads(
        (example_root / "output" / "embedded_asset_inventory_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema"] == "altium_monkey.examples.embedded_asset_inventory.a0"
    assert manifest["inventory_schema"] == "altium_monkey.pcb.embedded_assets.a0"
    assert set(manifest["sources"]) == {"pcbdoc", "pcblib"}

    validator = _embedded_asset_schema_validator()
    inventories: dict[str, dict[str, object]] = {}
    for source_kind in ("pcbdoc", "pcblib"):
        source = manifest["sources"][source_kind]
        assert source["schema"] == "altium_monkey.pcb.embedded_assets.a0"
        inventory = json.loads(
            (example_root / source["inventory"]).read_text(encoding="utf-8")
        )
        inventories[source_kind] = inventory
        _assert_schema_valid(validator, inventory, f"{source_kind} embedded inventory")
        assert inventory["schema"] == "altium_monkey.pcb.embedded_assets.a0"
        assert inventory["source_kind"] == source_kind
        assert len(inventory["models"]) == source["model_count"]
        assert len(inventory["fonts"]) == source["font_count"]
        assert len(inventory["opaque_assets"]) == source["opaque_count"]

    assert manifest["sources"]["pcbdoc"]["model_count"] > 0
    assert manifest["sources"]["pcbdoc"]["font_count"] > 0
    assert manifest["sources"]["pcblib"]["model_count"] > 0
    assert manifest["sources"]["pcblib"]["opaque_count"] > 0
    assert inventories["pcblib"]["fonts"] == []
    assert inventories["pcblib"]["opaque_assets"][0]["stream_name"] == (
        "Library/EmbeddedFonts"
    )

    def selected_inventory_entry(
        *,
        source_kind: str,
        collection_name: str,
        selected_entry: dict[str, object],
    ) -> dict[str, object]:
        matches = [
            item
            for item in inventories[source_kind][collection_name]  # type: ignore[index]
            if item["extraction_filename"] == selected_entry["suggested_filename"]
        ]
        assert len(matches) == 1
        return matches[0]

    selected = manifest["selected"]
    selected_entries = {
        "pcbdoc_model": selected_inventory_entry(
            source_kind="pcbdoc",
            collection_name="models",
            selected_entry=selected["pcbdoc_model"],
        ),
        "pcbdoc_font": selected_inventory_entry(
            source_kind="pcbdoc",
            collection_name="fonts",
            selected_entry=selected["pcbdoc_font"],
        ),
        "pcblib_model": selected_inventory_entry(
            source_kind="pcblib",
            collection_name="models",
            selected_entry=selected["pcblib_model"],
        ),
    }
    payload_paths = {
        "pcbdoc_model": example_root / selected["pcbdoc_model"]["path"],
        "pcbdoc_font": example_root / selected["pcbdoc_font"]["path"],
        "pcblib_model": example_root / selected["pcblib_model"]["path"],
    }
    for key, path in payload_paths.items():
        assert selected_entries[key]["payload_available"] is True
        assert selected_entries[key]["payload_sha256"] == selected[key]["sha256"]
        assert (
            selected_entries[key]["extraction_filename"]
            == selected[key]["suggested_filename"]
        )
        assert path.exists()
        assert path.stat().st_size == selected[key]["byte_count"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == selected[key]["sha256"]

    assert (
        payload_paths["pcbdoc_model"]
        .read_text(
            encoding="utf-8",
            errors="ignore",
        )
        .startswith("ISO-10303-21;")
    )
    assert (
        payload_paths["pcblib_model"]
        .read_text(
            encoding="utf-8",
            errors="ignore",
        )
        .startswith("ISO-10303-21;")
    )
    assert payload_paths["pcbdoc_font"].read_bytes()[:4] in (
        b"\x00\x01\x00\x00",
        b"OTTO",
    )


def test_extractable_asset_inventory_example_writes_selected_assets(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbLib, AltiumSchLib

    example = next(
        item for item in _load_examples() if item["id"] == "extractable_asset_inventory"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr
    assert "Inventoried sources: 4" in result.stdout

    example_root = check_examples_root / "extractable_asset_inventory"
    manifest = json.loads(
        (
            example_root / "output" / "extractable_asset_inventory_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["schema"] == "altium_monkey.examples.extractable_asset_inventory.a0"
    assert manifest["inventory_schema"] == "altium_monkey.extractable_assets.a0"
    assert set(manifest["sources"]) == {"schdoc", "schlib", "pcbdoc", "pcblib"}
    validator = _extractable_asset_schema_validator()
    inventories: dict[str, dict[str, object]] = {}

    expected_kinds = {
        "schdoc": "sch_symbol",
        "schlib": "sch_symbol",
        "pcbdoc": "pcb_footprint",
        "pcblib": "pcb_footprint",
    }
    for source_kind, asset_kind in expected_kinds.items():
        source = manifest["sources"][source_kind]
        assert source["schema"] == "altium_monkey.extractable_assets.a0"
        assert source["asset_counts"][asset_kind] > 0
        inventory = json.loads(
            (example_root / source["inventory"]).read_text(encoding="utf-8")
        )
        inventories[source_kind] = inventory
        _assert_schema_valid(validator, inventory, f"{source_kind} inventory")
        assert inventory["schema"] == "altium_monkey.extractable_assets.a0"
        assert inventory["source_kind"] == source_kind
        assert any(asset["kind"] == asset_kind for asset in inventory["assets"])

    schdoc_inventory = inventories["schdoc"]
    bad_details = deepcopy(schdoc_inventory)
    bad_details["assets"][0]["details"] = {  # type: ignore[index]
        "pattern": "QFN-16",
        "occurrence": 0,
        "component_indices": [],
        "component_designators": [],
        "component_count": 0,
        "source_footprint_library": "",
        "pad_count": 0,
        "primitive_count": 0,
    }
    _assert_schema_invalid(validator, bad_details, "mismatched detail kind")

    bad_ref_kind = deepcopy(schdoc_inventory)
    bad_ref_kind["assets"][0]["ref"]["kind"] = "pcb_footprint"  # type: ignore[index]
    _assert_schema_invalid(validator, bad_ref_kind, "mismatched ref.kind")

    bad_extras = deepcopy(schdoc_inventory)
    bad_extras["assets"][0]["extras"] = {"unexpected": True}  # type: ignore[index]
    _assert_schema_invalid(validator, bad_extras, "non-empty extras")

    pcbdoc_inventory = inventories["pcbdoc"]
    hashed_payload = next(
        asset
        for asset in pcbdoc_inventory["assets"]  # type: ignore[index]
        if asset["payload_sha256"] is not None
    )
    bad_hash = deepcopy(pcbdoc_inventory)
    bad_hash_asset = next(
        asset
        for asset in bad_hash["assets"]  # type: ignore[index]
        if asset["name"] == hashed_payload["name"]
    )
    bad_hash_asset["payload_sha256"] = "not-a-sha256"
    _assert_schema_invalid(validator, bad_hash, "malformed payload_sha256")

    reserved_kind = deepcopy(schdoc_inventory)
    reserved_kind["assets"][0]["kind"] = "sch_image"  # type: ignore[index]
    reserved_kind["assets"][0]["ref"]["kind"] = "sch_image"  # type: ignore[index]
    _assert_schema_invalid(validator, reserved_kind, "reserved sch_image kind")

    def selected_asset_entry(
        *,
        source_kind: str,
        selected_entry: dict[str, object],
    ) -> dict[str, object]:
        matches = [
            asset
            for asset in inventories[source_kind]["assets"]  # type: ignore[index]
            if asset["kind"] == selected_entry["kind"]
            and asset["name"] == selected_entry["name"]
            and asset["ref"]["key"] == selected_entry["ref_key"]
        ]
        assert len(matches) == 1
        return matches[0]

    selected = manifest["selected"]
    selected_entries = {
        "schdoc_symbol": selected_asset_entry(
            source_kind="schdoc",
            selected_entry=selected["schdoc_symbol"],
        ),
        "schlib_symbol": selected_asset_entry(
            source_kind="schlib",
            selected_entry=selected["schlib_symbol"],
        ),
        "pcbdoc_footprint": selected_asset_entry(
            source_kind="pcbdoc",
            selected_entry=selected["pcbdoc_footprint"],
        ),
        "pcblib_footprint": selected_asset_entry(
            source_kind="pcblib",
            selected_entry=selected["pcblib_footprint"],
        ),
        "pcbdoc_model": selected_asset_entry(
            source_kind="pcbdoc",
            selected_entry=selected["pcbdoc_model"],
        ),
    }
    for key, asset in selected_entries.items():
        assert asset["can_extract"] is True
        assert asset["extraction_filename"] == selected[key]["suggested_filename"]

    model_entry = selected_entries["pcbdoc_model"]
    assert model_entry["payload_available"] is True
    assert model_entry["payload_sha256"] == selected["pcbdoc_model"]["sha256"]

    schdoc_symbol = AltiumSchLib(example_root / selected["schdoc_symbol"]["path"])
    schlib_symbol = AltiumSchLib(example_root / selected["schlib_symbol"]["path"])
    pcbdoc_footprint = AltiumPcbLib.from_file(
        example_root / selected["pcbdoc_footprint"]["path"]
    )
    pcblib_footprint = AltiumPcbLib.from_file(
        example_root / selected["pcblib_footprint"]["path"]
    )
    assert len(schdoc_symbol.symbols) == 1
    assert len(schlib_symbol.symbols) == 1
    assert len(pcbdoc_footprint.footprints) == 1
    assert len(pcblib_footprint.footprints) == 1

    model_path = example_root / selected["pcbdoc_model"]["path"]
    assert model_path.exists()
    assert model_path.stat().st_size == selected["pcbdoc_model"]["byte_count"]
    assert (
        hashlib.sha256(model_path.read_bytes()).hexdigest()
        == (selected["pcbdoc_model"]["sha256"])
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


def test_pcbdoc_user_union_example_writes_expected_union(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbDoc
    from altium_monkey.altium_pcbdoc_unions import (
        pcb_packed_mask_user_union_index,
        pcb_pad_user_union_index,
        pcb_track_user_union_index,
    )

    example = next(
        item for item in _load_examples() if item["id"] == "pcbdoc_user_union"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    output_root = check_examples_root / "pcbdoc_user_union" / "output"
    pcbdoc = AltiumPcbDoc.from_file(output_root / "pcbdoc_user_union.PcbDoc")
    user_unions = {user_union.name: user_union for user_union in pcbdoc.user_unions}
    assert set(user_unions) == {"DEMO_USER_UNION"}

    user_union = user_unions["DEMO_USER_UNION"]
    expected_members = {
        "arcs": 6,
        "components": 2,
        "component_bodies": 3,
        "fills": 1,
        "pads": 3,
        "regions": 1,
        "shapebased_component_bodies": 3,
        "shapebased_regions": 1,
        "texts": 5,
        "tracks": 3,
        "vias": 1,
    }
    assert user_union.member_count == sum(expected_members.values())
    assert {
        collection: len(members)
        for collection, members in user_union.members_by_collection.items()
    } == expected_members
    assert {component.designator for component in pcbdoc.components} == {"TP1", "MH1"}
    assert {component.union_index for component in pcbdoc.components} == {
        user_union.union_index
    }
    assert len(pcbdoc.tracks) == 3
    assert {track.union_index for track in pcbdoc.tracks} == {0xFFFFFFFF}
    assert [pcb_track_user_union_index(track) for track in pcbdoc.tracks] == [
        user_union.union_index,
        user_union.union_index,
        user_union.union_index,
    ]
    assert {arc.union_index for arc in pcbdoc.arcs} == {0xFFFFFFFF}
    assert {pcb_packed_mask_user_union_index(arc) for arc in pcbdoc.arcs} == {
        user_union.union_index
    }
    assert {fill.union_index for fill in pcbdoc.fills} == {0xFFFFFFFF}
    assert {pcb_packed_mask_user_union_index(fill) for fill in pcbdoc.fills} == {
        user_union.union_index
    }
    assert {pad.union_index for pad in pcbdoc.pads} == {0xFFFFFFFF}
    assert {pcb_pad_user_union_index(pad) for pad in pcbdoc.pads} == {
        user_union.union_index
    }
    assert {region.union_index for region in pcbdoc.regions} == {user_union.union_index}
    assert {region.properties.get("UNIONINDEX") for region in pcbdoc.regions} == {
        str(user_union.union_index)
    }
    npth_pad = next(pad for pad in pcbdoc.pads if pad.designator == "NPTH1")
    assert npth_pad.hole_size / 10000.0 == pytest.approx(2.0 * 1000.0 / 25.4)
    assert npth_pad.is_plated is False
    assert npth_pad.is_tenting_top is True
    assert npth_pad.is_tenting_bottom is True
    assert npth_pad.pastemask_expansion_mode == 0
    assert npth_pad.soldermask_expansion_mode == 0

    summary = json.loads(
        (output_root / "pcbdoc_user_union.json").read_text(encoding="utf-8")
    )
    assert summary["union_name"] == "DEMO_USER_UNION"
    assert summary["union_index"] == user_union.union_index
    assert summary["member_count"] == user_union.member_count
    assert summary["members_by_collection"] == expected_members
    assert summary["component_designators"] == ["TP1", "MH1"]
    assert summary["footprints"] == [
        "9774080360R-YIYUAN.PcbLib",
        "YZ209315103P-01.PcbLib",
    ]
    assert summary["pad_count"] == 3
    assert summary["via_count"] == 1
    assert summary["track_count"] == 3
    assert summary["arc_count"] == 6
    assert summary["fill_count"] == 1
    assert summary["region_count"] == 1
    assert summary["component_body_count"] == 3
    assert summary["typed_smart_union_count"] == 0


def test_pcbdoc_via_ipc4761_examples_write_expected_via_state(
    check_examples_root: Path,
) -> None:
    from altium_monkey import (
        AltiumPcbDoc,
        PcbIpc4761ViaType,
        PcbViaStructureFeatureSide,
        PcbViaStructureFeatureType,
    )

    for example_id in (
        "pcbdoc_add_via_ipc4761_matrix",
        "pcbdoc_mutate_via_ipc4761",
    ):
        example = next(item for item in _load_examples() if item["id"] == example_id)
        result = _run_example_entrypoint(example, check_examples_root)
        assert result.returncode == 0, result.stderr

    matrix_root = check_examples_root / "pcbdoc_add_via_ipc4761_matrix" / "output"
    matrix_doc = AltiumPcbDoc.from_file(
        matrix_root / "pcbdoc_add_via_ipc4761_matrix.PcbDoc"
    )
    assert len(matrix_doc.vias) == 20
    assert {int(via.ipc4761_via_type) for via in matrix_doc.vias} == set(range(13))
    assert (
        sum(
            int(via.ipc4761_via_type)
            == int(PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING)
            for via in matrix_doc.vias
        )
        == 2
    )
    assert any(via.is_tent_top and not via.is_tent_bottom for via in matrix_doc.vias)
    assert any(via.is_tent_bottom and not via.is_tent_top for via in matrix_doc.vias)
    assert any(
        via.soldermask_expansion_manual and via.soldermask_expansion_front == 20000
        for via in matrix_doc.vias
    )

    epoxy_via = next(
        via
        for via in matrix_doc.vias
        if (
            via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING) is not None
            and via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING).material
            == "EPOXY"
        )
    )
    filling = epoxy_via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING)
    capping = epoxy_via.get_ipc4761_feature(PcbViaStructureFeatureType.CAPPING)
    assert filling is not None
    assert capping is not None
    assert int(filling.side) == int(PcbViaStructureFeatureSide.BOTH)
    assert capping.material == "COPPER"

    matrix_manifest = json.loads(
        (matrix_root / "pcbdoc_add_via_ipc4761_matrix.json").read_text(encoding="utf-8")
    )
    assert any(
        entry["label"] == "Type7 epoxy"
        for entry in matrix_manifest["tenting_and_mask_entries"]
    )

    mutation_root = check_examples_root / "pcbdoc_mutate_via_ipc4761" / "output"
    mutation_doc = AltiumPcbDoc.from_file(
        mutation_root / "rt_super_c1_type7_filled_vias" / "RT_SUPER_C1.PCBdoc"
    )
    matched_vias = [
        via
        for via in mutation_doc.vias
        if via.diameter == 120000 and via.hole_size == 60000
    ]
    assert len(matched_vias) == 91
    assert all(
        via.ipc4761_via_type == PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING
        for via in matched_vias
    )
    assert all(
        via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING) is not None
        and via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING).material
        == "EPOXY"
        for via in matched_vias
    )
    assert all(
        via.get_ipc4761_feature(PcbViaStructureFeatureType.CAPPING) is not None
        and via.get_ipc4761_feature(PcbViaStructureFeatureType.CAPPING).material
        == "COPPER"
        for via in matched_vias
    )
    matched_via_ids = {id(via) for via in matched_vias}
    assert all(
        int(via.ipc4761_via_type) == int(PcbIpc4761ViaType.NONE)
        for via in mutation_doc.vias
        if id(via) not in matched_via_ids
    )

    mutation_summary = json.loads(
        (mutation_root / "mutation_summary.json").read_text(encoding="utf-8")
    )
    assert mutation_summary["matched_via_count"] == len(matched_vias)


def test_pcbdoc_public_via_surface_policy_roundtrip(tmp_path: Path) -> None:
    from altium_monkey import AltiumPcbDoc

    pcbdoc = AltiumPcbDoc()
    pcbdoc.add_via(
        position_mils=(100.0, 0.0),
        diameter_mils=30.0,
        hole_size_mils=12.0,
        is_tent_top=True,
        is_tent_bottom=False,
        solder_mask_expansion_top_mils=-3.0,
        solder_mask_expansion_bottom_mils=5.0,
    )

    output_path = tmp_path / "public_via_surface_policy.PcbDoc"
    pcbdoc.save(output_path)
    parsed = AltiumPcbDoc.from_file(output_path)
    assert len(parsed.vias) == 1
    via = parsed.vias[0]
    assert via.is_tent_top is True
    assert via.is_tent_bottom is False
    assert via.solder_mask_expansion_mode == 2
    assert via.soldermask_expansion_front == -30000
    assert via.soldermask_expansion_back == 50000


def test_pcbdoc_differential_pair_examples_write_expected_state(
    check_examples_root: Path,
) -> None:
    from altium_monkey import AltiumPcbDoc

    for example_id in (
        "pcbdoc_add_differential_pairs",
        "pcbdoc_diff_pair_report",
    ):
        example = next(item for item in _load_examples() if item["id"] == example_id)
        result = _run_example_entrypoint(example, check_examples_root)
        assert result.returncode == 0, result.stderr

    author_root = check_examples_root / "pcbdoc_add_differential_pairs" / "output"
    author_doc = AltiumPcbDoc.from_file(
        author_root / "pcbdoc_add_differential_pairs.PcbDoc"
    )
    pairs_by_name = {pair.name: pair for pair in author_doc.differential_pairs}
    assert set(pairs_by_name) == {"USB_D", "CAM_D0", "LVDS_CLK"}
    assert pairs_by_name["USB_D"].net_names == ("USB_D_P", "USB_D_N")
    assert pairs_by_name["CAM_D0"].gather_control is True
    assert {net.name for net in author_doc.nets} == {
        "USB_D_P",
        "USB_D_N",
        "CAM_D0_P",
        "CAM_D0_N",
        "LVDS_CLK_P",
        "LVDS_CLK_N",
    }
    assert len(author_doc.tracks) == 6
    assert len(author_doc.vias) == 6

    author_manifest = json.loads(
        (author_root / "pcbdoc_add_differential_pairs.json").read_text(encoding="utf-8")
    )
    assert author_manifest["differential_pair_count"] == 3
    assert author_manifest["source"] == (
        "created from AltiumPcbDoc() without an input PcbDoc"
    )

    report_root = check_examples_root / "pcbdoc_diff_pair_report" / "output"
    report = json.loads(
        (report_root / "pcbdoc_diff_pair_report.json").read_text(encoding="utf-8")
    )
    assert report["project"] == "assets/projects/rt_super_c1/RT_SUPER_C1.PrjPcb"
    assert report["differential_pair_count"] >= 1
    report_pairs_by_name = {pair["name"]: pair for pair in report["pairs"]}
    assert report_pairs_by_name["USB_D"]["positive_net_name"] == "USB_D_P"
    assert report_pairs_by_name["USB_D"]["negative_net_name"] == "USB_D_N"
    assert report["pair_by_net_name"]["USB_D_P"] == "USB_D"
    assert report["pair_by_net_name"]["USB_D_N"] == "USB_D"

    report_text = (report_root / "pcbdoc_diff_pair_report.txt").read_text(
        encoding="utf-8"
    )
    assert "Differential Pairs" in report_text
    assert "USB_D" in report_text


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


def test_intlib_extract_sources_reports_metadata_and_writes_parseable_sources(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "intlib_extract_sources"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumIntLib, AltiumPcbLib, AltiumSchLib

    manifest_path = (
        check_examples_root
        / "intlib_extract_sources"
        / "output"
        / "intlib_extract_sources_manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["input_intlib"] == "assets/projects/rt_super_c1/RT_SUPER_C1.IntLib"
    assert manifest["component_parse_error"] is None
    assert manifest["component_count"] > 0
    assert {"SchLib", "PCBLib"}.issubset(set(manifest["source_kinds"]))

    by_kind = {source["kind"]: source for source in manifest["extracted_sources"]}
    schlib_path = (
        check_examples_root / "intlib_extract_sources" / by_kind["SchLib"]["path"]
    )
    pcblib_path = (
        check_examples_root / "intlib_extract_sources" / by_kind["PCBLib"]["path"]
    )
    assert len(AltiumSchLib.get_symbol_names(schlib_path)) > 0
    assert len(AltiumPcbLib.get_footprint_names(pcblib_path)) > 0

    source_intlib = check_examples_root / manifest["input_intlib"]
    with AltiumIntLib(source_intlib) as intlib:
        assert intlib.component_parse_error is None
        assert len(intlib.get_source_entries()) == manifest["source_count"]


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


def test_pcblib_create_cavity_region_writes_native_cavity(
    check_examples_root: Path,
) -> None:
    example = next(
        item for item in _load_examples() if item["id"] == "pcblib_create_cavity_region"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbLib, PcbLayer, PcbRegionKind

    output_root = check_examples_root / "pcblib_create_cavity_region" / "output"
    output_path = output_root / "pcblib_create_cavity_region.PcbLib"
    manifest_path = output_root / "pcblib_create_cavity_region.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    pcblib = AltiumPcbLib.from_file(output_path)
    assert len(pcblib.footprints) == 1
    footprint = pcblib.footprints[0]
    assert footprint.name == "R0402_0.40MM_HD_CAVITY_DEMO"
    assert len(footprint.pads) == 2
    assert len(footprint.regions) == 1
    assert len(footprint.component_bodies) == 1

    model_entries = pcblib.get_embedded_model_entries()
    assert len(model_entries) == 1
    model, payload = model_entries[0]
    assert model.name == "RESC1005X04L.step"
    try:
        step_payload = zlib.decompress(payload)
    except zlib.error:
        step_payload = payload
    assert step_payload.startswith(b"ISO-10303-21")

    body = footprint.component_bodies[0]
    assert body.model_is_embedded is True
    assert body.model_name == "RESC1005X04L.step"
    assert body.model_id == model.id
    assert int(body.model_checksum) & 0xFFFFFFFF == int(model.checksum) & 0xFFFFFFFF

    cavity_region = footprint.regions[0]
    assert cavity_region.kind == 4
    assert cavity_region.region_kind == PcbRegionKind.CAVITY_DEFINITION
    assert cavity_region.layer == PcbLayer.MECHANICAL_1
    assert cavity_region.properties["KIND"] == "4"
    assert cavity_region.properties["V7_LAYER"] == "MECHANICAL1"
    assert cavity_region.properties["CAVITYHEIGHT"] == "19.685mil"
    assert cavity_region.cavity_height_mils == pytest.approx(19.685)
    assert manifest["region_native_kind"] == 4
    assert manifest["region_kind"] == "CAVITY_DEFINITION"
    assert manifest["cavity_height_mm"] == pytest.approx(0.5, abs=0.0001)
    assert manifest["component_body_count"] == 1
    assert manifest["embedded_model_count"] == 1
    assert manifest["embedded_model_names"] == ["RESC1005X04L.step"]


def test_pcblib_via_ipc4761_example_writes_expected_side_tables(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcblib_add_via_ipc4761_matrix"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import (
        AltiumPcbLib,
        PcbIpc4761ViaType,
        PcbViaStructureFeatureSide,
        PcbViaStructureFeatureType,
    )

    output_root = check_examples_root / "pcblib_add_via_ipc4761_matrix" / "output"
    output_path = output_root / "pcblib_add_via_ipc4761_matrix.PcbLib"
    manifest_path = output_root / "pcblib_add_via_ipc4761_matrix.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    pcblib = AltiumPcbLib.from_file(output_path)
    assert len(pcblib.footprints) == 1
    footprint = pcblib.footprints[0]
    assert footprint.name == "PCBLIB_VIA_IPC4761_MATRIX"
    assert footprint.footprint_primitive_parameters == {
        "TEST_PARAMETER": "pcblib_add_via_ipc4761_matrix"
    }
    assert len(footprint.vias) == 14
    assert {int(via.ipc4761_via_type) for via in footprint.vias} == set(range(13))
    assert len(footprint.via_structures) == 13
    assert len(footprint.via_structure_links) == 13

    custom_via = footprint.vias[-1]
    assert custom_via.ipc4761_via_type == PcbIpc4761ViaType.TYPE_7_FILLING_AND_CAPPING
    assert custom_via.propagation_delay_ps == pytest.approx(12.5)
    assert custom_via.is_test_fab_top is True
    assert custom_via.is_assy_testpoint_bottom is True
    assert custom_via.via_structure is not None
    assert len(custom_via.via_structure.features) == 5
    filling = custom_via.get_ipc4761_feature(PcbViaStructureFeatureType.FILLING)
    capping = custom_via.get_ipc4761_feature(PcbViaStructureFeatureType.CAPPING)
    assert filling is not None
    assert capping is not None
    assert filling.side == PcbViaStructureFeatureSide.BOTH
    assert filling.material == "EPOXY"
    assert capping.side == PcbViaStructureFeatureSide.BOTH
    assert capping.material == "COPPER"

    assert manifest["via_count"] == len(footprint.vias)
    assert manifest["via_structure_count"] == len(footprint.via_structures)
    assert manifest["via_structure_link_count"] == len(footprint.via_structure_links)
    assert manifest["footprint_primitive_parameters"] == (
        footprint.footprint_primitive_parameters
    )
    assert manifest["custom_type7"]["filling_material"] == "EPOXY"
    assert manifest["custom_type7"]["capping_material"] == "COPPER"


def test_pcblib_recreate_via_feature_libraries_replays_public_authoring_api(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcblib_recreate_via_feature_libraries"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbLib

    output_root = (
        check_examples_root / "pcblib_recreate_via_feature_libraries" / "output"
    )
    manifest_path = output_root / "pcblib_recreate_via_feature_libraries.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    libraries = {entry["footprint"]: entry for entry in manifest["libraries"]}

    assert set(libraries) == {
        "via_ipc4761_type_matrix",
        "via_propagation_delay_matrix",
        "via_features_and_all_primitives",
        "via_test_point_flags",
        "footprint_parameters",
    }
    for entry in libraries.values():
        assert all(entry["semantic_match"].values())
        output_path = (
            check_examples_root
            / "pcblib_recreate_via_feature_libraries"
            / (entry["output"])
        )
        assert output_path.exists()
        reparsed = AltiumPcbLib.from_file(output_path)
        assert len(reparsed.footprints) == 1
        assert reparsed.footprints[0].name == entry["footprint"]

    mixed = libraries["via_features_and_all_primitives"]
    assert mixed["record_count"] == 156
    assert mixed["pad_count"] == 2
    assert mixed["via_count"] == 60
    assert mixed["via_structure_count"] == 39
    assert mixed["via_structure_link_count"] == 51
    assert mixed["footprint_primitive_parameters"] == {
        "TEST_PARAMETER": "via_features_and_all_primitives"
    }
    assert mixed["parameters"]["SMARTUNIONSSTORAGE"] == "2"
    assert sum(1 for via in mixed["vias"] if via["ipc4761_via_type"] != 0) == 51

    flags = libraries["via_test_point_flags"]
    assert [
        (
            via["is_test_fab_top"],
            via["is_test_fab_bottom"],
            via["is_assy_testpoint_top"],
            via["is_assy_testpoint_bottom"],
        )
        for via in flags["vias"]
    ] == [
        (True, False, False, False),
        (False, True, False, False),
        (True, True, False, False),
        (False, False, True, False),
        (False, True, False, True),
        (False, False, False, True),
        (True, True, True, True),
    ]

    footprint_parameters = libraries["footprint_parameters"]
    assert footprint_parameters["record_count"] == 0
    assert footprint_parameters["parameters"]["HEIGHT"] == "123mil"
    assert footprint_parameters["parameters"]["AREA"] == "314000000000001.728000"
    assert footprint_parameters["footprint_primitive_parameters"] == {
        "TEST_PARAMETER1": "1st_param",
        "TEST_PARAMETER3": "3rd_param",
        "TEST_PARAMETER2": "2nd_param",
    }


def test_pcbdoc_create_cavity_placements_writes_board_side_cavities(
    check_examples_root: Path,
) -> None:
    example = next(
        item
        for item in _load_examples()
        if item["id"] == "pcbdoc_create_cavity_placements"
    )
    result = _run_example_entrypoint(example, check_examples_root)
    assert result.returncode == 0, result.stderr

    from altium_monkey import AltiumPcbDoc, PcbLayer, PcbRegionKind
    from altium_monkey.altium_layer_stack_document import AltiumLayerStackDocument

    output_root = check_examples_root / "pcbdoc_create_cavity_placements" / "output"
    output_path = output_root / "pcbdoc_create_cavity_placements.PcbDoc"
    source_pcblib_path = output_root / "R0402_0.40MM_HD.PcbLib"
    manifest_path = output_root / "cavity_placements_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["semantic_match"] is True
    assert source_pcblib_path.exists()
    assert manifest["source_pcblib"] == (
        "pcbdoc_create_cavity_placements/output/R0402_0.40MM_HD.PcbLib"
    )
    assert manifest["stackup_reference"] == (
        "assets/pcbdoc_layer_stack_specs/cavity_placements.stackup"
    )
    assert manifest["stackupx_reference"] == (
        "assets/pcbdoc_layer_stack_specs/cavity_placements.stackupx"
    )
    assert manifest["stackup_exact_reference_match"] is True
    assert manifest["stackupx_exact_reference_match"] is True
    assert manifest["copper_layers"] == [
        "Top Layer",
        "IN1",
        "IN2",
        "IN3",
        "IN4",
        "Bottom Layer",
    ]
    assert manifest["copper_layer_policy"] == {
        "Top Layer": {
            "legacy_layer_id": 1,
            "component_placement": "BodyUp",
            "copper_orientation": "0",
            "thickness_mils": 1.378,
        },
        "IN1": {
            "legacy_layer_id": 2,
            "component_placement": "BodyUp",
            "copper_orientation": "0",
            "thickness_mils": 1.378,
        },
        "IN2": {
            "legacy_layer_id": 4,
            "component_placement": "None",
            "copper_orientation": "0",
            "thickness_mils": 1.378,
        },
        "IN3": {
            "legacy_layer_id": 5,
            "component_placement": "BodyDown",
            "copper_orientation": "1",
            "thickness_mils": 1.378,
        },
        "IN4": {
            "legacy_layer_id": 3,
            "component_placement": "BodyDown",
            "copper_orientation": "1",
            "thickness_mils": 1.378,
        },
        "Bottom Layer": {
            "legacy_layer_id": 32,
            "component_placement": "BodyDown",
            "copper_orientation": "1",
            "thickness_mils": 1.378,
        },
    }
    assert [layer["name"] for layer in manifest["dielectric_layers"]] == [
        "Top Solder",
        "Dielectric 1",
        "Dielectric 2",
        "Dielectric 3",
        "Dielectric 7",
        "Dielectric 6",
        "Dielectric 4",
        "Bottom Solder",
    ]
    assert [layer["thickness_mils"] for layer in manifest["dielectric_layers"]] == [
        1.0,
        18.0,
        18.0,
        18.0,
        18.0,
        18.0,
        18.0,
        1.0,
    ]
    assert manifest["pad_layers"] == {
        "R1": ["TOP"],
        "R2": ["MID1"],
        "R4": ["MID4"],
        "R5": ["MID2"],
        "R6": ["BOTTOM"],
    }
    assert manifest["cavity_region_count"] == 5
    assert manifest["shapebased_cavity_region_count"] == 5
    assert manifest["component_body_count"] == 5
    assert manifest["shapebased_component_body_count"] == 5
    assert manifest["embedded_model_count"] == 1
    assert manifest["embedded_model_names"] == ["RESC1005X04L.step"]
    assert manifest["cavity_height_mils"] == [19.685]
    expected_designator_positions = {
        "R6": (943.342, 469.0052, 970.0, 485.0, 73.3161),
        "R5": (858.342, 469.0052, 885.0, 485.0, 73.3161),
        "R4": (773.342, 469.0052, 800.0, 485.0, 73.3161),
        "R2": (597.0178, 469.8851, 623.6758, 485.8799, 73.3161),
        "R1": (523.6736, 469.0052, 545.0, 485.0, 62.6529),
    }
    for designator, (
        x_mils,
        y_mils,
        snap_x_mils,
        snap_y_mils,
        textbox_width_mils,
    ) in expected_designator_positions.items():
        text = manifest["component_text_layout"][designator]["designator"]
        assert text["text"] == designator
        assert text["layer"] == int(PcbLayer.TOP_OVERLAY)
        assert text["x_mils"] == pytest.approx(x_mils)
        assert text["y_mils"] == pytest.approx(y_mils)
        assert text["height_mils"] == pytest.approx(32.0)
        assert text["rotation_degrees"] == pytest.approx(0.0)
        assert text["stroke_width_mils"] == pytest.approx(10.0)
        assert text["textbox_width_mils"] == pytest.approx(textbox_width_mils)
        assert text["textbox_height_mils"] == pytest.approx(51.9897)
        assert text["snap_x_mils"] == pytest.approx(snap_x_mils)
        assert text["snap_y_mils"] == pytest.approx(snap_y_mils)
        assert text["is_mirrored"] is False
        assert set(manifest["component_text_layout"][designator]) == {"designator"}

    pcbdoc = AltiumPcbDoc.from_file(output_path)
    assert [component.layer for component in pcbdoc.components] == [
        "BOTTOM",
        "MID2",
        "MID4",
        "MID1",
        "TOP",
    ]
    assert [component.footprint for component in pcbdoc.components] == [
        "R0402_0.40MM_HD",
        "R0402_0.40MM_HD",
        "R0402_0.40MM_HD",
        "R0402_0.40MM_HD",
        "R0402_0.40MM_HD",
    ]
    assert all(
        component.raw_record["COMMENTON"] == "FALSE" for component in pcbdoc.components
    )
    assert not any(text.is_comment for text in pcbdoc.texts)
    assert {pad.layer for pad in pcbdoc.pads if pad.component_index == 1} == {
        PcbLayer.MID2
    }
    assert {pad.layer for pad in pcbdoc.pads if pad.component_index == 2} == {
        PcbLayer.MID4
    }
    assert {pad.layer for pad in pcbdoc.pads if pad.component_index == 3} == {
        PcbLayer.MID1
    }
    assert all(
        region.region_kind == PcbRegionKind.CAVITY_DEFINITION
        and region.cavity_height_mils == pytest.approx(19.685)
        for region in pcbdoc.regions
    )
    assert all(
        region.region_kind == PcbRegionKind.CAVITY_DEFINITION
        and region.properties["KIND"] == "4"
        for region in pcbdoc.shapebased_regions
    )
    pcbdoc_stack = AltiumLayerStackDocument.from_pcbdoc(pcbdoc)
    assert {
        layer.display_name: layer.component_placement
        for layer in pcbdoc_stack.physical_stacks[0].layers
        if layer.family == "copper"
    } == {
        "Top Layer": 1,
        "IN1": 1,
        "IN2": 0,
        "IN3": 2,
        "IN4": 2,
        "Bottom Layer": 2,
    }
    spec = json.loads(
        (
            check_examples_root
            / "assets"
            / "pcbdoc_layer_stack_specs"
            / "cavity_placements.json"
        ).read_text(encoding="utf-8")
    )
    _assert_board6_stack_source_entries_match_spec(output_path, spec)


def test_pcblib_public_mask_and_text_authoring_roundtrip(tmp_path: Path) -> None:
    from altium_monkey import (
        AltiumPcbLib,
        PadHoleShape,
        PadShape,
        PcbLayer,
        PcbMaskExpansion,
        PcbTextJustification,
        PcbTextKind,
    )

    pcblib = AltiumPcbLib()
    footprint = pcblib.add_footprint("PUBLIC_MASK_TEXT")
    footprint.add_pad(
        designator="1",
        position_mils=(0.0, 0.0),
        width_mils=40.0,
        height_mils=30.0,
        paste_mask_expansion=PcbMaskExpansion.manual(-40.0),
        solder_mask_expansion_mode="rule",
    )
    footprint.add_custom_pad(
        designator="2",
        position_mils=(100.0, 0.0),
        outline_points_mils=[(-10.0, -10.0), (10.0, -10.0), (0.0, 10.0)],
        anchor_width_mils=14.0,
        anchor_height_mils=8.0,
        anchor_rotation_degrees=37.0,
        anchor_shape=PadShape.RECTANGLE,
        paste_mask_expansion_mode="none",
    )
    footprint.add_pad(
        designator="SQ",
        position_mils=(200.0, 0.0),
        width_mils=60.0,
        height_mils=60.0,
        layer=PcbLayer.MULTI_LAYER,
        hole_size_mils=30.0,
        plated=True,
        hole_shape=PadHoleShape.SQUARE,
    )
    footprint.add_via(
        position_mils=(300.0, 0.0),
        diameter_mils=30.0,
        hole_size_mils=12.0,
        is_tent_top=True,
        is_tent_bottom=False,
        solder_mask_expansion_top_mils=-3.0,
        solder_mask_expansion_bottom_mils=5.0,
    )
    footprint.add_track(
        (0.0, 50.0),
        (100.0, 50.0),
        width_mils=6.0,
        solder_mask_expansion_mils=1.5,
        paste_mask_expansion_mils=-0.5,
    )
    footprint.add_arc(
        center_mils=(150.0, 50.0),
        radius_mils=20.0,
        start_angle_degrees=0.0,
        end_angle_degrees=180.0,
        width_mils=5.0,
        solder_mask_expansion_mils=2.0,
        paste_mask_expansion_mils=0.018,
    )
    footprint.add_fill(
        (200.0, 40.0),
        (240.0, 70.0),
        solder_mask_expansion_mils=3.0,
        paste_mask_expansion_mils=0.019,
    )
    footprint.add_text(
        text="SERIF",
        position_mils=(0.0, 100.0),
        height_mils=60.0,
        stroke_font_type="serif",
    )
    footprint.add_text(
        text="TT",
        position_mils=(0.0, 200.0),
        height_mils=60.0,
        font_kind=PcbTextKind.TRUETYPE,
        font_name="Arial",
        bold=True,
        is_inverted=True,
        inverted_margin_mils=5.0,
    )
    footprint.add_text(
        text="FRAME",
        position_mils=(0.0, 300.0),
        height_mils=60.0,
        is_frame=True,
        frame_size_mils=(300.0, 120.0),
        text_justification=PcbTextJustification.CENTER_CENTER,
    )

    output_path = tmp_path / "public_mask_text.PcbLib"
    pcblib.save(output_path)
    parsed = AltiumPcbLib.from_file(output_path)
    parsed_footprint = parsed.footprints[0]
    pads_by_designator = {pad.designator: pad for pad in parsed_footprint.pads}
    texts_by_content = {text.text_content: text for text in parsed_footprint.texts}

    assert pads_by_designator["1"].pastemask_expansion_mode == 2
    assert pads_by_designator["1"].pastemask_expansion_manual == -400000
    assert pads_by_designator["1"].soldermask_expansion_mode == 1
    assert pads_by_designator["2"].pastemask_expansion_mode == 0
    assert pads_by_designator["2"].width == 140000
    assert pads_by_designator["2"].height == 80000
    assert pads_by_designator["2"].rotation == 37.0
    assert pads_by_designator["2"].shape == PadShape.RECTANGLE
    assert pads_by_designator["2"].custom_shape is not None
    assert pads_by_designator["2"].custom_shape.anchor_pad_index == 1
    assert any(
        region.properties.get("PADINDEX") == "2" for region in parsed_footprint.regions
    )
    assert pads_by_designator["SQ"].hole_size == 300000
    assert pads_by_designator["SQ"].hole_shape == PadHoleShape.SQUARE
    assert len(parsed_footprint.vias) == 1
    assert parsed_footprint.vias[0].is_tent_top is True
    assert parsed_footprint.vias[0].is_tent_bottom is False
    assert parsed_footprint.vias[0].soldermask_expansion_front == -30000
    assert parsed_footprint.vias[0].soldermask_expansion_back == 50000
    assert parsed_footprint.tracks[0].solder_mask_expansion == 15000
    assert parsed_footprint.tracks[0].paste_mask_expansion == -5000
    assert parsed_footprint.arcs[0].solder_mask_expansion == 20000
    assert parsed_footprint.arcs[0].paste_mask_expansion == 180
    assert parsed_footprint.fills[0].solder_mask_expansion == 30000
    assert parsed_footprint.fills[0].paste_mask_expansion == 190
    assert texts_by_content["SERIF"].font_type == 0
    assert texts_by_content["SERIF"].stroke_font_type == 3
    assert texts_by_content["TT"].font_type == 1
    assert texts_by_content["TT"].font_name == "Arial"
    assert texts_by_content["TT"].is_bold is True
    assert texts_by_content["TT"].is_inverted is True
    assert texts_by_content["TT"].margin_border_width == 50000
    assert texts_by_content["FRAME"].is_frame is True
    assert texts_by_content["FRAME"].textbox_rect_width == 3000000
    assert texts_by_content["FRAME"].textbox_rect_height == 1200000


def test_pcbdoc_public_shared_primitive_option_roundtrip(tmp_path: Path) -> None:
    from altium_monkey import (
        AltiumPcbDoc,
        PadShape,
        PcbLayer,
        PcbMaskExpansion,
        PcbRegionKind,
    )

    pcbdoc = AltiumPcbDoc()
    pcbdoc.add_pad(
        designator="P1",
        position_mils=(0.0, 0.0),
        width_mils=40.0,
        height_mils=30.0,
        solder_mask_expansion=PcbMaskExpansion.manual(4.0),
        paste_mask_expansion_mode="none",
    )
    pcbdoc.add_pad(
        designator="LS",
        position_mils=(80.0, 0.0),
        width_mils=60.0,
        height_mils=60.0,
        layer=PcbLayer.MULTI_LAYER,
        shape=PadShape.CIRCLE,
        mid_shape=PadShape.RECTANGLE,
        mid_width_mils=50.0,
        mid_height_mils=50.0,
        bottom_shape=PadShape.OCTAGONAL,
        bottom_width_mils=55.0,
        bottom_height_mils=55.0,
        hole_size_mils=30.0,
        plated=True,
    )
    pcbdoc.add_track(
        (0.0, 50.0),
        (100.0, 50.0),
        width_mils=6.0,
        solder_mask_expansion_mils=1.5,
        paste_mask_expansion_mils=-0.5,
    )
    pcbdoc.add_arc(
        center_mils=(150.0, 50.0),
        radius_mils=20.0,
        start_angle_degrees=0.0,
        end_angle_degrees=180.0,
        width_mils=5.0,
        solder_mask_expansion_mils=2.0,
        paste_mask_expansion_mils=0.018,
    )
    pcbdoc.add_fill(
        (200.0, 40.0),
        (240.0, 70.0),
        solder_mask_expansion_mils=3.0,
        paste_mask_expansion_mils=0.019,
    )
    pcbdoc.add_region(
        outline_points_mils=[
            (0.0, 100.0),
            (100.0, 100.0),
            (100.0, 200.0),
            (0.0, 200.0),
        ],
        kind=PcbRegionKind.BOARD_CUTOUT,
        is_board_cutout=True,
        is_shapebased=True,
        subpoly_index=3,
    )

    output_path = tmp_path / "public_shared_options.PcbDoc"
    pcbdoc.save(output_path)
    parsed = AltiumPcbDoc.from_file(output_path)

    assert parsed.pads[0].soldermask_expansion_mode == 2
    assert parsed.pads[0].soldermask_expansion_manual == 40000
    assert parsed.pads[0].pastemask_expansion_mode == 0
    assert parsed.pads[1].pad_mode == 1
    assert parsed.pads[1].mid_shape == PadShape.RECTANGLE
    assert parsed.pads[1].mid_width == 500000
    assert parsed.pads[1].bot_shape == PadShape.OCTAGONAL
    assert parsed.pads[1].bot_width == 550000
    assert parsed.tracks[0].solder_mask_expansion == 15000
    assert parsed.tracks[0].paste_mask_expansion == -5000
    assert parsed.arcs[0].solder_mask_expansion == 20000
    assert parsed.arcs[0].paste_mask_expansion == 180
    assert parsed.fills[0].solder_mask_expansion == 30000
    assert parsed.fills[0].paste_mask_expansion == 190
    assert parsed.regions[0].is_board_cutout is True
    assert parsed.regions[0].is_shapebased is True
    assert parsed.regions[0].subpoly_index == 3
    assert parsed.shapebased_regions[0].properties["ISBOARDCUTOUT"] == "TRUE"


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
