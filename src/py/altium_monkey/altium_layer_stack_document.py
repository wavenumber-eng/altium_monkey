"""
Source-aware PcbDoc layer-stack document model.

This module is the first read-only slice of the layer-stack OOP model. It keeps
native Altium source evidence visible while exposing deterministic Python
objects for tests and future consumers.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
import re
import uuid
import zlib

from .altium_pcb_stream_helpers import format_bool_text as _format_bool_text
from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_pcb_stream_helpers import (
    parse_altium_bool_token as _parse_altium_bool_token,
)
from .altium_pcb_stream_helpers import parse_altium_int_token as _parse_int_token
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_pcbdoc_builder import (
        PcbDocBoardData,
        PcbDocBoardDataEntry,
        PcbDocBoardDataSegment,
        PcbDocBoardRegionsData,
    )
    from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackTemplate
    from .altium_record_pcb__board_region import AltiumPcbBoardRegion
    from .altium_stackup import AltiumStackupDocument
    from .altium_stackupx import AltiumStackupXDocument

_V9_STACK_RE = re.compile(r"^V9_STACK_LAYER(\d+)_(.+)$", re.IGNORECASE)
_V9_CACHE_RE = re.compile(r"^V9_CACHE_LAYER(\d+)_(.+)$", re.IGNORECASE)
_LAYER_V8_RE = re.compile(r"^LAYER_V8_(\d+)_?(.+)$", re.IGNORECASE)
_LEGACY_LAYER_RE = re.compile(r"^LAYER(\d+)(.+)$", re.IGNORECASE)
_LENGTH_VALUE_RE = re.compile(
    r"^\s*(?P<number>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?P<unit>mil|mm|um|m)?\s*$",
    re.IGNORECASE,
)
_SURFACE_FINISH_TYPE_ID = "B0827674-798C-4CF8-807C-8E6C2A11C145"
_SURFACE_FINISH_LAYER_ID = 16973824
_PLATING_TYPE_ID = "90B89AA0-A48A-45F4-82F5-B3ECA4EC8CCE"
_ADHESIVE_TYPE_ID = "448F9952-79BA-41D8-A8F4-4713EE7A3828"
_STIFFENER_TYPE_ID = "9FD889FA-C97A-401C-A066-E5F746678381"
_MISC_TYPE_ID = "786B5F28-F093-4084-BBA7-46E8F4F24F55"
_MARKING_TYPE_ID = "886956F5-B2E9-4114-93F3-F69AF872BFFB"
_LAYER_SCHEMA_TYPE_IDS = {
    "abstract": "5EE2359D-AD77-4DAD-BD77-5A2334695235",
    "unknown": "4AF561F4-6919-43D8-942A-D3579B371C26",
    "physical": "DAA9ECCC-0064-462D-854E-0B9E829EF563",
    "base_dielectric": "56EB1CA5-014A-4387-A43E-1485F44F081E",
    "overlay": "C7EF040E-8D00-490B-B00C-A7E7823FF174",
    "solder_mask": "7B384237-13D8-4318-8BCB-ACCD8D9A51E7",
    "bikini_coverlay": "8DE65238-89ED-47C5-BBF8-0F6F02D1899C",
    "paste_mask": "E6A36257-B90F-45E5-9F8D-A213ECDB687C",
    "mechanical": "7A47E6B3-B591-41F7-A27B-8C6B0477E458",
    "copper_foil": "31E48829-E750-4C28-95E0-1A8313F0158E",
    "copper_signal": "F4ECCD87-2CFB-4F37-BE50-4F3A272B4D01",
    "copper_plane": "F59FAB94-C5ED-467D-94CD-F60A323C5D5B",
    "dielectric": "92B02D5E-8D69-48A8-880E-AC4B77DB099D",
    "core": "136C62EF-1FA6-4897-AE71-7E797B632B92",
    "prepreg": "1A79611A-039D-4D40-A204-53C26C50F8B5",
    "surface_finish": _SURFACE_FINISH_TYPE_ID,
    "plating": _PLATING_TYPE_ID,
    "adhesive": _ADHESIVE_TYPE_ID,
    "stiffener": _STIFFENER_TYPE_ID,
    "misc": _MISC_TYPE_ID,
    "marking": _MARKING_TYPE_ID,
    "pe_layer": "8053A284-8886-43E8-BAA0-5AA1D8DE97D1",
    "pe_conductive": "C558FE3F-D914-4761-9D82-A12591CEB133",
    "pe_nonconductive": "18BE3C27-BFEB-4BBA-8E9A-DF4CAEEE1731",
    "pe_isolation": "8590CC63-42D8-48CB-8307-72991910BCC9",
    "pe_core": "E9A93AD8-17EE-438C-A1B6-92A3995DFDA8",
    "pe_prepreg": "743DE2F9-2902-4E35-B39C-BACD385F87DD",
}
_ADVANCED_LAYER_FLAGS = {
    "surface_finish": "ISSURFACEFINISH",
    "plating": "ISPLATING",
    "misc": "ISMISC",
    "marking": "ISMARK",
}
_ADVANCED_LAYER_FAMILIES = frozenset(_ADVANCED_LAYER_FLAGS)
_STACKUPX_PROPERTY_TYPES = {
    "PROCESS": "EntityReference",
    "MATERIAL": "String",
    "MATERIAL.COLOR": (
        "System.Windows.Media.Color, PresentationCore, Version=4.0.0.0, "
        "Culture=neutral, PublicKeyToken=31bf3856ad364e35"
    ),
    "THICKNESS": "LengthValue",
    "DIELECTRICCONSTANT": "DimensionlessValue",
    "LOSS TANGENT": "DimensionlessValue",
    "LOSSTANGENT": "DimensionlessValue",
    "COVERLAYEXPANSION": "LengthValue",
    "WEIGHT": "MassOunceValue",
}
_STACKUPX_PROPERTY_NAMES = {
    "PROCESS": "Process",
    "MATERIAL": "Material",
    "MATERIAL.COLOR": "Material.Color",
    "THICKNESS": "Thickness",
    "DIELECTRICCONSTANT": "DielectricConstant",
    "LOSSTANGENT": "LossTangent",
    "COVERLAYEXPANSION": "CoverlayExpansion",
    "WEIGHT": "Weight",
}

_STACK_ENTRY_KEY_RE = re.compile(
    r"^(?:"
    r"V9_MASTERSTACK_|"
    r"V9_SUBSTACK\d+_|"
    r"V9_STACK_LAYER\d+_|"
    r"V9_STACKCUSTOMDATA|"
    r"V9_CACHE_LAYER\d+_|"
    r"V9_IMPEDANCEPROFILE\d+_|"
    r"V9_TRACEIMPEDANCE\d+_|"
    r"LAYERMASTERSTACK_V8|"
    r"LAYERSUBSTACK_V8_|"
    r"LAYER_V8_|"
    r"VIASPAN_V8_|"
    r"DRILLPAIR_V8_|"
    r"DRILLSPAN_V8_|"
    r"IMPEDANCEPROFILE_V8_|"
    r"TRACEIMPEDANCE_V8_|"
    r"LAYERPAIR\d+|"
    r"RIGIDFLEXADVANCED|"
    r"LAYER\d+"
    r")",
    re.IGNORECASE,
)
_V8_SPAN_RE = re.compile(
    r"^(VIASPAN|DRILLPAIR|DRILLSPAN)_V8_(\{[^}]+\})_(\d+)(.+)$",
    re.IGNORECASE,
)
_IMPEDANCE_PROFILE_RE = re.compile(
    r"^IMPEDANCEPROFILE_V8_(\d+)(.+)$",
    re.IGNORECASE,
)
_TRACE_IMPEDANCE_RE = re.compile(
    r"^TRACEIMPEDANCE_V8_(\d+)(.+)$",
    re.IGNORECASE,
)
_BRANCH_V8_RE = re.compile(r"^BRANCH_V8_(\d+)(.+)$", re.IGNORECASE)
_BRANCHSECTION_V8_RE = re.compile(
    r"^BRANCHSECTION_V8_(\{[^}]+\})_(\d+)(.+)$",
    re.IGNORECASE,
)
_BRANCHSECTIONSTACK_V8_RE = re.compile(
    r"^BRANCHSECTIONSTACK_V8_(\{[^}]+\})_(\{[^}]+\})_(\d+)(.+)$",
    re.IGNORECASE,
)
_AUTHORING_NAMESPACE = uuid.UUID("be2608b0-1b1d-59ec-bcfe-0eb88e9d8684")


def is_layer_stack_source_entry_key(key: str | None) -> bool:
    """
    Return true for Board6/Data entries owned by the layer-stack source model.
    """
    return bool(key and _STACK_ENTRY_KEY_RE.match(str(key)))


def _text(value: object) -> str:
    return str(value or "").strip()


def _normalized_rigid_flex_mode(value: object) -> str:
    token = _text(value).lower()
    if token in {"advanced", "standard"}:
        return token
    return "none"


def _path_text(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("Layer stack interchange path is empty")
    return token


def _rigid_flex_mode_from_board_record(
    raw_record: Mapping[str, str],
    substacks: Sequence[object],
) -> str:
    flex_present = any(
        bool(getattr(substack, "is_flex", False)) for substack in substacks
    )
    if not flex_present:
        return "none"
    feature_names = _feature_names_from_record(raw_record)
    if any("RIGID/FLEX (ADVANCED)" in name for name in feature_names):
        return "advanced"
    if _parse_altium_bool_token(raw_record.get("RIGIDFLEXADVANCED")) is True:
        return "advanced"
    if any(name == "RIGID/FLEX" for name in feature_names):
        return "standard"
    if _parse_int_token(raw_record.get("SPLITLINECOUNT")):
        return "standard"
    return "standard"


def _feature_names_from_record(raw_record: Mapping[str, str]) -> tuple[str, ...]:
    names: list[str] = []
    index = 0
    while True:
        value = raw_record.get(f"FEATURE_{index}NAME")
        if value is None:
            break
        names.append(str(value).strip().upper())
        index += 1
    return tuple(names)


def _float_token(value: object) -> float | None:
    token = _text(value)
    if not token:
        return None
    if token.lower().endswith("mil"):
        token = token[:-3]
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def _parse_int_like_token(value: object) -> int | None:
    token = _text(value)
    if not token:
        return None
    try:
        return int(float(token))
    except (TypeError, ValueError):
        return None


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return format(float(value), "g")


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return ""
    return str(int(value))


def _radius_raw_to_mils(value: int | None) -> float | None:
    if value is None:
        return None
    return float(value) / 10000.0


def _legacy_layer_id_from_v7_save_id(saved_layer_id: int | None) -> int | None:
    if saved_layer_id in (None, 0):
        return None
    saved = int(saved_layer_id) & 0xFFFFFFFF
    if 0x01000001 <= saved <= 0x0100001F:
        return saved - 0x01000000
    if saved == 0x0100FFFF:
        return PcbLayer.BOTTOM.value
    if 0x01010001 <= saved <= 0x01010010:
        return PcbLayer.INTERNAL_PLANE_1.value + (saved - 0x01010001)
    if 0x01020001 <= saved <= 0x01020010:
        return PcbLayer.MECHANICAL_1.value + (saved - 0x01020001)
    misc_partitions = {
        6: PcbLayer.TOP_OVERLAY.value,
        7: PcbLayer.BOTTOM_OVERLAY.value,
        8: PcbLayer.TOP_PASTE.value,
        9: PcbLayer.BOTTOM_PASTE.value,
        10: PcbLayer.TOP_SOLDER.value,
        11: PcbLayer.BOTTOM_SOLDER.value,
        12: PcbLayer.DRILL_GUIDE.value,
        13: PcbLayer.KEEPOUT.value,
        14: PcbLayer.DRILL_DRAWING.value,
        15: PcbLayer.MULTI_LAYER.value,
        16: PcbLayer.CONNECT.value,
    }
    if (saved & 0xFFFF0000) == 0x01030000:
        return misc_partitions.get(saved & 0xFFFF)
    return None


def _standard_token_from_legacy(legacy_layer_id: int | None) -> str | None:
    if legacy_layer_id is None:
        return None
    if legacy_layer_id == PcbLayer.TOP.value:
        return "TOP"
    if legacy_layer_id == PcbLayer.BOTTOM.value:
        return "BOTTOM"
    if PcbLayer.TOP.value < legacy_layer_id < PcbLayer.BOTTOM.value:
        return f"MID{legacy_layer_id - PcbLayer.TOP.value}"
    if legacy_layer_id == PcbLayer.TOP_OVERLAY.value:
        return "TOPOVERLAY"
    if legacy_layer_id == PcbLayer.BOTTOM_OVERLAY.value:
        return "BOTTOMOVERLAY"
    if legacy_layer_id == PcbLayer.TOP_PASTE.value:
        return "TOPPASTE"
    if legacy_layer_id == PcbLayer.BOTTOM_PASTE.value:
        return "BOTTOMPASTE"
    if legacy_layer_id == PcbLayer.TOP_SOLDER.value:
        return "TOPSOLDER"
    if legacy_layer_id == PcbLayer.BOTTOM_SOLDER.value:
        return "BOTTOMSOLDER"
    if legacy_layer_id == PcbLayer.DRILL_GUIDE.value:
        return "DRILLGUIDE"
    if legacy_layer_id == PcbLayer.DRILL_DRAWING.value:
        return "DRILLDRAWING"
    if legacy_layer_id == PcbLayer.KEEPOUT.value:
        return "KEEPOUT"
    if legacy_layer_id == PcbLayer.MULTI_LAYER.value:
        return "MULTILAYER"
    if (
        PcbLayer.INTERNAL_PLANE_1.value
        <= legacy_layer_id
        <= PcbLayer.INTERNAL_PLANE_16.value
    ):
        return f"PLANE{legacy_layer_id - PcbLayer.INTERNAL_PLANE_1.value + 1}"
    if PcbLayer.MECHANICAL_1.value <= legacy_layer_id <= PcbLayer.MECHANICAL_16.value:
        return f"MECHANICAL{legacy_layer_id - PcbLayer.MECHANICAL_1.value + 1}"
    return None


def _layer_family(
    *,
    display_name: str,
    legacy_layer_id: int | None,
    copper_thickness_mils: float | None = None,
    dielectric_height_mils: float | None = None,
) -> str:
    name = display_name.upper()
    if legacy_layer_id is not None:
        try:
            layer = PcbLayer(legacy_layer_id)
        except ValueError:
            layer = None
        if layer is not None:
            if layer.is_copper():
                return "copper"
            if "SOLDER" in name:
                return "solder_mask"
            if "PASTE" in name:
                return "paste"
            if "OVERLAY" in name:
                return "overlay"
            if "MECHANICAL" in name:
                return "mechanical"
            if "DRILL" in name:
                return "drill"
            if layer == PcbLayer.KEEPOUT:
                return "keepout"
    if copper_thickness_mils and copper_thickness_mils > 0.0:
        return "copper"
    if dielectric_height_mils and dielectric_height_mils > 0.0:
        return "dielectric"
    if "SOLDER" in name:
        return "solder_mask"
    if "PASTE" in name:
        return "paste"
    if "OVERLAY" in name or "SILK" in name:
        return "overlay"
    if "DIELECTRIC" in name:
        return "dielectric"
    if "MECHANICAL" in name:
        return "mechanical"
    return "system"


def _advanced_layer_family(fields: Mapping[str, str]) -> str:
    for family, flag in _ADVANCED_LAYER_FLAGS.items():
        if _parse_altium_bool_token(fields.get(flag)) is True:
            return family
    type_id = _normalized_source_id(fields.get("TYPEID"))
    for family in _ADVANCED_LAYER_FAMILIES:
        schema_type_id = _LAYER_SCHEMA_TYPE_IDS[family]
        if type_id == schema_type_id:
            return family
    return ""


def _stackupx_properties_from_indexed_fields(
    fields: Mapping[str, str],
) -> tuple[tuple[str, str, str], ...]:
    properties: list[tuple[str, str, str]] = []
    for suffix, value in fields.items():
        normalized = suffix.upper()
        if "$LSM$" not in normalized:
            continue
        context_prefix, name_token = normalized.rsplit("$LSM$", 1)
        if context_prefix.strip("_"):
            continue
        if not name_token:
            continue
        name = _STACKUPX_PROPERTY_NAMES.get(
            name_token, _stackupx_property_name(name_token)
        )
        type_name = _STACKUPX_PROPERTY_TYPES.get(name_token, "")
        properties.append((name, type_name, _text(value)))
    properties.sort(key=lambda item: _stackupx_property_sort_key(item[0]))
    return tuple(properties)


def _stackupx_property_name(name_token: str) -> str:
    parts = [part for part in name_token.lower().replace("_", ".").split(".") if part]
    if not parts:
        return name_token
    return ".".join(part[:1].upper() + part[1:] for part in parts)


def _stackupx_property_sort_key(name: str) -> tuple[int, str]:
    order = {
        "Process": 0,
        "Material": 1,
        "Material.Color": 2,
        "Thickness": 3,
        "DielectricConstant": 4,
        "LossTangent": 5,
        "CoverlayExpansion": 6,
        "Weight": 7,
    }
    return (order.get(name, 100), name)


def _stackupx_property_value(
    properties: tuple[tuple[str, str, str], ...],
    name: str,
) -> str:
    for prop_name, _type_name, value in properties:
        if prop_name == name:
            return value
    return ""


def _stackupx_property_length_mils(
    properties: tuple[tuple[str, str, str], ...],
    name: str,
) -> float | None:
    value = _stackupx_property_value(properties, name)
    return _length_value_to_mils(value)


def _length_value_to_mils(value: object) -> float | None:
    match = _LENGTH_VALUE_RE.match(str(value or ""))
    if match is None:
        return _float_token(value)
    number = float(match.group("number"))
    unit = (match.group("unit") or "mil").lower()
    if unit == "mil":
        return number
    if unit == "mm":
        return number / 0.0254
    if unit == "um":
        return number / 25.4
    if unit == "m":
        return number / 0.0000254
    return number


def _layer_side(display_name: str, legacy_layer_id: int | None) -> str | None:
    name = display_name.upper()
    if legacy_layer_id == PcbLayer.TOP.value or name.startswith("TOP "):
        return "top"
    if legacy_layer_id == PcbLayer.BOTTOM.value or name.startswith("BOTTOM "):
        return "bottom"
    if "TOP" in name and "BOTTOM" not in name:
        return "top"
    if "BOTTOM" in name and "TOP" not in name:
        return "bottom"
    return None


def _model_id(
    *,
    v7_layer_id: int | None,
    legacy_layer_id: int | None,
    display_name: str,
    source_family: str,
    stack_index: int | None = None,
) -> str:
    if v7_layer_id:
        return f"v7:{int(v7_layer_id)}"
    if legacy_layer_id:
        return f"legacy:{int(legacy_layer_id)}"
    token = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_").upper()
    if token:
        return f"name:{token}"
    return f"{source_family}:{int(stack_index or 0)}"


def _group_indexed_fields(
    raw_record: Mapping[str, object],
    pattern: re.Pattern[str],
) -> dict[int, dict[str, str]]:
    groups: dict[int, dict[str, str]] = {}
    for key, value in raw_record.items():
        match = pattern.match(str(key or ""))
        if match is None:
            continue
        index = int(match.group(1))
        suffix = str(match.group(2) or "").upper()
        groups.setdefault(index, {})[suffix] = str(value)
    return groups


def _collect_contexts(fields: dict[str, str]) -> tuple[tuple[str, int | None], ...]:
    contexts: list[tuple[str, int | None]] = []
    for suffix, value in sorted(fields.items()):
        if not suffix.endswith("CONTEXT"):
            continue
        ref = suffix[: -len("CONTEXT")]
        if not ref:
            continue
        contexts.append((ref, _parse_int_token(value)))
    return tuple(contexts)


@dataclass(frozen=True)
class AltiumLayerRegistryEntry:
    """
    One source-aware logical layer entry.
    """

    model_id: str
    display_name: str
    family: str
    standard_token: str | None = None
    legacy_layer_id: int | None = None
    v7_layer_id: int | None = None
    source_layer_id: int | None = None
    source_record_id: str = ""
    side: str | None = None
    used_by_primitives: bool | None = None
    enabled: bool | None = None
    source_aliases: tuple[str, ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "display_name": self.display_name,
            "family": self.family,
            "standard_token": self.standard_token,
            "legacy_layer_id": self.legacy_layer_id,
            "v7_layer_id": self.v7_layer_id,
            "source_layer_id": self.source_layer_id,
            "source_record_id": self.source_record_id,
            "side": self.side,
            "used_by_primitives": self.used_by_primitives,
            "enabled": self.enabled,
            "source_aliases": list(self.source_aliases),
        }


@dataclass(frozen=True)
class AltiumLayerRegistry:
    """
    Deterministic registry of logical/editor layers.
    """

    entries: tuple[AltiumLayerRegistryEntry, ...] = ()

    def entry_by_model_id(self, model_id: str) -> AltiumLayerRegistryEntry | None:
        for entry in self.entries:
            if entry.model_id == model_id:
                return entry
        return None

    def to_debug_json(self) -> dict[str, object]:
        return {
            "entries": [entry.to_debug_json() for entry in self.entries],
            "entry_count": len(self.entries),
        }


@dataclass(frozen=True)
class AltiumStackLayer:
    """
    One ordered physical layer-stack row.
    """

    stack_index: int
    registry_ref: str
    display_name: str
    family: str
    source_family: str
    source_record_id: str = ""
    layer_id: int | None = None
    legacy_layer_id: int | None = None
    copper_thickness_mils: float | None = None
    dielectric_constant: float | None = None
    dielectric_loss_tangent: float | None = None
    dielectric_height_mils: float | None = None
    dielectric_material: str = ""
    dielectric_type: int | None = None
    component_placement: int | None = None
    used_by_primitives: bool | None = None
    coverlay_expansion: str = ""
    is_stiffener: bool | None = None
    is_adhesive: bool | None = None
    substack_contexts: tuple[tuple[str, int | None], ...] = ()
    source_keys: tuple[str, ...] = ()
    stackupx_properties: tuple[tuple[str, str, str], ...] = ()
    stackupx_substack_properties: tuple[
        tuple[str, tuple[tuple[str, str, str], ...]], ...
    ] = ()
    stackupx_is_shared: bool | None = None
    stackupx_substack_is_shared: tuple[tuple[str, bool], ...] = ()
    stackupx_substack_is_enabled: tuple[tuple[str, bool], ...] = ()

    def is_enabled_for_substack(self, source_stackup_ref: str) -> bool:
        ref = _normalized_source_id(source_stackup_ref)
        if not ref:
            return True
        found = False
        for context_ref, context_value in self.substack_contexts:
            if _normalized_source_id(context_ref) != ref:
                continue
            found = True
            if context_value not in (None, 0):
                return False
        return True if found else True

    def to_debug_json(self) -> dict[str, object]:
        return {
            "stack_index": self.stack_index,
            "registry_ref": self.registry_ref,
            "display_name": self.display_name,
            "family": self.family,
            "source_family": self.source_family,
            "source_record_id": self.source_record_id,
            "layer_id": self.layer_id,
            "legacy_layer_id": self.legacy_layer_id,
            "copper_thickness_mils": self.copper_thickness_mils,
            "dielectric_constant": self.dielectric_constant,
            "dielectric_loss_tangent": self.dielectric_loss_tangent,
            "dielectric_height_mils": self.dielectric_height_mils,
            "dielectric_material": self.dielectric_material,
            "dielectric_type": self.dielectric_type,
            "component_placement": self.component_placement,
            "used_by_primitives": self.used_by_primitives,
            "coverlay_expansion": self.coverlay_expansion,
            "is_stiffener": self.is_stiffener,
            "is_adhesive": self.is_adhesive,
            "substack_contexts": [list(item) for item in self.substack_contexts],
            "source_keys": list(self.source_keys),
            "stackupx_properties": [list(item) for item in self.stackupx_properties],
            "stackupx_substack_properties": [
                [ref, [list(prop) for prop in properties]]
                for ref, properties in self.stackupx_substack_properties
            ],
            "stackupx_is_shared": self.stackupx_is_shared,
            "stackupx_substack_is_shared": [
                list(item) for item in self.stackupx_substack_is_shared
            ],
            "stackupx_substack_is_enabled": [
                list(item) for item in self.stackupx_substack_is_enabled
            ],
        }


@dataclass(frozen=True)
class AltiumPhysicalStack:
    """
    Ordered physical stack with native source family metadata.
    """

    stack_ref: str
    display_name: str
    source_family: str
    is_flex: bool | None = None
    service_stackup: bool | None = None
    used_by_primitives: bool | None = None
    stackupx_is_flex: bool | None = None
    stackupx_stack_type: str | None = None
    layers: tuple[AltiumStackLayer, ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "stack_ref": self.stack_ref,
            "display_name": self.display_name,
            "source_family": self.source_family,
            "is_flex": self.is_flex,
            "service_stackup": self.service_stackup,
            "used_by_primitives": self.used_by_primitives,
            "stackupx_is_flex": self.stackupx_is_flex,
            "stackupx_stack_type": self.stackupx_stack_type,
            "layer_names": [layer.display_name for layer in self.layers],
            "layers": [layer.to_debug_json() for layer in self.layers],
        }


@dataclass(frozen=True)
class AltiumStackSubstack:
    """
    Rigid/flex substack metadata plus enabled physical layer refs.
    """

    index: int
    field_family: str
    source_stackup_ref: str
    name: str
    is_flex: bool | None = None
    show_top_dielectric: bool | None = None
    show_bottom_dielectric: bool | None = None
    service_stackup: bool | None = None
    used_by_primitives: bool | None = None
    raw_stackup_type: str = ""
    stackupx_is_flex: bool | None = None
    stackupx_stack_type: str | None = None
    enabled_layer_refs: tuple[str, ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "field_family": self.field_family,
            "source_stackup_ref": self.source_stackup_ref,
            "name": self.name,
            "is_flex": self.is_flex,
            "show_top_dielectric": self.show_top_dielectric,
            "show_bottom_dielectric": self.show_bottom_dielectric,
            "service_stackup": self.service_stackup,
            "used_by_primitives": self.used_by_primitives,
            "raw_stackup_type": self.raw_stackup_type,
            "stackupx_is_flex": self.stackupx_is_flex,
            "stackupx_stack_type": self.stackupx_stack_type,
            "enabled_layer_refs": list(self.enabled_layer_refs),
        }


@dataclass(frozen=True)
class AltiumStackBranchStack:
    """
    One stack reference inside a Layer Stack Manager branch section.
    """

    source_stack_ref: str
    description: str = ""
    source_record_id: str = ""
    material_usage: str = ""
    source: str = ""
    parent_layer_id: str = ""
    parent_layer_stack_id: str = ""
    source_layer_id: str = ""
    source_layer_stack_id: str = ""
    is_left_intrusions_linked: bool | None = None
    intrusion_left_bottom: str = ""
    intrusion_left_top: str = ""
    is_right_intrusions_linked: bool | None = None
    intrusion_right_bottom: str = ""
    intrusion_right_top: str = ""

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_stack_ref": self.source_stack_ref,
            "description": self.description,
            "source_record_id": self.source_record_id,
            "material_usage": self.material_usage,
            "source": self.source,
            "parent_layer_id": self.parent_layer_id,
            "parent_layer_stack_id": self.parent_layer_stack_id,
            "source_layer_id": self.source_layer_id,
            "source_layer_stack_id": self.source_layer_stack_id,
            "is_left_intrusions_linked": self.is_left_intrusions_linked,
            "intrusion_left_bottom": self.intrusion_left_bottom,
            "intrusion_left_top": self.intrusion_left_top,
            "is_right_intrusions_linked": self.is_right_intrusions_linked,
            "intrusion_right_bottom": self.intrusion_right_bottom,
            "intrusion_right_top": self.intrusion_right_top,
        }


@dataclass(frozen=True)
class AltiumStackBranchSection:
    """
    One topology node inside an LSM rigid-flex branch.
    """

    source_section_id: str
    name: str
    parent_section_id: str = ""
    stacks: tuple[AltiumStackBranchStack, ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_section_id": self.source_section_id,
            "name": self.name,
            "parent_section_id": self.parent_section_id,
            "stacks": [stack.to_debug_json() for stack in self.stacks],
        }


@dataclass(frozen=True)
class AltiumStackBranch:
    """
    Layer Stack Manager branch topology.
    """

    source_branch_id: str
    name: str
    description: str = ""
    parent_branch_id: str = ""
    sections: tuple[AltiumStackBranchSection, ...] = ()

    @property
    def root_stack_ref(self) -> str:
        if not self.sections or not self.sections[0].stacks:
            return ""
        return self.sections[0].stacks[0].source_stack_ref

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_branch_id": self.source_branch_id,
            "name": self.name,
            "description": self.description,
            "parent_branch_id": self.parent_branch_id,
            "root_stack_ref": self.root_stack_ref,
            "sections": [section.to_debug_json() for section in self.sections],
        }


@dataclass(frozen=True)
class AltiumLayerPair:
    """
    Drill/via span metadata parsed from Board6/Data layer-pair rows.
    """

    pair_index: int
    low_layer_token: str
    high_layer_token: str
    source_substack_refs: tuple[str, ...] = ()
    drill_pair_type_raw: int | None = None
    is_backdrill: bool | None = None
    is_inverted: bool | None = None
    drill_guide: bool | None = None
    drill_drawing: bool | None = None

    def to_debug_json(self) -> dict[str, object]:
        return {
            "pair_index": self.pair_index,
            "low_layer_token": self.low_layer_token,
            "high_layer_token": self.high_layer_token,
            "source_substack_refs": list(self.source_substack_refs),
            "drill_pair_type_raw": self.drill_pair_type_raw,
            "is_backdrill": self.is_backdrill,
            "is_inverted": self.is_inverted,
            "drill_guide": self.drill_guide,
            "drill_drawing": self.drill_drawing,
        }


@dataclass(frozen=True)
class AltiumImpedanceProfile:
    """
    Layer Stack Manager impedance-profile metadata.
    """

    index: int
    source_family: str
    source_record_id: str
    name: str
    profile_type: str = ""
    profile_type_raw: int | None = None
    impedance: str = ""
    tolerance: str = ""
    transmission_line_count: int = 0

    def to_debug_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "source_family": self.source_family,
            "source_record_id": self.source_record_id,
            "name": self.name,
            "profile_type": self.profile_type,
            "profile_type_raw": self.profile_type_raw,
            "impedance": self.impedance,
            "tolerance": self.tolerance,
            "transmission_line_count": self.transmission_line_count,
        }


@dataclass(frozen=True)
class AltiumTransmissionLine:
    """
    Layer Stack Manager transmission-line row for one impedance profile/layer.
    """

    index: int
    source_family: str
    profile_id: str
    substack_id: str
    layer_id: str
    layer_token: str
    is_differential: bool | None = None
    top_ref_id: str = ""
    top_ref_token: str = ""
    bottom_ref_id: str = ""
    bottom_ref_token: str = ""
    width: str = ""
    gap: str = ""
    calc_mode: str = ""
    calc_mode_raw: int | None = None
    impedance_error: str = ""
    tl_type_name: str = ""
    has_plating: bool | None = None
    use_solder_mask: bool | None = None
    coated_height_1: str = ""
    coated_height_2: str = ""
    clearance_to_plane: str = ""
    electric_parameters: tuple[tuple[str, str], ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "index": self.index,
            "source_family": self.source_family,
            "profile_id": self.profile_id,
            "substack_id": self.substack_id,
            "layer_id": self.layer_id,
            "layer_token": self.layer_token,
            "is_differential": self.is_differential,
            "top_ref_id": self.top_ref_id,
            "top_ref_token": self.top_ref_token,
            "bottom_ref_id": self.bottom_ref_id,
            "bottom_ref_token": self.bottom_ref_token,
            "width": self.width,
            "gap": self.gap,
            "calc_mode": self.calc_mode,
            "calc_mode_raw": self.calc_mode_raw,
            "impedance_error": self.impedance_error,
            "tl_type_name": self.tl_type_name,
            "has_plating": self.has_plating,
            "use_solder_mask": self.use_solder_mask,
            "coated_height_1": self.coated_height_1,
            "coated_height_2": self.coated_height_2,
            "clearance_to_plane": self.clearance_to_plane,
            "electric_parameters": [list(item) for item in self.electric_parameters],
        }


@dataclass(frozen=True)
class AltiumTransmissionLineSpec:
    """
    Authored transmission-line input for a rigid stack impedance profile.

    Layer selectors accept canonical Altium tokens such as `TOP`, `MID1`, and
    `BOTTOM`, or copper layer display names from the authored stack.
    """

    layer: str
    top_reference: str = ""
    bottom_reference: str = ""
    is_differential: bool | None = None
    width: str = ""
    gap: str = ""
    calc_mode: str = ""
    calc_mode_raw: int | None = None
    impedance_error: str = ""
    tl_type_name: str = ""
    has_plating: bool | None = None
    use_solder_mask: bool | None = None
    coated_height_1: str = ""
    coated_height_2: str = ""
    clearance_to_plane: str = ""
    electric_parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class AltiumImpedanceProfileSpec:
    """
    Authored Layer Stack Manager impedance-profile input for rigid stacks.
    """

    name: str
    impedance: str
    profile_type: str = "Single"
    profile_type_raw: int | None = None
    tolerance: str = ""
    transmission_lines: tuple[AltiumTransmissionLineSpec, ...] = ()


@dataclass(frozen=True)
class AltiumRigidCopperLayerSpec:
    """
    Authored rigid-board copper layer input for new PcbDoc stack synthesis.
    """

    name: str
    copper_thickness_mils: float = 1.4
    component_placement: int | None = None
    copper_orientation: int | None = None


@dataclass(frozen=True)
class AltiumRigidDielectricLayerSpec:
    """
    Authored rigid-board dielectric layer input for new PcbDoc stack synthesis.
    """

    name: str
    thickness_mils: float
    dielectric_constant: float
    material: str
    dielectric_type: int = 0
    loss_tangent: float | None = None


@dataclass(frozen=True)
class AltiumStackBendLine:
    """
    Typed bend-line metadata from BoardRegions/Data.
    """

    angle_deg: float | None = None
    radius_raw: int | None = None
    fold_index: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    raw_value: str = ""

    @property
    def radius_mils(self) -> float | None:
        """
        Bend radius in mils, derived from the native raw coordinate value.
        """
        return _radius_raw_to_mils(self.radius_raw)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "angle_deg": self.angle_deg,
            "radius_raw": self.radius_raw,
            "radius_mils": self.radius_mils,
            "fold_index": self.fold_index,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "raw_value": self.raw_value,
        }


@dataclass(frozen=True)
class AltiumBoardBendLine:
    """
    Native top-level Board6/Data bend-line cache entry.

    AD26 stores bend lines twice: the board-region-local geometry lives in
    BoardRegions/Data, while the top-level Board6 segment carries the Layer
    Stack Manager cache used by the editor. The coordinate systems are not
    interchangeable, so this model keeps the Board6 value source-aware.
    """

    angle_deg: float | None = None
    radius_raw: int | None = None
    fold_index: int | None = None
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    name: str = ""
    state_raw: str = ""
    region_name: str = ""
    raw_value: str = ""

    @classmethod
    def from_property_value(cls, raw_value: object) -> "AltiumBoardBendLine":
        text = _text(raw_value)
        tokens = [token.strip() for token in text.split(";")]
        return cls(
            angle_deg=_float_token(tokens[0]) if len(tokens) >= 1 else None,
            radius_raw=_parse_int_like_token(tokens[1]) if len(tokens) >= 2 else None,
            fold_index=_parse_int_like_token(tokens[2]) if len(tokens) >= 3 else None,
            x1=_parse_int_like_token(tokens[3]) if len(tokens) >= 4 else None,
            y1=_parse_int_like_token(tokens[4]) if len(tokens) >= 5 else None,
            x2=_parse_int_like_token(tokens[5]) if len(tokens) >= 6 else None,
            y2=_parse_int_like_token(tokens[6]) if len(tokens) >= 7 else None,
            name=tokens[7] if len(tokens) >= 8 else "",
            state_raw=tokens[8] if len(tokens) >= 9 else "",
            region_name=tokens[9] if len(tokens) >= 10 else "",
            raw_value=text,
        )

    @property
    def radius_mils(self) -> float | None:
        """
        Bend radius in mils, derived from the native raw coordinate value.
        """
        return _radius_raw_to_mils(self.radius_raw)

    def to_property_value(self) -> str:
        if self.raw_value and self._matches_raw_value():
            return self.raw_value
        tokens = [
            _format_optional_float(self.angle_deg),
            _format_optional_int(self.radius_raw),
            _format_optional_int(self.fold_index),
            _format_optional_int(self.x1),
            _format_optional_int(self.y1),
            _format_optional_int(self.x2),
            _format_optional_int(self.y2),
            self.name,
            self.state_raw,
            self.region_name,
        ]
        return ";".join(tokens)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "angle_deg": self.angle_deg,
            "radius_raw": self.radius_raw,
            "radius_mils": self.radius_mils,
            "fold_index": self.fold_index,
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "name": self.name,
            "state_raw": self.state_raw,
            "region_name": self.region_name,
            "raw_value": self.raw_value,
        }

    def _matches_raw_value(self) -> bool:
        parsed = self.from_property_value(self.raw_value)
        return (
            parsed.angle_deg == self.angle_deg
            and parsed.radius_raw == self.radius_raw
            and parsed.fold_index == self.fold_index
            and parsed.x1 == self.x1
            and parsed.y1 == self.y1
            and parsed.x2 == self.x2
            and parsed.y2 == self.y2
            and parsed.name == self.name
            and parsed.state_raw == self.state_raw
            and parsed.region_name == self.region_name
        )


@dataclass(frozen=True)
class AltiumStackRegion:
    """
    Board-region layer-stack context and bend-line evidence.
    """

    name: str
    layerstack_id: str = ""
    locked_3d: bool = False
    is_shapebased: bool = False
    cavity_height: str = ""
    layer_token: str = ""
    v7_layer: str = ""
    bending_lines: tuple[AltiumStackBendLine, ...] = ()
    outline_vertices: tuple[tuple[float, float], ...] = ()
    hole_vertices: tuple[tuple[tuple[float, float], ...], ...] = ()
    raw_property_keys: tuple[str, ...] = ()
    raw_properties: tuple[tuple[str, str], ...] = ()
    extended_outline_tail_base64: str = ""
    preserve_raw_property_order: bool = False

    @property
    def bending_line_count(self) -> int:
        return len(self.bending_lines)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "layerstack_id": self.layerstack_id,
            "locked_3d": self.locked_3d,
            "is_shapebased": self.is_shapebased,
            "cavity_height": self.cavity_height,
            "layer_token": self.layer_token,
            "v7_layer": self.v7_layer,
            "bending_line_count": self.bending_line_count,
            "bending_lines": [line.to_debug_json() for line in self.bending_lines],
            "outline_vertices": [list(vertex) for vertex in self.outline_vertices],
            "hole_vertices": [
                [list(vertex) for vertex in hole] for hole in self.hole_vertices
            ],
            "raw_property_keys": list(self.raw_property_keys),
            "raw_properties": [list(item) for item in self.raw_properties],
            "extended_outline_tail_base64": self.extended_outline_tail_base64,
            **(
                {"preserve_raw_property_order": True}
                if self.preserve_raw_property_order
                else {}
            ),
        }


@dataclass(frozen=True)
class AltiumLayerStackSourceMap:
    """
    Deterministic summary of native source evidence used to build the model.
    """

    origin: str = ""
    board_record: tuple[tuple[str, str], ...] = ()
    board_stack_entry_texts: tuple[str, ...] = ()
    v9_stack_keys: tuple[str, ...] = ()
    v9_cache_keys: tuple[str, ...] = ()
    v8_layer_keys: tuple[str, ...] = ()
    legacy_layer_keys: tuple[str, ...] = ()
    layer_pair_keys: tuple[str, ...] = ()
    substack_keys: tuple[str, ...] = ()
    board_region_count: int = 0

    def board_record_mapping(self) -> dict[str, str]:
        return dict(self.board_record)

    def to_debug_json(self) -> dict[str, object]:
        return {
            "origin": self.origin,
            "board_record_key_count": len(self.board_record),
            "board_stack_entry_count": len(self.board_stack_entry_texts),
            "v9_stack_keys": list(self.v9_stack_keys),
            "v9_cache_keys": list(self.v9_cache_keys),
            "v8_layer_keys": list(self.v8_layer_keys),
            "legacy_layer_keys": list(self.legacy_layer_keys),
            "layer_pair_keys": list(self.layer_pair_keys),
            "substack_keys": list(self.substack_keys),
            "board_region_count": self.board_region_count,
        }


def _is_layer_pair_source_key(key: str) -> bool:
    return key.upper().startswith(
        ("LAYERPAIR", "VIASPAN_V8_", "DRILLPAIR_V8_", "DRILLSPAN_V8_")
    )


def _is_substack_source_key(key: str) -> bool:
    upper_key = key.upper()
    return "SUBSTACK" in upper_key and not upper_key.startswith("LAYERPAIR")


def _build_layer_stack_source_map(
    *,
    raw_record: Mapping[str, str],
    ordered_stack_entries: tuple[str, ...],
    board_regions: Sequence[object] | None,
    source_origin: str,
) -> AltiumLayerStackSourceMap:
    return AltiumLayerStackSourceMap(
        origin=source_origin,
        board_record=tuple(sorted(raw_record.items())),
        board_stack_entry_texts=ordered_stack_entries,
        v9_stack_keys=tuple(
            sorted(key for key in raw_record if _V9_STACK_RE.match(key))
        ),
        v9_cache_keys=tuple(
            sorted(key for key in raw_record if _V9_CACHE_RE.match(key))
        ),
        v8_layer_keys=tuple(
            sorted(key for key in raw_record if _LAYER_V8_RE.match(key))
        ),
        legacy_layer_keys=tuple(
            sorted(key for key in raw_record if _LEGACY_LAYER_RE.match(key))
        ),
        layer_pair_keys=tuple(
            sorted(key for key in raw_record if _is_layer_pair_source_key(key))
        ),
        substack_keys=tuple(
            sorted(key for key in raw_record if _is_substack_source_key(key))
        ),
        board_region_count=len(tuple(board_regions or ())),
    )


def _build_board_bending_lines(
    raw_record: Mapping[str, str],
) -> tuple[AltiumBoardBendLine, ...]:
    indexed: list[tuple[int, AltiumBoardBendLine]] = []
    for key, value in raw_record.items():
        match = re.match(r"^BENDINGLINE(\d+)$", key, re.IGNORECASE)
        if match is None:
            continue
        indexed.append(
            (int(match.group(1)), AltiumBoardBendLine.from_property_value(value))
        )
    indexed.sort(key=lambda item: item[0])
    return tuple(line for _index, line in indexed)


def _build_board_arc_split_lines(raw_record: Mapping[str, str]) -> tuple[str, ...]:
    indexed: list[tuple[int, str]] = []
    for key, value in raw_record.items():
        match = re.match(r"^ARCSPLITLINE(\d+)$", key, re.IGNORECASE)
        if match is None:
            continue
        indexed.append((int(match.group(1)), str(value)))
    indexed.sort(key=lambda item: item[0])
    return tuple(value for _index, value in indexed)


def _build_board_split_lines(raw_record: Mapping[str, str]) -> tuple[str, ...]:
    indexed: list[tuple[int, str]] = []
    for key, value in raw_record.items():
        match = re.match(r"^SPLITLINE(\d+)$", key, re.IGNORECASE)
        if match is None:
            continue
        indexed.append((int(match.group(1)), str(value)))
    indexed.sort(key=lambda item: item[0])
    return tuple(value for _index, value in indexed)


def _build_board_impedance_records(
    raw_record: Mapping[str, str],
    stack_layers: Sequence["AltiumStackLayer"],
) -> tuple[tuple["AltiumImpedanceProfile", ...], tuple["AltiumTransmissionLine", ...]]:
    stack_custom_impedance = _build_stack_custom_data_impedance(raw_record)
    if stack_custom_impedance[0]:
        return stack_custom_impedance
    return (
        _build_v8_impedance_profiles(raw_record),
        _build_v8_transmission_lines(raw_record, stack_layers),
    )


@dataclass(frozen=True)
class AltiumNativePcbDocWriteSupport:
    """
    Current native PcbDoc semantic writer support summary.
    """

    supported: bool
    mode: str
    reasons: tuple[str, ...] = ()
    unsupported_source_field_buckets: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def unsupported_source_field_mapping(self) -> dict[str, tuple[str, ...]]:
        """
        Return unsupported preserved source fields grouped by family.
        """
        return dict(self.unsupported_source_field_buckets)

    def to_debug_json(self) -> dict[str, object]:
        """
        Return deterministic JSON-compatible diagnostic data.
        """
        return {
            "supported": self.supported,
            "mode": self.mode,
            "reasons": list(self.reasons),
            "unsupported_source_field_buckets": {
                bucket: list(keys)
                for bucket, keys in self.unsupported_source_field_buckets
            },
        }


@dataclass(frozen=True)
class AltiumLayerStackDocument:
    """
    Root source-aware PcbDoc layer-stack model.
    """

    layer_registry: AltiumLayerRegistry
    physical_stacks: tuple[AltiumPhysicalStack, ...]
    active_stack_ref: str | None = None
    rigid_flex_mode: str = "none"
    substacks: tuple[AltiumStackSubstack, ...] = ()
    stackupx_auxiliary_stacks: tuple[AltiumStackSubstack, ...] = ()
    branches: tuple[AltiumStackBranch, ...] = ()
    board_regions: tuple[AltiumStackRegion, ...] = ()
    layer_pairs: tuple[AltiumLayerPair, ...] = ()
    board_split_lines: tuple[str, ...] = ()
    board_bending_lines: tuple[AltiumBoardBendLine, ...] = ()
    board_arc_split_lines: tuple[str, ...] = ()
    materials: tuple[object, ...] = ()
    impedance_profiles: tuple[AltiumImpedanceProfile, ...] = ()
    transmission_lines: tuple[AltiumTransmissionLine, ...] = ()
    stackup_attributes: tuple[tuple[str, str], ...] = ()
    source: AltiumLayerStackSourceMap = field(default_factory=AltiumLayerStackSourceMap)

    @classmethod
    def canonical_empty(cls) -> "AltiumLayerStackDocument":
        """
        Build the model from the canonical empty PcbDoc builder profile.
        """
        from .altium_pcbdoc_builder import PcbDocBuildProfile

        return cls.from_pcbdoc_builder_profile(PcbDocBuildProfile.default())

    @classmethod
    def from_pcbdoc_builder_profile(cls, profile: object) -> "AltiumLayerStackDocument":
        board_data = getattr(profile, "board_data", None)
        if board_data is None:
            raise ValueError("PcbDoc build profile does not contain Board6/Data")
        board_regions_data = getattr(profile, "board_regions_data", None)
        board_regions = tuple(getattr(board_regions_data, "records", ()) or ())
        return cls.from_board_data(board_data, board_regions=board_regions)

    @classmethod
    def from_pcbdoc_layer_stack_template(
        cls,
        template: "PcbDocLayerStackTemplate | str",
        *,
        base_board_data: "PcbDocBoardData | None" = None,
    ) -> "AltiumLayerStackDocument":
        """
        Build a source-aware layer-stack model from a PcbDoc builder template.

        This is the compatibility bridge for the existing two-layer/four-layer
        template path. The low-level template fabricator only seeds native
        source rows; callers should emit new `Board6/Data` through this model.
        """
        from .altium_pcbdoc_builder import PcbDocBoardData
        from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackBuilder

        resolved_template = PcbDocLayerStackBuilder.resolve_template(template)
        source_base = (
            base_board_data
            if base_board_data is not None
            else PcbDocBoardData.default()
        )
        source_builder = getattr(source_base, "_with_layer_stack_template_source", None)
        if not callable(source_builder):
            raise TypeError(
                "base_board_data does not support layer-stack template source emission"
            )
        source_board_data = source_builder(resolved_template)
        return cls.from_board_data(source_board_data)

    @classmethod
    def from_rigid_stack(
        cls,
        *,
        name: str,
        copper_layers: Sequence[AltiumRigidCopperLayerSpec],
        dielectrics_between: Sequence[AltiumRigidDielectricLayerSpec],
        impedance_profiles: Sequence[AltiumImpedanceProfileSpec] = (),
        base_board_data: "PcbDocBoardData | None" = None,
    ) -> "AltiumLayerStackDocument":
        """
        Build a new-document rigid stack from typed copper/dielectric rows.

        This constructor owns ordinary rigid-board stack synthesis only. It
        does not edit existing complex boards, rigid-flex zones, or bend lines.
        Optional impedance-profile specs are emitted as native V8 Layer Stack
        Manager impedance rows during new PcbDoc authoring.
        """
        from .altium_pcbdoc_layer_stack_builder import (
            PcbDocCopperLayerTemplate,
            PcbDocDielectricTemplate,
            PcbDocLayerStackTemplate,
        )

        copper_specs = tuple(copper_layers)
        dielectric_specs = tuple(dielectrics_between)
        if len(copper_specs) < 2:
            raise ValueError("Rigid stack requires at least two copper layers")
        if len(copper_specs) > 32:
            raise ValueError("Rigid stack supports at most 32 copper layers")
        if len(dielectric_specs) != len(copper_specs) - 1:
            raise ValueError(
                "Rigid stack dielectric count must equal copper count minus one"
            )

        template = PcbDocLayerStackTemplate(
            name=str(name),
            copper_layers=tuple(
                PcbDocCopperLayerTemplate(
                    legacy_layer_id=_rigid_copper_legacy_layer_id(
                        index,
                        len(copper_specs),
                    ),
                    v7_layer_id=_rigid_copper_v7_layer_id(index, len(copper_specs)),
                    name=spec.name,
                    copper_thickness_mils=spec.copper_thickness_mils,
                    component_placement=_rigid_component_placement(
                        spec,
                        index,
                        len(copper_specs),
                    ),
                    copper_orientation=spec.copper_orientation,
                )
                for index, spec in enumerate(copper_specs)
            ),
            dielectrics_between=tuple(
                PcbDocDielectricTemplate(
                    name=spec.name,
                    thickness_mils=spec.thickness_mils,
                    dielectric_constant=spec.dielectric_constant,
                    material=spec.material,
                    dielectric_type=spec.dielectric_type,
                    loss_tangent=spec.loss_tangent,
                )
                for spec in dielectric_specs
            ),
        )
        document = cls.from_pcbdoc_layer_stack_template(
            template,
            base_board_data=base_board_data,
        )
        return _with_authored_impedance_profiles(document, impedance_profiles)

    @classmethod
    def two_layer_rigid(cls) -> "AltiumLayerStackDocument":
        """
        Build the existing two-layer rigid FR-4 template through the model.
        """
        from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackTemplate

        return cls.from_pcbdoc_layer_stack_template(
            PcbDocLayerStackTemplate.two_layer_fr4()
        )

    @classmethod
    def four_layer_rigid(cls) -> "AltiumLayerStackDocument":
        """
        Build the existing four-layer rigid FR-4 template through the model.
        """
        from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackTemplate

        return cls.from_pcbdoc_layer_stack_template(
            PcbDocLayerStackTemplate.four_layer_fr4()
        )

    def with_layer_pair(
        self,
        low_layer: object,
        high_layer: object,
        *,
        pair_index: int | None = None,
        source_substack_refs: Sequence[str] | None = None,
        drill_pair_type_raw: int | None = None,
        is_backdrill: bool | None = None,
        is_inverted: bool | None = None,
        drill_guide: bool | None = False,
        drill_drawing: bool | None = False,
    ) -> "AltiumLayerStackDocument":
        """
        Return a document with one authored layer-pair / drill-span row.

        This edits the Layer Stack Manager span metadata (`LAYERPAIR*`), not
        via binary tails. Historical native-save research showed direct
        backdrill/counterhole via-tail mutation is unsafe; authored backdrills
        should be represented as stack spans through this document model.
        """
        resolved_low = _resolve_authored_layer_pair_token(self, low_layer)
        resolved_high = _resolve_authored_layer_pair_token(self, high_layer)
        resolved_pair_index = (
            pair_index
            if pair_index is not None
            else max((pair.pair_index for pair in self.layer_pairs), default=-1) + 1
        )
        if resolved_pair_index < 0:
            raise ValueError("Layer-pair index must be non-negative")

        existing_pair = next(
            (
                pair
                for pair in self.layer_pairs
                if pair.pair_index == resolved_pair_index
            ),
            None,
        )
        if source_substack_refs is None:
            resolved_substacks = (
                existing_pair.source_substack_refs
                if existing_pair is not None
                else _default_layer_pair_substack_refs(self)
            )
        else:
            resolved_substacks = tuple(
                substack_ref
                for substack_ref in (_text(ref) for ref in source_substack_refs)
                if substack_ref
            )

        authored_pair = AltiumLayerPair(
            pair_index=resolved_pair_index,
            low_layer_token=resolved_low,
            high_layer_token=resolved_high,
            source_substack_refs=resolved_substacks,
            drill_pair_type_raw=drill_pair_type_raw,
            is_backdrill=is_backdrill,
            is_inverted=is_inverted,
            drill_guide=drill_guide,
            drill_drawing=drill_drawing,
        )
        pairs = [
            pair
            for pair in self.layer_pairs
            if pair.pair_index != authored_pair.pair_index
        ]
        pairs.append(authored_pair)
        return replace(
            self, layer_pairs=tuple(sorted(pairs, key=lambda pair: pair.pair_index))
        )

    def with_via_span(
        self,
        low_layer: object,
        high_layer: object,
        *,
        pair_index: int | None = None,
        source_substack_refs: Sequence[str] | None = None,
        drill_pair_type_raw: int | None = None,
        is_inverted: bool | None = None,
        drill_guide: bool | None = False,
        drill_drawing: bool | None = False,
    ) -> "AltiumLayerStackDocument":
        """
        Return a document with one authored non-backdrill via span.
        """
        return self.with_layer_pair(
            low_layer,
            high_layer,
            pair_index=pair_index,
            source_substack_refs=source_substack_refs,
            drill_pair_type_raw=drill_pair_type_raw,
            is_backdrill=False,
            is_inverted=is_inverted,
            drill_guide=drill_guide,
            drill_drawing=drill_drawing,
        )

    def with_backdrill_span(
        self,
        low_layer: object,
        high_layer: object,
        *,
        pair_index: int | None = None,
        source_substack_refs: Sequence[str] | None = None,
        drill_pair_type_raw: int | None = None,
        is_inverted: bool | None = None,
        drill_guide: bool | None = False,
        drill_drawing: bool | None = False,
    ) -> "AltiumLayerStackDocument":
        """
        Return a document with one authored Layer Stack Manager backdrill span.
        """
        return self.with_layer_pair(
            low_layer,
            high_layer,
            pair_index=pair_index,
            source_substack_refs=source_substack_refs,
            drill_pair_type_raw=drill_pair_type_raw,
            is_backdrill=True,
            is_inverted=is_inverted,
            drill_guide=drill_guide,
            drill_drawing=drill_drawing,
        )

    @classmethod
    def from_board_data(
        cls,
        board_data: object,
        *,
        board_regions: tuple[object, ...] | list[object] | None = None,
    ) -> "AltiumLayerStackDocument":
        board = _board_from_board_data(board_data)
        ordered_stack_entries = _ordered_stack_entries_from_board_data(board_data)
        return cls.from_board(
            board,
            board_regions=board_regions,
            ordered_stack_entries=ordered_stack_entries,
            source_origin="board_data",
        )

    @classmethod
    def from_pcbdoc(cls, pcbdoc: object) -> "AltiumLayerStackDocument":
        board = getattr(pcbdoc, "board", None)
        if board is None:
            raise ValueError("PcbDoc does not contain parsed board metadata")
        ordered_stack_entries: tuple[str, ...] = ()
        profile_getter = getattr(pcbdoc, "_profile_for_authoring_builder", None)
        if callable(profile_getter):
            try:
                profile = profile_getter()
                board_data = getattr(profile, "board_data", None)
                if board_data is not None:
                    ordered_stack_entries = _ordered_stack_entries_from_board_data(
                        board_data
                    )
            except (OSError, ValueError, TypeError, KeyError):
                ordered_stack_entries = ()
        return cls.from_board(
            board,
            board_regions=tuple(getattr(pcbdoc, "board_regions", ()) or ()),
            ordered_stack_entries=ordered_stack_entries,
            source_origin="pcbdoc",
        )

    @classmethod
    def from_stackupx(cls, source: object) -> "AltiumLayerStackDocument":
        """
        Build the common layer-stack view from a `.stackupx` XML export.
        """
        from .altium_stackupx import AltiumStackupXDocument

        if isinstance(source, AltiumStackupXDocument):
            return source.to_layer_stack_document()
        return AltiumStackupXDocument.from_file(
            _path_text(source)
        ).to_layer_stack_document()

    @classmethod
    def from_stackup(cls, source: object) -> "AltiumLayerStackDocument":
        """
        Build the common layer-stack view from a `.stackup` text export.
        """
        from .altium_stackup import AltiumStackupDocument

        if isinstance(source, AltiumStackupDocument):
            return source.to_layer_stack_document()
        return AltiumStackupDocument.from_file(
            _path_text(source)
        ).to_layer_stack_document()

    @classmethod
    def from_board(
        cls,
        board: object,
        *,
        board_regions: tuple[object, ...] | list[object] | None = None,
        ordered_stack_entries: tuple[str, ...] = (),
        source_origin: str = "",
    ) -> "AltiumLayerStackDocument":
        raw_record = {
            str(key): str(value)
            for key, value in (getattr(board, "raw_record", {}) or {}).items()
        }
        v9_groups = _group_indexed_fields(raw_record, _V9_STACK_RE)
        v8_groups = _group_indexed_fields(raw_record, _LAYER_V8_RE)
        cache_groups = _group_indexed_fields(raw_record, _V9_CACHE_RE)
        stack_custom_data = _stack_custom_data_stackupx(raw_record)

        registry_entries: dict[str, AltiumLayerRegistryEntry] = {}
        stack_layers = _build_stack_layers(
            raw_record=raw_record,
            board=board,
            registry_entries=registry_entries,
            v9_groups=v9_groups,
            v8_groups=v8_groups,
        )
        stack_layers = _with_stack_custom_data_layer_flags(
            stack_layers,
            stack_custom_data,
            registry_entries,
        )
        _append_cache_registry_entries(cache_groups, registry_entries)
        _append_legacy_registry_entries(board, registry_entries)

        substacks = _build_substacks(board, stack_layers)
        primary_substack = substacks[0] if substacks else None
        active_stack_ref = (
            _text(raw_record.get("V9_MASTERSTACK_ID"))
            or _text(raw_record.get("LAYERMASTERSTACK_V8ID"))
            or (primary_substack.source_stackup_ref if primary_substack else None)
        )

        physical_stack = AltiumPhysicalStack(
            stack_ref=active_stack_ref or "default",
            display_name=(
                _text(raw_record.get("V9_MASTERSTACK_NAME"))
                or _text(raw_record.get("LAYERMASTERSTACK_V8NAME"))
                or "Master layer stack"
            ),
            source_family="v9" if v9_groups else "v8" if v8_groups else "legacy",
            is_flex=_parse_altium_bool_token(raw_record.get("V9_MASTERSTACK_ISFLEX")),
            service_stackup=(
                primary_substack.service_stackup if primary_substack else None
            ),
            used_by_primitives=(
                primary_substack.used_by_primitives if primary_substack else None
            ),
            layers=tuple(stack_layers),
        )

        source = _build_layer_stack_source_map(
            raw_record=raw_record,
            ordered_stack_entries=ordered_stack_entries,
            board_regions=board_regions,
            source_origin=source_origin,
        )

        impedance_profiles, transmission_lines = _build_board_impedance_records(
            raw_record,
            stack_layers,
        )

        return cls(
            layer_registry=AltiumLayerRegistry(
                entries=tuple(registry_entries[key] for key in sorted(registry_entries))
            ),
            physical_stacks=(physical_stack,),
            active_stack_ref=active_stack_ref,
            rigid_flex_mode=_rigid_flex_mode_from_board_record(
                raw_record,
                substacks,
            ),
            substacks=tuple(substacks),
            stackupx_auxiliary_stacks=_stack_custom_data_auxiliary_stacks(
                stack_custom_data,
                tuple(substacks),
            ),
            branches=_build_branches(raw_record),
            board_regions=_build_regions(board_regions),
            layer_pairs=_build_layer_pairs(board, stack_layers),
            board_split_lines=_build_board_split_lines(raw_record),
            board_bending_lines=_build_board_bending_lines(raw_record),
            board_arc_split_lines=_build_board_arc_split_lines(raw_record),
            impedance_profiles=impedance_profiles,
            transmission_lines=transmission_lines,
            stackup_attributes=_stackup_attributes_from_sources(
                raw_record,
                stack_custom_data,
            ),
            source=source,
        )

    def to_board_data_entries(self) -> tuple[str, ...]:
        """
        Return ordered Board6/Data stack entries captured from the source.
        """
        return self.source.board_stack_entry_texts

    def physical_stack_by_ref(self, stack_ref: str) -> AltiumPhysicalStack | None:
        """
        Lookup a physical master stack by native stack GUID/ref.
        """
        normalized = _normalized_source_id(stack_ref)
        if not normalized:
            return None
        for stack in self.physical_stacks:
            if _normalized_source_id(stack.stack_ref) == normalized:
                return stack
        return None

    def substack_by_source_ref(
        self,
        source_stackup_ref: str,
    ) -> AltiumStackSubstack | None:
        """
        Lookup a rigid/flex substack by native stackup GUID/ref.
        """
        normalized = _normalized_source_id(source_stackup_ref)
        if not normalized:
            return None
        for substack in self.substacks:
            if _normalized_source_id(substack.source_stackup_ref) == normalized:
                return substack
        return None

    def substacks_by_name(self, name: str) -> tuple[AltiumStackSubstack, ...]:
        """
        Return all substacks with a matching display name.

        Display names are user-facing labels and may collide. Prefer
        `source_stackup_ref` for stable joins.
        """
        normalized = _text(name)
        if not normalized:
            return ()
        return tuple(
            substack for substack in self.substacks if substack.name == normalized
        )

    def board_region_by_name(self, name: str) -> AltiumStackRegion | None:
        """
        Lookup one board stack region by display name.
        """
        normalized = _text(name)
        if not normalized:
            return None
        for region in self.board_regions:
            if region.name == normalized:
                return region
        return None

    def board_regions_for_layerstack_id(
        self,
        layerstack_id: str,
    ) -> tuple[AltiumStackRegion, ...]:
        """
        Return board regions assigned to a native substack GUID/ref.
        """
        normalized = _normalized_source_id(layerstack_id)
        if not normalized:
            return ()
        return tuple(
            region
            for region in self.board_regions
            if _normalized_source_id(region.layerstack_id) == normalized
        )

    def layers_for_substack(
        self,
        source_stackup_ref: str,
    ) -> tuple[AltiumStackLayer, ...]:
        """
        Return physical layers enabled for a native substack GUID/ref.
        """
        substack = self.substack_by_source_ref(source_stackup_ref)
        if substack is None:
            return ()
        context_layers = tuple(
            layer
            for stack in self.physical_stacks
            for layer in stack.layers
            if _layer_has_substack_context(layer, substack.source_stackup_ref)
        )
        if context_layers:
            return tuple(
                layer
                for layer in context_layers
                if layer.is_enabled_for_substack(substack.source_stackup_ref)
            )
        refs = {ref.upper() for ref in substack.enabled_layer_refs}
        if refs:
            return tuple(
                layer
                for stack in self.physical_stacks
                for layer in stack.layers
                if layer.registry_ref.upper() in refs
            )
        return tuple(
            layer
            for stack in self.physical_stacks
            for layer in stack.layers
            if layer.is_enabled_for_substack(substack.source_stackup_ref)
        )

    def layers_for_board_region(
        self,
        board_region_or_layerstack_id: object,
    ) -> tuple[AltiumStackLayer, ...]:
        """
        Return physical layers enabled for a board region or raw layerstack id.
        """
        if isinstance(board_region_or_layerstack_id, str):
            layerstack_id = board_region_or_layerstack_id
        else:
            layerstack_id = _text(
                getattr(board_region_or_layerstack_id, "layerstack_id", "")
            )
        return self.layers_for_substack(layerstack_id)

    def branches_for_stack_ref(
        self,
        stack_ref: str,
    ) -> tuple[AltiumStackBranch, ...]:
        """
        Return branch topology records that reference a stack GUID/ref.
        """
        normalized = _normalized_source_id(stack_ref)
        if not normalized:
            return ()
        matches: list[AltiumStackBranch] = []
        for branch in self.branches:
            if _normalized_source_id(branch.root_stack_ref) == normalized:
                matches.append(branch)
                continue
            if any(
                _normalized_source_id(stack.source_stack_ref) == normalized
                for section in branch.sections
                for stack in section.stacks
            ):
                matches.append(branch)
        return tuple(matches)

    def to_authored_board_data_entries(self) -> tuple[str, ...]:
        """
        Return ordered stack entries with typed model values overlaid.

        This is the first canonical-empty writer bridge. It preserves unknown
        source entries in order, while replacing fields that this model owns
        with values generated from typed objects.
        """
        authored_values = self._authored_stack_values()
        entries: list[str] = []
        emitted_keys: set[str] = set()
        for raw in self.source.board_stack_entry_texts:
            key = raw.split("=", 1)[0] if "=" in raw else raw
            if key in authored_values:
                entries.append(f"{key}={authored_values[key]}")
                emitted_keys.add(key)
            else:
                entries.append(raw)
                emitted_keys.add(key)
        _append_missing_authored_layer_pair_entries(
            entries,
            authored_values,
            emitted_keys,
        )
        return tuple(entries)

    def to_canonical_empty_board_data(self) -> "PcbDocBoardData":
        """
        Build canonical empty `Board6/Data` with stack entries from this model.

        The surrounding non-stack board scaffolding still comes from
        `PcbDocBoardData.default()`. Stack-owned entries are regenerated from
        the model where typed fields exist and otherwise preserved in source
        order.
        """
        from .altium_pcbdoc_builder import PcbDocBoardData

        return _replace_stack_entries_in_board_data(
            PcbDocBoardData.default(),
            self.to_authored_board_data_entries(),
        )

    def to_board_data_for_new_pcbdoc(
        self,
        *,
        base_board_data: "PcbDocBoardData | None" = None,
    ) -> "PcbDocBoardData":
        """
        Build new-document `Board6/Data` with stack sections from this model.

        Unlike canonical-empty synthesis, template-backed rigid stacks can
        change the number of physical stack rows. Replace the stack-core,
        layer-pair, and V9 cache entry ranges as grouped sections.
        """
        from .altium_pcbdoc_builder import PcbDocBoardData
        from .altium_pcbdoc_layer_stack_builder import (
            LAYER_STACK_CACHE_RE,
            is_layer_pair_entry_key,
            is_layer_stack_core_entry_key,
        )

        board_data = (
            base_board_data
            if base_board_data is not None
            else PcbDocBoardData.default()
        )
        authored_entries = self.to_authored_board_data_entries()
        if authored_entries and self.source.origin == "pcbdoc":
            updated = _replace_board_data_entry_section(
                board_data,
                is_layer_stack_source_entry_key,
                authored_entries,
                "Board6/Data layer stack segment not found",
            )
            for mode_entry in _rigid_flex_mode_board_record_entries(self):
                updated = _with_board_record_raw_entry(updated, mode_entry)
            return _with_top_level_bend_cache_entries(updated, self)

        semantic_entries = False
        if not authored_entries:
            support = self.native_pcbdoc_write_support()
            if not support.supported:
                details = "; ".join(support.reasons) or "unsupported stack shape"
                raise ValueError(
                    "Layer-stack document cannot be emitted as a new native "
                    f"PcbDoc with the current writer: {details}"
                )
            authored_entries = _semantic_rigid_board_data_entries(self)
            semantic_entries = True

        def cache_key_predicate(key: str | None) -> bool:
            return bool(key and LAYER_STACK_CACHE_RE.match(key))

        use_native_section_replacement = (
            semantic_entries
            or _native_pcbdoc_writer_mode(self) == "rigid_flex_new_pcbdoc"
            or self.source.origin == "pcbdoc"
        )
        stack_key_predicate = (
            _is_native_stack_section_entry_key
            if use_native_section_replacement
            else is_layer_stack_core_entry_key
        )
        core_entries = _filter_primary_stack_core_entries(
            authored_entries,
            stack_key_predicate,
            cache_key_predicate,
        )
        cache_entries = _filter_stack_entries(authored_entries, cache_key_predicate)
        pair_entries = _filter_stack_entries(authored_entries, is_layer_pair_entry_key)

        replacement = (
            _replace_board_data_entry_section
            if use_native_section_replacement
            else _replace_board_data_entry_range
        )
        updated = replacement(
            board_data,
            stack_key_predicate,
            core_entries,
            "Board6/Data layer stack segment not found",
        )
        updated = _replace_board_data_entry_range(
            updated,
            is_layer_pair_entry_key,
            pair_entries,
            "Board6/Data layer pair segment not found",
        )
        custom_entry = _stack_custom_data_entry(authored_entries)
        emit_stack_custom_data = (
            not custom_entry and _should_emit_authored_stack_custom_data(self)
        )
        updated = _merge_impedance_entries_in_board_data(
            updated,
            () if emit_stack_custom_data else self._authored_impedance_entry_strings(),
        )
        if cache_entries and not semantic_entries and self.source.origin == "pcbdoc":
            updated = _replace_board_data_entry_section(
                updated,
                cache_key_predicate,
                cache_entries,
                "Board6/Data V9 cache segment not found",
            )
        elif _native_pcbdoc_writer_mode(self) == "rigid_flex_new_pcbdoc":
            updated = _replace_v9_cache_entries_in_board_data(updated, self)
        else:
            updated = updated._with_v9_cache_names(_v9_cache_layer_names_by_id(self))
        if emit_stack_custom_data:
            custom_entry = _authored_stack_custom_data_entry(self)
        if custom_entry:
            updated = _with_board_record_raw_entry(updated, custom_entry)
        for mode_entry in _rigid_flex_mode_board_record_entries(self):
            updated = _with_board_record_raw_entry(updated, mode_entry)
        updated = _with_top_level_bend_cache_entries(updated, self)
        return updated

    def to_board_regions_data_for_new_pcbdoc(
        self,
        *,
        base_board_regions_data: "PcbDocBoardRegionsData | None" = None,
    ) -> "PcbDocBoardRegionsData":
        """
        Build new-document `BoardRegions/Data` from typed stack regions.
        """
        return _semantic_board_regions_data(
            self,
            base_board_regions_data=base_board_regions_data,
        )

    def to_stackup(self) -> "AltiumStackupDocument":
        """
        Build an Altium `.stackup` text interchange document from this model.
        """
        from .altium_stackup import AltiumStackupDocument

        return AltiumStackupDocument.from_layer_stack_document(self)

    def to_stackup_text(self) -> str:
        """
        Serialize this model as Altium `.stackup` text.
        """
        to_text = getattr(self.to_stackup(), "to_text", None)
        if not callable(to_text):
            raise TypeError("Stackup export document does not support to_text()")
        return str(to_text())

    def to_stackupx(self) -> "AltiumStackupXDocument":
        """
        Build an Altium `.stackupx` XML interchange document from this model.
        """
        from .altium_stackupx import AltiumStackupXDocument

        return AltiumStackupXDocument.from_layer_stack_document(self)

    def to_stackupx_text(self) -> str:
        """
        Serialize this model as Altium `.stackupx` XML text.
        """
        return self.to_stackupx().to_text()

    def to_resolved_layer_stack(
        self,
        *,
        primitive_layer_ids: set[int] | None = None,
        drill_pairs: set[tuple[int, int]] | None = None,
    ) -> object:
        """
        Build the existing resolved consumer view from this model's source data.
        """
        from .altium_resolved_layer_stack import (
            _resolved_layer_stack_from_layer_stack_document,
        )

        return _resolved_layer_stack_from_layer_stack_document(
            self,
            primitive_layer_ids=primitive_layer_ids,
            drill_pairs=drill_pairs,
        )

    def to_debug_json(self) -> dict[str, object]:
        """
        Return deterministic JSON-compatible debug data for tests and inspection.
        """
        source_json = self.source.to_debug_json()
        source_json["unsupported_source_field_buckets"] = {
            bucket: list(keys)
            for bucket, keys in self.unsupported_source_field_buckets().items()
        }
        return {
            "active_stack_ref": self.active_stack_ref,
            "rigid_flex_mode": self.rigid_flex_mode,
            "layer_registry": self.layer_registry.to_debug_json(),
            "physical_stacks": [
                stack.to_debug_json() for stack in self.physical_stacks
            ],
            "substacks": [substack.to_debug_json() for substack in self.substacks],
            "stackupx_auxiliary_stacks": [
                stack.to_debug_json() for stack in self.stackupx_auxiliary_stacks
            ],
            "branches": [branch.to_debug_json() for branch in self.branches],
            "board_regions": [region.to_debug_json() for region in self.board_regions],
            "layer_pairs": [pair.to_debug_json() for pair in self.layer_pairs],
            "board_split_lines": list(self.board_split_lines),
            "board_bending_lines": [
                line.to_debug_json() for line in self.board_bending_lines
            ],
            "board_arc_split_lines": list(self.board_arc_split_lines),
            "materials": list(self.materials),
            "impedance_profiles": [
                profile.to_debug_json() for profile in self.impedance_profiles
            ],
            "transmission_lines": [
                line.to_debug_json() for line in self.transmission_lines
            ],
            "stackup_attributes": [list(item) for item in self.stackup_attributes],
            "source": source_json,
        }

    def unsupported_source_field_buckets(self) -> dict[str, tuple[str, ...]]:
        """
        Return layer-stack source fields preserved but not yet typed-authored.

        These keys are native source evidence that the model keeps visible for
        future writer expansion. They are preserved in source streams where the
        current writer bridge can preserve them, but they are not yet promoted
        to typed fields owned by the model.
        """
        authored_keys = set(self._authored_stack_values())
        buckets: dict[str, set[str]] = {}
        for key, _value in self.source.board_record:
            if not is_layer_stack_source_entry_key(key) or key in authored_keys:
                continue
            bucket = _source_field_bucket(key)
            buckets.setdefault(bucket, set()).add(key)
        return {
            bucket: tuple(sorted(keys))
            for bucket, keys in sorted(buckets.items(), key=lambda item: item[0])
        }

    def native_pcbdoc_write_support(self) -> AltiumNativePcbDocWriteSupport:
        """
        Report whether this stack can be regenerated as native PcbDoc today.

        This answers the semantic-write question only. Existing parsed PcbDocs
        may still no-op preserve raw native streams even when this diagnostic
        reports unsupported semantic regeneration.
        """
        reasons = _native_pcbdoc_writer_problem_reasons(self)
        return AltiumNativePcbDocWriteSupport(
            supported=not reasons,
            mode=_native_pcbdoc_writer_mode(self) if not reasons else "unsupported",
            reasons=reasons,
            unsupported_source_field_buckets=tuple(
                sorted(self.unsupported_source_field_buckets().items())
            ),
        )

    def _authored_stack_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.physical_stacks:
            stack = self.physical_stacks[0]
            if stack.stack_ref:
                values["V9_MASTERSTACK_ID"] = stack.stack_ref
            if stack.display_name:
                values["V9_MASTERSTACK_NAME"] = stack.display_name
            if stack.is_flex is not None:
                values["V9_MASTERSTACK_ISFLEX"] = _format_bool_text(stack.is_flex)
            _add_authored_v9_stack_layer_values(values, stack.layers)

        for substack in self.substacks:
            _add_authored_substack_values(values, substack)
        for pair in self.layer_pairs:
            _add_authored_layer_pair_values(values, pair)
        return values

    def _authored_impedance_entry_strings(self) -> tuple[str, ...]:
        profiles = tuple(self.impedance_profiles or ())
        if not profiles:
            return ()
        if self._uses_stack_custom_data_impedance(profiles):
            return ()
        substack_id = _primary_substack_id(self)
        records: list[str] = []
        for profile in profiles:
            records.extend(_authored_impedance_profile_entries(profile))
        for line in tuple(self.transmission_lines or ()):
            records.extend(
                _authored_trace_impedance_entries(line=line, substack_id=substack_id)
            )
        return tuple(records)

    def _uses_stack_custom_data_impedance(
        self,
        profiles: Sequence[AltiumImpedanceProfile],
    ) -> bool:
        if self.source.origin != "pcbdoc":
            return False
        if not _stack_custom_data_entry(self.source.board_stack_entry_texts):
            return False
        return all(
            _text(profile.source_family).lower() == "stackupx" for profile in profiles
        )


def _with_authored_impedance_profiles(
    document: AltiumLayerStackDocument,
    specs: Sequence[AltiumImpedanceProfileSpec],
) -> AltiumLayerStackDocument:
    profile_specs = tuple(specs or ())
    if not profile_specs:
        return document
    stack = _single_physical_stack(document)
    layer_lookup = _impedance_authoring_layer_lookup(stack.layers)
    substack_id = _primary_substack_id(document)
    profiles: list[AltiumImpedanceProfile] = []
    lines: list[AltiumTransmissionLine] = []
    for profile_index, spec in enumerate(profile_specs):
        source_record_id = _authoring_guid(
            "impedance-profile",
            document.active_stack_ref,
            profile_index,
            spec.name,
        )
        line_specs = tuple(spec.transmission_lines or ())
        profile_type_raw = _profile_type_raw_from_spec(spec)
        profiles.append(
            AltiumImpedanceProfile(
                index=profile_index,
                source_family="authored",
                source_record_id=source_record_id,
                name=str(spec.name),
                profile_type=str(
                    spec.profile_type or _profile_type_from_raw(profile_type_raw)
                ),
                profile_type_raw=profile_type_raw,
                impedance=str(spec.impedance),
                tolerance=str(spec.tolerance),
                transmission_line_count=len(line_specs),
            )
        )
        for line_spec in line_specs:
            layer = _resolve_impedance_authoring_layer(layer_lookup, line_spec.layer)
            top_ref = _resolve_optional_impedance_authoring_layer(
                layer_lookup,
                line_spec.top_reference,
            )
            bottom_ref = _resolve_optional_impedance_authoring_layer(
                layer_lookup,
                line_spec.bottom_reference,
            )
            lines.append(
                AltiumTransmissionLine(
                    index=len(lines),
                    source_family="authored",
                    profile_id=source_record_id,
                    substack_id=substack_id,
                    layer_id=layer.source_record_id,
                    layer_token=_layer_pair_token(layer),
                    is_differential=line_spec.is_differential,
                    top_ref_id=top_ref.source_record_id if top_ref is not None else "",
                    top_ref_token=_layer_pair_token(top_ref)
                    if top_ref is not None
                    else "",
                    bottom_ref_id=(
                        bottom_ref.source_record_id if bottom_ref is not None else ""
                    ),
                    bottom_ref_token=_layer_pair_token(bottom_ref)
                    if bottom_ref is not None
                    else "",
                    width=str(line_spec.width),
                    gap=str(line_spec.gap),
                    calc_mode=str(line_spec.calc_mode),
                    calc_mode_raw=line_spec.calc_mode_raw,
                    impedance_error=str(line_spec.impedance_error),
                    tl_type_name=str(line_spec.tl_type_name),
                    has_plating=line_spec.has_plating,
                    use_solder_mask=line_spec.use_solder_mask,
                    coated_height_1=str(line_spec.coated_height_1),
                    coated_height_2=str(line_spec.coated_height_2),
                    clearance_to_plane=str(line_spec.clearance_to_plane),
                    electric_parameters=tuple(
                        (str(key), str(value))
                        for key, value in tuple(line_spec.electric_parameters or ())
                    ),
                )
            )
    return replace(
        document,
        impedance_profiles=tuple(profiles),
        transmission_lines=tuple(lines),
    )


def _single_physical_stack(document: AltiumLayerStackDocument) -> AltiumPhysicalStack:
    stacks = tuple(document.physical_stacks or ())
    if len(stacks) != 1:
        raise ValueError("Rigid impedance authoring requires one physical stack")
    stack = stacks[0]
    if bool(stack.is_flex):
        raise ValueError("Rigid impedance authoring does not support flex stacks")
    return stack


def _primary_substack_id(document: AltiumLayerStackDocument) -> str:
    substacks = tuple(document.substacks or ())
    if substacks and substacks[0].source_stackup_ref:
        return substacks[0].source_stackup_ref
    if document.active_stack_ref:
        return document.active_stack_ref
    return _authoring_guid("substack", "default")


def _profile_type_raw_from_spec(spec: AltiumImpedanceProfileSpec) -> int:
    if spec.profile_type_raw is not None:
        return int(spec.profile_type_raw)
    token = str(spec.profile_type or "").strip().lower()
    return 1 if token.startswith("diff") else 0


def _authoring_guid(*parts: object) -> str:
    seed = "|".join(str(part) for part in parts if str(part or ""))
    return "{" + str(uuid.uuid5(_AUTHORING_NAMESPACE, seed)).upper() + "}"


def _impedance_authoring_layer_lookup(
    layers: tuple[AltiumStackLayer, ...],
) -> dict[str, AltiumStackLayer]:
    result: dict[str, AltiumStackLayer] = {}
    for layer in layers:
        if layer.family != "copper" or not layer.source_record_id:
            continue
        for key in (
            _layer_pair_token(layer),
            layer.display_name,
            str(layer.legacy_layer_id or ""),
            str(layer.layer_id or ""),
        ):
            normalized = _normalize_impedance_selector(key)
            if normalized:
                result.setdefault(normalized, layer)
    return result


def _resolve_impedance_authoring_layer(
    lookup: Mapping[str, AltiumStackLayer],
    selector: str,
) -> AltiumStackLayer:
    normalized = _normalize_impedance_selector(selector)
    if normalized and normalized in lookup:
        return lookup[normalized]
    allowed = ", ".join(sorted(key for key in lookup if key))
    raise ValueError(
        f"Unknown impedance layer selector {selector!r}; expected one of: {allowed}"
    )


def _resolve_optional_impedance_authoring_layer(
    lookup: Mapping[str, AltiumStackLayer],
    selector: str,
) -> AltiumStackLayer | None:
    if not str(selector or "").strip():
        return None
    return _resolve_impedance_authoring_layer(lookup, selector)


def _normalize_impedance_selector(value: object) -> str:
    token = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9]+", "", token)


def _authored_impedance_profile_entries(
    profile: AltiumImpedanceProfile,
) -> tuple[str, ...]:
    prefix = f"IMPEDANCEPROFILE_V8_{profile.index}"
    return (
        f"{prefix}ID={profile.source_record_id}",
        f"{prefix}NAME={profile.name}",
        f"{prefix}TYPE={_profile_type_raw_from_model(profile)}",
        f"{prefix}IMPEDANCE={profile.impedance}",
        f"{prefix}TOLERANCE={profile.tolerance}",
    )


def _authored_trace_impedance_entries(
    *,
    line: AltiumTransmissionLine,
    substack_id: str,
) -> tuple[str, ...]:
    prefix = f"TRACEIMPEDANCE_V8_{line.index}"
    entries: list[str] = [
        f"{prefix}LAYERID={line.layer_id}",
        f"{prefix}SUBSTACKID={line.substack_id or substack_id}",
        f"{prefix}PROFILEID={line.profile_id}",
    ]
    _append_authored_optional_bool(
        entries, f"{prefix}DIFFERENTIAL", line.is_differential
    )
    if line.top_ref_id:
        entries.append(f"{prefix}TOPREFID={line.top_ref_id}")
    if line.bottom_ref_id:
        entries.append(f"{prefix}BOTREFID={line.bottom_ref_id}")
    _append_authored_optional_text(entries, f"{prefix}WIDTH", line.width)
    _append_authored_optional_text(entries, f"{prefix}GAP", line.gap)
    _append_authored_optional_text(
        entries,
        f"{prefix}CALCMODE",
        str(line.calc_mode_raw) if line.calc_mode_raw is not None else line.calc_mode,
    )
    _append_authored_optional_text(
        entries,
        f"{prefix}IMPEDANCEERROR",
        line.impedance_error,
    )
    _append_authored_optional_text(entries, f"{prefix}TLTYPENAME", line.tl_type_name)
    _append_authored_optional_bool(entries, f"{prefix}HASPLATING", line.has_plating)
    _append_authored_optional_bool(
        entries,
        f"{prefix}USESOLDERMASK",
        line.use_solder_mask,
    )
    _append_authored_optional_text(
        entries,
        f"{prefix}COATEDHEIGHT1",
        line.coated_height_1,
    )
    _append_authored_optional_text(
        entries,
        f"{prefix}COATEDHEIGHT2",
        line.coated_height_2,
    )
    _append_authored_optional_text(
        entries,
        f"{prefix}CLEARANCETOPLANE",
        line.clearance_to_plane,
    )
    for key, value in tuple(line.electric_parameters or ()):
        entries.append(f"{prefix}_ELECTRICPARAMS_{key}={value}")
    return tuple(entries)


def _append_authored_optional_text(
    entries: list[str],
    key: str,
    value: object,
) -> None:
    token = str(value or "")
    if token:
        entries.append(f"{key}={token}")


def _append_authored_optional_bool(
    entries: list[str],
    key: str,
    value: bool | None,
) -> None:
    if value is not None:
        entries.append(f"{key}={_format_bool_text(value)}")


def _profile_type_raw_from_model(profile: AltiumImpedanceProfile) -> int:
    if profile.profile_type_raw is not None:
        return int(profile.profile_type_raw)
    token = str(profile.profile_type or "").strip().lower()
    return 1 if token.startswith("diff") else 0


def _merge_impedance_entries_in_board_data(
    board_data: "PcbDocBoardData",
    entry_strings: tuple[str, ...],
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import (
        PcbDocBoardDataEntry,
        PcbDocBoardDataSegment,
    )
    from .altium_pcbdoc_layer_stack_builder import is_layer_pair_entry_key

    replacement_entries = tuple(
        PcbDocBoardDataEntry(raw=entry) for entry in entry_strings
    )

    def entry_is_impedance(entry: object) -> bool:
        return _is_impedance_entry_key(getattr(entry, "key", None))

    segments = tuple(getattr(board_data, "segments", ()) or ())
    for segment_index, segment in enumerate(segments):
        if any(entry_is_impedance(entry) for entry in tuple(segment.entries)):
            return board_data.with_segment(
                segment_index,
                segment.with_replaced_entries(entry_is_impedance, replacement_entries),
            )

    if not replacement_entries:
        return board_data

    for segment_index, segment in enumerate(segments):
        entries = list(tuple(segment.entries))
        pair_indexes = [
            index
            for index, entry in enumerate(entries)
            if is_layer_pair_entry_key(getattr(entry, "key", None))
        ]
        if not pair_indexes:
            continue
        insert_at = max(pair_indexes) + 1
        entries[insert_at:insert_at] = replacement_entries
        return board_data.with_segment(
            segment_index,
            PcbDocBoardDataSegment(
                entries=tuple(entries),
                leading_pipe=bool(getattr(segment, "leading_pipe", True)),
            ),
        )
    raise KeyError("Board6/Data layer pair segment not found for impedance rows")


def _is_impedance_entry_key(key: str | None) -> bool:
    upper = str(key or "").upper()
    return upper.startswith(("IMPEDANCEPROFILE_V8_", "TRACEIMPEDANCE_V8_"))


def _source_field_bucket(key: str) -> str:
    upper = key.upper()
    if upper.startswith("V9_MASTERSTACK_"):
        return "v9_master_stack"
    if upper.startswith("V9_SUBSTACK"):
        return "v9_substack"
    if _V9_STACK_RE.match(key):
        return "v9_stack_layer"
    if _V9_CACHE_RE.match(key):
        return "v9_cache_layer"
    if upper.startswith("LAYERMASTERSTACK_V8"):
        return "v8_master_stack"
    if upper.startswith("LAYERSUBSTACK_V8_"):
        return "v8_substack"
    if _LAYER_V8_RE.match(key):
        return "v8_layer"
    if upper.startswith("VIASPAN_V8_"):
        return "v8_via_span"
    if upper.startswith(("DRILLPAIR_V8_", "DRILLSPAN_V8_")):
        return "v8_drill_pair"
    if upper.startswith("IMPEDANCEPROFILE_V8_"):
        return "v8_impedance_profile"
    if upper.startswith("TRACEIMPEDANCE_V8_"):
        return "v8_trace_impedance"
    if upper.startswith("LAYERPAIR"):
        return "layer_pair"
    if _LEGACY_LAYER_RE.match(key):
        return "legacy_layer"
    return "other_stack"


def _rigid_copper_legacy_layer_id(index: int, layer_count: int) -> int:
    if index == 0:
        return PcbLayer.TOP.value
    if index == layer_count - 1:
        return PcbLayer.BOTTOM.value
    return PcbLayer.TOP.value + index


def _rigid_copper_v7_layer_id(index: int, layer_count: int) -> int:
    legacy_layer_id = _rigid_copper_legacy_layer_id(index, layer_count)
    return _v7_layer_id_from_legacy_copper(legacy_layer_id)


def _v7_layer_id_from_legacy_copper(legacy_layer_id: int) -> int:
    if legacy_layer_id == PcbLayer.BOTTOM.value:
        return 0x0100FFFF
    return 0x01000000 + legacy_layer_id


def _rigid_component_placement(
    spec: AltiumRigidCopperLayerSpec,
    index: int,
    layer_count: int,
) -> int:
    if spec.component_placement is not None:
        return int(spec.component_placement)
    if index == 0:
        return 1
    if index == layer_count - 1:
        return 2
    return 0


def _native_pcbdoc_writer_problem_reasons(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    reasons: list[str] = []
    stacks = tuple(document.physical_stacks or ())
    if len(stacks) != 1:
        reasons.append("native writer requires exactly one physical stack")
    else:
        stack = stacks[0]
        reasons.extend(_rigid_stack_layer_problem_reasons(stack))

    substacks = tuple(document.substacks or ())
    board_regions = tuple(document.board_regions or ())
    if len(substacks) > 1 and not board_regions:
        reasons.append("native writer requires board regions for multiple substacks")
    for region in board_regions:
        if len(region.outline_vertices) < 3:
            reasons.append("native writer requires region outline geometry")
            break
    return tuple(reasons)


def _native_pcbdoc_writer_mode(document: AltiumLayerStackDocument) -> str:
    if len(tuple(document.substacks or ())) > 1 or any(
        bool(substack.is_flex) for substack in tuple(document.substacks or ())
    ):
        return "rigid_flex_new_pcbdoc"
    if len(tuple(document.board_regions or ())) > 1 or any(
        region.bending_lines for region in tuple(document.board_regions or ())
    ):
        return "rigid_flex_new_pcbdoc"
    return "rigid_new_pcbdoc"


def _rigid_stack_layer_problem_reasons(stack: AltiumPhysicalStack) -> tuple[str, ...]:
    reasons: list[str] = []
    copper_positions = _copper_layer_positions(stack)
    if len(copper_positions) < 2:
        reasons.append("rigid native writer requires at least two copper layers")
    if len(copper_positions) > 32:
        reasons.append("rigid native writer supports at most 32 copper layers")
    for left, right in zip(copper_positions, copper_positions[1:]):
        if not any(
            layer.family == "dielectric" for layer in stack.layers[left + 1 : right]
        ):
            reasons.append(
                "rigid native writer requires dielectric rows between copper layers"
            )
            break
    return tuple(reasons)


def _semantic_rigid_board_data_entries(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    from .altium_pcbdoc_builder import PcbDocBoardData
    from .altium_pcbdoc_layer_stack_builder import (
        is_layer_pair_entry_key,
    )

    board_data = PcbDocBoardData.default()
    substack_guid = board_data.default_substack_guid()
    substacks = _semantic_substacks(document, substack_guid)
    stack_entries = _semantic_legacy_layer_entry_strings(
        document
    ) + _semantic_native_stack_entry_strings(document, substacks)
    board_data = _replace_board_data_entry_range(
        board_data,
        _is_native_stack_section_entry_key,
        stack_entries,
        "Board6/Data layer stack segment not found",
    )
    board_data = _replace_board_data_entry_range(
        board_data,
        is_layer_pair_entry_key,
        _semantic_layer_pair_entry_strings(document, substacks),
        "Board6/Data layer pair segment not found",
    )
    return _ordered_stack_entries_from_board_data(board_data)


def _semantic_layer_pair_entry_strings(
    document: AltiumLayerStackDocument,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    pairs = tuple(document.layer_pairs or ())
    default_substack_refs = tuple(
        substack.source_stackup_ref
        for substack in substacks
        if substack.source_stackup_ref
    )
    if not pairs:
        pairs = (
            AltiumLayerPair(
                pair_index=0,
                low_layer_token="TOP",
                high_layer_token="BOTTOM",
                source_substack_refs=default_substack_refs,
                drill_guide=False,
                drill_drawing=False,
            ),
        )
    entries: list[str] = []
    for index, pair in enumerate(pairs):
        rewritten_pair = replace(
            pair,
            pair_index=index,
            source_substack_refs=pair.source_substack_refs or default_substack_refs,
        )
        values: dict[str, str] = {}
        _add_authored_layer_pair_values(values, rewritten_pair)
        entries.extend(f"{key}={value}" for key, value in values.items())
    return tuple(entries)


def _semantic_legacy_layer_entry_strings(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    from .altium_pcbdoc_layer_stack_builder import PcbDocLayerStackBuilder

    return PcbDocLayerStackBuilder.build_legacy_layer_entry_strings(
        _rigid_stack_template_from_document(document)
    )


def _is_native_stack_section_entry_key(key: str | None) -> bool:
    from .altium_pcbdoc_layer_stack_builder import is_layer_stack_core_entry_key

    upper = str(key or "").upper()
    return (
        is_layer_stack_core_entry_key(key)
        or upper.startswith("V9_MASTERSTACK_")
        or upper.startswith("V9_SUBSTACK")
        or upper.startswith("LAYERMASTERSTACK_V8")
        or upper.startswith("LAYERSUBSTACK_V8_")
        or bool(_LAYER_V8_RE.match(str(key or "")))
    )


def _semantic_native_stack_entry_strings(
    document: AltiumLayerStackDocument,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    return (
        _semantic_master_stack_entry_strings(document)
        + _semantic_substack_entry_strings(substacks)
        + _semantic_v9_stack_entry_strings(document, substacks)
        + _semantic_v8_lsm_board_entry_strings(document, substacks)
    )


def _semantic_v8_lsm_board_entry_strings(
    document: AltiumLayerStackDocument,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    """
    Return V8 Layer Stack Manager rows needed by native PcbDoc authoring.

    AD26 still writes and consumes these Board6/Data rows for the opened board
    editor, even when the richer V9 StackupX cache is present. This Board6
    table is intentionally not the same as the `.stackup` export: PcbDoc keeps
    paste and editor-only layer rows in the V8 table, while `.stackup` omits
    those rows. Branch topology remains in V9_STACKCUSTOMDATA; AD-exported
    PcbDoc fixtures do not carry BRANCH_V8 rows in Board6/Data.
    """
    if not document.physical_stacks:
        return ()
    stack = document.physical_stacks[0]
    v8_substacks = tuple(
        replace(
            substack,
            index=index,
            field_family="v8",
            raw_stackup_type=substack.raw_stackup_type
            or ("1" if substack.is_flex else "0"),
        )
        for index, substack in enumerate(substacks)
    )
    return (
        _semantic_v8_master_stack_entry_strings(document)
        + _semantic_substack_entry_strings(v8_substacks)
        + _semantic_v8_board_layer_entry_strings(stack, v8_substacks)
    )


def _semantic_v8_master_stack_entry_strings(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    stack = document.physical_stacks[0]
    stack_ref = (
        stack.stack_ref
        or document.active_stack_ref
        or _authoring_guid("v8-master-stack", stack.display_name)
    )
    return (
        "LAYERMASTERSTACK_V8STYLE=0",
        f"LAYERMASTERSTACK_V8ID={stack_ref}",
        "LAYERMASTERSTACK_V8NAME=Master layer stack",
        "LAYERMASTERSTACK_V8SHOWTOPDIELECTRIC=FALSE",
        "LAYERMASTERSTACK_V8SHOWBOTTOMDIELECTRIC=FALSE",
        f"LAYERMASTERSTACK_V8ISFLEX={_format_bool_text(bool(stack.is_flex))}",
    )


def _semantic_v8_board_layer_entry_strings(
    stack: AltiumPhysicalStack,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    entries: list[str] = []
    layers = _native_pcbdoc_board_layer_rows(tuple(stack.layers or ()))
    for index, layer in enumerate(layers):
        entries.extend(_semantic_v8_board_layer_entries(layer, index, substacks))
    entries.extend(_semantic_v8_editor_tail_entries(len(layers)))
    entries.extend(
        _semantic_v8_stiffener_coverlay_tail_entries(
            start_index=_next_v8_layer_index(entries),
            stack=stack,
            substacks=substacks,
        )
    )
    return tuple(entries)


def _native_pcbdoc_board_layer_rows(
    layers: Sequence[AltiumStackLayer],
) -> tuple[AltiumStackLayer, ...]:
    return tuple(
        layer
        for layer in tuple(layers or ())
        if _native_pcbdoc_emits_board_layer_row(layer)
    )


def _native_pcbdoc_emits_board_layer_row(layer: AltiumStackLayer) -> bool:
    # AD-authored PcbDocs keep surface-finish rows in V9_STACKCUSTOMDATA rather
    # than ordinary Board6 V8/V9/cache layer tables.
    return str(layer.family or "").strip().lower() != "surface_finish"


def _semantic_kvl_type_id_for_layer(layer: AltiumStackLayer) -> str:
    family = str(layer.family or "").strip().lower()
    if layer.is_stiffener is True:
        return _braced_schema_type_id("core")
    if layer.is_adhesive is True or family in {"plating", "misc", "marking"}:
        return _braced_schema_type_id("prepreg")
    if family == "surface_finish":
        return _braced_schema_type_id("surface_finish")
    if family == "overlay":
        return _braced_schema_type_id("overlay")
    if family == "solder_mask":
        return _braced_schema_type_id("solder_mask")
    if family == "copper":
        if (
            layer.legacy_layer_id is not None
            and PcbLayer.INTERNAL_PLANE_1.value
            <= int(layer.legacy_layer_id)
            <= PcbLayer.INTERNAL_PLANE_16.value
        ):
            return _braced_schema_type_id("copper_plane")
        return _braced_schema_type_id("copper_signal")
    if family == "dielectric":
        if layer.dielectric_type == 2:
            return _braced_schema_type_id("prepreg")
        if layer.dielectric_type == 0:
            return _braced_schema_type_id("core")
        return _braced_schema_type_id("dielectric")
    return _braced_schema_type_id("dielectric")


def _braced_schema_type_id(name: str) -> str:
    return "{" + _LAYER_SCHEMA_TYPE_IDS[name].upper() + "}"


def _semantic_advanced_layer_flag_entries(
    prefix: str,
    layer: AltiumStackLayer,
) -> tuple[str, ...]:
    entries: list[str] = []
    family = str(layer.family or "").strip().lower()
    if layer.is_stiffener is not None:
        entries.append(f"{prefix}ISSTIFFENER={_format_bool_text(layer.is_stiffener)}")
    if layer.is_adhesive is not None:
        entries.append(f"{prefix}ISADHESIVE={_format_bool_text(layer.is_adhesive)}")
    flag = _ADVANCED_LAYER_FLAGS.get(family)
    if flag is not None:
        entries.append(f"{prefix}{flag}=TRUE")
        entries.append(f"{prefix}ISADVANCED=TRUE")
    return tuple(entries)


def _semantic_stackupx_property_entries(
    prefix: str,
    layer: AltiumStackLayer,
) -> tuple[str, ...]:
    entries: list[str] = []
    for name, _type_name, value in tuple(layer.stackupx_properties or ()):
        property_name = str(name or "").strip()
        if not property_name:
            continue
        entries.append(f"{prefix}$LSM${property_name}={value}")
    return tuple(entries)


def _semantic_v8_board_layer_entries(
    layer: AltiumStackLayer,
    index: int,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    prefix = f"LAYER_V8_{index}"
    used_by_primitives = (
        _format_bool_text(layer.used_by_primitives)
        if layer.used_by_primitives is not None
        else "FALSE"
    )
    entries: list[str] = []
    for substack in substacks:
        substack_ref = substack.source_stackup_ref
        context = _semantic_substack_context_value(layer, substack)
        entries.append(f"{prefix}_{substack_ref}CONTEXT={context}")
        entries.append(f"{prefix}_{substack_ref}USEDBYPRIMS={used_by_primitives}")
    entries.append(
        f"{prefix}ID="
        f"{layer.source_record_id or _authoring_guid('v8-board-layer', index, layer.display_name)}"
    )
    if layer.display_name:
        entries.append(f"{prefix}NAME={layer.display_name}")
    layer_id = _semantic_v9_layer_id(layer, index)
    if layer_id is not None:
        entries.append(f"{prefix}LAYERID={int(layer_id)}")
    entries.append(f"{prefix}TYPEID={_semantic_kvl_type_id_for_layer(layer)}")
    entries.append(f"{prefix}USEDBYPRIMS={used_by_primitives}")
    if layer.copper_thickness_mils is not None:
        entries.append(
            f"{prefix}COPTHICK={_format_mil_value(layer.copper_thickness_mils)}"
        )
    if layer.component_placement is not None:
        entries.append(f"{prefix}COMPONENTPLACEMENT={int(layer.component_placement)}")
    if layer.dielectric_type is not None:
        entries.append(f"{prefix}DIELTYPE={int(layer.dielectric_type)}")
    if layer.dielectric_constant is not None:
        entries.append(
            f"{prefix}DIELCONST={format(float(layer.dielectric_constant), '.3f')}"
        )
    if layer.dielectric_loss_tangent is not None:
        entries.append(
            f"{prefix}DIELLOSSTANGENT="
            f"{format(float(layer.dielectric_loss_tangent), '.3f')}"
        )
    if layer.dielectric_height_mils is not None:
        entries.append(
            f"{prefix}DIELHEIGHT={_format_mil_value(layer.dielectric_height_mils)}"
        )
    if layer.dielectric_material:
        entries.append(f"{prefix}DIELMATERIAL={layer.dielectric_material}")
    if layer.coverlay_expansion:
        entries.append(f"{prefix}COVERLAY_EXPANSION={layer.coverlay_expansion}")
    entries.extend(_semantic_advanced_layer_flag_entries(prefix, layer))
    entries.extend(_semantic_stackupx_property_entries(prefix, layer))
    return tuple(entries)


def _semantic_v8_editor_tail_entries(start_index: int) -> tuple[str, ...]:
    from .altium_pcbdoc_builder import PcbDocBoardData

    default_entries = _ordered_stack_entries_from_board_data(PcbDocBoardData.default())
    default_layer_names: dict[int, str] = {}
    for raw in default_entries:
        key, _separator, value = raw.partition("=")
        match = _LAYER_V8_RE.match(key)
        if match is None or match.group(2).upper() != "NAME":
            continue
        default_layer_names[int(match.group(1))] = value
    tail_start = next(
        (
            index
            for index, name in sorted(default_layer_names.items())
            if name == "Drill Guide"
        ),
        None,
    )
    if tail_start is None:
        return ()

    entries: list[str] = []
    for raw in default_entries:
        key, _separator, value = raw.partition("=")
        match = _LAYER_V8_RE.match(key)
        if match is None:
            continue
        source_index = int(match.group(1))
        if source_index < tail_start:
            continue
        suffix = match.group(2)
        target_index = start_index + source_index - tail_start
        separator = "_" if suffix.startswith("{") else ""
        entries.append(f"LAYER_V8_{target_index}{separator}{suffix}={value}")
    return tuple(entries)


def _next_v8_layer_index(entries: Sequence[str]) -> int:
    indexes = []
    for raw in entries:
        key, _separator, _value = raw.partition("=")
        match = _LAYER_V8_RE.match(key)
        if match is not None:
            indexes.append(int(match.group(1)))
    return max(indexes, default=-1) + 1


def _semantic_v8_stiffener_coverlay_tail_entries(
    *,
    start_index: int,
    stack: AltiumPhysicalStack,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    if not any(layer.is_stiffener is True for layer in tuple(stack.layers or ())):
        return ()
    coverlay_layers = tuple(
        layer
        for layer in tuple(stack.layers or ())
        if layer.coverlay_expansion and layer.family == "overlay"
    )
    if not coverlay_layers:
        return ()

    entries: list[str] = []
    next_layer_id = 16972818
    index = start_index
    for substack in substacks:
        for layer in coverlay_layers:
            display_name = f"{layer.display_name} ({substack.name})"
            entries.extend(
                (
                    f"LAYER_V8_{index}ID="
                    f"{_authoring_guid('v8-coverlay-tail', substack.source_stackup_ref, layer.display_name)}",
                    f"LAYER_V8_{index}NAME={display_name}",
                    f"LAYER_V8_{index}LAYERID={next_layer_id}",
                    f"LAYER_V8_{index}USEDBYPRIMS=TRUE",
                    f"LAYER_V8_{index}MECHENABLED=TRUE",
                )
            )
            index += 1
            next_layer_id += 1
    return tuple(entries)


def _semantic_master_stack_entry_strings(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    if not document.physical_stacks:
        return ()
    stack = document.physical_stacks[0]
    entries: list[str] = []
    if stack.stack_ref:
        entries.append(f"V9_MASTERSTACK_ID={stack.stack_ref}")
    if stack.display_name:
        entries.append(f"V9_MASTERSTACK_NAME={stack.display_name}")
    if stack.is_flex is not None:
        entries.append(f"V9_MASTERSTACK_ISFLEX={_format_bool_text(stack.is_flex)}")
    return tuple(entries)


def _semantic_substacks(
    document: AltiumLayerStackDocument,
    default_substack_guid: str,
) -> tuple[AltiumStackSubstack, ...]:
    if document.substacks:
        return tuple(
            replace(substack, index=index, field_family="v9")
            for index, substack in enumerate(document.substacks)
        )
    return (
        AltiumStackSubstack(
            index=0,
            field_family="v9",
            source_stackup_ref=default_substack_guid,
            name="Board Layer Stack",
            is_flex=False,
            show_top_dielectric=False,
            show_bottom_dielectric=False,
            service_stackup=False,
            used_by_primitives=False,
            raw_stackup_type="0",
        ),
    )


def _semantic_substack_entry_strings(
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    entries: list[str] = []
    for substack in substacks:
        values: dict[str, str] = {}
        _add_authored_substack_values(values, substack)
        entries.extend(f"{key}={value}" for key, value in values.items())
    return tuple(entries)


def _semantic_v9_stack_entry_strings(
    document: AltiumLayerStackDocument,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[str, ...]:
    stack = document.physical_stacks[0]
    entries: list[str] = []
    layers = _native_pcbdoc_board_layer_rows(tuple(stack.layers or ()))
    for index, layer in enumerate(layers):
        prefix = f"V9_STACK_LAYER{index}_"
        for key, value in _semantic_v9_stack_layer_fields(
            layer,
            index,
            substacks,
        ):
            entries.append(f"{prefix}{key}={value}")
    return tuple(entries)


def _semantic_v9_stack_layer_fields(
    layer: AltiumStackLayer,
    index: int,
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[tuple[str, str], ...]:
    used_by_primitives = (
        _format_bool_text(layer.used_by_primitives)
        if layer.used_by_primitives is not None
        else "FALSE"
    )
    fields: list[tuple[str, str]] = [
        (
            "ID",
            layer.source_record_id
            or _authoring_guid("v9-stack-layer", index, layer.display_name),
        ),
        ("USEDBYPRIMS", used_by_primitives),
    ]
    for substack in substacks:
        substack_ref = substack.source_stackup_ref
        context = _semantic_substack_context_value(layer, substack)
        fields.append((f"{substack_ref}CONTEXT", str(context)))
        fields.append((f"{substack_ref}USEDBYPRIMS", used_by_primitives))
    layer_id = _semantic_v9_layer_id(layer, index)
    if layer_id is not None:
        fields.append(("LAYERID", str(int(layer_id))))
    fields.append(("TYPEID", _semantic_kvl_type_id_for_layer(layer)))
    if layer.display_name:
        fields.append(("NAME", layer.display_name))
    if layer.component_placement is not None:
        fields.append(("COMPONENTPLACEMENT", str(int(layer.component_placement))))
    if layer.copper_thickness_mils is not None:
        fields.append(("COPTHICK", _format_mil_value(layer.copper_thickness_mils)))
    if layer.coverlay_expansion:
        fields.append(("COVERLAY_EXPANSION", layer.coverlay_expansion))
    for entry in _semantic_advanced_layer_flag_entries("", layer):
        key, _separator, value = entry.partition("=")
        fields.append((key, value))
    if layer.dielectric_constant is not None:
        fields.append(("DIELCONST", format(float(layer.dielectric_constant), ".3f")))
    if layer.dielectric_height_mils is not None:
        fields.append(("DIELHEIGHT", _format_mil_value(layer.dielectric_height_mils)))
    if layer.dielectric_material:
        fields.append(("DIELMATERIAL", layer.dielectric_material))
    if layer.dielectric_type is not None:
        fields.append(("DIELTYPE", str(int(layer.dielectric_type))))
    if layer.dielectric_loss_tangent is not None:
        fields.append(
            ("DIELLOSSTANGENT", format(float(layer.dielectric_loss_tangent), ".3f"))
        )
    for entry in _semantic_stackupx_property_entries("", layer):
        key, _separator, value = entry.partition("=")
        fields.append((key, value))
    return tuple(fields)


def _semantic_substack_context_value(
    layer: AltiumStackLayer,
    substack: AltiumStackSubstack,
) -> int:
    substack_ref = _normalized_source_id(substack.source_stackup_ref)
    for context_ref, context_value in layer.substack_contexts:
        if _normalized_source_id(context_ref) == substack_ref:
            return int(context_value) if context_value is not None else 0
    if substack.enabled_layer_refs:
        return 0 if layer.registry_ref in substack.enabled_layer_refs else 1
    return 0


def _layer_has_substack_context(
    layer: AltiumStackLayer,
    source_stackup_ref: str,
) -> bool:
    ref = _normalized_source_id(source_stackup_ref)
    if not ref:
        return False
    return any(
        _normalized_source_id(context_ref) == ref
        for context_ref, _context_value in layer.substack_contexts
    )


def _semantic_v9_layer_id(layer: AltiumStackLayer, index: int) -> int | None:
    if layer.layer_id is not None:
        return int(layer.layer_id)
    if layer.family == "copper":
        if layer.legacy_layer_id is not None:
            return _v7_layer_id_from_legacy_copper(int(layer.legacy_layer_id))
        copper_index = max(0, index - 3)
        return _rigid_copper_v7_layer_id(copper_index, copper_index + 2)
    if layer.family == "dielectric":
        return 17039361 + max(0, index)
    if layer.family == "surface_finish":
        return _SURFACE_FINISH_LAYER_ID
    return None


def _rigid_stack_template_from_document(
    document: AltiumLayerStackDocument,
) -> "PcbDocLayerStackTemplate":
    from .altium_pcbdoc_layer_stack_builder import (
        PcbDocCopperLayerTemplate,
        PcbDocDielectricTemplate,
        PcbDocLayerStackTemplate,
    )

    problems = _native_pcbdoc_writer_problem_reasons(document)
    if problems:
        raise ValueError("; ".join(problems))

    stack = document.physical_stacks[0]
    copper_layers = _copper_layers_for_template(stack)
    dielectric_layers = _dielectric_layers_between_copper(stack)
    layer_pair = document.layer_pairs[0] if document.layer_pairs else None
    return PcbDocLayerStackTemplate(
        name=stack.display_name or "model-rigid-stack",
        copper_layers=tuple(
            PcbDocCopperLayerTemplate(
                legacy_layer_id=_layer_legacy_id_for_rigid_template(
                    layer,
                    index,
                    len(copper_layers),
                ),
                v7_layer_id=_layer_v7_id_for_rigid_template(
                    layer,
                    index,
                    len(copper_layers),
                ),
                name=layer.display_name,
                copper_thickness_mils=float(layer.copper_thickness_mils or 1.4),
                component_placement=(
                    int(layer.component_placement)
                    if layer.component_placement is not None
                    else _rigid_component_placement(
                        AltiumRigidCopperLayerSpec(layer.display_name),
                        index,
                        len(copper_layers),
                    )
                ),
            )
            for index, layer in enumerate(copper_layers)
        ),
        dielectrics_between=tuple(
            PcbDocDielectricTemplate(
                name=layer.display_name,
                thickness_mils=float(layer.dielectric_height_mils or 0.0),
                dielectric_constant=float(layer.dielectric_constant or 4.8),
                material=layer.dielectric_material or "FR-4",
                dielectric_type=int(layer.dielectric_type or 0),
                loss_tangent=layer.dielectric_loss_tangent,
            )
            for layer in dielectric_layers
        ),
        default_layer_pair_low=(
            layer_pair.low_layer_token if layer_pair is not None else "TOP"
        ),
        default_layer_pair_high=(
            layer_pair.high_layer_token if layer_pair is not None else "BOTTOM"
        ),
    )


def _copper_layer_positions(stack: AltiumPhysicalStack) -> tuple[int, ...]:
    return tuple(
        index for index, layer in enumerate(stack.layers) if layer.family == "copper"
    )


def _copper_layers_for_template(
    stack: AltiumPhysicalStack,
) -> tuple[AltiumStackLayer, ...]:
    return tuple(layer for layer in stack.layers if layer.family == "copper")


def _dielectric_layers_between_copper(
    stack: AltiumPhysicalStack,
) -> tuple[AltiumStackLayer, ...]:
    positions = _copper_layer_positions(stack)
    dielectrics: list[AltiumStackLayer] = []
    for left, right in zip(positions, positions[1:]):
        dielectric = next(
            (
                layer
                for layer in stack.layers[left + 1 : right]
                if layer.family == "dielectric"
            ),
            None,
        )
        if dielectric is not None:
            dielectrics.append(dielectric)
    return tuple(dielectrics)


def _layer_legacy_id_for_rigid_template(
    layer: AltiumStackLayer,
    index: int,
    layer_count: int,
) -> int:
    if layer.legacy_layer_id is not None:
        return int(layer.legacy_layer_id)
    return _rigid_copper_legacy_layer_id(index, layer_count)


def _layer_v7_id_for_rigid_template(
    layer: AltiumStackLayer,
    index: int,
    layer_count: int,
) -> int:
    if layer.layer_id is not None:
        return int(layer.layer_id)
    return _rigid_copper_v7_layer_id(index, layer_count)


def _semantic_board_regions_data(
    document: AltiumLayerStackDocument,
    *,
    base_board_regions_data: "PcbDocBoardRegionsData | None" = None,
) -> "PcbDocBoardRegionsData":
    from .altium_pcbdoc_builder import PcbDocBoardRegionsData

    if base_board_regions_data is None:
        base_board_regions_data = PcbDocBoardRegionsData.default()
    if not document.board_regions:
        return base_board_regions_data
    if not base_board_regions_data.records:
        raise ValueError("BoardRegions/Data has no template region")
    template = base_board_regions_data.records[0]
    synthesize_extended_outline_tail = (
        _normalized_rigid_flex_mode(document.rigid_flex_mode) != "standard"
    )
    return PcbDocBoardRegionsData(
        records=tuple(
            _semantic_board_region_record(
                template,
                region,
                synthesize_extended_outline_tail=synthesize_extended_outline_tail,
            )
            for region in document.board_regions
        )
    )


def _semantic_board_region_record(
    template: "AltiumPcbBoardRegion",
    region: AltiumStackRegion,
    *,
    synthesize_extended_outline_tail: bool = True,
) -> "AltiumPcbBoardRegion":
    from .altium_record_pcb__board_region import (
        AltiumPcbBendingLine,
        AltiumPcbBoardRegion,
    )
    from .altium_record_pcb__region import RegionVertex

    record = AltiumPcbBoardRegion()
    for key, value in vars(template).items():
        setattr(record, key, value)
    record.name = region.name
    record.layerstack_id = region.layerstack_id
    record.locked_3d = region.locked_3d
    record.is_shapebased = region.is_shapebased
    record.cavity_height = region.cavity_height
    record.layer_token = region.layer_token or getattr(record, "layer_token", "")
    record.v7_layer = region.v7_layer or getattr(record, "v7_layer", "")
    if region.raw_properties:
        props = {str(key): str(value) for key, value in record.properties.items()}
        props.update((str(key), str(value)) for key, value in region.raw_properties)
        record.properties = props
    record.outline_vertices = [
        RegionVertex(x_raw=x_raw, y_raw=y_raw)
        for x_raw, y_raw in region.outline_vertices
    ]
    record.outline_vertex_count = len(record.outline_vertices)
    record.hole_vertices = [
        [RegionVertex(x_raw=x_raw, y_raw=y_raw) for x_raw, y_raw in hole]
        for hole in region.hole_vertices
    ]
    record.hole_count = len(record.hole_vertices)
    record.bending_lines = [
        AltiumPcbBendingLine(
            angle_deg=line.angle_deg,
            radius_raw=line.radius_raw,
            fold_index=line.fold_index,
            x1=line.x1,
            y1=line.y1,
            x2=line.x2,
            y2=line.y2,
            raw_value=line.raw_value,
        )
        for line in region.bending_lines
    ]
    record.bending_line_count = len(record.bending_lines)
    if region.raw_properties and region.preserve_raw_property_order:
        record._typed_signature_at_parse = record._typed_signature()
    if region.extended_outline_tail_base64:
        record._trailing_data = base64.b64decode(region.extended_outline_tail_base64)
        record._trailing_data_outline_signature = (
            record._outline_signature_for_trailing_data()
        )
    else:
        record._trailing_data = b""
        record._trailing_data_outline_signature = (
            record._outline_signature_for_trailing_data()
        )
        record._emit_extended_outline_tail = synthesize_extended_outline_tail
    record._raw_binary = None
    record._raw_binary_signature = None
    record._properties_raw = None
    record._properties_raw_signature = None
    return record


def _ordered_stack_entries_from_board_data(board_data: object) -> tuple[str, ...]:
    entries: list[str] = []
    for segment in tuple(getattr(board_data, "segments", ()) or ()):
        for entry in tuple(getattr(segment, "entries", ()) or ()):
            if is_layer_stack_source_entry_key(getattr(entry, "key", None)):
                entries.append(str(getattr(entry, "raw", "") or ""))
    return tuple(entries)


def _replace_stack_entries_in_board_data(
    board_data: "PcbDocBoardData",
    replacement_entries: tuple[str, ...],
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import (
        PcbDocBoardData,
        PcbDocBoardDataEntry,
        PcbDocBoardDataSegment,
    )

    replacement_index = 0
    new_segments: list[PcbDocBoardDataSegment] = []
    for segment in tuple(getattr(board_data, "segments", ()) or ()):
        new_entries: list[PcbDocBoardDataEntry] = []
        for entry in tuple(getattr(segment, "entries", ()) or ()):
            if is_layer_stack_source_entry_key(getattr(entry, "key", None)):
                if replacement_index >= len(replacement_entries):
                    raise ValueError("Not enough layer-stack replacement entries")
                new_entries.append(
                    PcbDocBoardDataEntry(raw=replacement_entries[replacement_index])
                )
                replacement_index += 1
            else:
                new_entries.append(entry)
        new_segments.append(
            PcbDocBoardDataSegment(
                entries=tuple(new_entries),
                leading_pipe=bool(getattr(segment, "leading_pipe", True)),
            )
        )

    if replacement_index != len(replacement_entries):
        raise ValueError("Unused layer-stack replacement entries")

    return PcbDocBoardData(
        segments=tuple(new_segments),
        trailing_nul=bool(getattr(board_data, "trailing_nul", True)),
    )


def _filter_stack_entries(
    entries: tuple[str, ...],
    key_predicate: Callable[[str | None], bool],
) -> tuple[str, ...]:
    result: list[str] = []
    for raw in entries:
        key = raw.split("=", 1)[0] if "=" in raw else raw
        if key_predicate(key):
            result.append(raw)
    return tuple(result)


def _append_missing_authored_layer_pair_entries(
    entries: list[str],
    authored_values: Mapping[str, str],
    emitted_keys: set[str],
) -> None:
    missing_entries = [
        f"{key}={value}"
        for key, value in authored_values.items()
        if key.startswith("LAYERPAIR") and key not in emitted_keys
    ]
    if not missing_entries:
        return

    insert_index = len(entries)
    for index, entry in enumerate(entries):
        key = entry.split("=", 1)[0] if "=" in entry else entry
        if key.startswith("LAYERPAIR"):
            insert_index = index + 1
    entries[insert_index:insert_index] = missing_entries
    for entry in missing_entries:
        emitted_keys.add(entry.split("=", 1)[0])


def _filter_primary_stack_core_entries(
    entries: tuple[str, ...],
    core_key_predicate: Callable[[str | None], bool],
    cache_key_predicate: Callable[[str | None], bool],
) -> tuple[str, ...]:
    result: list[str] = []
    for raw in entries:
        key = raw.split("=", 1)[0] if "=" in raw else raw
        if cache_key_predicate(key):
            break
        if core_key_predicate(key):
            result.append(raw)
    return tuple(result)


def _stack_custom_data_entry(entries: tuple[str, ...]) -> str:
    for raw in entries:
        key = raw.split("=", 1)[0] if "=" in raw else raw
        if key.upper() == "V9_STACKCUSTOMDATA":
            return raw
    return ""


def _authored_stack_custom_data_entry(document: AltiumLayerStackDocument) -> str:
    xml_bytes = document.to_stackupx().to_bytes()
    token = base64.b64encode(zlib.compress(xml_bytes)).decode("ascii")
    return f"V9_STACKCUSTOMDATA={token}"


def _rigid_flex_mode_board_record_entries(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    if _normalized_rigid_flex_mode(document.rigid_flex_mode) != "advanced":
        return ()
    return ("RIGIDFLEXADVANCED=TRUE",)


def _should_emit_authored_stack_custom_data(
    document: AltiumLayerStackDocument,
) -> bool:
    return (
        _native_pcbdoc_writer_mode(document) == "rigid_flex_new_pcbdoc"
        or bool(tuple(document.impedance_profiles or ()))
        or bool(tuple(document.transmission_lines or ()))
    )


def _with_board_record_raw_entry(
    board_data: "PcbDocBoardData", raw_entry: str
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import PcbDocBoardDataEntry

    raw_entry = raw_entry.rstrip("\r\n")
    key, _, value = raw_entry.partition("=")
    if not key:
        return board_data
    for index, segment in enumerate(tuple(getattr(board_data, "segments", ()) or ())):
        if segment.get_value("RECORD") != "Board":
            continue
        if segment.get_value(key) is not None:
            return board_data.with_segment(
                index, segment.with_updated_value(key, value)
            )
        entries = tuple(getattr(segment, "entries", ()) or ())
        if entries:
            updated_entries = (
                entries[:-1] + (PcbDocBoardDataEntry(raw=raw_entry),) + entries[-1:]
            )
        else:
            updated_entries = (PcbDocBoardDataEntry(raw=raw_entry),)
        return board_data.with_segment(
            index,
            replace(
                segment,
                entries=updated_entries,
            ),
        )
    return board_data


def _with_top_level_bend_cache_entries(
    board_data: "PcbDocBoardData",
    document: AltiumLayerStackDocument,
) -> "PcbDocBoardData":
    entries = _top_level_bend_cache_entry_strings(document)
    if not entries:
        return board_data

    from .altium_pcbdoc_builder import PcbDocBoardDataEntry, PcbDocBoardDataSegment

    segments = tuple(getattr(board_data, "segments", ()) or ())
    if not segments:
        return board_data

    top_segment = segments[0]
    updated_entries: list[PcbDocBoardDataEntry] = []
    inserted = False
    replace_split_lines = bool(tuple(document.board_split_lines or ()))
    for entry in tuple(getattr(top_segment, "entries", ()) or ()):
        key = str(getattr(entry, "key", "") or "")
        if _is_top_level_bend_cache_key(key):
            continue
        if replace_split_lines and _is_top_level_split_cache_key(key):
            if not inserted and key == "SPLITLINECOUNT":
                updated_entries.extend(PcbDocBoardDataEntry(raw=raw) for raw in entries)
                inserted = True
            continue
        updated_entries.append(entry)
        if not inserted and key == "SPLITLINECOUNT":
            updated_entries.extend(PcbDocBoardDataEntry(raw=raw) for raw in entries)
            inserted = True

    if not inserted:
        insertion_index = _top_level_bend_cache_fallback_index(updated_entries)
        encoded_entries = tuple(PcbDocBoardDataEntry(raw=raw) for raw in entries)
        updated_entries = (
            updated_entries[:insertion_index]
            + list(encoded_entries)
            + updated_entries[insertion_index:]
        )

    updated_top_segment = PcbDocBoardDataSegment(
        entries=tuple(updated_entries),
        leading_pipe=bool(getattr(top_segment, "leading_pipe", True)),
    )
    return board_data.with_segment(0, updated_top_segment)


def _top_level_bend_cache_entry_strings(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    split_lines = tuple(document.board_split_lines or ())
    bend_lines = tuple(document.board_bending_lines or ())
    arc_split_lines = tuple(document.board_arc_split_lines or ())
    if not split_lines and not bend_lines and not arc_split_lines:
        return ()
    entries: list[str] = []
    for index, value in enumerate(split_lines):
        entries.append(f"SPLITLINE{index}={value}")
    if split_lines:
        entries.append(f"SPLITLINECOUNT={len(split_lines)}")
    if bend_lines or arc_split_lines:
        entries.append(f"BENDINGLINECOUNT={len(bend_lines)}")
        for index, line in enumerate(bend_lines):
            entries.append(f"BENDINGLINE{index}={line.to_property_value()}")
    if arc_split_lines:
        entries.append(f"ARCSPLITLINESCOUNT={len(arc_split_lines)}")
        for index, value in enumerate(arc_split_lines):
            entries.append(f"ARCSPLITLINE{index}={value}")
    return tuple(entries)


def _is_top_level_split_cache_key(key: str) -> bool:
    return bool(
        key == "SPLITLINECOUNT" or re.match(r"^SPLITLINE\d+$", key, re.IGNORECASE)
    )


def _is_top_level_bend_cache_key(key: str) -> bool:
    return bool(
        key == "BENDINGLINECOUNT"
        or key == "ARCSPLITLINESCOUNT"
        or re.match(r"^BENDINGLINE\d+$", key, re.IGNORECASE)
        or re.match(r"^ARCSPLITLINE\d+$", key, re.IGNORECASE)
    )


def _top_level_bend_cache_fallback_index(
    entries: Sequence["PcbDocBoardDataEntry"],
) -> int:
    for index, entry in enumerate(entries):
        if getattr(entry, "key", None) == "SHEETX":
            return index
    return len(entries)


def _v9_cache_layer_names_by_id(document: AltiumLayerStackDocument) -> dict[int, str]:
    names_by_id: dict[int, str] = {}
    for stack in document.physical_stacks:
        for layer in stack.layers:
            if layer.layer_id is None or layer.family not in {"copper", "dielectric"}:
                continue
            names_by_id[int(layer.layer_id)] = layer.display_name
    return names_by_id


def _v9_cache_registry_entries_by_layer_id(
    document: AltiumLayerStackDocument,
) -> dict[int, AltiumLayerRegistryEntry]:
    entries_by_layer_id: dict[int, AltiumLayerRegistryEntry] = {}
    for entry in tuple(document.layer_registry.entries or ()):
        layer_id = entry.source_layer_id or entry.v7_layer_id
        if layer_id is None:
            continue
        if not any(
            str(alias or "").upper().startswith("V9_CACHE_LAYER")
            for alias in tuple(entry.source_aliases or ())
        ):
            continue
        entries_by_layer_id[int(layer_id)] = entry
    return entries_by_layer_id


def _replace_v9_cache_entries_in_board_data(
    board_data: "PcbDocBoardData",
    document: AltiumLayerStackDocument,
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import (
        PcbDocBoardData,
        PcbDocBoardDataSegment,
    )

    if not document.physical_stacks:
        return board_data

    cache_entries: list[PcbDocBoardDataEntry] = []
    cache_segment_index: int | None = None
    for segment_index, segment in enumerate(
        tuple(getattr(board_data, "segments", ()) or ())
    ):
        for entry in tuple(getattr(segment, "entries", ()) or ()):
            if _V9_CACHE_RE.match(str(getattr(entry, "key", "") or "")):
                cache_entries.append(entry)
                if cache_segment_index is None:
                    cache_segment_index = segment_index

    if cache_segment_index is None:
        raise KeyError("Board6/Data V9 cache segment not found")

    before_groups, after_groups = _split_v9_cache_groups(document, tuple(cache_entries))
    default_substack_guid = _default_substack_guid_from_board_data(board_data)
    substacks = _semantic_substacks(document, default_substack_guid)
    replacement_entries = _semantic_v9_cache_entry_strings(
        document,
        substacks,
        before_groups,
        after_groups,
    )

    new_segments: list[PcbDocBoardDataSegment] = []
    inserted = False
    for segment in tuple(getattr(board_data, "segments", ()) or ()):
        new_entries: list[PcbDocBoardDataEntry] = []
        for entry in tuple(getattr(segment, "entries", ()) or ()):
            if _V9_CACHE_RE.match(str(getattr(entry, "key", "") or "")):
                if not inserted:
                    new_entries.extend(replacement_entries)
                    inserted = True
                continue
            new_entries.append(entry)
        new_segments.append(
            PcbDocBoardDataSegment(
                entries=tuple(new_entries),
                leading_pipe=bool(getattr(segment, "leading_pipe", True)),
            )
        )

    return PcbDocBoardData(
        segments=tuple(new_segments),
        trailing_nul=bool(getattr(board_data, "trailing_nul", True)),
    )


def _default_substack_guid_from_board_data(board_data: "PcbDocBoardData") -> str:
    getter = getattr(board_data, "default_substack_guid", None)
    if callable(getter):
        try:
            return str(getter())
        except (KeyError, ValueError):
            pass
    return _authoring_guid("v9-substack", 0, "Board Layer Stack")


def _split_v9_cache_groups(
    document: AltiumLayerStackDocument,
    cache_entries: tuple[PcbDocBoardDataEntry, ...],
) -> tuple[
    tuple[tuple[PcbDocBoardDataEntry, ...], ...],
    tuple[tuple[PcbDocBoardDataEntry, ...], ...],
]:
    grouped_entries: dict[int, list[PcbDocBoardDataEntry]] = {}
    grouped_fields: dict[int, dict[str, str]] = {}
    order: list[int] = []
    for entry in cache_entries:
        key = str(getattr(entry, "key", "") or "")
        match = _V9_CACHE_RE.match(key)
        if match is None:
            continue
        index = int(match.group(1))
        suffix = str(match.group(2) or "").upper()
        if index not in grouped_entries:
            order.append(index)
        grouped_entries.setdefault(index, []).append(entry)
        value = getattr(entry, "value", None)
        grouped_fields.setdefault(index, {})[suffix] = (
            "" if value is None else str(value)
        )

    physical_layer_ids = {
        int(layer.layer_id)
        for stack in tuple(document.physical_stacks or ())
        for layer in tuple(stack.layers or ())
        if layer.layer_id is not None
    }
    physical_names = {
        _text(layer.display_name).upper()
        for stack in tuple(document.physical_stacks or ())
        for layer in tuple(stack.layers or ())
        if _text(layer.display_name)
    }

    before: list[tuple[PcbDocBoardDataEntry, ...]] = []
    after: list[tuple[PcbDocBoardDataEntry, ...]] = []
    saw_stack_owned_group = False
    for index in order:
        fields = grouped_fields.get(index, {})
        layer_id = _parse_int_token(fields.get("LAYERID"))
        name = _text(fields.get("NAME")).upper()
        is_stack_owned = (
            _v9_cache_group_has_substack_context(fields)
            or (layer_id is not None and int(layer_id) in physical_layer_ids)
            or (name and name in physical_names)
        )
        if is_stack_owned:
            saw_stack_owned_group = True
            continue
        group = tuple(grouped_entries.get(index, ()))
        if saw_stack_owned_group:
            after.append(group)
        else:
            before.append(group)
    return tuple(before), tuple(after)


def _v9_cache_group_has_substack_context(fields: Mapping[str, str]) -> bool:
    for suffix in fields:
        upper = suffix.upper()
        if upper.endswith("CONTEXT"):
            return True
        if upper != "USEDBYPRIMS" and upper.endswith("USEDBYPRIMS"):
            return True
    return False


def _semantic_v9_cache_entry_strings(
    document: AltiumLayerStackDocument,
    substacks: tuple[AltiumStackSubstack, ...],
    before_groups: tuple[tuple[PcbDocBoardDataEntry, ...], ...],
    after_groups: tuple[tuple[PcbDocBoardDataEntry, ...], ...],
) -> tuple[PcbDocBoardDataEntry, ...]:
    from .altium_pcbdoc_builder import PcbDocBoardDataEntry

    entries: list[PcbDocBoardDataEntry] = []
    cache_index = 0
    registry_entries_by_layer_id = _v9_cache_registry_entries_by_layer_id(document)
    cache_index = _append_reindexed_v9_cache_groups(
        entries,
        before_groups,
        cache_index,
        registry_entries_by_layer_id=registry_entries_by_layer_id,
    )

    stack = document.physical_stacks[0]
    layers = _native_pcbdoc_board_layer_rows(tuple(stack.layers or ()))
    for layer_index, layer in enumerate(layers):
        source_index = (
            int(layer.stack_index)
            if layer.stack_index is not None
            else int(layer_index)
        )
        prefix = f"V9_CACHE_LAYER{cache_index}_"
        for key, value in _semantic_v9_stack_layer_fields(
            layer,
            source_index,
            substacks,
        ):
            entries.append(PcbDocBoardDataEntry(raw=f"{prefix}{key}={value}"))
        cache_index += 1
    cache_index = _append_reindexed_v9_cache_groups(
        entries,
        after_groups,
        cache_index,
        registry_entries_by_layer_id=registry_entries_by_layer_id,
    )
    return tuple(entries)


def _append_reindexed_v9_cache_groups(
    entries: list[PcbDocBoardDataEntry],
    groups: tuple[tuple[PcbDocBoardDataEntry, ...], ...],
    cache_index: int,
    *,
    registry_entries_by_layer_id: Mapping[int, AltiumLayerRegistryEntry],
) -> int:
    from .altium_pcbdoc_builder import PcbDocBoardDataEntry

    for group in groups:
        layer_id = _v9_cache_group_layer_id(group)
        registry_entry = (
            registry_entries_by_layer_id.get(layer_id) if layer_id is not None else None
        )
        for entry in group:
            key = str(getattr(entry, "key", "") or "")
            match = _V9_CACHE_RE.match(key)
            if match is None:
                continue
            suffix = match.group(2)
            raw_value = _v9_cache_group_value(entry, suffix, registry_entry)
            entries.append(
                PcbDocBoardDataEntry(
                    raw=f"V9_CACHE_LAYER{cache_index}_{suffix}={raw_value}"
                )
            )
        cache_index += 1
    return cache_index


def _v9_cache_group_layer_id(
    group: tuple["PcbDocBoardDataEntry", ...],
) -> int | None:
    for entry in group:
        key = str(getattr(entry, "key", "") or "")
        match = _V9_CACHE_RE.match(key)
        if match is None or str(match.group(2) or "").upper() != "LAYERID":
            continue
        return _parse_int_token(getattr(entry, "value", None))
    return None


def _v9_cache_group_value(
    entry: "PcbDocBoardDataEntry",
    suffix: str,
    registry_entry: AltiumLayerRegistryEntry | None,
) -> str:
    value = getattr(entry, "value", None)
    raw_value = "" if value is None else str(value)
    if registry_entry is None:
        return raw_value

    suffix_upper = str(suffix or "").upper()
    if suffix_upper == "ID" and registry_entry.source_record_id:
        return registry_entry.source_record_id
    if suffix_upper == "NAME" and registry_entry.display_name:
        return registry_entry.display_name
    if suffix_upper == "LAYERID":
        layer_id = registry_entry.source_layer_id or registry_entry.v7_layer_id
        return str(int(layer_id)) if layer_id is not None else raw_value
    if suffix_upper == "USEDBYPRIMS" and registry_entry.used_by_primitives is not None:
        return _format_bool_text(bool(registry_entry.used_by_primitives))
    if suffix_upper == "MECHENABLED" and registry_entry.enabled is not None:
        return _format_bool_text(bool(registry_entry.enabled))
    return raw_value


def _replace_board_data_entry_range(
    board_data: "PcbDocBoardData",
    key_predicate: Callable[[str | None], bool],
    replacement_entries: tuple[str, ...],
    missing_message: str,
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import PcbDocBoardDataEntry

    if not replacement_entries:
        raise ValueError("Layer-stack document did not provide replacement entries")

    def entry_predicate(entry: object) -> bool:
        return key_predicate(getattr(entry, "key", None))

    segment_index_for_entry_predicate = getattr(
        board_data,
        "_segment_index_for_entry_predicate",
    )
    segment_index = segment_index_for_entry_predicate(entry_predicate)
    if segment_index is None:
        raise KeyError(missing_message)

    segment = board_data.segments[segment_index]
    return board_data.with_segment(
        segment_index,
        segment.with_replaced_entries(
            entry_predicate,
            tuple(PcbDocBoardDataEntry(raw=raw) for raw in replacement_entries),
        ),
    )


def _replace_board_data_entry_section(
    board_data: "PcbDocBoardData",
    key_predicate: Callable[[str | None], bool],
    replacement_entries: tuple[str, ...],
    missing_message: str,
) -> "PcbDocBoardData":
    from .altium_pcbdoc_builder import PcbDocBoardDataEntry

    if not replacement_entries:
        raise ValueError("Layer-stack document did not provide replacement entries")

    replacement = tuple(PcbDocBoardDataEntry(raw=raw) for raw in replacement_entries)
    updated_segments: list[PcbDocBoardDataSegment] = []
    replaced = False
    inserted = False

    for segment in tuple(getattr(board_data, "segments", ()) or ()):
        updated_entries: list[object] = []
        for entry in tuple(getattr(segment, "entries", ()) or ()):
            if key_predicate(getattr(entry, "key", None)):
                if not inserted:
                    updated_entries.extend(replacement)
                    inserted = True
                replaced = True
                continue
            updated_entries.append(entry)
        updated_segments.append(replace(segment, entries=tuple(updated_entries)))

    if not replaced:
        raise KeyError(missing_message)
    return replace(board_data, segments=tuple(updated_segments))


def _add_authored_v9_stack_layer_values(
    values: dict[str, str],
    layers: tuple[AltiumStackLayer, ...],
) -> None:
    for layer in layers:
        if layer.source_family != "v9":
            continue
        prefix = f"V9_STACK_LAYER{layer.stack_index}_"
        for context_ref, context_value in layer.substack_contexts:
            values[f"{prefix}{context_ref}CONTEXT"] = str(
                int(context_value) if context_value is not None else 0
            )
        if layer.source_record_id:
            values[f"{prefix}ID"] = layer.source_record_id
        if layer.display_name:
            values[f"{prefix}NAME"] = layer.display_name
        if layer.layer_id is not None:
            values[f"{prefix}LAYERID"] = str(int(layer.layer_id))
        if layer.used_by_primitives is not None:
            values[f"{prefix}USEDBYPRIMS"] = _format_bool_text(layer.used_by_primitives)
        if layer.copper_thickness_mils is not None:
            values[f"{prefix}COPTHICK"] = _format_mil_value(layer.copper_thickness_mils)
        if layer.component_placement is not None:
            values[f"{prefix}COMPONENTPLACEMENT"] = str(int(layer.component_placement))
        if layer.dielectric_type is not None:
            values[f"{prefix}DIELTYPE"] = str(int(layer.dielectric_type))
        if layer.dielectric_constant is not None:
            values[f"{prefix}DIELCONST"] = format(
                float(layer.dielectric_constant), ".3f"
            )
        if layer.dielectric_loss_tangent is not None:
            values[f"{prefix}DIELLOSSTANGENT"] = format(
                float(layer.dielectric_loss_tangent), ".3f"
            )
        if layer.dielectric_height_mils is not None:
            values[f"{prefix}DIELHEIGHT"] = _format_mil_value(
                layer.dielectric_height_mils
            )
        if layer.dielectric_material:
            values[f"{prefix}DIELMATERIAL"] = layer.dielectric_material
        if layer.coverlay_expansion:
            values[f"{prefix}COVERLAY_EXPANSION"] = layer.coverlay_expansion
        if layer.is_stiffener is not None:
            values[f"{prefix}ISSTIFFENER"] = _format_bool_text(layer.is_stiffener)
        if layer.is_adhesive is not None:
            values[f"{prefix}ISADHESIVE"] = _format_bool_text(layer.is_adhesive)


def _add_authored_substack_values(
    values: dict[str, str],
    substack: AltiumStackSubstack,
) -> None:
    if substack.field_family == "v8":
        prefix = f"LAYERSUBSTACK_V8_{substack.index}"
        separator = ""
    elif substack.field_family == "legacy":
        prefix = f"SUBSTACK{substack.index}"
        separator = "_"
    else:
        prefix = f"V9_SUBSTACK{substack.index}"
        separator = "_"

    values[f"{prefix}{separator}ID"] = substack.source_stackup_ref
    values[f"{prefix}{separator}NAME"] = substack.name
    _set_optional_bool(values, f"{prefix}{separator}ISFLEX", substack.is_flex)
    _set_optional_bool(
        values,
        f"{prefix}{separator}SHOWTOPDIELECTRIC",
        substack.show_top_dielectric,
    )
    _set_optional_bool(
        values,
        f"{prefix}{separator}SHOWBOTTOMDIELECTRIC",
        substack.show_bottom_dielectric,
    )
    _set_optional_bool(values, f"{prefix}{separator}SERVICE", substack.service_stackup)
    _set_optional_bool(
        values,
        f"{prefix}{separator}USEDBYPRIMS",
        substack.used_by_primitives,
    )
    if substack.raw_stackup_type:
        values[f"{prefix}{separator}TYPE"] = substack.raw_stackup_type


def _add_authored_layer_pair_values(
    values: dict[str, str],
    pair: AltiumLayerPair,
) -> None:
    prefix = f"LAYERPAIR{pair.pair_index}"
    values[f"{prefix}LOW"] = pair.low_layer_token
    values[f"{prefix}HIGH"] = pair.high_layer_token
    _set_optional_bool(values, f"{prefix}DRILLGUIDE", pair.drill_guide)
    _set_optional_bool(values, f"{prefix}DRILLDRAWING", pair.drill_drawing)
    _set_optional_bool(values, f"{prefix}BACKDRILL", pair.is_backdrill)
    _set_optional_bool(values, f"{prefix}INVERTED", pair.is_inverted)
    if pair.drill_pair_type_raw is not None:
        values[f"{prefix}DRILLPAIRTYPE"] = str(int(pair.drill_pair_type_raw))
    for index, substack_ref in enumerate(pair.source_substack_refs):
        values[f"{prefix}SUBSTACK_{index}"] = substack_ref


def _set_optional_bool(
    values: dict[str, str],
    key: str,
    value: bool | None,
) -> None:
    if value is not None:
        values[key] = _format_bool_text(value)


def _board_from_board_data(board_data: object) -> object:
    from .altium_board import AltiumBoard

    segments_obj = getattr(board_data, "segments", ()) or ()
    segments: tuple[object, ...] = tuple(segments_obj)
    if not segments:
        board = getattr(board_data, "top_level_board", None)
        if board is None:
            raise ValueError("Board data does not expose any Board6/Data segments")
        return board

    merged: dict[str, str] = {}
    for segment in segments:
        to_mapping = getattr(segment, "to_mapping", None)
        if callable(to_mapping):
            mapping = to_mapping()
            if isinstance(mapping, dict):
                merged.update({str(key): str(value) for key, value in mapping.items()})
    return AltiumBoard.from_record(merged)


def _build_stack_layers(
    *,
    raw_record: dict[str, str],
    board: object,
    registry_entries: dict[str, AltiumLayerRegistryEntry],
    v9_groups: dict[int, dict[str, str]],
    v8_groups: dict[int, dict[str, str]],
) -> list[AltiumStackLayer]:
    if v9_groups:
        return _build_indexed_stack_layers(
            groups=v9_groups,
            source_family="v9",
            registry_entries=registry_entries,
        )
    if v8_groups:
        return _build_indexed_stack_layers(
            groups=v8_groups,
            source_family="v8",
            registry_entries=registry_entries,
        )
    return _build_legacy_stack_layers(board, raw_record, registry_entries)


def _build_indexed_stack_layers(
    *,
    groups: dict[int, dict[str, str]],
    source_family: str,
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> list[AltiumStackLayer]:
    layers: list[AltiumStackLayer] = []
    for index in sorted(groups):
        fields = groups[index]
        display_name = _text(fields.get("NAME")) or f"Layer {index}"
        layer_id = _parse_int_token(fields.get("LAYERID"))
        legacy_layer_id = _legacy_layer_id_from_v7_save_id(layer_id)
        copper_thickness = _float_token(fields.get("COPTHICK"))
        dielectric_height = _float_token(fields.get("DIELHEIGHT"))
        stackupx_properties = _stackupx_properties_from_indexed_fields(fields)
        advanced_family = _advanced_layer_family(fields)
        surface_finish = advanced_family == "surface_finish"
        family = (
            advanced_family
            if advanced_family
            else _layer_family(
                display_name=display_name,
                legacy_layer_id=legacy_layer_id,
                copper_thickness_mils=copper_thickness,
                dielectric_height_mils=dielectric_height,
            )
        )
        layer_height = dielectric_height
        if surface_finish and layer_height is None:
            layer_height = _stackupx_property_length_mils(
                stackupx_properties,
                "Thickness",
            )
        layer_material = _text(fields.get("DIELMATERIAL")) or (
            _stackupx_property_value(stackupx_properties, "Material")
            if surface_finish
            else ""
        )
        registry_ref = _model_id(
            v7_layer_id=layer_id,
            legacy_layer_id=legacy_layer_id,
            display_name=display_name,
            source_family=source_family,
            stack_index=index,
        )
        source_alias = f"{source_family.upper()}_STACK_LAYER{index}"
        registry_entries.setdefault(
            registry_ref,
            AltiumLayerRegistryEntry(
                model_id=registry_ref,
                display_name=display_name,
                family=family,
                standard_token=_standard_token_from_legacy(legacy_layer_id),
                legacy_layer_id=legacy_layer_id,
                v7_layer_id=layer_id,
                source_layer_id=layer_id,
                source_record_id=_text(fields.get("ID")),
                side=_layer_side(display_name, legacy_layer_id),
                used_by_primitives=_parse_altium_bool_token(fields.get("USEDBYPRIMS")),
                enabled=True,
                source_aliases=(source_alias,),
            ),
        )
        layers.append(
            AltiumStackLayer(
                stack_index=index,
                registry_ref=registry_ref,
                display_name=display_name,
                family=family,
                source_family=source_family,
                source_record_id=_text(fields.get("ID")),
                layer_id=layer_id,
                legacy_layer_id=legacy_layer_id,
                copper_thickness_mils=copper_thickness,
                dielectric_constant=_float_token(fields.get("DIELCONST")),
                dielectric_loss_tangent=_float_token(fields.get("DIELLOSSTANGENT")),
                dielectric_height_mils=layer_height,
                dielectric_material=layer_material,
                dielectric_type=_parse_int_token(fields.get("DIELTYPE")),
                component_placement=_parse_int_token(fields.get("COMPONENTPLACEMENT")),
                used_by_primitives=_parse_altium_bool_token(fields.get("USEDBYPRIMS")),
                coverlay_expansion=_text(fields.get("COVERLAY_EXPANSION")),
                is_stiffener=_parse_altium_bool_token(fields.get("ISSTIFFENER")),
                is_adhesive=_parse_altium_bool_token(fields.get("ISADHESIVE")),
                substack_contexts=_collect_contexts(fields),
                source_keys=tuple(
                    f"{source_family}:{index}:{suffix}" for suffix in sorted(fields)
                ),
                stackupx_properties=stackupx_properties,
            )
        )
    return layers


def _build_legacy_stack_layers(
    board: object,
    raw_record: dict[str, str],
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> list[AltiumStackLayer]:
    layers: list[AltiumStackLayer] = []
    source_layers = tuple(getattr(board, "layer_stackup", ()) or ())
    for source_index, layer in enumerate(source_layers):
        legacy_layer_id = int(getattr(layer, "layer_id", 0) or 0)
        if not legacy_layer_id:
            continue
        display_name = _text(getattr(layer, "name", "")) or f"Layer {legacy_layer_id}"
        registry_ref = _model_id(
            v7_layer_id=None,
            legacy_layer_id=legacy_layer_id,
            display_name=display_name,
            source_family="legacy",
            stack_index=legacy_layer_id,
        )
        registry_entries.setdefault(
            registry_ref,
            AltiumLayerRegistryEntry(
                model_id=registry_ref,
                display_name=display_name,
                family="copper",
                standard_token=_standard_token_from_legacy(legacy_layer_id),
                legacy_layer_id=legacy_layer_id,
                side=_layer_side(display_name, legacy_layer_id),
                enabled=True,
                source_aliases=(f"LAYER{legacy_layer_id}",),
            ),
        )
        layers.append(
            AltiumStackLayer(
                stack_index=len(layers),
                registry_ref=registry_ref,
                display_name=display_name,
                family="copper",
                source_family="legacy",
                legacy_layer_id=legacy_layer_id,
                copper_thickness_mils=float(
                    getattr(layer, "copper_thickness", 0.0) or 0.0
                ),
                used_by_primitives=None,
                source_keys=_legacy_layer_source_keys(
                    raw_record,
                    legacy_layer_id,
                    suffixes=("COPTHICK", "MECHENABLED", "NAME", "NEXT", "PREV"),
                ),
            )
        )
        if source_index < len(source_layers) - 1:
            # Legacy LAYERnDIEL* fields describe the dielectric below LAYERn.
            layers.append(
                _legacy_dielectric_layer(
                    layer=layer,
                    legacy_layer_id=legacy_layer_id,
                    stack_index=len(layers),
                    raw_record=raw_record,
                    registry_entries=registry_entries,
                )
            )
    return layers


def _legacy_dielectric_layer(
    *,
    layer: object,
    legacy_layer_id: int,
    stack_index: int,
    raw_record: dict[str, str],
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> AltiumStackLayer:
    dielectric_index = stack_index // 2 + 1
    display_name = f"Dielectric {dielectric_index}"
    registry_ref = _model_id(
        v7_layer_id=None,
        legacy_layer_id=None,
        display_name=display_name,
        source_family="legacy_dielectric",
        stack_index=stack_index,
    )
    source_record_id = f"LAYER{legacy_layer_id}DIEL"
    registry_entries.setdefault(
        registry_ref,
        AltiumLayerRegistryEntry(
            model_id=registry_ref,
            display_name=display_name,
            family="dielectric",
            source_record_id=source_record_id,
            enabled=True,
            source_aliases=(source_record_id,),
        ),
    )
    return AltiumStackLayer(
        stack_index=stack_index,
        registry_ref=registry_ref,
        display_name=display_name,
        family="dielectric",
        source_family="legacy_dielectric",
        source_record_id=source_record_id,
        dielectric_constant=float(getattr(layer, "diel_constant", 0.0) or 0.0),
        dielectric_height_mils=float(getattr(layer, "diel_height", 0.0) or 0.0),
        dielectric_material=_text(getattr(layer, "diel_material", "")),
        dielectric_type=int(getattr(layer, "diel_type", 0) or 0),
        used_by_primitives=None,
        source_keys=_legacy_layer_source_keys(
            raw_record,
            legacy_layer_id,
            suffixes=(
                "DIELCONST",
                "DIELHEIGHT",
                "DIELLOSSTANGENT",
                "DIELMATERIAL",
                "DIELTYPE",
            ),
        ),
    )


def _legacy_layer_source_keys(
    raw_record: dict[str, str],
    legacy_layer_id: int,
    *,
    suffixes: tuple[str, ...],
) -> tuple[str, ...]:
    upper_suffixes = tuple(suffix.upper() for suffix in suffixes)
    return tuple(
        sorted(
            key
            for key in raw_record
            if _is_legacy_layer_key_for(key, legacy_layer_id)
            and key.upper().endswith(upper_suffixes)
        )
    )


def _is_legacy_layer_key_for(key: str, legacy_layer_id: int) -> bool:
    upper = key.upper()
    prefix = f"LAYER{legacy_layer_id}"
    if not upper.startswith(prefix):
        return False
    suffix = upper[len(prefix) :]
    return bool(suffix) and not suffix[0].isdigit()


def _append_cache_registry_entries(
    cache_groups: dict[int, dict[str, str]],
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> None:
    for index in sorted(cache_groups):
        fields = cache_groups[index]
        display_name = _text(fields.get("NAME"))
        layer_id = _parse_int_token(fields.get("LAYERID"))
        if not display_name or layer_id is None:
            continue
        legacy_layer_id = _legacy_layer_id_from_v7_save_id(layer_id)
        registry_ref = _model_id(
            v7_layer_id=layer_id,
            legacy_layer_id=legacy_layer_id,
            display_name=display_name,
            source_family="v9_cache",
            stack_index=index,
        )
        if registry_ref in registry_entries:
            continue
        family = _layer_family(
            display_name=display_name,
            legacy_layer_id=legacy_layer_id,
        )
        registry_entries[registry_ref] = AltiumLayerRegistryEntry(
            model_id=registry_ref,
            display_name=display_name,
            family=family,
            standard_token=_standard_token_from_legacy(legacy_layer_id),
            legacy_layer_id=legacy_layer_id,
            v7_layer_id=layer_id,
            source_layer_id=layer_id,
            source_record_id=_text(fields.get("ID")),
            side=_layer_side(display_name, legacy_layer_id),
            used_by_primitives=_parse_altium_bool_token(fields.get("USEDBYPRIMS")),
            enabled=_parse_altium_bool_token(fields.get("MECHENABLED")),
            source_aliases=(f"V9_CACHE_LAYER{index}",),
        )


def _append_legacy_registry_entries(
    board: object,
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> None:
    for layer in list(getattr(board, "layer_stackup", ()) or ()):
        legacy_layer_id = int(getattr(layer, "layer_id", 0) or 0)
        if not legacy_layer_id:
            continue
        display_name = _text(getattr(layer, "name", "")) or f"Layer {legacy_layer_id}"
        registry_ref = _model_id(
            v7_layer_id=None,
            legacy_layer_id=legacy_layer_id,
            display_name=display_name,
            source_family="legacy",
            stack_index=legacy_layer_id,
        )
        registry_entries.setdefault(
            registry_ref,
            AltiumLayerRegistryEntry(
                model_id=registry_ref,
                display_name=display_name,
                family=_layer_family(
                    display_name=display_name,
                    legacy_layer_id=legacy_layer_id,
                    copper_thickness_mils=float(
                        getattr(layer, "copper_thickness", 0.0) or 0.0
                    ),
                    dielectric_height_mils=float(
                        getattr(layer, "diel_height", 0.0) or 0.0
                    ),
                ),
                standard_token=_standard_token_from_legacy(legacy_layer_id),
                legacy_layer_id=legacy_layer_id,
                side=_layer_side(display_name, legacy_layer_id),
                enabled=True,
                source_aliases=(f"LAYER{legacy_layer_id}",),
            ),
        )


def _build_substacks(
    board: object,
    stack_layers: list[AltiumStackLayer],
) -> list[AltiumStackSubstack]:
    results: list[AltiumStackSubstack] = []
    for substack in tuple(getattr(board, "substacks", ()) or ()):
        source_stackup_ref = _text(getattr(substack, "source_stackup_ref", ""))
        enabled_layer_refs = tuple(
            layer.registry_ref
            for layer in stack_layers
            if layer.is_enabled_for_substack(source_stackup_ref)
        )
        results.append(
            AltiumStackSubstack(
                index=int(getattr(substack, "index", 0) or 0),
                field_family=_text(getattr(substack, "field_family", "")),
                source_stackup_ref=source_stackup_ref,
                name=_text(getattr(substack, "name", "")),
                is_flex=getattr(substack, "is_flex", None),
                show_top_dielectric=getattr(substack, "show_top_dielectric", None),
                show_bottom_dielectric=getattr(
                    substack, "show_bottom_dielectric", None
                ),
                service_stackup=getattr(substack, "service_stackup", None),
                used_by_primitives=getattr(substack, "used_by_primitives", None),
                raw_stackup_type=_text(getattr(substack, "raw_stackup_type", "")),
                enabled_layer_refs=enabled_layer_refs,
            )
        )
    return results


def _with_stack_custom_data_layer_flags(
    stack_layers: list[AltiumStackLayer],
    stackupx: "AltiumStackupXDocument | None",
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> list[AltiumStackLayer]:
    """
    Overlay StackupX-only per-layer metadata by native layer GUID.

    AD26 stores adhesive rows in embedded `V9_STACKCUSTOMDATA` as
    `IsAdhesive`, while the ordinary Board6 V8/V9 rows keep the row shaped like
    prepreg. Some advanced rigid-flex data, including surface-finish rows, can
    live only in StackupX. The GUIDs match, so this is a source-safe join.
    """
    if stackupx is None:
        return stack_layers
    custom_model = stackupx.to_layer_stack_document()
    custom_stack = _stack_custom_data_primary_stack(custom_model)
    if custom_stack is None:
        return stack_layers
    custom_layers = tuple(custom_stack.layers or ())
    if not custom_layers:
        return stack_layers
    _append_stack_custom_registry_entries(custom_model, registry_entries)
    return _merge_stack_custom_layers(stack_layers, custom_layers, custom_model)


def _stack_custom_data_primary_stack(
    custom_model: "AltiumLayerStackDocument",
) -> AltiumPhysicalStack | None:
    stacks = tuple(custom_model.physical_stacks or ())
    if not stacks:
        return None
    return max(stacks, key=lambda stack: len(tuple(stack.layers or ())))


def _append_stack_custom_registry_entries(
    custom_model: "AltiumLayerStackDocument",
    registry_entries: dict[str, AltiumLayerRegistryEntry],
) -> None:
    for entry in tuple(custom_model.layer_registry.entries or ()):
        registry_entries.setdefault(entry.model_id, entry)


def _merge_stack_custom_layers(
    stack_layers: list[AltiumStackLayer],
    custom_layers: tuple[AltiumStackLayer, ...],
    custom_model: "AltiumLayerStackDocument",
) -> list[AltiumStackLayer]:
    custom_by_id = {
        _normalized_source_id(layer.source_record_id): layer
        for layer in custom_layers
        if _normalized_source_id(layer.source_record_id)
    }
    custom_index_by_id = {
        _normalized_source_id(layer.source_record_id): index
        for index, layer in enumerate(custom_layers)
        if _normalized_source_id(layer.source_record_id)
    }
    existing_ids = {
        _normalized_source_id(layer.source_record_id)
        for layer in stack_layers
        if _normalized_source_id(layer.source_record_id)
    }
    if not custom_by_id:
        return stack_layers

    updated: list[AltiumStackLayer] = []
    custom_index = 0
    for layer in stack_layers:
        key = _normalized_source_id(layer.source_record_id)
        target_index = custom_index_by_id.get(key)
        if target_index is not None:
            while custom_index < target_index:
                candidate = custom_layers[custom_index]
                candidate_key = _normalized_source_id(candidate.source_record_id)
                if candidate_key not in existing_ids:
                    updated.append(
                        _custom_layer_with_contexts(candidate, custom_model.substacks)
                    )
                custom_index += 1
            updated.append(_merge_stack_custom_layer_metadata(layer, custom_by_id[key]))
            custom_index = target_index + 1
        else:
            updated.append(layer)
    while custom_index < len(custom_layers):
        candidate = custom_layers[custom_index]
        candidate_key = _normalized_source_id(candidate.source_record_id)
        if candidate_key not in existing_ids:
            updated.append(
                _custom_layer_with_contexts(candidate, custom_model.substacks)
            )
        custom_index += 1
    return updated


def _merge_stack_custom_layer_metadata(
    layer: AltiumStackLayer,
    custom_layer: AltiumStackLayer,
) -> AltiumStackLayer:
    adhesive = _overlaid_optional_bool(layer.is_adhesive, custom_layer.is_adhesive)
    stiffener = _overlaid_optional_bool(layer.is_stiffener, custom_layer.is_stiffener)
    stackupx_properties = custom_layer.stackupx_properties or layer.stackupx_properties
    stackupx_substack_properties = _merge_stackupx_substack_properties(
        layer.stackupx_substack_properties,
        custom_layer.stackupx_substack_properties,
    )
    stackupx_is_shared = _overlaid_optional_bool(
        layer.stackupx_is_shared,
        custom_layer.stackupx_is_shared,
    )
    stackupx_substack_is_shared = _merge_stackupx_substack_shared(
        layer.stackupx_substack_is_shared,
        custom_layer.stackupx_substack_is_shared,
    )
    stackupx_substack_is_enabled = _merge_stackupx_substack_enabled(
        layer.stackupx_substack_is_enabled,
        custom_layer.stackupx_substack_is_enabled,
    )
    if (
        adhesive == layer.is_adhesive
        and stiffener == layer.is_stiffener
        and stackupx_properties == layer.stackupx_properties
        and stackupx_substack_properties == layer.stackupx_substack_properties
        and stackupx_is_shared == layer.stackupx_is_shared
        and stackupx_substack_is_shared == layer.stackupx_substack_is_shared
        and stackupx_substack_is_enabled == layer.stackupx_substack_is_enabled
    ):
        return layer
    return replace(
        layer,
        is_adhesive=adhesive,
        is_stiffener=stiffener,
        stackupx_properties=stackupx_properties,
        stackupx_substack_properties=stackupx_substack_properties,
        stackupx_is_shared=stackupx_is_shared,
        stackupx_substack_is_shared=stackupx_substack_is_shared,
        stackupx_substack_is_enabled=stackupx_substack_is_enabled,
    )


def _merge_stackupx_substack_shared(
    source_values: tuple[tuple[str, bool], ...],
    overlay_values: tuple[tuple[str, bool], ...],
) -> tuple[tuple[str, bool], ...]:
    return _merge_stackupx_substack_bool_values(source_values, overlay_values)


def _merge_stackupx_substack_enabled(
    source_values: tuple[tuple[str, bool], ...],
    overlay_values: tuple[tuple[str, bool], ...],
) -> tuple[tuple[str, bool], ...]:
    merged_values = _merge_stackupx_substack_bool_values(source_values, overlay_values)
    return merged_values


def _merge_stackupx_substack_properties(
    source_values: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...],
    overlay_values: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...],
) -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
    values: dict[str, tuple[str, tuple[tuple[str, str, str], ...]]] = {
        _normalized_source_id(ref): (ref, tuple(properties))
        for ref, properties in source_values
        if _normalized_source_id(ref)
    }
    for ref, properties in overlay_values:
        key = _normalized_source_id(ref)
        if key:
            values[key] = (ref, tuple(properties))
    return tuple(values[key] for key in sorted(values))


def _merge_stackupx_substack_bool_values(
    source_values: tuple[tuple[str, bool], ...],
    overlay_values: tuple[tuple[str, bool], ...],
) -> tuple[tuple[str, bool], ...]:
    values: dict[str, tuple[str, bool]] = {
        _normalized_source_id(ref): (ref, bool(shared))
        for ref, shared in source_values
        if _normalized_source_id(ref)
    }
    for ref, shared in overlay_values:
        key = _normalized_source_id(ref)
        if key:
            values[key] = (ref, bool(shared))
    return tuple(values[key] for key in sorted(values))


def _stack_custom_data_auxiliary_stacks(
    stackupx: "AltiumStackupXDocument | None",
    substacks: tuple[AltiumStackSubstack, ...],
) -> tuple[AltiumStackSubstack, ...]:
    if stackupx is None:
        return ()
    known_refs = {
        _normalized_source_id(substack.source_stackup_ref)
        for substack in substacks
        if _normalized_source_id(substack.source_stackup_ref)
    }
    branch_refs = _stackupx_branch_stack_refs(stackupx)
    auxiliary: list[AltiumStackSubstack] = []
    for stack in tuple(getattr(stackupx, "stacks", ()) or ()):
        stack_ref = _text(getattr(stack, "id", ""))
        key = _normalized_source_id(stack_ref)
        if not key or key in known_refs or key not in branch_refs:
            continue
        auxiliary.append(
            AltiumStackSubstack(
                index=len(substacks) + len(auxiliary),
                field_family="stackupx_auxiliary",
                source_stackup_ref=stack_ref,
                name=_text(getattr(stack, "name", "")),
                is_flex=getattr(stack, "is_flex", None),
                raw_stackup_type=_text(getattr(stack, "stack_type", "")),
                stackupx_is_flex=getattr(stack, "is_flex", None),
                stackupx_stack_type=_text(getattr(stack, "stack_type", "")),
                enabled_layer_refs=(),
            )
        )
    return tuple(auxiliary)


def _stackupx_branch_stack_refs(
    stackupx: "AltiumStackupXDocument",
) -> set[str]:
    refs: set[str] = set()
    for branch in tuple(getattr(stackupx, "branches", ()) or ()):
        for section in tuple(getattr(branch, "sections", ()) or ()):
            for stack in tuple(getattr(section, "stacks", ()) or ()):
                for value in (
                    getattr(stack, "layer_stack_id", ""),
                    getattr(stack, "source_layer_stack_id", ""),
                    getattr(stack, "parent_layer_stack_id", ""),
                ):
                    key = _normalized_source_id(value)
                    if key:
                        refs.add(key)
    return refs


def _custom_layer_with_contexts(
    layer: AltiumStackLayer,
    substacks: tuple[AltiumStackSubstack, ...],
) -> AltiumStackLayer:
    contexts: list[tuple[str, int | None]] = []
    for substack in substacks:
        refs = {str(ref) for ref in tuple(substack.enabled_layer_refs or ())}
        if not refs:
            continue
        contexts.append(
            (
                substack.source_stackup_ref,
                0 if layer.registry_ref in refs else 1,
            )
        )
    return replace(layer, substack_contexts=tuple(contexts))


def _overlaid_optional_bool(
    source_value: bool | None,
    overlay_value: bool | None,
) -> bool | None:
    if overlay_value is None:
        return source_value
    if source_value is None or bool(overlay_value):
        return bool(overlay_value)
    return source_value


def _build_layer_pairs(
    board: object,
    stack_layers: list[AltiumStackLayer],
) -> tuple[AltiumLayerPair, ...]:
    pairs: list[AltiumLayerPair] = []
    for pair in tuple(getattr(board, "layer_pairs", ()) or ()):
        pairs.append(
            AltiumLayerPair(
                pair_index=int(getattr(pair, "pair_index", 0) or 0),
                low_layer_token=_text(getattr(pair, "low_layer_token", "")),
                high_layer_token=_text(getattr(pair, "high_layer_token", "")),
                source_substack_refs=tuple(
                    str(item)
                    for item in tuple(getattr(pair, "source_substack_refs", ()) or ())
                ),
                drill_pair_type_raw=getattr(pair, "drill_pair_type_raw", None),
                is_backdrill=getattr(pair, "is_backdrill", None),
                is_inverted=getattr(pair, "is_inverted", None),
                drill_guide=getattr(pair, "drill_guide", None),
                drill_drawing=getattr(pair, "drill_drawing", None),
            )
        )
    pairs.extend(
        _build_v8_span_layer_pairs(
            raw_record=getattr(board, "raw_record", {}) or {},
            stack_layers=stack_layers,
            pair_index_start=len(pairs),
        )
    )
    return tuple(pairs)


def _build_branches(
    raw_record: Mapping[str, object],
) -> tuple[AltiumStackBranch, ...]:
    branches = _build_v8_branches(raw_record)
    if branches:
        return branches
    return _build_stack_custom_data_branches(raw_record)


def _build_v8_branches(
    raw_record: Mapping[str, object],
) -> tuple[AltiumStackBranch, ...]:
    branch_groups = _group_indexed_fields(raw_record, _BRANCH_V8_RE)
    if not branch_groups:
        return ()

    section_groups: dict[str, dict[int, dict[str, str]]] = {}
    stack_groups: dict[tuple[str, str], dict[int, dict[str, str]]] = {}
    for key, value in raw_record.items():
        token = str(key or "")
        section_match = _BRANCHSECTION_V8_RE.match(token)
        if section_match is not None:
            branch_id = _normalized_source_id(section_match.group(1))
            index = int(section_match.group(2))
            suffix = section_match.group(3).upper()
            section_groups.setdefault(branch_id, {}).setdefault(index, {})[suffix] = (
                str(value)
            )
            continue
        stack_match = _BRANCHSECTIONSTACK_V8_RE.match(token)
        if stack_match is not None:
            branch_id = _normalized_source_id(stack_match.group(1))
            section_id = _normalized_source_id(stack_match.group(2))
            index = int(stack_match.group(3))
            suffix = stack_match.group(4).upper()
            stack_groups.setdefault((branch_id, section_id), {}).setdefault(index, {})[
                suffix
            ] = str(value)

    branches: list[AltiumStackBranch] = []
    for branch_index in sorted(branch_groups):
        fields = branch_groups[branch_index]
        branch_id = fields.get("ID", "") or _authoring_guid("branch", branch_index)
        branch_key = _normalized_source_id(branch_id)
        sections: list[AltiumStackBranchSection] = []
        for section_index in sorted(section_groups.get(branch_key, {})):
            section_fields = section_groups[branch_key][section_index]
            section_id = section_fields.get("ID", "") or _authoring_guid(
                "branch-section",
                branch_id,
                section_index,
            )
            section_key = _normalized_source_id(section_id)
            section_stacks = tuple(
                _branch_stack_from_v8_fields(stack_fields)
                for _stack_index, stack_fields in sorted(
                    stack_groups.get((branch_key, section_key), {}).items()
                )
            )
            sections.append(
                AltiumStackBranchSection(
                    source_section_id=section_id,
                    name=section_fields.get("NAME", "")
                    or f"Branch Section-{section_index + 1}",
                    parent_section_id=section_fields.get("PARENTSECTIONID", ""),
                    stacks=section_stacks,
                )
            )
        branches.append(
            AltiumStackBranch(
                source_branch_id=branch_id,
                name=fields.get("NAME", "") or f"Branch {branch_index + 1}",
                description=fields.get("DESCRIPTION", ""),
                parent_branch_id=fields.get("PARENTBRANCHID", ""),
                sections=tuple(sections),
            )
        )
    return tuple(branches)


def _branch_stack_from_v8_fields(
    fields: Mapping[str, str],
) -> AltiumStackBranchStack:
    return AltiumStackBranchStack(
        source_record_id=fields.get("ID", ""),
        source_stack_ref=fields.get("LAYERSTACKID", ""),
        description=fields.get("DESCRIPTION", ""),
        material_usage=_branch_material_usage(fields.get("MATERIALUSAGE", "")),
        source=fields.get("SOURCE", ""),
        parent_layer_id=fields.get("PARENTLAYERSOURCEID", ""),
        parent_layer_stack_id=fields.get("PARENTLAYERSTACKID", ""),
        source_layer_id=fields.get("LAYERSOURCEID", ""),
        source_layer_stack_id=fields.get("LAYERSOURCESTACKID", ""),
        is_left_intrusions_linked=_parse_altium_bool_token(
            fields.get("ISLEFTINTRUSUONSLINKED")
        ),
        intrusion_left_bottom=fields.get("INTRUSIONLEFTBOTTOM", ""),
        intrusion_left_top=fields.get("INTRUSIONLEFTTOP", ""),
        is_right_intrusions_linked=_parse_altium_bool_token(
            fields.get("ISRIGHTINTRUSIONLINKED")
        ),
        intrusion_right_bottom=fields.get("INTRUSIONRIGHTBOTTOM", ""),
        intrusion_right_top=fields.get("INTRUSIONRIGHTTOP", ""),
    )


def _build_stack_custom_data_branches(
    raw_record: Mapping[str, object],
) -> tuple[AltiumStackBranch, ...]:
    stackupx = _stack_custom_data_stackupx(raw_record)
    if stackupx is None:
        return ()
    return _branches_from_stackupx_objects(tuple(stackupx.branches or ()))


def _build_stack_custom_data_impedance(
    raw_record: Mapping[str, object],
) -> tuple[tuple[AltiumImpedanceProfile, ...], tuple[AltiumTransmissionLine, ...]]:
    stackupx = _stack_custom_data_stackupx(raw_record)
    if stackupx is None:
        return (), ()
    model = stackupx.to_layer_stack_document()
    return tuple(model.impedance_profiles or ()), tuple(model.transmission_lines or ())


def _stack_custom_data_stackupx(
    raw_record: Mapping[str, object],
) -> AltiumStackupXDocument | None:
    token = _text(raw_record.get("V9_STACKCUSTOMDATA"))
    if not token:
        return None
    try:
        xml_bytes = zlib.decompress(base64.b64decode(token))
        if xml_bytes.startswith(b"?<"):
            xml_bytes = xml_bytes[1:]
        from .altium_stackupx import AltiumStackupXDocument

        return AltiumStackupXDocument.from_bytes(xml_bytes)
    except Exception:
        return None


_STACKUP_ATTRIBUTE_V8_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("RoughnessType", "ROUGHNESSTYPE"),
    ("RoughnessFactorSR", "ROUGHNESSFACTORSR"),
    ("RoughnessFactorRF", "ROUGHNESSFACTORRF"),
    ("RealisticRatio", "REALISTICRATIO"),
    ("CopperResistance", "COPPERRESISTANCE"),
    ("ViaPlatingThickness", "VIAPLATINGTHICKNESS"),
    ("AmbientTemperature", "AMBIENTTEMPERATURE"),
    ("TemperatureRise", "TEMPERATURERISE"),
)


def _stackup_attributes_from_sources(
    raw_record: Mapping[str, object],
    stackupx: "AltiumStackupXDocument | None",
) -> tuple[tuple[str, str], ...]:
    if stackupx is not None and stackupx.stackup_attributes:
        return tuple(
            (str(key), str(value)) for key, value in stackupx.stackup_attributes
        )
    pairs: list[tuple[str, str]] = []
    for attr_name, v8_suffix in _STACKUP_ATTRIBUTE_V8_SUFFIXES:
        value = raw_record.get(f"LAYERMASTERSTACK_V8{v8_suffix}")
        if value is not None:
            pairs.append((attr_name, str(value)))
    return tuple(pairs)


def _branches_from_stackupx_objects(
    branches: Sequence[object],
) -> tuple[AltiumStackBranch, ...]:
    return tuple(
        AltiumStackBranch(
            source_branch_id=_text(getattr(branch, "id", "")),
            name=_text(getattr(branch, "name", "")),
            description=_text(getattr(branch, "description", "")),
            parent_branch_id=_text(getattr(branch, "parent_branch_id", "")),
            sections=tuple(
                AltiumStackBranchSection(
                    source_section_id=_text(getattr(section, "id", "")),
                    name=_text(getattr(section, "name", "")),
                    parent_section_id=_text(getattr(section, "parent_section_id", "")),
                    stacks=tuple(
                        AltiumStackBranchStack(
                            source_record_id=_text(getattr(stack, "id", "")),
                            source_stack_ref=_text(
                                getattr(stack, "layer_stack_id", "")
                            ),
                            description=_text(getattr(stack, "description", "")),
                            material_usage=_branch_material_usage(
                                getattr(stack, "material_usage", "")
                            ),
                            source=_text(getattr(stack, "source", "")),
                            parent_layer_id=_text(
                                getattr(stack, "parent_layer_id", "")
                            ),
                            parent_layer_stack_id=_text(
                                getattr(stack, "parent_layer_stack_id", "")
                            ),
                            source_layer_id=_text(
                                getattr(stack, "source_layer_id", "")
                            ),
                            source_layer_stack_id=_text(
                                getattr(stack, "source_layer_stack_id", "")
                            ),
                            is_left_intrusions_linked=getattr(
                                stack,
                                "is_left_intrusions_linked",
                                None,
                            ),
                            intrusion_left_bottom=_text(
                                getattr(stack, "intrusion_left_bottom", "")
                            ),
                            intrusion_left_top=_text(
                                getattr(stack, "intrusion_left_top", "")
                            ),
                            is_right_intrusions_linked=getattr(
                                stack,
                                "is_right_intrusions_linked",
                                None,
                            ),
                            intrusion_right_bottom=_text(
                                getattr(stack, "intrusion_right_bottom", "")
                            ),
                            intrusion_right_top=_text(
                                getattr(stack, "intrusion_right_top", "")
                            ),
                        )
                        for stack in tuple(getattr(section, "stacks", ()) or ())
                    ),
                )
                for section in tuple(getattr(branch, "sections", ()) or ())
            ),
        )
        for branch in tuple(branches or ())
    )


def _branch_material_usage(value: object) -> str:
    token = _text(value)
    if token == "1" or token.lower() == "individual":
        return "Individual"
    if token == "0" or token.lower() == "common":
        return "Common"
    return token


def _build_v8_impedance_profiles(
    raw_record: Mapping[str, object],
) -> tuple[AltiumImpedanceProfile, ...]:
    profile_groups = _group_indexed_fields(raw_record, _IMPEDANCE_PROFILE_RE)
    if not profile_groups:
        return ()

    line_counts_by_profile_id: dict[str, int] = {}
    for fields in _group_indexed_fields(raw_record, _TRACE_IMPEDANCE_RE).values():
        profile_id = _normalized_source_id(fields.get("PROFILEID"))
        if profile_id:
            line_counts_by_profile_id[profile_id] = (
                line_counts_by_profile_id.get(profile_id, 0) + 1
            )

    profiles: list[AltiumImpedanceProfile] = []
    for index in sorted(profile_groups):
        fields = profile_groups[index]
        profile_id = _text(fields.get("ID"))
        profiles.append(
            AltiumImpedanceProfile(
                index=index,
                source_family="v8",
                source_record_id=profile_id,
                name=_text(fields.get("NAME")),
                profile_type=_profile_type_from_raw(fields.get("TYPE")),
                profile_type_raw=_parse_int_token(fields.get("TYPE")),
                impedance=_text(fields.get("IMPEDANCE")),
                tolerance=_text(fields.get("TOLERANCE")),
                transmission_line_count=line_counts_by_profile_id.get(
                    _normalized_source_id(profile_id), 0
                ),
            )
        )
    return tuple(profiles)


def _build_v8_transmission_lines(
    raw_record: Mapping[str, object],
    stack_layers: Sequence[AltiumStackLayer],
) -> tuple[AltiumTransmissionLine, ...]:
    line_groups = _group_indexed_fields(raw_record, _TRACE_IMPEDANCE_RE)
    if not line_groups:
        return ()

    layer_tokens_by_source_id = _layer_tokens_by_source_id(stack_layers)
    lines: list[AltiumTransmissionLine] = []
    for index in sorted(line_groups):
        fields = line_groups[index]
        layer_id = _text(fields.get("LAYERID"))
        top_ref_id = _text(fields.get("TOPREFID"))
        bottom_ref_id = _text(fields.get("BOTREFID"))
        lines.append(
            AltiumTransmissionLine(
                index=index,
                source_family="v8",
                profile_id=_text(fields.get("PROFILEID")),
                substack_id=_text(fields.get("SUBSTACKID")),
                layer_id=layer_id,
                layer_token=layer_tokens_by_source_id.get(
                    _normalized_source_id(layer_id), ""
                ),
                is_differential=_parse_altium_bool_token(fields.get("DIFFERENTIAL")),
                top_ref_id=top_ref_id,
                top_ref_token=layer_tokens_by_source_id.get(
                    _normalized_source_id(top_ref_id), ""
                ),
                bottom_ref_id=bottom_ref_id,
                bottom_ref_token=layer_tokens_by_source_id.get(
                    _normalized_source_id(bottom_ref_id), ""
                ),
                width=_text(fields.get("WIDTH")),
                gap=_text(fields.get("GAP")),
                calc_mode_raw=_parse_int_token(fields.get("CALCMODE")),
                impedance_error=_text(fields.get("IMPEDANCEERROR")),
                tl_type_name=_text(fields.get("TLTYPENAME")),
                has_plating=_parse_altium_bool_token(fields.get("HASPLATING")),
                use_solder_mask=_parse_altium_bool_token(fields.get("USESOLDERMASK")),
                coated_height_1=_text(fields.get("COATEDHEIGHT1")),
                coated_height_2=_text(fields.get("COATEDHEIGHT2")),
                clearance_to_plane=_text(fields.get("CLEARANCETOPLANE")),
                electric_parameters=_electric_parameters_from_fields(fields),
            )
        )
    return tuple(lines)


def _build_v8_span_layer_pairs(
    *,
    raw_record: Mapping[str, object],
    stack_layers: list[AltiumStackLayer],
    pair_index_start: int,
) -> list[AltiumLayerPair]:
    groups: dict[tuple[str, str, int], dict[str, str]] = {}
    ordered_keys: list[tuple[str, str, int]] = []
    for key, value in raw_record.items():
        match = _V8_SPAN_RE.match(str(key or ""))
        if match is None:
            continue
        group_key = (
            match.group(1).upper(),
            match.group(2),
            int(match.group(3)),
        )
        if group_key not in groups:
            groups[group_key] = {}
            ordered_keys.append(group_key)
        groups[group_key][match.group(4).upper()] = _text(value)

    if not groups:
        return []

    layer_tokens_by_source_id = _layer_tokens_by_source_id(stack_layers)
    pairs: list[AltiumLayerPair] = []
    for kind, stack_ref, _span_index in ordered_keys:
        fields = groups[(kind, stack_ref, _span_index)]
        first_layer = _normalized_source_id(fields.get("FIRSTLAYERID"))
        last_layer = _normalized_source_id(fields.get("LASTLAYERID"))
        low_layer_token = layer_tokens_by_source_id.get(first_layer, "")
        high_layer_token = layer_tokens_by_source_id.get(last_layer, "")
        if not low_layer_token or not high_layer_token:
            continue
        is_backdrill = _parse_altium_bool_token(fields.get("BACKDRILL"))
        if is_backdrill is None and kind == "VIASPAN":
            is_backdrill = False
        pairs.append(
            AltiumLayerPair(
                pair_index=pair_index_start + len(pairs),
                low_layer_token=low_layer_token,
                high_layer_token=high_layer_token,
                source_substack_refs=(stack_ref,),
                drill_pair_type_raw=_parse_int_token(fields.get("TYPE")),
                is_backdrill=is_backdrill,
            )
        )
    return pairs


def _layer_pair_token(layer: AltiumStackLayer) -> str:
    return (
        _standard_token_from_legacy(layer.legacy_layer_id)
        or _text(layer.display_name)
        or _text(layer.registry_ref)
    )


def _resolve_authored_layer_pair_token(
    document: AltiumLayerStackDocument,
    value: object,
) -> str:
    if isinstance(value, PcbLayer):
        token = _standard_token_from_legacy(value.value)
        if token:
            return token
    if isinstance(value, int):
        token = _standard_token_from_legacy(value)
        if token:
            return token

    text = _text(value).strip()
    if not text:
        raise ValueError("Layer-pair endpoint must not be empty")

    compact = _compact_layer_pair_endpoint(text)
    if compact in {"TOP", "TOPLAYER"}:
        return "TOP"
    if compact in {"BOTTOM", "BOTTOMLAYER", "BOT", "BOTLAYER"}:
        return "BOTTOM"
    if re.fullmatch(r"MID\d+", compact):
        return compact
    if re.fullmatch(r"PLANE\d+", compact):
        return compact

    for stack in document.physical_stacks:
        for layer in stack.layers:
            token = _layer_pair_token(layer)
            candidates = (
                token,
                layer.display_name,
                layer.registry_ref,
                str(layer.legacy_layer_id) if layer.legacy_layer_id else "",
            )
            if any(
                candidate
                and (
                    text.upper() == candidate.upper()
                    or compact == _compact_layer_pair_endpoint(candidate)
                )
                for candidate in candidates
            ):
                return token

    return text.upper()


def _compact_layer_pair_endpoint(value: str) -> str:
    return re.sub(r"[\s_-]+", "", value).upper()


def _default_layer_pair_substack_refs(
    document: AltiumLayerStackDocument,
) -> tuple[str, ...]:
    if document.active_stack_ref:
        return (document.active_stack_ref,)
    for substack in document.substacks:
        if substack.source_stackup_ref:
            return (substack.source_stackup_ref,)
    for stack in document.physical_stacks:
        if stack.stack_ref:
            return (stack.stack_ref,)
    return ()


def _layer_tokens_by_source_id(
    stack_layers: Sequence[AltiumStackLayer],
) -> dict[str, str]:
    return {
        _normalized_source_id(layer.source_record_id): _layer_pair_token(layer)
        for layer in stack_layers
        if layer.source_record_id
    }


def _normalized_source_id(value: object) -> str:
    return _text(value).strip("{}").upper()


def _profile_type_from_raw(value: object) -> str:
    raw = _parse_int_token(value)
    if raw == 0:
        return "Single"
    if raw == 1:
        return "Differential"
    return _text(value)


def _electric_parameters_from_fields(
    fields: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    prefix = "_ELECTRICPARAMS_"
    return tuple(
        (key[len(prefix) :], value)
        for key, value in sorted(fields.items())
        if key.startswith(prefix)
    )


def _build_regions(
    board_regions: tuple[object, ...] | list[object] | None,
) -> tuple[AltiumStackRegion, ...]:
    regions: list[AltiumStackRegion] = []
    for region in tuple(board_regions or ()):
        bend_lines: list[AltiumStackBendLine] = []
        for line in tuple(getattr(region, "bending_lines", ()) or ()):
            bend_lines.append(
                AltiumStackBendLine(
                    angle_deg=getattr(line, "angle_deg", None),
                    radius_raw=getattr(line, "radius_raw", None),
                    fold_index=getattr(line, "fold_index", None),
                    x1=getattr(line, "x1", None),
                    y1=getattr(line, "y1", None),
                    x2=getattr(line, "x2", None),
                    y2=getattr(line, "y2", None),
                    raw_value=_text(getattr(line, "raw_value", "")),
                )
            )
        regions.append(
            AltiumStackRegion(
                name=_text(getattr(region, "name", "")),
                layerstack_id=_text(getattr(region, "layerstack_id", "")),
                locked_3d=bool(getattr(region, "locked_3d", False)),
                is_shapebased=bool(getattr(region, "is_shapebased", False)),
                cavity_height=_text(getattr(region, "cavity_height", "")),
                layer_token=_text(getattr(region, "layer_token", "")),
                v7_layer=_text(getattr(region, "v7_layer", "")),
                bending_lines=tuple(bend_lines),
                outline_vertices=tuple(
                    (float(getattr(vertex, "x_raw")), float(getattr(vertex, "y_raw")))
                    for vertex in tuple(getattr(region, "outline_vertices", ()) or ())
                ),
                hole_vertices=tuple(
                    tuple(
                        (
                            float(getattr(vertex, "x_raw")),
                            float(getattr(vertex, "y_raw")),
                        )
                        for vertex in tuple(hole or ())
                    )
                    for hole in tuple(getattr(region, "hole_vertices", ()) or ())
                ),
                raw_property_keys=tuple(
                    sorted(
                        str(key) for key in (getattr(region, "properties", {}) or {})
                    )
                ),
                raw_properties=tuple(
                    sorted(
                        (str(key), str(value))
                        for key, value in (
                            getattr(region, "properties", {}) or {}
                        ).items()
                    )
                ),
                extended_outline_tail_base64=base64.b64encode(
                    bytes(getattr(region, "_trailing_data", b"") or b"")
                ).decode("ascii"),
            )
        )
    return tuple(regions)
