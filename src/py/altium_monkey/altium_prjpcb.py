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
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key, dotnet_trim
from .altium_api_markers import public_api
from .altium_configparser_helpers import preserve_option_case as _preserve_option_case
from ._logical_source_identity import (
    _logical_source_basename,
    _logical_source_identity_key,
    _normalize_project_document_identity as _normalize_logical_project_identity,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .altium_outjob_runner import OutJobRunResult


DocumentOption = tuple[str, str]
_PRIMARY_TEXT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "cp1252")
_ASCII_LOWER_TRANSLATION = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)
_LOSSY_TEXT_FALLBACK = "latin-1"


class DocumentEntry(TypedDict):
    path: str
    unique_id: str
    options: list[DocumentOption]


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
    field_names: dict[str, str] = {}
    for pair in str(value or "").split("|"):
        if "=" not in pair:
            continue
        key, parsed_value = pair.split("=", 1)
        lookup_key = dotnet_ordinal_ignore_case_key(key)
        selected_key = field_names.setdefault(lookup_key, key)
        result[selected_key] = parsed_value
    return result


def _build_parameter_override_map(
    param_variations: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    """
    Group parsed ParamVariation rows as designator -> parameter name -> value.
    """
    overrides: dict[str, dict[str, str]] = {}
    designator_names: dict[str, str] = {}
    parameter_names: dict[str, dict[str, str]] = {}
    for row in param_variations:
        designator = str(
            _variant_row_value(row, "ParamDesignator")
            or _variant_row_value(row, "Designator")
            or ""
        ).strip()
        parameter_name = str(_variant_row_value(row, "ParameterName") or "").strip()
        if not designator or not parameter_name:
            continue
        designator_key = dotnet_ordinal_ignore_case_key(designator)
        selected_designator = designator_names.setdefault(designator_key, designator)
        selected_parameters = overrides.setdefault(selected_designator, {})
        selected_names = parameter_names.setdefault(designator_key, {})
        parameter_key = dotnet_ordinal_ignore_case_key(parameter_name)
        selected_parameter = selected_names.setdefault(parameter_key, parameter_name)
        selected_parameters[selected_parameter] = str(
            _variant_row_value(row, "VariantValue") or ""
        )
    return overrides


def _variant_row_value(row: Mapping[str, str], name: str) -> str | None:
    lookup_key = dotnet_ordinal_ignore_case_key(name)
    for key, value in reversed(tuple(row.items())):
        if dotnet_ordinal_ignore_case_key(key) == lookup_key:
            return value
    return None


def _managed_variation_row(
    raw: str, *, overwrite_pcb_footprint: bool = True
) -> dict[str, object] | None:
    fields = raw.split("|")
    required = _managed_variation_required_fields(fields)
    if required is None:
        return None
    parameter_designator, unique_id, raw_kind, alternate = required
    designator = parameter_designator
    if not dotnet_trim(unique_id):
        return None
    kind = _managed_variation_kind(raw_kind)
    raw_alternate_part = alternate if kind == "2" else ""
    alternate_part = raw_alternate_part
    alternate_library_link = (
        _managed_alternate_library_link(
            fields, overwrite_pcb_footprint=overwrite_pcb_footprint
        )
        if kind == "2"
        else None
    )
    if kind == "2" and not _managed_alternate_link_is_valid(fields):
        kind = "0"
    return {
        "Designator": designator,
        "UniqueId": unique_id,
        "Kind": kind,
        "AlternatePart": alternate_part,
        "_parameters": {},
        "_parameter_designator": parameter_designator,
        "_alternate_library_link": alternate_library_link,
    }


def _managed_alternate_library_link(
    fields: list[str], *, overwrite_pcb_footprint: bool
) -> dict[str, object]:
    """Retain the exact link object created for an authored alternate row."""
    values: dict[str, object] = {
        "LibIdentifierKind": "NameWithType",
        "LibraryIdentifier": "",
        "SymbolReference": "",
        "DesignItemID": "",
        "UseLibraryName": False,
        "SourceLibraryName": "",
        "UseDBTableName": False,
        "DatabaseTableName": "",
        "VaultGUID": "",
        "ItemGUID": "",
        "RevisionGUID": "",
        "Footprint": "",
        "IsSameFootprint": False,
    }
    boolean_fields = {"UseLibraryName", "UseDBTableName", "IsSameFootprint"}
    for field in fields[4:]:
        parsed = _managed_field_value(field, "AltLibLink_")
        if parsed is None:
            continue
        name, separator, value = parsed.partition("=")
        if not separator or name not in values:
            continue
        if name == "LibIdentifierKind":
            values[name] = _managed_library_identifier_kind(value)
        elif name in boolean_fields:
            parsed = _parse_altium_bool(value, default=False)
            values[name] = (
                parsed or not overwrite_pcb_footprint
                if name == "IsSameFootprint"
                else parsed
            )
        else:
            values[name] = value if dotnet_trim(value) else ""
    return values


def _managed_library_identifier_kind(value: str) -> str:
    kinds = {
        dotnet_ordinal_ignore_case_key(label): name
        for label, name in (
            ("Any", "Any"),
            ("Library Name Only", "NameNoType"),
            ("Library Name And Type", "NameWithType"),
            ("Library Full Path", "FullPath"),
            ("Vault Name", "VaultName"),
        )
    }
    return kinds.get(dotnet_ordinal_ignore_case_key(value), "NameWithType")


def _managed_alternate_link_is_valid(fields: list[str]) -> bool:
    identifiers = dict.fromkeys(
        ("LibraryIdentifier", "DesignItemID", "VaultGUID", "ItemGUID"), False
    )
    for index in range(4, len(fields)):
        field = _managed_field_value(fields[index], "AltLibLink_")
        if field is None:
            continue
        name, separator, value = field.partition("=")
        # Managed prefix matching ignores case; its field-name switch does not.
        if separator and name in identifiers:
            identifiers[name] = bool(dotnet_trim(value))
    return (identifiers["LibraryIdentifier"] and identifiers["DesignItemID"]) or (
        identifiers["VaultGUID"] and identifiers["ItemGUID"]
    )


def _managed_variation_required_fields(
    fields: list[str],
) -> tuple[str, str, str, str] | None:
    if len(fields) < 4:
        return None
    parameter_designator = _managed_field_value(fields[0], "Designator=", anywhere=True)
    unique_id = _managed_field_value(fields[1], "UniqueId=")
    raw_kind = _managed_field_value(fields[2], "Kind=")
    alternate = _managed_field_value(fields[3], "AlternatePart=")
    values = (parameter_designator, unique_id, raw_kind, alternate)
    if any(value is None for value in values):
        return None
    return cast(tuple[str, str, str, str], values)


def _managed_variation_kind(raw: str) -> str:
    clean = raw.strip()
    if not re.fullmatch(r"[+-]?[0-9]+", clean):
        return "0"
    try:
        parsed = int(clean, 10)
    except ValueError:
        return "0"
    if not -(2**31) <= parsed < 2**31:
        return "0"
    return str(parsed) if parsed in {0, 1, 2} else "0"


def _managed_field_value(
    value: str,
    marker: str,
    *,
    anywhere: bool = False,
) -> str | None:
    normalized = dotnet_ordinal_ignore_case_key(value)
    normalized_marker = dotnet_ordinal_ignore_case_key(marker)
    index = normalized.find(normalized_marker) if anywhere else 0
    if index < 0 or not normalized[index:].startswith(normalized_marker):
        return None
    return value[index + len(marker) :]


def _managed_field_starts_with(value: str, marker: str) -> bool:
    return dotnet_ordinal_ignore_case_key(value).startswith(
        dotnet_ordinal_ignore_case_key(marker)
    )


def _managed_variation_parameter(raw: str) -> tuple[str, str] | None:
    fields = raw.split("|")
    if len(fields) < 2:
        return None
    parameter_marker = "ParameterName="
    parameter_index = dotnet_ordinal_ignore_case_key(fields[0]).find(
        dotnet_ordinal_ignore_case_key(parameter_marker)
    )
    if parameter_index < 0 or not _managed_field_starts_with(
        fields[1], "VariantValue="
    ):
        return None
    name = fields[0][parameter_index + len(parameter_marker) :]
    value = fields[1][len("VariantValue=") :]
    return name, value if dotnet_trim(value) else ""


def _managed_variations_from_entries(
    entries: list[tuple[str, str]],
    *,
    overwrite_pcb_footprint: bool = True,
) -> list[dict[str, object]]:
    variations: list[dict[str, object]] = []
    for index, (key, raw) in enumerate(entries):
        if not _numbered_key(key, "Variation"):
            continue
        variation = _managed_variation_row(
            raw, overwrite_pcb_footprint=overwrite_pcb_footprint
        )
        if variation is None:
            continue
        _attach_managed_variation_parameters(entries, index + 1, variation)
        variations.append(variation)
    return variations


def _managed_variations_from_lines(
    lines: list[str], *, overwrite_pcb_footprint: bool = True
) -> list[dict[str, object]]:
    variations: list[dict[str, object]] = []
    current_variation: dict[str, object] | None = None
    for index, line in enumerate(lines):
        is_boundary, parsed_variation = _managed_variation_line(
            line, overwrite_pcb_footprint=overwrite_pcb_footprint
        )
        if is_boundary:
            current_variation = parsed_variation
            if parsed_variation is not None:
                variations.append(parsed_variation)
            continue
        _attach_managed_line_parameter(lines, index, current_variation)
    return variations


def _managed_variation_line(
    line: str,
    *,
    overwrite_pcb_footprint: bool = True,
) -> tuple[bool, dict[str, object] | None]:
    if not _managed_field_starts_with(line, "Variation"):
        return False, None
    key, separator, raw = line.partition("=")
    if not separator or not _numbered_key(key, "Variation"):
        return True, None
    return True, _managed_variation_row(
        raw, overwrite_pcb_footprint=overwrite_pcb_footprint
    )


def _attach_managed_line_parameter(
    lines: list[str],
    index: int,
    variation: dict[str, object] | None,
) -> None:
    if variation is None or index + 1 >= len(lines):
        return
    key, separator, raw = lines[index].partition("=")
    if not separator or not _numbered_key(key, "ParamVariation"):
        return
    designator = str(variation["_parameter_designator"])
    if not dotnet_ordinal_ignore_case_key(lines[index + 1]).endswith(
        dotnet_ordinal_ignore_case_key(designator)
    ):
        return
    parsed = _managed_variation_parameter(raw)
    if parsed is not None:
        _set_managed_variation_parameter(variation, *parsed)


def _set_managed_variation_parameter(
    variation: dict[str, object],
    name: str,
    value: str,
) -> None:
    parameters = variation["_parameters"]
    if not isinstance(parameters, dict):
        return
    lookup_key = dotnet_ordinal_ignore_case_key(name)
    selected_name = next(
        (
            str(candidate)
            for candidate in parameters
            if dotnet_ordinal_ignore_case_key(str(candidate)) == lookup_key
        ),
        name,
    )
    parameters[selected_name] = value


def _attach_managed_variation_parameters(
    entries: list[tuple[str, str]],
    start: int,
    variation: dict[str, object],
) -> None:
    designator = str(variation["_parameter_designator"])
    parameters = variation["_parameters"]
    if not isinstance(parameters, dict):
        return
    parameter_names: dict[str, str] = {}
    for index in range(start, len(entries) - 1):
        key, raw = entries[index]
        if dotnet_ordinal_ignore_case_key(key).startswith("VARIATION"):
            break
        if not _numbered_key(key, "ParamVariation"):
            continue
        next_key, next_value = entries[index + 1]
        next_line = f"{next_key}={next_value}"
        if not dotnet_ordinal_ignore_case_key(next_line).endswith(
            dotnet_ordinal_ignore_case_key(designator)
        ):
            continue
        parsed = _managed_variation_parameter(raw)
        if parsed is not None:
            name, value = parsed
            lookup_key = dotnet_ordinal_ignore_case_key(name)
            selected_name = parameter_names.setdefault(lookup_key, name)
            parameters[selected_name] = value


def _numbered_key(value: str, prefix: str) -> bool:
    normalized = dotnet_ordinal_ignore_case_key(value)
    marker = dotnet_ordinal_ignore_case_key(prefix)
    suffix = normalized[len(marker) :]
    return (
        normalized.startswith(marker)
        and bool(suffix)
        and ord(suffix[0]) <= 0xFFFF
        and suffix[0].isdecimal()
    )


def _managed_variant_source_lines(
    source_lines: list[str],
    variant_name: str,
) -> list[str] | None:
    target = dotnet_ordinal_ignore_case_key(variant_name)
    for index, line in enumerate(source_lines):
        if not _managed_field_starts_with(line, "[ProjectVariant"):
            continue
        header = _managed_variant_header(source_lines, index)
        if header is None:
            continue
        description, description_index = header
        if dotnet_ordinal_ignore_case_key(description) != target:
            continue
        end = _managed_variant_region_end(source_lines, description_index + 1)
        return source_lines[description_index + 1 : end]
    return None


def _managed_variant_region_end(source_lines: list[str], start: int) -> int:
    for index in range(start, len(source_lines)):
        line = source_lines[index]
        if line.startswith("[") and not _managed_variant_parameter_header(line):
            return index
    return len(source_lines)


def _managed_variant_parameter_header(line: str) -> bool:
    return _managed_field_starts_with(line, "[Parameter") and "_" in line


def _managed_variant_parameters_from_lines(lines: list[str]) -> dict[str, str]:
    parameters: dict[str, str] = {}
    for index, line in enumerate(lines):
        if not _managed_variant_parameter_header(line) or index + 2 >= len(lines):
            continue
        name = _managed_field_value(lines[index + 1], "Name=")
        value = _managed_field_value(lines[index + 2], "Value=")
        if name is None or value is None:
            continue
        lookup = dotnet_ordinal_ignore_case_key(name)
        existing = next(
            (
                candidate
                for candidate in parameters
                if dotnet_ordinal_ignore_case_key(candidate) == lookup
            ),
            None,
        )
        normalized = value if dotnet_trim(value) else ""
        if existing is None:
            parameters[name] = normalized
        else:
            parameters[existing] = normalized
    return parameters


def _managed_variant_header(
    source_lines: list[str],
    header_index: int,
) -> tuple[str, int] | None:
    first_index = header_index + 1
    if first_index >= len(source_lines):
        return None
    description_index = first_index
    if _managed_field_starts_with(source_lines[first_index], "UniqueID="):
        description_index += 1
    if description_index >= len(source_lines):
        return None
    description = _managed_field_value(
        source_lines[description_index],
        "Description=",
    )
    if description is None:
        return None
    return description, description_index


def _managed_entries_description(entries: list[tuple[str, str]]) -> str | None:
    if not entries:
        return None
    description_index = int(
        dotnet_ordinal_ignore_case_key(entries[0][0])
        == dotnet_ordinal_ignore_case_key("UniqueID")
    )
    if description_index >= len(entries):
        return None
    key, description = entries[description_index]
    if dotnet_ordinal_ignore_case_key(key) != dotnet_ordinal_ignore_case_key(
        "Description"
    ):
        return None
    return description


def _managed_config_value(
    config: configparser.ConfigParser,
    section_name: str,
    option_name: str,
) -> str | None:
    section_key = dotnet_ordinal_ignore_case_key(section_name)
    option_key = dotnet_ordinal_ignore_case_key(option_name)
    selected: str | None = None
    for section in config.sections():
        if dotnet_ordinal_ignore_case_key(section) != section_key:
            continue
        entries = _raw_config_section_entries(config, section) or []
        for key, value in entries:
            if dotnet_ordinal_ignore_case_key(key) == option_key:
                selected = value
        break
    return selected


def _selected_variant_section(
    config: configparser.ConfigParser,
    variant_name: str,
) -> str | None:
    target = dotnet_ordinal_ignore_case_key(variant_name)
    for section in config.sections():
        if not _managed_field_starts_with(section, "ProjectVariant"):
            continue
        entries = _raw_config_section_entries(config, section)
        description = (
            _managed_entries_description(entries) if entries is not None else None
        )
        if (
            description is not None
            and dotnet_ordinal_ignore_case_key(description) == target
        ):
            return section
    return None


def _raw_config_section_entries(
    config: configparser.ConfigParser,
    section: str,
) -> list[tuple[str, str]] | None:
    raw_sections = vars(config).get("_sections")
    if not isinstance(raw_sections, dict):
        return None
    raw_section = raw_sections.get(section)
    if not isinstance(raw_section, dict):
        return None
    return [
        (str(key), str(value))
        for key, value in raw_section.items()
        if key != "__name__" and value is not None
    ]


def _read_nonnegative_variant_count(
    config: configparser.ConfigParser,
    section: str,
    option: str,
) -> int:
    count = config.getint(section, option, fallback=0)
    if count < 0:
        raise ValueError(f"{section}.{option} must be a nonnegative integer")
    return count


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


class ChannelRoomNamingStyle(IntEnum):
    """
    Multichannel room naming style for Altium projects.

    Mirrors Altium's channel room naming style options. Stored as
    ChannelRoomNamingStyle in the [Design] section of .PrjPcb files and
    controls how compiled physical-document room names are suffixed
    (flat rank suffix vs hierarchical name path, numeric vs alpha rank).
    """

    FLAT_NUMERIC_WITH_NAMES = 0  # eChannelRoomNamingStyle_FlatNumericWithNames
    FLAT_ALPHA_WITH_NAMES = 1  # eChannelRoomNamingStyle_FlatAlphaWithNames
    NUMERIC_NAME_PATH = 2  # eChannelRoomNamingStyle_NumericNamePath
    ALPHA_NAME_PATH = 3  # eChannelRoomNamingStyle_AlphaNamePath
    MIXED_NAME_PATH = 4  # eChannelRoomNamingStyle_MixedNamePath


def _parse_altium_bool(value: object, *, default: bool = False) -> bool:
    """
    Parse an Altium boolean value from INI text.

    Altium project files commonly use `1`/`0`, but some project-adjacent
    formats use `TRUE`/`FALSE`. Unknown values return `default`.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    return default


def _format_altium_bool(value: bool) -> str:
    """
    Format a project-file boolean using Altium's common `1`/`0` convention.
    """
    return "1" if bool(value) else "0"


def _option_value(
    options: list[DocumentOption],
    key: str,
    *,
    fallback: str | None = None,
) -> str | None:
    target = key.lower()
    for option_key, value in options:
        if option_key.lower() == target:
            return value
    return fallback


def _set_document_option(
    document: DocumentEntry,
    key: str,
    value: str,
) -> None:
    options = list(document.get("options", []))
    target = key.lower()
    for index, (option_key, _) in enumerate(options):
        if option_key.lower() == target:
            options[index] = (option_key, value)
            break
    else:
        options.append((key, value))
    document["options"] = options


@public_api
@dataclass(frozen=True, slots=True)
class AltiumPrjPcbClassGenerationOptions:
    """
    Project-wide class-generation policy from `.PrjPcb` `[PrjClassGen]`.

    These options control which schematic/user-defined classes Altium offers
    or pushes during compile/ECO workflows. They do not themselves create PCB
    classes; they preserve the policy that lets Altium transfer directive data
    such as schematic `ClassName` and `DifferentialPairClassName` parameters
    into PcbDoc `Classes6/Data`.

    Attributes map directly to Altium project keys:
    - `comp_class_manual_enabled`: `CompClassManualEnabled`
    - `comp_class_manual_room_enabled`: `CompClassManualRoomEnabled`
    - `net_class_auto_bus_enabled`: `NetClassAutoBusEnabled`
    - `net_class_auto_comp_enabled`: `NetClassAutoCompEnabled`
    - `net_class_auto_named_harness_enabled`: `NetClassAutoNamedHarnessEnabled`
    - `net_class_manual_enabled`: `NetClassManualEnabled`
    - `net_class_separate_for_bus_sections`: `NetClassSeparateForBusSections`
    """

    comp_class_manual_enabled: bool = False
    comp_class_manual_room_enabled: bool = False
    net_class_auto_bus_enabled: bool = True
    net_class_auto_comp_enabled: bool = False
    net_class_auto_named_harness_enabled: bool = False
    net_class_manual_enabled: bool = False
    net_class_separate_for_bus_sections: bool = False

    @classmethod
    def from_config(
        cls,
        config: configparser.ConfigParser,
    ) -> "AltiumPrjPcbClassGenerationOptions":
        """
        Build options from a project config, using observed Altium defaults when
        `[PrjClassGen]` is absent.
        """
        section = "PrjClassGen"
        return cls(
            comp_class_manual_enabled=config.getboolean(
                section, "CompClassManualEnabled", fallback=False
            ),
            comp_class_manual_room_enabled=config.getboolean(
                section, "CompClassManualRoomEnabled", fallback=False
            ),
            net_class_auto_bus_enabled=config.getboolean(
                section, "NetClassAutoBusEnabled", fallback=True
            ),
            net_class_auto_comp_enabled=config.getboolean(
                section, "NetClassAutoCompEnabled", fallback=False
            ),
            net_class_auto_named_harness_enabled=config.getboolean(
                section, "NetClassAutoNamedHarnessEnabled", fallback=False
            ),
            net_class_manual_enabled=config.getboolean(
                section, "NetClassManualEnabled", fallback=False
            ),
            net_class_separate_for_bus_sections=config.getboolean(
                section, "NetClassSeparateForBusSections", fallback=False
            ),
        )

    def write_to_config(self, config: configparser.ConfigParser) -> None:
        """
        Write this policy to `[PrjClassGen]`.
        """
        section = "PrjClassGen"
        if not config.has_section(section):
            config.add_section(section)
        config.set(
            section,
            "CompClassManualEnabled",
            _format_altium_bool(self.comp_class_manual_enabled),
        )
        config.set(
            section,
            "CompClassManualRoomEnabled",
            _format_altium_bool(self.comp_class_manual_room_enabled),
        )
        config.set(
            section,
            "NetClassAutoBusEnabled",
            _format_altium_bool(self.net_class_auto_bus_enabled),
        )
        config.set(
            section,
            "NetClassAutoCompEnabled",
            _format_altium_bool(self.net_class_auto_comp_enabled),
        )
        config.set(
            section,
            "NetClassAutoNamedHarnessEnabled",
            _format_altium_bool(self.net_class_auto_named_harness_enabled),
        )
        config.set(
            section,
            "NetClassManualEnabled",
            _format_altium_bool(self.net_class_manual_enabled),
        )
        config.set(
            section,
            "NetClassSeparateForBusSections",
            _format_altium_bool(self.net_class_separate_for_bus_sections),
        )


@public_api
@dataclass(frozen=True, slots=True)
class AltiumPrjPcbDocumentClassGenerationOptions:
    """
    Per-document class-generation options from a `.PrjPcb` `DocumentN` section.

    These options are carried on individual source/library documents and control
    document-scoped class generation behavior during Altium compile/ECO flows.
    They are preserved as document options on save.

    Attributes map directly to Altium document keys:
    - `component_class_auto_enabled`: `ClassGenCCAutoEnabled`
    - `component_class_auto_room_enabled`: `ClassGenCCAutoRoomEnabled`
    - `net_class_auto_scope`: `ClassGenNCAutoScope`
    - `generate_class_cluster`: `GenerateClassCluster`
    """

    component_class_auto_enabled: bool = True
    component_class_auto_room_enabled: bool = False
    net_class_auto_scope: str = "None"
    generate_class_cluster: bool = False

    @classmethod
    def from_document_options(
        cls,
        options: list[DocumentOption],
    ) -> "AltiumPrjPcbDocumentClassGenerationOptions":
        """
        Build options from one `DocumentN` option list.
        """
        return cls(
            component_class_auto_enabled=_parse_altium_bool(
                _option_value(options, "ClassGenCCAutoEnabled", fallback="1"),
                default=True,
            ),
            component_class_auto_room_enabled=_parse_altium_bool(
                _option_value(options, "ClassGenCCAutoRoomEnabled", fallback="0"),
                default=False,
            ),
            net_class_auto_scope=str(
                _option_value(options, "ClassGenNCAutoScope", fallback="None") or "None"
            ),
            generate_class_cluster=_parse_altium_bool(
                _option_value(options, "GenerateClassCluster", fallback="0"),
                default=False,
            ),
        )

    def write_to_document(self, document: DocumentEntry) -> None:
        """
        Write this policy into one project document entry.
        """
        _set_document_option(
            document,
            "ClassGenCCAutoEnabled",
            _format_altium_bool(self.component_class_auto_enabled),
        )
        _set_document_option(
            document,
            "ClassGenCCAutoRoomEnabled",
            _format_altium_bool(self.component_class_auto_room_enabled),
        )
        _set_document_option(document, "ClassGenNCAutoScope", self.net_class_auto_scope)
        _set_document_option(
            document,
            "GenerateClassCluster",
            _format_altium_bool(self.generate_class_cluster),
        )


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


def _normalize_project_document_identity(path: str | Path) -> str:
    """Normalize one nonempty relative project identity without losing subpaths."""
    try:
        normalized = _normalize_logical_project_identity(str(path))
    except ValueError as exc:
        raise ValueError(f"invalid project document path {str(path)!r}: {exc}") from exc
    return normalized.replace("/", "\\")


def _altium_path_name(path: str | Path) -> str:
    """
    Return the final name from an Altium project path on any host platform.

    `.PrjPcb` paths use Windows separators even when parsed on macOS/Linux, so
    `Path(...).name` is not sufficient for project-relative paths.
    """
    return _altium_path_for_host(path).name


def _altium_path_for_host(path: str | Path) -> Path:
    """Convert Altium separators before using a project path on this host."""
    return Path(str(path).replace("\\", "/"))


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
        kill_after_run: bool = False,
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
            kill_after_run: Require no pre-existing X2.exe processes, then
                force-terminate every X2.exe process after collecting the run
                marker/log. Useful for unattended automation when project
                compile can leave the project dirty in memory and interactive
                close prompts must be avoided. Without this option, opened
                documents may remain in the Altium session.

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
            kill_after_run=kill_after_run,
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
        self._document_source_sections: dict[int, str] = {}
        self._loaded_encoding: str | None = None
        self._source_lines: list[str] = []

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
        self._source_lines = text.splitlines()
        self.config.read_string(text, source=str(filepath))
        self._extract_documents()

    def _extract_documents(self) -> None:
        """
        Extract document list from config (including full DocumentN options).

        Managed device sheets can be stored in `[DeviceSheetN]` sections rather
        than contiguous `[DocumentN]` sections. They are durable schematic
        references and must participate in project-level compile loading.
        """
        self.documents = []
        self._document_source_sections = {}
        identities: set[str] = set()
        doc_num = 1

        while True:
            section = f"Document{doc_num}"
            if not self.config.has_section(section):
                break

            doc_path = self.config.get(section, "DocumentPath", fallback="")
            doc_id = self.config.get(section, "DocumentUniqueId", fallback="")
            options = [(key, value) for key, value in self.config.items(section)]
            normalized = _normalize_project_document_identity(doc_path)
            identity = _logical_source_identity_key(normalized)
            if identity in identities:
                raise ValueError(f"duplicate project document path: {doc_path}")
            identities.add(identity)

            self.documents.append(
                {
                    "path": doc_path,
                    "unique_id": doc_id,
                    "options": options,
                }
            )

            doc_num += 1

        def _device_sheet_index(section: str) -> int:
            match = re.search(r"\d+", section)
            return int(match.group(0)) if match else 0

        device_sheet_sections = sorted(
            (
                section
                for section in self.config.sections()
                if re.fullmatch(r"DeviceSheet\d+", section, re.IGNORECASE)
            ),
            key=_device_sheet_index,
        )
        for section in device_sheet_sections:
            doc_path = self.config.get(section, "DocumentPath", fallback="")
            if not doc_path:
                continue
            normalized = _normalize_project_document_identity(doc_path)
            identity = _logical_source_identity_key(normalized)
            if identity in identities:
                raise ValueError(f"duplicate project document path: {doc_path}")
            identities.add(identity)
            options = [(key, value) for key, value in self.config.items(section)]
            document: DocumentEntry = {
                "path": doc_path,
                "unique_id": self.config.get(
                    section,
                    "DocumentUniqueId",
                    fallback="",
                ),
                "options": options,
            }
            self.documents.append(document)
            self._document_source_sections[id(document)] = section

    def add_document(self, path: str | Path, unique_id: str | None = None) -> None:
        """
        Add a document to the project.

        Args:
            path: Document path (relative to project).
                  Use backslashes for Windows compatibility.
            unique_id: Optional unique ID (generated if not provided)
        """
        path_str = _normalize_project_document_identity(path)
        identity = _logical_source_identity_key(path_str)
        for document in self.documents:
            existing = _normalize_project_document_identity(document["path"])
            if _logical_source_identity_key(existing) == identity:
                raise ValueError(f"duplicate project document path: {path_str}")

        if unique_id is None:
            # Generate a unique ID similar to Altium's format (8 uppercase chars)
            unique_id = str(uuid.uuid4()).replace("-", "").upper()[:8]

        document: DocumentEntry = {
            "path": path_str,
            "unique_id": unique_id,
            "options": [
                ("DocumentPath", path_str),
                ("DocumentUniqueId", unique_id),
            ],
        }
        self.documents.append(document)

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
            pattern: Path.glob pattern for files to include

        Matching directories are ignored. Results use a deterministic ASCII-
        lowercase filename order with exact spelling as the tie-breaker. The
        replacement is atomic and retains managed DeviceSheet sections.
        """
        directory = Path(directory)
        files = sorted(
            (path for path in directory.glob(pattern) if path.is_file()),
            key=lambda path: (
                path.name.translate(_ASCII_LOWER_TRANSLATION),
                path.name,
            ),
        )
        retained_devices = [
            document
            for document in self.documents
            if self._document_source_sections.get(id(document), "")
            .lower()
            .startswith("devicesheet")
        ]
        identities = {
            _logical_source_identity_key(
                _normalize_project_document_identity(document["path"])
            )
            for document in retained_devices
        }
        replacements: list[DocumentEntry] = []
        for file in files:
            path = _normalize_project_document_identity(file.name)
            identity = _logical_source_identity_key(path)
            if identity in identities:
                raise ValueError(f"duplicate project document path: {path}")
            identities.add(identity)
            unique_id = uuid.uuid4().hex.upper()[:8]
            replacements.append(
                {
                    "path": path,
                    "unique_id": unique_id,
                    "options": [
                        ("DocumentPath", path),
                        ("DocumentUniqueId", unique_id),
                    ],
                }
            )

        device_sections = {
            id(document): self._document_source_sections[id(document)]
            for document in retained_devices
        }
        doc_num = 1
        while self.config.remove_section(f"Document{doc_num}"):
            doc_num += 1
        self.documents = [*replacements, *retained_devices]
        self._document_source_sections = device_sections

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
        writable_documents = (
            document
            for document in self.documents
            if not self._document_source_sections.get(id(document), "")
            .lower()
            .startswith("devicesheet")
        )
        for idx, doc in enumerate(writable_documents, start=1):
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
        appended as additional numbered `[ParameterN]` sections. All names are
        validated before mutation so an invalid later row cannot leave a
        partially updated project.
        """
        for name in parameters:
            if not name.strip():
                raise ValueError("project parameter name must not be blank")
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
            variation_count = _read_nonnegative_variant_count(
                self.config, section, "VariationCount"
            )
            parameters = []
            parameter_count = _read_nonnegative_variant_count(
                self.config, section, "ParameterCount"
            )
            param_variations = []
            param_variation_count = _read_nonnegative_variant_count(
                self.config, section, "ParamVariationCount"
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
                "overwrite_schematic_symbol": _parse_altium_bool(
                    self.config.get(
                        section,
                        "OverwriteSchematicSymbol",
                        fallback="True",
                    ),
                    default=True,
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
                if _variant_row_value(v, "Kind") == "1":
                    dnp_list.append(_variant_row_value(v, "Designator") or "")
            variants[description]["DNP"] = dnp_list

        return variants

    def _managed_variant_data(self, variant_name: str) -> dict[str, object] | None:
        """Return the AD26 compiler projection for one named project variant."""
        source_lines = _managed_variant_source_lines(self._source_lines, variant_name)
        if source_lines is not None:
            overwrite = next(
                (
                    value
                    for line in reversed(source_lines)
                    if (
                        value := _managed_field_value(line, "OverwriteSchematicSymbol=")
                    )
                    is not None
                ),
                "True",
            )
            overwrite_pcb = next(
                (
                    value
                    for line in reversed(source_lines[:2])
                    if (value := _managed_field_value(line, "OverwritePCBFootprint="))
                    is not None
                ),
                "True",
            )
            overwrite_pcb_footprint = _parse_altium_bool(overwrite_pcb, default=True)
            return {
                "variations": _managed_variations_from_lines(
                    source_lines,
                    overwrite_pcb_footprint=overwrite_pcb_footprint,
                ),
                "overwrite_schematic_symbol": _parse_altium_bool(
                    overwrite, default=True
                ),
                "overwrite_pcb_footprint": overwrite_pcb_footprint,
                "parameters": _managed_variant_parameters_from_lines(source_lines),
            }
        selected_section = _selected_variant_section(self.config, variant_name)
        if selected_section is None:
            return None
        entries = _raw_config_section_entries(self.config, selected_section)
        if entries is None:
            return None
        overwrite = _managed_config_value(
            self.config, selected_section, "OverwriteSchematicSymbol"
        )
        overwrite_pcb_footprint = _parse_altium_bool(
            _managed_config_value(
                self.config, selected_section, "OverwritePCBFootprint"
            ),
            default=True,
        )
        return {
            "variations": _managed_variations_from_entries(
                entries,
                overwrite_pcb_footprint=overwrite_pcb_footprint,
            ),
            "overwrite_schematic_symbol": _parse_altium_bool(overwrite, default=True),
            "overwrite_pcb_footprint": overwrite_pcb_footprint,
            "parameters": {},
        }

    def _managed_current_variant(self) -> str | None:
        """Return the AD26 compiler current-variant spelling."""
        selected: str | None = None
        for line in self._source_lines:
            value = _managed_field_value(line, "CurrentVariant=")
            if value is not None:
                selected = value
        if selected is not None:
            return selected or None
        fallback = _managed_config_value(self.config, "Design", "CurrentVariant")
        return fallback or None

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
        filepath = self.filepath
        if filepath is None:
            raise ValueError("Cannot get SchDoc paths: project has no filepath context")

        all_paths = self.get_schdoc_paths()
        if not all_paths:
            return []

        path_by_project_path = {
            str(path).replace("\\", "/").casefold(): path for path in all_paths
        }
        project_dir = filepath.parent
        active_paths: list[Path] = []
        for document in self.documents:
            doc_path = str(document.get("path", ""))
            host_path = _altium_path_for_host(doc_path)
            if host_path.suffix.lower() != ".schdoc":
                continue

            option_keys = {key for key, _ in document["options"]}
            extra_keys = option_keys - {"DocumentPath", "DocumentUniqueId"}
            if not extra_keys:
                continue

            resolved_path = (project_dir / host_path).resolve()
            path_key = str(resolved_path).replace("\\", "/").casefold()
            full_path = path_by_project_path.get(path_key)
            if full_path is not None:
                active_paths.append(full_path)

        return active_paths or all_paths

    def _saved_structure_schdoc_names(self) -> tuple[str | None, tuple[str, ...]]:
        if not self.filepath:
            return None, ()
        structure_path = self.filepath.with_suffix(".PrjPcbStructure")
        try:
            lines = structure_path.read_text(
                encoding="utf-8-sig",
                errors="replace",
            ).splitlines()
        except OSError:
            return None, ()

        top_level: str | None = None
        identities: list[str] = []
        seen: set[str] = set()
        for line in lines:
            fields: dict[str, str] = {}
            for part in line.split("|"):
                if "=" not in part:
                    continue
                key, value = part.split("=", 1)
                fields[key.strip().lower()] = value.strip()

            record = fields.get("record", "").lower()
            if record == "topleveldocument":
                raw_top = fields.get("filename", "")
                if raw_top:
                    try:
                        normalized_top = _normalize_logical_project_identity(raw_top)
                    except ValueError:
                        normalized_top = ""
                    if normalized_top:
                        top_level = _logical_source_basename(normalized_top)
            for key in ("sourcedocument", "filename"):
                value = fields.get(key, "")
                if not value.lower().endswith(".schdoc"):
                    continue
                try:
                    normalized = _normalize_logical_project_identity(value)
                except ValueError:
                    continue
                identity = _logical_source_identity_key(normalized)
                if identity not in seen:
                    seen.add(identity)
                    identities.append(normalized)
        return top_level, tuple(identities)

    def get_saved_structure_top_level_schdoc_name(self) -> str | None:
        """Get the saved `.PrjPcbStructure` top-level SchDoc file name."""
        top_level, _names = self._saved_structure_schdoc_names()
        return top_level

    def get_saved_structure_schdoc_paths(self) -> list[Path]:
        """
        Get SchDoc paths named by the saved `.PrjPcbStructure` file.

        The returned paths are resolved from the project document list so stale
        structure-only names do not introduce files outside the project.
        """
        _top_level, names = self._saved_structure_schdoc_names()
        if not names:
            return []
        filepath = self.filepath
        if filepath is None:
            return []
        exact: dict[str, Path] = {}
        by_name: dict[str, list[Path]] = {}
        for document in self.documents:
            raw = str(document.get("path", ""))
            try:
                normalized = _normalize_logical_project_identity(raw)
            except ValueError:
                continue
            if not normalized.lower().endswith(".schdoc"):
                continue
            path = (filepath.parent / _altium_path_for_host(raw)).resolve()
            if not path.exists():
                continue
            exact[_logical_source_identity_key(normalized)] = path
            name_key = _logical_source_identity_key(
                _logical_source_basename(normalized)
            )
            by_name.setdefault(name_key, []).append(path)

        resolved: list[Path] = []
        for identity in names:
            match = exact.get(_logical_source_identity_key(identity))
            if match is None:
                candidates = by_name.get(
                    _logical_source_identity_key(_logical_source_basename(identity)), []
                )
                if len(candidates) == 1:
                    match = candidates[0]
                elif len(candidates) > 1:
                    raise ValueError(f"ambiguous saved schematic basename: {identity}")
            if match is not None:
                resolved.append(match)
        return resolved

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
            full_path = (project_dir / _altium_path_for_host(doc_path)).resolve()
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
    def class_generation_options(self) -> AltiumPrjPcbClassGenerationOptions:
        """
        Get project-wide class-generation policy from `[PrjClassGen]`.

        This policy controls whether Altium transfers schematic/user-defined
        classes into PCB classes during compile/ECO workflows. For differential
        pair fixtures, `net_class_manual_enabled=True` is the project switch
        that lets schematic directive `ClassName` and
        `DifferentialPairClassName` values become PCB `Classes6/Data` records.
        """
        return AltiumPrjPcbClassGenerationOptions.from_config(self.config)

    def set_class_generation_options(
        self,
        options: AltiumPrjPcbClassGenerationOptions,
    ) -> None:
        """
        Set project-wide class-generation policy in `[PrjClassGen]`.

        Args:
            options: Complete class-generation policy to write.
        """
        options.write_to_config(self.config)

    @property
    def document_class_generation_options(
        self,
    ) -> dict[str, AltiumPrjPcbDocumentClassGenerationOptions]:
        """
        Get class-generation options for each project document by document path.

        The returned keys are the project-relative `DocumentPath` strings stored
        in the `.PrjPcb`.
        """
        return {
            document[
                "path"
            ]: AltiumPrjPcbDocumentClassGenerationOptions.from_document_options(
                document["options"]
            )
            for document in self.documents
        }

    def _resolve_document_index(self, document: int | str | Path) -> int:
        """
        Resolve a document index from either a zero-based index or document path.
        """
        if isinstance(document, int):
            if document < 0 or document >= len(self.documents):
                raise IndexError(
                    f"Document index {document} out of range for {len(self.documents)} documents"
                )
            return document

        requested = _normalize_project_document_identity(document)
        requested_key = _logical_source_identity_key(requested)
        requested_name = _logical_source_identity_key(_altium_path_name(requested))
        basename_matches: list[int] = []
        for index, entry in enumerate(self.documents):
            entry_path = _normalize_project_document_identity(
                str(entry.get("path", ""))
            )
            if _logical_source_identity_key(entry_path) == requested_key:
                return index
            if (
                _logical_source_identity_key(_altium_path_name(entry_path))
                == requested_name
            ):
                basename_matches.append(index)
        if len(basename_matches) == 1:
            return basename_matches[0]
        if len(basename_matches) > 1:
            raise KeyError(f"Ambiguous project document name: {document}")
        raise KeyError(f"Project document not found: {document}")

    def get_document_class_generation_options(
        self,
        document: int | str | Path,
    ) -> AltiumPrjPcbDocumentClassGenerationOptions:
        """
        Get class-generation options for one project document.

        Args:
            document: Zero-based document index, project-relative path, or
                filename.
        """
        entry = self.documents[self._resolve_document_index(document)]
        return AltiumPrjPcbDocumentClassGenerationOptions.from_document_options(
            entry["options"]
        )

    def set_document_class_generation_options(
        self,
        document: int | str | Path,
        options: AltiumPrjPcbDocumentClassGenerationOptions,
    ) -> None:
        """
        Set class-generation options for one project document.

        Args:
            document: Zero-based document index, project-relative path, or
                filename.
            options: Complete document-level class-generation policy to write.
        """
        entry = self.documents[self._resolve_document_index(document)]
        options.write_to_document(entry)

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
