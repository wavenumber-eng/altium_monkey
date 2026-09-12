"""Read Altium Layer Stack Manager `.stackup` text exports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from .altium_pcb_stream_helpers import format_bool_text as _format_bool_text
from .altium_pcb_stream_helpers import format_mil_value as _format_mil_value
from .altium_record_types import PcbLayer

if TYPE_CHECKING:
    from .altium_layer_stack_document import (
        AltiumImpedanceProfile,
        AltiumLayerStackDocument,
        AltiumPhysicalStack,
        AltiumStackLayer,
        AltiumTransmissionLine,
    )

_EXPORT_NAMESPACE = uuid.UUID("7a05657a-2f64-5f54-a9dd-f792362da62a")
_TYPE_ID_ABSTRACT = "{5EE2359D-AD77-4DAD-BD77-5A2334695235}"
_TYPE_ID_UNKNOWN = "{4AF561F4-6919-43D8-942A-D3579B371C26}"
_TYPE_ID_PHYSICAL = "{DAA9ECCC-0064-462D-854E-0B9E829EF563}"
_TYPE_ID_BASE_DIELECTRIC = "{56EB1CA5-014A-4387-A43E-1485F44F081E}"
_TYPE_ID_COPPER_SIGNAL = "{F4ECCD87-2CFB-4F37-BE50-4F3A272B4D01}"
_TYPE_ID_COPPER_PLANE = "{F59FAB94-C5ED-467D-94CD-F60A323C5D5B}"
_TYPE_ID_COPPER_FOIL = "{31E48829-E750-4C28-95E0-1A8313F0158E}"
_TYPE_ID_DIELECTRIC = "{92B02D5E-8D69-48A8-880E-AC4B77DB099D}"
_TYPE_ID_PREPREG = "{1A79611A-039D-4D40-A204-53C26C50F8B5}"
_TYPE_ID_CORE = "{136C62EF-1FA6-4897-AE71-7E797B632B92}"
_TYPE_ID_OVERLAY = "{C7EF040E-8D00-490B-B00C-A7E7823FF174}"
_TYPE_ID_SOLDER_MASK = "{7B384237-13D8-4318-8BCB-ACCD8D9A51E7}"
_TYPE_ID_BIKINI_COVERLAY = "{8DE65238-89ED-47C5-BBF8-0F6F02D1899C}"
_TYPE_ID_PASTE_MASK = "{E6A36257-B90F-45E5-9F8D-A213ECDB687C}"
_TYPE_ID_MECHANICAL = "{7A47E6B3-B591-41F7-A27B-8C6B0477E458}"
_TYPE_ID_SURFACE_FINISH = "{B0827674-798C-4CF8-807C-8E6C2A11C145}"
_TYPE_ID_PLATING = "{90B89AA0-A48A-45F4-82F5-B3ECA4EC8CCE}"
_TYPE_ID_ADHESIVE = "{448F9952-79BA-41D8-A8F4-4713EE7A3828}"
_TYPE_ID_STIFFENER = "{9FD889FA-C97A-401C-A066-E5F746678381}"
_TYPE_ID_MISC = "{786B5F28-F093-4084-BBA7-46E8F4F24F55}"
_TYPE_ID_MARKING = "{886956F5-B2E9-4114-93F3-F69AF872BFFB}"
_TYPE_ID_PE_LAYER = "{8053A284-8886-43E8-BAA0-5AA1D8DE97D1}"
_TYPE_ID_PE_CONDUCTIVE = "{C558FE3F-D914-4761-9D82-A12591CEB133}"
_TYPE_ID_PE_NONCONDUCTIVE = "{18BE3C27-BFEB-4BBA-8E9A-DF4CAEEE1731}"
_TYPE_ID_PE_ISOLATION = "{8590CC63-42D8-48CB-8307-72991910BCC9}"
_TYPE_ID_PE_CORE = "{E9A93AD8-17EE-438C-A1B6-92A3995DFDA8}"
_TYPE_ID_PE_PREPREG = "{743DE2F9-2902-4E35-B39C-BACD385F87DD}"
_LAYER_ID_SURFACE_FINISH = 16973824
_LSM_FAMILIES = frozenset(
    {
        "overlay",
        "solder_mask",
        "copper",
        "dielectric",
        "surface_finish",
        "plating",
        "misc",
        "marking",
    }
)
_ADVANCED_FAMILY_FLAGS = {
    "surface_finish": "ISSURFACEFINISH",
    "plating": "ISPLATING",
    "misc": "ISMISC",
    "marking": "ISMARK",
}
_FEATURE_ID_STANDARD = "{C8939E8A-FD0E-4D52-8860-B7A98F452016}"
_FEATURE_ID_RIGID_FLEX = "{5277E6A4-9E5F-4F54-951F-DC18CFEB7530}"
_FEATURE_ID_RIGID_FLEX_ADVANCED = "{4E697926-85C3-4C60-8B9B-E23CCB226764}"
_FEATURE_ID_IMPEDANCE = "{E3DF2B86-5F1B-49CA-B266-D1AE57F0BA6F}"
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


@dataclass(frozen=True)
class StackupTextFeature:
    id: str
    name: str

    def to_debug_json(self) -> dict[str, object]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class AltiumStackupDocument:
    version: str
    document_id: str
    revision_id: str
    revision_date: str
    records: tuple[tuple[str, str], ...]
    features: tuple[StackupTextFeature, ...]
    source_path: str = ""

    @classmethod
    def from_layer_stack_document(
        cls,
        document: "AltiumLayerStackDocument",
    ) -> "AltiumStackupDocument":
        """
        Build a rigid `.stackup` interchange document from the common model.
        """
        records = _records_from_layer_stack_document(document)
        mapping = dict(records)
        return cls(
            version=mapping.get("STACKUPVERSION", ""),
            document_id=mapping.get("STACKUPDOCUMENTID", ""),
            revision_id=mapping.get("STACKUPDOCUMENTREVISIONID", ""),
            revision_date=mapping.get("STACKUPDOCUMENTREVISIONDATE", ""),
            records=records,
            features=_parse_features(mapping),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "AltiumStackupDocument":
        source = Path(path)
        return cls.from_text(
            source.read_text(encoding="utf-8-sig"),
            source_path=str(source),
        )

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        source_path: str = "",
    ) -> "AltiumStackupDocument":
        return cls.from_text(data.decode("utf-8-sig"), source_path=source_path)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        source_path: str = "",
    ) -> "AltiumStackupDocument":
        records = _parse_stackup_record(text)
        mapping = dict(records)
        return cls(
            version=mapping.get("STACKUPVERSION", ""),
            document_id=mapping.get("STACKUPDOCUMENTID", ""),
            revision_id=mapping.get("STACKUPDOCUMENTREVISIONID", ""),
            revision_date=mapping.get("STACKUPDOCUMENTREVISIONDATE", ""),
            records=records,
            features=_parse_features(mapping),
            source_path=source_path,
        )

    def to_record_mapping(self) -> dict[str, str]:
        return dict(self.records)

    def to_text(self) -> str:
        """
        Serialize this document as Altium's pipe-delimited `.stackup` text.
        """
        return "|" + "|".join(f"{key}={value}" for key, value in self.records) + "\n"

    def write(self, path: str | Path) -> Path:
        """
        Write this document to `path` and return the resolved path object.
        """
        target = Path(path)
        target.write_text(self.to_text(), encoding="utf-8")
        return target

    def to_layer_stack_document(self) -> "AltiumLayerStackDocument":
        """
        Build an `AltiumLayerStackDocument` from the exported V8 LSM records.

        The text export is already shaped like `Board6/Data` Layer Stack
        Manager fields. Reuse the existing V8 board parser instead of adding a
        parallel decoder.
        """
        from .altium_board import AltiumBoard
        from .altium_layer_stack_document import AltiumLayerStackDocument

        return AltiumLayerStackDocument.from_board(
            AltiumBoard.from_record(self.to_record_mapping()),
            source_origin="stackup",
        )

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_path": self.source_path,
            "version": self.version,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "revision_date": self.revision_date,
            "record_count": len(self.records),
            "features": [feature.to_debug_json() for feature in self.features],
        }


def _parse_stackup_record(text: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in str(text or "").strip("\ufeff\x00\r\n ").split("|"):
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator:
            result.append((key.strip(), ""))
        else:
            result.append((key.strip(), value.strip()))
    return tuple(result)


def _parse_features(mapping: dict[str, str]) -> tuple[StackupTextFeature, ...]:
    features: list[StackupTextFeature] = []
    index = 0
    while True:
        feature_id = mapping.get(f"FEATURE_{index}ID")
        feature_name = mapping.get(f"FEATURE_{index}NAME")
        if feature_id is None and feature_name is None:
            break
        features.append(
            StackupTextFeature(
                id=feature_id or "",
                name=feature_name or "",
            )
        )
        index += 1
    return tuple(features)


def _records_from_layer_stack_document(
    document: "AltiumLayerStackDocument",
) -> tuple[tuple[str, str], ...]:
    stack = _single_rigid_stack(document)
    visible_layers = _lsm_visible_layers(stack)
    layer_ids = _v8_layer_ids(visible_layers)
    layer_record_ids = _v8_layer_record_ids(document, visible_layers)
    substacks = _export_substacks(document, stack)

    records: list[tuple[str, str]] = [
        ("STACKUPVERSION", "1"),
        ("STACKUPDOCUMENTID", _document_guid(document, stack)),
        ("STACKUPDOCUMENTREVISIONID", _stack_guid("revision", stack.display_name)),
        ("STACKUPDOCUMENTREVISIONDATE", "01/01/2000 00:00:00"),
    ]
    records.extend(_feature_records(document, substacks))
    records.extend(
        (
            ("LAYERMASTERSTACK_V8ID", _document_guid(document, stack)),
            ("LAYERMASTERSTACK_V8TYPE", "0"),
            ("LAYERMASTERSTACK_V8NAME", "Master layer stack"),
            ("LAYERMASTERSTACK_V8ISFLEX", _format_bool_text(False)),
        )
    )
    records.extend(_master_stack_attribute_records(document))
    records.extend(_substack_records(substacks))
    for index, layer in enumerate(visible_layers):
        records.extend(
            _layer_records(
                index=index,
                layer=layer,
                layer_id=layer_ids[index],
                layer_record_id=layer_record_ids[index],
                substacks=substacks,
            )
        )
    records.extend(
        _span_records(
            layer_pairs=tuple(document.layer_pairs or ()),
            substacks=substacks,
            layer_record_ids=layer_record_ids,
            visible_layers=visible_layers,
        )
    )
    records.extend(
        _branch_records(
            document=document,
            substacks=substacks,
            layer_record_ids=layer_record_ids,
            visible_layers=visible_layers,
        )
    )
    records.extend(
        _impedance_records(
            profiles=tuple(document.impedance_profiles or ()),
            transmission_lines=tuple(document.transmission_lines or ()),
            substacks=substacks,
            layer_record_ids=layer_record_ids,
            visible_layers=visible_layers,
        )
    )
    return tuple(records)


def _single_rigid_stack(document: "AltiumLayerStackDocument") -> "AltiumPhysicalStack":
    stacks = tuple(document.physical_stacks or ())
    if len(stacks) != 1:
        raise ValueError("Rigid .stackup export requires exactly one physical stack")
    stack = stacks[0]
    if bool(getattr(stack, "is_flex", False)):
        raise ValueError("Rigid .stackup export does not support flex stacks yet")
    return stack


def _feature_records(
    document: "AltiumLayerStackDocument",
    substacks: tuple["_ExportSubstack", ...],
) -> tuple[tuple[str, str], ...]:
    features = [
        (_FEATURE_ID_STANDARD, "Standard Stackup"),
    ]
    mode = _normalized_rigid_flex_mode(
        str(getattr(document, "rigid_flex_mode", "") or "")
    )
    if mode == "none" and any(substack.is_flex for substack in substacks):
        mode = "standard"
    if mode == "advanced":
        features.append((_FEATURE_ID_RIGID_FLEX_ADVANCED, "Rigid/Flex (Advanced)"))
        features.append((_FEATURE_ID_IMPEDANCE, "Impedance Calculator"))
        features.append((_FEATURE_ID_RIGID_FLEX, "Rigid/Flex"))
    elif mode == "standard":
        features.append((_FEATURE_ID_RIGID_FLEX, "Rigid/Flex"))
        features.append((_FEATURE_ID_IMPEDANCE, "Impedance Calculator"))
    else:
        features.append((_FEATURE_ID_IMPEDANCE, "Impedance Calculator"))

    records: list[tuple[str, str]] = []
    for index, (feature_id, feature_name) in enumerate(features):
        records.append((f"FEATURE_{index}ID", feature_id))
        records.append((f"FEATURE_{index}NAME", feature_name))
    return tuple(records)


def _normalized_rigid_flex_mode(value: str) -> str:
    token = str(value or "").strip().lower()
    return token if token in ("advanced", "standard") else "none"


@dataclass(frozen=True)
class _ExportSubstack:
    index: int
    source_ref: str
    export_id: str
    name: str
    is_flex: bool
    raw_stackup_type: str
    enabled_layer_refs: tuple[str, ...]


def _export_substacks(
    document: "AltiumLayerStackDocument",
    stack: "AltiumPhysicalStack",
) -> tuple[_ExportSubstack, ...]:
    source_substacks = tuple(document.substacks or ())
    if not source_substacks:
        export_id = _stack_guid(
            "substack",
            document.active_stack_ref,
            getattr(stack, "stack_ref", ""),
            getattr(stack, "display_name", ""),
        )
        return (
            _ExportSubstack(
                index=0,
                source_ref=export_id,
                export_id=export_id,
                name=str(getattr(stack, "display_name", "") or "Board Layer Stack"),
                is_flex=False,
                raw_stackup_type="0",
                enabled_layer_refs=(),
            ),
        )
    return tuple(
        _export_substack(index, substack)
        for index, substack in enumerate(source_substacks)
    )


def _export_substack(index: int, substack: object) -> _ExportSubstack:
    source_ref = str(getattr(substack, "source_stackup_ref", "") or "")
    export_id = _existing_or_stack_guid(
        source_ref,
        "substack",
        index,
        getattr(substack, "name", ""),
    )
    is_flex = bool(getattr(substack, "is_flex", False))
    return _ExportSubstack(
        index=index,
        source_ref=source_ref,
        export_id=export_id,
        name=str(getattr(substack, "name", "") or f"Layer Stack {index + 1}"),
        is_flex=is_flex,
        raw_stackup_type=str(
            getattr(substack, "raw_stackup_type", "") or ("1" if is_flex else "0")
        ),
        enabled_layer_refs=tuple(
            str(ref) for ref in tuple(getattr(substack, "enabled_layer_refs", ()) or ())
        ),
    )


def _substack_records(
    substacks: tuple[_ExportSubstack, ...],
) -> tuple[tuple[str, str], ...]:
    records: list[tuple[str, str]] = []
    for substack in substacks:
        prefix = f"LAYERSUBSTACK_V8_{substack.index}"
        records.extend(
            (
                (f"{prefix}ID", substack.export_id),
                (f"{prefix}TYPE", substack.raw_stackup_type),
                (f"{prefix}NAME", substack.name),
                (f"{prefix}SYMMETRIC", _format_bool_text(True)),
                (f"{prefix}ISFLEX", _format_bool_text(substack.is_flex)),
                (f"{prefix}SHOWTOPDIELECTRIC", _format_bool_text(False)),
                (f"{prefix}SHOWBOTTOMDIELECTRIC", _format_bool_text(False)),
                (f"{prefix}USEDBYPRIMS", _format_bool_text(False)),
                (f"{prefix}SERVICE", _format_bool_text(False)),
            )
        )
    return tuple(records)


def _master_stack_attribute_records(
    document: "AltiumLayerStackDocument",
) -> tuple[tuple[str, str], ...]:
    attrs = {str(key): str(value) for key, value in document.stackup_attributes}
    records: list[tuple[str, str]] = []
    for attr_name, v8_suffix in _STACKUP_ATTRIBUTE_V8_SUFFIXES:
        value = attrs.get(attr_name)
        if value is not None:
            records.append((f"LAYERMASTERSTACK_V8{v8_suffix}", value))
    return tuple(records)


def _lsm_visible_layers(stack: "AltiumPhysicalStack") -> tuple["AltiumStackLayer", ...]:
    layers = tuple(
        layer
        for layer in tuple(getattr(stack, "layers", ()) or ())
        if str(getattr(layer, "family", "")).strip().lower() != "paste"
    )
    unsupported = sorted(
        {
            str(getattr(layer, "family", "")).strip().lower()
            for layer in layers
            if str(getattr(layer, "family", "")).strip().lower() not in _LSM_FAMILIES
        }
    )
    if unsupported:
        raise ValueError(
            "Rigid .stackup export does not support layer families: "
            + ", ".join(unsupported)
        )
    if not any(
        str(getattr(layer, "family", "")).lower() == "copper" for layer in layers
    ):
        raise ValueError("Rigid .stackup export requires copper layers")
    return layers


def _document_guid(
    document: "AltiumLayerStackDocument",
    stack: "AltiumPhysicalStack",
) -> str:
    return _existing_or_stack_guid(
        document.active_stack_ref,
        getattr(stack, "stack_ref", ""),
        getattr(stack, "display_name", ""),
    )


def _v8_layer_record_ids(
    document: "AltiumLayerStackDocument",
    layers: tuple["AltiumStackLayer", ...],
) -> tuple[str, ...]:
    stack_seed = _document_guid(document, _single_rigid_stack(document))
    return tuple(
        _stack_guid(
            "layer",
            stack_seed,
            index,
            getattr(layer, "source_record_id", ""),
            getattr(layer, "display_name", ""),
            getattr(layer, "family", ""),
        )
        for index, layer in enumerate(layers)
    )


def _v8_layer_ids(layers: tuple[object, ...]) -> tuple[int, ...]:
    copper_count = _family_count(layers, "copper")
    copper_seen = 0
    dielectric_seen = 0
    result: list[int] = []
    for layer in layers:
        family = str(getattr(layer, "family", "")).strip().lower()
        source_layer_id = getattr(layer, "layer_id", None)
        if isinstance(source_layer_id, int) and source_layer_id > 0:
            result.append(source_layer_id)
            if family == "copper":
                copper_seen += 1
            elif family == "dielectric":
                dielectric_seen += 1
            continue
        legacy_layer_id = getattr(layer, "legacy_layer_id", None)
        saved_layer_id = _v8_saved_layer_id_from_legacy(legacy_layer_id)
        if saved_layer_id is not None:
            result.append(saved_layer_id)
            if family == "copper":
                copper_seen += 1
            elif family == "dielectric":
                dielectric_seen += 1
            continue
        if family == "copper":
            result.append(_synthetic_copper_v7_layer_id(copper_seen, copper_count))
            copper_seen += 1
        elif family == "dielectric":
            result.append(0x01040001 + dielectric_seen)
            dielectric_seen += 1
        elif family == "surface_finish":
            result.append(_LAYER_ID_SURFACE_FINISH)
        else:
            result.append(_synthetic_mask_overlay_layer_id(layer))
    return tuple(result)


def _layer_records(
    *,
    index: int,
    layer: "AltiumStackLayer",
    layer_id: int,
    layer_record_id: str,
    substacks: tuple[_ExportSubstack, ...],
) -> tuple[tuple[str, str], ...]:
    family = str(getattr(layer, "family", "")).strip().lower()
    prefix = f"LAYER_V8_{index}"
    records: list[tuple[str, str]] = [
        (f"{prefix}LAYERID", str(layer_id)),
        (f"{prefix}ID", layer_record_id),
        (f"{prefix}TYPEID", _type_id_for_layer(layer)),
    ]
    records.extend(_advanced_layer_flag_records(prefix, layer))
    records.extend(
        (
            (
                f"{prefix}NAME",
                str(getattr(layer, "display_name", "") or f"Layer {index}"),
            ),
            (f"{prefix}ISHIDDEN", _format_bool_text(False)),
            (f"{prefix}USEDBYPRIMS", _format_bool_text(False)),
        )
    )
    for substack in substacks:
        records.extend(_layer_context_records(prefix, layer, substack))
    if family == "copper":
        _append_optional_mil(
            records, f"{prefix}COPTHICK", layer, "copper_thickness_mils"
        )
        component_placement = getattr(layer, "component_placement", None)
        if component_placement is not None:
            records.append(
                (f"{prefix}COMPONENTPLACEMENT", str(int(component_placement)))
            )
    elif family in {"dielectric", "solder_mask"}:
        _append_dielectric_records(records, prefix, layer)
    elif family in {"surface_finish", "plating", "misc", "marking"}:
        _append_stackupx_property_records(records, prefix, layer)
    return tuple(records)


def _advanced_layer_flag_records(
    prefix: str,
    layer: "AltiumStackLayer",
) -> tuple[tuple[str, str], ...]:
    records: list[tuple[str, str]] = []
    is_stiffener = getattr(layer, "is_stiffener", None)
    if is_stiffener is not None:
        records.append((f"{prefix}ISSTIFFENER", _format_bool_text(bool(is_stiffener))))
    is_adhesive = getattr(layer, "is_adhesive", None)
    if is_adhesive is not None:
        records.append((f"{prefix}ISADHESIVE", _format_bool_text(bool(is_adhesive))))
    family = str(getattr(layer, "family", "")).strip().lower()
    flag = _ADVANCED_FAMILY_FLAGS.get(family)
    if flag is not None:
        records.append((f"{prefix}{flag}", _format_bool_text(True)))
        records.append((f"{prefix}ISADVANCED", _format_bool_text(True)))
    return tuple(records)


def _layer_context_records(
    prefix: str,
    layer: "AltiumStackLayer",
    substack: _ExportSubstack,
) -> tuple[tuple[str, str], ...]:
    enabled = _layer_enabled_for_substack(layer, substack)
    records: list[tuple[str, str]] = [
        (
            f"{prefix}_{substack.export_id}NAME",
            str(getattr(layer, "display_name", "")),
        ),
        (f"{prefix}_{substack.export_id}CONTEXT", "0" if enabled else "1"),
        (f"{prefix}_{substack.export_id}USEDBYPRIMS", _format_bool_text(False)),
        (f"{prefix}_{substack.export_id}SHARED", "0"),
    ]
    _append_stackupx_property_records(records, f"{prefix}_{substack.export_id}", layer)
    return tuple(records)


def _layer_enabled_for_substack(
    layer: "AltiumStackLayer",
    substack: _ExportSubstack,
) -> bool:
    if substack.enabled_layer_refs:
        return str(getattr(layer, "registry_ref", "")) in substack.enabled_layer_refs
    checker = getattr(layer, "is_enabled_for_substack", None)
    if callable(checker):
        return bool(checker(substack.source_ref or substack.export_id))
    return True


def _append_dielectric_records(
    records: list[tuple[str, str]],
    prefix: str,
    layer: "AltiumStackLayer",
) -> None:
    dielectric_type = getattr(layer, "dielectric_type", None)
    if dielectric_type is not None:
        records.append((f"{prefix}DIELTYPE", str(int(dielectric_type))))
    _append_optional_float(records, f"{prefix}DIELCONST", layer, "dielectric_constant")
    _append_optional_float(
        records,
        f"{prefix}DIELLOSSTANGENT",
        layer,
        "dielectric_loss_tangent",
    )
    _append_optional_mil(
        records, f"{prefix}DIELHEIGHT", layer, "dielectric_height_mils"
    )
    material = str(getattr(layer, "dielectric_material", "") or "")
    if material:
        records.append((f"{prefix}DIELMATERIAL", material))
    coverlay_expansion = str(getattr(layer, "coverlay_expansion", "") or "")
    if coverlay_expansion:
        records.append((f"{prefix}COVERLAY_EXPANSION", coverlay_expansion))
    _append_stackupx_property_records(records, prefix, layer)


def _append_stackupx_property_records(
    records: list[tuple[str, str]],
    prefix: str,
    layer: "AltiumStackLayer",
) -> None:
    for name, _type_name, value in layer.stackupx_properties:
        property_name = str(name or "").strip()
        if property_name:
            records.append((f"{prefix}$LSM${property_name}", str(value or "")))


def _append_optional_mil(
    records: list[tuple[str, str]],
    key: str,
    source: object,
    attr: str,
) -> None:
    value = getattr(source, attr, None)
    if value is not None:
        records.append((key, _format_mil_value(float(value))))


def _append_optional_float(
    records: list[tuple[str, str]],
    key: str,
    source: object,
    attr: str,
) -> None:
    value = getattr(source, attr, None)
    if value is not None:
        records.append((key, format(float(value), ".12g")))


def _span_records(
    *,
    layer_pairs: tuple[object, ...],
    substacks: tuple[_ExportSubstack, ...],
    layer_record_ids: tuple[str, ...],
    visible_layers: tuple[object, ...],
) -> tuple[tuple[str, str], ...]:
    token_indexes = _layer_indexes_by_pair_token(visible_layers)
    substack_ids_by_source = _substack_ids_by_source(substacks)
    default_substack_id = substacks[0].export_id if substacks else ""
    pairs = layer_pairs or _default_layer_pairs(visible_layers)
    via_indexes: dict[str, int] = {}
    drill_indexes: dict[str, int] = {}
    records: list[tuple[str, str]] = []
    for pair in pairs:
        first = token_indexes.get(_pair_token(getattr(pair, "low_layer_token", "")))
        last = token_indexes.get(_pair_token(getattr(pair, "high_layer_token", "")))
        if first is None or last is None:
            continue
        pair_substack_ids = _pair_export_substack_ids(
            pair,
            substack_ids_by_source,
            default_substack_id,
        )
        if bool(getattr(pair, "is_backdrill", False)):
            for substack_id in pair_substack_ids:
                drill_index = drill_indexes.get(substack_id, 0)
                records.extend(
                    _drill_pair_records(
                        substack_id=substack_id,
                        span_index=drill_index,
                        first_layer_id=layer_record_ids[first],
                        last_layer_id=layer_record_ids[last],
                        name=_span_name("BD", first, last, visible_layers),
                    )
                )
                drill_indexes[substack_id] = drill_index + 1
        else:
            for substack_id in pair_substack_ids:
                via_index = via_indexes.get(substack_id, 0)
                records.extend(
                    _via_span_records(
                        substack_id=substack_id,
                        span_index=via_index,
                        first_layer_id=layer_record_ids[first],
                        last_layer_id=layer_record_ids[last],
                        name=_span_name(
                            _via_span_name_prefix(
                                getattr(pair, "drill_pair_type_raw", None)
                            ),
                            first,
                            last,
                            visible_layers,
                        ),
                        span_type_raw=_via_span_type_raw(pair),
                    ),
                )
                via_indexes[substack_id] = via_index + 1
    return tuple(records)


def _substack_ids_by_source(
    substacks: tuple[_ExportSubstack, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for substack in substacks:
        for value in (substack.source_ref, substack.export_id):
            key = _normalized_guid_key(value)
            if key:
                result[key] = substack.export_id
    return result


def _pair_export_substack_ids(
    pair: object,
    substack_ids_by_source: dict[str, str],
    default_substack_id: str,
) -> tuple[str, ...]:
    refs = tuple(getattr(pair, "source_substack_refs", ()) or ())
    export_ids = tuple(
        substack_ids_by_source.get(_normalized_guid_key(ref), "")
        for ref in refs
        if substack_ids_by_source.get(_normalized_guid_key(ref), "")
    )
    if export_ids:
        return tuple(dict.fromkeys(export_ids))
    return (default_substack_id,) if default_substack_id else ()


def _default_layer_pairs(visible_layers: tuple[object, ...]) -> tuple[object, ...]:
    copper_indexes = [
        index
        for index, layer in enumerate(visible_layers)
        if str(getattr(layer, "family", "")).strip().lower() == "copper"
    ]
    if len(copper_indexes) < 2:
        return ()
    return (_ExportLayerPair("TOP", "BOTTOM", 0, False),)


@dataclass(frozen=True)
class _ExportLayerPair:
    low_layer_token: str
    high_layer_token: str
    drill_pair_type_raw: int | None
    is_backdrill: bool


def _via_span_records(
    *,
    substack_id: str,
    span_index: int,
    first_layer_id: str,
    last_layer_id: str,
    name: str,
    span_type_raw: int,
) -> tuple[tuple[str, str], ...]:
    prefix = f"VIASPAN_V8_{substack_id}_{span_index}"
    return (
        (f"{prefix}ID", _stack_guid("viaspan", substack_id, span_index)),
        (f"{prefix}NAME", name),
        (f"{prefix}TYPE", str(span_type_raw)),
        (f"{prefix}FIRSTLAYERID", first_layer_id),
        (f"{prefix}LASTLAYERID", last_layer_id),
        (f"{prefix}SYMMETRIC", _format_bool_text(False)),
    )


def _drill_pair_records(
    *,
    substack_id: str,
    span_index: int,
    first_layer_id: str,
    last_layer_id: str,
    name: str,
) -> tuple[tuple[str, str], ...]:
    prefix = f"DRILLPAIR_V8_{substack_id}_{span_index}"
    return (
        (f"{prefix}ID", _stack_guid("drillpair", substack_id, span_index)),
        (f"{prefix}NAME", name),
        (f"{prefix}FIRSTLAYERID", first_layer_id),
        (f"{prefix}LASTLAYERID", last_layer_id),
        (f"{prefix}BACKDRILL", _format_bool_text(True)),
        (f"{prefix}SYMMETRIC", _format_bool_text(False)),
    )


def _branch_records(
    *,
    document: "AltiumLayerStackDocument",
    substacks: tuple[_ExportSubstack, ...],
    layer_record_ids: tuple[str, ...],
    visible_layers: tuple[object, ...],
) -> tuple[tuple[str, str], ...]:
    source_branches = tuple(getattr(document, "branches", ()) or ())
    if source_branches:
        return _branch_records_from_topology(source_branches)
    if not any(substack.is_flex for substack in substacks):
        return ()

    branch_id = _stack_guid("branch", document.active_stack_ref, "board")
    records: list[tuple[str, str]] = [
        ("BRANCH_V8_0ID", branch_id),
        ("BRANCH_V8_0NAME", "Board"),
    ]
    parent_section_id = ""
    parent_substack = _first_rigid_substack(substacks) or substacks[0]
    source_layer_id = _branch_source_layer_id(layer_record_ids, visible_layers)
    for index, substack in enumerate(substacks):
        section_id = _stack_guid("branch-section", branch_id, index)
        section_prefix = f"BRANCHSECTION_V8_{branch_id}_{index}"
        records.append((f"{section_prefix}ID", section_id))
        records.append((f"{section_prefix}NAME", f"Branch Section-{index + 1}"))
        if parent_section_id:
            records.append((f"{section_prefix}PARENTSECTIONID", parent_section_id))
        records.extend(
            _branch_section_stack_records(
                branch_id=branch_id,
                section_id=section_id,
                substack=substack,
                parent_substack=parent_substack,
                source_layer_id=source_layer_id,
            )
        )
        parent_section_id = section_id
        if not substack.is_flex:
            parent_substack = substack
    return tuple(records)


def _branch_records_from_topology(
    branches: tuple[object, ...],
) -> tuple[tuple[str, str], ...]:
    records: list[tuple[str, str]] = []
    for branch_index, branch in enumerate(branches):
        branch_id = _existing_or_stack_guid(
            getattr(branch, "source_branch_id", ""),
            "branch",
            branch_index,
            getattr(branch, "name", ""),
        )
        records.append((f"BRANCH_V8_{branch_index}ID", branch_id))
        records.append(
            (
                f"BRANCH_V8_{branch_index}NAME",
                str(getattr(branch, "name", "") or f"Branch {branch_index + 1}"),
            )
        )
        description = str(getattr(branch, "description", "") or "")
        if description:
            records.append((f"BRANCH_V8_{branch_index}DESCRIPTION", description))
        parent_branch_id = _existing_or_empty_stack_guid(
            getattr(branch, "parent_branch_id", "")
        )
        if parent_branch_id:
            records.append(
                (f"BRANCH_V8_{branch_index}PARENTBRANCHID", parent_branch_id)
            )
        for section_index, section in enumerate(
            tuple(getattr(branch, "sections", ()) or ())
        ):
            section_id = _existing_or_stack_guid(
                getattr(section, "source_section_id", ""),
                "branch-section",
                branch_id,
                section_index,
            )
            section_prefix = f"BRANCHSECTION_V8_{branch_id}_{section_index}"
            records.append((f"{section_prefix}ID", section_id))
            records.append(
                (
                    f"{section_prefix}NAME",
                    str(
                        getattr(section, "name", "")
                        or f"Branch Section-{section_index + 1}"
                    ),
                )
            )
            parent_section_id = _existing_or_empty_stack_guid(
                getattr(section, "parent_section_id", "")
            )
            if parent_section_id:
                records.append((f"{section_prefix}PARENTSECTIONID", parent_section_id))
            for stack_index, stack in enumerate(
                tuple(getattr(section, "stacks", ()) or ())
            ):
                records.extend(
                    _branch_section_stack_records_from_topology(
                        branch_id=branch_id,
                        section_id=section_id,
                        stack_index=stack_index,
                        stack=stack,
                    )
                )
    return tuple(records)


def _branch_section_stack_records_from_topology(
    *,
    branch_id: str,
    section_id: str,
    stack_index: int,
    stack: object,
) -> tuple[tuple[str, str], ...]:
    prefix = f"BRANCHSECTIONSTACK_V8_{branch_id}_{section_id}_{stack_index}"
    layer_stack_id = _existing_or_stack_guid(
        getattr(stack, "source_stack_ref", ""),
        "branch-section-stack-layer-stack",
        section_id,
        stack_index,
    )
    description = str(getattr(stack, "description", "") or "")
    records: list[tuple[str, str]] = [
        (
            f"{prefix}ID",
            _existing_or_stack_guid(
                getattr(stack, "source_record_id", ""),
                "branch-section-stack",
                section_id,
                stack_index,
            ),
        ),
        (f"{prefix}SOURCE", _stackup_branch_source_raw(getattr(stack, "source", ""))),
        (
            f"{prefix}ISLEFTINTRUSUONSLINKED",
            _format_bool_text(
                _intrusion_linked_value(
                    getattr(stack, "is_left_intrusions_linked", None)
                )
            ),
        ),
        (
            f"{prefix}INTRUSIONLEFTBOTTOM",
            _intrusion_value(getattr(stack, "intrusion_left_bottom", "")),
        ),
        (
            f"{prefix}INTRUSIONLEFTTOP",
            _intrusion_value(getattr(stack, "intrusion_left_top", "")),
        ),
        (
            f"{prefix}ISRIGHTINTRUSIONLINKED",
            _format_bool_text(
                _intrusion_linked_value(
                    getattr(stack, "is_right_intrusions_linked", None)
                )
            ),
        ),
        (
            f"{prefix}INTRUSIONRIGHTBOTTOM",
            _intrusion_value(getattr(stack, "intrusion_right_bottom", "")),
        ),
        (
            f"{prefix}INTRUSIONRIGHTTOP",
            _intrusion_value(getattr(stack, "intrusion_right_top", "")),
        ),
        (f"{prefix}LAYERSTACKID", layer_stack_id),
    ]
    if description:
        records.insert(2, (f"{prefix}DESCRIPTION", description))
    optional_guid_fields = (
        ("LAYERSOURCEID", "source_layer_id"),
        ("LAYERSOURCESTACKID", "source_layer_stack_id"),
        ("PARENTLAYERSOURCEID", "parent_layer_id"),
        ("PARENTLAYERSTACKID", "parent_layer_stack_id"),
    )
    for field_name, attr_name in optional_guid_fields:
        value = _existing_or_empty_stack_guid(getattr(stack, attr_name, ""))
        if value:
            records.append((f"{prefix}{field_name}", value))
    records.append(
        (
            f"{prefix}MATERIALUSAGE",
            _stackup_material_usage_raw(getattr(stack, "material_usage", "")),
        )
    )
    return tuple(records)


def _existing_or_empty_stack_guid(value: object) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    return _existing_or_stack_guid(token)


def _intrusion_value(value: object) -> str:
    token = str(value or "").strip()
    return token or "0m"


def _intrusion_linked_value(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if not token:
        return True
    return token not in {"0", "false", "no"}


def _stackup_material_usage_raw(value: object) -> str:
    token = str(value or "").strip()
    if token == "1" or token.lower() == "individual":
        return "1"
    return "0"


def _stackup_branch_source_raw(value: object) -> str:
    token = str(value or "").strip()
    if not token or token.lower() == "design":
        return "0"
    return token


def _first_rigid_substack(
    substacks: tuple[_ExportSubstack, ...],
) -> _ExportSubstack | None:
    return next((substack for substack in substacks if not substack.is_flex), None)


def _branch_source_layer_id(
    layer_record_ids: tuple[str, ...],
    visible_layers: tuple[object, ...],
) -> str:
    for layer_id, layer in zip(layer_record_ids, visible_layers):
        if str(getattr(layer, "family", "")).strip().lower() == "copper":
            return layer_id
    return layer_record_ids[0] if layer_record_ids else ""


def _branch_section_stack_records(
    *,
    branch_id: str,
    section_id: str,
    substack: _ExportSubstack,
    parent_substack: _ExportSubstack,
    source_layer_id: str,
) -> tuple[tuple[str, str], ...]:
    prefix = f"BRANCHSECTIONSTACK_V8_{branch_id}_{section_id}_0"
    records: list[tuple[str, str]] = [
        (f"{prefix}ID", _stack_guid("branch-section-stack", substack.export_id)),
        (f"{prefix}SOURCE", "0"),
        (f"{prefix}DESCRIPTION", _branch_stack_description(substack)),
        (f"{prefix}ISLEFTINTRUSUONSLINKED", "True"),
        (f"{prefix}INTRUSIONLEFTBOTTOM", "0m"),
        (f"{prefix}INTRUSIONLEFTTOP", "0m"),
        (f"{prefix}ISRIGHTINTRUSIONLINKED", "True"),
        (f"{prefix}INTRUSIONRIGHTBOTTOM", "0m"),
        (f"{prefix}INTRUSIONRIGHTTOP", "0m"),
        (f"{prefix}LAYERSTACKID", substack.export_id),
    ]
    if substack.is_flex and source_layer_id:
        records.extend(
            (
                (f"{prefix}LAYERSOURCEID", source_layer_id),
                (f"{prefix}LAYERSOURCESTACKID", substack.export_id),
                (f"{prefix}PARENTLAYERSOURCEID", source_layer_id),
                (f"{prefix}PARENTLAYERSTACKID", parent_substack.export_id),
            )
        )
    records.append((f"{prefix}MATERIALUSAGE", "0"))
    return tuple(records)


def _branch_stack_description(substack: _ExportSubstack) -> str:
    return "Board Layer Stack" if not substack.is_flex else substack.name


def _layer_indexes_by_pair_token(visible_layers: tuple[object, ...]) -> dict[str, int]:
    copper_indexes = [
        index
        for index, layer in enumerate(visible_layers)
        if str(getattr(layer, "family", "")).strip().lower() == "copper"
    ]
    if not copper_indexes:
        return {}
    tokens: dict[str, int] = {}
    for layer_index in copper_indexes:
        token = _pair_token_from_legacy_layer_id(
            getattr(visible_layers[layer_index], "legacy_layer_id", None)
        )
        if token:
            tokens[token] = layer_index
    tokens.setdefault("TOP", copper_indexes[0])
    tokens.setdefault("BOTTOM", copper_indexes[-1])
    for internal_index, layer_index in enumerate(copper_indexes[1:-1], start=1):
        tokens.setdefault(f"MID{internal_index}", layer_index)
        tokens.setdefault(f"PLANE{internal_index}", layer_index)
    return tokens


def _pair_token_from_legacy_layer_id(value: object) -> str:
    if not isinstance(value, int):
        return ""
    if value == PcbLayer.TOP.value:
        return "TOP"
    if value == PcbLayer.BOTTOM.value:
        return "BOTTOM"
    if PcbLayer.TOP.value < value < PcbLayer.BOTTOM.value:
        return f"MID{value - PcbLayer.TOP.value}"
    if PcbLayer.INTERNAL_PLANE_1.value <= value <= PcbLayer.INTERNAL_PLANE_16.value:
        return f"PLANE{value - PcbLayer.INTERNAL_PLANE_1.value + 1}"
    return ""


def _pair_token(value: object) -> str:
    return str(value or "").strip().upper()


def _via_span_type_raw(pair: object) -> int:
    raw = getattr(pair, "drill_pair_type_raw", None)
    if isinstance(raw, int):
        return raw
    return 0


def _via_span_name_prefix(span_type_raw: object) -> str:
    if span_type_raw == 2:
        return "Buried"
    if span_type_raw == 3:
        return "MicroVia"
    return "Thru"


def _span_name(
    prefix: str,
    first_layer_index: int,
    last_layer_index: int,
    visible_layers: tuple[object, ...],
) -> str:
    copper_indexes = [
        index
        for index, layer in enumerate(visible_layers)
        if str(getattr(layer, "family", "")).strip().lower() == "copper"
    ]
    first = copper_indexes.index(first_layer_index) + 1
    last = copper_indexes.index(last_layer_index) + 1
    return f"{prefix} {first}:{last}"


def _impedance_records(
    *,
    profiles: tuple["AltiumImpedanceProfile", ...],
    transmission_lines: tuple["AltiumTransmissionLine", ...],
    substacks: tuple[_ExportSubstack, ...],
    layer_record_ids: tuple[str, ...],
    visible_layers: tuple[object, ...],
) -> tuple[tuple[str, str], ...]:
    if not profiles:
        return ()
    profile_ids = _export_profile_ids(profiles)
    records: list[tuple[str, str]] = []
    for index, profile in enumerate(profiles):
        records.extend(_impedance_profile_records(index, profile, profile_ids[index]))
    records.extend(
        _trace_impedance_records(
            transmission_lines=transmission_lines,
            profile_ids=profile_ids,
            profiles=profiles,
            substacks=substacks,
            layer_record_ids=layer_record_ids,
            visible_layers=visible_layers,
        )
    )
    return tuple(records)


def _export_profile_ids(
    profiles: tuple["AltiumImpedanceProfile", ...],
) -> tuple[str, ...]:
    return tuple(
        _existing_or_stack_guid(
            getattr(profile, "source_record_id", ""),
            "impedance-profile",
            index,
            getattr(profile, "name", ""),
        )
        for index, profile in enumerate(profiles)
    )


def _impedance_profile_records(
    index: int,
    profile: "AltiumImpedanceProfile",
    profile_id: str,
) -> tuple[tuple[str, str], ...]:
    prefix = f"IMPEDANCEPROFILE_V8_{index}"
    return (
        (f"{prefix}ID", profile_id),
        (f"{prefix}NAME", str(getattr(profile, "name", "") or f"Profile {index}")),
        (f"{prefix}TYPE", str(_impedance_profile_type_raw(profile))),
        (f"{prefix}IMPEDANCE", str(getattr(profile, "impedance", "") or "")),
        (f"{prefix}TOLERANCE", str(getattr(profile, "tolerance", "") or "")),
    )


def _trace_impedance_records(
    *,
    transmission_lines: tuple["AltiumTransmissionLine", ...],
    profile_ids: tuple[str, ...],
    profiles: tuple["AltiumImpedanceProfile", ...],
    substacks: tuple[_ExportSubstack, ...],
    layer_record_ids: tuple[str, ...],
    visible_layers: tuple[object, ...],
) -> tuple[tuple[str, str], ...]:
    layer_ids_by_token = _layer_record_ids_by_pair_token(
        visible_layers,
        layer_record_ids,
    )
    substack_ids_by_source = _substack_ids_by_source(substacks)
    default_substack_id = substacks[0].export_id if substacks else ""
    profile_ids_by_source = _profile_ids_by_source(profiles, profile_ids)
    records: list[tuple[str, str]] = []
    for index, line in enumerate(transmission_lines):
        profile_id = _remapped_profile_id(line, profile_ids_by_source)
        layer_id = layer_ids_by_token.get(_pair_token(getattr(line, "layer_token", "")))
        line_substack_id = _line_export_substack_id(
            line,
            substack_ids_by_source,
            default_substack_id,
        )
        if not profile_id or not layer_id:
            continue
        records.extend(
            _trace_impedance_line_records(
                index=index,
                line=line,
                profile_id=profile_id,
                substack_id=line_substack_id,
                layer_id=layer_id,
                top_ref_id=layer_ids_by_token.get(
                    _pair_token(getattr(line, "top_ref_token", ""))
                ),
                bottom_ref_id=layer_ids_by_token.get(
                    _pair_token(getattr(line, "bottom_ref_token", ""))
                ),
            )
        )
    return tuple(records)


def _line_export_substack_id(
    line: "AltiumTransmissionLine",
    substack_ids_by_source: dict[str, str],
    default_substack_id: str,
) -> str:
    source_id = _normalized_guid_key(getattr(line, "substack_id", ""))
    if source_id:
        return substack_ids_by_source.get(source_id, default_substack_id)
    return default_substack_id


def _trace_impedance_line_records(
    *,
    index: int,
    line: "AltiumTransmissionLine",
    profile_id: str,
    substack_id: str,
    layer_id: str,
    top_ref_id: str | None,
    bottom_ref_id: str | None,
) -> tuple[tuple[str, str], ...]:
    prefix = f"TRACEIMPEDANCE_V8_{index}"
    records: list[tuple[str, str]] = [
        (f"{prefix}LAYERID", layer_id),
        (f"{prefix}SUBSTACKID", substack_id),
        (f"{prefix}PROFILEID", profile_id),
    ]
    _append_optional_bool(records, f"{prefix}DIFFERENTIAL", line, "is_differential")
    if top_ref_id:
        records.append((f"{prefix}TOPREFID", top_ref_id))
    if bottom_ref_id:
        records.append((f"{prefix}BOTREFID", bottom_ref_id))
    _append_optional_text(records, f"{prefix}WIDTH", line, "width")
    _append_optional_text(records, f"{prefix}GAP", line, "gap")
    _append_trace_calc_mode(records, f"{prefix}CALCMODE", line)
    _append_optional_text(records, f"{prefix}IMPEDANCEERROR", line, "impedance_error")
    _append_optional_text(records, f"{prefix}TLTYPENAME", line, "tl_type_name")
    _append_optional_bool(records, f"{prefix}HASPLATING", line, "has_plating")
    _append_optional_bool(records, f"{prefix}USESOLDERMASK", line, "use_solder_mask")
    _append_optional_text(records, f"{prefix}COATEDHEIGHT1", line, "coated_height_1")
    _append_optional_text(records, f"{prefix}COATEDHEIGHT2", line, "coated_height_2")
    _append_optional_text(
        records,
        f"{prefix}CLEARANCETOPLANE",
        line,
        "clearance_to_plane",
    )
    for key, value in line.electric_parameters:
        records.append((f"{prefix}_ELECTRICPARAMS_{key}", str(value)))
    return tuple(records)


def _layer_record_ids_by_pair_token(
    visible_layers: tuple[object, ...],
    layer_record_ids: tuple[str, ...],
) -> dict[str, str]:
    return {
        token: layer_record_ids[index]
        for token, index in _layer_indexes_by_pair_token(visible_layers).items()
    }


def _profile_ids_by_source(
    profiles: tuple[object, ...],
    profile_ids: tuple[str, ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, profile in enumerate(profiles):
        source_id = _normalized_guid_key(getattr(profile, "source_record_id", ""))
        if source_id:
            result[source_id] = profile_ids[index]
    return result


def _remapped_profile_id(
    line: object,
    profile_ids_by_source: dict[str, str],
) -> str:
    source_id = _normalized_guid_key(getattr(line, "profile_id", ""))
    if source_id:
        return profile_ids_by_source.get(source_id, "")
    return ""


def _impedance_profile_type_raw(profile: object) -> int:
    raw = getattr(profile, "profile_type_raw", None)
    if isinstance(raw, int):
        return raw
    token = str(getattr(profile, "profile_type", "") or "").strip().lower()
    return 1 if token == "differential" else 0


def _append_optional_bool(
    records: list[tuple[str, str]],
    key: str,
    source: object,
    attr: str,
) -> None:
    value = getattr(source, attr, None)
    if value is not None:
        records.append((key, _format_bool_text(bool(value))))


def _append_optional_text(
    records: list[tuple[str, str]],
    key: str,
    source: object,
    attr: str,
) -> None:
    value = str(getattr(source, attr, "") or "")
    if value:
        records.append((key, value))


def _append_trace_calc_mode(
    records: list[tuple[str, str]],
    key: str,
    line: object,
) -> None:
    raw = getattr(line, "calc_mode_raw", None)
    if isinstance(raw, int):
        records.append((key, str(raw)))
        return
    value = str(getattr(line, "calc_mode", "") or "")
    if value:
        records.append((key, value))


def _normalized_guid_key(value: object) -> str:
    return str(value or "").strip().strip("{}").upper()


def _type_id_for_layer(layer: object) -> str:
    stackupx_type_id = _normalized_guid_key(getattr(layer, "stackupx_type_id", ""))
    if stackupx_type_id:
        return "{" + stackupx_type_id + "}"
    family = str(getattr(layer, "family", "")).strip().lower()
    if getattr(layer, "is_stiffener", None) is True:
        return _TYPE_ID_CORE
    if getattr(layer, "is_adhesive", None) is True:
        return _TYPE_ID_PREPREG
    if family in {"plating", "misc", "marking"}:
        return _TYPE_ID_PREPREG
    if family == "surface_finish":
        return _TYPE_ID_SURFACE_FINISH
    if family == "copper":
        legacy_layer_id = getattr(layer, "legacy_layer_id", None)
        if (
            isinstance(legacy_layer_id, int)
            and PcbLayer.INTERNAL_PLANE_1.value
            <= legacy_layer_id
            <= PcbLayer.INTERNAL_PLANE_16.value
        ):
            return _TYPE_ID_COPPER_PLANE
        return _TYPE_ID_COPPER_SIGNAL
    if family == "dielectric":
        dielectric_type = getattr(layer, "dielectric_type", None)
        if dielectric_type == 2:
            return _TYPE_ID_PREPREG
        if dielectric_type == 0:
            return _TYPE_ID_CORE
        return _TYPE_ID_DIELECTRIC
    if family == "overlay":
        return _TYPE_ID_OVERLAY
    if family == "solder_mask":
        return _TYPE_ID_SOLDER_MASK
    raise ValueError(f"Unsupported layer family for .stackup export: {family}")


def _v8_saved_layer_id_from_legacy(value: object) -> int | None:
    if not isinstance(value, int):
        return None
    if PcbLayer.TOP.value <= value < PcbLayer.BOTTOM.value:
        return 0x01000000 + value
    if value == PcbLayer.BOTTOM.value:
        return 0x0100FFFF
    if PcbLayer.INTERNAL_PLANE_1.value <= value <= PcbLayer.INTERNAL_PLANE_16.value:
        return 0x01010001 + (value - PcbLayer.INTERNAL_PLANE_1.value)
    return None


def _family_count(layers: tuple[object, ...], family: str) -> int:
    return sum(
        1
        for layer in layers
        if str(getattr(layer, "family", "")).strip().lower() == family
    )


def _synthetic_copper_v7_layer_id(index: int, copper_count: int) -> int:
    if index == 0:
        return 0x01000001
    if index == copper_count - 1:
        return 0x0100FFFF
    return 0x01000001 + index


def _synthetic_mask_overlay_layer_id(layer: object) -> int:
    name = str(getattr(layer, "display_name", "") or "").upper()
    family = str(getattr(layer, "family", "")).strip().lower()
    if family == "overlay" and "BOTTOM" in name:
        return 0x01030007
    if family == "overlay":
        return 0x01030006
    if family == "solder_mask" and "BOTTOM" in name:
        return 0x0103000B
    if family == "solder_mask":
        return 0x0103000A
    raise ValueError(f"Cannot synthesize V7 layer id for layer {name!r}")


def _stack_guid(*parts: object) -> str:
    seed = "|".join(str(part or "") for part in parts)
    return "{" + str(uuid.uuid5(_EXPORT_NAMESPACE, seed)).upper() + "}"


def _existing_or_stack_guid(*parts: object) -> str:
    for part in parts:
        token = str(part or "").strip()
        if not token:
            continue
        try:
            return "{" + str(uuid.UUID(token.strip("{}"))).upper() + "}"
        except ValueError:
            continue
    return _stack_guid(*parts)
