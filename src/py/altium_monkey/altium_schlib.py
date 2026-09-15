"""
Parse `.SchLib` schematic library files into an object model.
"""

from __future__ import annotations

import logging
import base64
import json
import math
import os
import re
import tempfile
import zlib
from contextvars import ContextVar
from copy import deepcopy
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from ._safe_artifact_name import (
    dedupe_artifact_basename,
    safe_schlib_output_name,
)
from . import (
    AltiumSchDesignator,
    AltiumSchHarnessConnector,
    AltiumSchHarnessEntry,
    AltiumSchImplementation,
    AltiumSchImplementationList,
    AltiumSchImplParams,
    AltiumSchLabel,
    AltiumSchMapDefiner,
    AltiumSchMapDefinerList,
    AltiumSchParameter,
    AltiumSchPin,
    AltiumSchSheetEntry,
    AltiumSchSheetSymbol,
    AltiumSchTextFrame,
)
from .altium_api_markers import public_api
from .altium_extractable_assets import (
    AltiumAssetInventory,
    AltiumAssetRef,
    AltiumAssetSummary,
    AltiumExtractedAsset,
    SchSymbolAssetDetails,
    semantic_asset_key,
    selected_asset_index,
    source_instance_id_for,
)
from .altium_font_manager import FontIDManager
from .altium_sch_auxiliary_codec import (
    SchAuxiliaryReadLimits,
    SchAuxiliaryStreamError,
    _decode_managed_auxiliary_stream,
    _first_managed_storage_entries,
)
from .altium_json_apply_helpers import (
    JsonBlobBudget,
    JsonApplyMixin,
    _json_casefold_value,
    json_object_rows,
    json_record_from_object,
    load_bounded_json_source,
)
from .altium_object_collection import ObjectCollection, ObjectCollectionView
from .altium_ole import AltiumOleFile, AltiumOleWriter
from .altium_record_types import (
    CoordPoint,
    LineWidth,
    SchPrimitive,
    SchRecordType,
    TextOrientation,
    _ManagedUniqueIdOwner,
    _generate_available_unique_id,
    generate_unique_id as generate_sch_unique_id,
    parse_bool,
)
from .altium_sch_binding import SchematicBindingContext
from .altium_text_codec import decode_altium_ansi, encode_altium_ansi_lossy
from .altium_sch_implementation_helpers import (
    build_footprint_implementation_payload,
    clean_implementation_child_record_fields,
    clean_implementation_record_fields,
)
from .altium_sch_display_mode import record_belongs_to_display_mode
from .altium_sch_json_object_types import (
    SchJsonObjectType,
    sch_json_object_type_from_record,
)
from .altium_sch_image_payload import (
    SchEmbeddedImageEntry,
    SchEmbeddedImagePayloadError,
    build_embedded_image_storage,
    decode_sch_embedded_image_payload,
    resolve_embedded_image_group,
)
from .altium_sch_pin_vertical_margin_data import (
    _apply_pin_vertical_margins,
    _decode_pin_vertical_margin_stream,
    _encode_pin_vertical_margin_stream,
)
from .altium_sch_record_factory import (
    create_record_from_record,
    create_record_from_type,
)
from .altium_schlib_container import (
    SchLibContainerError,
    _SchLibBudget,
    _SchLibReadLimits,
    _header_symbol_order,
    _parse_file_header,
    _parse_i32,
    _parse_instruction_stream,
    _parse_section_keys,
    _snapshot_container,
)
from .altium_serializer import (
    AltiumSerializer,
    Fields,
    read_dynamic_string_field,
    sanitize_stream_name,
)
from ._sch_managed_defaults import SHEET_AREA_COLOR
from .altium_schdoc_symbol_extractor import _sanitize_filename
from .altium_schlib_aux_streams import (
    build_pinfrac_stream_for_pins,
)
from .altium_utilities import (
    as_dynamic as _as_dynamic,
    create_storage_stream,
    create_stream_from_records,
    get_records_in_section,
)

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._sch_source_admission import _SourceAdmission
    from .altium_record_sch__parameter import AltiumSchImageParameter
    from .altium_record_sch__parameter_set import AltiumSchParameterSet
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryDocument,
        SchGeometryRecord,
    )
    from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions

_SchLibWeightPolicy = Literal["authored", "serialized_data_records"]
_SchObjectT = TypeVar("_SchObjectT")
_SCHLIB_DESIGNATOR_OWNER_TYPES = frozenset(
    {
        SchRecordType.COMPONENT,
        SchRecordType.HARNESS_COMPONENT,
        SchRecordType.HARNESS_SPLICE,
        SchRecordType.HARNESS_LAYOUT_LABEL,
        SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
        SchRecordType.HARNESS_BUNDLE,
        SchRecordType.HARNESS_LAYOUT_COVERING,
        SchRecordType.HARNESS_CAVITY_COMPONENT,
    }
)
_SCHLIB_REQUIRED_OWNER_TYPES = {
    SchRecordType.SHEET_NAME: frozenset(
        {SchRecordType.SHEET_SYMBOL, SchRecordType.HIGH_LEVEL_CODE_SYMBOL}
    ),
    SchRecordType.FILE_NAME: frozenset(
        {SchRecordType.SHEET_SYMBOL, SchRecordType.HIGH_LEVEL_CODE_SYMBOL}
    ),
    SchRecordType.MAP_DEFINER_LIST: frozenset({SchRecordType.IMPLEMENTATION}),
    SchRecordType.MAP_DEFINER: frozenset({SchRecordType.MAP_DEFINER_LIST}),
    SchRecordType.HARNESS_TYPE: frozenset({SchRecordType.HARNESS_CONNECTOR}),
}
_SCHLIB_LAST_WINNER_RECORD_TYPES = frozenset(
    {
        SchRecordType.DESIGNATOR,
        SchRecordType.SHEET_NAME,
        SchRecordType.FILE_NAME,
        SchRecordType.HARNESS_TYPE,
        SchRecordType.MAP_DEFINER_LIST,
    }
)
_SCHLIB_SKIPPED_ADDITIONAL_RECORD_IDS = frozenset({230, 231})
_SCHLIB_MANAGED_ADDITIONAL_RECORD_IDS = frozenset(
    {
        129,  # Object definition
        138,  # Reuse-block implementation info
        215,  # Harness connector
        216,  # Harness entry
        217,  # Harness connector type
        218,  # Signal harness
        220,  # High-level code symbol
        221,  # High-level code entry
        224,  # Open-bus pin group
        225,  # Blanket
    }
)
_SCHLIB_JSON_TEXT_FIELDS = frozenset(
    field.upper
    for field in (
        Fields.ARROW_KIND,
        Fields.ASSIGNED_INTERFACE,
        Fields.ASSIGNED_INTERFACE_SIGNAL,
        Fields.AUTHOR,
        Fields.COMPONENT_DESCRIPTION,
        Fields.CROSS_REFERENCE,
        Fields.DATABASE_TABLE_NAME,
        Fields.DESCRIPTION,
        Fields.DESIGN_ITEM_ID,
        Fields.DOCUMENT_NAME,
        Fields.FILENAME,
        Fields.FOOTPRINT,
        Fields.HARNESS_TYPE,
        Fields.ITEM_GUID,
        Fields.LIBRARY_PATH,
        Fields.LIB_REFERENCE,
        Fields.MODEL_NAME,
        Fields.MODEL_TYPE,
        Fields.NAME,
        Fields.OBJECT_DEFINITION_ID,
        Fields.REVISION_GUID,
        Fields.REVISION_NAME,
        Fields.SHEET_PART_FILENAME,
        Fields.SOURCE_LIBRARY_NAME,
        Fields.SYMBOL_ITEM_GUID,
        Fields.SYMBOL_REVISION_GUID,
        Fields.SYMBOL_TYPE,
        Fields.SYMBOL_VAULT_GUID,
        Fields.SYMBOL,
        Fields.TARGET_FILENAME,
        Fields.TEXT,
        Fields.TEXT_STYLE,
        Fields.URL,
        Fields.VAULT_GUID,
    )
) | frozenset(
    {
        "ALIASLIST",
        "CONNECTIONPAIRSTOSUPPRESS",
        "DEFAULTVALUE",
        "DESIGNATOR",
        "DESINTF",
        "ERRORKINDSETTOSUPPRESS",
        "FILEVERSIONINFO",
        "HEADER",
        "MODELFILEID",
        "MODELLOCATION",
        "OBJECTDEFINITIONHASH",
        "SWAPIDPART",
    }
)
_SCHLIB_JSON_INDEXED_TEXT_PREFIXES = (
    "COVEREDITEMFIRSTPIN",
    "COVEREDITEMLASTPIN",
    "COVEREDITEMID",
    "MODELDATAFILEENTITY",
    "MODELDATAFILEKIND",
    "MODELDATAFILE",
    "DESIMP",
)


class _ManagedComponentRoot:
    record_type = SchRecordType.COMPONENT


_MANAGED_COMPONENT_ROOT = _ManagedComponentRoot()
_SCHLIB_BLANKET_RENDER_BUDGET: ContextVar[object | None] = ContextVar(
    "altium_monkey_schlib_blanket_render_budget", default=None
)
_SCHLIB_PARAMETER_SET_RENDER_BUDGET: ContextVar[object | None] = ContextVar(
    "altium_monkey_schlib_parameter_set_render_budget", default=None
)


def _publish_schlib_svg_output(
    destination: Path | None, pending_output: Collection[tuple[Path, str]]
) -> None:
    if not pending_output:
        return
    assert destination is not None
    destination.mkdir(parents=True, exist_ok=True)
    for output_path, svg in pending_output:
        output_path.write_text(svg, encoding="utf-8")


def _queue_schlib_svg_output(
    pending_output: list[tuple[Path, str]],
    destination: Path | None,
    symbol_name: str,
    part_id: int,
    part_count: int,
    svg: str,
) -> None:
    if destination is None:
        return
    filename = (
        f"{symbol_name}_part{part_id}_ir.svg"
        if part_count > 1
        else f"{symbol_name}_ir.svg"
    )
    pending_output.append((destination / filename, svg))


def _parse_pinfunctiondata_from_ole(
    ole: Any, symbol_storage: str
) -> dict[str, Any] | None:
    """
    Parse PinFunctionData from OLE stream for a symbol.

    PinFunctionData contains alternate function definitions for multi-functional
    pins (e.g., GPIO/UART/SPI modes).

    Note: migrated from the older altium_pin_parser.py implementation.
    PinFunctionData is only used during SchLib parsing, so it belongs here.

    Args:
        ole: AltiumOleFile object
        symbol_storage: Symbol storage name (truncated to 31 chars if longer!)

    Returns:
        Dict with 'functions' key containing list of alternate function names,
        or None if no alternate functions defined
    """
    stream_path = f"{symbol_storage}/PinFunctionData"

    # Check if PinFunctionData exists
    if not ole.exists(stream_path):
        return None

    try:
        # Read OLE stream
        records = get_records_in_section(ole, stream_path)

        # Find binary record
        binary_data = None
        for rec in records:
            if rec.get("__BINARY_RECORD__"):
                binary_data = rec["__BINARY_DATA__"]
                break

        if binary_data is None:
            return None

        # Find zlib compressed data (starts with 0x78 0x9C)
        compressed_offset = None
        for i in range(len(binary_data) - 1):
            if binary_data[i : i + 2] == b"\x78\x9c":
                compressed_offset = i
                break

        if compressed_offset is None:
            return None

        # Decompress
        decompressed = zlib.decompress(binary_data[compressed_offset:])

        # Decode as UTF-16 LE (skip first 2 bytes - likely BOM or padding)
        text = decompressed[2:].decode("utf-16-le", errors="replace")

        # Parse pipe-delimited format
        # Format: |PINDEFINEDFUNCTIONSCOUNT=N|PINDEFINEDFUNCTION1=name|PINDEFINEDFUNCTION2=name|...
        parts = text.split("|")
        functions = []
        count = 0

        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                if key == "PINDEFINEDFUNCTIONSCOUNT":
                    count = int(value)
                elif key.startswith("PINDEFINEDFUNCTION"):
                    functions.append(value)

        return {"count": count, "functions": functions}

    except Exception as e:
        log.debug(f"Error parsing PinFunctionData: {e}")
        return None


def _create_record_object(record: dict[str, Any]) -> Any | None:
    """
    Factory function to create appropriate record object from raw record.

    Args:
        record: Raw record dictionary

    Returns:
        Parsed record object or None if type not supported
    """
    if "RECORD" not in record:
        return None

    try:
        SchRecordType(int(record["RECORD"]))
    except (ValueError, KeyError):
        return None

    # Create record object using raw-record-aware factory
    record_obj = create_record_from_record(record)

    if record_obj:
        # Parse the record data into the object
        record_obj.parse_from_record(record)

    return record_obj


def _cp1252_fallback(value: str) -> str:
    """
    Return a Windows-1252-safe fallback for an Altium text-record value.
    """
    return decode_altium_ansi(encode_altium_ansi_lossy(value))


def _needs_utf8_field(value: str) -> bool:
    """
    Return True when the existing companion policy requires authoritative UTF-8.

    This intentionally uses Python's strict cp1252 repertoire. The managed
    writer can preserve the five C1 compatibility values in the ordinary field,
    but current Monkey behavior still emits their UTF-8 companions.
    """
    try:
        value.encode("cp1252")
    except UnicodeEncodeError:
        return True
    return False


def _set_header_text_field(
    header: dict[str, str],
    key: str,
    value: str,
    *,
    fallback: str | None = None,
) -> None:
    """
    Set an Altium FileHeader field, adding a %UTF8% companion when required.
    """
    if _needs_utf8_field(value):
        header[f"%UTF8%{key}"] = value
        header[key] = _cp1252_fallback(fallback or value)
        return
    header[key] = value


def _replace_dynamic_text_field(
    record: dict[str, object], key: str, value: str
) -> None:
    """Replace one case-insensitive text field and its UTF-8 sidecar."""
    managed_keys = {key.casefold(), f"%UTF8%{key}".casefold()}
    for existing in tuple(record):
        if existing.casefold() in managed_keys:
            record.pop(existing)
    _set_header_text_field(cast(dict[str, str], record), key, value)


def _header_symbol_fallback_name(symbol: "AltiumSymbol") -> str:
    """
    Return the cp1252 FileHeader LibRef fallback for a symbol.
    """
    component_record = symbol.component_record or {}
    return str(
        component_record.get("LibReference")
        or component_record.get("LIBREFERENCE")
        or component_record.get("DesignItemId")
        or component_record.get("DESIGNITEMID")
        or symbol.original_name
        or symbol.name
    )


def _symbol_uses_implicit_storage_mapping(symbol: "AltiumSymbol") -> bool:
    if not symbol._uses_implicit_storage_mapping:
        return False
    candidates = (
        str(symbol._header_display_name or ""),
        str(symbol.original_name or symbol.name),
    )
    return any(
        sanitize_stream_name(candidate).casefold() == symbol.name.casefold()
        for candidate in candidates
        if candidate
    )


def _copy_symbol_storage_dialect(
    source: "AltiumSymbol", target: "AltiumSymbol"
) -> None:
    target._uses_implicit_storage_mapping = source._uses_implicit_storage_mapping
    target._header_display_name = source._header_display_name


def _schlib_json_text_field(field_name: str) -> bool:
    normalized = field_name.upper().removeprefix("%UTF8%")
    return (
        normalized in _SCHLIB_JSON_TEXT_FIELDS
        or "UNIQUEID" in normalized
        or normalized.endswith("GUID")
        or _schlib_json_indexed_text_field(normalized)
    )


def _schlib_json_indexed_text_field(field_name: str) -> bool:
    return any(
        field_name.startswith(prefix) and field_name.removeprefix(prefix).isdigit()
        for prefix in _SCHLIB_JSON_INDEXED_TEXT_PREFIXES
    )


def _schlib_json_scalar(value: object, *, field_name: str | None = None) -> object:
    if not isinstance(value, str):
        return value
    if field_name is not None and _schlib_json_text_field(field_name):
        return value
    if value == "T":
        return True
    if value == "F":
        return False
    return _schlib_json_number_or_text(value)


def _schlib_json_number_or_text(value: str) -> object:
    digits = value.removeprefix("-")
    if digits.isdigit():
        parsed = int(value)
        if not -(2**63) <= parsed < 2**63:
            raise ValueError(f"integer {value!r} exceeds signed i64")
        return parsed
    try:
        parsed_float = float(value)
    except ValueError:
        return value
    if not math.isfinite(parsed_float):
        raise ValueError(f"non-finite float {value!r}")
    return parsed_float


def _schlib_binary_json_object(
    record: dict[str, object],
    object_index: int,
    normalized_owner_index: int | None,
) -> dict[str, object] | None:
    binary_value = record.get("__BINARY_DATA__", b"")
    if not isinstance(binary_value, bytes | bytearray) or not binary_value:
        return None
    binary_data = bytes(binary_value)
    record_type = binary_data[0]
    if normalized_owner_index is not None:
        binary_data = (
            binary_data[:1]
            + normalized_owner_index.to_bytes(4, "little", signed=True)
            + binary_data[5:]
        )
    object_type = sch_json_object_type_from_record(
        {"__BINARY_RECORD__": True, "__BINARY_DATA__": binary_data}
    )
    return {
        "ObjectType": (
            object_type.value if object_type is not None else f"Unknown_{record_type}"
        ),
        "ObjectIndex": object_index,
        "BinaryData": base64.b64encode(zlib.compress(binary_data)).decode("ascii"),
        "_binary_record_type": record_type,
    }


def _schlib_text_json_object(
    record: dict[str, object], object_index: int
) -> dict[str, object] | None:
    record_num = record.get("RECORD")
    if record_num is None or not isinstance(record_num, int | float | str):
        return None
    object_type = sch_json_object_type_from_record(record)
    try:
        record_type_int = int(record_num)
    except (TypeError, ValueError):
        return None
    type_name = (
        object_type.value if object_type is not None else f"Unknown_{record_type_int}"
    )
    result: dict[str, object] = {
        "ObjectType": type_name,
        "ObjectIndex": object_index,
    }
    result.update(
        (key, _schlib_json_scalar(value, field_name=key))
        for key, value in record.items()
        if key != "RECORD"
    )
    return result


def _schlib_record_json_object(
    record: dict[str, object],
    object_index: int,
    normalized_owner_index: int | None,
) -> dict[str, object] | None:
    if record.get("__BINARY_RECORD__"):
        return _schlib_binary_json_object(record, object_index, normalized_owner_index)
    return _schlib_text_json_object(record, object_index)


def _schlib_json_font(font_id: int, info: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {
        "FontID": font_id,
        "FontName": info["name"],
        "FontSize": info["size"],
    }
    for source, target in (
        ("bold", "Bold"),
        ("italic", "Italic"),
        ("underline", "Underline"),
        ("strikeout", "Strikeout"),
        ("rotation", "Rotation"),
    ):
        if info.get(source):
            result[target] = info[source]
    return result


def _schlib_json_symbol(symbol: AltiumSymbol) -> dict[str, object]:
    objects: list[dict[str, object]] = []
    for index, record in enumerate(symbol.raw_records):
        normalized_owner = (
            symbol._normalized_owner_indices[index] if index > 0 else None
        )
        item = _schlib_record_json_object(record, index, normalized_owner)
        if item is None:
            continue
        for key in [key for key in item if key.casefold() == "ownerindex"]:
            item.pop(key)
        if index > 0 and "BinaryData" not in item:
            item["OwnerIndex"] = symbol._normalized_owner_indices[index]
        objects.append(item)
    return {
        "Name": symbol.name,
        "Description": symbol.description,
        "PartCount": symbol.part_count,
        "Objects": objects,
    }


class AltiumSymbol(_ManagedUniqueIdOwner):
    """
    Represents a single symbol in a SchLib.

        New code should create symbols through ``AltiumSchLib.add_symbol(...)``
        instead of constructing ``AltiumSymbol(...)`` directly. Direct
        construction is a low-level parser/authoring detail and should generally
        be reserved for ``altium_monkey`` internals and tightly scoped
        conversion code.

        Objects are stored in a single authoritative collection. ``.objects``
        and typed convenience properties (``.pins``,
        ``.graphic_primitives``, ``.rectangles``, etc.) return live read-only
        views. Use the symbol's explicit add/remove APIs to change membership.
    """

    # Graphics types used by the .graphic_primitives aggregate view
    _GRAPHICS_TYPES: tuple[type, ...] | None = None

    def __init__(self, name: str, *, original_name: str | None = None) -> None:
        self.name = name
        self.original_name = original_name or name
        self.unique_id: str | None = generate_sch_unique_id()
        self._is_unique_id_locked = False
        self.component_record = None
        self._objects = ObjectCollection()
        self.raw_records: list[dict[str, object]] = []
        self._additional_raw_records: list[dict[str, object]] = []
        self._additional_terminal_record: dict[str, object] = {"RECORD": "0"}
        self._pin_parameters: dict[int, list[AltiumSchParameter]] = {}
        self._schematic_binding_context: SchematicBindingContext | None = None

        # Component metadata
        self.description = ""
        self.part_count = 1
        self.display_mode = 0
        self.display_mode_count = 1

        # Original auxiliary streams for round-trip (PinTextData, PinFrac, etc.)
        self._original_streams: dict[str, bytes] = {}
        self._normalized_owner_indices: tuple[int, ...] = ()
        self._data_membership_dirty = False
        self._data_record_index_remap: dict[int, int] = {}
        self._additional_membership_dirty = False
        self._uses_implicit_storage_mapping = False
        self._header_display_name: str | None = None

    def _bind_to_schematic_library(self, schlib: "AltiumSchLib") -> None:
        """
        Bind this symbol and its records to a schematic library context.
        """
        self._schematic_binding_context = schlib._binding_context()
        self._bind_all_objects_to_context()

    def _bind_object_to_context(self, obj: Any) -> None:
        bind_hook = getattr(obj, "_bind_to_schematic_context", None)
        if callable(bind_hook) and self._schematic_binding_context is not None:
            bind_hook(self._schematic_binding_context)

    def _bind_all_objects_to_context(self) -> None:
        for obj in self._objects:
            self._bind_object_to_context(obj)

    def _identity_owners(self) -> Iterator[object]:
        context = self._schematic_binding_context
        owner = context.owner if context is not None else None
        symbols = owner.symbols if isinstance(owner, AltiumSchLib) else [self]
        for symbol in symbols:
            yield from symbol._owned_identity_records()

    def _owned_identity_records(self) -> Iterator[object]:
        """Yield each identity-bearing record owned by this symbol once."""
        seen: set[int] = set()
        records = [self, *self._objects]
        for parameters in self._pin_parameters.values():
            records.extend(parameters)
        for record in records:
            identity = id(record)
            if identity not in seen:
                seen.add(identity)
                yield record

    def _unique_id_is_in_use(self, unique_id: str, obj: object) -> bool:
        return any(
            owner is not obj and getattr(owner, "unique_id", "") == unique_id
            for owner in self._identity_owners()
        )

    def _fix_object_unique_id_if_required(self, obj: object) -> None:
        if not isinstance(obj, SchPrimitive):
            return
        unique_id = obj.unique_id or ""
        if unique_id in {"", "$$$"} or self._unique_id_is_in_use(unique_id, obj):
            obj._update_unique_id(
                _generate_available_unique_id(
                    generate_sch_unique_id,
                    lambda candidate: self._unique_id_is_in_use(candidate, self),
                )
            )

    def _fix_symbol_unique_id_if_required(self) -> None:
        unique_id = self.unique_id or ""
        if unique_id in {"", "$$$"} or self._unique_id_is_in_use(unique_id, self):
            self._update_unique_id(
                _generate_available_unique_id(
                    generate_sch_unique_id,
                    lambda candidate: self._unique_id_is_in_use(candidate, self),
                )
            )

    def _append_object(
        self, obj: _SchObjectT, *, preserve_raw_identity: bool
    ) -> _SchObjectT:
        self._validate_object_attachment(obj)
        if not preserve_raw_identity:
            self._clear_persisted_record_location(obj)
            _as_dynamic(obj)._managed_container_membership = "object_list"
            parent = getattr(obj, "parent", None)
            if parent is not None and not self._is_live_container_parent(parent):
                _as_dynamic(obj).parent = None
            if self._routes_new_object_to_additional(obj):
                _as_dynamic(obj)._source_stream = "Additional"
        self._bind_object_to_context(obj)
        if not preserve_raw_identity:
            self._fix_object_unique_id_if_required(obj)
        self._register_embedded_image_object(obj)
        self._objects.append(obj)
        if isinstance(obj, SchPrimitive):
            obj._set_unique_id_locked(True)
        if not preserve_raw_identity:
            if getattr(obj, "_source_stream", "Data") == "Additional":
                self._additional_membership_dirty = True
            elif self.raw_records:
                self._data_membership_dirty = True
        return obj

    def _is_live_container_parent(self, parent: object) -> bool:
        from ._sch_source_projection import _record_import_ignores_source

        return (
            any(candidate is parent for candidate in self._objects)
            and not _record_import_ignores_source(parent)
            and not bool(getattr(parent, "_managed_unattached", False))
        )

    @staticmethod
    def _clear_persisted_record_location(obj: object) -> None:
        """Detach copied source-row placement before attaching a new member."""
        for attribute in (
            "_record_index",
            "_source_stream",
            "_additional_record_index",
            "_additional_stream_record_index",
            "_managed_selected_owner",
            "_managed_selected_owner_type",
            "_managed_selected_owner_exists",
            "_managed_unattached",
        ):
            if hasattr(obj, attribute):
                delattr(obj, attribute)
        if hasattr(obj, "owner_index"):
            _as_dynamic(obj).owner_index = 0
        if hasattr(obj, "index_in_sheet"):
            _as_dynamic(obj).index_in_sheet = -1
        if hasattr(obj, "owner_index_additional_list"):
            _as_dynamic(obj).owner_index_additional_list = False

    def _routes_new_object_to_additional(self, obj: object) -> bool:
        record_type = getattr(obj, "record_type", 0)
        record_id = int(getattr(record_type, "value", record_type))
        if record_id in _SCHLIB_MANAGED_ADDITIONAL_RECORD_IDS:
            return True
        parent = getattr(obj, "parent", None)
        return parent is not None and any(
            candidate is parent
            and getattr(candidate, "_source_stream", "Data") == "Additional"
            for candidate in self._objects
        )

    def _validate_object_attachment(self, obj: object) -> None:
        """Validate exclusive symbol membership before changing object state."""
        if any(candidate is obj for candidate in self._objects):
            raise ValueError("object is already attached to this schematic symbol")

        symbol_context = self._schematic_binding_context
        symbol_library = symbol_context.owner if symbol_context is not None else None
        object_context = getattr(obj, "_bound_schematic_context", None)
        object_library = getattr(object_context, "owner", None)
        if object_library is not None and object_library is not symbol_library:
            raise ValueError(
                "object is already attached to another schematic container"
            )

        if not isinstance(symbol_library, AltiumSchLib):
            return
        if self._object_is_attached_to_another_symbol(obj, symbol_library):
            raise ValueError("object is already attached to another schematic symbol")

    def _object_is_attached_to_another_symbol(
        self,
        obj: object,
        library: "AltiumSchLib",
    ) -> bool:
        return any(
            symbol is not self
            and any(candidate is obj for candidate in symbol._objects)
            for symbol in library.symbols
        )

    def _append_parsed_object(self, obj: _SchObjectT) -> _SchObjectT:
        return self._append_object(obj, preserve_raw_identity=True)

    def add_object(self, obj: Any) -> Any:
        """
        Add an object to this symbol and bind document-scoped context if present.
        """
        return self._append_object(obj, preserve_raw_identity=False)

    def remove_object(self, obj: object) -> bool:
        """Remove an object subtree and release its managed identity locks."""
        if obj not in self._objects:
            return False
        removed = self._removal_subtree(obj)
        removed_ids = {id(source) for source in removed}
        self._remove_additional_subtree_rows(removed)
        retained = [source for source in self._objects if id(source) not in removed_ids]
        for source in removed:
            self._detach_removed_object(source)
        self._objects.clear()
        self._objects.extend(retained)
        removed_data_indices = self._removed_data_indices(removed)
        self._retain_pin_parameters(removed_ids)
        if self.raw_records and removed_data_indices:
            self._data_membership_dirty = True
        return True

    def _removal_subtree(self, root: object) -> list[object]:
        """Project the managed container removal cascade onto the flat live view."""
        objects, parameter_owners = self._live_data_objects()
        indexed = self._indexed_data_objects(objects)
        owners = self._data_owner_objects(objects, indexed, parameter_owners)
        children: dict[int, list[object]] = {}
        for candidate in objects:
            parent = owners[id(candidate)]
            if parent is not None:
                children.setdefault(id(parent), []).append(candidate)
        for candidate in self._objects:
            if getattr(candidate, "_source_stream", "Data") != "Additional":
                continue
            parent = getattr(candidate, "parent", None)
            if parent is not None:
                children.setdefault(id(parent), []).append(candidate)
        removed: list[object] = []
        removed_ids: set[int] = set()
        pending = [root]
        while pending:
            current = pending.pop()
            current_id = id(current)
            if current_id in removed_ids:
                continue
            removed_ids.add(current_id)
            removed.append(current)
            pending.extend(reversed(children.get(current_id, ())))
        return removed

    @staticmethod
    def _detach_removed_object(source: object) -> None:
        if isinstance(source, SchPrimitive):
            source._set_unique_id_locked(False)
        if hasattr(source, "_bound_schematic_context"):
            _as_dynamic(source)._bound_schematic_context = None

    @staticmethod
    def _removed_data_indices(removed: Collection[object]) -> set[int]:
        return {
            index
            for source in removed
            if getattr(source, "_source_stream", "Data") != "Additional"
            and isinstance((index := getattr(source, "_record_index", None)), int)
        }

    def _retain_pin_parameters(self, removed_ids: set[int]) -> None:
        self._pin_parameters = {
            owner: [
                parameter
                for parameter in parameters
                if id(parameter) not in removed_ids
            ]
            for owner, parameters in self._pin_parameters.items()
            if any(id(parameter) not in removed_ids for parameter in parameters)
        }

    def _remove_additional_subtree_rows(self, removed: Collection[object]) -> None:
        removed_additional = self._additional_removal_seed(removed)
        children = self._additional_child_rows()
        pending = list(removed_additional)
        while pending:
            for child in children.get(pending.pop(), ()):
                if child not in removed_additional:
                    removed_additional.add(child)
                    pending.append(child)
        if removed_additional:
            self._retain_additional_rows(removed_additional)

    def _additional_removal_seed(self, removed: Collection[object]) -> set[int]:
        from .altium_serializer import _read_param_boolean

        removed_ids = {id(source) for source in removed}
        removed_data = self._removed_data_indices(removed)
        removed = {
            index
            for source in self._objects
            if id(source) in removed_ids
            and getattr(source, "_source_stream", "Data") == "Additional"
            and isinstance(
                (index := getattr(source, "_additional_stream_record_index", None)),
                int,
            )
        }
        for index, record in enumerate(self._additional_raw_records):
            owner = self._record_owner_index(record)
            in_additional = _read_param_boolean(record, "OwnerIndexAdditionalList")
            if not in_additional and owner in removed_data:
                removed.add(index)
        return removed

    def _additional_child_rows(self) -> dict[int, list[int]]:
        from .altium_serializer import _read_param_boolean

        children: dict[int, list[int]] = {}
        for index, record in enumerate(self._additional_raw_records):
            if _read_param_boolean(record, "OwnerIndexAdditionalList"):
                owner = self._record_owner_index(record)
                children.setdefault(owner, []).append(index)
        return children

    def _retain_additional_rows(self, removed: set[int]) -> None:
        from .altium_serializer import _read_param_boolean

        old_to_new: dict[int, int] = {}
        retained: list[dict[str, object]] = []
        for old_index, record in enumerate(self._additional_raw_records):
            if old_index not in removed:
                old_to_new[old_index] = len(retained)
                retained.append(record)
        for record in retained:
            if _read_param_boolean(record, "OwnerIndexAdditionalList"):
                owner = self._record_owner_index(record)
                self._set_record_owner_index(record, old_to_new.get(owner, 0))
        self._additional_raw_records = retained
        self._reindex_additional_objects(removed, old_to_new)
        self._additional_membership_dirty = True

    def _reindex_additional_objects(
        self, removed: set[int], old_to_new: Mapping[int, int]
    ) -> None:
        retained_objects = []
        retained_additional = []
        for source in self._objects:
            old_index = getattr(source, "_additional_stream_record_index", None)
            if not isinstance(old_index, int):
                retained_objects.append(source)
                continue
            if old_index in removed:
                self._detach_removed_object(source)
                continue
            new_index = old_to_new[old_index]
            _as_dynamic(source)._additional_stream_record_index = new_index
            if hasattr(source, "owner_index"):
                _as_dynamic(source).owner_index = self._record_owner_index(
                    self._additional_raw_records[new_index]
                )
            retained_objects.append(source)
            retained_additional.append(source)
        retained_additional.sort(
            key=lambda source: getattr(source, "_additional_stream_record_index")
        )
        for slot, source in enumerate(retained_additional):
            _as_dynamic(source)._additional_record_index = slot
            _as_dynamic(source)._record_index = len(self.raw_records) + slot
        self._objects.clear()
        self._objects.extend(retained_objects)

    @staticmethod
    def _record_owner_index(record: Mapping[str, object]) -> int:
        value = next(
            (value for key, value in record.items() if key.casefold() == "ownerindex"),
            0,
        )
        return int(str(value))

    @staticmethod
    def _set_record_owner_index(record: dict[str, object], owner_index: int) -> None:
        keys = [key for key in record if key.casefold() == "ownerindex"]
        if not keys and owner_index != 0:
            record["OwnerIndex"] = str(owner_index)
            return
        for key in keys:
            record[key] = str(owner_index)

    def _copy_objects_from(self, source: "AltiumSymbol") -> None:
        clones = deepcopy(list(source.objects))
        for obj in clones:
            if hasattr(obj, "_bound_schematic_context"):
                _as_dynamic(obj)._bound_schematic_context = None
            self._append_parsed_object(obj)

    def _register_embedded_image_object(self, obj: Any) -> None:
        """
        Mirror image payloads into the owning SchLib storage table when possible.
        """
        from .altium_record_sch__image import AltiumSchImage

        if not isinstance(obj, AltiumSchImage):
            return
        image_data = obj.image_data
        if not obj.embed_image or not obj.filename or not image_data:
            return
        owner = (
            self._schematic_binding_context.owner
            if self._schematic_binding_context is not None
            else None
        )
        if not isinstance(owner, AltiumSchLib):
            return
        entries = (
            SchEmbeddedImageEntry(
                filename=image.filename,
                orientation=int(image.orientation),
                data=image.image_data,
            )
            for symbol in owner.symbols
            for image in symbol.images
            if image.embed_image and image.filename and image.image_data
        )
        candidate = SchEmbeddedImageEntry(
            filename=obj.filename,
            orientation=int(obj.orientation),
            data=image_data,
        )
        synced_images = build_embedded_image_storage((*entries, candidate))
        owner.embedded_images = synced_images

    def set_description(self, description: str) -> AltiumSymbol:
        """
        Set the symbol/component description.
        """
        self.description = description
        return self

    def set_part_count(self, count: int) -> AltiumSymbol:
        """
        Set the logical schematic part count.

        Args:
            count: Number of parts. Must be >= 1.
        """
        if count < 1:
            raise ValueError("part count must be >= 1")
        self.part_count = count
        return self

    # -- Typed convenience properties --
    @property
    def objects(self) -> ObjectCollectionView:
        """Return a live read-only view of all symbol objects."""
        return ObjectCollectionView(self._objects, lambda _obj: True)

    # Each returns an ObjectCollection filtered from the authoritative store.

    @property
    def pins(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchPin)

    @property
    def labels(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchLabel)

    @property
    def images(self) -> ObjectCollection:
        from .altium_record_sch__image import AltiumSchImage

        return self._objects.of_type(AltiumSchImage)

    @property
    def parameters(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchParameter)

    @property
    def designators(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchDesignator)

    @property
    def text_frames(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchTextFrame)

    @property
    def implementation_lists(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchImplementationList)

    @property
    def implementations(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchImplementation)

    @property
    def map_definer_lists(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchMapDefinerList)

    @property
    def map_definers(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchMapDefiner)

    @property
    def impl_params(self) -> ObjectCollection:
        return self._objects.of_type(AltiumSchImplParams)

    @property
    def graphic_primitives(self) -> ObjectCollection:
        """
        All graphical primitive objects (lines, rectangles, arcs, etc.).

                This aggregate excludes pins, parameters, labels, images, designators,
                and text frames. Prefer specific typed properties like ``.rectangles``
                or ``.lines`` when a caller needs one known primitive class.
        """
        if AltiumSymbol._GRAPHICS_TYPES is None:
            from . import (
                AltiumSchArc,
                AltiumSchBezier,
                AltiumSchEllipse,
                AltiumSchEllipticalArc,
                AltiumSchIeeeSymbol,
                AltiumSchLine,
                AltiumSchPieChart,
                AltiumSchPolygon,
                AltiumSchPolyline,
                AltiumSchRectangle,
                AltiumSchRoundedRectangle,
            )

            AltiumSymbol._GRAPHICS_TYPES = (
                AltiumSchArc,
                AltiumSchBezier,
                AltiumSchEllipse,
                AltiumSchEllipticalArc,
                AltiumSchIeeeSymbol,
                AltiumSchLine,
                AltiumSchPieChart,
                AltiumSchPolygon,
                AltiumSchPolyline,
                AltiumSchRectangle,
                AltiumSchRoundedRectangle,
            )
        graphics_types = AltiumSymbol._GRAPHICS_TYPES
        if graphics_types is None:
            return ObjectCollectionView(self.objects, lambda _o: False)
        return ObjectCollectionView(
            self.objects,
            lambda o: isinstance(o, graphics_types),
        )

    # -- Specific shape properties --

    @property
    def rectangles(self) -> ObjectCollection:
        from .altium_record_sch__rectangle import AltiumSchRectangle

        return self._objects.of_type(AltiumSchRectangle)

    @property
    def lines(self) -> ObjectCollection:
        from .altium_record_sch__line import AltiumSchLine

        return self._objects.of_type(AltiumSchLine)

    @property
    def arcs(self) -> ObjectCollection:
        from .altium_record_sch__arc import AltiumSchArc

        return self._objects.of_type(AltiumSchArc)

    @property
    def polylines(self) -> ObjectCollection:
        from .altium_record_sch__polyline import AltiumSchPolyline

        return self._objects.of_type(AltiumSchPolyline)

    @property
    def polygons(self) -> ObjectCollection:
        from .altium_record_sch__polygon import AltiumSchPolygon

        return self._objects.of_type(AltiumSchPolygon)

    @property
    def beziers(self) -> ObjectCollection:
        from .altium_record_sch__bezier import AltiumSchBezier

        return self._objects.of_type(AltiumSchBezier)

    @property
    def ellipses(self) -> ObjectCollection:
        from .altium_record_sch__ellipse import AltiumSchEllipse

        return self._objects.of_type(AltiumSchEllipse)

    @property
    def pie_charts(self) -> ObjectCollection:
        from .altium_record_sch__piechart import AltiumSchPieChart

        return self._objects.of_type(AltiumSchPieChart)

    @property
    def ieee_symbols(self) -> ObjectCollection:
        from .altium_record_sch__ieee_symbol import AltiumSchIeeeSymbol

        return self._objects.of_type(AltiumSchIeeeSymbol)

    # -- Convenience add_* methods --

    def add_pin(self, pin: AltiumSchPin) -> AltiumSchPin:
        """
        Add a pin to this symbol.

                Args:
                    pin: AltiumSchPin object.

                Returns:
                    The added pin.
        """
        if not isinstance(pin, AltiumSchPin):
            raise TypeError("AltiumSchLib.add_pin() requires an AltiumSchPin instance")
        return self.add_object(pin)

    def add_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALL,
        is_solid: bool = True,
        transparent: bool = False,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a rectangle to this symbol.

                Args:
                    x1, y1: First corner in 10-mil units.
                    x2, y2: Opposite corner in 10-mil units.
                    color: Border color (Win32 0x00BBGGRR).
                    area_color: Fill color.
                    is_solid: Whether rectangle is filled.
                    owner_part_id: Part ID (-1 for all parts).

                Returns:
                    The added AltiumSchRectangle.
        """
        from .altium_record_sch__rectangle import AltiumSchRectangle

        rect = AltiumSchRectangle()
        rect.location = CoordPoint.from_mils(x1, y1)
        rect.corner = CoordPoint.from_mils(x2, y2)
        rect.color = color
        rect.area_color = area_color
        rect.line_width = line_width
        rect.is_solid = is_solid
        rect.transparent = transparent
        rect.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        rect.is_not_accessible = True
        return self.add_object(rect)

    def add_line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a line to this symbol.
        """
        from .altium_record_sch__line import AltiumSchLine

        line = AltiumSchLine()
        line.location = CoordPoint.from_mils(x1, y1)
        line.corner = CoordPoint.from_mils(x2, y2)
        line.color = color
        line.line_width = line_width
        line.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        line.is_not_accessible = True
        return self.add_object(line)

    def add_arc(
        self,
        x: int,
        y: int,
        radius: int,
        *,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an arc to this symbol.
        """
        from .altium_record_sch__arc import AltiumSchArc

        arc = AltiumSchArc()
        arc.location = CoordPoint.from_mils(x, y)
        arc.radius = int(radius // 10)
        arc.start_angle = start_angle
        arc.end_angle = end_angle
        arc.color = color
        arc.line_width = line_width
        arc.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        arc.is_not_accessible = True
        return self.add_object(arc)

    def add_elliptical_arc(
        self,
        x: int,
        y: int,
        radius: int,
        secondary_radius: int,
        *,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an elliptical arc to this symbol.
        """
        from .altium_record_sch__elliptical_arc import AltiumSchEllipticalArc

        earc = AltiumSchEllipticalArc()
        earc.location = CoordPoint.from_mils(x, y)
        earc.radius = int(radius // 10)
        earc.secondary_radius = int(secondary_radius // 10)
        earc.start_angle = start_angle
        earc.end_angle = end_angle
        earc.color = color
        earc.line_width = line_width
        earc.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        earc.is_not_accessible = True
        return self.add_object(earc)

    def add_polyline(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a polyline to this symbol.
        """
        from .altium_record_sch__polyline import AltiumSchPolyline

        poly = AltiumSchPolyline()
        poly.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        poly.location = poly.vertices[0] if poly.vertices else CoordPoint()
        poly.color = color
        poly.line_width = line_width
        poly.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        poly.is_not_accessible = True
        return self.add_object(poly)

    def add_polygon(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALLEST,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a polygon to this symbol.
        """
        from .altium_record_sch__polygon import AltiumSchPolygon

        poly = AltiumSchPolygon()
        poly.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        poly.location = poly.vertices[0] if poly.vertices else CoordPoint()
        poly.color = color
        poly.area_color = area_color
        poly.line_width = line_width
        poly.is_solid = is_solid
        poly.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        poly.is_not_accessible = True
        return self.add_object(poly)

    def add_ellipse(
        self,
        x: int,
        y: int,
        radius_x: int,
        radius_y: int,
        *,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALLEST,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an ellipse to this symbol.
        """
        from .altium_record_sch__ellipse import AltiumSchEllipse

        ellipse = AltiumSchEllipse()
        ellipse.location = CoordPoint.from_mils(x, y)
        ellipse.radius = int(radius_x // 10)
        ellipse.secondary_radius = int(radius_y // 10)
        ellipse.color = color
        ellipse.area_color = area_color
        ellipse.line_width = line_width
        ellipse.is_solid = is_solid
        ellipse.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        ellipse.is_not_accessible = True
        return self.add_object(ellipse)

    def add_rounded_rectangle(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        corner_x_radius: int = 50,
        corner_y_radius: int = 50,
        color: int = 0x000000,
        area_color: int = 0xFFFFFF,
        line_width: LineWidth = LineWidth.SMALL,
        is_solid: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a rounded rectangle to this symbol.
        """
        from .altium_record_sch__rounded_rectangle import AltiumSchRoundedRectangle

        rrect = AltiumSchRoundedRectangle()
        rrect.location = CoordPoint.from_mils(x1, y1)
        rrect.corner = CoordPoint.from_mils(x2, y2)
        rrect.corner_x_radius = int(corner_x_radius // 10)
        rrect.corner_y_radius = int(corner_y_radius // 10)
        rrect.color = color
        rrect.area_color = area_color
        rrect.line_width = line_width
        rrect.is_solid = is_solid
        rrect.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        rrect.is_not_accessible = True
        return self.add_object(rrect)

    def add_bezier(
        self,
        vertices: list[tuple[int, int]],
        *,
        color: int = 0x000000,
        line_width: LineWidth = LineWidth.SMALLEST,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add a Bezier curve to this symbol.
        """
        from .altium_record_sch__bezier import AltiumSchBezier

        if len(vertices) != 4:
            raise ValueError(f"Bezier requires exactly 4 vertices, got {len(vertices)}")

        bezier = AltiumSchBezier()
        bezier.vertices = [CoordPoint.from_mils(v[0], v[1]) for v in vertices]
        bezier.location = bezier.vertices[0]
        bezier.color = color
        bezier.line_width = line_width
        bezier.owner_part_id = 1 if owner_part_id == -1 else owner_part_id
        bezier.is_not_accessible = True
        return self.add_object(bezier)

    def add_label(
        self,
        text: str,
        x: int,
        y: int,
        *,
        color: int = 0x000000,
        font_id: int = 1,
        orientation: "TextOrientation" = TextOrientation.DEGREES_0,
        owner_part_id: int = -1,
    ) -> AltiumSchLabel:
        """
        Add a text label to this symbol.
        """
        label = AltiumSchLabel()
        label.text = text
        label.location = CoordPoint.from_mils(x, y)
        label.color = color
        label.font_id = font_id
        label.orientation = orientation
        label.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(label)

    def add_parameter(
        self,
        name: str,
        text: str = "",
        *,
        x: int = 0,
        y: int = 0,
        is_hidden: bool = False,
        font_id: int = 1,
        read_only: bool = False,
        index_in_sheet: int | None = None,
        owner_part_id: int = -1,
        owner_index: int | None = None,
        unique_id: str = "",
    ) -> AltiumSchParameter:
        """
        Add a parameter to this symbol.
        """
        param = AltiumSchParameter()
        param.name = name
        param.text = text
        param.location = CoordPoint.from_mils(x, y)
        param.is_hidden = is_hidden
        param.font_id = font_id
        param.owner_part_id = None if owner_part_id == -1 else owner_part_id
        param.unique_id = unique_id
        _as_dynamic(param)._extra_fields = {}
        if read_only:
            _as_dynamic(param)._extra_fields["ReadOnlyState"] = "1"
        if index_in_sheet is not None:
            _as_dynamic(param)._extra_fields["IndexInSheet"] = str(index_in_sheet)
        added = self.add_object(param)
        if owner_index is not None:
            added.owner_index = owner_index
        return added

    def add_designator(
        self,
        text: str = "U?",
        x: int = 0,
        y: int = 0,
        *,
        color: int = 0x000000,
        font_id: int = 1,
        owner_part_id: int = -1,
    ) -> AltiumSchDesignator:
        """
        Add a designator to this symbol.
        """
        desig = AltiumSchDesignator()
        desig.text = text
        desig.location = CoordPoint.from_mils(x, y)
        desig.color = color
        desig.font_id = font_id
        desig.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(desig)

    def add_image(
        self,
        filename: str,
        image_data: bytes,
        x: int,
        y: int,
        corner_x: int,
        corner_y: int,
        *,
        keep_aspect: bool = True,
        embedded: bool = True,
        owner_part_id: int = -1,
    ) -> object:
        """
        Add an embedded image to this symbol.
        """
        from .altium_record_sch__image import AltiumSchImage

        image = AltiumSchImage()
        image.filename = filename
        image.image_data = image_data
        image.embed_image = embedded
        image.keep_aspect = keep_aspect
        image.location = CoordPoint.from_mils(x, y)
        image.corner = CoordPoint.from_mils(corner_x, corner_y)
        image.owner_part_id = None if owner_part_id == -1 else owner_part_id
        return self.add_object(image)

    def add_implementation(
        self,
        impl_record: dict[str, object] | AltiumSchImplementation,
        children: list[dict[str, object] | Any] | None = None,
    ) -> AltiumSchImplementation:
        """
        Add an implementation record with optional child records.

        Args:
            impl_record: IMPLEMENTATION payload or typed implementation record.
            children: Optional child records (MAP_DEFINER_LIST, MAP_DEFINER,
                IMPL_PARAMS) as raw dicts or typed objects.
        """
        implementation = self._coerce_implementation_record(impl_record)
        implementation_children = [
            self._coerce_implementation_child_record(child) for child in children or []
        ]
        if not any(
            isinstance(child, AltiumSchMapDefinerList)
            for child in implementation_children
        ):
            implementation_children.insert(0, AltiumSchMapDefinerList())
        if not any(
            isinstance(child, AltiumSchImplParams) for child in implementation_children
        ):
            implementation_children.append(AltiumSchImplParams())
        self._validate_implementation_child_order(implementation_children)
        self.add_object(implementation)
        for child in implementation_children:
            self.add_object(child)
        self._ensure_implementation_list_marker()
        self._rebuild_implementation_structure()
        return implementation

    @staticmethod
    def _validate_implementation_child_order(children: Collection[object]) -> None:
        has_map_list = False
        for child in children:
            if isinstance(child, AltiumSchMapDefinerList):
                has_map_list = True
            elif isinstance(child, AltiumSchMapDefiner) and not has_map_list:
                raise ValueError(
                    "a MapDefiner requires an earlier MapDefinerList child"
                )

    def add_footprint(
        self,
        model_name: str,
        *,
        description: str = "",
        is_current: bool = True,
        library_name: str = "",
    ) -> AltiumSchImplementation:
        """
        Add a PCB footprint implementation to this symbol.
        """
        impl_record, children = build_footprint_implementation_payload(
            model_name,
            description=description,
            is_current=is_current,
            library_name=library_name,
        )
        return self.add_implementation(impl_record, children)

    @staticmethod
    def _is_implementation_related_object(obj: Any) -> bool:
        return isinstance(
            obj,
            (
                AltiumSchImplementationList,
                AltiumSchImplementation,
                AltiumSchMapDefinerList,
                AltiumSchMapDefiner,
                AltiumSchImplParams,
            ),
        )

    @staticmethod
    def _coerce_implementation_record(
        record: dict[str, object] | AltiumSchImplementation,
    ) -> AltiumSchImplementation:
        if isinstance(record, AltiumSchImplementation):
            return record
        if not isinstance(record, dict):
            raise TypeError(
                "add_implementation() requires an AltiumSchImplementation or raw record dict"
            )
        record_obj = _create_record_object(record)
        if not isinstance(record_obj, AltiumSchImplementation):
            raise TypeError("implementation record must resolve to an Implementation")
        return record_obj

    @staticmethod
    def _coerce_implementation_child_record(record: dict[str, object] | Any) -> Any:
        if isinstance(
            record,
            (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
        ):
            return record
        if not isinstance(record, dict):
            raise TypeError(
                "implementation child records must be typed objects or raw record dicts"
            )
        record_obj = _create_record_object(record)
        if not isinstance(
            record_obj,
            (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
        ):
            raise TypeError("implementation child must resolve to RECORD 46, 47, or 48")
        return record_obj

    def _ensure_implementation_list_marker(self) -> AltiumSchImplementationList:
        existing = next(iter(self.implementation_lists), None)
        if existing is not None:
            return existing
        marker = AltiumSchImplementationList()
        self.add_object(marker)
        return marker

    def _rebuild_implementation_structure(self) -> None:
        """
        Rebuild runtime implementation grouping from the symbol object list.
        """
        implementation_lists = list(self.implementation_lists)
        implementations: list[AltiumSchImplementation] = []

        for implementation_list in implementation_lists:
            implementation_list.children = []
        for implementation in self.implementations:
            implementation.map = AltiumSchMapDefinerList()
            implementation.map.parent = implementation
            implementation.children = [implementation.map]

        current_impl: AltiumSchImplementation | None = None
        current_map: AltiumSchMapDefinerList | None = None
        for obj in self.objects:
            if isinstance(obj, AltiumSchImplementationList):
                current_impl = None
                current_map = None
                continue
            if isinstance(obj, AltiumSchImplementation):
                current_impl = obj
                current_map = obj.map
                implementations.append(obj)
                continue
            if current_impl is None:
                continue
            if isinstance(obj, AltiumSchMapDefinerList):
                previous = current_impl.map
                if previous in current_impl.children:
                    current_impl.children.remove(previous)
                previous.parent = None
                current_impl.map = obj
                current_map = obj
                current_impl.children.append(obj)
                obj.parent = current_impl
                continue
            if isinstance(obj, AltiumSchMapDefiner) and current_map is not None:
                current_map.add_map_definer(obj)
                continue
            if isinstance(obj, AltiumSchImplParams):
                current_impl.children.append(obj)
                _as_dynamic(obj).parent = current_impl

        if implementation_lists:
            marker = implementation_lists[0]
            marker.children = implementations
            for implementation in implementations:
                if hasattr(implementation, "parent"):
                    _as_dynamic(implementation).parent = marker

    def _collect_implementation_groups_for_synthesis(
        self,
    ) -> tuple[
        AltiumSchImplementationList | None,
        list[tuple[AltiumSchImplementation, list[Any]]],
    ]:
        implementation_list = next(iter(self.implementation_lists), None)
        groups: list[tuple[AltiumSchImplementation, list[Any]]] = []
        current_impl: AltiumSchImplementation | None = None
        current_children: list[Any] = []

        for obj in self.objects:
            if isinstance(obj, AltiumSchImplementationList):
                continue
            if isinstance(obj, AltiumSchImplementation):
                if current_impl is not None:
                    groups.append((current_impl, current_children))
                current_impl = obj
                current_children = []
                continue
            if current_impl is not None and isinstance(
                obj,
                (AltiumSchMapDefinerList, AltiumSchMapDefiner, AltiumSchImplParams),
            ):
                if isinstance(obj, AltiumSchMapDefinerList):
                    current_children.append(obj)
                    current_children.extend(obj.children)
                elif not isinstance(obj, AltiumSchMapDefiner):
                    current_children.append(obj)

        if current_impl is not None:
            groups.append((current_impl, current_children))

        return implementation_list, groups

    @staticmethod
    def _update_bounds_point(bounds: dict[str, float], x: float, y: float) -> None:
        bounds["min_x"] = min(bounds["min_x"], x)
        bounds["min_y"] = min(bounds["min_y"], y)
        bounds["max_x"] = max(bounds["max_x"], x)
        bounds["max_y"] = max(bounds["max_y"], y)

    def _update_graphic_bounds(self, bounds: dict[str, float], graphic: Any) -> None:
        points = getattr(graphic, "points", ())
        point_defined_bounds = getattr(
            graphic, "record_type", None
        ) == SchRecordType.HARNESS_BUNDLE and bool(points)
        if hasattr(graphic, "location") and not point_defined_bounds:
            self._update_bounds_point(bounds, graphic.location.x, graphic.location.y)
        if hasattr(graphic, "corner"):
            self._update_bounds_point(bounds, graphic.corner.x, graphic.corner.y)
        if hasattr(graphic, "vertices"):
            for vertex in graphic.vertices:
                if hasattr(vertex, "x"):
                    self._update_bounds_point(bounds, vertex.x, vertex.y)
        if points:
            for point in points:
                if hasattr(point, "x"):
                    self._update_bounds_point(bounds, point.x, point.y)
        if hasattr(graphic, "radius") and hasattr(graphic, "location"):
            cx, cy = graphic.location.x, graphic.location.y
            rx = graphic.radius
            ry = getattr(graphic, "secondary_radius", rx)
            self._update_bounds_point(bounds, cx - rx, cy - ry)
            self._update_bounds_point(bounds, cx + rx, cy + ry)

    def _update_group_size_bounds(
        self,
        bounds: dict[str, float],
        graphical_object: object,
    ) -> None:
        location = getattr(graphical_object, "location", None)
        if location is None:
            return
        x_size = getattr(
            graphical_object,
            "xsize",
            getattr(graphical_object, "x_size", None),
        )
        y_size = getattr(
            graphical_object,
            "ysize",
            getattr(graphical_object, "y_size", None),
        )
        if not isinstance(x_size, int | float) or not isinstance(y_size, int | float):
            return
        right_padding = (
            15 if isinstance(graphical_object, AltiumSchHarnessConnector) else 0
        )
        self._update_bounds_point(
            bounds,
            location.x + x_size + right_padding,
            location.y - y_size,
        )

    def _update_pin_bounds(self, bounds: dict[str, float], pin: Any) -> None:
        if not hasattr(pin, "location"):
            return

        loc = pin.location
        self._update_bounds_point(bounds, loc.x, loc.y)
        if not (hasattr(pin, "length") and hasattr(pin, "orientation")):
            return

        pin_len = pin.length
        orient = (
            pin.orientation.value
            if hasattr(pin.orientation, "value")
            else pin.orientation
        )
        if orient == 0:
            self._update_bounds_point(bounds, loc.x + pin_len, loc.y)
        elif orient == 1:
            self._update_bounds_point(bounds, loc.x, loc.y + pin_len)
        elif orient == 2:
            self._update_bounds_point(bounds, loc.x - pin_len, loc.y)
        elif orient == 3:
            self._update_bounds_point(bounds, loc.x, loc.y - pin_len)

    def _update_corner_bounds(self, bounds: dict[str, float], obj: Any) -> None:
        if hasattr(obj, "location") and hasattr(obj, "corner"):
            self._update_bounds_point(bounds, obj.location.x, obj.location.y)
            self._update_bounds_point(bounds, obj.corner.x, obj.corner.y)

    def get_bounds(
        self,
        part_id: int | None = None,
        display_mode: int | None = None,
        *,
        _eligible_source_ids: Collection[int] | None = None,
        _parent_by_source_id: Mapping[int, object | None] | None = None,
        _geometry_ctx: SchSvgRenderContext | None = None,
    ) -> tuple[int, int, int, int] | None:
        """
        Calculate the bounding box for this symbol in internal units (10-mil).

        Args:
            part_id: For multi-part symbols, calculate bounds only for this part.
                     If None, calculates bounds for all parts combined.
                     Records with owner_part_id=0 or -1 are shared across all parts.
            display_mode: Optional Altium symbol display mode/body style.
                          If None, calculates bounds for all display modes.

        Returns:
            Tuple of (min_x, min_y, max_x, max_y) in internal units, or None if
            the symbol has no graphics/pins with calculable bounds (e.g., image-only symbols).

        Note:
            Internal units are 10-mil (0.254mm). Multiply by 10 to get mils.
        """
        from .altium_schlib import AltiumSchLib

        bounds = {
            "min_x": float("inf"),
            "min_y": float("inf"),
            "max_x": float("-inf"),
            "max_y": float("-inf"),
        }
        eligible_ids = (
            None if _eligible_source_ids is None else frozenset(_eligible_source_ids)
        )

        def is_eligible(source_object: object) -> bool:
            if eligible_ids is not None:
                return id(source_object) in eligible_ids
            return AltiumSchLib._record_belongs_to_part(
                source_object, part_id
            ) and record_belongs_to_display_mode(source_object, display_mode)

        parents = _parent_by_source_id or {}
        selected_parameter_models, image_parameter_owned_ids = (
            self._selected_image_parameter_models(
                is_eligible,
                parents,
            )
        )
        selected_model_ids = {
            id(model) for _parameter, model in selected_parameter_models if model
        }

        for graphic in self.graphic_primitives:
            if not is_eligible(graphic):
                continue
            self._update_graphic_bounds(bounds, graphic)

        for pin in self.pins:
            if not is_eligible(pin):
                continue
            self._update_pin_bounds(bounds, pin)

        for img in self.images:
            if not is_eligible(img):
                continue
            if (
                id(img) in image_parameter_owned_ids
                and id(img) not in selected_model_ids
            ):
                continue
            self._update_corner_bounds(bounds, img)

        self._update_image_parameter_model_bounds(
            bounds,
            selected_parameter_models,
            _geometry_ctx,
        )

        for label in self.labels:
            if not is_eligible(label):
                continue
            if hasattr(label, "location"):
                self._update_bounds_point(bounds, label.location.x, label.location.y)

        for text_frame in self.text_frames:
            if not is_eligible(text_frame):
                continue
            self._update_corner_bounds(bounds, text_frame)

        self._update_parameter_set_bounds(
            bounds,
            (source for source in self.objects if is_eligible(source)),
            _geometry_ctx,
        )

        for graphical_object in self.objects:
            if not is_eligible(graphical_object):
                continue
            if getattr(graphical_object, "record_type", None) not in {
                SchRecordType.HARNESS_SPLICE,
                SchRecordType.HARNESS_LAYOUT_LABEL,
                SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
                SchRecordType.HARNESS_BUNDLE,
                SchRecordType.HARNESS_LAYOUT_COVERING,
                SchRecordType.HARNESS_CONNECTOR,
                SchRecordType.SHEET_SYMBOL,
            }:
                continue
            self._update_graphic_bounds(bounds, graphical_object)
            self._update_group_size_bounds(bounds, graphical_object)

        if bounds["min_x"] == float("inf"):
            return None

        return (
            int(bounds["min_x"]),
            int(bounds["min_y"]),
            int(bounds["max_x"]),
            int(bounds["max_y"]),
        )

    def _update_parameter_set_bounds(
        self,
        bounds: dict[str, float],
        sources: Iterable[object],
        geometry_ctx: SchSvgRenderContext | None,
    ) -> None:
        from .altium_record_sch__parameter_set import AltiumSchParameterSet

        selected_sources = tuple(sources)
        geometry_ctx = self._parameter_set_bounds_context(
            selected_sources, geometry_ctx
        )
        if geometry_ctx is None:
            return
        for source in selected_sources:
            if type(source) is not AltiumSchParameterSet:
                continue
            self._update_internal_geometry_bounds(
                bounds,
                self._parameter_set_source_bounds(source, geometry_ctx),
            )

    def _parameter_set_bounds_context(
        self,
        selected_sources: tuple[object, ...],
        geometry_ctx: SchSvgRenderContext | None,
    ) -> SchSvgRenderContext | None:
        if geometry_ctx is not None:
            return geometry_ctx
        from .altium_record_sch__parameter_set import AltiumSchParameterSet

        if not any(
            type(source) is AltiumSchParameterSet for source in selected_sources
        ):
            return None
        from ._sch_source_admission import _SourceAdmission
        from .altium_sch_svg_renderer import SchSvgRenderContext

        all_sources = tuple(self.objects)
        geometry_ctx = SchSvgRenderContext(schlib_mode=True)
        geometry_ctx._source_admission = _SourceAdmission.from_projection(
            all_sources,
            {id(source) for source in selected_sources},
            {id(source): getattr(source, "parent", None) for source in all_sources},
        )
        geometry_ctx._capture_parameter_set_state()
        return geometry_ctx

    @staticmethod
    def _parameter_set_source_bounds(
        source: AltiumSchParameterSet,
        geometry_ctx: SchSvgRenderContext,
    ) -> SchGeometryBounds:
        from ._altium_record_sch__harness_layout import _harness_internal_location
        from ._altium_sch_component_bounds import (
            _parameter_set_bounds_from_state,
            _portable_string_bounds,
        )
        from .altium_sch_enums import ParameterSetStyle

        differential_pair = source._source_is_differential_pair(
            geometry_ctx._source_admission,
            geometry_ctx._parameter_set_work_budget,
        )
        geometry_ctx._parameter_set_work_budget.reserve_text_characters(
            len(source.name)
        )
        display_string = (
            "" if differential_pair else source._get_display_string(geometry_ctx)
        )
        measure_count = (
            int(not differential_pair and source.style == ParameterSetStyle.LARGE) * 2
        )
        geometry_ctx._parameter_set_work_budget.reserve_text_characters(
            len(display_string) * measure_count
        )
        return _parameter_set_bounds_from_state(
            _harness_internal_location(source.location),
            source.orientation,
            source.style,
            has_effective_document=geometry_ctx.owner_document_present,
            differential_pair=differential_pair,
            display_string=display_string,
            measure=lambda location, text, font_id: _portable_string_bounds(
                geometry_ctx, location, text, font_id
            ),
            max_text_characters=geometry_ctx.options.max_parameter_set_text_characters,
        )

    def _selected_image_parameter_models(
        self,
        is_eligible: Callable[[object], bool],
        parents: Mapping[int, object | None],
    ) -> tuple[
        tuple[tuple[AltiumSchImageParameter, object | None], ...],
        frozenset[int],
    ]:
        from ._sch_source_admission import _SourceAdmission
        from .altium_record_sch__parameter import AltiumSchImageParameter

        sources = tuple(self.objects)
        admission = _SourceAdmission.from_projection(
            sources,
            {id(source) for source in sources if is_eligible(source)},
            parents,
        )
        image_parameter_owned_ids = admission.descendant_ids_of_type(
            sources, AltiumSchImageParameter
        )
        selected = tuple(
            (source, source._model_source(admission))
            for source in sources
            if isinstance(source, AltiumSchImageParameter)
            and is_eligible(source)
            and id(source) not in image_parameter_owned_ids
        )
        return selected, image_parameter_owned_ids

    def _update_image_parameter_model_bounds(
        self,
        bounds: dict[str, float],
        selected: Collection[tuple[AltiumSchImageParameter, object | None]],
        geometry_ctx: SchSvgRenderContext | None,
    ) -> None:
        from ._altium_record_sch__physical_model import (
            _AltiumSchHarnessCavityComponent,
            _AltiumSchLineView,
        )
        from .altium_record_sch__image import AltiumSchImage

        internal_model_types = (_AltiumSchLineView, _AltiumSchHarnessCavityComponent)
        for parameter, model in selected:
            if model is None and geometry_ctx is not None:
                from .altium_schdoc import AltiumSchDoc

                self._update_internal_geometry_bounds(
                    bounds,
                    AltiumSchDoc._harness_parameter_text_bounds(
                        parameter, geometry_ctx
                    ),
                )
            elif model is None:
                self._update_bounds_point(
                    bounds, parameter.location.x, parameter.location.y
                )
            elif isinstance(model, internal_model_types):
                self._update_internal_model_bounds(bounds, model)
            elif not isinstance(model, AltiumSchImage):
                self._update_bounds_point(
                    bounds, parameter.location.x, parameter.location.y
                )

    def _update_internal_model_bounds(
        self, bounds: dict[str, float], source: object
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryBounds

        own_bounds = getattr(source, "own_bounds_internal", None)
        raw = own_bounds() if callable(own_bounds) else None
        if not isinstance(raw, SchGeometryBounds):
            return
        self._update_internal_geometry_bounds(bounds, raw)

    def _update_internal_geometry_bounds(
        self, bounds: dict[str, float], raw: SchGeometryBounds
    ) -> None:
        self._update_bounds_point(bounds, raw.left / 100_000, raw.bottom / 100_000)
        self._update_bounds_point(bounds, raw.right / 100_000, raw.top / 100_000)

    def _apply_component_record(self, record: dict[str, Any]) -> None:
        self.component_record = record
        self.unique_id = record.get("UniqueID") or record.get("UNIQUEID")
        serializer = AltiumSerializer()
        libref, _, _ = read_dynamic_string_field(
            serializer,
            record,
            record,
            Fields.LIB_REFERENCE,
            default="",
        )
        design_item_id, _, _ = read_dynamic_string_field(
            serializer,
            record,
            record,
            Fields.DESIGN_ITEM_ID,
            default="",
        )
        self.original_name = libref or design_item_id or self.original_name
        self.description, _, _ = read_dynamic_string_field(
            serializer,
            record,
            record,
            Fields.COMPONENT_DESCRIPTION,
            default="",
        )
        # Altium stores the part count field as actual_count + 1.
        part_count_stored = int(record.get("PartCount", record.get("PARTCOUNT", 1)))
        self.part_count = part_count_stored - 1 if part_count_stored > 1 else 1
        self.display_mode = int(record.get("DisplayMode", record.get("DISPLAYMODE", 0)))
        self.display_mode_count = int(
            record.get("DisplayModeCount", record.get("DISPLAYMODECOUNT", 1))
        )

    def _set_component_identity(self, original_name: str) -> None:
        """Update the semantic component identity in every retained record view."""
        self.original_name = original_name
        records = [self.component_record, *self.raw_records]
        seen: set[int] = set()
        for record in records:
            if record is None or str(record.get("RECORD", "")) != "1":
                continue
            identity = id(record)
            if identity in seen:
                continue
            seen.add(identity)
            _replace_dynamic_text_field(record, "LibReference", original_name)
            _replace_dynamic_text_field(record, "DesignItemId", original_name)

    def _add_image_record(self, record: dict[str, Any], record_index: int) -> None:
        image_obj = create_record_from_type(SchRecordType.IMAGE)
        if image_obj is None:
            return
        image_obj.parse_from_record(record)
        _as_dynamic(image_obj)._record_index = record_index
        self._append_parsed_object(image_obj)

    def _raw_record_is_pin(self, record_index: int) -> bool:
        if record_index >= len(self.raw_records):
            return False
        owner_record = self.raw_records[record_index]
        if not owner_record.get("__BINARY_RECORD__"):
            return False
        binary_data = owner_record.get("__BINARY_DATA__")
        return bool(
            isinstance(binary_data, bytes | bytearray)
            and len(binary_data) > 0
            and binary_data[0] == 0x02
        )

    def _store_pin_parameter(
        self,
        owner_index: int,
        parameter: AltiumSchParameter,
    ) -> None:
        parameter._set_unique_id_locked(True)
        self._pin_parameters.setdefault(owner_index, []).append(parameter)

    def _add_parameter_record_object(self, parameter: AltiumSchParameter) -> None:
        owner_index = getattr(parameter, "_owner_index", None)
        if isinstance(owner_index, int) and owner_index > 0:
            if self._raw_record_is_pin(owner_index):
                self._store_pin_parameter(owner_index, parameter)
                return
            self._append_parsed_object(parameter)
            return
        self._append_parsed_object(parameter)

    def _add_text_record_object(self, record_obj: object) -> None:
        if isinstance(record_obj, AltiumSchParameter):
            self._add_parameter_record_object(record_obj)
            return
        if isinstance(record_obj, AltiumSchPin):
            # PINs are synchronized via sync_pins_to_raw_records(); do not track
            # them in graphics to avoid stale-index overwrite bugs.
            return
        self._append_parsed_object(record_obj)

    def _add_typed_record_object(
        self,
        record_type_int: int,
        record: dict[str, Any],
        record_index: int,
    ) -> None:
        if record_type_int == SchRecordType.COMPONENT:
            self._apply_component_record(record)
            return
        if record_type_int == SchRecordType.IMAGE:
            self._add_image_record(record, record_index)
            return

        record_obj = _create_record_object(record)
        if record_obj is None:
            return
        _as_dynamic(record_obj)._record_index = record_index
        self._add_text_record_object(record_obj)

    def _add_binary_pin_record(
        self,
        record: dict[str, Any],
        record_index: int,
        font_manager: FontIDManager | None,
    ) -> None:
        binary_data = record.get("__BINARY_DATA__")
        if not record.get("__BINARY_RECORD__") or not binary_data:
            return
        if len(binary_data) == 0 or binary_data[0] != 0x02:
            return

        pin = AltiumSchPin()
        pin.parse_from_record(record, font_manager=font_manager)
        _as_dynamic(pin)._record_index = record_index
        self._append_parsed_object(pin)

    def add_record(
        self,
        record: dict[str, Any],
        font_manager: FontIDManager | None = None,
    ) -> None:
        """
        Add a parsed record to the symbol.

                Args:
                    record: Raw record dictionary
                    font_manager: FontIDManager for PIN font handling (optional)
        """
        record_index = len(self.raw_records)
        self.raw_records.append(record)

        record_type = record.get("RECORD")
        if record_type:
            self._add_typed_record_object(int(record_type), record, record_index)

        self._add_binary_pin_record(record, record_index, font_manager)

    def sync_pins_to_raw_records(self) -> int:
        """
        Sync OOP PIN objects back to raw_records for serialization.

        This method updates the __BINARY_DATA__ and __ORIGINAL_LENGTH_BYTES__
        in raw_records from the corresponding OOP AltiumSchPin objects.
        Called before save() to ensure modifications to PIN objects are persisted.

        Returns:
            Number of PINs synced.
        """
        import struct

        synced_count = 0
        for pin in self.pins:
            record_index = getattr(pin, "_record_index", None)
            if record_index is not None and record_index < len(self.raw_records):
                raw_record = self.raw_records[record_index]
                if raw_record.get("__BINARY_RECORD__"):
                    # Serialize OOP PIN to binary and update raw_record
                    new_binary = pin._serialize_binary()
                    raw_record["__BINARY_DATA__"] = new_binary
                    # Update length bytes to match new data length
                    # Binary records use high byte 0x01 as mode indicator
                    new_length = len(new_binary) | 0x01000000
                    raw_record["__ORIGINAL_LENGTH_BYTES__"] = struct.pack(
                        "<I", new_length
                    )
                    synced_count += 1
        return synced_count

    def sync_graphics_to_raw_records(self) -> int:
        """
        Sync OOP graphical/text records back to raw_records.

        Includes `graphic_primitives`, `labels`, `designators`, and `parameters` so style
        edits on symbol text records are persisted during SchLib saves.

        Returns:
            Number of records synced.
        """
        from .altium_record_sch__image import AltiumSchImage

        synced_count = 0
        seen_record_indices: set[int] = set()
        for record_obj in [
            *self.graphic_primitives,
            *self.images,
            *self.labels,
            *self.designators,
            *self.parameters,
        ]:
            # PIN objects are handled by sync_pins_to_raw_records().
            if isinstance(record_obj, AltiumSchPin):
                continue
            record_index = getattr(record_obj, "_record_index", None)
            if record_index is None and isinstance(record_obj, AltiumSchImage):
                record_index = len(self.raw_records)
                _as_dynamic(record_obj)._record_index = record_index
                self.raw_records.append(record_obj.serialize_to_record())
                seen_record_indices.add(record_index)
                synced_count += 1
                continue
            if record_index is None or record_index >= len(self.raw_records):
                continue
            if record_index in seen_record_indices:
                continue
            raw_record = self.raw_records[record_index]
            if raw_record.get("__BINARY_RECORD__"):
                continue
            self.raw_records[record_index] = record_obj.serialize_to_record()
            seen_record_indices.add(record_index)
            synced_count += 1
        return synced_count

    def _live_data_objects(
        self,
    ) -> tuple[list[object], dict[int, object]]:
        """Collect live Data members and the owning pins of deferred parameters."""
        candidates = [
            source
            for source in self._objects
            if getattr(source, "_source_stream", "Data") != "Additional"
        ]
        indexed_candidates = self._indexed_data_objects(candidates)
        parameter_owners: dict[int, object] = {}
        for owner_index, parameters in self._pin_parameters.items():
            owner = indexed_candidates.get(owner_index)
            if not isinstance(owner, AltiumSchPin):
                continue
            candidates.extend(parameters)
            parameter_owners.update((id(parameter), owner) for parameter in parameters)
        parents = AltiumSchLib._symbol_render_parent_map(self, candidates)
        unattached_ids = self._managed_membership_unattached_ids(candidates, parents)
        objects: list[object] = []
        for source in candidates:
            if id(source) in unattached_ids:
                _as_dynamic(source)._managed_unattached = True
            else:
                objects.append(source)
        retained_ids = {id(source) for source in objects}
        parameter_owners = {
            source_id: owner
            for source_id, owner in parameter_owners.items()
            if source_id in retained_ids
        }
        return objects, parameter_owners

    def _managed_membership_unattached_ids(
        self,
        candidates: Collection[object],
        parents: Mapping[int, object | None],
    ) -> set[int]:
        from ._sch_source_projection import (
            _parameter_attachment_role,
        )

        structurally_unattached = set(
            AltiumSchLib._symbol_structurally_unattached_source_ids(
                self, candidates, parents
            )
        )
        ignored_ids = {
            id(source)
            for source in candidates
            if self._managed_import_ignores_source(source)
        }
        parameter_parents = {
            source_id: (_MANAGED_COMPONENT_ROOT if parent is None else parent)
            for source_id, parent in parents.items()
        }
        structurally_unattached.update(
            self._managed_parameter_unattached_ids(
                candidates,
                parameter_parents,
                ignored_ids,
                _parameter_attachment_role,
            )
        )
        save_phase_record_types = {
            SchRecordType.IMPLEMENTATION_LIST,
            SchRecordType.IMPL_PARAMS,
        }
        omitted = self._direct_managed_unattached_ids(
            candidates,
            structurally_unattached,
            save_phase_record_types,
            self._managed_import_ignores_source,
        )
        return self._descendant_closed_ids(candidates, parents, omitted)

    @staticmethod
    def _managed_import_ignores_source(source: object) -> bool:
        from ._sch_source_projection import _record_import_ignores_source

        return getattr(
            source, "_managed_container_membership", None
        ) != "object_list" and _record_import_ignores_source(source)

    @classmethod
    def _direct_managed_unattached_ids(
        cls,
        candidates: Collection[object],
        structurally_unattached: Collection[int],
        save_phase_record_types: Collection[SchRecordType],
        ignores_source: Callable[[object], bool],
    ) -> set[int]:
        return {
            id(source)
            for source in candidates
            if cls._data_source_is_unattached(
                source,
                structurally_unattached,
                save_phase_record_types,
                ignores_source(source),
            )
        }

    @staticmethod
    def _descendant_closed_ids(
        candidates: Collection[object],
        parents: Mapping[int, object | None],
        direct_ids: Collection[int],
    ) -> set[int]:
        omitted = set(direct_ids)
        children: dict[int, list[int]] = {}
        for source in candidates:
            parent = parents[id(source)]
            if parent is not None:
                children.setdefault(id(parent), []).append(id(source))
        pending = list(omitted)
        while pending:
            for child_id in children.get(pending.pop(), ()):
                if child_id not in omitted:
                    omitted.add(child_id)
                    pending.append(child_id)
        return omitted

    @staticmethod
    def _managed_parameter_unattached_ids(
        candidates: Collection[object],
        parents: Mapping[int, object | None],
        ignored_ids: Collection[int],
        attachment_role: Callable[[AltiumSchParameter, object | None], str],
    ) -> set[int]:
        omitted: set[int] = set()
        fields: dict[tuple[int, str], int] = {}
        for source in candidates:
            if (
                not isinstance(source, AltiumSchParameter)
                or source.record_type != SchRecordType.PARAMETER
                or id(source) in ignored_ids
            ):
                continue
            if getattr(source, "_managed_container_membership", None) == "object_list":
                continue
            owner = parents.get(id(source))
            role = attachment_role(source, owner)
            if role in {"unattached", "pin_state"}:
                omitted.add(id(source))
            elif role != "ordinary":
                key = (id(owner), role)
                previous = fields.get(key)
                if previous is not None:
                    omitted.add(previous)
                fields[key] = id(source)
        return omitted

    @staticmethod
    def _data_source_is_unattached(
        source: object,
        structurally_unattached: Collection[int],
        save_phase_record_types: Collection[SchRecordType],
        ignored: bool,
    ) -> bool:
        if ignored or bool(getattr(source, "_managed_unattached", False)):
            return True
        if id(source) not in structurally_unattached:
            return False
        return getattr(source, "record_type", None) not in save_phase_record_types

    def _indexed_data_objects(self, objects: Iterable[object]) -> dict[int, object]:
        indexed: dict[int, object] = {}
        for source in objects:
            record_index = getattr(source, "_record_index", None)
            if not isinstance(record_index, int):
                continue
            if not 0 < record_index < len(self.raw_records):
                continue
            if record_index in indexed:
                raise ValueError(
                    f"multiple schematic objects retain Data row {record_index}"
                )
            indexed[record_index] = source
        return indexed

    def _data_owner_objects(
        self,
        objects: Collection[object],
        old_objects: Mapping[int, object],
        parameter_owners: Mapping[int, object],
    ) -> dict[int, object | None]:
        live_ids = {id(source) for source in objects}
        owners: dict[int, object | None] = {}
        for source in objects:
            parent = parameter_owners.get(id(source), getattr(source, "parent", None))
            if parent is not None and id(parent) in live_ids:
                owners[id(source)] = parent
                continue
            record_index = getattr(source, "_record_index", None)
            old_owner_index = self._old_data_owner_index(record_index)
            old_owner = old_objects.get(old_owner_index)
            if old_owner is not None and id(old_owner) in live_ids:
                owners[id(source)] = old_owner
                continue
            owner_index = getattr(source, "owner_index", 0)
            owners[id(source)] = old_objects.get(owner_index)
        return owners

    def _old_data_owner_index(self, record_index: object) -> int:
        if not isinstance(record_index, int):
            return 0
        if not 0 <= record_index < len(self._normalized_owner_indices):
            return 0
        return self._normalized_owner_indices[record_index]

    @staticmethod
    def _owner_ordered_data_objects(
        objects: Collection[object], owners: Mapping[int, object | None]
    ) -> list[object]:
        """Return the managed stable depth-first save order."""
        live_ids = {id(source) for source in objects}
        children = AltiumSymbol._data_children_by_owner(objects, owners, live_ids)
        return AltiumSymbol._managed_preorder(children, len(objects))

    @staticmethod
    def _data_children_by_owner(
        objects: Collection[object],
        owners: Mapping[int, object | None],
        live_ids: set[int],
    ) -> dict[int | None, list[object]]:
        children: dict[int | None, list[object]] = {}
        for source in objects:
            parent = owners[id(source)]
            parent_id = (
                id(parent) if parent is not None and id(parent) in live_ids else None
            )
            children.setdefault(parent_id, []).append(source)
        for parent_id, siblings in children.items():
            if parent_id is None:
                siblings.sort(key=AltiumSymbol._managed_component_sort_key)
                continue
            siblings.sort(
                key=lambda source: (
                    not AltiumSymbol._is_container_list_member_for_parent(
                        source, owners[id(source)]
                    ),
                    *AltiumSymbol._managed_sibling_sort_key(source),
                )
            )
        return children

    @staticmethod
    def _managed_preorder(
        children: Mapping[int | None, Sequence[object]], object_count: int
    ) -> list[object]:
        emitted: set[int] = set()
        visiting: set[int] = set()
        ordered: list[object] = []
        pending = [(source, False) for source in reversed(children.get(None, ()))]
        while pending:
            source, leaving = pending.pop()
            source_id = id(source)
            if leaving:
                visiting.remove(source_id)
                emitted.add(source_id)
                continue
            if source_id in emitted:
                continue
            if source_id in visiting:
                raise ValueError("schematic symbol ownership contains a cycle")
            visiting.add(source_id)
            ordered.append(source)
            pending.append((source, True))
            pending.extend(
                (child, False) for child in reversed(children.get(source_id, ()))
            )
        if len(emitted) != object_count:
            raise ValueError("schematic symbol ownership contains a cycle")
        return ordered

    @staticmethod
    def _managed_sibling_sort_key(source: object) -> tuple[bool, int]:
        """Model SchDataObjectComparator's stable ordering for supported records."""
        record_type = getattr(source, "record_type", 0)
        binary_code = int(getattr(record_type, "value", record_type))
        return binary_code > 225, binary_code if binary_code > 225 else 0

    @staticmethod
    def _managed_component_sort_key(source: object) -> tuple[int, int]:
        """Model the component override's object, field, and implementation phases."""
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key

        binary_high, binary_code = AltiumSymbol._managed_sibling_sort_key(source)
        if isinstance(source, AltiumSchImplementationList):
            return 3, 0
        if binary_high:
            return 2, binary_code
        if not AltiumSymbol._is_container_list_member_for_parent(source, None):
            if isinstance(source, AltiumSchDesignator):
                return 1, 0
            if isinstance(source, AltiumSchParameter) and (
                dotnet_ordinal_ignore_case_key(source.name)
                == dotnet_ordinal_ignore_case_key("Comment")
            ):
                return 1, 1
        return 0, 0

    @staticmethod
    def _serialized_data_record(source: object) -> dict[str, object]:
        import struct

        serialize = getattr(source, "serialize_to_record", None)
        if not callable(serialize):
            raise TypeError(
                f"schematic symbol member {type(source).__name__} is not serializable"
            )
        record = cast(dict[str, object], serialize())
        binary_data = record.get("__BINARY_DATA__")
        if record.get("__BINARY_RECORD__") and isinstance(binary_data, bytes):
            record["__ORIGINAL_LENGTH_BYTES__"] = struct.pack(
                "<I", len(binary_data) | 0x01000000
            )
        return record

    def _reconcile_data_membership(self) -> None:
        """Rebuild parsed Data membership while retaining typed record payloads."""
        if not self._data_membership_dirty:
            return
        objects, parameter_owners = self._live_data_objects()
        old_objects = self._indexed_data_objects(objects)
        ordered_input = self._data_reconciliation_input(objects, old_objects)
        owners = self._data_owner_objects(ordered_input, old_objects, parameter_owners)
        ordered = self._owner_ordered_data_objects(ordered_input, owners)
        records, normalized_owners, new_indices, old_to_new = (
            self._reconciled_data_records(ordered, owners)
        )
        self.raw_records = records
        self.component_record = records[0]
        self._normalized_owner_indices = tuple(normalized_owners)
        self._remap_pin_parameter_owners(old_objects, new_indices)
        self._rebase_additional_record_indices(len(records), old_to_new)
        self._clear_omitted_data_record_locations(objects)
        self._data_record_index_remap = old_to_new
        self._data_membership_dirty = False

    def _prepare_managed_membership_rebuild(self) -> bool:
        """Route a mutated object graph through the managed save warehouses."""
        if not (self._data_membership_dirty or self._additional_membership_dirty):
            return False
        members = self._container_member_objects()
        parents = AltiumSchLib._symbol_render_parent_map(self, members)
        live_ids = {id(source) for source in members}
        children = self._children_by_live_parent(members, live_ids)
        ordered = self._managed_preorder(children, len(members))
        additional_ids: set[int] = set()
        for source in ordered:
            parent = parents[id(source)]
            record_type = getattr(source, "record_type", 0)
            record_id = int(getattr(record_type, "value", record_type))
            if id(parent) in additional_ids or (
                record_id in _SCHLIB_MANAGED_ADDITIONAL_RECORD_IDS
            ):
                additional_ids.add(id(source))
            _as_dynamic(source).parent = parent
        for source in members:
            if id(source) in additional_ids:
                _as_dynamic(source)._source_stream = "Additional"
            else:
                _as_dynamic(source)._source_stream = "Data"
                self._clear_source_additional_owner_flag(source)
        self._data_membership_dirty = True
        self._additional_membership_dirty = True
        return True

    @staticmethod
    def _clear_source_additional_owner_flag(source: object) -> None:
        if hasattr(source, "owner_index_additional_list"):
            _as_dynamic(source).owner_index_additional_list = False
        for attribute in ("_raw_record", "_record"):
            record = getattr(source, attribute, None)
            if not isinstance(record, MutableMapping):
                continue
            for key in list(record):
                if str(key).casefold() == "ownerindexadditionallist":
                    record.pop(key)

    def _clear_data_additional_owner_flags(self) -> None:
        for record in self.raw_records:
            for key in list(record):
                if key.casefold() == "ownerindexadditionallist":
                    record.pop(key)

    def _clear_omitted_data_record_locations(
        self, retained: Collection[object]
    ) -> None:
        retained_ids = {id(source) for source in retained}
        for source in self._objects:
            if (
                id(source) not in retained_ids
                and getattr(source, "_source_stream", "Data") != "Additional"
            ):
                self._clear_persisted_record_location(source)
                _as_dynamic(source)._managed_unattached = True

    @staticmethod
    def _data_reconciliation_input(
        objects: Collection[object], old_objects: Mapping[int, object]
    ) -> list[object]:
        highest_index = max(old_objects, default=0)
        ordered_input = [
            old_objects[index]
            for index in range(1, highest_index + 1)
            if index in old_objects
        ]
        indexed_object_ids = {id(source) for source in old_objects.values()}
        ordered_input.extend(
            source for source in objects if id(source) not in indexed_object_ids
        )
        return ordered_input

    def _reconciled_data_records(
        self,
        ordered: Collection[object],
        owners: Mapping[int, object | None],
    ) -> tuple[
        list[dict[str, object]],
        list[int],
        dict[int, int],
        dict[int, int],
    ]:
        new_indices = {id(source): index for index, source in enumerate(ordered, 1)}
        sibling_indices = self._data_sibling_indices(ordered, owners)
        old_to_new: dict[int, int] = {0: 0}
        records = [dict(self.raw_records[0])]
        normalized_owners = [0]
        for source in ordered:
            old_index = getattr(source, "_record_index", None)
            owner = owners[id(source)]
            owner_index = new_indices.get(id(owner), 0) if owner is not None else 0
            if hasattr(source, "owner_index"):
                _as_dynamic(source).owner_index = owner_index
            if hasattr(source, "owner_index_additional_list"):
                _as_dynamic(source).owner_index_additional_list = False
            if hasattr(source, "index_in_sheet"):
                _as_dynamic(source).index_in_sheet = sibling_indices[id(source)]
            record = self._serialized_data_record(source)
            for key in list(record):
                if key.casefold() == "ownerindexadditionallist":
                    record.pop(key)
            self._normalize_index_in_sheet_field(record, sibling_indices[id(source)])
            new_index = len(records)
            _as_dynamic(source)._record_index = new_index
            records.append(record)
            normalized_owners.append(owner_index)
            if isinstance(old_index, int) and not hasattr(
                source, "_additional_stream_record_index"
            ):
                old_to_new[old_index] = new_index
        return records, normalized_owners, new_indices, old_to_new

    @classmethod
    def _data_sibling_indices(
        cls,
        ordered: Collection[object],
        owners: Mapping[int, object | None],
    ) -> dict[int, int]:
        next_index: dict[int | None, int] = {}
        result: dict[int, int] = {}
        for source in ordered:
            parent = owners[id(source)]
            parent_id = id(parent) if parent is not None else None
            if not cls._is_container_list_member_for_parent(source, parent):
                result[id(source)] = -1
                continue
            result[id(source)] = next_index.get(parent_id, 0)
            next_index[parent_id] = result[id(source)] + 1
        return result

    def _remap_pin_parameter_owners(
        self,
        old_objects: Mapping[int, object],
        new_indices: Mapping[int, int],
    ) -> None:
        self._pin_parameters = {
            new_indices[id(owner)]: parameters
            for old_owner, parameters in self._pin_parameters.items()
            if (owner := old_objects.get(old_owner)) is not None
            and id(owner) in new_indices
        }

    def _rebase_additional_record_indices(
        self, data_record_count: int, old_to_new: Mapping[int, int]
    ) -> None:
        from .altium_serializer import _read_param_boolean

        for record in self._additional_raw_records:
            if _read_param_boolean(record, "OwnerIndexAdditionalList"):
                continue
            old_owner = self._record_owner_index(record)
            new_owner = old_to_new.get(old_owner, 0)
            if new_owner != old_owner:
                self._set_record_owner_index(record, new_owner)
                self._additional_membership_dirty = True
        for source in self._objects:
            if getattr(source, "_source_stream", "Data") != "Additional":
                continue
            stream_index = getattr(source, "_additional_stream_record_index", None)
            if isinstance(stream_index, int) and 0 <= stream_index < len(
                self._additional_raw_records
            ):
                raw_record = self._additional_raw_records[stream_index]
                if not _read_param_boolean(raw_record, "OwnerIndexAdditionalList"):
                    _as_dynamic(source).owner_index = self._record_owner_index(
                        raw_record
                    )
            slot = getattr(source, "_additional_record_index", None)
            if isinstance(slot, int):
                _as_dynamic(source)._record_index = data_record_count + slot

    def _sync_additional_to_raw_records(self) -> bool:
        """Synchronize modeled Additional objects into their retained stream rows."""
        if self._additional_membership_dirty:
            self._reconcile_additional_membership()
            self._additional_membership_dirty = False
            self._clear_migrated_additional_locations()
            return True
        return sum(self._sync_additional_source(source) for source in self.objects) > 0

    def _clear_migrated_additional_locations(self) -> None:
        for source in self.objects:
            if getattr(source, "_source_stream", "Data") == "Additional":
                continue
            for attribute in (
                "_additional_record_index",
                "_additional_stream_record_index",
            ):
                if hasattr(source, attribute):
                    delattr(source, attribute)

    def _sync_additional_source(self, source: object) -> bool:
        if not self._is_additional_save_member(source):
            return False
        stream_index = getattr(source, "_additional_stream_record_index", None)
        serialize = getattr(source, "serialize_to_record", None)
        if not isinstance(stream_index, int) or not callable(serialize):
            return False
        if not 0 <= stream_index < len(self._additional_raw_records):
            return False
        serialized = cast(dict[str, object], serialize())
        if serialized == self._additional_raw_records[stream_index]:
            return False
        self._additional_raw_records[stream_index] = serialized
        return True

    def _reconcile_additional_membership(self) -> None:
        """Rebuild Additional rows in the established depth-first save order."""
        container_members = self._container_member_objects()
        sources = [
            source
            for source in container_members
            if self._is_additional_save_member(source)
        ]
        source_ids = {id(source) for source in sources}
        owners = self._normalized_additional_owners(sources)
        children = self._container_children_for_save()
        ordered = self._managed_additional_order(children, source_ids)
        sibling_indices = self._additional_sibling_indices(children, source_ids)
        opaque = self._retained_opaque_additional_rows()
        old_to_new = self._complete_additional_index_remap(ordered, opaque)
        records = self._serialize_additional_members(ordered, owners, sibling_indices)
        records.extend(
            self._remap_opaque_additional_owner(record, old_to_new)
            for _old_index, record in opaque
        )
        self._additional_raw_records = records
        self._clear_omitted_additional_locations(source_ids)

    def _clear_omitted_additional_locations(
        self, retained_source_ids: Collection[int]
    ) -> None:
        for source in self.objects:
            if id(source) in retained_source_ids:
                continue
            had_additional_location = any(
                hasattr(source, attribute)
                for attribute in (
                    "_additional_record_index",
                    "_additional_stream_record_index",
                )
            )
            is_additional = getattr(source, "_source_stream", "Data") == "Additional"
            if not (had_additional_location or is_additional):
                continue
            for attribute in (
                "_additional_record_index",
                "_additional_stream_record_index",
            ):
                if hasattr(source, attribute):
                    delattr(source, attribute)
            if is_additional:
                if hasattr(source, "_record_index"):
                    delattr(source, "_record_index")
                _as_dynamic(source)._managed_unattached = True

    def _normalized_additional_owners(
        self, sources: Collection[object]
    ) -> dict[int, object | None]:
        all_ids = {id(source) for source in self.objects}
        owners: dict[int, object | None] = {}
        for source in sources:
            parent = getattr(source, "parent", None)
            if id(parent) not in all_ids:
                parent = None
                _as_dynamic(source).parent = None
            owners[id(source)] = parent
        return owners

    def _serialize_additional_members(
        self,
        ordered: Collection[object],
        owners: Mapping[int, object | None],
        sibling_indices: Mapping[int, int],
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        new_indices = {id(source): index for index, source in enumerate(ordered)}
        for slot, source in enumerate(ordered):
            parent = owners[id(source)]
            owner_index, owner_in_additional = self._additional_owner_for_save(
                parent, new_indices
            )
            self._prepare_additional_source_for_save(
                source,
                slot=slot,
                owner_index=owner_index,
                owner_in_additional=owner_in_additional,
                sibling_index=sibling_indices[id(source)],
            )
            record = self._serialized_data_record(source)
            self._normalize_index_in_sheet_field(record, sibling_indices[id(source)])
            self._normalize_additional_owner_fields(
                record,
                owner_index=owner_index,
                owner_in_additional=owner_in_additional,
            )
            records.append(record)
        return records

    @staticmethod
    def _normalize_index_in_sheet_field(
        record: dict[str, object], index_in_sheet: int
    ) -> None:
        if record.get("__BINARY_RECORD__"):
            return
        for key in list(record):
            if key.casefold() == "indexinsheet":
                record.pop(key)
        if index_in_sheet >= 0:
            record["IndexInSheet"] = str(index_in_sheet)

    def _complete_additional_index_remap(
        self,
        ordered: Collection[object],
        opaque: Collection[tuple[int, dict[str, object]]],
    ) -> dict[int, int]:
        result = self._additional_modeled_index_remap(ordered)
        opaque_start = len(ordered)
        result.update(
            (old_index, opaque_start + offset)
            for offset, (old_index, _record) in enumerate(opaque)
        )
        return result

    @staticmethod
    def _is_additional_save_member(source: object) -> bool:
        from ._sch_source_projection import _record_import_ignores_source

        return (
            getattr(source, "_source_stream", None) == "Additional"
            and not _record_import_ignores_source(source)
            and not bool(getattr(source, "_managed_unattached", False))
        )

    def _container_children_for_save(self) -> dict[int | None, list[object]]:
        """Reconstruct managed owner-container order before save-time sorting."""
        objects = self._container_member_objects()
        live_ids = {id(source) for source in objects}
        if not self.raw_records:
            children = self._children_by_live_parent(objects, live_ids)
            self._place_fields_after_object_list(children)
            return children
        parsed_data, parsed_additional, new_objects = self._container_object_partitions(
            objects
        )
        ordered_sources = [*parsed_data, *parsed_additional, *new_objects]
        parent_ids = self._container_parent_ids(objects, live_ids)
        children = self._children_by_parent_ids(ordered_sources, parent_ids)
        self._restore_additional_container_positions(
            children, parsed_additional, parent_ids
        )
        self._place_fields_after_object_list(children)
        return children

    def _container_member_objects(self) -> list[object]:
        candidates = list(self.objects)
        parents = AltiumSchLib._symbol_render_parent_map(self, candidates)
        omitted = self._managed_membership_unattached_ids(candidates, parents)
        return [source for source in candidates if id(source) not in omitted]

    @staticmethod
    def _container_object_partitions(
        objects: Collection[object],
    ) -> tuple[list[object], list[object], list[object]]:
        parsed_data = sorted(
            (
                source
                for source in objects
                if getattr(source, "_source_stream", "Data") != "Additional"
                and isinstance(getattr(source, "_record_index", None), int)
            ),
            key=lambda source: getattr(source, "_record_index"),
        )
        parsed_additional = sorted(
            (
                source
                for source in objects
                if getattr(source, "_source_stream", "Data") == "Additional"
                and isinstance(
                    getattr(source, "_additional_stream_record_index", None), int
                )
            ),
            key=lambda source: getattr(source, "_additional_stream_record_index"),
        )
        parsed_ids = {id(source) for source in (*parsed_data, *parsed_additional)}
        new_objects = [source for source in objects if id(source) not in parsed_ids]
        return parsed_data, parsed_additional, new_objects

    def _container_parent_ids(
        self, objects: Collection[object], live_ids: set[int]
    ) -> dict[int, int | None]:
        data_objects = [
            source
            for source in objects
            if getattr(source, "_source_stream", "Data") != "Additional"
        ]
        indexed = self._indexed_data_objects(data_objects)
        data_owners = self._data_owner_objects(data_objects, indexed, {})
        result: dict[int, int | None] = {}
        for source in objects:
            parent = (
                getattr(source, "parent", None)
                if getattr(source, "_source_stream", "Data") == "Additional"
                else data_owners[id(source)]
            )
            result[id(source)] = id(parent) if id(parent) in live_ids else None
        return result

    @staticmethod
    def _children_by_live_parent(
        sources: Collection[object], live_ids: set[int]
    ) -> dict[int | None, list[object]]:
        parent_ids = {
            id(source): (
                id(parent)
                if id(parent := getattr(source, "parent", None)) in live_ids
                else None
            )
            for source in sources
        }
        return AltiumSymbol._children_by_parent_ids(sources, parent_ids)

    @staticmethod
    def _children_by_parent_ids(
        sources: Collection[object], parent_ids: Mapping[int, int | None]
    ) -> dict[int | None, list[object]]:
        children: dict[int | None, list[object]] = {}
        for source in sources:
            children.setdefault(parent_ids[id(source)], []).append(source)
        return children

    @staticmethod
    def _is_container_list_member(source: object) -> bool:
        """Exclude managed field/synthetic records absent from objectList."""
        return AltiumSymbol._is_container_list_member_for_parent(
            source, getattr(source, "parent", None)
        )

    @staticmethod
    def _is_container_list_member_for_parent(
        source: object, parent: object | None
    ) -> bool:
        """Classify membership with a reconstructed managed owner when needed."""
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key

        membership = getattr(source, "_managed_container_membership", None)
        if membership in {"field", "object_list"}:
            return membership == "object_list"

        parent_type = getattr(parent, "record_type", None)
        record_type = getattr(source, "record_type", None)
        if parent_type == SchRecordType.HARNESS_CONNECTOR:
            return record_type != SchRecordType.HARNESS_TYPE
        if parent_type in {
            SchRecordType.SHEET_SYMBOL,
            SchRecordType.HIGH_LEVEL_CODE_SYMBOL,
        }:
            return record_type not in {
                SchRecordType.SHEET_NAME,
                SchRecordType.FILE_NAME,
            }
        if parent_type == SchRecordType.IMPLEMENTATION:
            return record_type not in {
                SchRecordType.MAP_DEFINER_LIST,
                SchRecordType.IMPL_PARAMS,
            }
        if parent_type == SchRecordType.RTF_LINK and isinstance(
            source, AltiumSchParameter
        ):
            return dotnet_ordinal_ignore_case_key(
                source.name
            ) != dotnet_ordinal_ignore_case_key("Comment")
        if parent is not None:
            return True
        if isinstance(source, AltiumSchDesignator):
            return False
        if isinstance(source, AltiumSchParameter) and (
            dotnet_ordinal_ignore_case_key(source.name)
            == dotnet_ordinal_ignore_case_key("Comment")
        ):
            return False
        return getattr(source, "record_type", None) not in {
            SchRecordType.IMPLEMENTATION_LIST,
            SchRecordType.IMPL_PARAMS,
        }

    def _additional_sibling_indices(
        self,
        children: Mapping[int | None, Sequence[object]],
        additional_ids: set[int],
    ) -> dict[int, int]:
        result: dict[int, int] = {}
        for siblings in children.values():
            object_list_index = 0
            for source in siblings:
                if id(source) not in additional_ids:
                    if self._is_container_list_member(source):
                        object_list_index += 1
                    continue
                if not self._is_container_list_member(source):
                    result[id(source)] = -1
                    continue
                result[id(source)] = object_list_index
                object_list_index += 1
        return result

    def _place_fields_after_object_list(
        self, children: Mapping[int | None, list[object]]
    ) -> None:
        """Mirror iterator order: objectList members precede managed fields."""
        for siblings in children.values():
            fields = [
                source
                for source in siblings
                if not self._is_container_list_member(source)
            ]
            if not fields:
                continue
            siblings[:] = [
                source for source in siblings if self._is_container_list_member(source)
            ]
            siblings.extend(fields)

    @staticmethod
    def _restore_additional_container_positions(
        children: Mapping[int | None, list[object]],
        parsed_additional: Collection[object],
        parent_ids: Mapping[int, int | None],
    ) -> None:
        """Restore imported Additional-row positions from their stored indexes."""
        for source in parsed_additional:
            if getattr(source, "owner_index_additional_list", False):
                continue
            index = getattr(source, "index_in_sheet", -1)
            if not isinstance(index, int) or index < 0:
                continue
            siblings = children.get(parent_ids[id(source)])
            if siblings is None or source not in siblings:
                continue
            siblings.remove(source)
            siblings.insert(min(index, len(siblings)), source)

    @classmethod
    def _managed_additional_order(
        cls,
        container_children: Mapping[int | None, Sequence[object]],
        additional_ids: set[int],
    ) -> list[object]:
        """Walk the primary warehouse depth-first and divert Additional subtrees."""
        ordered: list[object] = []
        pending: list[tuple[object, bool]] = [
            (source, False)
            for source in reversed(
                cls._sorted_container_children(container_children.get(None, ()), True)
            )
        ]
        while pending:
            source, in_additional = pending.pop()
            source_id = id(source)
            diverted = in_additional or source_id in additional_ids
            if diverted:
                ordered.append(source)
            children = container_children.get(source_id, ())
            if diverted:
                pending.extend((child, True) for child in reversed(children))
            else:
                pending.extend(
                    (child, False)
                    for child in reversed(
                        cls._sorted_container_children(children, False)
                    )
                )
        if len(ordered) != len(additional_ids):
            raise ValueError("schematic symbol ownership contains a cycle")
        return ordered

    @classmethod
    def _sorted_container_children(
        cls, sources: Sequence[object], component_root: bool
    ) -> list[object]:
        key = (
            cls._managed_component_sort_key
            if component_root
            else cls._managed_sibling_sort_key
        )
        return sorted(sources, key=key)

    def _additional_modeled_index_remap(
        self, ordered: Collection[object]
    ) -> dict[int, int]:
        result: dict[int, int] = {}
        for new_index, source in enumerate(ordered):
            old_index = getattr(source, "_additional_stream_record_index", None)
            if isinstance(old_index, int):
                result[old_index] = new_index
        return result

    def _retained_opaque_additional_rows(
        self,
    ) -> list[tuple[int, dict[str, object]]]:
        modeled_indices = {
            index
            for source in self.objects
            if isinstance(
                (index := getattr(source, "_additional_stream_record_index", None)),
                int,
            )
        }
        return [
            (index, dict(record))
            for index, record in enumerate(self._additional_raw_records)
            if index not in modeled_indices
        ]

    def _prepare_additional_source_for_save(
        self,
        source: object,
        *,
        slot: int,
        owner_index: int,
        owner_in_additional: bool,
        sibling_index: int,
    ) -> None:
        if hasattr(source, "owner_index"):
            _as_dynamic(source).owner_index = owner_index
        if hasattr(source, "owner_index_additional_list"):
            _as_dynamic(source).owner_index_additional_list = owner_in_additional
        if hasattr(source, "index_in_sheet"):
            _as_dynamic(source).index_in_sheet = sibling_index
        _as_dynamic(source)._additional_stream_record_index = slot
        _as_dynamic(source)._additional_record_index = slot
        _as_dynamic(source)._record_index = len(self.raw_records) + slot

    def _remap_opaque_additional_owner(
        self, record: dict[str, object], old_to_new: Mapping[int, int]
    ) -> dict[str, object]:
        from .altium_serializer import _read_param_boolean

        if not _read_param_boolean(record, "OwnerIndexAdditionalList"):
            return record
        owner_index = self._record_owner_index(record)
        self._set_record_owner_index(record, old_to_new.get(owner_index, 0))
        return record

    @staticmethod
    def _additional_owner_for_save(
        parent: object | None, additional_indices: Mapping[int, int]
    ) -> tuple[int, bool]:
        if parent is None:
            return 0, False
        if getattr(parent, "_source_stream", "Data") == "Additional":
            index = additional_indices.get(id(parent))
            if index is None:
                raise ValueError("Additional parent must precede its child during save")
            return index, True
        index = getattr(parent, "_record_index", None)
        return (index if isinstance(index, int) else 0), False

    @staticmethod
    def _normalize_additional_owner_fields(
        record: dict[str, object], *, owner_index: int, owner_in_additional: bool
    ) -> None:
        for key in list(record):
            if key.casefold() in {"ownerindex", "ownerindexadditionallist"}:
                record.pop(key)
        if owner_index != 0:
            record["OwnerIndex"] = str(owner_index)
        if owner_in_additional:
            record["OwnerIndexAdditionalList"] = "T"

    def _get_record_name(self, record_type: int) -> str:
        """
        Get human-readable name for record type.
        """
        names = {
            4: "LABEL",
            5: "BEZIER",
            6: "POLYLINE",
            7: "POLYGON",
            8: "ELLIPSE",
            10: "ROUND_RECTANGLE",
            11: "ELLIPTICAL_ARC",
            12: "ARC",
            13: "LINE",
            14: "RECTANGLE",
            30: "IMAGE",
            41: "PARAMETER",
        }
        return names.get(record_type, f"UNKNOWN({record_type})")

    def get_summary(self) -> dict[str, Any]:
        """
        Get summary statistics for this symbol.
        """
        return {
            "name": self.name,
            "description": self.description,
            "part_count": self.part_count,
            "pin_count": len(self.pins),
            "graphic_count": len(self.graphic_primitives),
            "label_count": len(self.labels),
            "image_count": len(self.images),
            "parameter_count": len(self.parameters),
            "designator_count": len(self.designators),
            "text_frame_count": len(self.text_frames),
            "implementation_count": len(self.implementations),
            "total_records": len(self.raw_records),
        }

    def get_all_records(self) -> list:
        """
        Get all parsed record objects (excluding raw records and pins).

        Returns list of OOP record objects that can be serialized.
        """
        records = []
        records.extend(self.labels)
        records.extend(self.parameters)
        records.extend(self.designators)
        records.extend(self.text_frames)
        records.extend(self.graphic_primitives)
        return records

    def synthesize_raw_records(self) -> list[dict[str, object]]:
        """
        Generate raw_records from OOP objects in self.objects.

                This bridges authored symbol objects to the binary serialization path.
                When symbols are built via add_symbol() + add_object(), raw_records
                is empty. This method synthesizes the raw records needed for save().

                Returns:
                    List of raw record dicts ready for create_stream_from_records().
        """
        import struct

        records: list[dict[str, object]] = []

        # Component record must be first
        comp_record = self.component_record
        if comp_record is None:
            comp_record = {
                "RECORD": str(SchRecordType.COMPONENT),
                "LibReference": self.original_name,
                "ComponentDescription": self.description or "",
                "PartCount": str(
                    self.part_count + 1
                ),  # Altium quirk: stored as actual+1
                "CurrentPartId": "1",
                "DisplayModeCount": str(self.display_mode_count),
                "OwnerPartId": "-1",
                "LibraryPath": "*",
                "SourceLibraryName": "*",
                "TargetFileName": "*",
                "DesignItemId": self.original_name,
            }
            if self.unique_id:
                comp_record["UniqueID"] = self.unique_id
            if self.display_mode != 0:
                comp_record["DisplayMode"] = str(self.display_mode)
        records.append(cast(dict[str, object], comp_record))

        # Serialize non-implementation OOP objects in insertion order.
        for obj in self.objects:
            if getattr(obj, "_source_stream", "Data") == "Additional":
                continue
            if self._is_implementation_related_object(obj):
                continue
            if hasattr(obj, "serialize_to_record"):
                raw = obj.serialize_to_record()
                if raw:
                    _as_dynamic(obj)._record_index = len(records)
                    # PIN records produce binary records with __BINARY_DATA__
                    if raw.get("__BINARY_RECORD__"):
                        # Ensure length bytes are set for binary records
                        if (
                            "__ORIGINAL_LENGTH_BYTES__" not in raw
                            and "__BINARY_DATA__" in raw
                        ):
                            binary_data = raw["__BINARY_DATA__"]
                            new_length = len(binary_data) | 0x01000000
                            raw["__ORIGINAL_LENGTH_BYTES__"] = struct.pack(
                                "<I", new_length
                            )
                    records.append(raw)

        implementation_list, implementation_groups = (
            self._collect_implementation_groups_for_synthesis()
        )
        marker_record = (
            implementation_list.serialize_to_record()
            if implementation_list is not None
            else {"RECORD": "44"}
        )
        records.append(clean_implementation_record_fields(dict(marker_record)))
        implementation_list_index = len(records) - 1

        for implementation, children in implementation_groups:
            implementation_record = clean_implementation_record_fields(
                implementation.serialize_to_record()
            )
            implementation_record["OwnerIndex"] = str(implementation_list_index)
            records.append(implementation_record)
            implementation_index = len(records) - 1
            map_list_index: int | None = None
            for child in children:
                if isinstance(child, AltiumSchMapDefinerList):
                    owner_index = implementation_index
                    map_list_index = len(records)
                elif isinstance(child, AltiumSchMapDefiner):
                    if map_list_index is None:
                        raise ValueError(
                            "a MapDefiner requires an earlier MapDefinerList child"
                        )
                    owner_index = map_list_index
                else:
                    owner_index = implementation_index
                child_record = clean_implementation_child_record_fields(
                    child.serialize_to_record(),
                    owner_index=owner_index,
                )
                records.append(child_record)

        return records

    def __repr__(self) -> str:
        return (
            f"AltiumSymbol('{self.name}', {len(self.pins)} pins, "
            f"{len(self.graphic_primitives)} graphics, {len(self.labels)} labels, "
            f"{len(self.images)} images)"
        )


@public_api
class AltiumSchLib(JsonApplyMixin):
    """
    Complete parser for Altium .SchLib files.
    """

    def __init__(
        self,
        filepath: Path | str | None = None,
        debug: bool = False,
        *,
        show_comments_designators: bool = False,
    ) -> None:
        """
        Create an AltiumSchLib.

                Args:
                    filepath: Path to .SchLib binary file to parse.
                              If None, creates an empty library for authoring.
                    debug: Enable debug output.
                    show_comments_designators: Write the SchLib document option
                              that makes Altium show component comments and
                              designators by default in the library editor.
        """
        self._symbols = ObjectCollection()
        self.font_manager: FontIDManager | None = None
        self.file_header = None
        self.embedded_images = {}
        self._raw_storage_entries: dict[str, tuple[bytes, bytes]] = {}
        self._source_streams: dict[str, bytes] = {}
        self._source_stream_paths_by_fold: dict[str, str] = {}
        self._source_storages: tuple[str, ...] = ()
        self._source_symbol_keys: tuple[str, ...] = ()
        self._section_keys_raw: bytes | None = None
        self._section_key_map: dict[str, str] = {}
        self._section_keys_symbol_signature: tuple[tuple[str, str], ...] = ()
        self._header_table_coherent = False
        self._parsed_symbol_signature: tuple[tuple[str, str, int, str], ...] = ()
        self._lib_additional_header: dict[str, object] | None = None
        self._read_limits = _SchLibReadLimits()
        self.debug = debug
        self._show_comments_designators = bool(show_comments_designators)
        self._weight_policy: _SchLibWeightPolicy = "authored"

        if filepath is not None:
            self.filepath = Path(filepath)
            self.filename = self.filepath.name
            self._parse()
            if show_comments_designators:
                self.show_comments_designators = True
            else:
                self._load_show_comments_designators_from_header()
        else:
            self.filepath = None
            self.filename = ""

    @property
    def symbols(self) -> ObjectCollectionView:
        """Return the live read-only symbol membership view."""
        return ObjectCollectionView(self._symbols, lambda _symbol: True)

    @staticmethod
    def _remove_always_show_cd_fields(header: MutableMapping[str, object]) -> None:
        """
        Remove any casing variant of the AlwaysShowCD FileHeader field.
        """
        for key in list(header.keys()):
            if key.upper() == "ALWAYSSHOWCD":
                header.pop(key, None)

    def _load_show_comments_designators_from_header(self) -> None:
        """
        Hydrate the OOP option from a parsed SchLib FileHeader when present.
        """
        if not self.file_header:
            return
        for key, value in self.file_header.items():
            if key.upper() == "ALWAYSSHOWCD":
                self._show_comments_designators = parse_bool(value)
                return

    @property
    def show_comments_designators(self) -> bool:
        """
        Whether SchLib rendering and saved files show comments/designators.

        The default for newly authored libraries is False to preserve existing
        output. Set this to True to render non-hidden bound fields and emit
        ``AlwaysShowCD=T`` in the SchLib FileHeader.
        """
        return self._show_comments_designators

    @show_comments_designators.setter
    def show_comments_designators(self, value: bool) -> None:
        enabled = bool(value)
        self._show_comments_designators = enabled
        if not self.file_header:
            return
        self._remove_always_show_cd_fields(
            cast(MutableMapping[str, object], self.file_header)
        )
        if enabled:
            self.file_header["AlwaysShowCD"] = "T"

    def _ensure_font_manager(self) -> FontIDManager:
        """
        Ensure this library has a font manager for object-bound font resolution.
        """
        if self.font_manager is None:
            self.font_manager = FontIDManager.from_font_dict({})
        return self.font_manager

    def _binding_context(self) -> SchematicBindingContext:
        """
        Get the narrow schematic binding context for this library.
        """
        context = getattr(self, "_schematic_binding_context", None)
        if context is None or context.owner is not self:
            context = SchematicBindingContext(self, kind="schlib")
            self._schematic_binding_context = context
        return context

    def _bind_all_symbols_to_context(self) -> None:
        """
        Bind every symbol and object in this library to the library context.
        """
        context = self._binding_context()
        for symbol in self.symbols:
            symbol._schematic_binding_context = context
            symbol._bind_all_objects_to_context()

    @staticmethod
    def get_symbol_names(filepath: Path) -> list[str]:
        """
        Get symbol names without full parse.

        This is a lightweight method that reads the symbol OLE storage names,
        avoiding the overhead of parsing all symbol data. Storage names are used
        instead of FileHeader LibRef fields because some split libraries retain
        stale FileHeader symbol lists from their source library.

        Args:
            filepath: Path to .SchLib file

        Returns:
            List of symbol names in the library
        """
        ole = AltiumOleFile(str(filepath))
        try:
            return [
                str(entry[0])
                for entry in ole.listdir(streams=False, storages=True)
                if str(entry[0]) != "FileHeader"
            ]
        finally:
            ole.close()

    def _load_file_header(
        self,
        streams: dict[str, bytes],
        budget: _SchLibBudget,
    ) -> dict[str, object]:
        """
        Load the library file header when present.
        """
        data = streams.get("FileHeader")
        if data is None:
            raise SchLibContainerError(
                "missing",
                "SchLib is missing the FileHeader stream",
                stream="FileHeader",
            )
        return _parse_file_header(data, budget)

    def _load_lib_additional_header(
        self,
        streams: Mapping[str, bytes],
        budget: _SchLibBudget,
    ) -> dict[str, object] | None:
        stream = next(
            (path for path in streams if path.casefold() == "libadditional"),
            None,
        )
        if stream is None:
            return None
        records = _parse_instruction_stream(streams[stream], stream, budget)
        if (
            len(records) != 1
            or records[0].get("__BINARY_RECORD__")
            or self._additional_record_type(records[0], stream) != 0
        ):
            raise SchLibContainerError(
                "malformed",
                "LibAdditional must contain exactly one RECORD=0 header",
                stream=stream,
            )
        header = records[0]
        expected_header = self._header_value(
            cast(MutableMapping[str, object], self.file_header or {}), "HEADER"
        )
        if self._header_value(header, "HEADER") != expected_header:
            raise SchLibContainerError(
                "unsupported",
                "LibAdditional is not the SchLib V5 profile",
                stream=stream,
            )
        weight = _parse_i32(
            self._header_value(header, "Weight"), "Weight", stream=stream
        )
        if weight < 0:
            raise SchLibContainerError(
                "malformed", "LibAdditional Weight is negative", stream=stream
            )
        return header

    def _iter_symbol_names(
        self,
        storages: tuple[str, ...],
        streams: dict[str, bytes],
    ) -> tuple[str, ...]:
        """
        Return OLE storage names that represent symbol entries.
        """
        return tuple(
            storage
            for storage in storages
            if "/" not in storage and f"{storage}/Data" in streams
        )

    @staticmethod
    def _canonicalize_data_stream_paths(
        storages: tuple[str, ...], streams: dict[str, bytes]
    ) -> None:
        for storage in storages:
            if "/" in storage:
                continue
            canonical = f"{storage}/Data"
            matches = [
                path for path in streams if path.casefold() == canonical.casefold()
            ]
            if len(matches) > 1:
                raise SchLibContainerError(
                    "duplicate",
                    f"symbol Data paths collide case-insensitively for {storage!r}",
                )
            if matches and matches[0] != canonical:
                streams[canonical] = streams.pop(matches[0])

    def _attach_pin_parameters(self, symbol: AltiumSymbol) -> None:
        """
        Attach deferred pin parameter records to their pin objects.
        """
        if not hasattr(symbol, "_pin_parameters"):
            return

        pins_by_record_index = {
            getattr(pin, "_record_index", None): pin for pin in symbol.pins
        }
        for record_idx, param_list in symbol._pin_parameters.items():
            pin = pins_by_record_index.get(record_idx)
            if pin is None:
                continue
            if not hasattr(pin, "pin_parameters"):
                pin.pin_parameters = []
            pin.pin_parameters.extend(param_list)

    def _apply_pin_functions(
        self,
        ole: AltiumOleFile,
        symbol_name: str,
        symbol: AltiumSymbol,
    ) -> None:
        """
        Apply alternate pin functions from PinFunctionData when present.
        """
        pin_functions = _parse_pinfunctiondata_from_ole(ole, symbol_name)
        if pin_functions and pin_functions["functions"] and symbol.pins:
            _as_dynamic(symbol.pins[0]).alternate_names = pin_functions["functions"]

    def _apply_pin_position_settings(
        self,
        settings: Any,
        pin_text_position: Any,
        margin_attr: str,
        pin: AltiumSchPin,
        *,
        pin_item_mode: Any,
        pin_text_anchor: Any,
        rotation90: Any,
    ) -> None:
        """
        Apply custom PinTextData position settings to a pin text surface.
        """
        settings.position_mode = pin_item_mode.CUSTOM
        rot_value = pin_text_position.orientation // 90
        settings.rotation = rotation90(rot_value & 0x03)

        margin_mils = pin_text_position.margin_mils
        internal_coord = int(round(margin_mils * 10000))
        settings.position_margin = internal_coord // 100000
        margin_frac = internal_coord % 100000
        if margin_frac != 0:
            settings.position_margin_frac = margin_frac

        settings.rotation_anchor = (
            pin_text_anchor.COMPONENT
            if pin_text_position.reference_to_component
            else pin_text_anchor.PIN
        )
        setattr(pin, margin_attr, margin_mils)

    def _apply_pin_font_settings(self, pin: AltiumSchPin, pin_text_data: Any) -> None:
        """
        Apply font and color overrides from PinTextData.
        """
        if pin_text_data.name_font_id is not None:
            pin.name_settings.font_mode = pin.name_settings.font_mode.CUSTOM
            raw_font_id = pin_text_data.name_font_id
            pin.name_settings.font_id = (
                self.font_manager.translate_in(raw_font_id)
                if self.font_manager
                else raw_font_id
            )
        if pin_text_data.name_color is not None:
            pin.name_settings.font_mode = pin.name_settings.font_mode.CUSTOM
            pin.name_settings.color = int(pin_text_data.name_color)
        if pin_text_data.designator_font_id is not None:
            pin.designator_settings.font_mode = pin.designator_settings.font_mode.CUSTOM
            raw_font_id = pin_text_data.designator_font_id
            pin.designator_settings.font_id = (
                self.font_manager.translate_in(raw_font_id)
                if self.font_manager
                else raw_font_id
            )
        if pin_text_data.designator_color is not None:
            pin.designator_settings.font_mode = pin.designator_settings.font_mode.CUSTOM
            pin.designator_settings.color = int(pin_text_data.designator_color)

    def _apply_pintextdata(
        self,
        ole: AltiumOleFile,
        symbol_name: str,
        symbol: AltiumSymbol,
        budget: _SchLibBudget | None = None,
    ) -> None:
        """
        Apply PinTextData stream settings to parsed pin objects when present.
        """
        from .altium_pintextdata_modifier import PinTextDataModifier
        from .altium_sch_enums import PinItemMode, PinTextAnchor, Rotation90

        pintextdata_path = f"{symbol_name}/PinTextData"
        pintextdata_stream = self._source_streams.get(pintextdata_path)
        if pintextdata_stream is None:
            if not ole.exists(pintextdata_path):
                return
            pintextdata_stream = ole.openstream(pintextdata_path)
        active_budget = budget or _SchLibBudget(self._read_limits)
        modifier = PinTextDataModifier()
        try:
            modifier.parse(
                pintextdata_stream,
                limits=self._auxiliary_limits(active_budget),
            )
        except SchAuxiliaryStreamError as exc:
            raise SchLibContainerError(
                exc.kind,
                exc.reason,
                stream=pintextdata_path,
                byte_offset=exc.offset,
            ) from exc
        active_budget.consume_stream(pintextdata_path, len(modifier.entries), 0)
        active_budget.consume_decompressed(
            pintextdata_path,
            tuple(len(entry.raw_data) for _, entry in modifier.entries),
        )

        for index, pin_text_data in modifier._applicable_entries(len(symbol.pins)):
            pin = symbol.pins[index]
            pin.name_settings.position_mode = PinItemMode.DEFAULT
            pin.designator_settings.position_mode = PinItemMode.DEFAULT
            if pin_text_data.name_position:
                self._apply_pin_position_settings(
                    pin.name_settings,
                    pin_text_data.name_position,
                    "_name_margin_mils",
                    pin,
                    pin_item_mode=PinItemMode,
                    pin_text_anchor=PinTextAnchor,
                    rotation90=Rotation90,
                )
            if pin_text_data.designator_position:
                self._apply_pin_position_settings(
                    pin.designator_settings,
                    pin_text_data.designator_position,
                    "_designator_margin_mils",
                    pin,
                    pin_item_mode=PinItemMode,
                    pin_text_anchor=PinTextAnchor,
                    rotation90=Rotation90,
                )
            pin.name_settings.font_mode = PinItemMode.DEFAULT
            pin.designator_settings.font_mode = PinItemMode.DEFAULT
            self._apply_pin_font_settings(pin, pin_text_data)

    def _apply_pin_vertical_margin_data(
        self,
        symbol_name: str,
        symbol: AltiumSymbol,
        budget: _SchLibBudget,
    ) -> None:
        canonical_path = f"{symbol_name}/PinVerticalMarginData"
        stream_path = self._source_stream_paths_by_fold.get(canonical_path.casefold())
        if stream_path is None:
            return
        stream_data = self._source_streams[stream_path]
        try:
            entries = _decode_pin_vertical_margin_stream(
                stream_data,
                limits=self._auxiliary_limits(budget),
            )
        except SchAuxiliaryStreamError as exc:
            raise SchLibContainerError(
                exc.kind,
                exc.reason,
                stream=stream_path,
                byte_offset=exc.offset,
            ) from exc
        budget.consume_stream(stream_path, len(entries), 0)
        budget.consume_decompressed(
            stream_path,
            tuple(len(entry.payload) for entry in entries),
        )
        _apply_pin_vertical_margins(
            [cast(AltiumSchPin, pin) for pin in symbol.pins],
            entries,
        )

    def _attach_embedded_images(self) -> None:
        """
        Match embedded image payloads onto all library IMAGE records.
        """
        embedded = [
            image
            for symbol in self.symbols
            for image in symbol.images
            if image.embedded
        ]
        resolved = resolve_embedded_image_group(
            self.embedded_images,
            ((img.filename, int(img.orientation)) for img in embedded),
        )
        for img, data in zip(embedded, resolved, strict=True):
            if data is not None:
                img.image_data = data
                img.detect_format()

    def _preserve_symbol_streams(
        self,
        symbol_name: str,
        symbol: AltiumSymbol,
    ) -> None:
        """
        Preserve non-Data symbol streams for round-trip saves.
        """
        prefix = f"{symbol_name}/"
        for full_path, payload in self._source_streams.items():
            if not full_path.startswith(prefix):
                continue
            stream_name = full_path[len(prefix) :]
            if "/" in stream_name:
                continue
            if stream_name == "Data":
                continue
            symbol._original_streams[stream_name] = payload

    def _validate_symbol_data_records(
        self,
        records: list[dict[str, object]],
        stream: str,
    ) -> None:
        if not records:
            raise SchLibContainerError(
                "malformed", "symbol Data stream is empty", stream=stream
            )
        component_count = 0
        for index, record in enumerate(records):
            component_count += self._validate_symbol_data_record(record, index, stream)
        if component_count != 1:
            raise SchLibContainerError(
                "malformed",
                "symbol Data must contain exactly one component root",
                stream=stream,
            )

    @staticmethod
    def _validate_symbol_data_record(
        record: dict[str, object], index: int, stream: str
    ) -> int:
        if record.get("__BINARY_RECORD__"):
            if index == 0 or record.get("RECORD") != SchRecordType.PIN.value:
                raise SchLibContainerError(
                    "unsupported",
                    "only compact binary PIN records are supported",
                    stream=stream,
                    record_index=index,
                )
            return 0
        raw_type = next(
            (value for key, value in record.items() if key.casefold() == "record"),
            None,
        )
        if raw_type is None:
            raise SchLibContainerError(
                "malformed",
                "symbol record is missing RECORD",
                stream=stream,
                record_index=index,
            )
        record_type = _parse_i32(raw_type, "RECORD", stream=stream)
        if record_type == SchRecordType.PIN.value:
            raise SchLibContainerError(
                "unsupported",
                "SchLib PIN records must use compact binary encoding",
                stream=stream,
                record_index=index,
            )
        if record_type == SchRecordType.COMPONENT.value:
            if index != 0:
                raise SchLibContainerError(
                    "malformed",
                    "component root must be the first Data record",
                    stream=stream,
                    record_index=index,
                )
            return 1
        if index == 0:
            raise SchLibContainerError(
                "malformed",
                "first Data record is not a component root",
                stream=stream,
                record_index=index,
            )
        AltiumSchLib._require_supported_record(record_type, index, stream)
        return 0

    @staticmethod
    def _require_supported_record(record_type: int, index: int, stream: str) -> None:
        try:
            typed_record = SchRecordType(record_type)
        except ValueError as exc:
            raise SchLibContainerError(
                "unsupported",
                f"unknown schematic record ID {record_type}",
                stream=stream,
                record_index=index,
            ) from exc
        if create_record_from_type(typed_record) is None:
            raise SchLibContainerError(
                "unsupported",
                f"schematic record ID {record_type} has no typed implementation",
                stream=stream,
                record_index=index,
            )

    @staticmethod
    def _raw_owner_index(record: dict[str, object], stream: str) -> int:
        for key, value in record.items():
            if key.casefold() == "ownerindex":
                return _parse_i32(value, "OwnerIndex", stream=stream)
        return 0

    def _validate_symbol_owners(
        self, symbol: AltiumSymbol, stream: str, *, strict_json: bool = False
    ) -> None:
        owners = [0] * len(symbol.raw_records)
        if strict_json and self._raw_owner_index(symbol.raw_records[0], stream) != 0:
            raise ValueError(f"{stream} component root OwnerIndex must be zero")
        pin_owners = {
            int(record_index): int(pin.owner_index)
            for pin in symbol.pins
            if (record_index := getattr(pin, "_record_index", None)) is not None
        }
        for index in range(1, len(symbol.raw_records)):
            record = symbol.raw_records[index]
            owner = (
                pin_owners[index]
                if record.get("__BINARY_RECORD__")
                else self._raw_owner_index(record, stream)
            )
            if strict_json and not 0 <= owner < index:
                raise ValueError(
                    f"{stream} record {index} OwnerIndex must reference an earlier row"
                )
            # Binary source parsing follows AD's repair behavior, while strict
            # JSON must describe the ownership graph without source repair.
            owners[index] = owner if 0 <= owner < index else 0
        depths = [0] * len(owners)
        for index in range(1, len(owners)):
            depths[index] = depths[owners[index]] + 1
            if depths[index] > self._read_limits.max_ownership_depth:
                raise SchLibContainerError(
                    "limit",
                    "ownership depth exceeds the reviewed limit",
                    stream=stream,
                    record_index=index,
                )
            if strict_json:
                child = self._json_record_number(
                    symbol.raw_records[index].get("RECORD"), stream
                )
                owner = self._json_record_number(
                    symbol.raw_records[owners[index]].get("RECORD"), stream
                )
                if not self._valid_json_symbol_owner_kind(child, owner):
                    raise ValueError(
                        f"{stream} record {index} has an invalid owner record kind"
                    )
        symbol._normalized_owner_indices = tuple(owners)

    @staticmethod
    def _valid_json_symbol_owner_kind(child: int, owner: int) -> bool:
        exact = {
            SchRecordType.IMPLEMENTATION.value: {
                SchRecordType.IMPLEMENTATION_LIST.value
            },
            SchRecordType.MAP_DEFINER_LIST.value: {SchRecordType.IMPLEMENTATION.value},
            SchRecordType.IMPL_PARAMS.value: {
                SchRecordType.COMPONENT.value,
                SchRecordType.IMPLEMENTATION.value,
            },
            SchRecordType.MAP_DEFINER.value: {SchRecordType.MAP_DEFINER_LIST.value},
            SchRecordType.IMPLEMENTATION_LIST.value: {SchRecordType.COMPONENT.value},
        }
        allowed = exact.get(child)
        if allowed is not None:
            return owner in allowed
        return owner in {
            SchRecordType.COMPONENT.value,
            SchRecordType.PIN.value,
            SchRecordType.PARAMETER_SET.value,
            SchRecordType.PORT.value,
            SchRecordType.IMPL_PARAMS.value,
        }

    @staticmethod
    def _auxiliary_limits(budget: _SchLibBudget) -> SchAuxiliaryReadLimits:
        limits = budget.limits
        remaining_decompressed = budget.remaining_decompressed_bytes
        return SchAuxiliaryReadLimits(
            max_stream_bytes=limits.max_stream_bytes,
            max_records_per_stream=min(
                limits.max_records_per_stream,
                budget.remaining_records,
            ),
            max_record_bytes=limits.max_record_bytes,
            max_compressed_blob_bytes=limits.max_stream_bytes,
            max_decompressed_blob_bytes=min(
                limits.max_decompressed_blob_bytes,
                remaining_decompressed,
            ),
            max_total_decompressed_blob_bytes=remaining_decompressed,
        )

    def _parse_symbol(
        self,
        ole: AltiumOleFile,
        symbol_name: str,
        original_name: str,
        budget: _SchLibBudget,
    ) -> AltiumSymbol:
        """
        Parse a single symbol storage into an `AltiumSymbol`.
        """
        symbol = AltiumSymbol(symbol_name, original_name=original_name)
        stream = f"{symbol_name}/Data"
        records = _parse_instruction_stream(
            self._source_streams[stream], stream, budget
        )
        self._validate_symbol_data_records(records, stream)
        for record in records:
            symbol.add_record(record, self.font_manager)

        self._validate_symbol_identity(symbol)
        self._validate_symbol_owners(symbol, stream)

        self._attach_pin_parameters(symbol)
        self._apply_pintextdata(ole, symbol_name, symbol, budget)
        self._apply_pin_vertical_margin_data(symbol_name, symbol, budget)
        self._preserve_symbol_streams(symbol_name, symbol)
        symbol._rebuild_implementation_structure()
        self._parse_symbol_additional(symbol_name, symbol, budget)
        symbol._set_unique_id_locked(True)
        return symbol

    def _parse_symbol_additional(
        self,
        symbol_name: str,
        symbol: AltiumSymbol,
        budget: _SchLibBudget,
    ) -> None:
        if self._lib_additional_header is None:
            return
        canonical = f"{symbol_name}/Additional"
        stream = self._source_stream_paths_by_fold.get(canonical.casefold())
        if stream is None:
            return
        records = _parse_instruction_stream(
            self._source_streams[stream], stream, budget
        )
        data_records = self._additional_data_records(records, stream)
        symbol._additional_raw_records = [dict(record) for record in data_records]
        symbol._additional_terminal_record = dict(records[-1])
        self._import_symbol_additional_records(symbol, data_records, stream)

    @staticmethod
    def _additional_record_type(record: Mapping[str, object], stream: str) -> int:
        value = next(
            (value for key, value in record.items() if key.casefold() == "record"),
            None,
        )
        if value is None:
            raise SchLibContainerError(
                "malformed", "Additional record is missing RECORD", stream=stream
            )
        return _parse_i32(value, "RECORD", stream=stream)

    @staticmethod
    def _additional_data_records(
        records: list[dict[str, object]], stream: str
    ) -> list[dict[str, object]]:
        if not records:
            raise SchLibContainerError(
                "malformed", "SchLib Additional stream is empty", stream=stream
            )
        record_ids = [
            AltiumSchLib._additional_record_type(record, stream) for record in records
        ]
        if record_ids[-1] != 0 or 0 in record_ids[:-1]:
            raise SchLibContainerError(
                "malformed",
                "SchLib Additional must end with exactly one RECORD=0",
                stream=stream,
            )
        return records[:-1]

    def _import_symbol_additional_records(
        self,
        symbol: AltiumSymbol,
        records: Collection[dict[str, object]],
        stream: str,
    ) -> None:
        from ._sch_source_projection import _record_import_ignores_source

        base_slots = self._symbol_base_warehouse_slots(symbol)
        additional_slots: list[object | None] = []
        for stream_index, record in enumerate(records):
            record_type_id = self._additional_record_type(record, stream)
            if record_type_id in _SCHLIB_SKIPPED_ADDITIONAL_RECORD_IDS:
                continue
            self._require_supported_record(record_type_id, stream_index, stream)
            source = create_record_from_type(SchRecordType(record_type_id))
            if source is None:
                raise SchLibContainerError(
                    "unsupported",
                    f"schematic record ID {record_type_id} has no typed implementation",
                    stream=stream,
                    record_index=stream_index,
                )
            source.parse_from_record(record, font_manager=self.font_manager)
            slot_index = len(additional_slots)
            _as_dynamic(source)._source_stream = "Additional"
            _as_dynamic(source)._additional_record_index = slot_index
            _as_dynamic(source)._additional_stream_record_index = stream_index
            _as_dynamic(source)._record_index = len(symbol.raw_records) + slot_index
            ignored = _record_import_ignores_source(source)
            additional_slots.append(None if ignored else source)
            owner, owner_type, owner_exists = self._additional_owner_context(
                symbol,
                source,
                record,
                base_slots,
                additional_slots,
                stream,
            )
            _as_dynamic(source)._managed_selected_owner = owner
            _as_dynamic(source)._managed_selected_owner_type = owner_type
            _as_dynamic(source)._managed_selected_owner_exists = owner_exists
            owner, owner_type, owner_exists = self._additional_implementation_owner(
                symbol,
                source,
                owner,
                owner_type,
                owner_exists,
                base_slots,
            )
            _as_dynamic(source).parent = owner
            _as_dynamic(source)._managed_unattached = (
                ignored
                or not owner_exists
                or not self._symbol_update_owner_attaches(source, owner_type)
            )
            symbol._append_parsed_object(source)

    @staticmethod
    def _symbol_base_warehouse_slots(
        symbol: AltiumSymbol,
    ) -> tuple[object | None, ...]:
        from ._sch_source_projection import _record_import_ignores_source

        slots: list[object | None] = [None] * len(symbol.raw_records)
        for source in symbol.objects:
            record_index = getattr(source, "_record_index", None)
            if (
                isinstance(record_index, int)
                and 0 < record_index < len(slots)
                and not _record_import_ignores_source(source)
            ):
                slots[record_index] = source
        return tuple(slots)

    def _additional_owner_context(
        self,
        symbol: AltiumSymbol,
        source: object,
        record: Mapping[str, object],
        base_slots: Sequence[object | None],
        additional_slots: Sequence[object | None],
        stream: str,
    ) -> tuple[object | None, object, bool]:
        from .altium_serializer import _read_param_boolean

        owner_index = self._raw_owner_index(dict(record), stream)
        use_additional = _read_param_boolean(record, "OwnerIndexAdditionalList")
        slots = additional_slots if use_additional else base_slots
        if not 0 <= owner_index < len(slots):
            owner_index = 0
        owner = slots[owner_index] if slots else None
        if use_additional:
            owner_type = getattr(owner, "record_type", None)
            return owner, owner_type, owner is not None
        owner_type = (
            SchRecordType.COMPONENT
            if owner_index == 0
            else getattr(owner, "record_type", None)
        )
        return owner, owner_type, owner_index == 0 or owner is not None

    def _additional_implementation_owner(
        self,
        symbol: AltiumSymbol,
        source: object,
        owner: object | None,
        owner_type: object,
        owner_exists: bool,
        base_slots: Sequence[object | None],
    ) -> tuple[object | None, object, bool]:
        if (
            getattr(source, "record_type", None) != SchRecordType.IMPLEMENTATION
            or owner_type != SchRecordType.IMPLEMENTATION_LIST
            or not owner_exists
            or owner is None
        ):
            return owner, owner_type, owner_exists
        if getattr(owner, "_source_stream", None) == "Additional":
            return (
                getattr(owner, "_managed_selected_owner", None),
                getattr(owner, "_managed_selected_owner_type", None),
                bool(getattr(owner, "_managed_selected_owner_exists", False)),
            )
        marker_index = getattr(owner, "_record_index", None)
        if not isinstance(marker_index, int) or not 0 <= marker_index < len(
            symbol._normalized_owner_indices
        ):
            return None, None, False
        marker_owner_index = symbol._normalized_owner_indices[marker_index]
        marker_owner = base_slots[marker_owner_index]
        marker_owner_type = (
            SchRecordType.COMPONENT
            if marker_owner_index == 0
            else getattr(marker_owner, "record_type", None)
        )
        return (
            marker_owner,
            marker_owner_type,
            (marker_owner_index == 0 or marker_owner is not None),
        )

    def _validate_symbol_identity(self, symbol: AltiumSymbol) -> None:
        libref = str(symbol.original_name or symbol.name)
        explicit_storage_key = self._section_key_map.get(libref)
        storage_key = explicit_storage_key or libref
        if explicit_storage_key is None:
            implicit_storage_key = sanitize_stream_name(libref)
            if implicit_storage_key.casefold() == symbol.name.casefold():
                symbol._uses_implicit_storage_mapping = True
                return
        if storage_key.casefold() != symbol.name.casefold():
            raise SchLibContainerError(
                "malformed",
                "component LibReference does not map to its symbol storage",
                stream=f"{symbol.name}/Data",
            )

    def _parse(self) -> None:
        """
        Parse the SchLib file.
        """
        filepath = self.filepath
        if filepath is None:
            raise SchLibContainerError("missing", "SchLib path is unavailable")
        with AltiumOleFile(str(filepath)) as ole:
            self._source_streams, self._source_storages = _snapshot_container(
                ole,
                filepath.stat().st_size,
                self._read_limits,
            )
            self._canonicalize_data_stream_paths(
                self._source_storages, self._source_streams
            )
            self._index_source_stream_paths()
            budget = _SchLibBudget(self._read_limits)
            self.file_header = self._load_file_header(self._source_streams, budget)
            self._lib_additional_header = self._load_lib_additional_header(
                self._source_streams, budget
            )
            self._section_keys_raw = self._source_streams.get("SectionKeys")
            section_keys = _parse_section_keys(self._section_keys_raw, budget)
            self._section_key_map = dict(section_keys.by_libref)
            discovered = self._iter_symbol_names(
                self._source_storages, self._source_streams
            )
            self._source_symbol_keys = discovered
            symbol_plan = _header_symbol_order(
                self.file_header,
                section_keys,
                discovered,
                self._source_storages,
                tuple(self._source_streams),
                max_indexed_items=self._read_limits.max_indexed_items_per_record,
            )
            self._header_table_coherent = symbol_plan.header_coherent
            storage_data = self._source_streams.get("Storage")
            try:
                storage_result = (
                    _decode_managed_auxiliary_stream(
                        storage_data,
                        expected_header="Icon storage",
                        limits=self._auxiliary_limits(budget),
                    )
                    if storage_data is not None
                    else None
                )
            except SchAuxiliaryStreamError as exc:
                raise SchLibContainerError(
                    exc.kind,
                    exc.reason,
                    stream="Storage",
                    byte_offset=exc.offset,
                ) from exc
            storage_entries = storage_result.entries if storage_result else ()
            budget.consume_stream("Storage", len(storage_entries), 0)
            budget.consume_decompressed(
                "Storage", tuple(len(entry.data) for entry in storage_entries)
            )
            for index, entry in enumerate(storage_entries, start=1):
                try:
                    decode_sch_embedded_image_payload(entry.data)
                except SchEmbeddedImagePayloadError as exc:
                    raise SchLibContainerError(
                        "malformed",
                        str(exc),
                        stream="Storage",
                        record_index=index,
                    ) from exc
            storage_entries = _first_managed_storage_entries(storage_entries)
            self.embedded_images = {entry.name: entry.data for entry in storage_entries}
            self._raw_storage_entries = {
                entry.name: (entry.binary_header, entry.compressed_data)
                for entry in storage_entries
            }
            self.font_manager = FontIDManager.load_from_record(self.file_header)

            for symbol_entry in symbol_plan.entries:
                self._symbols.append(
                    self._parse_symbol(
                        ole,
                        symbol_entry.storage_key,
                        symbol_entry.libref,
                        budget,
                    )
                )
            self._header_table_coherent = (
                self._header_table_coherent
                and self._header_symbol_metadata_is_coherent()
            )
            self._attach_embedded_images()
            self._bind_all_symbols_to_context()
            self._parsed_symbol_signature = self._symbol_table_signature()
            self._section_keys_symbol_signature = self._section_key_signature()

    def _count_pin_synthetic_params(self, pins: ObjectCollection) -> int:
        count = 0
        for pin in pins:
            if getattr(pin, "hidden_net_name", ""):
                count += 1
        return count

    def _get_polygon_vertex_count(self, graphic: object) -> int | None:
        vertices = getattr(graphic, "vertices", None)
        if isinstance(vertices, list | tuple):
            return len(vertices)
        if isinstance(graphic, dict):
            record_type = graphic.get("RECORD")
            if record_type == str(SchRecordType.POLYGON.value):
                return sum(
                    1 for key in graphic if key.startswith("X") and key[1:].isdigit()
                )
        return None

    def _count_graphics_weight(self, graphics: ObjectCollection) -> int:
        import math

        total = 0
        for graphic in graphics:
            vertex_count = self._get_polygon_vertex_count(graphic)
            if vertex_count is None or vertex_count < 50:
                total += 1
                continue
            additional_records = math.ceil((vertex_count - 50) / 49)
            total += 1 + additional_records
        return total

    def _calculate_weight(self, minimal: bool = False) -> int:
        del minimal
        return self._calculate_serialized_data_records_weight()

    def _calculate_serialized_data_records_weight(
        self, serialized_record_counts: dict[str, int] | None = None
    ) -> int:
        """
        Calculate FileHeader Weight from the emitted symbol Data records.

        Altium's managed SchLib V5 exporter writes the base warehouse count as
        FileHeader Weight. For split/merge outputs this corresponds to one
        library/header item plus the serialized records in each symbol's Data
        stream.
        """
        from .altium_sch_stream_sync import _derive_schlib_weight

        if serialized_record_counts is not None:
            return _derive_schlib_weight(tuple(serialized_record_counts.values()))

        total = 1
        for symbol in self.symbols:
            if symbol.raw_records:
                total += len(symbol.raw_records)
            else:
                total += len(symbol.synthesize_raw_records())
        return _derive_schlib_weight((total - 1,))

    def _synthesize_file_header(self, *, minimal: bool = False) -> dict[str, str]:
        """
        Create FileHeader for SchLib builds.

                Builds a complete font table from the font_manager (if available
                from a parsed source) or defaults to Times New Roman 10pt.
        """
        import random
        import string

        unique_id = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        sheet_size = "18000" if minimal else "20000"
        header: dict[str, str] = {
            "HEADER": "Protel for Windows - Schematic Library Editor Binary File Version 5.0",
            "Weight": str(self._calculate_weight(minimal=minimal)),
            "MinorVersion": "9",
            "UniqueID": unique_id,
            "UseMBCS": "T",
            "IsBOC": "T",
            "SheetStyle": "9",
            "BorderOn": "T",
            "SheetNumberSpaceSize": "12",
            "AreaColor": "16317695",
            "SnapGridOn": "T",
            "SnapGridSize": "10",
            "VisibleGridOn": "T",
            "VisibleGridSize": "10",
            "CustomX": sheet_size,
            "CustomY": sheet_size,
            "UseCustomSheet": "T",
            "ReferenceZonesOn": "T",
            "Display_Unit": "0",
            "CompCount": str(len(self.symbols)),
        }
        if self.show_comments_designators:
            header["AlwaysShowCD"] = "T"

        # Build font table from font_manager or default
        if self.font_manager and self.font_manager.fonts:
            export_fonts = self.font_manager.get_export_font_table()
            fonts = export_fonts or list(self.font_manager.fonts.values())
            header["FontIdCount"] = str(len(fonts))
            for font_id, info in enumerate(fonts, start=1):
                header[f"FontName{font_id}"] = info.get("name", "Times New Roman")
                header[f"Size{font_id}"] = str(info.get("size", 10))
                if info.get("bold"):
                    header[f"Bold{font_id}"] = "T"
                if info.get("italic"):
                    header[f"Italic{font_id}"] = "T"
                if info.get("underline"):
                    header[f"Underline{font_id}"] = "T"
                if info.get("strikeout"):
                    header[f"StrikeOut{font_id}"] = "T"
        else:
            header["FontIdCount"] = "1"
            header["FontName1"] = "Times New Roman"
            header["Size1"] = "10"

        for i, symbol in enumerate(self.symbols):
            _set_header_text_field(
                header,
                f"LibRef{i}",
                symbol.name,
                fallback=_header_symbol_fallback_name(symbol),
            )
            header[f"PartCount{i}"] = str(symbol.part_count + 1)
            if symbol.description:
                _set_header_text_field(
                    header,
                    f"CompDescr{i}",
                    symbol.description,
                )

        return header

    def _sync_file_header_font_table(self) -> None:
        """
        Synchronize FileHeader font fields from the current FontIDManager table.
        """
        if not self.font_manager:
            return
        if not self.file_header:
            self.file_header = {
                "HEADER": "Protel for Windows - Schematic Library Editor Binary File Version 5.0",
            }

        def _is_font_field(key: str) -> bool:
            normalized_key = key.casefold()
            if normalized_key == "fontidcount":
                return True
            prefixes = (
                "fontname",
                "size",
                "bold",
                "italic",
                "underline",
                "strikeout",
                "rotation",
            )
            for prefix in prefixes:
                if normalized_key.startswith(prefix):
                    suffix = normalized_key[len(prefix) :]
                    return suffix.isdigit()
            return False

        for key in list(self.file_header.keys()):
            if _is_font_field(key):
                self.file_header.pop(key, None)

        export_fonts = self.font_manager.get_export_font_table()
        fonts = export_fonts or list(self.font_manager.fonts.values())
        self.file_header["FontIdCount"] = str(len(fonts))
        for font_id, font_data in enumerate(fonts, start=1):
            idx = str(font_id)
            self.file_header[f"FontName{idx}"] = str(
                font_data.get("name", "Times New Roman")
            )
            self.file_header[f"Size{idx}"] = str(int(font_data.get("size", 10)))
            if font_data.get("bold"):
                self.file_header[f"Bold{idx}"] = "T"
            if font_data.get("italic"):
                self.file_header[f"Italic{idx}"] = "T"
            if font_data.get("underline"):
                self.file_header[f"Underline{idx}"] = "T"
            if font_data.get("strikeout"):
                self.file_header[f"StrikeOut{idx}"] = "T"
            rotation = int(font_data.get("rotation", 0))
            if rotation:
                self.file_header[f"Rotation{idx}"] = str(rotation)

    @staticmethod
    def _header_value(header: MutableMapping[str, object], field: str) -> object | None:
        target = field.casefold()
        matches = [value for key, value in header.items() if key.casefold() == target]
        if len(matches) > 1:
            raise SchLibContainerError(
                "duplicate", f"FileHeader contains duplicate {field} fields"
            )
        return matches[0] if matches else None

    @classmethod
    def _header_dynamic_value(
        cls,
        header: MutableMapping[str, object],
        field: str,
    ) -> str | None:
        sidecar = cls._header_value(header, f"%UTF8%{field}")
        value = sidecar if sidecar is not None else cls._header_value(header, field)
        return str(value) if value is not None else None

    def _existing_aliases_by_libref(
        self, header: MutableMapping[str, object]
    ) -> dict[str, tuple[str, ...]]:
        folded_header = {key.casefold(): value for key, value in header.items()}
        raw_count = folded_header.get("compcount")
        if raw_count is None:
            return {}
        count = self._checked_header_index_count(raw_count, "CompCount")
        aliases: dict[str, tuple[str, ...]] = {}
        for symbol_index in range(count):
            libref = self._folded_dynamic_value(folded_header, f"LibRef{symbol_index}")
            if not libref:
                continue
            folded = libref.casefold()
            if folded in aliases:
                raise SchLibContainerError(
                    "duplicate", "FileHeader LibRefs collide case-insensitively"
                )
            aliases[folded] = self._folded_header_aliases(folded_header, symbol_index)
        return aliases

    def _folded_header_aliases(
        self, folded_header: dict[str, object], symbol_index: int
    ) -> tuple[str, ...]:
        raw_count = folded_header.get(f"aliascount{symbol_index}")
        count = (
            self._checked_header_index_count(raw_count, f"AliasCount{symbol_index}")
            if raw_count is not None
            else 0
        )
        return tuple(
            alias
            for alias_index in range(count)
            if (
                alias := self._folded_dynamic_value(
                    folded_header, f"Comp{symbol_index}Alias{alias_index}"
                )
            )
        )

    def _checked_header_index_count(self, raw_count: object, field: str) -> int:
        count = _parse_i32(raw_count, field, stream="FileHeader")
        if count < 0:
            raise SchLibContainerError("malformed", f"FileHeader {field} is negative")
        if count > self._read_limits.max_indexed_items_per_record:
            raise SchLibContainerError(
                "limit", f"FileHeader {field} exceeds the reviewed limit"
            )
        return count

    @staticmethod
    def _folded_dynamic_value(
        folded_record: dict[str, object], field: str
    ) -> str | None:
        value = folded_record.get(f"%utf8%{field}".casefold())
        if value is None:
            value = folded_record.get(field.casefold())
        return str(value) if value is not None else None

    def _symbol_table_signature(self) -> tuple[tuple[str, str, int, str], ...]:
        return tuple(
            (
                symbol.name,
                str(symbol.original_name or symbol.name),
                symbol.part_count,
                symbol.description,
            )
            for symbol in self.symbols
        )

    def _section_key_signature(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (symbol.name, str(symbol.original_name or symbol.name))
            for symbol in self.symbols
        )

    @staticmethod
    def _component_dynamic_value(
        component: MutableMapping[str, object], field: str
    ) -> str:
        sidecar = AltiumSchLib._header_value(component, f"%UTF8%{field}")
        value = (
            sidecar
            if sidecar is not None
            else AltiumSchLib._header_value(component, field)
        )
        return str(value) if value is not None else ""

    def _header_symbol_metadata_is_coherent(self) -> bool:
        if not self.file_header:
            return False
        header = cast(MutableMapping[str, object], self.file_header)
        folded_header = {key.casefold(): value for key, value in header.items()}
        return all(
            self._header_symbol_row_is_coherent(folded_header, index, symbol)
            for index, symbol in enumerate(self.symbols)
        )

    def _header_symbol_row_is_coherent(
        self,
        folded_header: dict[str, object],
        index: int,
        symbol: AltiumSymbol,
    ) -> bool:
        component = cast(MutableMapping[str, object], symbol.component_record or {})
        folded_component = {key.casefold(): value for key, value in component.items()}
        header_part_count = self._coherent_header_part_count(folded_header, index)
        component_part_count = _parse_i32(
            folded_component.get("partcount", "1"),
            "PartCount",
            stream=f"{symbol.name}/Data",
        )
        header_description = (
            self._folded_dynamic_value(folded_header, f"CompDescr{index}") or ""
        )
        header_libref = (
            self._folded_dynamic_value(folded_header, f"LibRef{index}") or ""
        )
        return (
            header_libref,
            header_part_count,
            header_description,
            self._header_aliases(folded_header, index),
        ) == (
            str(symbol.original_name or symbol.name),
            component_part_count,
            symbol.description,
            self._component_aliases(folded_component),
        )

    @staticmethod
    def _coherent_header_part_count(
        folded_header: dict[str, object], index: int
    ) -> int | None:
        raw_count = folded_header.get(f"partcount{index}")
        if raw_count is None:
            return 0
        try:
            return _parse_i32(raw_count, f"PartCount{index}", stream="FileHeader")
        except SchLibContainerError:
            return None

    def _header_aliases(
        self, folded_header: dict[str, object], index: int
    ) -> tuple[str, ...]:
        raw_count = folded_header.get(f"aliascount{index}")
        if raw_count is None:
            return ()
        count = self._checked_header_index_count(raw_count, f"AliasCount{index}")
        return tuple(
            self._folded_dynamic_value(folded_header, f"Comp{index}Alias{alias_index}")
            or ""
            for alias_index in range(count)
        )

    def _component_aliases(
        self, folded_component: dict[str, object]
    ) -> tuple[str, ...]:
        raw_aliases = self._folded_dynamic_value(folded_component, "AliasList") or ""
        alias_count = raw_aliases.count(",") + 1 if raw_aliases else 0
        if alias_count > self._read_limits.max_indexed_items_per_record:
            raise SchLibContainerError(
                "limit", "Component AliasList exceeds the reviewed limit"
            )
        return tuple(alias for alias in raw_aliases.split(",") if alias)

    def _sync_file_header_symbol_table(self) -> None:
        if not self.file_header:
            self.file_header = self._synthesize_file_header()
        if not self.file_header:
            raise SchLibContainerError("malformed", "unable to build FileHeader")
        header = cast(MutableMapping[str, object], self.file_header)
        aliases_by_libref = self._header_aliases_for_rebuild(header)
        self._remove_header_symbol_fields(header)
        header["CompCount"] = str(len(self.symbols))
        seen_librefs: set[str] = set()
        for symbol_index, symbol in enumerate(self.symbols):
            libref = str(symbol.original_name or symbol.name)
            folded = libref.casefold()
            if not libref or folded in seen_librefs:
                raise SchLibContainerError(
                    "duplicate", "symbol LibRefs must be nonempty and unique"
                )
            seen_librefs.add(folded)
            self._write_header_symbol_row(
                header,
                symbol_index,
                symbol,
                aliases_by_libref.get(folded, ()),
            )

    def _header_aliases_for_rebuild(
        self, header: MutableMapping[str, object]
    ) -> dict[str, tuple[str, ...]]:
        if self._header_table_coherent:
            return self._existing_aliases_by_libref(header)
        return {
            str(symbol.original_name or symbol.name).casefold(): (
                self._component_aliases(
                    {
                        key.casefold(): value
                        for key, value in cast(
                            MutableMapping[str, object],
                            symbol.component_record or {},
                        ).items()
                    }
                )
            )
            for symbol in self.symbols
        }

    @staticmethod
    def _remove_header_symbol_fields(header: MutableMapping[str, object]) -> None:
        indexed = re.compile(
            r"(?:%UTF8%)?(?:LibRef|PartCount|CompDescr|AliasCount)[0-9]+"
            r"|(?:%UTF8%)?Comp[0-9]+Alias[0-9]+",
            re.IGNORECASE,
        )
        for key in tuple(header):
            if key.casefold() == "compcount" or indexed.fullmatch(key):
                header.pop(key, None)

    @classmethod
    def _write_header_symbol_row(
        cls,
        header: MutableMapping[str, object],
        symbol_index: int,
        symbol: AltiumSymbol,
        aliases: tuple[str, ...],
    ) -> None:
        libref = str(symbol._header_display_name or symbol.original_name or symbol.name)
        _set_header_text_field(
            cast(dict[str, str], header),
            f"LibRef{symbol_index}",
            libref,
            fallback=_header_symbol_fallback_name(symbol),
        )
        component = cast(MutableMapping[str, object], symbol.component_record or {})
        stored_part_count = _parse_i32(
            cls._header_value(component, "PartCount") or str(symbol.part_count + 1),
            "PartCount",
            stream=f"{symbol.name}/Data",
        )
        header[f"PartCount{symbol_index}"] = str(stored_part_count)
        description = cls._component_dynamic_value(component, "ComponentDescription")
        if description:
            _set_header_text_field(
                cast(dict[str, str], header),
                f"CompDescr{symbol_index}",
                description,
            )
        if aliases:
            header[f"AliasCount{symbol_index}"] = str(len(aliases))
        for alias_index, alias in enumerate(aliases):
            _set_header_text_field(
                cast(dict[str, str], header),
                f"Comp{symbol_index}Alias{alias_index}",
                alias,
            )

    def _build_pintextdata_stream_for_symbol(
        self,
        symbol: AltiumSymbol,
        *,
        original_stream: bytes | None = None,
    ) -> bytes | None:
        """
        Build PinTextData stream bytes from current OOP PIN settings for one symbol.

        Returns None when no pins in this symbol require PinTextData customization.
        """

        def _output_font_id(internal_font_id: int) -> int:
            normalized = internal_font_id
            if normalized <= 0:
                normalized = 1
            if self.font_manager is not None:
                if normalized > self.font_manager.font_count:
                    normalized = 1
                normalized = self.font_manager.translate_out(normalized)
            if normalized < 1 or normalized > 1000:
                return 1
            return normalized

        from .altium_pintextdata_modifier import (
            PinTextData,
            PinTextDataModifier,
            PinTextPosition,
        )
        from .altium_sch_enums import PinItemMode, PinTextAnchor, PinTextOrientation

        self._ensure_font_manager()

        def _resolve_font_id(settings: object) -> int:
            font_id = getattr(settings, "font_id", None)
            if font_id is not None:
                return _output_font_id(int(font_id))

            font_name = getattr(settings, "font_name", None) or "Arial"
            font_size = getattr(settings, "font_size", None)
            resolved_font_id = (
                self.font_manager.get_or_create_font(
                    font_name=font_name,
                    font_size=int(font_size) if font_size is not None else 10,
                    bold=bool(getattr(settings, "font_bold", False)),
                    italic=bool(getattr(settings, "font_italic", False)),
                )
                if self.font_manager
                else 1
            )
            return _output_font_id(resolved_font_id)

        def _margin_mils(pin: AltiumSchPin, *, for_name: bool) -> float | None:
            if for_name:
                cached = getattr(pin, "_name_margin_mils", None)
                settings = pin.name_settings
            else:
                cached = getattr(pin, "_designator_margin_mils", None)
                settings = pin.designator_settings
            if cached is not None:
                return float(cached)
            if settings.position_margin is None:
                return None
            return float(
                settings.position_margin * 10
                + (settings.position_margin_frac or 0) / 10000.0
            )

        entries: list[tuple[str, PinTextData]] = []
        for pin_index, pin in enumerate(symbol.pins):
            name_settings = pin.name_settings
            des_settings = pin.designator_settings

            name_position: PinTextPosition | None = None
            if name_settings.position_mode == PinItemMode.CUSTOM:
                margin_mils = _margin_mils(pin, for_name=True) or 0.0
                name_position = PinTextPosition(
                    margin_mils=margin_mils,
                    orientation=PinTextOrientation(
                        int(name_settings.rotation.value) * 90
                    ),
                    reference_to_component=(
                        name_settings.rotation_anchor == PinTextAnchor.COMPONENT
                    ),
                )

            designator_position: PinTextPosition | None = None
            if des_settings.position_mode == PinItemMode.CUSTOM:
                margin_mils = _margin_mils(pin, for_name=False) or 0.0
                designator_position = PinTextPosition(
                    margin_mils=margin_mils,
                    orientation=PinTextOrientation(
                        int(des_settings.rotation.value) * 90
                    ),
                    reference_to_component=(
                        des_settings.rotation_anchor == PinTextAnchor.COMPONENT
                    ),
                )

            name_font_id: int | None = None
            name_color: int | None = None
            if name_settings.font_mode == PinItemMode.CUSTOM:
                name_font_id = _resolve_font_id(name_settings)
                name_color = (
                    int(name_settings.color)
                    if name_settings.color is not None
                    else int(getattr(pin, "color", 0))
                )

            designator_font_id: int | None = None
            designator_color: int | None = None
            if des_settings.font_mode == PinItemMode.CUSTOM:
                designator_font_id = _resolve_font_id(des_settings)
                designator_color = (
                    int(des_settings.color)
                    if des_settings.color is not None
                    else int(getattr(pin, "color", 0))
                )

            has_custom_data = (
                name_position is not None
                or designator_position is not None
                or name_font_id is not None
                or designator_font_id is not None
                or name_color is not None
                or designator_color is not None
            )
            if not has_custom_data:
                continue

            pin_data = PinTextData(
                format_type="AUTO",
                raw_data=bytearray(),
                name_font_id=name_font_id,
                name_color=name_color,
                designator_font_id=designator_font_id,
                designator_color=designator_color,
                position=name_position,
                name_position=name_position,
                designator_position=designator_position,
            )
            entries.append((str(pin_index), pin_data))

        if not entries:
            return None

        modifier = PinTextDataModifier()
        modifier.entries = entries
        return modifier.serialize(original_data=original_stream)

    def _build_pinfrac_stream_for_symbol(self, symbol: AltiumSymbol) -> bytes | None:
        """
        Build PinFrac stream bytes from current OOP PIN settings for one symbol.
        """
        return build_pinfrac_stream_for_pins(
            [cast(AltiumSchPin, pin) for pin in symbol.pins]
        )

    def _copy_original_ole_structure(
        self,
        ole_writer: AltiumOleWriter,
        *,
        sync_pin_text_data: bool,
    ) -> dict[str, bytes]:
        original_pin_aux_streams: dict[str, bytes] = {}
        if not self._source_streams:
            return original_pin_aux_streams
        live_keys = {symbol.name.casefold() for symbol in self.symbols}
        if len(live_keys) != len(self.symbols):
            raise SchLibContainerError(
                "duplicate", "live symbol storage names collide case-insensitively"
            )
        source_keys = {name.casefold() for name in self._source_symbol_keys}
        self._copy_source_entries(ole_writer, source_keys, live_keys)
        if not sync_pin_text_data:
            return original_pin_aux_streams
        for symbol in self.symbols:
            self._capture_original_pin_aux_streams(
                ole_writer,
                symbol,
                original_pin_aux_streams,
            )
        return original_pin_aux_streams

    def _copy_source_entries(
        self,
        ole_writer: AltiumOleWriter,
        source_keys: set[str],
        live_keys: set[str],
    ) -> None:
        for storage in self._source_storages:
            if not self._belongs_to_removed_symbol(storage, source_keys, live_keys):
                ole_writer.addEntry(storage, storage=True)
        for path, payload in self._source_streams.items():
            if self._belongs_to_removed_symbol(path, source_keys, live_keys):
                continue
            ole_writer.add_stream(path, payload)

    @staticmethod
    def _belongs_to_removed_symbol(
        path: str, source_keys: set[str], live_keys: set[str]
    ) -> bool:
        top = path.split("/", 1)[0].casefold()
        return top in source_keys and top not in live_keys

    def _capture_original_pin_aux_streams(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        original_streams: dict[str, bytes],
    ) -> None:
        for stream_name in ("PinTextData", "PinVerticalMarginData"):
            canonical_path = f"{symbol.name}/{stream_name}"
            stream_path = self._source_stream_paths_by_fold.get(
                canonical_path.casefold()
            )
            if stream_path is None:
                continue
            original_streams[canonical_path] = self._source_streams[stream_path]
            # A copied stream must not survive when synchronization determines
            # that the symbol no longer has the corresponding customization.
            ole_writer._remove_stream(stream_path)

    def _records_for_symbol_save(
        self,
        symbol: AltiumSymbol,
        *,
        debug: bool,
    ) -> tuple[list[dict[str, Any]], bool]:
        is_oop_built_symbol = bool(symbol.objects) and not bool(symbol.raw_records)
        if symbol.raw_records:
            membership_rebuilt = symbol._prepare_managed_membership_rebuild()
            symbol._reconcile_data_membership()
            pins_synced = symbol.sync_pins_to_raw_records()
            graphics_synced = symbol.sync_graphics_to_raw_records()
            if membership_rebuilt:
                symbol._clear_data_additional_owner_flags()
            if debug and pins_synced > 0:
                log.info(f"  Synced {pins_synced} PINs for {symbol.name}")
            if debug and graphics_synced > 0:
                log.info(f"  Synced {graphics_synced} graphics for {symbol.name}")
            return symbol.raw_records, is_oop_built_symbol

        records_to_write = symbol.synthesize_raw_records()
        if debug:
            source = "OOP objects" if symbol.objects else "empty symbol"
            log.info(
                f"  Synthesized {len(records_to_write)} records for {symbol.name} from {source}"
            )
        return records_to_write, is_oop_built_symbol

    def _write_pintextdata_stream(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        pintextdata: bytes | None,
        *,
        debug: bool,
        action: str,
    ) -> None:
        if pintextdata is None:
            return
        pintext_path = f"{symbol.name}/PinTextData"
        ole_writer.editEntry(pintext_path, data=pintextdata)
        if debug:
            log.info(
                f"  {action} PinTextData for {symbol.name}: {len(pintextdata)} bytes"
            )

    def _write_pin_vertical_margin_stream(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        stream_data: bytes | None,
        *,
        debug: bool,
        action: str,
    ) -> None:
        if stream_data is None:
            return
        stream_path = f"{symbol.name}/PinVerticalMarginData"
        ole_writer.editEntry(stream_path, data=stream_data)
        if debug:
            log.info(
                f"  {action} PinVerticalMarginData for {symbol.name}: "
                f"{len(stream_data)} bytes"
            )

    def _write_oop_symbol_aux_streams(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        *,
        debug: bool,
    ) -> None:
        self._write_pintextdata_stream(
            ole_writer,
            symbol,
            self._build_pintextdata_stream_for_symbol(symbol),
            debug=debug,
            action="Wrote",
        )
        self._write_pin_vertical_margin_stream(
            ole_writer,
            symbol,
            _encode_pin_vertical_margin_stream(
                [cast(AltiumSchPin, pin) for pin in symbol.pins]
            ),
            debug=debug,
            action="Wrote",
        )

        pinfrac = self._build_pinfrac_stream_for_symbol(symbol)
        if pinfrac is None:
            return
        pinfrac_path = f"{symbol.name}/PinFrac"
        ole_writer.editEntry(pinfrac_path, data=pinfrac)
        if debug:
            log.info(f"  Wrote PinFrac for {symbol.name}: {len(pinfrac)} bytes")

    def _write_synced_pin_aux_streams(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        original_pin_aux_streams: dict[str, bytes],
        *,
        debug: bool,
    ) -> None:
        pintext_path = f"{symbol.name}/PinTextData"
        pintextdata = self._build_pintextdata_stream_for_symbol(
            symbol,
            original_stream=original_pin_aux_streams.get(pintext_path),
        )
        self._write_pintextdata_stream(
            ole_writer,
            symbol,
            pintextdata,
            debug=debug,
            action="Synced",
        )
        vertical_path = f"{symbol.name}/PinVerticalMarginData"
        self._write_pin_vertical_margin_stream(
            ole_writer,
            symbol,
            _encode_pin_vertical_margin_stream(
                [cast(AltiumSchPin, pin) for pin in symbol.pins],
                original_stream=original_pin_aux_streams.get(vertical_path),
            ),
            debug=debug,
            action="Synced",
        )

    @staticmethod
    def _write_preserved_symbol_streams(
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        *,
        skip_synced_pin_streams: bool,
    ) -> None:
        for stream_name, stream_data in symbol._original_streams.items():
            if skip_synced_pin_streams and stream_name.casefold() in {
                "pintextdata",
                "pinverticalmargindata",
            }:
                continue
            stream_path = f"{symbol.name}/{stream_name}"
            ole_writer.editEntry(stream_path, data=stream_data)

    def _write_symbol_to_ole(
        self,
        ole_writer: AltiumOleWriter,
        symbol: AltiumSymbol,
        original_pin_aux_streams: dict[str, bytes],
        *,
        debug: bool,
        sync_pin_text_data: bool,
    ) -> int:
        stream_path = f"{symbol.name}/Data"
        records_to_write, is_oop_built_symbol = self._records_for_symbol_save(
            symbol,
            debug=debug,
        )
        serialized = create_stream_from_records(records_to_write)
        ole_writer.editEntry(stream_path, data=serialized)
        if debug:
            log.info(
                f"  Serialized {symbol.name}: {len(records_to_write)} records -> {len(serialized)} bytes"
            )

        if is_oop_built_symbol:
            self._write_oop_symbol_aux_streams(ole_writer, symbol, debug=debug)
        elif sync_pin_text_data:
            self._write_synced_pin_aux_streams(
                ole_writer,
                symbol,
                original_pin_aux_streams,
                debug=debug,
            )

        self._write_preserved_symbol_streams(
            ole_writer,
            symbol,
            skip_synced_pin_streams=sync_pin_text_data,
        )
        if symbol._sync_additional_to_raw_records():
            stream_name = next(
                (
                    name
                    for name in symbol._original_streams
                    if name.casefold() == "additional"
                ),
                "Additional",
            )
            additional_path = f"{symbol.name}/{stream_name}"
            ole_writer.editEntry(
                additional_path,
                data=create_stream_from_records(
                    [
                        *symbol._additional_raw_records,
                        symbol._additional_terminal_record,
                    ]
                ),
            )
        return len(records_to_write)

    def _write_file_header_to_ole(
        self,
        ole_writer: AltiumOleWriter,
        *,
        sync_pin_text_data: bool,
        minimal: bool,
        serialized_record_counts: list[int] | None,
    ) -> None:
        if not self.file_header:
            self.file_header = self._synthesize_file_header(minimal=minimal)
        if not self.file_header:
            return
        if sync_pin_text_data:
            self._sync_file_header_font_table()
        if (
            not self._header_table_coherent
            or self._symbol_table_signature() != self._parsed_symbol_signature
        ):
            self._sync_file_header_symbol_table()
        file_header = self.file_header
        from .altium_sch_stream_sync import _derive_schlib_weight

        if serialized_record_counts is None:
            raise ValueError("serialized SchLib Data counts are required at save time")
        weight_key = next(
            (key for key in file_header if key.casefold() == "weight"), "Weight"
        )
        file_header[weight_key] = str(_derive_schlib_weight(serialized_record_counts))
        serialized = create_stream_from_records([file_header])
        ole_writer.editEntry("FileHeader", data=serialized)

    def _write_lib_additional_header_to_ole(self, ole_writer: AltiumOleWriter) -> None:
        modeled_count = self._modeled_additional_count()
        header = self._lib_additional_header_for_save(modeled_count)
        if header is None:
            return
        stream = self._source_stream_paths_by_fold.get("libadditional", "LibAdditional")
        ole_writer.editEntry(stream, data=create_stream_from_records([header]))
        self._lib_additional_header = header

    def _modeled_additional_count(self) -> int:
        return sum(
            1
            for symbol in self.symbols
            for record in symbol._additional_raw_records
            if self._additional_record_type(record, f"{symbol.name}/Additional")
            not in _SCHLIB_SKIPPED_ADDITIONAL_RECORD_IDS
        )

    def _lib_additional_header_for_save(
        self, modeled_count: int
    ) -> dict[str, object] | None:
        if self._lib_additional_header is None and modeled_count == 0:
            return None
        header = dict(self._lib_additional_header or {})
        if self._header_value(header, "RECORD") is None:
            header["RECORD"] = "0"
        if self._header_value(header, "HEADER") is None:
            profile = self._header_value(
                cast(MutableMapping[str, object], self.file_header or {}), "HEADER"
            )
            header["HEADER"] = str(profile or "")
        weight_key = next(
            (key for key in header if key.casefold() == "weight"), "Weight"
        )
        header[weight_key] = str(modeled_count)
        return header

    def _write_embedded_images_to_ole(self, ole_writer: AltiumOleWriter) -> None:
        entries = (
            SchEmbeddedImageEntry(
                filename=image.filename,
                orientation=int(image.orientation),
                data=image.image_data,
            )
            for symbol in self.symbols
            for image in symbol.images
            if image.embed_image and image.filename and image.image_data
        )
        synced_images = build_embedded_image_storage(entries)
        self.embedded_images = synced_images
        raw_by_name: dict[str, tuple[bytes, bytes]] = {}
        for name, entry in self._raw_storage_entries.items():
            raw_by_name.setdefault(name.lower(), entry)
        self._raw_storage_entries = {
            name: raw_by_name[name.lower()]
            for name in synced_images
            if name.lower() in raw_by_name
        }
        if self.embedded_images:
            storage_data = create_storage_stream(
                self.embedded_images,
                raw_entries=self._raw_storage_entries,
            )
            ole_writer.editEntry("Storage", data=storage_data)
        else:
            ole_writer._remove_stream("Storage")

    def _validate_symbol_storage_plan(self) -> None:
        if len(self.symbols) > self._read_limits.max_indexed_items_per_record:
            raise SchLibContainerError(
                "limit", "symbol count exceeds the reviewed indexed-item limit"
            )
        seen: set[str] = set()
        librefs: set[str] = set()
        for symbol in self.symbols:
            folded = self._validate_symbol_storage(symbol)
            if folded in seen:
                raise SchLibContainerError(
                    "duplicate", "symbol storage keys collide case-insensitively"
                )
            seen.add(folded)
            original_name = str(symbol.original_name or symbol.name).casefold()
            if original_name in librefs:
                raise SchLibContainerError(
                    "duplicate", "symbol library references collide"
                )
            librefs.add(original_name)

    def _validate_symbol_storage(self, symbol: AltiumSymbol) -> str:
        storage_key = symbol.name
        if (
            not storage_key
            or any(character in storage_key for character in "\\/:!")
            or "\x00" in storage_key
            or len(storage_key.encode("utf-16-le")) // 2 > 31
        ):
            raise SchLibContainerError(
                "malformed", f"invalid OLE symbol storage key {storage_key!r}"
            )
        folded = storage_key.casefold()
        return folded

    def _desired_section_key_map(self) -> dict[str, str]:
        return {
            str(symbol.original_name or symbol.name): symbol.name
            for symbol in self.symbols
            if str(symbol.original_name or symbol.name) != symbol.name
            and not _symbol_uses_implicit_storage_mapping(symbol)
        }

    def _sync_section_keys_to_ole(self, ole_writer: AltiumOleWriter) -> None:
        if (
            self._section_keys_raw is not None
            and self._section_key_signature() == self._section_keys_symbol_signature
        ):
            return
        desired = self._desired_section_key_map()
        if desired == self._section_key_map and self._section_keys_raw is not None:
            return
        if not desired:
            ole_writer._remove_stream("SectionKeys")
            self._section_keys_raw = None
            self._section_key_map = {}
            return

        record: dict[str, str] = {"KeyCount": str(len(desired))}
        for index, (libref, storage_key) in enumerate(desired.items()):
            _set_header_text_field(record, f"LibRef{index}", libref)
            _set_header_text_field(record, f"SectionKey{index}", storage_key)
        payload = create_stream_from_records([record])
        ole_writer.editEntry("SectionKeys", data=payload)
        self._section_keys_raw = payload
        self._section_key_map = desired

    def _validate_staged_writer(self, ole_writer: AltiumOleWriter) -> None:
        limits = self._read_limits.validate()
        streams = ole_writer._streams
        storages = ole_writer._storages
        self._build_stream_path_index(streams)
        if len(streams) > limits.max_streams_per_container:
            raise SchLibContainerError("limit", "output has too many streams")
        directory_entries = 1 + len(streams) + len(storages)
        if directory_entries > limits.max_ole_directory_entries:
            raise SchLibContainerError("limit", "output has too many directory entries")
        aggregate = 0
        for path, payload in streams.items():
            if len(payload) > limits.max_stream_bytes:
                raise SchLibContainerError(
                    "limit", "output stream exceeds stream limit", stream=path
                )
            aggregate += len(payload)
            if aggregate > limits.max_container_bytes:
                raise SchLibContainerError(
                    "limit", "output aggregate stream bytes exceed container limit"
                )
        self._validate_staged_instruction_streams(streams, limits)

    def _validate_staged_instruction_streams(
        self,
        streams: dict[str, bytes],
        limits: _SchLibReadLimits,
    ) -> None:
        """Apply read-side framing and field budgets to staged managed streams."""
        budget = _SchLibBudget(limits)
        self._load_file_header(streams, budget)
        lib_additional = self._load_lib_additional_header(streams, budget)
        _parse_section_keys(streams.get("SectionKeys"), budget)
        staged_paths_by_fold = {path.casefold(): path for path in streams}
        for symbol in self.symbols:
            stream = f"{symbol.name}/Data"
            payload = streams.get(stream)
            if payload is None:
                raise SchLibContainerError(
                    "missing", "staged symbol Data stream is missing", stream=stream
                )
            _parse_instruction_stream(payload, stream, budget)
            if lib_additional is None:
                continue
            additional_path = staged_paths_by_fold.get(
                f"{symbol.name}/Additional".casefold()
            )
            if additional_path is not None:
                additional = _parse_instruction_stream(
                    streams[additional_path], additional_path, budget
                )
                self._additional_data_records(additional, additional_path)

    def _stage_schlib_writer(
        self,
        *,
        debug: bool,
        sync_pin_text_data: bool,
        minimal: bool,
    ) -> AltiumOleWriter:
        self._validate_symbol_storage_plan()
        self._ensure_font_manager()
        self._bind_all_symbols_to_context()
        ole_writer = AltiumOleWriter()
        original_pin_aux_streams = self._copy_original_ole_structure(
            ole_writer,
            sync_pin_text_data=sync_pin_text_data,
        )
        self._sync_section_keys_to_ole(ole_writer)
        serialized_record_counts = [
            self._write_symbol_to_ole(
                ole_writer,
                symbol,
                original_pin_aux_streams,
                debug=debug,
                sync_pin_text_data=sync_pin_text_data,
            )
            for symbol in self.symbols
        ]
        self._write_file_header_to_ole(
            ole_writer,
            sync_pin_text_data=sync_pin_text_data,
            minimal=minimal,
            serialized_record_counts=serialized_record_counts,
        )
        self._write_lib_additional_header_to_ole(ole_writer)
        self._write_embedded_images_to_ole(ole_writer)
        self._validate_staged_writer(ole_writer)
        self._refresh_staged_symbol_streams(ole_writer)
        return ole_writer

    def _refresh_staged_symbol_streams(self, ole_writer: AltiumOleWriter) -> None:
        for symbol in self.symbols:
            prefix = f"{symbol.name}/"
            symbol._original_streams = {
                relative: payload
                for path, payload in ole_writer._streams.items()
                if path.startswith(prefix)
                and "/" not in (relative := path[len(prefix) :])
                and relative != "Data"
            }

    def _write_staged_container(
        self,
        ole_writer: AltiumOleWriter,
        filepath: Path,
    ) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{filepath.name}.",
            suffix=".tmp",
            dir=filepath.parent,
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            ole_writer.write(temporary)
            size = temporary.stat().st_size
            if size > self._read_limits.max_container_bytes:
                raise SchLibContainerError(
                    "limit", "output OLE container exceeds container byte limit"
                )
            os.replace(temporary, filepath)
        finally:
            temporary.unlink(missing_ok=True)

    def _commit_staged_save(
        self,
        staged: AltiumSchLib,
        ole_writer: AltiumOleWriter,
    ) -> None:
        self.file_header = staged.file_header
        self._lib_additional_header = staged._lib_additional_header
        self.font_manager = staged.font_manager
        self.embedded_images = staged.embedded_images
        self._raw_storage_entries = staged._raw_storage_entries
        self._source_streams = dict(ole_writer._streams)
        self._source_stream_paths_by_fold = {
            path.casefold(): path for path in self._source_streams
        }
        self._source_storages = tuple(sorted(ole_writer._storages))
        self._source_symbol_keys = tuple(symbol.name for symbol in self.symbols)
        self._section_keys_raw = self._source_streams.get("SectionKeys")
        self._section_key_map = dict(staged._section_key_map)
        self._section_keys_symbol_signature = self._section_key_signature()
        self._header_table_coherent = True
        self._parsed_symbol_signature = self._symbol_table_signature()
        for symbol, staged_symbol in zip(self.symbols, staged.symbols, strict=True):
            symbol.raw_records = staged_symbol.raw_records
            if symbol.raw_records:
                symbol.component_record = symbol.raw_records[0]
            symbol._additional_raw_records = staged_symbol._additional_raw_records
            symbol._additional_terminal_record = (
                staged_symbol._additional_terminal_record
            )
            symbol._original_streams = staged_symbol._original_streams
            symbol._normalized_owner_indices = staged_symbol._normalized_owner_indices
            self._commit_symbol_data_positions(symbol, staged_symbol)

    @staticmethod
    def _commit_symbol_data_positions(
        symbol: AltiumSymbol, staged_symbol: AltiumSymbol
    ) -> None:
        """Adopt staged row positions only after the container write succeeds."""
        AltiumSchLib._copy_staged_object_positions(symbol, staged_symbol)
        AltiumSchLib._remap_committed_pin_parameters(symbol, staged_symbol)
        AltiumSchLib._adopt_committed_object_order(symbol)
        symbol._data_membership_dirty = staged_symbol._data_membership_dirty
        symbol._data_record_index_remap = {}
        symbol._additional_membership_dirty = staged_symbol._additional_membership_dirty

    @staticmethod
    def _copy_staged_object_positions(
        symbol: AltiumSymbol, staged_symbol: AltiumSymbol
    ) -> None:
        for source, staged_source in zip(
            symbol._objects, staged_symbol._objects, strict=True
        ):
            for attribute in (
                "_record_index",
                "_source_stream",
                "_additional_record_index",
                "_additional_stream_record_index",
                "_managed_unattached",
            ):
                AltiumSchLib._copy_optional_object_attribute(
                    source, staged_source, attribute
                )
            for attribute in (
                "owner_index",
                "owner_index_additional_list",
                "index_in_sheet",
            ):
                AltiumSchLib._copy_shared_object_attribute(
                    source, staged_source, attribute
                )
            if getattr(source, "_source_stream", "Data") != "Additional":
                symbol._clear_source_additional_owner_flag(source)

    @staticmethod
    def _copy_optional_object_attribute(
        target: object, source: object, attribute: str
    ) -> None:
        if hasattr(source, attribute):
            setattr(_as_dynamic(target), attribute, getattr(source, attribute))
        elif hasattr(target, attribute):
            delattr(target, attribute)

    @staticmethod
    def _copy_shared_object_attribute(
        target: object, source: object, attribute: str
    ) -> None:
        if hasattr(target, attribute) and hasattr(source, attribute):
            setattr(_as_dynamic(target), attribute, getattr(source, attribute))

    @staticmethod
    def _adopt_committed_object_order(symbol: AltiumSymbol) -> None:
        """Align the live structural store with committed physical row order."""
        ordered = sorted(
            symbol._objects,
            key=lambda source: (
                not isinstance((index := getattr(source, "_record_index", None)), int),
                index if isinstance(index, int) else 0,
            ),
        )
        symbol._objects.clear()
        symbol._objects.extend(ordered)

    @staticmethod
    def _remap_committed_pin_parameters(
        symbol: AltiumSymbol, staged_symbol: AltiumSymbol
    ) -> None:
        remap = staged_symbol._data_record_index_remap
        if not remap:
            return
        symbol._pin_parameters = {
            remap[owner_index]: parameters
            for owner_index, parameters in symbol._pin_parameters.items()
            if owner_index in remap
        }
        for owner_index, parameters in symbol._pin_parameters.items():
            staged_parameters = staged_symbol._pin_parameters[owner_index]
            for parameter, staged_parameter in zip(
                parameters, staged_parameters, strict=True
            ):
                staged_index = getattr(staged_parameter, "_record_index", None)
                if isinstance(staged_index, int):
                    _as_dynamic(parameter)._record_index = staged_index
                parameter.owner_index = staged_parameter.owner_index

    def _index_source_stream_paths(self) -> None:
        self._source_stream_paths_by_fold = self._build_stream_path_index(
            self._source_streams
        )

    @staticmethod
    def _build_stream_path_index(streams: Mapping[str, bytes]) -> dict[str, str]:
        paths_by_fold: dict[str, str] = {}
        for path in streams:
            folded = path.casefold()
            if folded in paths_by_fold:
                raise SchLibContainerError(
                    "duplicate",
                    f"source stream paths collide case-insensitively for {path!r}",
                )
            paths_by_fold[folded] = path
        return paths_by_fold

    def to_schlib(
        self,
        filepath: Path,
        debug: bool = False,
        *,
        sync_pin_text_data: bool = False,
        minimal: bool = False,
    ) -> None:
        """
        Serialize SchLib back to file.

        This is the primary API method for saving SchLib files.
        Use this method for all file writing operations.

        IMPORTANT: This method ALWAYS creates a new OLE file from scratch,
        using AltiumOleWriter. It does NOT patch the original file in-place.
        This allows handling stream size changes (e.g., adding/removing records).

        Args:
            filepath: Output path for .SchLib file
            debug: Enable debug output
            sync_pin_text_data: Regenerate each symbol's PinTextData and
                PinVerticalMarginData streams from OOP pin settings, and
                synchronize the FileHeader font table.
        """
        filepath = Path(filepath)
        log.info(f"Saving SchLib to: {filepath}")
        try:
            staged = deepcopy(self)
            ole_writer = staged._stage_schlib_writer(
                debug=debug,
                sync_pin_text_data=sync_pin_text_data,
                minimal=minimal,
            )
            staged._write_staged_container(ole_writer, filepath)
            self._commit_staged_save(staged, ole_writer)

            log.info(f"  Saved successfully: {len(self.symbols)} symbols")

        except Exception as e:
            log.error(f"  Error saving {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            raise

    def save(
        self,
        filepath: Path | str,
        debug: bool = False,
        *,
        sync_pin_text_data: bool = False,
        minimal: bool = False,
    ) -> None:
        """
        Save to binary SchLib format.

        This is the canonical public write path. Prefer `save()` over
        format-specific helpers such as `to_schlib()`.

                Args:
                    filepath: Output file path.
                    debug: Enable debug output.
                    sync_pin_text_data: Rebuild PinTextData and
                        PinVerticalMarginData streams from OOP pin objects.
        """
        self.to_schlib(
            filepath=Path(filepath),
            debug=debug,
            sync_pin_text_data=sync_pin_text_data,
            minimal=minimal,
        )

    def add_symbol(
        self,
        name: str,
        description: str = "",
        *,
        original_name: str | None = None,
    ) -> AltiumSymbol:
        """
        Add a new empty symbol to this library.

                Args:
                    name: Symbol name (must be unique within the library).
                    description: Optional component description.

                Returns:
                    The new AltiumSymbol, ready for adding objects.
        """
        resolved_original_name = name if original_name is None else original_name
        self._validate_new_symbol_names(name, resolved_original_name)
        symbol = AltiumSymbol(name, original_name=resolved_original_name)
        symbol.description = description
        symbol._bind_to_schematic_library(self)
        symbol._fix_symbol_unique_id_if_required()
        symbol._set_unique_id_locked(True)
        self._symbols.append(symbol)
        return symbol

    def _validate_new_symbol_names(self, name: str, original_name: str) -> None:
        self._validate_new_storage_name(name)
        if not isinstance(original_name, str) or not original_name:
            raise ValueError("original_name must be a non-empty string")
        if self._symbol_name_is_used(name):
            raise ValueError(f"symbol name {name!r} already exists in this library")
        if self._library_reference_is_used(original_name):
            raise ValueError(
                f"symbol library reference {original_name!r} already exists"
            )

    @staticmethod
    def _validate_new_storage_name(name: str) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("symbol name must be a non-empty string")
        if any(character in name for character in "\\/:!") or "\x00" in name:
            raise ValueError("symbol name must be a top-level OLE storage key")
        if len(name.encode("utf-16-le")) // 2 > 31:
            raise ValueError("symbol name exceeds the 31-character OLE storage limit")

    def _symbol_name_is_used(self, name: str) -> bool:
        return any(symbol.name.casefold() == name.casefold() for symbol in self.symbols)

    def _library_reference_is_used(self, original_name: str) -> bool:
        return any(
            str(symbol.original_name or symbol.name).casefold()
            == original_name.casefold()
            for symbol in self.symbols
        )

    def remove_symbol(self, symbol_or_name: AltiumSymbol | str) -> bool:
        """Remove one symbol and release its library binding and identity locks."""
        symbol = (
            symbol_or_name
            if isinstance(symbol_or_name, AltiumSymbol)
            else self.get_symbol(symbol_or_name)
        )
        if symbol is None or not any(
            candidate is symbol for candidate in self._symbols
        ):
            return False

        self._symbols.remove(symbol)
        symbol._schematic_binding_context = None
        for record in symbol._owned_identity_records():
            unlock = getattr(record, "_set_unique_id_locked", None)
            if callable(unlock):
                unlock(False)
            if hasattr(record, "_bound_schematic_context"):
                _as_dynamic(record)._bound_schematic_context = None
        return True

    def get_symbol(self, name: str) -> AltiumSymbol | None:
        """
        Get a symbol by name.
        """
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        return None

    def _symbol_asset_summaries(self) -> tuple[AltiumAssetSummary, ...]:
        """
        Return extractable symbol summaries in library order.
        """
        source_path = str(self.filepath) if self.filepath is not None else None
        source_instance_id = source_instance_id_for(self, source_path)
        summaries: list[AltiumAssetSummary] = []
        for index, symbol in enumerate(self.symbols):
            name = str(getattr(symbol, "name", "") or f"symbol_{index:03d}")
            kind = "sch_symbol"
            summaries.append(
                AltiumAssetSummary(
                    ref=AltiumAssetRef(
                        source_kind="schlib",
                        source_path=source_path,
                        kind=kind,
                        key=semantic_asset_key(kind, name, index),
                        index=index,
                        name=name,
                        source_instance_id=source_instance_id,
                    ),
                    kind=kind,
                    name=name,
                    extraction_filename=f"{_sanitize_filename(name)}.SchLib",
                    native_extension="SchLib",
                    can_extract=True,
                    payload_available=False,
                    details=SchSymbolAssetDetails(
                        display_name=name,
                        safe_name=_sanitize_filename(name),
                        component_count=0,
                        lib_reference=name,
                        original_name=getattr(symbol, "original_name", name),
                        description=getattr(symbol, "description", ""),
                        pin_count=len(getattr(symbol, "pins", []) or []),
                        object_count=len(getattr(symbol, "objects", []) or []) + 1,
                        part_count=getattr(symbol, "part_count", None),
                    ),
                )
            )
        return tuple(summaries)

    def asset_inventory(self, *, include_hashes: bool = False) -> AltiumAssetInventory:
        """
        Return extractable asset inventory for this SchLib.
        """
        _ = include_hashes
        source_path = str(self.filepath) if self.filepath is not None else None
        return AltiumAssetInventory(
            source_kind="schlib",
            source_path=source_path,
            assets=self._symbol_asset_summaries(),
        )

    def extract_symbol(self, ref_or_name_or_index: object) -> "AltiumSchLib":
        """
        Extract one symbol as a single-symbol SchLib.
        """
        summaries = self._symbol_asset_summaries()
        index = selected_asset_index(
            ref_or_name_or_index,
            summaries=summaries,
            expected_source_kind="schlib",
            expected_kind="sch_symbol",
        )
        symbol = self.symbols[index]

        single = AltiumSchLib()
        single.font_manager = self.font_manager
        single._weight_policy = "serialized_data_records"
        single._lib_additional_header = deepcopy(self._lib_additional_header)

        new_sym = single.add_symbol(
            symbol.name,
            symbol.description,
            original_name=symbol.original_name,
        )
        new_sym.part_count = symbol.part_count
        new_sym.component_record = symbol.component_record
        new_sym._copy_objects_from(symbol)
        new_sym.raw_records = symbol.raw_records
        new_sym._additional_raw_records = symbol._additional_raw_records
        new_sym._additional_terminal_record = symbol._additional_terminal_record
        new_sym._original_streams = dict(symbol._original_streams)
        _copy_symbol_storage_dialect(symbol, new_sym)

        for img in symbol.images:
            filename = getattr(img, "filename", None)
            if filename and filename in self.embedded_images:
                single.embedded_images[filename] = self.embedded_images[filename]

        return single

    def extract_asset(self, ref: AltiumAssetRef) -> AltiumExtractedAsset:
        """
        Extract one asset selected from `asset_inventory()`.
        """
        if ref.source_kind != "schlib":
            raise ValueError(
                f"asset reference source mismatch: expected schlib, got {ref.source_kind}"
            )
        if ref.kind != "sch_symbol":
            raise ValueError(f"unsupported SchLib extractable asset kind: {ref.kind}")

        summaries = self._symbol_asset_summaries()
        index = selected_asset_index(
            ref,
            summaries=summaries,
            expected_source_kind="schlib",
            expected_kind="sch_symbol",
        )
        return AltiumExtractedAsset(
            ref=ref,
            filename=summaries[index].extraction_filename
            or f"symbol_{index:03d}.SchLib",
            schlib=self.extract_symbol(ref),
        )

    def get_summary(self) -> dict[str, object]:
        """
        Get summary statistics for the entire library.
        """
        return {
            "filename": self.filename,
            "filepath": str(self.filepath),
            "symbol_count": len(self.symbols),
            "font_count": len(self.font_manager.fonts) if self.font_manager else 0,
            "symbols": [s.get_summary() for s in self.symbols],
        }

    def split(
        self,
        output_dir: Path,
        name_pattern: str = "{symbol_name}.SchLib",
        symbol_filter: list[str] | None = None,
        verbose: bool = True,
    ) -> dict[str, Path | None]:
        """
        Split this multi-symbol SchLib into individual files.

                Copies symbols with their objects and preserves font tables
                and embedded images via save().

                Args:
                    output_dir: Directory to write individual SchLib files.
                    name_pattern: Filename pattern with {symbol_name} placeholder.
                    symbol_filter: Optional list of symbol names to extract (None = all).
                    verbose: Print progress messages.

                Returns:
                    Dict mapping symbol names to output file paths.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if verbose:
            log.info(f"Splitting: {self.filename}")

        symbols_to_process = self.symbols
        if symbol_filter is not None:
            symbols_to_process = [s for s in self.symbols if s.name in symbol_filter]

        results: dict[str, Path | None] = {}
        used_output_names: dict[str, int] = {}

        for symbol in symbols_to_process:
            try:
                single = AltiumSchLib()
                single.font_manager = self.font_manager
                single._weight_policy = "serialized_data_records"
                single._lib_additional_header = deepcopy(self._lib_additional_header)

                new_sym = single.add_symbol(
                    symbol.name,
                    symbol.description,
                    original_name=symbol.original_name,
                )
                new_sym.part_count = symbol.part_count
                new_sym.component_record = symbol.component_record
                new_sym._copy_objects_from(symbol)
                new_sym.raw_records = symbol.raw_records
                new_sym._additional_raw_records = symbol._additional_raw_records
                new_sym._additional_terminal_record = symbol._additional_terminal_record
                new_sym._original_streams = dict(symbol._original_streams)
                _copy_symbol_storage_dialect(symbol, new_sym)

                # Copy embedded images referenced by this symbol
                for img in symbol.images:
                    filename = getattr(img, "filename", None)
                    if filename and filename in self.embedded_images:
                        single.embedded_images[filename] = self.embedded_images[
                            filename
                        ]

                output_filename = dedupe_artifact_basename(
                    safe_schlib_output_name(name_pattern, symbol.name),
                    used_output_names,
                )
                output_path = output_dir / output_filename
                single.save(output_path, sync_pin_text_data=True)

                if verbose:
                    log.info(f"  Split: {symbol.name} -> {output_filename}")

                results[symbol.name] = output_path

            except Exception as e:
                log.error(f"Failed to split {symbol.name}: {e}")
                results[symbol.name] = None

        if verbose:
            successes = sum(1 for v in results.values() if v is not None)
            log.info(f"Split complete: {successes}/{len(results)} symbols")

        return results

    @classmethod
    def merge(
        cls,
        input_paths: Path | list[Path],
        output_path: Path,
        handle_conflicts: str = "rename",
        verbose: bool = True,
    ) -> AltiumSchLib:
        """
        Merge multiple SchLib files into a single multi-symbol SchLib file.

        This is the primary high-level interface for merging SchLib files.
        It combines multiple individual SchLib files into one file while handling
        font table merging, image deduplication, and symbol name conflicts.

        This performs a vanilla merge that preserves all fonts, graphics, and
        formatting exactly as they appear in the input files.

        Args:
            input_paths: Either:
                - Path to a directory containing SchLib files (*.SchLib, *.Schlib)
                - List of Path objects pointing to individual SchLib files
            output_path: Destination path for the merged SchLib file
            handle_conflicts: How to handle duplicate symbol names:
                - "rename": Append _1, _2, etc. to duplicate names (default)
                - "skip": Skip duplicate symbol names
                - "error": Raise error on conflicts
            verbose: Print progress messages

        Returns:
            AltiumSchLib instance of the merged file

        Raises:
            ValueError: If input_paths is invalid or merge fails
        """
        from .altium_schlib_merger import merge_directory, merge_schlibs

        # Handle input_paths
        if isinstance(input_paths, Path):
            # Directory path - use merge_directory
            if input_paths.is_dir():
                success = merge_directory(
                    input_paths,
                    output_path,
                    pattern="*.SchLib",
                    handle_conflicts=handle_conflicts,
                    verbose=verbose,
                )
            else:
                raise ValueError(f"Path is not a directory: {input_paths}")
        elif isinstance(input_paths, list):
            # List of files - use merge_schlibs
            if not input_paths:
                raise ValueError("input_paths list is empty")

            # Convert to Path objects if needed
            file_paths = [
                Path(p) if not isinstance(p, Path) else p for p in input_paths
            ]

            # Verify all files exist
            for p in file_paths:
                if not p.exists():
                    raise ValueError(f"File not found: {p}")

            success = merge_schlibs(
                file_paths,
                output_path,
                handle_conflicts=handle_conflicts,
                verbose=verbose,
            )
        else:
            raise ValueError(
                f"input_paths must be Path (directory) or list[Path] (files), "
                f"got {type(input_paths)}"
            )

        if not success:
            raise ValueError("Merge operation failed")

        # Parse and return the merged file
        return cls(output_path, debug=False)

    def _find_symbol(self, symbol_name: str) -> AltiumSymbol:
        """
        Return a symbol by name or raise ValueError.
        """
        for symbol in self.symbols:
            if symbol.name == symbol_name:
                return symbol
        raise ValueError(f"Symbol '{symbol_name}' not found in library")

    @staticmethod
    def _record_belongs_to_part(record: Any, part_id: int | None) -> bool:
        """
        Return whether a symbol child record belongs to the selected part.
        """
        if part_id is None:
            return True
        if not hasattr(record, "owner_part_id"):
            return True
        record_part = record.owner_part_id
        if record_part is None or record_part == 0 or record_part == -1:
            return True
        return record_part == part_id

    @staticmethod
    def _build_symbol_viewport(
        symbol: AltiumSymbol,
        *,
        width: int | None,
        height: int | None,
        padding: int,
        auto_fit: bool,
        part_id: int | None,
        display_mode: int | None,
        eligible_source_ids: Collection[int] | None = None,
        parent_by_source_id: Mapping[int, object | None] | None = None,
        root_text_sources: Collection[AltiumSchParameter] = (),
        geometry_ctx: SchSvgRenderContext | None = None,
    ) -> tuple[int, int, float, float]:
        """
        Return (width, height, offset_x, offset_y) for symbol rendering.
        """
        bounds = symbol.get_bounds(
            part_id=part_id,
            display_mode=display_mode,
            _eligible_source_ids=eligible_source_ids,
            _parent_by_source_id=parent_by_source_id,
            _geometry_ctx=geometry_ctx,
        )
        if bounds is None and root_text_sources:
            anchor = next(iter(root_text_sources)).location
            min_x = max_x = anchor.x
            min_y = max_y = anchor.y
        elif bounds is None:
            min_x, min_y, max_x, max_y = -10, -10, 10, 10
        else:
            min_x, min_y, max_x, max_y = bounds

        for source in root_text_sources:
            min_x = min(min_x, source.location.x)
            min_y = min(min_y, source.location.y)
            max_x = max(max_x, source.location.x)
            max_y = max(max_y, source.location.y)

        symbol_width = max_x - min_x
        symbol_height = max_y - min_y
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0

        if auto_fit:
            render_width = max(int(symbol_width + 2 * padding), 100)
            render_height = max(int(symbol_height + 2 * padding), 100)
        else:
            render_width = width if width is not None else 800
            render_height = height if height is not None else 600

        offset_x = render_width / 2.0 - center_x
        offset_y = center_y - render_height / 2.0
        return (render_width, render_height, offset_x, offset_y)

    def _symbol_render_source_state(
        self,
        symbol: AltiumSymbol,
        *,
        part_id: int | None,
        display_mode: int | None,
    ) -> tuple[
        list[object],
        dict[int, int],
        list[object],
        set[int],
        dict[int, object | None],
        _SourceAdmission,
    ]:
        from ._sch_source_admission import _SourceAdmission

        source_objects = list(symbol.objects)
        source_positions = {
            id(source_object): index
            for index, source_object in enumerate(source_objects)
        }
        parent_by_source_id = self._symbol_render_parent_map(symbol, source_objects)
        structurally_unattached = self._symbol_structurally_unattached_source_ids(
            symbol, source_objects, parent_by_source_id
        )
        semantic = _SourceAdmission.for_rendering(
            source_objects,
            parents=parent_by_source_id,
            unattached_ids=structurally_unattached,
        )
        assert semantic.parent_by_source_id is not None
        own_eligibility = {
            id(source_object): semantic.admits(source_object)
            and self._symbol_source_matches_view(
                source_object,
                semantic.parent_by_source_id,
                part_id,
                display_mode,
            )
            for source_object in source_objects
        }
        eligible_source_ids = self._symbol_render_eligible_source_ids(
            source_objects,
            semantic.parent_by_source_id,
            own_eligibility,
        )
        eligible_source_objects = [
            source_object
            for source_object in source_objects
            if id(source_object) in eligible_source_ids
        ]
        return (
            source_objects,
            source_positions,
            eligible_source_objects,
            eligible_source_ids,
            parent_by_source_id,
            semantic,
        )

    @staticmethod
    def _symbol_source_matches_view(
        source: object,
        parents: Mapping[int, object | None],
        part_id: int | None,
        display_mode: int | None,
    ) -> bool:
        from .altium_record_sch__parameter import AltiumSchImageParameter

        current: object | None = source
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, AltiumSchImageParameter):
                return True
            current = parents.get(id(current))
        return AltiumSchLib._record_belongs_to_part(
            source, part_id
        ) and record_belongs_to_display_mode(source, display_mode)

    @staticmethod
    def _symbol_ordinary_root_parameter(
        source: AltiumSchParameter,
        parents: Mapping[int, object | None],
    ) -> bool:
        from ._sch_source_projection import (
            _COMPONENT_BOUND_PARAMETER_NAMES,
            _component_bound_field_role,
        )
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
        from .altium_record_sch__parameter import AltiumSchImageParameter

        if (
            source.record_type != SchRecordType.PARAMETER
            or source.is_image_parameter
            or isinstance(source, AltiumSchImageParameter)
        ):
            return False
        if _component_bound_field_role(source) is not None:
            return False
        if (
            dotnet_ordinal_ignore_case_key(source.name)
            in _COMPONENT_BOUND_PARAMETER_NAMES
        ):
            return False
        parent = parents.get(id(source))
        if isinstance(parent, AltiumSchImplParams):
            parent = parents.get(id(parent))
        return parent is None

    @staticmethod
    def _symbol_import_admitted_ids(
        source_objects: Collection[object],
        parents: Mapping[int, object | None],
    ) -> set[int]:
        from ._sch_source_projection import _record_import_ignores_source

        return AltiumSchLib._symbol_render_eligible_source_ids(
            source_objects,
            parents,
            {
                id(source): not _record_import_ignores_source(source)
                for source in source_objects
            },
        )

    def _symbol_root_text_sources(
        self,
        source_objects: Collection[object],
        parents: Mapping[int, object | None],
    ) -> list[AltiumSchParameter]:
        import_ids = self._symbol_import_admitted_ids(source_objects, parents)
        admitted = [
            source
            for source in source_objects
            if isinstance(source, AltiumSchParameter) and id(source) in import_ids
        ]
        ordinary = [
            source
            for source in admitted
            if not source.is_hidden
            and self._symbol_ordinary_root_parameter(source, parents)
        ]
        return ordinary + self._symbol_visible_root_fields(admitted, parents)

    def _symbol_visible_root_fields(
        self,
        admitted: Collection[AltiumSchParameter],
        parents: Mapping[int, object | None],
    ) -> list[AltiumSchParameter]:
        from ._sch_source_projection import _component_bound_field_slots

        if not self.show_comments_designators:
            return []
        fields = _component_bound_field_slots(
            source for source in admitted if parents.get(id(source)) is None
        )
        # The library editor moves ordinary members first, then copies its
        # bound fields with original ownership cleared. Neither field is
        # part/display filtered, and the copied designator stays unsuffixed.
        return [
            field
            for role in ("designator", "comment")
            if isinstance(field := fields.get(role), AltiumSchParameter)
            and not field.is_hidden
        ]

    def _symbol_root_render_transfer(
        self,
        sources: Collection[object],
        parents: Mapping[int, object | None],
        semantic: _SourceAdmission,
    ) -> tuple[list[AltiumSchParameter], dict[int, object | None], set[int]]:
        from ._sch_source_projection import _component_bound_field_role

        root_text = self._symbol_root_text_sources(sources, parents)
        copied = {
            id(source)
            for source in root_text
            if _component_bound_field_role(source) is not None
        }
        root_text = [
            source
            for source in root_text
            if semantic.admits(source) or id(source) in copied
        ]
        assert semantic.parent_by_source_id is not None
        projected = dict(semantic.parent_by_source_id)
        for source in root_text:
            projected[id(source)] = None
        return root_text, projected, copied

    @staticmethod
    def _symbol_order_root_fields(
        records: list[SchGeometryRecord],
        root_text_sources: Collection[AltiumSchParameter],
    ) -> list[SchGeometryRecord]:
        from ._sch_source_projection import _component_bound_field_role

        field_positions = {
            id(source): index
            for index, source in enumerate(root_text_sources)
            if _component_bound_field_role(source) is not None
        }
        ordinary = [
            record
            for record in records
            if record.render_source_id not in field_positions
        ]
        fields = [
            record for record in records if record.render_source_id in field_positions
        ]
        fields.sort(
            key=lambda record: field_positions[cast(int, record.render_source_id)]
        )
        return ordinary + fields

    @staticmethod
    def _symbol_render_eligible_source_ids(
        source_objects: Collection[object],
        parent_by_source_id: Mapping[int, object | None],
        own_eligibility: Mapping[int, bool],
    ) -> set[int]:
        source_by_id = {
            id(source_object): source_object for source_object in source_objects
        }
        resolved: dict[int, bool] = {}
        for source_object in source_objects:
            AltiumSchLib._resolve_symbol_render_eligibility(
                source_object,
                source_by_id,
                parent_by_source_id,
                own_eligibility,
                resolved,
            )
        return {source_id for source_id, eligible in resolved.items() if eligible}

    @staticmethod
    def _resolve_symbol_render_eligibility(
        source_object: object,
        source_by_id: Mapping[int, object],
        parent_by_source_id: Mapping[int, object | None],
        own_eligibility: Mapping[int, bool],
        resolved: dict[int, bool],
    ) -> None:
        current: object | None = source_object
        path: list[object] = []
        active: set[int] = set()
        while current is not None:
            current_id = id(current)
            if current_id not in source_by_id:
                eligible = False
                break
            if current_id in resolved:
                eligible = resolved[current_id]
                break
            if current_id in active:
                raise ValueError("cyclic SchLib render ownership")
            active.add(current_id)
            path.append(current)
            eligible = own_eligibility[current_id]
            if not eligible:
                break
            current = parent_by_source_id.get(current_id)
        else:
            eligible = True
        for path_object in reversed(path):
            path_id = id(path_object)
            eligible = own_eligibility[path_id] and eligible
            resolved[path_id] = eligible

    @staticmethod
    def _symbol_render_parent_map(
        symbol: AltiumSymbol,
        source_objects: Collection[object],
    ) -> dict[int, object | None]:
        objects_by_record_index = {
            record_index: source_object
            for source_object in source_objects
            if isinstance(
                record_index := getattr(source_object, "_record_index", None),
                int,
            )
        }
        normalized_owners = symbol._normalized_owner_indices
        result: dict[int, object | None] = {}
        for source_object in source_objects:
            parent = getattr(source_object, "parent", None)
            has_raw_parent, raw_parent = AltiumSchLib._symbol_parsed_parent(
                source_object,
                normalized_owners,
                objects_by_record_index,
            )
            if parent is None and has_raw_parent:
                parent = raw_parent
            parent = AltiumSchLib._symbol_implementation_import_parent(
                source_object,
                parent,
                has_raw_parent,
                raw_parent,
                normalized_owners,
                objects_by_record_index,
            )
            result[id(source_object)] = parent
        return result

    @staticmethod
    def _symbol_parsed_parent(
        source: object,
        normalized_owners: Sequence[int],
        objects_by_record_index: Mapping[int, object],
    ) -> tuple[bool, object | None]:
        record_index = getattr(source, "_record_index", None)
        if isinstance(record_index, int) and 0 <= record_index < len(normalized_owners):
            return True, objects_by_record_index.get(normalized_owners[record_index])
        return False, None

    @staticmethod
    def _symbol_implementation_import_parent(
        source: object,
        parent: object | None,
        has_raw_parent: bool,
        raw_parent: object | None,
        normalized_owners: Sequence[int],
        objects_by_record_index: Mapping[int, object],
    ) -> object | None:
        from ._sch_source_projection import _record_import_ignores_source

        if (
            getattr(source, "record_type", None) != SchRecordType.IMPLEMENTATION
            or not has_raw_parent
            or getattr(raw_parent, "record_type", None)
            != SchRecordType.IMPLEMENTATION_LIST
        ):
            return parent
        if _record_import_ignores_source(raw_parent):
            return raw_parent
        has_marker_parent, marker_parent = AltiumSchLib._symbol_parsed_parent(
            raw_parent,
            normalized_owners,
            objects_by_record_index,
        )
        if has_marker_parent:
            return marker_parent
        return getattr(raw_parent, "parent", None)

    @staticmethod
    def _symbol_structurally_unattached_source_ids(
        symbol: AltiumSymbol,
        source_objects: Collection[object],
        parents: Mapping[int, object | None],
    ) -> frozenset[int]:
        objects_by_record_index = {
            record_index: source_object
            for source_object in source_objects
            if isinstance(
                record_index := getattr(source_object, "_record_index", None),
                int,
            )
        }
        unattached: set[int] = set()
        last_field_by_owner_role: dict[tuple[int, SchRecordType], int] = {}
        map_definer_keys_by_owner: set[tuple[int, str]] = set()
        for source in source_objects:
            if getattr(source, "_managed_container_membership", None) == "object_list":
                continue
            owner_context = AltiumSchLib._symbol_managed_owner_context(
                symbol,
                source,
                parents,
                objects_by_record_index,
            )
            if owner_context is None:
                continue
            owner, owner_type, owner_exists = owner_context
            if not owner_exists or not AltiumSchLib._symbol_update_owner_attaches(
                source, owner_type
            ):
                unattached.add(id(source))
                continue
            AltiumSchLib._observe_symbol_managed_field_attachment(
                source,
                owner,
                unattached,
                last_field_by_owner_role,
                map_definer_keys_by_owner,
            )
        return frozenset(unattached)

    @staticmethod
    def _symbol_managed_owner_context(
        symbol: AltiumSymbol,
        source: object,
        parents: Mapping[int, object | None],
        objects_by_record_index: Mapping[int, object],
    ) -> tuple[object | None, object, bool] | None:
        if getattr(source, "_source_stream", None) == "Additional":
            owner = parents.get(id(source))
            if not hasattr(source, "_managed_selected_owner_type"):
                owner_type = (
                    getattr(owner, "record_type", None)
                    if owner is not None
                    else SchRecordType.COMPONENT
                )
                return owner, owner_type, True
            return (
                owner,
                getattr(source, "_managed_selected_owner_type", None),
                not bool(getattr(source, "_managed_unattached", False)),
            )
        record_index = getattr(source, "_record_index", None)
        if not (
            isinstance(record_index, int)
            and 0 <= record_index < len(symbol._normalized_owner_indices)
        ):
            return None
        owner = parents.get(id(source))
        owner_type = getattr(owner, "record_type", None)
        owner_index = symbol._normalized_owner_indices[record_index]
        if owner is not None:
            return owner, owner_type, True
        if owner_index == 0:
            return None, SchRecordType.COMPONENT, True
        return None, owner_type, owner_index in objects_by_record_index

    @staticmethod
    def _observe_symbol_managed_field_attachment(
        source: object,
        owner: object | None,
        unattached: set[int],
        last_field_by_owner_role: dict[tuple[int, SchRecordType], int],
        map_definer_keys_by_owner: set[tuple[int, str]],
    ) -> None:
        from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
        from ._sch_source_projection import _record_import_ignores_source

        if (
            _record_import_ignores_source(source)
            or getattr(source, "_managed_container_membership", None) == "object_list"
        ):
            return
        record_type = getattr(source, "record_type", None)
        owner_key = id(owner) if owner is not None else 0
        if (
            isinstance(record_type, SchRecordType)
            and record_type in _SCHLIB_LAST_WINNER_RECORD_TYPES
        ):
            field_key = (owner_key, record_type)
            previous = last_field_by_owner_role.get(field_key)
            if previous is not None:
                unattached.add(previous)
            last_field_by_owner_role[field_key] = id(source)
        elif record_type == SchRecordType.MAP_DEFINER:
            key = (
                owner_key,
                dotnet_ordinal_ignore_case_key(
                    str(getattr(source, "designator_interface", ""))
                ),
            )
            if key in map_definer_keys_by_owner:
                unattached.add(id(source))
            else:
                map_definer_keys_by_owner.add(key)

    @staticmethod
    def _symbol_update_owner_attaches(source: object, owner_type: object) -> bool:
        record_type = getattr(source, "record_type", None)
        if not isinstance(record_type, SchRecordType):
            return True
        if record_type == SchRecordType.JUNCTION and not bool(
            getattr(source, "locked", False)
        ):
            return False
        if record_type in {
            SchRecordType.IMPLEMENTATION_LIST,
            SchRecordType.IMPL_PARAMS,
        }:
            return False
        if record_type == SchRecordType.DESIGNATOR:
            return owner_type in _SCHLIB_DESIGNATOR_OWNER_TYPES
        required_owners = _SCHLIB_REQUIRED_OWNER_TYPES.get(record_type)
        return required_owners is None or owner_type in required_owners

    def _append_symbol_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        ctx: Any,
        *,
        document_id: str,
        part_id: int | None,
        display_mode: int | None,
        source_positions: Mapping[int, int],
        eligible_source_ids: Collection[int],
        should_skip: Any | None = None,
        copied_root_text: bool = False,
    ) -> None:
        """
        Append symbol child records that expose a callable to_geometry surface.
        """
        from .altium_sch_geometry_oracle import SchGeometryRecord

        eligible_ids = frozenset(eligible_source_ids)
        for obj in objects:
            if id(obj) not in eligible_ids:
                continue
            if isinstance(obj, (AltiumSchHarnessEntry, AltiumSchSheetEntry)):
                continue
            if not self._symbol_source_matches_view(
                obj,
                ctx._source_admission.parent_by_source_id or {},
                part_id,
                display_mode,
            ):
                continue
            if should_skip is not None and should_skip(obj):
                continue
            geometry_record = self._symbol_source_geometry(
                obj, ctx, document_id, copied_root_text
            )
            if isinstance(geometry_record, SchGeometryRecord):
                records.append(
                    replace(
                        geometry_record,
                        source_object_index=source_positions.get(id(obj)),
                        render_group_id=ctx.render_group_id(obj) or None,
                        render_group_identity=ctx.render_group_identity(obj),
                        render_source_id=id(obj),
                    )
                )

    @staticmethod
    def _symbol_source_geometry(
        source: object,
        ctx: SchSvgRenderContext,
        document_id: str,
        copied_root_text: bool,
    ) -> SchGeometryRecord | None:
        from .altium_record_sch__parameter import AltiumSchImageParameter
        from .altium_sch_geometry_oracle import SchGeometryRecord

        if copied_root_text and isinstance(source, AltiumSchImageParameter):
            # The editor copies this field into a plain SchComponentComment;
            # its image-model subtype and descendants are not transferred.
            geometry = source._to_component_comment_geometry(
                ctx, document_id=document_id, units_per_px=64
            )
        else:
            method = getattr(source, "to_geometry", None)
            geometry = (
                method(ctx, document_id=document_id, units_per_px=64)
                if callable(method)
                else None
            )
        return geometry if isinstance(geometry, SchGeometryRecord) else None

    def _append_symbol_parent_bound_geometry_records(
        self,
        records: list[Any],
        source_objects: list[object],
        ctx: Any,
        *,
        document_id: str,
        eligible_source_ids: Collection[int],
        source_positions: Mapping[int, int],
        parent_by_source_id: Mapping[int, object | None],
    ) -> None:
        """Append entry records whose geometry is relative to an owner."""
        eligible_ids = frozenset(eligible_source_ids)
        for entry in source_objects:
            if id(entry) not in eligible_ids:
                continue
            owner = parent_by_source_id.get(id(entry))
            if id(owner) not in eligible_ids:
                continue
            if isinstance(owner, AltiumSchHarnessConnector):
                parent_x, parent_y = ctx.transform_point(
                    owner.location.x,
                    owner.location.y,
                )
                parent_width = owner.xsize * ctx.scale
                parent_height = owner.ysize * ctx.scale
                connector_side = int(getattr(owner, "side", 1))
                if not isinstance(entry, AltiumSchHarnessEntry):
                    continue
                brace_orientation = (
                    connector_side
                    if connector_side in (2, 3)
                    else 1 - int(getattr(entry, "side", 0))
                )
                record = entry.to_geometry(
                    ctx,
                    document_id=document_id,
                    parent_x=parent_x,
                    parent_y=parent_y,
                    parent_width=parent_width,
                    parent_height=parent_height,
                    parent_orientation=brace_orientation,
                    units_per_px=64,
                )
                records.append(
                    replace(
                        record,
                        source_object_index=source_positions.get(id(entry)),
                        render_group_id=ctx.render_group_id(entry) or None,
                        render_group_identity=ctx.render_group_identity(entry),
                        render_source_id=id(entry),
                    )
                )
            elif isinstance(owner, AltiumSchSheetSymbol):
                if not isinstance(entry, AltiumSchSheetEntry):
                    continue
                parent_x, parent_y = ctx.transform_point(
                    owner.location.x,
                    owner.location.y,
                )
                parent_width = owner.x_size * ctx.scale
                parent_height = owner.y_size * ctx.scale
                record = entry.to_geometry(
                    ctx,
                    document_id=document_id,
                    parent_x=parent_x,
                    parent_y=parent_y,
                    parent_width=parent_width,
                    parent_height=parent_height,
                    units_per_px=64,
                )
                records.append(
                    replace(
                        record,
                        source_object_index=source_positions.get(id(entry)),
                        render_group_id=ctx.render_group_id(entry) or None,
                        render_group_identity=ctx.render_group_identity(entry),
                        render_source_id=id(entry),
                    )
                )

    @staticmethod
    def _snapshot_symbol_harness_render_state(
        source_objects: Collection[object],
    ) -> tuple[tuple[object, str, object], ...]:
        snapshots: list[tuple[object, str, object]] = []
        for source_object in source_objects:
            if not isinstance(source_object, (AltiumSchDesignator, AltiumSchParameter)):
                continue
            for attribute in ("location", "orientation", "font_id"):
                if hasattr(source_object, attribute):
                    snapshots.append(
                        (source_object, attribute, getattr(source_object, attribute))
                    )
        for source_object in source_objects:
            if hasattr(source_object, "default_designator_position"):
                snapshots.append(
                    (
                        source_object,
                        "default_designator_position",
                        getattr(source_object, "default_designator_position"),
                    )
                )
        return tuple(snapshots)

    @staticmethod
    def _restore_symbol_harness_render_state(
        snapshots: Collection[tuple[object, str, object]],
    ) -> None:
        for source_object, attribute, value in snapshots:
            setattr(source_object, attribute, value)

    def _append_symbol_image_geometry_records(
        self,
        records: list[Any],
        symbol: AltiumSymbol,
        ctx: Any,
        *,
        document_id: str,
        part_id: int | None,
        display_mode: int | None,
        source_positions: Mapping[int, int],
        eligible_source_ids: Collection[int],
    ) -> dict[str, str]:
        """
        Append image geometry records and return runtime image hrefs.
        """
        import base64

        from .altium_record_sch__parameter import AltiumSchImageParameter

        runtime_image_hrefs: dict[str, str] = {}
        eligible_ids = frozenset(eligible_source_ids)
        image_parameter_owned_ids = ctx._source_admission.descendant_ids_of_type(
            ctx._source_admission.source_objects,
            AltiumSchImageParameter,
        )
        for image in symbol.images:
            if id(image) not in eligible_ids:
                continue
            if not self._symbol_source_matches_view(
                image,
                ctx._source_admission.parent_by_source_id or {},
                part_id,
                display_mode,
            ):
                continue
            geometry_record = image.to_geometry(
                ctx,
                document_id=document_id,
                units_per_px=64,
            )
            if geometry_record is not None:
                records.append(
                    self._symbol_image_parameter_model_record(
                        geometry_record,
                        image,
                        ctx,
                        source_positions,
                        suppress_svg=id(image) in image_parameter_owned_ids,
                    )
                )
            if not getattr(image, "image_data", None):
                continue
            runtime_payload = image._runtime_image_payload(
                document_path=str(self.filepath) if self.filepath else None,
            )
            if runtime_payload is None:
                continue
            mime_type, image_data = runtime_payload
            runtime_key = (
                image.runtime_image_key(document_id)
                if hasattr(image, "runtime_image_key")
                else str(getattr(image, "unique_id", "") or "")
            )
            runtime_image_hrefs[runtime_key] = (
                f"data:{mime_type};base64,"
                + base64.b64encode(image_data).decode("ascii")
            )
        return runtime_image_hrefs

    @staticmethod
    def _symbol_image_parameter_model_record(
        record: "SchGeometryRecord",
        image: object,
        ctx: SchSvgRenderContext,
        source_positions: Mapping[int, int],
        *,
        suppress_svg: bool,
    ) -> "SchGeometryRecord":
        extras = dict(record.extras)
        if suppress_svg:
            extras["skip_svg"] = True
        return replace(
            record,
            extras=extras,
            source_object_index=source_positions.get(id(image)),
            render_group_id=ctx.render_group_id(image) or None,
            render_group_identity=ctx.render_group_identity(image),
            render_source_id=id(image),
        )

    def _append_symbol_image_parameter_model_target_records(
        self,
        records: list[Any],
        source_objects: Collection[object],
        ctx: SchSvgRenderContext,
        *,
        document_id: str,
        source_positions: Mapping[int, int],
        eligible_source_ids: Collection[int],
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryRecord
        from .altium_record_sch__parameter import AltiumSchImageParameter

        model_types = {
            SchRecordType.IMAGE,
            SchRecordType.LINE_VIEW,
            SchRecordType.HARNESS_CAVITY_COMPONENT,
        }
        emitted_ids = {
            record.render_source_id
            for record in records
            if record.render_source_id is not None
        }
        eligible_ids = frozenset(eligible_source_ids)
        image_parameter_owned_ids = ctx._source_admission.descendant_ids_of_type(
            source_objects,
            AltiumSchImageParameter,
        )
        for source in source_objects:
            if id(source) not in eligible_ids or id(source) in emitted_ids:
                continue
            if (
                not isinstance(source, AltiumSchImageParameter)
                and getattr(source, "record_type", None) not in model_types
            ):
                continue
            if id(source) not in image_parameter_owned_ids:
                continue
            raw_record = self._symbol_source_geometry(
                source, ctx, document_id, copied_root_text=False
            )
            if not isinstance(raw_record, SchGeometryRecord):
                continue
            records.append(
                self._symbol_image_parameter_model_record(
                    raw_record,
                    source,
                    ctx,
                    source_positions,
                    suppress_svg=True,
                )
            )

    def _render_sheet_area_color(self) -> int:
        color, _ = AltiumSerializer().read_color(
            self.file_header or {}, Fields.AREA_COLOR, default=SHEET_AREA_COLOR
        )
        return SHEET_AREA_COLOR if color is None else color

    def symbol_to_ir(
        self,
        symbol_name: str,
        width: int | None = None,
        height: int | None = None,
        padding: int = 50,
        background: str = "#FFFFFF",
        auto_fit: bool = True,
        part_id: int | None = None,
        display_mode: int | None = None,
        *,
        profile: str = "onscreen",
        render_options: SchSvgRenderOptions | None = None,
        pin_text_follows_orientation: bool = False,
    ) -> SchGeometryDocument:
        """
        Build a symbol-scoped IR document for a standalone SchLib symbol render.

        The symbol renderer now follows the same IR -> SVG pipeline as SchDoc.
        """
        from ._sch_source_admission import _SourceAdmission
        from .altium_sch_geometry_oracle import (
            SchGeometryDocument,
            _render_group_ids_for_sources,
            normalize_sch_ir_render_profile,
        )
        from .altium_sch_svg_renderer import (
            SchSvgRenderContext,
            SchSvgRenderOptions,
            _BlanketRenderBudget,
            _ParameterSetRenderBudget,
        )

        symbol = self._find_symbol(symbol_name)
        (
            source_objects,
            source_positions,
            eligible_source_objects,
            eligible_source_ids,
            parent_by_source_id,
            semantic_admission,
        ) = self._symbol_render_source_state(
            symbol,
            part_id=part_id,
            display_mode=display_mode,
        )
        root_text_sources, parent_by_source_id, copied_field_ids = (
            self._symbol_root_render_transfer(
                source_objects, parent_by_source_id, semantic_admission
            )
        )
        eligible_source_ids.update(id(source) for source in root_text_sources)
        eligible_source_objects = [
            source for source in source_objects if id(source) in eligible_source_ids
        ]
        resolved_profile = normalize_sch_ir_render_profile(profile)
        if render_options is None:
            render_options = (
                SchSvgRenderOptions.onscreen()
                if resolved_profile.value == "onscreen"
                else SchSvgRenderOptions.native_altium()
            )
        render_admission = _SourceAdmission.from_projection(
            source_objects,
            eligible_source_ids,
            parent_by_source_id,
            unattached_ids=semantic_admission.unattached_ids - copied_field_ids,
        )
        active_parameter_set_budget = _SCHLIB_PARAMETER_SET_RENDER_BUDGET.get()
        if active_parameter_set_budget is not None and not isinstance(
            active_parameter_set_budget, _ParameterSetRenderBudget
        ):
            raise TypeError("invalid SchLib ParameterSet render budget")
        bounds_ctx = SchSvgRenderContext(
            scale=1.0,
            stroke_scale=1.0,
            font_manager=self.font_manager,
            schlib_mode=True,
            options=render_options,
            _prepared_parameter_set_budget=active_parameter_set_budget,
        )
        bounds_ctx._source_admission = render_admission
        bounds_ctx._blanket_metafile_line_patterns = resolved_profile.value == "oracle"
        active_blanket_budget = _SCHLIB_BLANKET_RENDER_BUDGET.get()
        if active_blanket_budget is not None:
            if not isinstance(active_blanket_budget, _BlanketRenderBudget):
                raise TypeError("invalid SchLib Blanket render budget")
            bounds_ctx._blanket_work_budget = active_blanket_budget
        bounds_ctx._capture_parameter_set_state()
        render_width, render_height, offset_x, offset_y = self._build_symbol_viewport(
            symbol,
            width=width,
            height=height,
            padding=padding,
            auto_fit=auto_fit,
            part_id=part_id,
            display_mode=display_mode,
            eligible_source_ids=eligible_source_ids,
            parent_by_source_id=parent_by_source_id,
            root_text_sources=root_text_sources,
            geometry_ctx=bounds_ctx,
        )

        ctx = SchSvgRenderContext(
            scale=1.0,
            stroke_scale=1.0,
            offset_x=offset_x,
            offset_y=offset_y,
            flip_y=True,
            sheet_height=render_height,
            sheet_width=render_width,
            show_pins=True,
            show_pin_names=True,
            show_pin_numbers=True,
            font_manager=self.font_manager,
            background_color=background,
            document_path=str(self.filepath) if self.filepath else None,
            schlib_mode=True,
            pin_text_follows_orientation=pin_text_follows_orientation,
            options=render_options,
            _prepared_parameter_set_budget=bounds_ctx._parameter_set_work_budget,
            sheet_area_color=self._render_sheet_area_color(),
            render_group_ids=_render_group_ids_for_sources(source_objects),
        )
        ctx._blanket_metafile_line_patterns = resolved_profile.value == "oracle"
        ctx._blanket_work_budget = bounds_ctx._blanket_work_budget
        ctx._imported_parameter_set_ids = bounds_ctx._imported_parameter_set_ids
        ctx._imported_parameter_ids = bounds_ctx._imported_parameter_ids
        ctx._source_admission = render_admission

        part_suffix = f"-part{part_id}" if part_id is not None else ""
        doc_unique_id = f"symbol-{symbol.name}{part_suffix}"
        records = []

        self._append_symbol_geometry_records(
            records,
            root_text_sources,
            ctx,
            document_id=doc_unique_id,
            part_id=None,
            display_mode=None,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
            copied_root_text=True,
        )

        from .altium_record_sch__parameter import AltiumSchImageParameter

        root_image_parameters = [
            source
            for source in source_objects
            if isinstance(source, AltiumSchImageParameter)
            and parent_by_source_id.get(id(source)) is None
            and id(source) not in copied_field_ids
        ]
        self._append_symbol_geometry_records(
            records,
            root_image_parameters,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )

        from .altium_record_sch__parameter_set import AltiumSchParameterSet

        parameter_sets = [
            source for source in source_objects if type(source) is AltiumSchParameterSet
        ]
        self._append_symbol_geometry_records(
            records,
            parameter_sets,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )
        self._append_symbol_geometry_records(
            records,
            (
                source
                for source in source_objects
                if isinstance(
                    ctx._source_admission.parent(source), AltiumSchParameterSet
                )
            ),
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )

        self._append_symbol_geometry_records(
            records,
            symbol.graphic_primitives,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
            should_skip=lambda graphic: (
                "Designator" in type(graphic).__name__
                or "Parameter" in type(graphic).__name__
                or "Pin" in type(graphic).__name__
            ),
        )
        self._append_symbol_geometry_records(
            records,
            symbol.pins,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )
        runtime_image_hrefs = self._append_symbol_image_geometry_records(
            records,
            symbol,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )
        self._append_symbol_image_parameter_model_target_records(
            records,
            source_objects,
            ctx,
            document_id=doc_unique_id,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )
        self._append_symbol_geometry_records(
            records,
            symbol.labels,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )
        self._append_symbol_geometry_records(
            records,
            symbol.text_frames,
            ctx,
            document_id=doc_unique_id,
            part_id=part_id,
            display_mode=display_mode,
            source_positions=source_positions,
            eligible_source_ids=eligible_source_ids,
        )

        self._append_symbol_parent_bound_geometry_records(
            records,
            source_objects,
            ctx,
            document_id=doc_unique_id,
            eligible_source_ids=eligible_source_ids,
            source_positions=source_positions,
            parent_by_source_id=parent_by_source_id,
        )

        from .altium_schdoc import AltiumSchDoc

        harness_state = self._snapshot_symbol_harness_render_state(source_objects)
        try:
            AltiumSchDoc._append_harness_layout_geometry_records_for_source_objects(
                records,
                source_objects,
                ctx,
                document_id=doc_unique_id,
                units_per_px=64,
                eligible_source_ids=eligible_source_ids,
                parent_by_source_id=parent_by_source_id,
            )
        finally:
            self._restore_symbol_harness_render_state(harness_state)

        from .altium_sch_paint_order import order_geometry_records_by_source

        records = order_geometry_records_by_source(
            records,
            source_objects,
            sort_root_transparency=True,
            eligible_source_objects=eligible_source_objects,
            parent_by_source_id=parent_by_source_id,
        )
        records = self._symbol_order_root_fields(records, root_text_sources)

        from .altium_record_sch__blanket import _complete_blanket_record_bounds

        records = _complete_blanket_record_bounds(
            records,
            ctx,
            document_kind="library",
        )

        document = SchGeometryDocument(
            records=records,
            source_path=str(self.filepath) if self.filepath else None,
            source_kind="SCHLIB",
            include_kinds=["all"],
            coordinate_space={
                "kind": "screen_px_fixed",
                "units_per_px": 64,
                "y_axis_down": True,
            },
            canvas={
                "width_px": render_width,
                "height_px": render_height,
            },
            document_id=doc_unique_id,
            workspace_background_color=background,
            render_hints={
                "ir_profile": normalize_sch_ir_render_profile(profile).value,
                "schlib_mode": True,
            },
            extras={
                "symbol_name": symbol.name,
                "part_id": part_id,
                "display_mode": display_mode,
                "background_color": background,
            },
        )
        object.__setattr__(document, "_runtime_image_hrefs", runtime_image_hrefs)
        return document

    def symbol_to_svg(
        self,
        symbol_name: str,
        width: int | None = None,
        height: int | None = None,
        padding: int = 50,
        background: str = "#FFFFFF",
        auto_fit: bool = True,
        part_id: int | None = None,
        display_mode: int | None = None,
        *,
        options: SchSvgRenderOptions | None = None,
        pin_text_follows_orientation: bool = False,
    ) -> str:
        """
        Render a single symbol from this library as a standalone SVG.

        Uses the same IR -> SVG rendering path as SchDoc for consistency.

        Args:
            symbol_name: Name of the symbol to render
            width: SVG width in pixels. If None and auto_fit=True, computed from bounds.
            height: SVG height in pixels. If None and auto_fit=True, computed from bounds.
            padding: Padding around symbol in pixels (used when auto_fit=True)
            background: Background color
            auto_fit: If True, derive viewport size from symbol bounding box + padding.
                      If False, use width/height (defaults to 800x600 if not specified).
            part_id: For multipart symbols, render only graphics/pins belonging to this part.
                     If None, renders all parts (original behavior). Part IDs start at 1.
            display_mode: For symbols with alternate display modes, render only
                          graphics/pins for this display mode. If None, renders
                          all display modes.
            options: SchSvgRenderOptions for SVG compatibility/rendering controls.

        Returns:
            Complete SVG document as string

        Raises:
            ValueError: If symbol_name not found in library
        """
        from .altium_sch_geometry_renderer import (
            SchGeometrySvgRenderer,
            SchGeometrySvgRenderOptions,
        )
        from .altium_sch_svg_renderer import (
            SchSvgRenderOptions,
            _BlanketRenderBudget,
            _ParameterSetRenderBudget,
        )

        render_options = options if options is not None else SchSvgRenderOptions()
        active_blanket_budget = _SCHLIB_BLANKET_RENDER_BUDGET.get()
        if active_blanket_budget is None:
            active_blanket_budget = _BlanketRenderBudget.from_options(render_options)
        if not isinstance(active_blanket_budget, _BlanketRenderBudget):
            raise TypeError("invalid SchLib Blanket render budget")
        active_parameter_set_budget = _SCHLIB_PARAMETER_SET_RENDER_BUDGET.get()
        if active_parameter_set_budget is None:
            active_parameter_set_budget = _ParameterSetRenderBudget.from_options(
                render_options
            )
        if not isinstance(active_parameter_set_budget, _ParameterSetRenderBudget):
            raise TypeError("invalid SchLib ParameterSet render budget")
        blanket_token = (
            _SCHLIB_BLANKET_RENDER_BUDGET.set(active_blanket_budget)
            if _SCHLIB_BLANKET_RENDER_BUDGET.get() is None
            else None
        )
        parameter_set_token = (
            _SCHLIB_PARAMETER_SET_RENDER_BUDGET.set(active_parameter_set_budget)
            if _SCHLIB_PARAMETER_SET_RENDER_BUDGET.get() is None
            else None
        )
        try:
            ir_profile = (
                "oracle"
                if render_options.truncate_font_size_for_baseline
                else "onscreen"
            )
            ir_document = self.symbol_to_ir(
                symbol_name,
                width=width,
                height=height,
                padding=padding,
                background=background,
                auto_fit=auto_fit,
                part_id=part_id,
                display_mode=display_mode,
                profile=ir_profile,
                render_options=render_options,
                pin_text_follows_orientation=pin_text_follows_orientation,
            )
            svg = SchGeometrySvgRenderer(
                SchGeometrySvgRenderOptions(
                    include_workspace_background=getattr(
                        render_options, "include_workspace_background", True
                    ),
                    workspace_background_color=background,
                    include_xml_declaration=getattr(
                        render_options, "include_xml_declaration", True
                    ),
                    text_mode=(
                        "native_svg_export"
                        if render_options.truncate_font_size_for_baseline
                        else "onscreen"
                    ),
                    compile_mask_render_mode=render_options.compile_mask_render_mode,
                    text_as_polygons=render_options.text_as_polygons,
                    polygon_text_tolerance=render_options.polygon_text_tolerance,
                    include_view_box=render_options.include_view_box,
                    embed_bundled_fallback_fonts=(
                        render_options.embed_bundled_fallback_fonts
                    ),
                )
            ).render(ir_document)
            active_blanket_budget.reserve_output_bytes(len(svg.encode("utf-8")))
            return svg
        finally:
            if parameter_set_token is not None:
                _SCHLIB_PARAMETER_SET_RENDER_BUDGET.reset(parameter_set_token)
            if blanket_token is not None:
                _SCHLIB_BLANKET_RENDER_BUDGET.reset(blanket_token)

    def to_svg(
        self,
        output_dir: Path | None = None,
        width: int = 800,
        height: int = 600,
        padding: int = 50,
        background: str = "#FFFFFF",
        options: SchSvgRenderOptions | None = None,
    ) -> dict[str, dict[int, str]]:
        """
        Render all symbols in the library to SVG.

        Each symbol is rendered as a standalone SVG document.
        For multipart symbols (part_count > 1), generates a separate SVG for each part.

        Args:
            output_dir: If provided, write SVG files to this directory.
                        Naming convention:
                        - Single-part: "{symbol_name}.svg"
                        - Multipart: "{symbol_name}_part{n}.svg"
            width: SVG width in pixels
            height: SVG height in pixels
            padding: Padding around symbol in pixels
            background: Background color
            options: SchSvgRenderOptions for SVG compatibility/rendering controls.

        Returns:
            Nested dict: {symbol_name: {part_id: svg_content}}
            - symbol_name: Name of the symbol
            - part_id: Part number (1-based), always present even for single-part
            - svg_content: SVG document as string

            Example structure:
            {
                "LED": {1: "<svg>...</svg>"},
                "MCXA156VMP": {1: "<svg>...</svg>", 2: "<svg>...</svg>", ...}
            }
        """
        from pathlib import Path

        from .altium_sch_svg_renderer import (
            SchSvgRenderOptions,
            _BlanketRenderBudget,
            _ParameterSetRenderBudget,
        )

        results: dict[str, dict[int, str]] = {}
        pending_output: list[tuple[Path, str]] = []
        destination = Path(output_dir) if output_dir is not None else None
        render_options = options if options is not None else SchSvgRenderOptions()
        blanket_budget = _BlanketRenderBudget.from_options(render_options)
        parameter_set_budget = _ParameterSetRenderBudget.from_options(render_options)
        blanket_token = _SCHLIB_BLANKET_RENDER_BUDGET.set(blanket_budget)
        parameter_set_token = _SCHLIB_PARAMETER_SET_RENDER_BUDGET.set(
            parameter_set_budget
        )
        try:
            for symbol in self.symbols:
                # Check if multipart symbol
                part_count = getattr(symbol, "part_count", 1) or 1
                results[symbol.name] = {}

                for part_id in range(1, part_count + 1):
                    try:
                        svg = self.symbol_to_svg(
                            symbol.name,
                            width=width,
                            height=height,
                            padding=padding,
                            background=background,
                            options=render_options,
                            part_id=part_id if part_count > 1 else None,
                        )
                        results[symbol.name][part_id] = svg

                        _queue_schlib_svg_output(
                            pending_output,
                            destination,
                            symbol.name,
                            part_id,
                            part_count,
                            svg,
                        )

                    except Exception as error:
                        if blanket_budget.failed or parameter_set_budget.failed:
                            raise
                        log.warning(
                            "Failed to render symbol '%s' part %s: %s",
                            symbol.name,
                            part_id,
                            error,
                        )
                        continue
        finally:
            _SCHLIB_PARAMETER_SET_RENDER_BUDGET.reset(parameter_set_token)
            _SCHLIB_BLANKET_RENDER_BUDGET.reset(blanket_token)

        _publish_schlib_svg_output(destination, pending_output)
        return results

    def to_json(self, filepath: Path | str | None = None) -> dict:
        """
        Export SchLib to JSON format for interoperability testing.

        Creates a JSON structure compatible with native AltiumInterop.JsonTests.
        Format:
            {
                "Header": {...},
                "Symbols": [
                    {
                        "Name": "symbol_name",
                        "Description": "...",
                        "Objects": [...]  # All records as JSON
                    },
                ]
            }

        Args:
            filepath: Optional path to write JSON file.
                     If provided, writes to file and returns dict.
                     If None, just returns the dict.

        Returns:
            JSON-serializable dict of the library structure
        """
        result: dict[str, object] = {
            "Header": {
                "Filename": self.filename,
                "SymbolCount": len(self.symbols),
                "FontCount": len(self.font_manager.fonts) if self.font_manager else 0,
            },
            "Symbols": [],
        }

        # Add font table if present
        if self.font_manager and self.font_manager.fonts:
            header = cast(dict[str, object], result["Header"])
            header["Fonts"] = [
                _schlib_json_font(font_id, info)
                for font_id, info in sorted(self.font_manager.fonts.items())
            ]

        # Export each symbol
        symbols = cast(list[dict[str, object]], result["Symbols"])
        symbols.extend(_schlib_json_symbol(symbol) for symbol in self.symbols)

        # Write to file if path provided
        if filepath is not None:
            output_path = Path(filepath)
            with output_path.open("w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
            log.info("Exported SchLib to JSON: %s", output_path)

        return result

    def to_tagged_json(self, filepath: Path | str | None = None) -> dict[str, object]:
        """Export the explicit versioned SchLib interoperability envelope."""
        from .altium_sch_interop_contract import SCHLIB_INTEROP_SCHEMA

        result: dict[str, object] = {
            "schema": SCHLIB_INTEROP_SCHEMA,
            "document": self.to_json(),
        }
        if filepath is not None:
            output_path = Path(filepath)
            with output_path.open("w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
            log.info("Exported tagged SchLib JSON: %s", output_path)
        return result

    @classmethod
    def from_json(
        cls,
        source: Path | str | dict,
    ) -> AltiumSchLib:
        """
        Create a new SchLib from JSON data.

                This is the public alternate-format ingest path. The returned library
                is created in empty authoring mode and then populated from the JSON payload.
                To mutate an existing binary-backed library or template, instantiate
                ``AltiumSchLib`` first and then call ``apply_json()``.

                Args:
                    source: JSON file path (Path or str) or parsed dict.

                Returns:
                    AltiumSchLib instance with data loaded from JSON.

                Raises:
                    ValueError: If JSON format is invalid.
        """
        instance = cls()
        instance.apply_json(source)
        return instance

    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict:
        """
        Load and validate a SchLib JSON payload.
        """
        from .altium_sch_interop_contract import _unwrap_schlib_interop_json

        data = load_bounded_json_source(source, limits=_SchLibReadLimits())
        data = _unwrap_schlib_interop_json(data)
        unexpected = set(data).difference({"Header", "Symbols"})
        if unexpected:
            raise ValueError(
                f"Invalid JSON format: unexpected root key {min(unexpected)!r}"
            )
        if "Header" not in data:
            raise ValueError("Invalid JSON format: missing 'Header' key")
        if "Symbols" not in data:
            raise ValueError("Invalid JSON format: missing 'Symbols' key")

        return data

    def _validate_json_staged(self) -> None:
        """Serialize and reopen the complete candidate before JSON commit."""
        validation = deepcopy(self)
        writer = validation._stage_schlib_writer(
            debug=False,
            sync_pin_text_data=False,
            minimal=False,
        )
        with tempfile.TemporaryDirectory(prefix="altium-monkey-json-") as directory:
            path = Path(directory) / "candidate.SchLib"
            validation._write_staged_container(writer, path)
            AltiumSchLib(path)

    def _update_from_json(self, data: dict) -> None:
        """
        Build and validate a complete replacement symbol inventory from JSON.
        """
        header = data["Header"]
        if not isinstance(header, Mapping):
            raise ValueError("SchLib JSON Header must be an object")
        unexpected = set(header).difference(
            {"Filename", "SymbolCount", "FontCount", "Fonts"}
        )
        if unexpected:
            raise ValueError(
                f"SchLib JSON Header has unexpected field {min(unexpected)!r}"
            )
        symbol_rows = data.get("Symbols")
        if not isinstance(symbol_rows, list):
            raise ValueError("SchLib JSON Symbols must be a list")
        self._validate_json_symbol_row_budgets(symbol_rows)
        self._validate_json_header_counts(header, len(symbol_rows))
        template_symbols = list(self.symbols)
        template_mode = bool(template_symbols) or self.filepath is not None
        if template_mode and len(symbol_rows) != len(template_symbols):
            raise ValueError("SchLib JSON changes the template symbol inventory")

        self.font_manager = self._json_font_manager(header)
        blob_budget = JsonBlobBudget(
            self._read_limits.max_total_decompressed_blob_bytes
        )
        rebuilt: list[AltiumSymbol] = []
        for index, symbol_row in enumerate(symbol_rows):
            template = template_symbols[index] if template_mode else None
            rebuilt.append(
                self._symbol_from_json(symbol_row, index, template, blob_budget)
            )

        self._symbols.clear()
        self._symbols.extend(rebuilt)
        self.filename = self._json_filename(header)
        self._validate_symbol_storage_plan()
        self._attach_embedded_images()
        self._schematic_binding_context = SchematicBindingContext(self, kind="schlib")
        self._bind_all_symbols_to_context()

    def _validate_json_symbol_row_budgets(self, symbols: list[object]) -> None:
        total = 0
        for index, value in enumerate(symbols):
            if not isinstance(value, Mapping):
                raise ValueError(f"Symbols[{index}] must be an object")
            rows = value.get("Objects")
            if not isinstance(rows, list):
                raise ValueError(f"Symbols[{index}].Objects must be a list")
            count = len(rows)
            if count > self._read_limits.max_records_per_stream:
                raise ValueError(
                    f"Symbols[{index}].Objects exceeds the reviewed record limit"
                )
            total += count
            if total > self._read_limits.max_total_records_per_document:
                raise ValueError("SchLib JSON exceeds the aggregate record limit")

    @staticmethod
    def _json_filename(header: Mapping[str, object]) -> str:
        if "Filename" not in header:
            raise ValueError("SchLib JSON Header.Filename is required")
        filename = header["Filename"]
        if not isinstance(filename, str):
            raise ValueError("SchLib JSON Header.Filename must be a string")
        return filename

    @staticmethod
    def _validate_json_header_counts(
        header: Mapping[str, object], symbol_count: int
    ) -> None:
        if "SymbolCount" not in header:
            raise ValueError("SchLib JSON Header.SymbolCount is required")
        declared = header["SymbolCount"]
        if (
            isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared != symbol_count
        ):
            raise ValueError("SchLib JSON Header.SymbolCount is inconsistent")

    @staticmethod
    def _json_font_manager(header: Mapping[str, object]) -> FontIDManager | None:
        if "FontCount" not in header:
            raise ValueError("SchLib JSON Header.FontCount is required")
        font_rows = header.get("Fonts", [])
        if not isinstance(font_rows, list):
            raise ValueError("SchLib JSON Header.Fonts must be a list")
        fonts = AltiumSchLib._json_font_table(font_rows)
        AltiumSchLib._validate_json_font_count(header.get("FontCount"), len(fonts))
        return FontIDManager.from_font_dict(fonts) if fonts else None

    @staticmethod
    def _json_font_table(font_rows: list[object]) -> dict[int, dict[str, object]]:
        fonts: dict[int, dict[str, object]] = {}
        for index, row in enumerate(font_rows):
            font_id, font = AltiumSchLib._json_font_spec(row, index)
            if font_id != index + 1:
                raise ValueError(
                    "SchLib JSON Header.Fonts must use dense FontID values"
                )
            if font_id in fonts:
                raise ValueError("SchLib JSON Header.Fonts has a duplicate FontID")
            fonts[font_id] = font
        return fonts

    @staticmethod
    def _json_font_spec(value: object, index: int) -> tuple[int, dict[str, object]]:
        context = f"SchLib JSON Header.Fonts[{index}]"
        if not isinstance(value, Mapping):
            raise ValueError(f"{context} must be an object")
        unexpected = set(value).difference(
            {
                "FontID",
                "FontName",
                "FontSize",
                "Rotation",
                "Underline",
                "Italic",
                "Bold",
                "Strikeout",
            }
        )
        if unexpected:
            raise ValueError(f"{context} has unexpected field {min(unexpected)!r}")
        font_id = value.get("FontID")
        name = value.get("FontName")
        size = value.get("FontSize")
        if isinstance(font_id, bool) or not isinstance(font_id, int):
            raise ValueError(f"{context}.FontID is invalid")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{context}.FontName is invalid")
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueError(f"{context}.FontSize is invalid")
        optional_values = {
            "rotation": value.get("Rotation", 0),
            "underline": value.get("Underline", False),
            "italic": value.get("Italic", False),
            "bold": value.get("Bold", False),
            "strikeout": value.get("Strikeout", False),
        }
        AltiumSchLib._validate_json_font_options(optional_values, context)
        return font_id, {
            "name": name,
            "size": size,
            **optional_values,
        }

    @staticmethod
    def _validate_json_font_options(
        options: Mapping[str, object], context: str
    ) -> None:
        rotation = options["rotation"]
        if isinstance(rotation, bool) or not isinstance(rotation, int):
            raise ValueError(f"{context}.Rotation is invalid")
        for field in ("underline", "italic", "bold", "strikeout"):
            if not isinstance(options[field], bool):
                raise ValueError(f"{context}.{field.title()} must be a boolean")

    @staticmethod
    def _validate_json_font_count(declared: object, actual: int) -> None:
        if (
            isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared != actual
        ):
            raise ValueError("SchLib JSON Header.FontCount is inconsistent")

    def _symbol_from_json(
        self,
        value: object,
        index: int,
        template: AltiumSymbol | None,
        blob_budget: JsonBlobBudget,
    ) -> AltiumSymbol:
        context = f"Symbols[{index}]"
        if not isinstance(value, Mapping):
            raise ValueError(f"{context} must be an object")
        unexpected = set(value).difference(
            {"Name", "Description", "PartCount", "Objects"}
        )
        if unexpected:
            raise ValueError(f"{context} has unexpected field {min(unexpected)!r}")
        name = self._json_symbol_name(value.get("Name"), context, template)
        records = self._json_symbol_records(
            value.get("Objects"), context, template, blob_budget
        )
        return self._build_json_symbol(name, records, value, context, template)

    def _json_symbol_name(
        self, value: object, context: str, template: AltiumSymbol | None
    ) -> str:
        name = value
        if not isinstance(name, str) or not name:
            raise ValueError(f"{context}.Name must be a non-empty string")
        self._validate_new_storage_name(name)
        if template is not None and name != template.name:
            raise ValueError(f"{context}.Name changes template symbol order")
        return name

    def _json_symbol_records(
        self,
        value: object,
        context: str,
        template: AltiumSymbol | None,
        blob_budget: JsonBlobBudget,
    ) -> list[dict[str, object]]:
        rows = json_object_rows(
            value,
            context=f"{context}.Objects",
            max_rows=self._read_limits.max_records_per_stream,
        )
        if template is None:
            self._reject_unrepresentable_scratch_symbol_images(rows, context)
        if template is not None and len(rows) != len(template.raw_records):
            raise ValueError(f"{context}.Objects changes template record inventory")
        records = [
            json_record_from_object(
                row,
                context=f"{context}.Objects[{row_index}]",
                max_binary_bytes=self._read_limits.max_record_bytes,
                blob_budget=blob_budget,
            )
            for row_index, row in enumerate(rows)
        ]
        if template is not None:
            self._validate_json_symbol_record_types(template, records, context)
        return records

    @staticmethod
    def _reject_unrepresentable_scratch_symbol_images(
        rows: tuple[Mapping[str, object], ...], context: str
    ) -> None:
        for index, row in enumerate(rows):
            if row.get("ObjectType") != SchJsonObjectType.IMAGE.value:
                continue
            value = _json_casefold_value(row, "EmbedImage", False)
            if (
                value is True
                or value == 1
                or (isinstance(value, str) and value.casefold() in {"t", "true", "1"})
            ):
                raise ValueError(
                    f"{context}.Objects[{index}] embeds an image but scratch JSON "
                    "has no payload"
                )

    @staticmethod
    def _validate_json_symbol_record_types(
        template: AltiumSymbol,
        records: list[dict[str, object]],
        context: str,
    ) -> None:
        for index, (current, replacement) in enumerate(
            zip(template.raw_records, records, strict=True)
        ):
            current_type = AltiumSchLib._json_record_number(
                current.get("RECORD"), context
            )
            replacement_type = AltiumSchLib._json_record_number(
                replacement.get("RECORD"), context
            )
            if current_type != replacement_type:
                raise ValueError(
                    f"{context}.Objects changes record type at index {index}"
                )

    @staticmethod
    def _json_record_number(value: object, context: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError(f"{context}.Objects has an invalid record type")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"{context}.Objects has an invalid record type") from exc

    def _build_json_symbol(
        self,
        name: str,
        records: list[dict[str, object]],
        value: Mapping[str, object],
        context: str,
        template: AltiumSymbol | None,
    ) -> AltiumSymbol:
        self._validate_symbol_data_records(records, f"{name}/Data")
        original_name = (
            str(template.original_name or template.name)
            if template is not None
            else self._json_original_symbol_name(records, name)
        )
        symbol = AltiumSymbol(name, original_name=original_name)
        for record in records:
            symbol.add_record(record, self.font_manager)
        self._apply_json_symbol_metadata(symbol, value, context)
        self._validate_symbol_owners(symbol, f"{name}/Data", strict_json=True)
        self._attach_pin_parameters(symbol)
        symbol._rebuild_implementation_structure()
        if template is not None:
            symbol._original_streams = dict(template._original_streams)
            self._transfer_json_symbol_image_payloads(template, symbol, context)
        symbol._set_unique_id_locked(True)
        return symbol

    @staticmethod
    def _transfer_json_symbol_image_payloads(
        template: AltiumSymbol, symbol: AltiumSymbol, context: str
    ) -> None:
        from .altium_record_sch__image import AltiumSchImage

        current_images = [
            value for value in template.objects if isinstance(value, AltiumSchImage)
        ]
        incoming_images = [
            value for value in symbol.objects if isinstance(value, AltiumSchImage)
        ]
        if len(current_images) != len(incoming_images):
            raise ValueError(f"{context}.Objects changes image topology")
        for index, (current, incoming) in enumerate(
            zip(current_images, incoming_images, strict=True)
        ):
            if current.embed_image != incoming.embed_image:
                raise ValueError(
                    f"{context}.Objects image {index} changes embedded topology"
                )
            if not current.embed_image:
                continue
            if not current.image_data:
                raise ValueError(
                    f"{context}.Objects image {index} has no retained payload"
                )
            incoming.image_data = current.image_data
            incoming.detect_format()

    @staticmethod
    def _json_original_symbol_name(
        records: list[dict[str, object]], fallback: str
    ) -> str:
        component = records[0]
        folded = {key.casefold(): value for key, value in component.items()}
        for field in ("libreference", "designitemid"):
            value = folded.get(field)
            if isinstance(value, str) and value:
                return value
        return fallback

    @staticmethod
    def _apply_json_symbol_metadata(
        symbol: AltiumSymbol,
        value: Mapping[str, object],
        context: str,
    ) -> None:
        description, part_count = AltiumSchLib._json_symbol_metadata(value, context)
        parsed_description = symbol.description
        parsed_part_count = symbol.part_count
        symbol.description = description
        symbol.part_count = part_count
        component = symbol.component_record
        if component is None:
            return
        AltiumSchLib._apply_json_component_description(
            component, description, parsed_description
        )
        AltiumSchLib._apply_json_component_part_count(
            component, part_count, parsed_part_count
        )

    @staticmethod
    def _json_symbol_metadata(
        value: Mapping[str, object], context: str
    ) -> tuple[str, int]:
        if "Description" not in value:
            raise ValueError(f"{context}.Description is required")
        if "PartCount" not in value:
            raise ValueError(f"{context}.PartCount is required")
        description = value["Description"]
        part_count = value["PartCount"]
        if not isinstance(description, str):
            raise ValueError(f"{context}.Description must be a string")
        if (
            isinstance(part_count, bool)
            or not isinstance(part_count, int)
            or part_count < 1
        ):
            raise ValueError(f"{context}.PartCount must be a positive integer")
        return description, part_count

    @staticmethod
    def _apply_json_component_description(
        component: dict[str, object], description: str, parsed_description: str
    ) -> None:
        description_keys = {
            "componentdescription",
            "%utf8%componentdescription",
        }
        field_present = any(key.casefold() in description_keys for key in component)
        if description != parsed_description or field_present:
            _replace_dynamic_text_field(component, "ComponentDescription", description)

    @staticmethod
    def _apply_json_component_part_count(
        component: dict[str, object], part_count: int, parsed_part_count: int
    ) -> None:
        part_count_key = next(
            (key for key in tuple(component) if key.casefold() == "partcount"),
            None,
        )
        if part_count_key is not None:
            component[part_count_key] = str(part_count + 1)
        elif part_count != parsed_part_count:
            component["PartCount"] = str(part_count + 1)

    def _commit_json_update(self, staged: object) -> None:
        if not isinstance(staged, AltiumSchLib):
            raise TypeError("staged JSON update must be an AltiumSchLib")
        old_symbols = list(self._symbols)
        staged_symbols = list(staged._symbols)
        for symbol in old_symbols:
            symbol._schematic_binding_context = None
            for record in symbol._owned_identity_records():
                unlock = getattr(record, "_set_unique_id_locked", None)
                if callable(unlock):
                    unlock(False)
                if hasattr(record, "_bound_schematic_context"):
                    _as_dynamic(record)._bound_schematic_context = None
        for name, value in staged.__dict__.items():
            if name not in {"_symbols", "_schematic_binding_context"}:
                setattr(self, name, value)
        self._symbols.clear()
        self._symbols.extend(staged_symbols)
        self._schematic_binding_context = SchematicBindingContext(self, kind="schlib")
        self._bind_all_symbols_to_context()

    def __repr__(self) -> str:
        return f"AltiumSchLib('{self.filename}', {len(self.symbols)} symbols)"
