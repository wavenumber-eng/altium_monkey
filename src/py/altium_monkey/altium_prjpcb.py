"""
Altium PrjPcb (Project) File Parser

Altium project files (.PrjPcb) are INI-style configuration files that define:
- Project settings and configuration
- Document list (SchDoc, PcbDoc, SchLib, PcbLib, OutJob, etc.)
- Output groups and configurations
- ERC rules and settings

File Format:
- INI-style with [Section] headers
- Key=Value pairs within sections
- Document sections: [Document1], [Document2], etc.
- Each document has DocumentPath and DocumentUniqueId fields
"""

from __future__ import annotations

import configparser
import logging
import re
import uuid
from collections.abc import Mapping
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from .altium_api_markers import public_api

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .altium_outjob_runner import OutJobRunResult


DocumentOption = tuple[str, str]
_PRIMARY_TEXT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252")
_LOSSY_TEXT_FALLBACK = "latin-1"


class DocumentEntry(TypedDict):
    path: str
    unique_id: str
    options: list[DocumentOption]


def _preserve_option_case(optionstr: str) -> str:
    """
    ConfigParser callback that preserves original key casing.
    """
    return optionstr


def _decode_prjpcb_text(raw: bytes, filepath: Path) -> tuple[str, str]:
    """
    Decode PrjPcb text with a Windows-compatible fallback policy.
    """
    for encoding in _PRIMARY_TEXT_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue

    log.warning(
        "Loading legacy PrjPcb with lossy latin-1 fallback: %s",
        filepath,
    )
    return raw.decode(_LOSSY_TEXT_FALLBACK), _LOSSY_TEXT_FALLBACK


def _parse_variant_key_values(value: str) -> dict[str, str]:
    """
    Parse Altium's pipe-separated project variant field format.
    """
    result: dict[str, str] = {}
    for pair in str(value or "").split("|"):
        if "=" not in pair:
            continue
        key, parsed_value = pair.split("=", 1)
        result[key] = parsed_value
    return result


def _build_parameter_override_map(
    param_variations: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    """
    Group parsed ParamVariation rows as designator -> parameter name -> value.
    """
    overrides: dict[str, dict[str, str]] = {}
    for row in param_variations:
        designator = str(
            row.get("ParamDesignator") or row.get("Designator") or ""
        ).strip()
        parameter_name = str(row.get("ParameterName") or "").strip()
        if not designator or not parameter_name:
            continue
        overrides.setdefault(designator, {})[parameter_name] = str(
            row.get("VariantValue", "")
        )
    return overrides


class NetIdentifierScope(IntEnum):
    """
    Net Identifier Scope for Altium projects.

    Controls how net names propagate across sheets in multi-sheet designs.
    Stored as HierarchyMode in the [Design] section of .PrjPcb files.
    """

    AUTOMATIC = 0  # eFlatten_Smart: Smart hierarchy (default for board projects)
    FLAT = 1  # eFlatten_Flat: Only ports global
    HIERARCHICAL = 2  # eFlatten_Hierarchical_GlobalPorts: Sheet entry/port connections
    GLOBAL = 3  # eFlatten_Global: Everything global (default for free documents)
    STRICT_HIERARCHICAL = 4  # eFlatten_Hierarchical_Strict: Strict hierarchical


def _normalize_altium_path(path: str, is_directory: bool = False) -> str:
    """
    Normalize a path to Altium's expected format (Windows-style).

    Altium requires:
    - Backslashes as separators (not forward slashes)
    - Directory paths should end with backslash

    Args:
        path: Path string to normalize
        is_directory: If True, ensure path ends with backslash

    Returns:
        Normalized path string with Windows-style separators
    """
    if not path:
        return path

    # Convert forward slashes to backslashes
    normalized = path.replace("/", "\\")

    # Add trailing backslash for directories
    if is_directory and normalized and not normalized.endswith("\\"):
        normalized += "\\"

    return normalized


def _numbered_section_index(section: str, prefix: str) -> int | None:
    match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", section)
    if match is None:
        return None
    return int(match.group(1))


def _next_numbered_section_name(
    config: configparser.ConfigParser,
    prefix: str,
) -> str:
    highest_index = 0
    for section in config.sections():
        index = _numbered_section_index(section, prefix)
        if index is not None:
            highest_index = max(highest_index, index)
    return f"{prefix}{highest_index + 1}"


def _parse_altium_major_version(version: int | str) -> int:
    """
    Parse an Altium major version selector.

    Accepted inputs:
    - integer major version (e.g. 25)
    - "AD25"
    - "25"
    - "25.8.1" (major extracted as 25)
    """
    if isinstance(version, int):
        if version <= 0:
            raise ValueError(f"Invalid Altium major version: {version}")
        return version

    text = str(version).strip().upper()
    if text.startswith("AD"):
        text = text[2:].strip()

    match = re.match(r"^(\d+)", text)
    if not match:
        raise ValueError(f"Could not parse Altium version selector: {version!r}")
    major = int(match.group(1))
    if major <= 0:
        raise ValueError(f"Invalid Altium major version: {major}")
    return major


@public_api
class AltiumPrjPcbOutJob:
    """
    OutJob handle bound to a specific AltiumPrjPcb instance.

    This is a lightweight consumer API for running OutJobs directly from a
    loaded project object.
    """

    def __init__(self, project: AltiumPrjPcb, path: Path) -> None:
        self._project = project
        self._path = path.resolve()

    @property
    def path(self) -> Path:
        """
        Absolute path to the `.OutJob` file.
        """
        return self._path

    @property
    def name(self) -> str:
        """
        Filename of the `.OutJob` (including extension).
        """
        return self._path.name

    def run(
        self,
        preferred_version: int | str = 25,
        *,
        timeout_seconds: float = 300.0,
        normalize_generated_paths: bool = True,
        default_generated_output_path: str | None = None,
        bind_pcbdoc_path: Path | str | None = None,
        auto_bind_pcbdoc: bool = False,
        stage_outjob_copy: bool = True,
        script_directory: Path | str | None = None,
        keep_script_artifacts: bool = False,
        poll_interval_seconds: float = 0.5,
    ) -> OutJobRunResult:
        """
        Run this OutJob.

        Args:
            preferred_version: Altium major version selector (e.g. 25 or "AD25").
            timeout_seconds: Run timeout.
            normalize_generated_paths: Normalize generated output path fields before run.
            default_generated_output_path: Fallback relative path for generated outputs.
            bind_pcbdoc_path: Optional explicit `.PcbDoc` for DocumentPath rebinding.
            auto_bind_pcbdoc: If True, auto-discover the primary project
                `.PcbDoc` and bind it into embedded OutJob `DocumentPath=`
                tokens. Leave False for normal project-bound OutJobs.
            stage_outjob_copy: Run using a temporary staged copy of the OutJob.
            script_directory: Where to write temporary run artifacts.
            keep_script_artifacts: Keep generated script artifacts after run.
            poll_interval_seconds: Marker poll interval.

        Returns:
            `OutJobRunResult` from `altium_outjob_runner`.
        """
        if self._project.filepath is None:
            raise ValueError("Project has no filepath context; load from file first")

        from .altium_outjob_runner import AltiumOutJobRunner

        runner = AltiumOutJobRunner(
            preferred_version=_parse_altium_major_version(preferred_version)
        )
        return runner.run(
            self._project.filepath,
            outjob_path=self._path,
            timeout_seconds=timeout_seconds,
            normalize_generated_paths=normalize_generated_paths,
            default_generated_output_path=default_generated_output_path,
            bind_pcbdoc_path=bind_pcbdoc_path,
            auto_bind_pcbdoc=auto_bind_pcbdoc,
            stage_outjob_copy=stage_outjob_copy,
            script_directory=script_directory,
            keep_script_artifacts=keep_script_artifacts,
            poll_interval_seconds=poll_interval_seconds,
        )

    def __repr__(self) -> str:
        return f"AltiumPrjPcbOutJob(path={self._path})"


@public_api
class AltiumPrjPcb:
    """
    Parser and writer for Altium .PrjPcb project files.

    Attributes:
        filepath: Path to the project file
        config: ConfigParser object with project data
        documents: List of document paths
    """

    def __init__(self, filepath: Path | str | None = None) -> None:
        """
        Create an AltiumPrjPcb.

                Args:
                    filepath: Path to .PrjPcb file to parse.
                              If None, creates an empty project.
        """
        self.filepath = Path(filepath) if filepath is not None else None
        self.config = configparser.ConfigParser(interpolation=None)
        self.config.optionxform = _preserve_option_case
        self.documents: list[DocumentEntry] = []
        self._loaded_encoding: str | None = None

        if filepath is not None:
            self._load_from_file()

    def _load_from_file(self) -> None:
        """
        Parse the project file at self.filepath.
        """
        filepath = self.filepath
        if filepath is None or not filepath.exists():
            raise FileNotFoundError(f"Project file not found: {filepath}")
        text, encoding = _decode_prjpcb_text(filepath.read_bytes(), filepath)
        self._loaded_encoding = encoding
        self.config.read_string(text, source=str(filepath))
        self._extract_documents()

    def _extract_documents(self) -> None:
        """
        Extract document list from config (including full DocumentN options).
        """
        self.documents = []
        doc_num = 1

        while True:
            section = f"Document{doc_num}"
            if not self.config.has_section(section):
                break

            doc_path = self.config.get(section, "DocumentPath", fallback="")
            doc_id = self.config.get(section, "DocumentUniqueId", fallback="")
            options = [(key, value) for key, value in self.config.items(section)]

            self.documents.append(
                {
                    "path": doc_path,
                    "unique_id": doc_id,
                    "options": options,
                }
            )

            doc_num += 1

    def add_document(self, path: str | Path, unique_id: str | None = None) -> None:
        """
        Add a document to the project.

        Args:
            path: Document path (relative to project).
                  Use backslashes for Windows compatibility.
            unique_id: Optional unique ID (generated if not provided)
        """
        path_str = str(Path(path).name)  # Use just the filename

        # Normalize path to Windows format (backslashes)
        path_str = _normalize_altium_path(path_str, is_directory=False)

        if unique_id is None:
            # Generate a unique ID similar to Altium's format (8 uppercase chars)
            unique_id = str(uuid.uuid4()).replace("-", "").upper()[:8]

        self.documents.append(
            {
                "path": path_str,
                "unique_id": unique_id,
                "options": [
                    ("DocumentPath", path_str),
                    ("DocumentUniqueId", unique_id),
                ],
            }
        )

    def remove_all_documents(self) -> None:
        """
        Remove all documents from the project.
        """
        self.documents = []

        # Remove document sections from config
        doc_num = 1
        while True:
            section = f"Document{doc_num}"
            if not self.config.has_section(section):
                break
            self.config.remove_section(section)
            doc_num += 1

    def set_documents_from_directory(
        self, directory: Path, pattern: str = "*.SchDoc"
    ) -> None:
        """
        Set documents by scanning a directory for files.

        Args:
            directory: Directory to scan
            pattern: Glob pattern for files to include
        """
        self.remove_all_documents()

        directory = Path(directory)
        files = sorted(directory.glob(pattern), key=lambda p: p.name.lower())

        for file in files:
            self.add_document(file.name)

    def save(self, filepath: Path | str) -> None:
        """
        Save project to file.

        This is the canonical public write path for PrjPcb files.

                Args:
                    filepath: Output path.
        """
        self._write_to_file(Path(filepath))

    def _write_to_file(self, filepath: Path) -> None:
        """
        Internal write implementation.
        """
        filepath = Path(filepath)

        # Remove old document sections
        doc_num = 1
        while True:
            section = f"Document{doc_num}"
            if not self.config.has_section(section):
                break
            self.config.remove_section(section)
            doc_num += 1

        # Add document sections
        for idx, doc in enumerate(self.documents, start=1):
            section = f"Document{idx}"
            self.config.add_section(section)

            path_value = _normalize_altium_path(
                str(doc.get("path", "")),
                is_directory=False,
            )
            unique_id = str(doc.get("unique_id", ""))

            options = doc.get("options", [])
            wrote_path = False
            wrote_unique_id = False
            if isinstance(options, list):
                for item in options:
                    if not isinstance(item, tuple) or len(item) != 2:
                        continue
                    key, value = str(item[0]), str(item[1])
                    key_lower = key.lower()
                    if key_lower == "documentpath":
                        value = path_value
                        wrote_path = True
                    elif key_lower == "documentuniqueid":
                        value = unique_id
                        wrote_unique_id = True
                    self.config.set(section, key, value)

            if not wrote_path:
                self.config.set(section, "DocumentPath", path_value)
            if not wrote_unique_id:
                self.config.set(section, "DocumentUniqueId", unique_id)

        # Write to file with UTF-8 BOM (Altium standard)
        with open(filepath, "w", encoding="utf-8-sig") as f:
            self.config.write(f, space_around_delimiters=False)

    @classmethod
    def create_minimal(cls, name: str = "project") -> AltiumPrjPcb:
        """
        Create a minimal project file with default settings.

        Args:
            name: Project name

        Returns:
            AltiumPrjPcb instance with default settings
        """
        project = cls()

        # Add minimal required sections
        project.config.add_section("Design")
        project.config.set("Design", "Version", "1.0")
        project.config.set("Design", "HierarchyMode", "0")
        project.config.set("Design", "ChannelRoomNamingStyle", "0")
        project.config.set("Design", "ReleasesFolder", "")
        project.config.set(
            "Design", "ChannelDesignatorFormatString", "$Component_$RoomName"
        )
        project.config.set("Design", "ChannelRoomLevelSeperator", "_")
        project.config.set("Design", "OpenOutputs", "1")

        project.config.add_section("Preferences")
        project.config.set("Preferences", "PrefsVaultGUID", "")
        project.config.set("Preferences", "PrefsRevisionGUID", "")

        return project

    def set_parameter(self, name: str, value: str) -> None:
        """
        Set one project-level parameter.

        Project parameters are written as numbered `[ParameterN]` sections in
        the `.PrjPcb`, not as schematic document parameters.

        Args:
            name: Project parameter name, for example `PCB_CODENAME`.
            value: Project parameter value.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("project parameter name must not be blank")

        target_name = clean_name.lower()
        for section in self.config.sections():
            if _numbered_section_index(section, "Parameter") is None:
                continue
            if not self.config.has_option(section, "Name"):
                continue
            existing_name = self.config.get(section, "Name")
            if existing_name.lower() != target_name:
                continue
            self.config.set(section, "Value", str(value))
            return

        section = _next_numbered_section_name(self.config, "Parameter")
        self.config.add_section(section)
        self.config.set(section, "Name", clean_name)
        self.config.set(section, "Value", str(value))

    def set_parameters(self, parameters: Mapping[str, str]) -> None:
        """
        Set multiple project-level parameters.

        Existing parameters are updated case-insensitively; new parameters are
        appended as additional numbered `[ParameterN]` sections.
        """
        for name, value in parameters.items():
            self.set_parameter(name, value)

    def delete_parameter(self, name: str) -> bool:
        """
        Delete one project-level parameter by name.

        Args:
            name: Project parameter name. Matching is case-insensitive.

        Returns:
            `True` when a parameter section was removed, otherwise `False`.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("project parameter name must not be blank")

        target_name = clean_name.lower()
        for section in self.config.sections():
            if _numbered_section_index(section, "Parameter") is None:
                continue
            if not self.config.has_option(section, "Name"):
                continue
            existing_name = self.config.get(section, "Name")
            if existing_name.lower() != target_name:
                continue
            self.config.remove_section(section)
            return True
        return False

    def set_current_variant(self, name: str | None) -> None:
        """
        Set or clear the current project variant.

        `=VariantName` resolves from this project-level setting, not from a
        schematic document parameter.
        """
        if not self.config.has_section("Design"):
            self.config.add_section("Design")
        if name is None:
            self.config.remove_option("Design", "CurrentVariant")
            return
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("current variant name must not be blank")
        self.config.set("Design", "CurrentVariant", clean_name)

    def add_variant(
        self,
        name: str,
        *,
        unique_id: str | None = None,
        allow_fabrication: bool = True,
        current: bool = False,
    ) -> str:
        """
        Add an empty project variant and optionally make it current.

        This creates the standard `[ProjectVariantN]` section used by Altium.
        Component-level fitted/not-fitted and alternate-part variation entries
        are intentionally outside this convenience method.

        Args:
            name: Variant description/name, for example `A`.
            unique_id: Optional Altium variant GUID. A GUID is generated when
                omitted.
            allow_fabrication: Whether the variant allows fabrication output.
            current: Also write `[Design] CurrentVariant` to this variant name.

        Returns:
            The variant unique ID.
        """
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("variant name must not be blank")

        target_name = clean_name.lower()
        for section in self.config.sections():
            if _numbered_section_index(section, "ProjectVariant") is None:
                continue
            existing_name = self.config.get(section, "Description", fallback="")
            if existing_name.lower() != target_name:
                continue
            if unique_id is not None:
                self.config.set(section, "UniqueId", unique_id)
            if not self.config.has_option(section, "UniqueId"):
                self.config.set(section, "UniqueId", str(uuid.uuid4()).upper())
            self.config.set(
                section,
                "AllowFabrication",
                "1" if allow_fabrication else "0",
            )
            for option, default in (
                ("ParameterCount", "0"),
                ("VariationCount", "0"),
                ("ParamVariationCount", "0"),
            ):
                if not self.config.has_option(section, option):
                    self.config.set(section, option, default)
            if current:
                self.set_current_variant(clean_name)
            return self.config.get(section, "UniqueId")

        section = _next_numbered_section_name(self.config, "ProjectVariant")
        resolved_unique_id = unique_id or str(uuid.uuid4()).upper()
        self.config.add_section(section)
        self.config.set(section, "UniqueId", resolved_unique_id)
        self.config.set(section, "Description", clean_name)
        self.config.set(
            section,
            "AllowFabrication",
            "1" if allow_fabrication else "0",
        )
        self.config.set(section, "ParameterCount", "0")
        self.config.set(section, "VariationCount", "0")
        self.config.set(section, "ParamVariationCount", "0")
        if current:
            self.set_current_variant(clean_name)
        return resolved_unique_id

    @property
    def parameters(self) -> dict[str, str]:
        """
        Get all project parameters.

        Project parameters are stored in sections like [Parameter1], [Parameter2], etc.
        Each section has Name and Value fields.

        Returns:
            Dict mapping parameter names to their values
        """
        params = {}
        for section in self.config.sections():
            if (
                section.startswith("Parameter")
                and self.config.has_option(section, "Name")
                and self.config.has_option(section, "Value")
            ):
                name = self.config.get(section, "Name")
                value = self.config.get(section, "Value")
                params[name] = value
        return params

    @property
    def variants(self) -> dict[str, dict]:
        """
        Get all project variants with their configuration and DNP lists.

        Project variants are stored in sections like [ProjectVariant1], [ProjectVariant2], etc.
        Each variant has a description, unique ID, and a list of component variations
        (e.g., Not Fitted, Alternate Part).

        Returns:
            Dict mapping variant description (name) to variant data:
            {
                'unique_id': str,
                'allow_fabrication': bool,
                'variation_count': int,
                'variations': list[dict],  # Raw variation data
                'parameter_count': int,
                'parameters': list[dict],  # Variant-level parameter rows
                'param_variation_count': int,
                'param_variations': list[dict],  # Per-variation parameter changes
                'parameter_overrides': dict[str, dict[str, str]],
                'DNP': list[str]           # Designators marked as Not Fitted (Kind=1)
            }
        """
        variants = {}

        # Find all ProjectVariant sections
        variant_sections = [
            s for s in self.config.sections() if s.startswith("ProjectVariant")
        ]

        for section in variant_sections:
            # Extract base properties
            description = self.config.get(section, "Description", fallback="Unknown")

            variations = []
            variation_count = self.config.getint(section, "VariationCount", fallback=0)
            parameters = []
            parameter_count = self.config.getint(section, "ParameterCount", fallback=0)
            param_variations = []
            param_variation_count = self.config.getint(
                section, "ParamVariationCount", fallback=0
            )

            # Get variations
            for i in range(1, variation_count + 1):
                var_key = f"Variation{i}"
                if self.config.has_option(section, var_key):
                    var_str = self.config.get(section, var_key)
                    variations.append(_parse_variant_key_values(var_str))

            # Get variant-level parameters
            for i in range(1, parameter_count + 1):
                param_key = f"Parameter{i}"
                if self.config.has_option(section, param_key):
                    param_str = self.config.get(section, param_key)
                    parameters.append(_parse_variant_key_values(param_str))

            # Get per-variation parameter overrides. Altium indexes these in
            # lockstep with ParamDesignatorN rows.
            for i in range(1, param_variation_count + 1):
                param_var_key = f"ParamVariation{i}"
                if self.config.has_option(section, param_var_key):
                    param_var_str = self.config.get(section, param_var_key)
                    param_variation = _parse_variant_key_values(param_var_str)
                    param_designator_key = f"ParamDesignator{i}"
                    if self.config.has_option(section, param_designator_key):
                        param_designator = self.config.get(
                            section, param_designator_key
                        )
                        param_variation["ParamDesignator"] = param_designator
                        param_variation.setdefault("Designator", param_designator)
                    param_variations.append(param_variation)

            parameter_overrides = _build_parameter_override_map(param_variations)

            variants[description] = {
                "unique_id": self.config.get(section, "UniqueId", fallback=""),
                "allow_fabrication": self.config.getboolean(
                    section, "AllowFabrication", fallback=False
                ),
                "variation_count": variation_count,
                "variations": variations,
                "parameter_count": parameter_count,
                "parameters": parameters,
                "param_variation_count": param_variation_count,
                "param_variations": param_variations,
                "parameter_overrides": parameter_overrides,
            }

            # Build DNP list (Kind=1 means Not Fitted)
            dnp_list = []
            for v in variations:
                if v.get("Kind") == "1":
                    dnp_list.append(v.get("Designator", ""))
            variants[description]["DNP"] = dnp_list

        return variants

    def get_parameter(self, name: str) -> str | None:
        """
        Get project parameter value by name (case-insensitive).

        Args:
            name: Parameter name to look up

        Returns:
            Parameter value, or None if not found
        """
        name_lower = name.lower()
        for key, value in self.parameters.items():
            if key.lower() == name_lower:
                return value
        return None

    def get_current_variant(self) -> str | None:
        """
        Get the current variant name from the project's [Design] section.

        The current variant is stored as CurrentVariant=XXX in the [Design] section.
        This is used for parameter substitution (e.g., =VariantName in templates).

        Returns:
            Current variant name (e.g., "A0"), or None if not set.
        """
        if self.config.has_option("Design", "CurrentVariant"):
            return self.config.get("Design", "CurrentVariant")
        return None

    def get_schdoc_paths(self) -> list[Path]:
        """
        Get full paths to all SchDoc files in the project.

        Returns:
            List of absolute Path objects for each SchDoc referenced in the project.
            Paths are resolved relative to the project file's directory.

        Raises:
            ValueError: If project was not loaded from a file (no filepath context).
        """
        if not self.filepath:
            raise ValueError("Cannot get SchDoc paths: project has no filepath context")

        return self._get_document_paths_by_extension(".schdoc")

    def get_reachable_schdoc_paths(self) -> list[Path]:
        """
        Get active SchDoc paths from durable `.PrjPcb` document metadata.

        Active design sheets carry the normal document option payload
        (`AnnotationEnabled`, `AnnotateOrder`, etc.). Inactive/scratch pages
        often remain as stub entries with only `DocumentPath` and
        `DocumentUniqueId`.

        This method intentionally avoids transient `.PrjPcbStructure` files.

        Returns:
            List of absolute Path objects for active SchDocs in project order.
        """
        all_paths = self.get_schdoc_paths()
        if not all_paths:
            return []

        path_by_name = {path.name.lower(): path for path in all_paths}
        active_paths: list[Path] = []
        for document in self.documents:
            doc_path = str(document.get("path", ""))
            if Path(doc_path).suffix.lower() != ".schdoc":
                continue

            option_keys = {key for key, _ in document["options"]}
            extra_keys = option_keys - {"DocumentPath", "DocumentUniqueId"}
            if not extra_keys:
                continue

            full_path = path_by_name.get(Path(doc_path).name.lower())
            if full_path is not None:
                active_paths.append(full_path)

        return active_paths or all_paths

    def get_pcbdoc_paths(self) -> list[Path]:
        """
        Get full paths to all PcbDoc files in the project.

        Returns:
            List of absolute Path objects for each PcbDoc referenced in the project.
            Paths are resolved relative to the project file's directory.

        Raises:
            ValueError: If project was not loaded from a file (no filepath context).
        """
        return self._get_document_paths_by_extension(".pcbdoc")

    def get_outjob_paths(self) -> list[Path]:
        """
        Get full paths to all OutJob files referenced in the project.

        Returns:
            List of absolute Path objects for each OutJob referenced in the project.
            Paths are resolved relative to the project file's directory.

        Raises:
            ValueError: If project was not loaded from a file (no filepath context).
        """
        return self._get_document_paths_by_extension(".outjob")

    def _get_document_paths_by_extension(self, extension: str) -> list[Path]:
        """
        Resolve project document paths by extension.

        Args:
            extension: File extension filter (e.g. '.schdoc').

        Returns:
            List of existing absolute paths in project order.
        """
        if not self.filepath:
            raise ValueError(
                "Cannot get document paths: project has no filepath context"
            )

        ext = extension.lower()
        project_dir = self.filepath.parent
        matched_paths: list[Path] = []
        for doc in self.documents:
            doc_path = doc["path"]
            if not doc_path.lower().endswith(ext):
                continue
            full_path = (project_dir / doc_path).resolve()
            if full_path.exists():
                matched_paths.append(full_path)
        return matched_paths

    def outjob(self, name: str | Path | None = None) -> AltiumPrjPcbOutJob:
        """
        Resolve a project OutJob handle.

        Args:
            name: OutJob name/path. Accepts:
                - None: auto-select (prefer `reference_gen.OutJob`, else single outjob)
                - "reference_gen" or "reference_gen.OutJob"
                - relative or absolute `Path`

        Returns:
            Bound `AltiumPrjPcbOutJob` handle.

        Raises:
            ValueError: If no/some ambiguous OutJobs are available.
            FileNotFoundError: If the requested OutJob cannot be resolved.
        """
        if not self.filepath:
            raise ValueError("Cannot resolve OutJob: project has no filepath context")

        project_dir = self.filepath.parent
        known_paths: list[Path] = self.get_outjob_paths()

        # Include sibling .OutJob files so users can run generated outjobs even
        # before adding them to DocumentN entries.
        for candidate in sorted(
            project_dir.glob("*.OutJob"), key=lambda p: p.name.lower()
        ):
            resolved = candidate.resolve()
            if all(
                str(resolved).lower() != str(existing).lower()
                for existing in known_paths
            ):
                known_paths.append(resolved)

        if name is None:
            preferred = (project_dir / "reference_gen.OutJob").resolve()
            for candidate in known_paths:
                if str(candidate).lower() == str(preferred).lower():
                    return AltiumPrjPcbOutJob(self, candidate)
            if len(known_paths) == 1:
                return AltiumPrjPcbOutJob(self, known_paths[0])
            if len(known_paths) == 0:
                raise FileNotFoundError(
                    f"No .OutJob found for project: {self.filepath}"
                )
            names = ", ".join(path.name for path in known_paths)
            raise ValueError(f"Multiple OutJobs found; specify one explicitly: {names}")

        name_text = str(name)
        requested = Path(name_text)
        if requested.is_absolute():
            resolved = requested.resolve()
            if not resolved.is_file():
                raise FileNotFoundError(f"OutJob not found: {resolved}")
            return AltiumPrjPcbOutJob(self, resolved)

        requested_name = requested.name
        if requested.suffix.lower() != ".outjob":
            requested_name = f"{requested_name}.OutJob"

        for candidate in known_paths:
            if candidate.name.lower() == requested_name.lower():
                return AltiumPrjPcbOutJob(self, candidate)

        fallback = (project_dir / requested_name).resolve()
        if fallback.is_file():
            return AltiumPrjPcbOutJob(self, fallback)
        raise FileNotFoundError(f"OutJob not found for project: {requested_name}")

    @property
    def net_identifier_scope(self) -> NetIdentifierScope:
        """
        Get the Net Identifier Scope (HierarchyMode) from the [Design] section.

        This controls how net names propagate across sheets:
        - AUTOMATIC (0): Smart hierarchy (default for board projects)
        - FLAT (1): Only ports are global across sheets
        - HIERARCHICAL (2): Sheet entry/port connections define scope
        - GLOBAL (3): All net identifiers are global (default for free documents)
        - STRICT_HIERARCHICAL (4): Strict hierarchical, ports only within hierarchy

        Returns:
            NetIdentifierScope enum value
        """
        mode = self.config.getint("Design", "HierarchyMode", fallback=0)
        try:
            return NetIdentifierScope(mode)
        except ValueError:
            log.warning("Unknown HierarchyMode=%d, defaulting to AUTOMATIC", mode)
            return NetIdentifierScope.AUTOMATIC

    @property
    def netlist_options(self) -> dict:
        """
        Get netlist-related project options from the [Design] section.

        These settings affect how netlists are generated:
        - net_identifier_scope: NetIdentifierScope enum (HierarchyMode)
        - allow_ports_to_name_nets: If True, ports can assign net names
        - allow_sheet_entries_to_name_nets: If True, sheet entries can assign net names
        - allow_single_pin_nets: If True, single-pin nets are included in output
        - append_sheet_numbers_to_local_nets: If True, sheet numbers prefix local nets
        - power_port_names_take_priority: If True, power ports override other naming
        - name_nets_hierarchically: If True, nets include hierarchy path

        Returns:
            Dict of settings for netlist generation
        """
        design = "Design"
        return {
            "net_identifier_scope": self.net_identifier_scope,
            "allow_ports_to_name_nets": self.config.getboolean(
                design, "AllowPortNetNames", fallback=False
            ),
            "allow_sheet_entries_to_name_nets": self.config.getboolean(
                design, "AllowSheetEntryNetNames", fallback=True
            ),
            "allow_single_pin_nets": self.config.getboolean(
                design, "NetlistSinglePinNets", fallback=False
            ),
            "append_sheet_numbers_to_local_nets": self.config.getboolean(
                design, "AppendSheetNumberToLocalNets", fallback=False
            ),
            "power_port_names_take_priority": self.config.getboolean(
                design, "PowerPortNamesTakePriority", fallback=False
            ),
            "name_nets_hierarchically": self.config.getboolean(
                design, "NameNetsHierarchically", fallback=False
            ),
            "auto_sheet_numbering": self.config.getboolean(
                design, "AutoSheetNumbering", fallback=False
            ),
            "channel_designator_format": self.config.get(
                design, "ChannelDesignatorFormatString", fallback=""
            ),
        }

    def __getattr__(self, name: str) -> Any:
        """
        Convenience dynamic access for project OutJobs.

        Attribute form:
            `<stem>_outjob` -> resolves `<stem>.OutJob`
        """
        if name.endswith("_outjob"):
            stem = name[:-7]
            if stem:
                try:
                    return self.outjob(stem)
                except (FileNotFoundError, ValueError):
                    pass
        raise AttributeError(f"{self.__class__.__name__!s} has no attribute {name!r}")

    def __repr__(self) -> str:
        return (
            f"AltiumPrjPcb(filepath={self.filepath}, documents={len(self.documents)})"
        )
