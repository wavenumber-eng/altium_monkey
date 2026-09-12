"""
Parse `.SchDoc` schematic files into an object model.
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
import base64
import json
import zlib
from collections.abc import Collection, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any, Callable, Iterable, cast

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
from .altium_json_apply_helpers import (
    JsonBlobBudget,
    JsonApplyMixin,
    _json_casefold_value,
    json_object_rows,
    json_record_from_object,
    load_bounded_json_source,
)
from .altium_dotnet_ordinal import dotnet_ordinal_ignore_case_key
from .altium_sch_display_mode import (
    pin_belongs_to_component_view,
    pin_is_managed_part_member,
    pin_is_runtime_hidden,
    record_belongs_to_display_mode,
)
from .altium_sch_enums import PortStyle
from . import (
    AltiumSchArc,
    AltiumSchBezier,
    AltiumSchBlanket,
    AltiumSchBus,
    AltiumSchBusEntry,
    AltiumSchCompileMask,
    AltiumSchComponent,
    AltiumSchCrossSheetConnector,
    AltiumSchDesignator,
    AltiumSchEllipse,
    AltiumSchEllipticalArc,
    AltiumSchFileName,
    AltiumSchHarnessConnector,
    AltiumSchHarnessEntry,
    AltiumSchHarnessType,
    AltiumSchImage,
    AltiumSchImplementation,
    AltiumSchImplementationList,
    AltiumSchImplParams,
    AltiumSchMapDefiner,
    AltiumSchMapDefinerList,
    AltiumSchJunction,
    AltiumSchLabel,
    AltiumSchLine,
    AltiumSchNetLabel,
    AltiumSchNote,
    AltiumSchNoErc,
    AltiumSchParameter,
    AltiumSchParameterSet,
    AltiumSchPin,
    AltiumSchPolygon,
    AltiumSchPolyline,
    AltiumSchPort,
    AltiumSchPowerPort,
    AltiumSchRectangle,
    AltiumSchRoundedRectangle,
    AltiumSchSheet,
    AltiumSchSheetEntry,
    AltiumSchSheetName,
    AltiumSchSheetSymbol,
    AltiumSchSignalHarness,
    AltiumSchTemplate,
    AltiumSchTextFrame,
    AltiumSchWire,
)
from .altium_sch_record_factory import create_record_from_record
from .altium_record_sch__component import AltiumSchHarnessComponent
from ._altium_sch_bounds_accessibility import (
    _BoundsImportedAccessibility,
    _capture_bounds_import_accessibility,
)
from ._altium_sch_bounds_source_tree import _build_bounds_source_tree
from ._sch_source_admission import _SourceAdmission
from .altium_record_sch__parameter import (
    AltiumSchImageParameter,
)
from ._altium_record_sch__physical_model import (
    _AltiumSchHarnessCavity,
    _AltiumSchHarnessCavityComponent,
    _AltiumSchLineView,
)
from ._altium_record_sch__harness_layout import (
    AltiumSchHarnessBundle,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
    AltiumSchHarnessLayoutLabel,
    AltiumSchHarnessSplice,
    HarnessLayoutConnectionPointStyle,
    _HarnessCompileMaskIndex,
    _HarnessComponentIndex,
    _HarnessConnectionBundleIndex,
    _HarnessCoveringProjection,
    _HarnessCoveringTopologyIndex,
    _abs_safe_i32,
    _float_to_i32,
    _harness_bundle_child_operations,
    _harness_internal_location,
    _unchecked_i32_offset,
    _utf16_code_unit_prefix,
)
from ._altium_sch_covering_cache import (
    _MAX_COVERING_RENDER_CACHE_WORK,
    _CoveringCacheBudget,
    _prepare_covering_render,
)
from .altium_sch_harness_connection_points import (
    HarnessConnectionPointConnectorData,
    HarnessConnectionPointData,
    HarnessConnectionPointDataError,
    decode_harness_connection_point_stream,
    encode_harness_connection_point_stream,
)
from .altium_record_sch__object_definition import AltiumSchObjectDefinition
from .altium_sch_binding import SchematicBindingContext
from .altium_record_types import (
    CoordPoint,
    SchPrimitive,
    SchRecordType,
    _generate_available_unique_id,
)
from .altium_record_types import generate_unique_id as generate_sch_unique_id
from .altium_object_collection import ObjectCollection, ObjectCollectionView


from .altium_ole import AltiumOleFile, AltiumOleWriter
from .altium_sch_json_object_types import (
    SchJsonObjectType,
    sch_json_object_type_from_record,
    sch_record_type_from_json_object_type,
)
from .altium_sch_component_insert_helpers import (
    clone_symbol_children,
    load_or_cache_schlib,
    merge_schlib_fonts,
    remap_font_ids,
)
from .altium_sch_image_payload import (
    SchEmbeddedImageEntry,
    SchEmbeddedImageFormat,
    build_embedded_image_storage,
    decode_sch_embedded_image_payload,
    resolve_embedded_image_group,
)
from .altium_schdoc_container import (
    SchDocContainerError,
    _SchDocBudget,
    _SchDocReadLimits,
    _header_value,
    _parse_storage,
    _parse_warehouse,
    _record_id,
    _record_owner_index,
    _record_uses_additional_owner,
    _root_stream,
    _root_stream_path,
    _snapshot_container,
    _storage_weight_is_stale,
    _validate_fileheader_records,
    _validate_modeled_root_paths,
    _validate_stream_record_families,
)
from .altium_schdoc_info import (
    SchComponentInfo,
    SchCrossSheetConnectorInfo,
    SchHarnessInfo,
    SchNetLabelInfo,
    SchPinInfo,
    SchPortInfo,
    SchPowerPortInfo,
    SchSheetSymbolInfo,
)
from .altium_utilities import as_dynamic as _as_dynamic
from .altium_utilities import get_records_in_section


def _shorten_managed_title_block_path(file_path: str) -> str:
    """Mirror the managed title-block path shortening rule."""
    utf16_units = sum(2 if ord(character) > 0xFFFF else 1 for character in file_path)
    if utf16_units < 40:
        return file_path
    first = file_path.find("\\")
    if first < 0 or first >= len(file_path) - 1:
        return file_path
    second = file_path.find("\\", first + 1)
    last = file_path.rfind("\\")
    if second > 0 and second != last:
        return file_path[:second] + "\\.." + file_path[last:]
    return file_path


_MANAGED_SCHDOC_IGNORED_RECORD_IDS = frozenset({220, 221, 222, 223, 240, 241})

if TYPE_CHECKING:
    from ._altium_sch_component_project_state import _ComponentProjectRenderState
    from ._altium_sch_component_bounds_query import _ComponentBoundsQueries
    from ._altium_sch_component_bounds_query import (
        _BoundsLabelText,
        _BoundsParameterSetText,
    )
    from ._altium_sch_bounds_source_tree import _BoundsDocumentOwnerState
    from .altium_font_manager import FontIDManager, _FontSpec
    from .altium_sch_enums import Rotation90
    from .altium_schlib import AltiumSchLib, AltiumSymbol
    from .altium_sch_geometry_oracle import (
        SchGeometryBounds,
        SchGeometryDocument,
        SchGeometryOp,
        SchGeometryRecord,
        SchIrRenderProfile,
    )
    from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions

_MISSING_SYNC_VALUE = object()


@dataclass(frozen=True)
class _SchObjectSyncState:
    target: object
    owner_index: object
    index_in_sheet: object
    record_index: object
    owner_index_additional_list: object
    not_auto_position: object
    source_stream: object
    raw_record: dict[str, object] | None


@dataclass(frozen=True)
class _SchLocalSyncSnapshot:
    objects: tuple[_SchObjectSyncState, ...]
    file_weight: int | None
    preserve_loaded_indices: bool
    index_dirty: bool
    dirty_index_owner_ids: frozenset[int]
    weight_dirty: frozenset[str]


@dataclass(frozen=True)
class _SchAuthoringMutationSnapshot:
    bounds_import_accessibility: dict[SchPrimitive, _BoundsImportedAccessibility]
    objects: tuple[object, ...]
    local_sync: _SchLocalSyncSnapshot
    fonts: dict[int, "_FontSpec"]
    font_id_count: int


@dataclass(frozen=True)
class _SchDocCommitPlan:
    object_states: tuple[_SchObjectSyncState, ...]
    objects: ObjectCollection
    sheet: object
    fileheader_objects: list[object]
    additional_objects: list[object]
    definition_objects: list[object]
    fileheader_raw_records: list[dict[str, object]]
    additional_raw_records: list[dict[str, object]]
    normalized_owner_refs: dict[int, tuple[str, int] | None]
    fileheader_header: dict[str, object] | None
    additional_header: dict[str, object] | None
    object_definitions_header: dict[str, object] | None
    file_weight: int | None
    embedded_images: dict[str, bytes]
    source_streams: dict[str, bytes]
    source_storages: tuple[str, ...]
    raw_storage_entries: dict[str, tuple[bytes, bytes]]
    filepath: Path


log = logging.getLogger(__name__)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


_PIN_STATE_PARAMETER_NAMES = frozenset(
    {
        dotnet_ordinal_ignore_case_key("DefaultNet"),
        dotnet_ordinal_ignore_case_key("HiddenNetName"),
    }
)


def _apply_pin_state_parameters(objects: Iterable[object]) -> None:
    for obj in objects:
        if isinstance(obj, AltiumSchPin):
            obj.hidden_net_name = ""
        elif isinstance(obj, AltiumSchParameter):
            parent = getattr(obj, "parent", None)
            if isinstance(parent, AltiumSchPin) and (
                dotnet_ordinal_ignore_case_key(obj.name) in _PIN_STATE_PARAMETER_NAMES
            ):
                parent.hidden_net_name = obj.text


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _with_template_render_identity(
    record: "SchGeometryRecord",
    *,
    document_id: str,
    template_index: int,
    source_index: int,
    units_per_px: int,
) -> "SchGeometryRecord":
    """Give a UID-less template child a deterministic render-local identity."""
    if record.unique_id:
        return record

    from .altium_sch_geometry_oracle import (
        unwrap_record_operations,
        wrap_record_operations,
    )

    unique_id = f"TPL{template_index:05d}C{source_index:05d}"
    return replace(
        record,
        handle=f"{document_id}\\{unique_id}",
        unique_id=unique_id,
        operations=wrap_record_operations(
            unique_id,
            unwrap_record_operations(record),
            units_per_px=units_per_px,
        ),
    )


@dataclass(frozen=True)
class _PreparedProjectComponentBounds:
    query: _ComponentBoundsQueries
    index_by_source_id: Mapping[int, int]


@dataclass(frozen=True)
class _SchGeometryOwnershipState:
    template_obj: AltiumSchTemplate | None
    template_idx: int | None
    show_template_graphics: bool
    multipart_suffix_component_ids: frozenset[int]
    component_owner_indexes: set[int]
    pin_owner_indexes: set[int]
    source_admission: _SourceAdmission = _SourceAdmission()

    def has_projected_owner(self, obj: object) -> bool:
        parents = self.source_admission.parent_by_source_id
        return parents is not None and id(obj) in parents

    def owner_index_of(self, obj: object) -> int | None:
        owner_index = getattr(obj, "owner_index", None)
        if owner_index is None:
            return None
        try:
            return int(owner_index)
        except (TypeError, ValueError):
            return None

    def is_component_owned(self, obj: object) -> bool:
        if self.has_projected_owner(obj):
            return isinstance(self.source_admission.parent(obj), AltiumSchComponent)
        parent = getattr(obj, "parent", None)
        if parent is not None:
            return isinstance(parent, AltiumSchComponent)
        owner_index = self.owner_index_of(obj)
        return owner_index is not None and owner_index in self.component_owner_indexes

    def is_template_owned(self, obj: object) -> bool:
        if self.has_projected_owner(obj):
            return (
                self.template_obj is not None
                and self.source_admission.parent(obj) is self.template_obj
            )
        parent = getattr(obj, "parent", None)
        if parent is not None:
            return parent is self.template_obj
        owner_index = self.owner_index_of(obj)
        return (
            self.template_idx is not None
            and self.template_idx > 0
            and owner_index == self.template_idx
        )

    def is_pin_owned(self, obj: object) -> bool:
        if self.has_projected_owner(obj):
            return isinstance(self.source_admission.parent(obj), AltiumSchPin)
        parent = getattr(obj, "parent", None)
        if parent is not None:
            return isinstance(parent, AltiumSchPin)
        owner_index = self.owner_index_of(obj)
        return owner_index is not None and owner_index in self.pin_owner_indexes


@dataclass(frozen=True)
class _HarnessPhysicalModelProjection:
    parameter: AltiumSchImageParameter
    model: SchPrimitive | None
    model_record: "SchGeometryRecord | None"
    bounds: "SchGeometryBounds"


@dataclass(frozen=True)
class _SchSheetGeometrySetup:
    sheet_width_mils: float
    sheet_height_mils: float
    sheet_unique_id: str
    area_color: int
    use_custom_sheet: bool
    border_on: bool
    margin: float
    reference_zones_on: bool
    reference_zone_style: int
    title_block_on: bool
    x_zones: int
    y_zones: int
    units_per_px: int
    workspace_bottom_units: int
    outer_top_units: float
    outer_right_units: float
    has_explicit_border_on: bool

    @property
    def render_border_rects(self) -> bool:
        return self.border_on and (
            self.has_explicit_border_on
            or not (self.reference_zones_on and self.reference_zone_style == 1)
        )

    @property
    def working_margin(self) -> float:
        return self.margin if (self.reference_zones_on or self.title_block_on) else 0


COMPONENT_GRAPHIC_CHILD_TYPES = (
    AltiumSchLine,
    AltiumSchRectangle,
    AltiumSchRoundedRectangle,
    AltiumSchEllipse,
    AltiumSchArc,
    AltiumSchPolyline,
    AltiumSchPolygon,
    AltiumSchBezier,
    AltiumSchTextFrame,
    AltiumSchNoErc,
    AltiumSchParameterSet,
    AltiumSchCompileMask,
    AltiumSchBlanket,
    AltiumSchImage,
    AltiumSchLabel,
    AltiumSchNetLabel,
    AltiumSchPort,
    AltiumSchPowerPort,
    AltiumSchSheetSymbol,
    AltiumSchSheetEntry,
    AltiumSchHarnessEntry,
    AltiumSchHarnessConnector,
    AltiumSchSignalHarness,
    AltiumSchSheetName,
    AltiumSchFileName,
    AltiumSchDesignator,
    _AltiumSchHarnessCavity,
)


PARENT_BOUND_GEOMETRY_CHILD_TYPES = (AltiumSchSheetEntry, AltiumSchHarnessEntry)
HARNESS_LAYOUT_SPATIAL_CHILD_RECORD_TYPES = frozenset(
    {
        SchRecordType.COMPONENT,
        SchRecordType.HARNESS_COMPONENT,
        SchRecordType.PIN,
        SchRecordType.IEEE_SYMBOL,
        SchRecordType.LABEL,
        SchRecordType.BEZIER,
        SchRecordType.POLYLINE,
        SchRecordType.POLYGON,
        SchRecordType.ELLIPSE,
        SchRecordType.PIECHART,
        SchRecordType.ROUND_RECTANGLE,
        SchRecordType.ELLIPTICAL_ARC,
        SchRecordType.ARC,
        SchRecordType.LINE,
        SchRecordType.RECTANGLE,
        SchRecordType.SHEET_SYMBOL,
        SchRecordType.SHEET_ENTRY,
        SchRecordType.POWER_PORT,
        SchRecordType.PORT,
        SchRecordType.NO_ERC,
        SchRecordType.NET_LABEL,
        SchRecordType.BUS,
        SchRecordType.WIRE,
        SchRecordType.TEXT_FRAME,
        SchRecordType.JUNCTION,
        SchRecordType.IMAGE,
        SchRecordType.SHEET,
        SchRecordType.SHEET_NAME,
        SchRecordType.FILE_NAME,
        SchRecordType.DESIGNATOR,
        SchRecordType.BUS_ENTRY,
        SchRecordType.PARAMETER,
        SchRecordType.PARAMETER_SET,
        SchRecordType.HARNESS_SPLICE,
        SchRecordType.HARNESS_LAYOUT_LABEL,
        SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
        SchRecordType.HARNESS_BUNDLE,
        SchRecordType.HARNESS_LAYOUT_COVERING,
        SchRecordType.HARNESS_CAVITY,
        SchRecordType.HARNESS_CAVITY_COMPONENT,
        SchRecordType.NOTE,
        SchRecordType.COMPILE_MASK,
        SchRecordType.HARNESS_CONNECTOR,
        SchRecordType.HARNESS_ENTRY,
        SchRecordType.HARNESS_TYPE,
        SchRecordType.SIGNAL_HARNESS,
        SchRecordType.BLANKET,
        SchRecordType.HYPERLINK,
    }
)
HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES = (
    AltiumSchHarnessBundle,
    AltiumSchHarnessLayoutConnectionPoint,
    AltiumSchHarnessLayoutCovering,
    AltiumSchHarnessLayoutLabel,
    AltiumSchHarnessSplice,
)
HARNESS_LAYOUT_CHILD_CONTAINER_TYPES = (
    AltiumSchHarnessConnector,
    AltiumSchSheetSymbol,
)


def _is_parent_bound_geometry_child(obj: object) -> bool:
    # Harness entries and sheet entries are not standalone graphics: their
    # geometry is defined relative to a harness connector or sheet symbol and
    # their to_geometry methods require parent_* context. Public mirror issue
    # #18 included a synthetic SchDoc where a harness entry's Additional-stream
    # OwnerIndex collided with a component index. Altium opens that file but
    # does not render the orphan entry, so generic/component dispatch should
    # skip these malformed children instead of trying to recover or draw them.
    return isinstance(obj, PARENT_BOUND_GEOMETRY_CHILD_TYPES)


def _font_resolution_hint_sort_key(diagnostic: dict[str, object]) -> tuple[object, ...]:
    # Canonical ordering for render_hints.font_resolution diagnostics: the
    # resolver dedupe key (tried_families excluded). Both the Python and C++
    # IR builders emit this order so the hint does not depend on the order in
    # which each render pass first touches a font.
    def optional_text(value: object) -> tuple[bool, str]:
        return (value is not None, "" if value is None else str(value))

    return (
        str(diagnostic["requested_family"]),
        bool(diagnostic["requested_bold"]),
        bool(diagnostic["requested_italic"]),
        optional_text(diagnostic["resolved_family"]),
        optional_text(diagnostic["resolved_name"]),
        str(diagnostic["status"]),
        str(diagnostic["source"]),
        optional_text(diagnostic["path"]),
    )


def _schdoc_json_record(obj: object) -> dict[str, object] | None:
    serializer = getattr(obj, "serialize_to_record", None)
    value = serializer() if callable(serializer) else obj
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _schdoc_binary_json_object(
    record: dict[str, object], object_index: int
) -> dict[str, object] | None:
    binary_data = record.get("__BINARY_DATA__", b"")
    if not isinstance(binary_data, bytes | bytearray) or not binary_data:
        return None
    record_type = binary_data[0]
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


def _schdoc_json_scalar(value: object) -> object:
    if value == "T":
        return True
    if value == "F":
        return False
    if not isinstance(value, str):
        return value
    stripped = value.lstrip("-")
    if stripped.isdigit() and (len(stripped) == 1 or not stripped.startswith("0")):
        return int(value)
    return value


def _schdoc_text_json_object(
    record: dict[str, object], object_index: int
) -> dict[str, object] | None:
    record_num = record.get("RECORD")
    if record_num is None:
        if "HEADER" not in record:
            return None
        return {
            "ObjectType": SchJsonObjectType.FILE_HEADER.value,
            "ObjectIndex": object_index,
            **record,
        }
    object_type = sch_json_object_type_from_record(record)
    record_type_int = _optional_int(record_num)
    if object_type is None and record_type_int is None:
        return None
    type_name = (
        object_type.value if object_type is not None else f"Unknown_{record_type_int}"
    )
    result: dict[str, object] = {
        "ObjectType": type_name,
        "ObjectIndex": object_index,
    }
    result.update(
        (key, _schdoc_json_scalar(value))
        for key, value in record.items()
        if key != "RECORD"
    )
    return result


def _schdoc_json_object(obj: object, object_index: int) -> dict[str, object] | None:
    record = _schdoc_json_record(obj)
    if record is None:
        return None
    if record.get("__BINARY_RECORD__"):
        return _schdoc_binary_json_object(record, object_index)
    return _schdoc_text_json_object(record, object_index)


def _schdoc_json_fonts(
    fonts: Mapping[int, _FontSpec],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for font_id, info in sorted(fonts.items()):
        entry: dict[str, object] = {
            "FontID": font_id,
            "FontName": info.get("name", "Unknown"),
            "FontSize": info.get("size", 10),
        }
        if info.get("bold"):
            entry["Bold"] = True
        if info.get("italic"):
            entry["Italic"] = True
        result.append(entry)
    return result


# Clean SchDoc API - wrapper classes and helper functions


@public_api
class AltiumSchDoc(JsonApplyMixin):
    """
    Represents a complete .SchDoc schematic file.

    Attributes:
        filepath: Path to SchDoc file
        sheet: SHEET record (root container with font table)
        components: List of placed component instances (AltiumSchComponent with children)
        wires: List of wire connections
        buses: List of bus connections
        net_labels: List of net labels
        power_ports: List of power/ground symbols
        junctions: List of wire junctions
        ports: List of hierarchical ports
        sheet_symbols: List of hierarchical sheet boxes
        graphics: List of sheet-level graphical objects
        labels: List of sheet-level text labels
        embedded_images: Dict of embedded images
        all_objects: List of all parsed objects (preserves file order)
    """

    def __init__(
        self,
        filepath: Path | str | None = None,
        *,
        create_sheet: bool = True,
        debug: bool = False,
        _read_limits: _SchDocReadLimits | None = None,
    ) -> None:
        """
        Create an AltiumSchDoc.

                Args:
                    filepath: Path to .SchDoc binary file to parse.
                              If None, creates an empty schematic for authoring.
                    create_sheet: If True and no filepath, auto-create default Sheet.
                    debug: Enable debug output.
        """
        self.filepath: Path | None = None
        self._json_filename = "unknown.SchDoc"
        self._json_embedded_image_count = 0
        self.sheet: AltiumSchSheet | None = None

        # Single authoritative object collection. Typed access is exposed via properties below.
        self._objects = ObjectCollection()

        # Embedded data
        self.embedded_images: dict[str, bytes] = {}
        self._raw_storage_entries: dict[str, tuple[bytes, bytes]] = {}
        self._source_streams: dict[str, bytes] = {}
        self._source_storages: tuple[str, ...] = ()
        self._fileheader_header: dict[str, object] | None = None
        self._additional_header: dict[str, object] | None = None
        self._object_definitions_header: dict[str, object] | None = None
        self._fileheader_objects: list[object] = []
        self._additional_objects: list[object] = []
        self._fileheader_raw_records: list[dict[str, object]] = []
        self._additional_raw_records: list[dict[str, object]] = []
        self._object_definition_objects: list[object] = []
        self._normalized_owner_refs: dict[int, tuple[str, int] | None] = {}
        self._bounds_import_accessibility: dict[
            SchPrimitive, _BoundsImportedAccessibility
        ] = {}
        self._read_limits = (_read_limits or _SchDocReadLimits()).validate()
        self._schlib_cache: dict[Path, Any] = {}
        self._connection_points_cache: dict[
            tuple[
                bool,
                tuple[tuple[tuple[int, int], ...], ...],
                tuple[tuple[int, int], ...],
            ],
            frozenset[tuple[int, int]],
        ] = {}
        self._geometry_source_positions: dict[int, int] = {}
        self._geometry_render_group_ids: dict[int, str] = {}
        self._geometry_parent_by_source_id: dict[int, object | None] | None = None
        self._geometry_refresh_compile_mask_state = True

        # Object definitions for custom power port graphics
        self.object_definitions: dict[str, list[dict]] = {}
        self._object_definition_records: list[
            tuple[AltiumSchObjectDefinition, list[dict[str, Any]]]
        ] = []

        # File-level metadata (preserved for round-trip)
        self._file_unique_id: str | None = None  # UniqueID from FileHeader
        self._file_weight: int | None = (
            None  # Original Weight from FileHeader (for round-trip)
        )
        self._preserve_loaded_index_in_sheet: bool = False
        self._index_sync_dirty: bool = filepath is None
        self._dirty_index_owner_ids: set[int] = set()
        self._stream_weight_dirty: set[str] = set()

        if filepath is not None:
            # Parse binary file - delegate to from_file logic
            self._load_from_file(Path(filepath), debug=debug)
            self._preserve_loaded_index_in_sheet = True
        elif create_sheet:
            # Empty authoring mode with default sheet
            self._create_default_sheet()

    # -- Read-only live views over authoritative membership --
    @property
    def objects(self) -> ObjectCollectionView:
        return ObjectCollectionView(self._objects, lambda _obj: True)

    @property
    def all_objects(self) -> ObjectCollectionView:
        return ObjectCollectionView(self._objects, lambda _obj: True)

    # -- Typed convenience properties --

    def _object_view(self, predicate: Callable[[object], bool]) -> ObjectCollectionView:
        """
        Return a live read-only query view over the authoritative object store.
        """
        return ObjectCollectionView(self._objects, predicate)

    @property
    def components(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchComponent))

    @property
    def wires(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchWire)

    @property
    def buses(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBus)

    @property
    def bus_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBusEntry)

    @property
    def arcs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchArc)

    @property
    def lines(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLine)

    @property
    def rectangles(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchRectangle)

    @property
    def compile_masks(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchCompileMask)

    @property
    def rounded_rectangles(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchRoundedRectangle)

    @property
    def polygons(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPolygon)

    @property
    def blankets(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBlanket)

    @property
    def beziers(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchBezier)

    @property
    def polylines(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPolyline)

    @property
    def ellipses(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchEllipse)

    @property
    def elliptical_arcs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchEllipticalArc)

    @property
    def net_labels(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchNetLabel))

    @property
    def power_ports(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchPowerPort)

    @property
    def cross_sheet_connectors(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchCrossSheetConnector))

    @property
    def junctions(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchJunction))

    @property
    def ports(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchPort))

    @property
    def sheet_symbols(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetSymbol))

    @property
    def sheet_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetEntry))

    @property
    def sheet_names(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSheetName))

    @property
    def file_names(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchFileName))

    @property
    def labels(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLabel)

    @property
    def text_strings(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchLabel)

    @property
    def text_frames(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchTextFrame)

    @property
    def notes(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchNote))

    @property
    def no_ercs(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchNoErc)

    @property
    def parameter_sets(self) -> ObjectCollection:
        return self._object_view(lambda o: type(o) is AltiumSchParameterSet)

    @property
    def differential_pair_directives(self) -> ObjectCollection:
        return self._object_view(
            lambda o: isinstance(o, AltiumSchParameterSet) and o.is_differential_pair()
        )

    @property
    def parameters(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchParameter))

    @property
    def designators(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchDesignator))

    @property
    def images(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchImage))

    @staticmethod
    def _sanitize_embedded_asset_name(name: str, fallback: str) -> str:
        """
        Sanitize embedded asset names for stable filesystem extraction.
        """
        text = str(name or "").strip()
        if not text:
            text = fallback
        text = text.replace("\x00", "").strip()
        text = re.sub(r'[<>:"/\\|?*]+', "_", text)
        text = re.sub(r"\s+", "_", text).strip("._ ")
        return text or fallback

    @staticmethod
    def _embedded_image_extension_from_format(
        image_format: SchEmbeddedImageFormat | None,
    ) -> str | None:
        if image_format == SchEmbeddedImageFormat.PNG:
            return ".png"
        if image_format == SchEmbeddedImageFormat.BMP:
            return ".bmp"
        if image_format == SchEmbeddedImageFormat.JPEG:
            return ".jpg"
        if image_format == SchEmbeddedImageFormat.GIF:
            return ".gif"
        if image_format == SchEmbeddedImageFormat.WEBP:
            return ".webp"
        if image_format == SchEmbeddedImageFormat.SVG:
            return ".svg"
        return None

    @staticmethod
    def _embedded_image_payload_for_output(data: bytes) -> tuple[bytes, str | None]:
        """
        Return export-ready embedded-image bytes and the matching extension.

        Altium may store a placed IMAGE as a BMP preview followed by a native
        payload such as `TdxPNGImage`. This internal helper returns the native
        payload when one is present, otherwise it returns the raw storage bytes.

        Public callers should normally use `extract_embedded_images(...)`
        instead of writing `AltiumSchImage.image_data` directly.
        """
        payload = decode_sch_embedded_image_payload(data)
        extension = AltiumSchDoc._embedded_image_extension_from_format(
            payload.preferred_format
        )
        return payload.preferred_data, extension

    @staticmethod
    def _embedded_image_extension_from_data(data: bytes) -> str | None:
        """
        Return the extension for an export-ready embedded-image payload.

        This is a private compatibility helper for package internals. It follows
        the same wrapper-unwrapping rules as `_embedded_image_payload_for_output`.
        """
        _, extension = AltiumSchDoc._embedded_image_payload_for_output(data)
        return extension

    @staticmethod
    def _embedded_image_output_name(image: AltiumSchImage, index: int) -> str:
        fallback = f"image_{index:03d}"
        original_name = PureWindowsPath(str(image.filename or "")).name
        stem = Path(original_name).stem if original_name else fallback
        stem = AltiumSchDoc._sanitize_embedded_asset_name(stem, fallback)

        data = image.image_data or b""
        _, extension = AltiumSchDoc._embedded_image_payload_for_output(data)
        if extension is None and original_name:
            original_extension = Path(original_name).suffix.lower()
            if original_extension in {
                ".bmp",
                ".gif",
                ".jpg",
                ".jpeg",
                ".png",
                ".svg",
                ".webp",
            }:
                extension = (
                    ".jpg" if original_extension == ".jpeg" else original_extension
                )
        extension = extension or ".bin"
        return f"{index:03d}__{stem}{extension}"

    def extract_embedded_images(
        self,
        output_dir: Path | str,
        *,
        verbose: bool = False,
    ) -> list[Path]:
        """
        Extract embedded schematic IMAGE payloads to `output_dir`.

        Files are written as `<index:03d>__<source stem>.<detected ext>`.
        Altium wrapper payloads are unwrapped first, so a BMP preview followed
        by `TdxPNGImage` extracts as the native PNG bytes. Plain BMP payloads
        remain BMP.

        Linked image records without embedded payload bytes are skipped.

        This is the public API to use when exporting image files. Direct access
        to `AltiumSchImage.image_data` is intended for preservation and
        round-trip work because it may contain Altium wrapper bytes rather than
        a standalone image file.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, image in enumerate(self.images, start=1):
            if not image.image_data:
                if verbose:
                    log.info(
                        "Skipping image without embedded payload: %s",
                        image.filename or f"image_{index:03d}",
                    )
                continue
            filename = self._embedded_image_output_name(image, index)
            image_path = output_path / filename
            output_data, _ = self._embedded_image_payload_for_output(image.image_data)
            image_path.write_bytes(output_data)
            written.append(image_path)
            if verbose:
                log.info("Extracted embedded image: %s", image_path.name)

        return written

    @property
    def harness_connectors(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessConnector))

    @property
    def harness_entries(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessEntry))

    @property
    def harness_types(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchHarnessType))

    @property
    def signal_harnesses(self) -> ObjectCollection:
        return self._object_view(lambda o: isinstance(o, AltiumSchSignalHarness))

    @property
    def graphics(self) -> ObjectCollection:
        """
        All graphical primitives (lines, rectangles, arcs, etc.).
        """
        return self._object_view(
            lambda o: isinstance(
                o,
                (
                    AltiumSchLine,
                    AltiumSchRectangle,
                    AltiumSchRoundedRectangle,
                    AltiumSchEllipse,
                    AltiumSchArc,
                    AltiumSchPolyline,
                    AltiumSchPolygon,
                    AltiumSchBezier,
                ),
            ),
        )

    def _create_default_sheet(self) -> None:
        """
        Create a default Sheet with reasonable defaults.

        Matches Altium File->New pattern:
        - A size sheet (9.5" x 7.5")
        - No title block
        - Reference zones on
        - Standard grids (10 mil snap/visible)
        - Font 1: Times New Roman 10pt
        """
        import random
        import string

        # Create sheet with defaults (AltiumSchSheet has good defaults)
        sheet = AltiumSchSheet()
        sheet.sheet_style = 5  # A size (9.5" x 7.5")
        sheet.title_block_on = False
        sheet.reference_zones_on = True
        sheet.owner_index = 0  # Sheet is root (owned by file header)
        sheet.index_in_sheet = -1

        self.sheet = sheet
        self._objects.append(sheet)

        # Add default system PARAMETER records (required by Altium)
        self._add_default_parameters()

        # Generate unique ID for file header
        self._file_unique_id = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=8)
        )

    def _binding_context(self) -> SchematicBindingContext:
        """
        Get the narrow schematic binding context for this document.
        """
        context = getattr(self, "_schematic_binding_context", None)
        if context is None:
            context = SchematicBindingContext(self, kind="schdoc")
            self._schematic_binding_context = context
        return context

    def _bind_schematic_object(self, obj: Any) -> None:
        """
        Bind an object to this document's schematic context.
        """
        bind_hook = getattr(obj, "_bind_to_schematic_context", None)
        if callable(bind_hook):
            bind_hook(self._binding_context())

    def _bind_all_objects_to_context(self) -> None:
        """
        Bind every document object to this document context.
        """
        for obj in self.all_objects:
            self._bind_schematic_object(obj)

    def _lock_loaded_object_identities(self) -> None:
        """Apply the managed identity lock after document reconstruction."""
        for obj in self.all_objects:
            self._set_managed_unique_id_lock(obj, True)

    def _sync_embedded_images_from_objects(self) -> None:
        """
        Rebuild the embedded-image storage map from current IMAGE objects.

        Public mutation creates and edits real ``AltiumSchImage`` objects.
        Before save, synchronize the storage-stream payloads from those objects
        so ``add_object(make_sch_embedded_image(...))`` is the canonical public
        write path.
        """
        entries = (
            SchEmbeddedImageEntry(
                filename=image.filename,
                orientation=int(image.orientation),
                data=image.image_data,
            )
            for image in self.images
            if isinstance(image, AltiumSchImage)
            and image.embed_image
            and image.filename
            and image.image_data
        )
        synced_images = build_embedded_image_storage(entries)
        self.embedded_images = synced_images
        self._raw_storage_entries = {
            filename: entry
            for filename, entry in self._raw_storage_entries.items()
            if filename in synced_images
        }

    def _add_default_parameters(self) -> None:
        """
        Add default system PARAMETER records.

        Altium SchDoc files require system parameters like CurrentTime, Author, etc.
        Without these, Altium may freeze or fail to load the file.
        """
        from .altium_record_sch__parameter import AltiumSchParameter

        # System parameters required by Altium SchDoc format
        # These are the parameters found in a standard Altium-created SchDoc file
        default_params = [
            "CurrentTime",
            "CurrentDate",
            "Time",
            "Date",
            "DocumentFullPathAndName",
            "DocumentName",
            "ModifiedDate",
            "ApprovedBy",
            "CheckedBy",
            "Author",
            "CompanyName",
            "DrawnBy",
            "Engineer",
            "Organization",
            "Address1",
            "Address2",
            "Address3",
            "Address4",
            "Title",
            "DocumentNumber",
            "Revision",
            "SheetNumber",
            "SheetTotal",
            "Rule",
            "ImagePath",
            "ProjectName",
            "Application_BuildNumber",
        ]

        for i, name in enumerate(default_params):
            param = AltiumSchParameter()
            param.name = name
            param.text = ""  # Empty value by default
            param.owner_index = 0  # All owned by sheet (at position 0)
            _as_dynamic(param)._use_pascal_case = True  # SchDoc uses PascalCase

            # IndexInSheet: first param omits the persisted field, rest are sequential.
            if i == 0:
                param.index_in_sheet = -2
            else:
                param.index_in_sheet = i

            self._objects.append(param)

    @classmethod
    def _from_schdoc(cls, filepath: Path, debug: bool = False) -> AltiumSchDoc:
        """
        Parse a SchDoc file (convenience method for round-trip testing).

        This is the primary API method for loading SchDoc files.
        Use this method for all file loading operations.

        Args:
            filepath: Path to .SchDoc file
            debug: Enable debug output

        Returns:
            AltiumSchDoc instance with parsed data
        """
        return cls(filepath, debug=debug)

    def _load_from_file(self, filepath: Path, debug: bool = False) -> None:
        """
        Parse a SchDoc file into this instance.
        """
        if not filepath.exists():
            raise FileNotFoundError(f"SchDoc file not found: {filepath}")

        size = filepath.stat().st_size
        if size > self._read_limits.max_container_bytes:
            raise SchDocContainerError(
                "limit", "SchDoc exceeds the reviewed container byte limit"
            )
        log.debug("Parsing SchDoc file: %s", filepath.name)
        self.filepath = filepath
        with AltiumOleFile(filepath) as ole:
            self._source_streams, self._source_storages = _snapshot_container(
                ole, size, self._read_limits
            )
        _validate_modeled_root_paths(self._source_streams, self._source_storages)

        budget = _SchDocBudget(self._read_limits)
        fileheader_data = _root_stream(self._source_streams, "FileHeader")
        if fileheader_data is None:
            raise SchDocContainerError(
                "missing", "SchDoc is missing FileHeader", stream="FileHeader"
            )
        fileheader = _parse_warehouse(
            fileheader_data, "FileHeader", budget, weight_required=True
        )
        _validate_fileheader_records(fileheader.records)

        additional_data = _root_stream(self._source_streams, "Additional")
        additional = None
        if additional_data is not None:
            additional = _parse_warehouse(
                additional_data, "Additional", budget, weight_required=False
            )

        storage_entries = _parse_storage(
            _root_stream(self._source_streams, "Storage"), budget
        )

        definitions_data = _root_stream(self._source_streams, "ObjectDefinitions")
        definitions = None
        if definitions_data is not None:
            definitions = _parse_warehouse(
                definitions_data,
                "ObjectDefinitions",
                budget,
                weight_required=False,
            )

        fileheader_records = self._managed_import_records(
            fileheader.records, stream="FileHeader"
        )
        additional_records = self._managed_import_records(
            additional.records if additional is not None else (),
            stream="Additional",
        )
        _validate_stream_record_families(
            tuple(fileheader_records),
            tuple(additional_records),
            definitions.records if definitions is not None else (),
        )

        self._fileheader_header = fileheader.header
        self._file_weight = fileheader.stored_weight
        unique_id = _header_value(fileheader.header, "UniqueID")
        self._file_unique_id = str(unique_id) if unique_id else None
        self._parse_records(fileheader_records, debug, source_stream="FileHeader")
        self._fileheader_raw_records = [dict(record) for record in fileheader_records]
        self._fileheader_objects = list(self.all_objects)

        if additional is not None:
            self._additional_header = additional.header
            start = len(self.all_objects)
            self._parse_records(additional_records, debug, source_stream="Additional")
            self._additional_raw_records = [
                dict(record) for record in additional_records
            ]
            self._additional_objects = list(self.all_objects)[start:]

        self._hydrate_harness_connection_point_connectors()

        self.embedded_images = {entry.name: entry.data for entry in storage_entries}
        self._raw_storage_entries = {
            entry.name: (entry.binary_header, entry.compressed_data)
            for entry in storage_entries
        }

        if definitions is not None:
            self._object_definitions_header = definitions.header
            self._parse_object_definition_records(
                list(definitions.records), debug=debug
            )

        self._validate_and_assign_owner_refs()
        self._capture_base_bounds_accessibility()
        self._hydrate_pin_state_parameters()

        # Build component hierarchy (assign children to components based on OwnerIndex)
        self._build_component_hierarchy(debug)

        # Build ParameterSet hierarchy (assign child parameters based on OwnerIndex)
        self._build_parameterset_hierarchy(debug)

        # Build Implementation hierarchy (ImplementationList -> Implementation)
        self._build_implementation_hierarchy(debug)

        # Build harness and sheet symbol hierarchy
        self._build_harness_and_sheet_hierarchy(debug)

        # Link images with embedded data
        self._link_embedded_images(debug)

        # Set parent references for any remaining objects not covered above.
        self._set_all_parent_references(debug)
        self._hydrate_harness_physical_models()
        self._refresh_harness_bundle_field_identities()
        self._lock_loaded_object_identities()
        self._bind_all_objects_to_context()

        log.debug(
            f"  Parsed successfully: {len(self.all_objects)} total objects, "
            f"{len(self.components)} components"
        )

    @staticmethod
    def _managed_import_records(
        records: Iterable[dict[str, object]], *, stream: str
    ) -> list[dict[str, object]]:
        """Apply the managed document importer's context-only record skip."""
        return [
            record
            for index, record in enumerate(records)
            if _record_id(record, stream, index)
            not in _MANAGED_SCHDOC_IGNORED_RECORD_IDS
        ]

    def _hydrate_harness_connection_point_connectors(self) -> None:
        stream = _root_stream(
            self._source_streams,
            "HarnessConnectionPointConnector",
        )
        if stream is None:
            return
        try:
            rows = decode_harness_connection_point_stream(stream)
        except HarnessConnectionPointDataError as exc:
            raise SchDocContainerError(
                "malformed",
                str(exc),
                stream="HarnessConnectionPointConnector",
            ) from exc
        if not rows:
            return
        by_uid = self._harness_connection_points_by_uid()
        self._apply_harness_connection_point_rows(by_uid, rows)

    def _harness_connection_points_by_uid(
        self,
    ) -> dict[str, AltiumSchHarnessLayoutConnectionPoint]:
        by_uid: dict[str, AltiumSchHarnessLayoutConnectionPoint] = {}
        for obj in self.all_objects:
            if type(obj) is not AltiumSchHarnessLayoutConnectionPoint:
                continue
            unique_id = str(obj.unique_id or "")
            if unique_id in by_uid:
                raise SchDocContainerError(
                    "malformed",
                    f"duplicate connection-point UniqueID {unique_id!r}",
                    stream="HarnessConnectionPointConnector",
                )
            by_uid[unique_id] = obj
        return by_uid

    @staticmethod
    def _apply_harness_connection_point_rows(
        by_uid: Mapping[str, AltiumSchHarnessLayoutConnectionPoint],
        rows: Iterable[HarnessConnectionPointData],
    ) -> None:
        for row in rows:
            connection_point = by_uid.get(row.connection_point_id)
            if connection_point is None:
                continue
            for connector_row in row.connectors:
                connector = connection_point.add_connector(connector_row.connector_id)
                for pin_id in connector_row.pin_ids:
                    connector.add_pin(pin_id)

    def _parse_object_definitions(self, ole: Any, debug: bool = False) -> None:
        """
        Parse ObjectDefinitions stream for custom power port/connector graphics.

                The ObjectDefinitions stream contains object-definition headers
                followed by child primitive records (Lines, Polygons, etc.) in
                local coordinates. These define custom graphics for power ports
                that have an ObjectDefinitionId GUID.

                Structure:
                    [0] Header record
                    [1] ObjectDefinition {ObjectDefinitionId=GUID}
                    [2..N] Child primitives (Line, Polygon, etc.)
                    [N+1] Next definition header (if multiple)
        """
        records = get_records_in_section(ole, "ObjectDefinitions")
        if not records:
            return

        self.object_definitions = {}
        self._object_definition_records = []
        current_definition: AltiumSchObjectDefinition | None = None
        current_children: list[dict[str, Any]] = []

        for rec in records[1:]:  # Skip header
            record_type = rec.get("RECORD", "")
            if str(record_type) == "129":
                self._remember_object_definition(current_definition, current_children)
                current_definition = AltiumSchObjectDefinition()
                current_definition.parse_from_record(rec)
                current_children = []
                if debug:
                    log.debug(
                        "    ObjectDefinition: %s (%s)",
                        current_definition.lib_reference,
                        current_definition.definition_id,
                    )
            else:
                current_children.append(rec)

        self._remember_object_definition(current_definition, current_children)

        if self.object_definitions:
            log.debug(f"    Found {len(self.object_definitions)} object definitions")

    def _parse_object_definition_records(
        self,
        records: list[dict[str, object]],
        *,
        debug: bool = False,
    ) -> None:
        """Parse the isolated ObjectDefinitions warehouse without opaque fallback."""
        self.object_definitions = {}
        self._object_definition_records = []
        self._object_definition_objects = []
        current_definition: AltiumSchObjectDefinition | None = None
        current_children: list[dict[str, Any]] = []
        current_root_index: int | None = None
        depths: list[int] = []

        for index, record in enumerate(records):
            record_id = _record_id(record, "ObjectDefinitions", index)
            obj = self._parse_definition_object(record, record_id, index)

            if record_id == SchRecordType.OBJECT_DEFINITION:
                self._validate_definition_root(record, index)
                self._remember_object_definition(current_definition, current_children)
                current_definition = cast(AltiumSchObjectDefinition, obj)
                current_children = []
                current_root_index = index
                depths.append(0)
                self._normalized_owner_refs[id(obj)] = None
            else:
                parent, owner, depth = self._definition_child_parent(
                    record, index, current_root_index, depths
                )
                depths.append(depth)
                self._normalized_owner_refs[id(obj)] = ("ObjectDefinitions", owner)
                if hasattr(obj, "parent"):
                    _as_dynamic(obj).parent = parent
                current_children.append(dict(record))
            self._object_definition_objects.append(obj)

        self._remember_object_definition(current_definition, current_children)
        if debug and self.object_definitions:
            log.debug("    Found %d object definitions", len(self.object_definitions))

    def _parse_definition_object(
        self, record: dict[str, object], record_id: int, index: int
    ) -> object:
        obj = create_record_from_record(record)
        if obj is None:
            raise SchDocContainerError(
                "unsupported",
                f"unsupported ObjectDefinitions RECORD {record_id}",
                stream="ObjectDefinitions",
                record_index=index,
            )
        try:
            obj.parse_from_record(record, font_manager=self.font_manager)
        except Exception as exc:
            raise SchDocContainerError(
                "malformed",
                f"unable to parse ObjectDefinitions object: {exc}",
                stream="ObjectDefinitions",
                record_index=index,
            ) from exc
        _as_dynamic(obj)._source_stream = "ObjectDefinitions"
        _as_dynamic(obj)._stream_record_index = index
        _as_dynamic(obj)._record_index = index
        return obj

    @staticmethod
    def _validate_definition_root(record: dict[str, object], index: int) -> None:
        owner = _record_owner_index(record, "ObjectDefinitions", index, default=-1)
        if owner != -1:
            raise SchDocContainerError(
                "owner",
                "ObjectDefinition root OwnerIndex must be absent or -1",
                stream="ObjectDefinitions",
                record_index=index,
            )

    def _definition_child_parent(
        self,
        record: dict[str, object],
        index: int,
        current_root_index: int | None,
        depths: list[int],
    ) -> tuple[object, int, int]:
        owner = _record_owner_index(record, "ObjectDefinitions", index, default=0)
        if owner < 0 or owner >= index or current_root_index is None:
            raise SchDocContainerError(
                "owner",
                "ObjectDefinitions child must reference an earlier local object",
                stream="ObjectDefinitions",
                record_index=index,
            )
        if owner < current_root_index:
            raise SchDocContainerError(
                "owner",
                "ObjectDefinitions child crosses definition groups",
                stream="ObjectDefinitions",
                record_index=index,
            )
        depth = depths[owner] + 1
        if depth > self._read_limits.max_ownership_depth:
            raise SchDocContainerError(
                "limit",
                "ObjectDefinitions ownership depth exceeds the reviewed limit",
                stream="ObjectDefinitions",
                record_index=index,
            )
        return self._object_definition_objects[owner], owner, depth

    def _capture_base_bounds_accessibility(self) -> None:
        """Capture the validated Base phase before combined hierarchy binding."""
        self._bounds_import_accessibility = {}
        if self.sheet is None or self.sheet.is_boc:
            return
        sources = self._fileheader_objects
        calls = [
            index
            for index, source in enumerate(sources)
            if type(source) in (AltiumSchComponent, AltiumSchHarnessComponent)
        ]
        if not calls:
            return
        parents: dict[int, object | None] = {}
        for source in sources:
            reference = self._normalized_owner_refs[id(source)]
            if reference is None:
                parents[id(source)] = None
            else:
                stream, index = reference
                if stream != "FileHeader":
                    raise ValueError("Base bounds ownership crosses physical streams")
                parents[id(source)] = sources[index]
        limit = self._read_limits.max_total_records_per_document
        tree = _build_bounds_source_tree(sources, parents, max_sources=limit)
        self._bounds_import_accessibility = _capture_bounds_import_accessibility(
            tree, calls, max_calls=limit
        )

    def _validate_and_assign_owner_refs(self) -> None:
        """Validate the two physical owner domains and retain normalized references."""
        definition_ids = {id(obj) for obj in self._object_definition_objects}
        self._normalized_owner_refs = {
            object_id: reference
            for object_id, reference in self._normalized_owner_refs.items()
            if object_id in definition_ids
        }
        base_depths = self._validate_owner_vector(
            self._fileheader_objects,
            self._fileheader_raw_records,
            stream="FileHeader",
            base_objects=None,
            base_depths=None,
        )
        self._validate_owner_vector(
            self._additional_objects,
            self._additional_raw_records,
            stream="Additional",
            base_objects=self._fileheader_objects,
            base_depths=base_depths,
        )

    def _validate_owner_vector(
        self,
        objects: list[object],
        records: list[dict[str, object]],
        *,
        stream: str,
        base_objects: list[object] | None,
        base_depths: list[int] | None,
    ) -> list[int]:
        if len(objects) != len(records):
            raise SchDocContainerError(
                "malformed", f"{stream} object and frame counts differ", stream=stream
            )
        depths: list[int] = []
        for index, (obj, record) in enumerate(zip(objects, records, strict=True)):
            _as_dynamic(obj)._stream_record_index = index
            if stream == "FileHeader" and index == 0:
                self._validate_sheet_owner(record, index)
                self._normalized_owner_refs[id(obj)] = None
                depths.append(0)
                continue

            owner = _record_owner_index(record, stream, index, default=0)
            target_objects, target_depths, target_stream, upper_bound = (
                self._owner_target_domain(
                    stream,
                    record,
                    index,
                    objects,
                    depths,
                    base_objects,
                    base_depths,
                )
            )
            self._validate_owner_position(
                owner, upper_bound, stream, target_stream, index
            )
            parent = target_objects[owner]
            if not self._is_valid_owner_target(parent):
                raise SchDocContainerError(
                    "owner",
                    f"{stream} OwnerIndex targets a non-container object",
                    stream=stream,
                    record_index=index,
                )
            depth = target_depths[owner] + 1
            if depth > self._read_limits.max_ownership_depth:
                raise SchDocContainerError(
                    "limit",
                    f"{stream} ownership depth exceeds the reviewed limit",
                    stream=stream,
                    record_index=index,
                )
            depths.append(depth)
            self._normalized_owner_refs[id(obj)] = (target_stream, owner)
            if hasattr(obj, "parent") and not isinstance(parent, AltiumSchSheet):
                _as_dynamic(obj).parent = parent
        return depths

    @staticmethod
    def _validate_sheet_owner(record: dict[str, object], index: int) -> None:
        owner = _record_owner_index(record, "FileHeader", index, default=0)
        if owner not in (-1, 0):
            raise SchDocContainerError(
                "owner",
                "Sheet OwnerIndex must be absent, -1, or 0",
                stream="FileHeader",
                record_index=index,
            )

    @staticmethod
    def _owner_target_domain(
        stream: str,
        record: dict[str, object],
        index: int,
        objects: list[object],
        depths: list[int],
        base_objects: list[object] | None,
        base_depths: list[int] | None,
    ) -> tuple[list[object], list[int], str, int]:
        if stream == "Additional" and _record_uses_additional_owner(record):
            return objects, depths, "Additional", index
        target_objects = objects if base_objects is None else base_objects
        target_depths = depths if base_depths is None else base_depths
        upper_bound = index if base_objects is None else len(target_objects)
        return target_objects, target_depths, "FileHeader", upper_bound

    @staticmethod
    def _validate_owner_position(
        owner: int,
        upper_bound: int,
        stream: str,
        target_stream: str,
        index: int,
    ) -> None:
        if owner < 0 or owner >= upper_bound:
            raise SchDocContainerError(
                "owner",
                f"{stream} OwnerIndex does not resolve in {target_stream}",
                stream=stream,
                record_index=index,
            )

    @staticmethod
    def _is_valid_owner_target(obj: object) -> bool:
        record_type = getattr(obj, "record_type", None)
        return record_type in {
            SchRecordType.COMPONENT,
            SchRecordType.HARNESS_COMPONENT,
            SchRecordType.PIN,
            SchRecordType.SHEET_SYMBOL,
            SchRecordType.SHEET,
            SchRecordType.TEMPLATE,
            SchRecordType.PARAMETER_SET,
            SchRecordType.IMPLEMENTATION_LIST,
            SchRecordType.IMPLEMENTATION,
            SchRecordType.MAP_DEFINER_LIST,
            SchRecordType.IMPL_PARAMS,
            SchRecordType.PORT,
            SchRecordType.HARNESS_CONNECTOR,
            SchRecordType.HARNESS_SPLICE,
            SchRecordType.HARNESS_LAYOUT_LABEL,
            SchRecordType.HARNESS_LAYOUT_CONNECTION_POINT,
            SchRecordType.HARNESS_BUNDLE,
            SchRecordType.HARNESS_LAYOUT_COVERING,
            SchRecordType.HARNESS_CAVITY_COMPONENT,
        } or isinstance(obj, AltiumSchImageParameter)

    def _remember_object_definition(
        self,
        definition: AltiumSchObjectDefinition | None,
        children: list[dict[str, object]],
    ) -> None:
        if definition is None:
            return
        preserved_children = list(children)
        self._object_definition_records.append((definition, preserved_children))
        if definition.definition_id:
            self.object_definitions[definition.definition_id] = preserved_children

    def _parse_records(
        self,
        records: list[dict[str, Any]],
        debug: bool = False,
        source_stream: str = "FileHeader",
    ) -> None:
        """
        Parse records and categorize by type.

        This is a first pass that parses all records. The component
        hierarchy is built in a second pass (_build_component_hierarchy).

        Args:
            records: List of record dictionaries
            debug: Enable debug output
            source_stream: Which stream these records came from ('FileHeader' or 'Additional')
        """
        for _idx, record in enumerate(records):
            try:
                record_type_id = _record_id(record, source_stream, _idx)
                try:
                    record_type = SchRecordType(record_type_id)
                except ValueError as exc:
                    raise SchDocContainerError(
                        "unsupported",
                        f"unsupported SchDoc RECORD {record_type_id}",
                        stream=source_stream,
                        record_index=_idx,
                    ) from exc

                # Parse based on record type
                if record_type == SchRecordType.SHEET:
                    # SHEET record (root container)
                    sheet = AltiumSchSheet()
                    sheet.parse_from_record(record)
                    _as_dynamic(sheet)._record_index = len(self.all_objects)
                    _as_dynamic(sheet)._raw_record_index = (
                        _idx + 1
                    )  # +1 because we skip header record
                    _as_dynamic(
                        sheet
                    )._source_stream = (
                        source_stream  # Track which stream this came from
                    )
                    self.sheet = sheet
                    self._objects.append(sheet)

                    # Set up font ID translation for deduplication
                    # This maps file font IDs to internal IDs
                    if self.sheet.fonts:
                        self.font_manager.setup_in_translator(self.sheet.fonts)

                elif record_type == SchRecordType.COMPONENT:
                    # Component instance - parse as OOP object
                    component = AltiumSchComponent()
                    component.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(component)._record_index = len(self.all_objects)
                    _as_dynamic(component)._source_stream = source_stream
                    self._objects.append(component)

                elif record_type == SchRecordType.PIN:
                    # PIN instance - parse into AltiumSchPin object
                    pin = AltiumSchPin()
                    pin.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(pin)._record_index = len(self.all_objects)
                    _as_dynamic(pin)._source_stream = source_stream
                    self._objects.append(pin)

                elif record_type == SchRecordType.WIRE:
                    wire = AltiumSchWire()
                    wire.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(wire)._record_index = len(self.all_objects)
                    _as_dynamic(wire)._source_stream = source_stream
                    self._objects.append(wire)

                elif record_type == SchRecordType.BUS:
                    bus = AltiumSchBus()
                    bus.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(bus)._record_index = len(self.all_objects)
                    _as_dynamic(bus)._source_stream = source_stream
                    self._objects.append(bus)

                elif record_type == SchRecordType.IMAGE:
                    image = AltiumSchImage()
                    image.parse_from_record(record, font_manager=self.font_manager)
                    _as_dynamic(image)._record_index = len(self.all_objects)
                    _as_dynamic(image)._source_stream = source_stream
                    # Store raw record for image type classification
                    image._raw_record = record
                    self._objects.append(image)

                else:
                    # Try to create OOP record object
                    obj = create_record_from_record(record)

                    if obj:
                        # Parse using OOP class with font translation support
                        obj.parse_from_record(record, font_manager=self.font_manager)
                        _as_dynamic(obj)._record_index = len(self.all_objects)
                        _as_dynamic(obj)._source_stream = source_stream
                        self._objects.append(obj)

                        # Typed access is provided via properties; no separate lists are maintained.

                    else:
                        raise SchDocContainerError(
                            "unsupported",
                            f"unsupported SchDoc RECORD {record_type_id}",
                            stream=source_stream,
                            record_index=_idx,
                        )

            except Exception as e:
                if debug:
                    log.error(f"    Error parsing record: {e}")
                    log.error(f"      Record: {record}")
                if isinstance(e, SchDocContainerError):
                    raise
                raise SchDocContainerError(
                    "malformed",
                    f"unable to parse SchDoc object: {e}",
                    stream=source_stream,
                    record_index=_idx,
                ) from e

    def _build_component_hierarchy(self, debug: bool = False) -> None:
        """
        Build component-child hierarchy using OwnerIndex.

        In SchDoc files, objects have an OwnerIndex field that points to
        their parent object's index. Components own their pins, graphics,
        labels, etc.

        This allows extracting complete symbols from schematics.
        """
        if debug:
            log.info("  Building component hierarchy...")

        for comp in self.components:
            comp.pins.clear()
            comp.parameters.clear()
            comp.graphics.clear()
            comp.children.clear()
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchComponent):
                parent_comp = getattr(obj, "parent", None)
                if (
                    isinstance(obj, _AltiumSchHarnessCavityComponent)
                    and isinstance(parent_comp, AltiumSchComponent)
                    and obj not in parent_comp.children
                ):
                    parent_comp.children.append(obj)
                continue
            parent_comp = getattr(obj, "parent", None)
            if not isinstance(parent_comp, AltiumSchComponent):
                continue
            if _is_parent_bound_geometry_child(obj):
                continue
            if obj not in parent_comp.children:
                parent_comp.children.append(obj)
            if isinstance(obj, AltiumSchPin):
                parent_comp.pins.append(obj)
            elif isinstance(
                obj,
                (
                    AltiumSchParameter,
                    AltiumSchDesignator,
                    AltiumSchImplementationList,
                ),
            ):
                parent_comp.parameters.append(obj)
            elif isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
                parent_comp.graphics.append(obj)

        if debug:
            for comp in self.components:
                log.info(
                    f"    {comp.lib_reference}: {len(comp.pins)} pins, "
                    f"{len(comp.parameters)} params"
                )

    def _hydrate_pin_state_parameters(self) -> None:
        _apply_pin_state_parameters(self.all_objects)

    def _build_parameterset_hierarchy(self, debug: bool = False) -> None:
        """
        Build ParameterSet-child hierarchy using OwnerIndex.

        ParameterSet records can have child Parameter records.
        These children are used for:
        - DifferentialPair detection: parameter with name="DifferentialPair", text="True"
        - Other directive-specific parameters

        Differential-pair detection depends on these child parameters being
        attached to the owning ParameterSet.
        """
        if debug:
            log.info("  Building ParameterSet hierarchy...")

        parameter_sets = list(self.parameter_sets)
        for parameter_set in parameter_sets:
            parameter_set.parameters.clear()

        if not parameter_sets:
            if debug:
                log.info("    No ParameterSets found")
            return

        # Iterate through all parameters and assign to ParameterSets
        assigned_count = 0
        for obj in self.all_objects:
            if not isinstance(obj, AltiumSchParameter):
                continue

            parent_ps = getattr(obj, "parent", None)
            if isinstance(parent_ps, AltiumSchParameterSet):
                parent_ps.parameters.append(obj)
                assigned_count += 1

        if debug:
            log.info(
                f"    Assigned {assigned_count} parameters to {len(parameter_sets)} ParameterSets"
            )
            for ps in parameter_sets:
                if ps.parameters:
                    param_names = [p.name for p in ps.parameters]
                    log.info(f"    ParameterSet '{ps.name}': {param_names}")

    def _build_implementation_hierarchy(self, debug: bool = False) -> None:
        """
        Build ImplementationList-child hierarchy using OwnerIndex.

        ImplementationList records own Implementation records.
        This is needed for SchComponentInfo.footprint to find the current footprint.

        This allows wrapper APIs to resolve the active footprint implementation.
        """
        if debug:
            log.info("  Building Implementation hierarchy...")

        implementation_lists: list[AltiumSchImplementationList] = []
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchImplementationList):
                obj.children.clear()
                implementation_lists.append(obj)
            elif isinstance(obj, AltiumSchImplementation):
                obj.map = AltiumSchMapDefinerList()
                obj.map.parent = obj
                obj.children = [obj.map]
            elif isinstance(obj, AltiumSchMapDefinerList):
                obj.children.clear()

        if not implementation_lists:
            if debug:
                log.info("    No ImplementationLists found")
            return

        assigned_count = 0
        for obj in self.all_objects:
            parent = getattr(obj, "parent", None)
            if isinstance(obj, AltiumSchImplementation):
                if not isinstance(parent, AltiumSchImplementationList):
                    continue
                parent.children.append(obj)
                assigned_count += 1
                continue
            if isinstance(obj, AltiumSchMapDefinerList):
                if not isinstance(parent, AltiumSchImplementation):
                    continue
                previous = parent.map
                if previous in parent.children:
                    parent.children.remove(previous)
                previous.parent = None
                parent.map = obj
                parent.children.insert(0, obj)
                continue
            if isinstance(obj, AltiumSchMapDefiner):
                if isinstance(parent, AltiumSchMapDefinerList):
                    parent.add_map_definer(obj)
                continue
            if isinstance(obj, AltiumSchImplParams):
                if isinstance(parent, AltiumSchImplementation):
                    parent.children.append(obj)

        if debug:
            log.info(
                f"    Assigned {assigned_count} implementations to "
                f"{len(implementation_lists)} ImplementationLists"
            )

    @staticmethod
    def _set_runtime_parent(child: Any, parent: Any) -> None:
        """
        Attach a resolved runtime parent when the child exposes a `parent` attribute.
        """
        if hasattr(child, "parent"):
            _as_dynamic(child).parent = parent

    def _collect_harness_objects(self) -> list[Any]:
        """
        Return Additional-stream harness objects in file order.
        """
        return [
            obj
            for obj in self.all_objects
            if isinstance(
                obj,
                (
                    AltiumSchHarnessConnector,
                    AltiumSchHarnessEntry,
                    AltiumSchHarnessType,
                    AltiumSchSignalHarness,
                ),
            )
        ]

    @staticmethod
    def _build_harness_connector_index(
        harness_objects: list[Any],
    ) -> dict[int, AltiumSchHarnessConnector]:
        """
        Map Additional-stream indices to harness connectors.
        """
        return {
            index: obj
            for index, obj in enumerate(harness_objects)
            if isinstance(obj, AltiumSchHarnessConnector)
        }

    @staticmethod
    def _resolve_harness_parent(
        child: Any,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> AltiumSchHarnessConnector | None:
        """
        Resolve the parent connector for a harness child.
        """
        owner_index = getattr(child, "owner_index", 0)
        uses_file_order = getattr(child, "owner_index_additional_list", False)
        if uses_file_order and owner_index == 0:
            return current_connector
        if owner_index > 0:
            return connector_by_index.get(owner_index)
        return None

    def _attach_harness_entry(
        self,
        entry: AltiumSchHarnessEntry,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> None:
        """
        Attach a harness entry to its resolved harness connector.
        """
        parent = self._resolve_harness_parent(
            entry, current_connector, connector_by_index
        )
        if parent is None:
            return
        self._set_runtime_parent(entry, parent)
        parent.entries.append(entry)

    def _attach_harness_type(
        self,
        harness_type: AltiumSchHarnessType,
        current_connector: AltiumSchHarnessConnector | None,
        connector_by_index: dict[int, AltiumSchHarnessConnector],
    ) -> None:
        """
        Attach a harness type label to its resolved harness connector.
        """
        parent = self._resolve_harness_parent(
            harness_type, current_connector, connector_by_index
        )
        if parent is None:
            return
        parent.set_type_label(harness_type)

    def _build_harness_hierarchy(self) -> None:
        """
        Resolve harness connector ownership from the Additional stream.
        """
        for connector in self.harness_connectors:
            connector.entries.clear()
            default_field = connector.type_label
            if (
                default_field is not None
                and id(default_field) not in self._normalized_owner_refs
            ):
                # Managed import starts with an empty field, not the authoring
                # factory's visible placeholder. No persisted row is changed.
                default_field.text = ""
        for obj in self._additional_objects:
            parent = getattr(obj, "parent", None)
            if not isinstance(parent, AltiumSchHarnessConnector):
                continue
            if isinstance(obj, AltiumSchHarnessEntry):
                parent.entries.append(obj)
            elif isinstance(obj, AltiumSchHarnessType):
                # Import retains every raw child and normalized parent. The
                # authoring setter detaches replaced fields and is not a loader.
                parent.type_label = obj

    def _build_sheet_symbol_index(self) -> dict[int, AltiumSchSheetSymbol]:
        """
        Map persisted record indices to sheet symbols.
        """
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol] = {}
        for symbol in self.sheet_symbols:
            record_index = getattr(symbol, "_record_index", None)
            if record_index is not None:
                sheet_symbol_by_index[record_index] = symbol
        return sheet_symbol_by_index

    @staticmethod
    def _resolve_indexed_parent(
        owner_index: int,
        parent_by_index: dict[int, Any],
    ) -> Any | None:
        """
        Resolve an explicit owner index to its parent object.
        """
        if owner_index < 0:
            return None
        return parent_by_index.get(owner_index)

    def _attach_sheet_entries(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach sheet entries to their parent sheet symbols.
        """
        for entry in self.sheet_entries:
            parent = self._resolve_indexed_parent(
                getattr(entry, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(entry, parent)
            parent.entries.append(entry)

    def _attach_sheet_names(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach sheet-name records to their parent sheet symbols.
        """
        for name in self.sheet_names:
            parent = self._resolve_indexed_parent(
                getattr(name, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(name, parent)
            parent.children.append(name)
            parent.sheet_name = name

    def _attach_sheet_file_names(
        self,
        sheet_symbol_by_index: dict[int, AltiumSchSheetSymbol],
    ) -> None:
        """
        Attach file-name records to their parent sheet symbols.
        """
        for file_name in self.file_names:
            parent = self._resolve_indexed_parent(
                getattr(file_name, "owner_index", -1),
                sheet_symbol_by_index,
            )
            if parent is None:
                continue
            self._set_runtime_parent(file_name, parent)
            parent.children.append(file_name)
            parent.file_name = file_name

    def _build_sheet_symbol_hierarchy(self) -> None:
        """
        Resolve sheet-symbol owned records from persisted OwnerIndex links.
        """
        for symbol in self.sheet_symbols:
            symbol.entries.clear()
            symbol.children.clear()
            symbol.sheet_name = None
            symbol.file_name = None
        for obj in self._fileheader_objects:
            parent = getattr(obj, "parent", None)
            if not isinstance(parent, AltiumSchSheetSymbol):
                continue
            if isinstance(obj, AltiumSchSheetEntry):
                parent.entries.append(obj)
                parent.children.append(obj)
            elif isinstance(obj, AltiumSchSheetName):
                parent.sheet_name = obj
                parent.children.append(obj)
            elif isinstance(obj, AltiumSchFileName):
                parent.file_name = obj
                parent.children.append(obj)

    def _build_harness_and_sheet_hierarchy(self, debug: bool = False) -> None:
        """
        Build parent-child relationships for harness connectors and sheet symbols.

        Harness connectors own harness entries and harness types.
        Sheet symbols own sheet entries.

        OWNERSHIP MODELS:
        1. Harness objects (in Additional stream):
           - Use file-order hierarchy with OwnerIndexAdditionalList=T flag
           - Entries/types with no OwnerIndex belong to immediately preceding connector
           - Entries/types with OwnerIndex > 0 reference a connector by its Additional stream index

        2. Sheet symbols (in FileHeader stream):
           - Use explicit OwnerIndex referencing parent's record index
        """
        if debug:
            log.info("  Building harness and sheet symbol hierarchy...")

        self._build_harness_hierarchy()
        self._build_sheet_symbol_hierarchy()

        if debug:
            for connector in self.harness_connectors:
                log.info(f"    HarnessConnector: {len(connector.entries)} entries")
            for symbol in self.sheet_symbols:
                log.info(f"    SheetSymbol: {len(symbol.entries)} entries")

    def _link_embedded_images(self, debug: bool = False) -> None:
        """
        Link IMAGE records with their embedded image data.
        """
        if not self.embedded_images or not self.images:
            return

        if debug:
            log.info("  Linking embedded images...")

        embedded = [img for img in self.images if img.embedded]
        resolved = resolve_embedded_image_group(
            self.embedded_images,
            ((img.filename, int(img.orientation)) for img in embedded),
        )
        for img, data in zip(embedded, resolved, strict=True):
            if data is not None:
                img.image_data = data
                img.detect_format()

        if debug:
            linked = sum(1 for img in self.images if img.image_data)
            log.info(f"    Linked {linked}/{len(self.images)} images")

    def _set_all_parent_references(self, debug: bool = False) -> None:
        """
        Set parent references for any remaining objects based on owner_index.

        This is a comprehensive pass that handles parent relationships not covered
        by the specific hierarchy assembly passes (Components, SheetSymbols, Harness).

        Handles:
        - Template -> children (polylines, labels, etc.)
        - PIN -> children (parameters)
        - ParameterSet -> children (parameters)
        - ImplementationList -> children (implementations)
        - Any other owner_index relationships

        This method is called after the specific hierarchy assembly passes, so it only
        sets parent for objects that don't already have one.
        """
        if debug:
            log.info("  Setting all parent references...")

        set_count = 0
        for obj in self.all_objects:
            parent = getattr(obj, "parent", None)
            if parent is None:
                continue
            parent_children = getattr(parent, "children", None)
            if isinstance(parent_children, list) and obj not in parent_children:
                parent_children.append(obj)
                set_count += 1

        self._apply_additional_child_order()

        if debug:
            log.info(f"    Set {set_count} additional parent references")

    def _hydrate_harness_physical_models(self) -> None:
        from ._sch_source_projection import (
            _document_source_parents,
            _parameter_source_exclusions,
        )

        admission = _SourceAdmission.prepare(self.all_objects)
        source_parents = _document_source_parents(self)
        admission = _SourceAdmission.from_projection(
            admission.source_objects,
            {id(source) for source in admission.source_objects} - admission.ignored_ids,
            source_parents,
            unattached_ids=_parameter_source_exclusions(
                admission.source_objects,
                source_parents,
                admission.ignored_ids,
            ),
        )
        by_owner: dict[int, list[AltiumSchImageParameter]] = {}
        removed_models: list[SchPrimitive] = []
        for obj in admission.admitted(tuple(self.all_objects)):
            if not isinstance(obj, AltiumSchImageParameter):
                continue
            parent = admission.parent(obj)
            if type(parent) in {
                AltiumSchHarnessComponent,
                AltiumSchHarnessLayoutConnectionPoint,
            }:
                removed_models.extend(
                    self._cleanup_harness_physical_model_after_import(obj, admission)
                )
                by_owner.setdefault(id(parent), []).append(obj)
        for parameters in by_owner.values():
            if parameters and not any(item.is_main_model for item in parameters):
                parameters[0].is_main_model = True
        self._remove_import_cleaned_physical_models(removed_models)

    @staticmethod
    def _cleanup_harness_physical_model_after_import(
        parameter: AltiumSchImageParameter,
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> tuple[SchPrimitive, ...]:
        image_or_cavity: tuple[SchPrimitive, ...] = tuple(
            child
            for child in source_admission.children(parameter, parameter.children)
            if isinstance(child, (AltiumSchImage, _AltiumSchHarnessCavityComponent))
        )
        cavity = next(
            filter(
                lambda child: isinstance(child, _AltiumSchHarnessCavityComponent),
                image_or_cavity,
            ),
            None,
        )
        if len(image_or_cavity) <= 1 or cavity is None:
            return ()
        removed_ids = set(map(id, image_or_cavity)) - {id(cavity)}
        parameter.children[:] = list(
            filter(lambda child: id(child) not in removed_ids, parameter.children)
        )
        return tuple(child for child in image_or_cavity if id(child) in removed_ids)

    def _remove_import_cleaned_physical_models(
        self, removed_models: Collection[SchPrimitive]
    ) -> None:
        removed_ids = {id(model) for model in removed_models}
        if not removed_ids:
            return
        for model in removed_models:
            self._bounds_import_accessibility.pop(model, None)
        retained = [obj for obj in self._objects if id(obj) not in removed_ids]
        self._objects.clear()
        self._objects.extend(retained)
        self._fileheader_objects[:] = [
            obj for obj in self._fileheader_objects if id(obj) not in removed_ids
        ]
        self._additional_objects[:] = [
            obj for obj in self._additional_objects if id(obj) not in removed_ids
        ]

    @staticmethod
    def _refresh_harness_physical_model(parameter: AltiumSchImageParameter) -> None:
        # Retained as a compatibility hook; ``model`` is a live managed-style query.
        _ = parameter.model

    def _apply_additional_child_order(self) -> None:
        """Apply AD's semantic child ordering without changing physical vectors."""
        for obj in self._additional_objects:
            reference = self._normalized_owner_refs.get(id(obj))
            if reference is None or reference[0] != "FileHeader":
                continue
            parent = getattr(obj, "parent", None)
            children = getattr(parent, "children", None)
            if not isinstance(children, list) or obj not in children:
                continue
            raw_index = self._get_index_in_sheet(obj)
            index = 0 if raw_index is None else raw_index
            if index == -1:
                continue
            children.remove(obj)
            children.insert(max(0, min(index, len(children))), obj)

    def _get_index_in_sheet(self, obj: Any) -> int | None:
        """
        Get IndexInSheet from an object's raw record.
        """
        raw = getattr(obj, "_raw_record", None)
        if raw:
            idx = raw.get("IndexInSheet", raw.get("INDEXINSHEET"))
            if idx is not None:
                return int(idx)
        return None

    def _set_index_in_sheet(self, obj: Any, value: int) -> None:
        """
        Set IndexInSheet in an object (both OOP attribute and raw record).
        """
        # Update OOP attribute if present
        if hasattr(obj, "index_in_sheet"):
            obj.index_in_sheet = value

        # Update raw record if present
        raw = getattr(obj, "_raw_record", None)
        if raw is not None:
            if value == -2:
                raw.pop("IndexInSheet", None)
                raw.pop("INDEXINSHEET", None)
                return
            if "IndexInSheet" in raw:
                raw["IndexInSheet"] = str(value)
            elif "INDEXINSHEET" in raw:
                raw["INDEXINSHEET"] = str(value)
            elif value != 0:
                raw["IndexInSheet"] = str(value)

    def _recalculate_indices(self) -> None:
        """
        Recalculate OwnerIndex and IndexInSheet for all objects after modifications.

        This uses direct parent references when available instead of searching
        by stale record indices. That keeps indices consistent regardless of
        object order in all_objects.

        OwnerIndex is written as the warehouse position of the parent container,
        and IndexInSheet is written as the position in the parent's child list.

        Both are recalculated from scratch based on current object tree structure.
        This is called automatically before save operations.
        """
        warehouse_position = self._warehouse_positions()
        children_by_owner = self._recalculate_owner_indices(warehouse_position)
        self._recalculate_index_in_sheet(children_by_owner)
        self._update_record_indices(warehouse_position)

        log.info(f"  Recalculated indices for {len(self.all_objects)} objects")

    def _capture_local_sync_state(self) -> _SchLocalSyncSnapshot:
        states: list[_SchObjectSyncState] = []
        for target in self.all_objects:
            raw = getattr(target, "_raw_record", None)
            raw_copy = dict(raw) if isinstance(raw, dict) else None
            states.append(
                _SchObjectSyncState(
                    target=target,
                    owner_index=getattr(target, "owner_index", _MISSING_SYNC_VALUE),
                    index_in_sheet=getattr(
                        target, "index_in_sheet", _MISSING_SYNC_VALUE
                    ),
                    record_index=getattr(target, "_record_index", _MISSING_SYNC_VALUE),
                    owner_index_additional_list=getattr(
                        target,
                        "owner_index_additional_list",
                        _MISSING_SYNC_VALUE,
                    ),
                    not_auto_position=getattr(
                        target,
                        "not_auto_position",
                        _MISSING_SYNC_VALUE,
                    ),
                    source_stream=getattr(
                        target, "_source_stream", _MISSING_SYNC_VALUE
                    ),
                    raw_record=raw_copy,
                )
            )
        return _SchLocalSyncSnapshot(
            objects=tuple(states),
            file_weight=self._file_weight,
            preserve_loaded_indices=self._preserve_loaded_index_in_sheet,
            index_dirty=self._index_sync_dirty,
            dirty_index_owner_ids=frozenset(self._dirty_index_owner_ids),
            weight_dirty=frozenset(self._stream_weight_dirty),
        )

    def _restore_local_sync_state(self, snapshot: _SchLocalSyncSnapshot) -> None:
        for state in snapshot.objects:
            self._apply_object_sync_state(state.target, state)
        self._file_weight = snapshot.file_weight
        self._preserve_loaded_index_in_sheet = snapshot.preserve_loaded_indices
        self._index_sync_dirty = snapshot.index_dirty
        self._dirty_index_owner_ids = set(snapshot.dirty_index_owner_ids)
        self._stream_weight_dirty = set(snapshot.weight_dirty)

    @staticmethod
    def _apply_object_sync_state(
        target_object: object, state: _SchObjectSyncState
    ) -> None:
        target = _as_dynamic(target_object)
        for name, value in (
            ("owner_index", state.owner_index),
            ("index_in_sheet", state.index_in_sheet),
            ("_record_index", state.record_index),
            (
                "owner_index_additional_list",
                state.owner_index_additional_list,
            ),
            ("not_auto_position", state.not_auto_position),
            ("_source_stream", state.source_stream),
        ):
            if value is _MISSING_SYNC_VALUE:
                if hasattr(target, name):
                    delattr(target, name)
            else:
                setattr(target, name, value)
        if state.raw_record is not None:
            raw = getattr(target, "_raw_record", None)
            if isinstance(raw, dict):
                raw.clear()
                raw.update(state.raw_record)
            else:
                target._raw_record = dict(state.raw_record)

    def _finish_local_sync(self) -> None:
        self._preserve_loaded_index_in_sheet = True
        self._index_sync_dirty = False
        self._dirty_index_owner_ids.clear()
        self._stream_weight_dirty.clear()

    def _warehouse_positions(self) -> dict[int, int]:
        """
        Map object identity to its current warehouse position.
        """
        return {id(obj): pos for pos, obj in enumerate(self.all_objects)}

    @staticmethod
    def _raw_record_has_owner_index(raw_record: Any) -> bool:
        return raw_record is not None and (
            "OwnerIndex" in raw_record or "OWNERINDEX" in raw_record
        )

    @classmethod
    def _object_has_owner_reference(cls, obj: Any, raw_record: Any) -> bool:
        if getattr(obj, "parent", None) is not None:
            return True
        return cls._raw_record_has_owner_index(raw_record)

    @staticmethod
    def _set_owner_index_on_object(
        obj: Any,
        raw_record: Any,
        parent_pos: int,
    ) -> None:
        obj.owner_index = parent_pos
        if raw_record:
            if "OwnerIndex" in raw_record:
                raw_record["OwnerIndex"] = str(parent_pos)
            if "OWNERINDEX" in raw_record:
                raw_record["OWNERINDEX"] = str(parent_pos)

    @staticmethod
    def _append_owner_child(
        children_by_owner: dict[int, list[Any]],
        parent_pos: int,
        obj: Any,
    ) -> None:
        if parent_pos not in children_by_owner:
            children_by_owner[parent_pos] = []
        children_by_owner[parent_pos].append(obj)

    def _assign_owner_index_from_parent(
        self,
        obj: Any,
        raw_record: Any,
        warehouse_position: dict[int, int],
        children_by_owner: dict[int, list[Any]],
    ) -> bool:
        parent_obj = getattr(obj, "parent", None)
        if parent_obj is None:
            return False

        parent_pos = warehouse_position.get(id(parent_obj))
        if parent_pos is None:
            return False

        self._set_owner_index_on_object(obj, raw_record, parent_pos)
        self._append_owner_child(children_by_owner, parent_pos, obj)
        return True

    def _assign_owner_index_from_legacy_index(
        self,
        obj: Any,
        raw_record: Any,
        warehouse_position: dict[int, int],
        children_by_owner: dict[int, list[Any]],
    ) -> bool:
        old_owner = getattr(obj, "owner_index", None)
        if old_owner is None or old_owner <= 0:
            return False

        for potential_parent in self.all_objects:
            old_idx = getattr(potential_parent, "_record_index", None)
            if old_idx is None or old_idx != old_owner:
                continue
            parent_pos = warehouse_position.get(id(potential_parent))
            if parent_pos is None:
                return False
            self._set_owner_index_on_object(obj, raw_record, parent_pos)
            self._append_owner_child(children_by_owner, parent_pos, obj)
            return True

        log.debug(f"  Parent at old index {old_owner} not found for object")
        return False

    def _recalculate_owner_indices(
        self,
        warehouse_position: dict[int, int],
    ) -> dict[int, list[Any]]:
        children_by_owner: dict[int, list[Any]] = {}
        for obj in self.all_objects:
            raw_record = getattr(obj, "_raw_record", None)
            if not self._object_has_owner_reference(obj, raw_record):
                continue
            if self._assign_owner_index_from_parent(
                obj,
                raw_record,
                warehouse_position,
                children_by_owner,
            ):
                continue
            self._assign_owner_index_from_legacy_index(
                obj,
                raw_record,
                warehouse_position,
                children_by_owner,
            )
        return children_by_owner

    def _recalculate_index_in_sheet(
        self,
        children_by_owner: dict[int, list[Any]],
    ) -> None:
        # Parent links alone do not describe every managed subcollection. Rebuild
        # only root and the two owner collections modeled exactly by this API.
        del children_by_owner
        self._assign_index_in_sheet_sequence(
            self._top_level_sheet_children(),
            dirty=self._index_sync_dirty,
        )
        for owner in self.all_objects:
            if id(owner) not in self._dirty_index_owner_ids:
                continue
            if isinstance(owner, AltiumSchHarnessConnector):
                self._assign_index_in_sheet_sequence(list(owner.entries), dirty=True)
            elif isinstance(owner, AltiumSchSheetSymbol):
                self._assign_index_in_sheet_sequence(list(owner.entries), dirty=True)

    @staticmethod
    def _raw_record_has_index_in_sheet(raw_record: Any) -> bool:
        return raw_record is not None and (
            "IndexInSheet" in raw_record or "INDEXINSHEET" in raw_record
        )

    def _should_preserve_index_in_sheet(self, obj: Any) -> bool:
        current_index = getattr(obj, "index_in_sheet", None)
        if current_index is not None:
            try:
                current_index_int = int(current_index)
                if current_index_int == -2:
                    return True
                if current_index_int < 0:
                    return getattr(obj, "_raw_record", None) is not None
                return False
            except (TypeError, ValueError):
                return False

        raw_record = getattr(obj, "_raw_record", None)
        return raw_record is not None and not self._raw_record_has_index_in_sheet(
            raw_record
        )

    def _should_preserve_explicit_positive_index_in_sheet(self, obj: Any) -> bool:
        if not self._preserve_loaded_index_in_sheet:
            return False

        raw_record = getattr(obj, "_raw_record", None)
        if not self._raw_record_has_index_in_sheet(raw_record):
            return False

        current_index = getattr(obj, "index_in_sheet", None)
        if current_index is None:
            current_index = self._get_index_in_sheet(obj)
        if current_index is None:
            return False

        try:
            return int(current_index) >= 0
        except (TypeError, ValueError):
            return False

    def _assign_index_in_sheet_sequence(
        self,
        children: list[Any],
        *,
        dirty: bool,
    ) -> None:
        from .altium_sch_stream_sync import (
            _IndexDisposition,
            _IndexPlanEntry,
            _plan_index_updates,
        )

        entries: list[_IndexPlanEntry] = []
        children_by_token: dict[int, Any] = {}
        owner_position = 0
        for token, child in enumerate(children):
            current = _optional_int(getattr(child, "index_in_sheet", None))
            if current is None:
                current = self._get_index_in_sheet(child)
            if current is None:
                current = 0
            raw_record = getattr(child, "_raw_record", None)
            raw_present = self._raw_record_has_index_in_sheet(raw_record)
            if current < 0 and current != -2 and raw_record is not None:
                disposition = _IndexDisposition.preserve()
            elif owner_position == 0:
                disposition = _IndexDisposition.omit()
                owner_position += 1
            else:
                disposition = _IndexDisposition.assign(owner_position)
                owner_position += 1
            entries.append(_IndexPlanEntry(token, current, raw_present, disposition))
            children_by_token[token] = child

        updates = _plan_index_updates(entries, dirty=dirty)
        for update in updates:
            self._set_index_in_sheet(
                children_by_token[update.record_token], update.value
            )

    def _top_level_sheet_children(self) -> list[Any]:
        root_children: list[Any] = []
        for obj in self.all_objects:
            if obj is self.sheet:
                continue
            if getattr(obj, "parent", None) is not None:
                continue
            owner_index = getattr(obj, "owner_index", 0)
            if owner_index is None or int(owner_index) <= 0:
                root_children.append(obj)
        return root_children

    def _update_record_indices(
        self,
        warehouse_position: dict[int, int],
    ) -> None:
        for obj in self.all_objects:
            pos = warehouse_position.get(id(obj))
            if pos is not None and hasattr(obj, "_record_index"):
                _as_dynamic(obj)._record_index = pos

    # =========================================================================
    # Font Management
    # =========================================================================

    @property
    def font_manager(self) -> FontIDManager:
        """
        Get font manager for this document.

        The FontIDManager provides font lookup and creation:
        - get_or_create_font(name, size, ...) - Find or create font, returns ID
        - get_font_info(font_id) - Get font attributes by ID
        """
        from .altium_font_manager import FontIDManager

        if not hasattr(self, "_font_manager") or self._font_manager is None:
            if self.sheet is None:
                raise ValueError("Cannot access font_manager without a Sheet record")
            self._font_manager = FontIDManager(self.sheet)
        return self._font_manager

    # =========================================================================
    # Object Management Methods
    # =========================================================================
    # Structural mutation happens through the authoritative object store only.
    # Typed convenience properties are read-only live query views over self.objects.
    # Indices (OwnerIndex, IndexInSheet) are recalculated automatically on save.

    def _invalidate_fileheader_weight(self, stream_type: str = "FileHeader") -> None:
        """
        Drop the preserved FileHeader Weight after structural mutations.

        Existing-file round-trip saves preserve the original Weight until the
        object inventory changes. Once records are added/removed/reordered, the
        native importer expects the FileHeader Weight to match the rebuilt
        FileHeader stream, so the preserved count is no longer valid.
        """
        if stream_type not in {"FileHeader", "Additional"}:
            raise ValueError(f"unsupported schematic stream {stream_type!r}")
        if stream_type == "FileHeader":
            self._file_weight = None
        self._stream_weight_dirty.add(stream_type)

    def _invalidate_index_sync(self, owner: object | None = None) -> None:
        """Mark owner-relative index plans dirty without changing Weight state."""
        if owner is None:
            self._index_sync_dirty = True
        else:
            self._dirty_index_owner_ids.add(id(owner))
        self._preserve_loaded_index_in_sheet = False

    def _invalidate_structural_sync(
        self,
        obj: object,
        *,
        root_index_dirty: bool = False,
        index_owner: object | None = None,
    ) -> None:
        """Dirty only the affected physical stream and explicit owner plan."""
        stream_type = (
            "Additional"
            if getattr(obj, "_source_stream", "FileHeader") == "Additional"
            else "FileHeader"
        )
        self._invalidate_fileheader_weight(stream_type)
        if root_index_dirty:
            self._invalidate_index_sync()
        elif index_owner is not None:
            self._invalidate_index_sync(index_owner)

    @staticmethod
    def _is_harness_child_object(obj: object) -> bool:
        return isinstance(obj, (AltiumSchHarnessEntry, AltiumSchHarnessType))

    @staticmethod
    def _is_sheet_symbol_child_object(obj: object) -> bool:
        return isinstance(
            obj,
            (AltiumSchSheetEntry, AltiumSchSheetName, AltiumSchFileName),
        )

    @staticmethod
    def _is_additional_stream_object(obj: object) -> bool:
        return isinstance(
            obj,
            (
                AltiumSchHarnessConnector,
                AltiumSchHarnessEntry,
                AltiumSchHarnessType,
                AltiumSchSignalHarness,
                AltiumSchBlanket,
            ),
        )

    def _mark_default_source_stream(self, obj: object) -> None:
        if not self._is_additional_stream_object(obj):
            return
        source_stream = getattr(obj, "_source_stream", None)
        if source_stream is None:
            _as_dynamic(obj)._source_stream = "Additional"

    @staticmethod
    def _prepare_record_for_schdoc(obj: object) -> None:
        if not isinstance(obj, AltiumSchPin):
            return
        obj._source_is_binary = False
        raw_record = getattr(obj, "_raw_record", None)
        if isinstance(raw_record, dict) and "__BINARY_DATA__" in raw_record:
            # Binary pin records belong to SchLib. A detached pin adopted by a
            # SchDoc must rebuild its record in the document's text encoding.
            obj._raw_record = None

    @staticmethod
    def _iter_harness_connector_children(
        connector: AltiumSchHarnessConnector,
    ) -> list[object]:
        children: list[object] = list(getattr(connector, "entries", []))
        type_label = getattr(connector, "type_label", None)
        if type_label is None:
            type_label = next(
                (
                    child
                    for child in getattr(connector, "children", [])
                    if isinstance(child, AltiumSchHarnessType)
                ),
                None,
            )
        if type_label is not None:
            children.append(type_label)
        return children

    @staticmethod
    def _iter_sheet_symbol_children(
        symbol: AltiumSchSheetSymbol,
    ) -> list[object]:
        children: list[object] = []
        seen_ids: set[int] = set()

        def _append_child(candidate: object | None) -> None:
            if candidate is None:
                return
            if not AltiumSchDoc._is_sheet_symbol_child_object(candidate):
                return
            candidate_id = id(candidate)
            if candidate_id in seen_ids:
                return
            seen_ids.add(candidate_id)
            children.append(candidate)

        for entry in list(getattr(symbol, "entries", [])):
            _append_child(entry)
        _append_child(getattr(symbol, "sheet_name", None))
        _append_child(getattr(symbol, "file_name", None))
        for child in list(getattr(symbol, "children", [])):
            _append_child(child)
        return children

    @staticmethod
    def _set_owned_object_owner_index(obj: object, owner_pos: int) -> None:
        if hasattr(obj, "owner_index"):
            _as_dynamic(obj).owner_index = owner_pos

        owner_index_text = str(owner_pos)
        raw_record = getattr(obj, "_raw_record", None)
        if raw_record is None:
            return

        if "OWNERINDEX" in raw_record:
            raw_record["OWNERINDEX"] = owner_index_text
        if "OwnerIndex" in raw_record or "OWNERINDEX" not in raw_record:
            raw_record["OwnerIndex"] = owner_index_text

    def _prepare_harness_connector_child(
        self,
        connector: AltiumSchHarnessConnector,
        child: object,
    ) -> None:
        self._mark_default_source_stream(child)
        self._bind_schematic_object(child)
        if hasattr(child, "parent"):
            _as_dynamic(child).parent = connector
        if isinstance(child, AltiumSchHarnessEntry):
            child.owner_index_additional_list = True
        elif isinstance(child, AltiumSchHarnessType):
            child.owner_index_additional_list = True
            if child not in connector.children:
                connector.children.append(child)
            connector.type_label = child

    def _prepare_sheet_symbol_child(
        self,
        symbol: AltiumSchSheetSymbol,
        child: object,
        *,
        owner_pos: int,
    ) -> None:
        self._bind_schematic_object(child)
        self._attach_to_owner_relationships(child, symbol)
        self._set_owned_object_owner_index(child, owner_pos)

    @staticmethod
    def _assign_default_sheet_symbol_child_indices(
        symbol: AltiumSchSheetSymbol,
    ) -> None:
        for index, entry in enumerate(getattr(symbol, "entries", [])):
            index_in_sheet = getattr(entry, "index_in_sheet", None)
            if index_in_sheet is None or int(index_in_sheet) < 0:
                entry.index_in_sheet = -2 if index == 0 else index

        for label in (
            getattr(symbol, "sheet_name", None),
            getattr(symbol, "file_name", None),
        ):
            if label is not None and getattr(label, "index_in_sheet", None) is None:
                label.index_in_sheet = -1

    def _sync_harness_connector_group_objects(
        self,
        connector: AltiumSchHarnessConnector,
    ) -> None:
        """
        Keep the internal object store aligned with a connector-owned harness group.
        """
        self._mark_default_source_stream(connector)
        self._bind_schematic_object(connector)

        for child in self._iter_harness_connector_children(connector):
            self._prepare_harness_connector_child(connector, child)

        if connector not in self.all_objects:
            return

        existing_children = [
            obj
            for obj in list(self.all_objects)
            if self._is_harness_child_object(obj)
            and getattr(obj, "parent", None) is connector
        ]
        for child in existing_children:
            self._objects.remove(child)
            self._uncategorize_object(child)

        insert_at = self.all_objects.index(connector) + 1
        for child in self._iter_harness_connector_children(connector):
            if child in self.all_objects:
                self._objects.remove(child)
                self._uncategorize_object(child)
            self._objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

        self._invalidate_structural_sync(connector, index_owner=connector)

    def _sync_sheet_symbol_group_objects(
        self,
        symbol: AltiumSchSheetSymbol,
    ) -> None:
        """
        Keep the internal object store aligned with a symbol-owned sheet group.
        """
        self._bind_schematic_object(symbol)
        if symbol not in self.all_objects:
            return

        owner_pos = self.all_objects.index(symbol)
        ordered_children = self._iter_sheet_symbol_children(symbol)
        for child in ordered_children:
            self._prepare_sheet_symbol_child(symbol, child, owner_pos=owner_pos)

        symbol.entries = [
            child
            for child in ordered_children
            if isinstance(child, AltiumSchSheetEntry)
        ]
        symbol.sheet_name = next(
            (
                child
                for child in ordered_children
                if isinstance(child, AltiumSchSheetName)
            ),
            None,
        )
        symbol.file_name = next(
            (
                child
                for child in ordered_children
                if isinstance(child, AltiumSchFileName)
            ),
            None,
        )
        symbol.children[:] = ordered_children
        self._assign_default_sheet_symbol_child_indices(symbol)

        ordered_child_ids = {id(child) for child in ordered_children}
        existing_children = [
            obj
            for obj in list(self.all_objects)
            if self._is_sheet_symbol_child_object(obj)
            and getattr(obj, "parent", None) is symbol
        ]
        for child in existing_children:
            self._objects.remove(child)
            self._uncategorize_object(child)
            if id(child) not in ordered_child_ids:
                self._detach_from_parent_relationships(child)
                self._discard_detached_object_state(child)

        insert_at = self.all_objects.index(symbol) + 1
        for child in ordered_children:
            if child in self.all_objects:
                self._objects.remove(child)
                self._uncategorize_object(child)
            self._objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

        self._invalidate_structural_sync(symbol, index_owner=symbol)

    def _iter_component_children(
        self,
        component: AltiumSchComponent,
    ) -> list[object]:
        children = list(getattr(component, "children", []))
        if children:
            return children
        return [
            obj for obj in self.all_objects if getattr(obj, "parent", None) is component
        ]

    def _iter_component_group_objects(
        self,
        component: AltiumSchComponent,
    ) -> list[object]:
        ordered: list[object] = []

        def _append_child_tree(obj: object) -> None:
            ordered.append(obj)
            for child in list(getattr(obj, "children", [])):
                _append_child_tree(child)

        for child in self._iter_component_children(component):
            _append_child_tree(child)
        return ordered

    @staticmethod
    def _component_group_reordered_ids(
        ordered_children: Collection[object],
    ) -> set[int]:
        """Return descendants reinserted by the component stream-order pass."""
        reordered = {id(child) for child in ordered_children}
        for child in ordered_children:
            if not isinstance(child, AltiumSchImplementationList):
                continue
            for implementation in list(getattr(child, "children", [])):
                reordered.add(id(implementation))
                for implementation_child in list(
                    getattr(implementation, "children", [])
                ):
                    reordered.add(id(implementation_child))
                    if isinstance(implementation_child, AltiumSchMapDefinerList):
                        reordered.update(
                            id(map_child)
                            for map_child in list(implementation_child.children)
                        )
        return reordered

    @staticmethod
    def _has_ancestor(obj: object, ancestor: object) -> bool:
        parent = getattr(obj, "parent", None)
        while parent is not None:
            if parent is ancestor:
                return True
            parent = getattr(parent, "parent", None)
        return False

    @staticmethod
    def _owning_component_for_object(obj: object | None) -> AltiumSchComponent | None:
        current = obj
        while current is not None:
            if isinstance(current, AltiumSchComponent):
                return current
            current = getattr(current, "parent", None)
        return None

    def _sync_component_group_objects(
        self,
        component: AltiumSchComponent,
    ) -> None:
        """
        Keep the internal object store aligned with a component-owned subtree.
        """
        self._bind_schematic_object(component)
        if component not in self.all_objects:
            return

        ordered_children = self._iter_component_children(component)
        ordered_group = self._iter_component_group_objects(component)
        ordered_group_ids = {id(child) for child in ordered_group}
        reordered_group_ids = self._component_group_reordered_ids(ordered_children)

        existing_children = [
            obj for obj in list(self.all_objects) if self._has_ancestor(obj, component)
        ]
        for child in existing_children:
            child_id = id(child)
            if child_id in ordered_group_ids and child_id not in reordered_group_ids:
                continue
            self._objects.remove(child)
            self._uncategorize_object(child)
            if child_id not in ordered_group_ids:
                self._detach_from_parent_relationships(child)
                self._discard_detached_object_state(child)

        component_pos = self.all_objects.index(component)
        insert_at = component_pos + 1
        for child in ordered_children:
            self._prepare_record_for_schdoc(child)
            self._bind_schematic_object(child)
            self._attach_to_owner_relationships(child, component)
            self._set_owned_object_owner_index(child, component_pos)
            if child in self.all_objects:
                self._objects.remove(child)
                self._uncategorize_object(child)
            self._objects.insert(insert_at, child)
            self._categorize_object(child)
            insert_at += 1

            if not isinstance(child, AltiumSchImplementationList):
                continue

            implementation_list_pos = self.all_objects.index(child)
            for implementation in list(getattr(child, "children", [])):
                self._bind_schematic_object(implementation)
                self._attach_to_owner_relationships(implementation, child)
                self._set_owned_object_owner_index(
                    implementation,
                    implementation_list_pos,
                )
                if implementation in self.all_objects:
                    self._objects.remove(implementation)
                    self._uncategorize_object(implementation)
                self._objects.insert(insert_at, implementation)
                self._categorize_object(implementation)
                insert_at += 1

                implementation_pos = self.all_objects.index(implementation)
                for implementation_child in list(
                    getattr(implementation, "children", [])
                ):
                    self._bind_schematic_object(implementation_child)
                    self._attach_to_owner_relationships(
                        implementation_child,
                        implementation,
                    )
                    self._set_owned_object_owner_index(
                        implementation_child,
                        implementation_pos,
                    )
                    if implementation_child in self.all_objects:
                        self._objects.remove(implementation_child)
                        self._uncategorize_object(implementation_child)
                    self._objects.insert(insert_at, implementation_child)
                    self._categorize_object(implementation_child)
                    insert_at += 1

                    if not isinstance(
                        implementation_child,
                        AltiumSchMapDefinerList,
                    ):
                        continue
                    map_pos = self.all_objects.index(implementation_child)
                    for map_child in list(implementation_child.children):
                        self._bind_schematic_object(map_child)
                        self._attach_to_owner_relationships(
                            map_child,
                            implementation_child,
                        )
                        self._set_owned_object_owner_index(map_child, map_pos)
                        if map_child in self.all_objects:
                            self._objects.remove(map_child)
                            self._uncategorize_object(map_child)
                        self._objects.insert(insert_at, map_child)
                        self._categorize_object(map_child)
                        insert_at += 1

        self._invalidate_structural_sync(component)

    @staticmethod
    def _set_parent_if_supported(obj: object, owner: object) -> None:
        if hasattr(obj, "parent"):
            _as_dynamic(obj).parent = owner

    @staticmethod
    def _append_unique(collection: object, obj: object) -> None:
        if isinstance(collection, list) and obj not in collection:
            collection.append(obj)

    @classmethod
    def _append_runtime_owner_child(cls, owner: object, obj: object) -> None:
        cls._append_unique(getattr(owner, "children", None), obj)

    @classmethod
    def _attach_component_child(cls, obj: object, owner: AltiumSchComponent) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)
        if isinstance(obj, AltiumSchPin):
            cls._append_unique(owner.pins, obj)
            return
        if isinstance(
            obj,
            (AltiumSchParameter, AltiumSchDesignator, AltiumSchImplementationList),
        ):
            cls._append_unique(owner.parameters, obj)
            return
        if isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
            cls._append_unique(owner.graphics, obj)

    @classmethod
    def _attach_simple_owner_child(cls, obj: object, owner: object) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)

    @classmethod
    def _attach_implementation_child(
        cls,
        obj: object,
        owner: AltiumSchImplementation,
    ) -> None:
        cls._set_parent_if_supported(obj, owner)
        if isinstance(obj, AltiumSchMapDefinerList):
            previous = owner.map
            if previous is not obj:
                if previous in owner.children:
                    owner.children.remove(previous)
                previous.parent = None
                owner.map = obj
        cls._append_runtime_owner_child(owner, obj)

    @classmethod
    def _attach_implementation_map_child(
        cls,
        obj: object,
        owner: AltiumSchMapDefinerList,
    ) -> None:
        if isinstance(obj, AltiumSchMapDefiner):
            owner.add_map_definer(obj)
            return
        cls._attach_simple_owner_child(obj, owner)

    @staticmethod
    def _attach_harness_child(
        obj: object,
        owner: AltiumSchHarnessConnector,
    ) -> bool:
        if isinstance(obj, AltiumSchHarnessEntry):
            owner.add_entry(obj)
            return True
        if isinstance(obj, AltiumSchHarnessType):
            owner.set_type_label(obj)
            return True
        return False

    @classmethod
    def _attach_sheet_symbol_child(
        cls,
        obj: object,
        owner: AltiumSchSheetSymbol,
    ) -> None:
        cls._set_parent_if_supported(obj, owner)
        if isinstance(obj, AltiumSchSheetEntry):
            cls._append_unique(owner.entries, obj)
        if isinstance(obj, AltiumSchSheetName):
            owner.sheet_name = obj
        if isinstance(obj, AltiumSchFileName):
            owner.file_name = obj
        cls._append_runtime_owner_child(owner, obj)

    @classmethod
    def _attach_generic_owner_child(cls, obj: object, owner: object) -> None:
        cls._set_parent_if_supported(obj, owner)
        cls._append_runtime_owner_child(owner, obj)
        cls._append_unique(getattr(owner, "parameters", None), obj)
        if type(owner) is AltiumSchHarnessBundle:
            from .altium_sch_paint_order import _harness_bundle_field_role

            role = _harness_bundle_field_role(obj)
            if role is not None:
                _as_dynamic(obj)._harness_bundle_field_candidate_role = role
                _as_dynamic(obj)._harness_bundle_field_role = role
                if role == "length" and isinstance(obj, AltiumSchParameter):
                    obj.name = "Length"

    @classmethod
    def _attach_to_owner_relationships(cls, obj: object, owner: object) -> None:
        """
        Update in-memory parent/child links when an owner is supplied.
        """
        if isinstance(owner, AltiumSchComponent):
            cls._attach_component_child(obj, owner)
            return

        if isinstance(owner, AltiumSchImplementation):
            cls._attach_implementation_child(obj, owner)
            return

        if isinstance(owner, AltiumSchMapDefinerList):
            cls._attach_implementation_map_child(obj, owner)
            return

        if isinstance(owner, AltiumSchImplementationList):
            cls._attach_simple_owner_child(obj, owner)
            return

        if isinstance(owner, AltiumSchHarnessConnector) and cls._attach_harness_child(
            obj, owner
        ):
            return

        if isinstance(owner, AltiumSchSheetSymbol):
            cls._attach_sheet_symbol_child(obj, owner)
            return

        cls._attach_generic_owner_child(obj, owner)

    @staticmethod
    def _detach_component_child(obj: object, parent: AltiumSchComponent) -> None:
        if isinstance(obj, AltiumSchPin):
            try:
                parent.pins.remove(obj)
            except ValueError:
                pass
        elif isinstance(
            obj,
            (AltiumSchParameter, AltiumSchDesignator, AltiumSchImplementationList),
        ):
            try:
                parent.parameters.remove(obj)
            except ValueError:
                pass
        elif isinstance(obj, COMPONENT_GRAPHIC_CHILD_TYPES):
            try:
                parent.graphics.remove(obj)
            except ValueError:
                pass
        try:
            parent.children.remove(obj)
        except ValueError:
            pass

    @staticmethod
    def _detach_implementation_child(
        obj: object, parent: AltiumSchImplementation
    ) -> None:
        if not isinstance(obj, (AltiumSchMapDefinerList, AltiumSchImplParams)):
            return
        try:
            parent.children.remove(obj)
        except ValueError:
            pass
        if obj is parent.map:
            replacement = AltiumSchMapDefinerList()
            replacement.parent = parent
            parent.map = replacement
            parent.children.insert(0, replacement)

    @staticmethod
    def _detach_harness_child(obj: object, parent: AltiumSchHarnessConnector) -> None:
        if isinstance(obj, AltiumSchHarnessEntry) and obj in parent.entries:
            parent.entries.remove(obj)
        if isinstance(obj, AltiumSchHarnessType):
            if getattr(parent, "type_label", None) is obj:
                parent.type_label = None
            try:
                parent.children.remove(obj)
            except ValueError:
                pass
        AltiumSchDoc._detach_generic_parent_collections(obj, parent)

    @staticmethod
    def _detach_sheet_symbol_child(obj: object, parent: AltiumSchSheetSymbol) -> None:
        if isinstance(obj, AltiumSchSheetEntry) and obj in parent.entries:
            parent.entries.remove(obj)
        if isinstance(obj, AltiumSchSheetName) and parent.sheet_name is obj:
            parent.sheet_name = None
        if isinstance(obj, AltiumSchFileName) and parent.file_name is obj:
            parent.file_name = None
        AltiumSchDoc._detach_generic_parent_collections(obj, parent)

    @staticmethod
    def _detach_map_definer_child(obj: object, parent: AltiumSchMapDefinerList) -> None:
        if isinstance(obj, AltiumSchMapDefiner):
            parent.remove_map_definer(obj)

    @staticmethod
    def _detach_implementation_list_child(
        obj: object, parent: AltiumSchImplementationList
    ) -> None:
        if not isinstance(obj, AltiumSchImplementation):
            return
        try:
            parent.children.remove(obj)
        except ValueError:
            pass

    @staticmethod
    def _detach_generic_parent_collections(obj: object, parent: object) -> None:
        for attr_name in ("children", "parameters"):
            siblings = getattr(parent, attr_name, None)
            if not isinstance(siblings, list):
                continue
            try:
                siblings.remove(obj)
            except ValueError:
                pass

    @staticmethod
    def _detach_from_parent_relationships(obj: object) -> None:
        """Remove an object from any immediate in-memory parent collections."""
        parent = getattr(obj, "parent", None)
        if isinstance(parent, AltiumSchComponent):
            AltiumSchDoc._detach_component_child(obj, parent)
        elif isinstance(parent, AltiumSchMapDefinerList):
            AltiumSchDoc._detach_map_definer_child(obj, parent)
        elif isinstance(parent, AltiumSchImplementationList):
            AltiumSchDoc._detach_implementation_list_child(obj, parent)
        elif isinstance(parent, AltiumSchImplementation):
            AltiumSchDoc._detach_implementation_child(obj, parent)
        elif isinstance(parent, AltiumSchHarnessConnector):
            AltiumSchDoc._detach_harness_child(obj, parent)
        elif isinstance(parent, AltiumSchSheetSymbol):
            AltiumSchDoc._detach_sheet_symbol_child(obj, parent)
        elif parent is not None:
            AltiumSchDoc._detach_generic_parent_collections(obj, parent)

    def _discard_detached_object_state(self, obj: object) -> None:
        if isinstance(obj, SchPrimitive):
            self._bounds_import_accessibility.pop(obj, None)
        self._clear_detached_object_state(obj)

    @staticmethod
    def _clear_detached_object_state(obj: object) -> None:
        if hasattr(obj, "owner_index"):
            _as_dynamic(obj).owner_index = 0
        if hasattr(obj, "index_in_sheet"):
            _as_dynamic(obj).index_in_sheet = None
        if hasattr(obj, "parent"):
            _as_dynamic(obj).parent = None
        if hasattr(obj, "owner_index_additional_list"):
            _as_dynamic(obj).owner_index_additional_list = False
        if hasattr(obj, "_bound_schematic_context"):
            _as_dynamic(obj)._bound_schematic_context = None
        AltiumSchDoc._set_managed_unique_id_lock(obj, False)
        raw_record = getattr(obj, "_raw_record", None)
        if raw_record:
            raw_record.pop("OwnerIndex", None)
            raw_record.pop("OWNERINDEX", None)
            raw_record.pop("IndexInSheet", None)
            raw_record.pop("INDEXINSHEET", None)

    @staticmethod
    def _validate_top_level_add_object(
        obj: object,
        owner: object | None = None,
    ) -> None:
        """
        Reject connector-owned and symbol-owned child records as top-level
        document objects.
        """
        if isinstance(obj, (AltiumSchHarnessEntry, AltiumSchHarnessType)):
            raise ValueError(
                "Harness entries and harness type labels are connector-owned. "
                "Add them through the harness connector and then add the "
                "connector to the document with schdoc.add_object(connector)."
            )
        if owner is None and isinstance(
            obj,
            (AltiumSchSheetEntry, AltiumSchSheetName, AltiumSchFileName),
        ):
            raise ValueError(
                "Sheet entries, sheet name labels, and file name labels are "
                "symbol-owned. Add them through the sheet symbol and then add "
                "the sheet symbol to the document with "
                "schdoc.add_object(sheet_symbol)."
            )
        if owner is None and isinstance(
            obj,
            (
                AltiumSchPin,
                AltiumSchDesignator,
                AltiumSchImplementationList,
                AltiumSchImplementation,
                AltiumSchMapDefinerList,
                AltiumSchImplParams,
            ),
        ):
            raise ValueError(
                "Component-owned pin and implementation records must be added "
                "through a component-owned mutation API or with "
                "schdoc.add_object(child, owner=component)."
            )

    def _validate_object_attachment(
        self,
        obj: object,
        owner: object | None,
    ) -> None:
        """Validate exclusive document membership before changing object state."""
        if any(candidate is obj for candidate in self.all_objects):
            raise ValueError("object is already attached to this schematic document")

        context = getattr(obj, "_bound_schematic_context", None)
        bound_owner = getattr(context, "owner", None)
        if bound_owner is not None and bound_owner is not self:
            raise ValueError(
                "object is already attached to another schematic container"
            )

        parent = getattr(obj, "parent", None)
        if parent is not None and parent is not owner:
            raise ValueError("object is already attached to another owner")

        if owner is None:
            return
        if not any(candidate is owner for candidate in self.all_objects):
            raise ValueError(
                "owner must already be attached to this schematic document"
            )

    @staticmethod
    def _allows_duplicate_sheet_unique_id(obj: object) -> bool:
        return isinstance(obj, (AltiumSchComponent, AltiumSchSheetSymbol))

    @staticmethod
    def _set_managed_unique_id_lock(obj: object, locked: bool) -> None:
        if isinstance(obj, SchPrimitive):
            obj._set_unique_id_locked(locked)

    def _unique_id_is_in_use(self, unique_id: str, obj: object) -> bool:
        return any(
            other is not obj and getattr(other, "unique_id", "") == unique_id
            for other in self.all_objects
        )

    def _fix_object_unique_id_if_required(self, obj: object) -> None:
        if not isinstance(obj, SchPrimitive):
            return
        unique_id = obj.unique_id or ""
        conflicts = self._unique_id_is_in_use(unique_id, obj)
        if unique_id in {"", "$$$"} or (
            conflicts and not self._allows_duplicate_sheet_unique_id(obj)
        ):
            obj._update_unique_id(
                _generate_available_unique_id(
                    generate_sch_unique_id,
                    lambda candidate: self._unique_id_is_in_use(candidate, self),
                )
            )

    def add_object(self, obj: object, owner: object | None = None) -> None:
        """
        Add an object to the schematic.

        Adds the object to the document object store and sets owner_index
        when an owner is supplied.

        Args:
            obj: The object to add (OOP record instance)
            owner: Optional owner object (for setting OwnerIndex)
        """
        self._validate_object_attachment(obj, owner)
        self._validate_top_level_add_object(obj, owner)
        self._fix_object_unique_id_if_required(obj)
        self._mark_default_source_stream(obj)
        self._prepare_record_for_schdoc(obj)
        self._bind_schematic_object(obj)

        if owner is None and hasattr(obj, "owner_index"):
            owner_index = getattr(obj, "owner_index", None)
            if owner_index is None or int(owner_index) < 0:
                _as_dynamic(obj).owner_index = 0

        # Set ownership if owner specified
        if owner is not None:
            self._attach_to_owner_relationships(obj, owner)
            owner_pos = self._get_object_position(owner)
            if owner_pos is not None:
                # OwnerIndex is 0-based position in all_objects list
                # Reference file shows OwnerIndex=28 for children when parent is at position 28
                self._set_owned_object_owner_index(obj, owner_pos)

        # Add to all_objects
        self._objects.append(obj)
        self._set_managed_unique_id_lock(obj, True)
        self._invalidate_structural_sync(
            obj,
            root_index_dirty=owner is None,
            index_owner=(
                owner
                if isinstance(owner, (AltiumSchHarnessConnector, AltiumSchSheetSymbol))
                else None
            ),
        )

        # Typed query views recompute from self.objects automatically.
        self._categorize_object(obj)
        component_owner = self._owning_component_for_object(owner)
        if component_owner is None and isinstance(obj, AltiumSchComponent):
            component_owner = obj
        if component_owner is not None:
            self._sync_component_group_objects(component_owner)
        if isinstance(owner, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(owner)
        elif isinstance(obj, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(obj)
        if isinstance(owner, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(owner)
        elif isinstance(obj, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(obj)

    def insert_object(
        self, obj: object, index: int, owner: object | None = None
    ) -> None:
        """
        Insert an object at a specific position in the schematic.

        Indices are recalculated automatically on save.

        Args:
            obj: The object to insert
            index: Position in all_objects to insert at
            owner: Optional owner object (for setting OwnerIndex)
        """
        self._validate_object_attachment(obj, owner)
        self._validate_top_level_add_object(obj, owner)
        self._fix_object_unique_id_if_required(obj)
        self._mark_default_source_stream(obj)
        self._prepare_record_for_schdoc(obj)
        self._bind_schematic_object(obj)

        if owner is None and hasattr(obj, "owner_index"):
            owner_index = getattr(obj, "owner_index", None)
            if owner_index is None or int(owner_index) < 0:
                _as_dynamic(obj).owner_index = 0

        # Set ownership if owner specified
        if owner is not None:
            self._attach_to_owner_relationships(obj, owner)
            owner_pos = self._get_object_position(owner)
            if owner_pos is not None:
                # OwnerIndex is 0-based position (Issue #1 fix)
                self._set_owned_object_owner_index(obj, owner_pos)

        # Insert at position
        self._objects.insert(index, obj)
        self._set_managed_unique_id_lock(obj, True)
        self._invalidate_structural_sync(
            obj,
            root_index_dirty=owner is None,
            index_owner=(
                owner
                if isinstance(owner, (AltiumSchHarnessConnector, AltiumSchSheetSymbol))
                else None
            ),
        )

        # Typed query views recompute from self.objects automatically.
        self._categorize_object(obj)
        component_owner = self._owning_component_for_object(owner)
        if component_owner is None and isinstance(obj, AltiumSchComponent):
            component_owner = obj
        if component_owner is not None:
            self._sync_component_group_objects(component_owner)
        if isinstance(owner, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(owner)
        elif isinstance(obj, AltiumSchHarnessConnector):
            self._sync_harness_connector_group_objects(obj)
        if isinstance(owner, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(owner)
        elif isinstance(obj, AltiumSchSheetSymbol):
            self._sync_sheet_symbol_group_objects(obj)

    def remove_object(self, obj: object) -> bool:
        """
        Remove an object from the schematic.

        Removes the object from all_objects and clears ownership flags.

        Args:
            obj: The object to remove

        Returns:
            True if object was found and removed, False otherwise
        """
        parent = getattr(obj, "parent", None)
        refresh_pin_state = isinstance(obj, AltiumSchParameter) and isinstance(
            parent, AltiumSchPin
        )
        if isinstance(parent, AltiumSchHarnessConnector):
            if isinstance(obj, AltiumSchHarnessEntry):
                return parent.remove_entry(obj)
            if isinstance(obj, AltiumSchHarnessType):
                return parent.clear_type_label()

        if obj not in self.all_objects:
            return False

        if isinstance(
            obj,
            (AltiumSchComponent, AltiumSchImplementationList, AltiumSchImplementation),
        ):
            owned_children = [
                child
                for child in list(self.all_objects)
                if self._has_ancestor(child, obj)
            ]
            for child in owned_children:
                self._objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._discard_detached_object_state(child)
        elif isinstance(obj, AltiumSchHarnessConnector):
            harness_children = [
                child
                for child in list(self.all_objects)
                if self._is_harness_child_object(child)
                and getattr(child, "parent", None) is obj
            ]
            for child in harness_children:
                self._objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._discard_detached_object_state(child)
        elif isinstance(obj, AltiumSchSheetSymbol):
            sheet_symbol_children = [
                child
                for child in list(self.all_objects)
                if self._is_sheet_symbol_child_object(child)
                and getattr(child, "parent", None) is obj
            ]
            for child in sheet_symbol_children:
                self._objects.remove(child)
                self._uncategorize_object(child)
                self._detach_from_parent_relationships(child)
                self._discard_detached_object_state(child)

        # Remove from all_objects
        self._objects.remove(obj)
        self._invalidate_structural_sync(
            obj,
            root_index_dirty=parent is None,
            index_owner=(
                parent
                if isinstance(parent, (AltiumSchHarnessConnector, AltiumSchSheetSymbol))
                else None
            ),
        )

        # Typed query views recompute from self.objects automatically.
        self._uncategorize_object(obj)
        self._detach_from_parent_relationships(obj)

        # Clear ownership flags on the detached object.
        self._discard_detached_object_state(obj)

        if refresh_pin_state:
            self._hydrate_pin_state_parameters()

        return True

    def add_component(
        self,
        lib_reference: str,
        designator: str = "U?",
        x: int = 0,
        y: int = 0,
        library_path: str = "*",
        orientation: int = 0,
        is_mirrored: bool = False,
        part_id: int = 1,
        part_count: int | None = None,
        display_mode: int = 0,
        display_mode_count: int = 1,
        designator_x: int = 0,
        designator_y: int = 100,
        comment_x: int = 0,
        comment_y: int = -100,
    ) -> AltiumSchComponent:
        """
        Add a placed component and return the live component record.
        """
        from .altium_record_sch__parameter import AltiumSchParameter
        from .altium_sch_enums import Rotation90
        from .altium_symbol_transform import generate_unique_id

        resolved_part_id = int(part_id)
        if resolved_part_id < 1:
            raise ValueError("part_id must be >= 1")
        resolved_part_count = (
            max(1, resolved_part_id) if part_count is None else int(part_count)
        )
        if resolved_part_count < 1:
            raise ValueError("part_count must be >= 1")
        if resolved_part_id > resolved_part_count:
            raise ValueError(
                f"part_id {resolved_part_id} exceeds part_count {resolved_part_count}"
            )
        resolved_display_mode_count = int(display_mode_count)
        if resolved_display_mode_count < 1:
            raise ValueError("display_mode_count must be >= 1")

        resolved_orientation = Rotation90(int(orientation))
        snapshot = self._capture_authoring_mutation_state()
        try:
            component = AltiumSchComponent()
            component.lib_reference = lib_reference
            component.library_path = library_path
            component.source_library_name = (
                Path(library_path).name if library_path != "*" else "*"
            )
            component.location = CoordPoint.from_mils(x, y)
            component.orientation = resolved_orientation
            component.is_mirrored = is_mirrored
            component.current_part_id = resolved_part_id
            component.part_count = resolved_part_count
            component.display_mode = int(display_mode)
            component.display_mode_count = resolved_display_mode_count
            component.unique_id = generate_unique_id()
            component._has_part_count = True
            component._has_current_part_id = True
            component._has_display_mode_count = True

            self.add_object(component)

            designator_record = AltiumSchDesignator()
            designator_record.text = designator
            designator_record.location = CoordPoint.from_mils(
                x + designator_x,
                y + designator_y,
            )
            designator_record.font_id = self.font_manager.get_or_create_font(
                font_name="Arial",
                font_size=12,
                bold=True,
            )
            designator_record.unique_id = generate_unique_id()
            self.add_object(designator_record, owner=component)

            comment_record = AltiumSchParameter()
            comment_record.name = "Comment"
            comment_record.text = "=Value"
            comment_record.location = CoordPoint.from_mils(
                x + comment_x,
                y + comment_y,
            )
            comment_record.font_id = self.font_manager.get_or_create_font(
                font_name="Arial",
                font_size=10,
                bold=False,
            )
            comment_record.unique_id = generate_unique_id()
            self.add_object(comment_record, owner=component)

            component.index_in_sheet = self._get_object_position(component)
            return component
        except Exception:
            self._restore_authoring_mutation_state(snapshot)
            raise

    def add_component_from_library(
        self,
        library_path: str | Path,
        symbol_name: str,
        designator: str,
        x: int,
        y: int,
        orientation: int = 0,
        is_mirrored: bool = False,
        part_id: int = 1,
        display_mode: int = 0,
    ) -> AltiumSchComponent:
        """
        Place a component from a SchLib symbol and return the live component record.
        """
        from .altium_symbol_transform import generate_unique_id

        library_path = Path(library_path)
        if not library_path.exists():
            raise FileNotFoundError(f"Library not found: {library_path}")

        schlib = load_or_cache_schlib(self._schlib_cache, library_path)
        symbol, resolved_part_id, resolved_part_count, resolved_orientation = (
            self._resolve_library_component_selection(
                schlib,
                library_path,
                symbol_name,
                part_id,
                orientation,
            )
        )
        snapshot = self._capture_authoring_mutation_state()
        try:
            font_id_map = merge_schlib_fonts(self.font_manager, schlib)
            component = AltiumSchComponent()
            component.lib_reference = symbol_name
            component.library_path = str(library_path)
            component.source_library_name = library_path.name
            component.location = CoordPoint.from_mils(x, y)
            component.orientation = resolved_orientation
            component.is_mirrored = is_mirrored
            component.part_count = resolved_part_count
            component.current_part_id = resolved_part_id
            component.display_mode = display_mode
            component.display_mode_count = int(
                getattr(symbol, "display_mode_count", 1) or 1
            )
            component.unique_id = generate_unique_id()
            if getattr(symbol, "description", ""):
                component.component_description = str(symbol.description)
                component._has_component_description = True
            component._has_part_count = True
            component._has_current_part_id = True
            component._has_display_mode_count = True

            cloned_children = clone_symbol_children(
                symbol,
                component,
                designator=designator,
                part_id=resolved_part_id,
                font_id_map=font_id_map,
            )

            self.add_object(component)
            for child in cloned_children.ordered_children:
                self.add_object(child, owner=component)

            component.index_in_sheet = self._get_object_position(component)
            component.all_pin_count = len(component.pins)
            component._has_all_pin_count = True
            return component
        except Exception:
            self._restore_authoring_mutation_state(snapshot)
            raise

    @staticmethod
    def _resolve_library_component_selection(
        schlib: "AltiumSchLib",
        library_path: Path,
        symbol_name: str,
        part_id: int,
        orientation: int,
    ) -> tuple["AltiumSymbol", int, int, "Rotation90"]:
        from .altium_sch_enums import Rotation90

        symbol = schlib.get_symbol(symbol_name)
        if symbol is None:
            available = [record.name for record in schlib.symbols]
            raise ValueError(
                f"Symbol '{symbol_name}' not found in {library_path}. "
                f"Available symbols: {available}"
            )

        resolved_part_id = int(part_id)
        if resolved_part_id < 1:
            raise ValueError("part_id must be >= 1")
        resolved_part_count = int(getattr(symbol, "part_count", 1) or 1)
        if resolved_part_id > resolved_part_count:
            raise ValueError(
                f"part_id {resolved_part_id} exceeds symbol part_count {resolved_part_count}"
            )
        return (
            symbol,
            resolved_part_id,
            resolved_part_count,
            Rotation90(int(orientation)),
        )

    def _capture_authoring_mutation_state(self) -> _SchAuthoringMutationSnapshot:
        manager = self.font_manager
        return _SchAuthoringMutationSnapshot(
            bounds_import_accessibility=self._bounds_import_accessibility.copy(),
            objects=tuple(self.all_objects),
            local_sync=self._capture_local_sync_state(),
            fonts=deepcopy(manager.sheet.fonts),
            font_id_count=manager.sheet.font_id_count,
        )

    def _restore_authoring_mutation_state(
        self,
        snapshot: _SchAuthoringMutationSnapshot,
    ) -> None:
        retained_ids = {id(obj) for obj in snapshot.objects}
        for obj in self.all_objects:
            if id(obj) not in retained_ids:
                self._discard_detached_object_state(obj)
        self._objects.clear()
        self._objects.extend(snapshot.objects)
        self._bounds_import_accessibility = snapshot.bounds_import_accessibility.copy()
        self._restore_local_sync_state(snapshot.local_sync)
        manager = self.font_manager
        manager.sheet.fonts.clear()
        manager.sheet.fonts.update(deepcopy(snapshot.fonts))
        manager.sheet.font_id_count = snapshot.font_id_count

    def _get_object_position(self, obj: Any) -> int | None:
        """
        Get the current position of an object in all_objects.
        """
        try:
            return self.all_objects.index(obj)
        except ValueError:
            return None

    def _categorize_object(self, obj: Any) -> None:
        """
        No-op: objects are already in self.objects.

                Type-specific access is now provided via properties.
                This method preserves the existing call surface but does not
                append (the caller already appends to self.objects).
        """

    def _uncategorize_object(self, obj: Any) -> None:
        """
        Remove object from the collection.

                Since this uses a single ObjectCollection, this just removes
                from self.objects.
        """
        if obj in self._objects:
            self._objects.remove(obj)

    def get_parameter(self, name: str) -> str | None:
        """
        Get document parameter value by name (case-insensitive).

        Args:
            name: Parameter name to look up

        Returns:
            Parameter text value, or None if not found
        """
        name_lower = name.lower()
        for param in self._document_parameters():
            if hasattr(param, "name") and param.name.lower() == name_lower:
                return param.text if hasattr(param, "text") else None
        return None

    def _document_parameters(self) -> Iterable[AltiumSchParameter]:
        """Iterate parameters owned by the schematic sheet itself."""
        for param in self.parameters:
            parent = getattr(param, "parent", None)
            if parent is self.sheet:
                yield param
                continue
            if parent is not None:
                continue
            identity = id(param)
            if identity in self._normalized_owner_refs:
                if self._normalized_owner_refs[identity] == ("FileHeader", 0):
                    yield param
                continue
            owner_index = getattr(param, "owner_index", 0)
            if owner_index is None or int(owner_index) == 0:
                yield param

    def get_parameter_dict(self) -> dict[str, str]:
        """
        Get all document parameters as a dictionary.

        Returns:
            Dict mapping parameter names to their values
        """
        result = {}
        for param in self._document_parameters():
            if hasattr(param, "name") and hasattr(param, "text"):
                result[param.name] = param.text
        return result

    def get_template(self) -> AltiumSchTemplate | None:
        """
        Get the template object from the document.

        Template is stored as a top-level object with record type 39 (eTemplate).
        Its children contain the actual template graphics (polylines, labels,
        text frames, images) which are rendered when ShowTemplateGraphics=True.

        Returns:
            The template object, or None if no template exists.
        """
        for obj in self.all_objects:
            if isinstance(obj, AltiumSchTemplate):
                return obj
        return None

    def _template_child_objects(self, template: AltiumSchTemplate) -> list[object]:
        """
        Return records currently owned by a template object.
        """
        template_owner_indexes = {
            index
            for index in (
                self._get_object_position(template),
                getattr(template, "_record_index", None),
            )
            if isinstance(index, int) and index >= 0
        }

        def is_template_child(obj: object) -> bool:
            if obj is template:
                return False
            if getattr(obj, "parent", None) is template:
                return True
            owner_index = getattr(obj, "owner_index", None)
            owner_index_int = _optional_int(owner_index)
            return (
                owner_index_int is not None
                and owner_index_int in template_owner_indexes
            )

        return [obj for obj in list(self.all_objects) if is_template_child(obj)]

    @staticmethod
    def _is_top_level_parameter(obj: object) -> bool:
        """
        Return whether a parameter is sheet/document-level instead of owned.
        """
        if not isinstance(obj, AltiumSchParameter):
            return False
        if getattr(obj, "parent", None) is not None:
            return False
        owner_index = getattr(obj, "owner_index", 0)
        try:
            return int(owner_index) <= 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _clone_detached_schematic_object(obj: object) -> object:
        """
        Clone a source record and remove document-specific owner/index state.
        """
        cloned = deepcopy(obj)
        AltiumSchDoc._clear_detached_object_state(cloned)
        if hasattr(cloned, "_record_index"):
            _as_dynamic(cloned)._record_index = None
        if hasattr(cloned, "_raw_record_index"):
            _as_dynamic(cloned)._raw_record_index = None
        return cloned

    def _merge_template_fonts(self, template_doc: "AltiumSchDoc") -> dict[int, int]:
        """
        Merge template font table entries into this document and return ID map.
        """
        font_id_map: dict[int, int] = {}
        if self.sheet is None or template_doc.sheet is None:
            return font_id_map

        for source_id, font_data in template_doc.sheet.fonts.items():
            target_id = self.font_manager.get_or_create_font(
                font_name=font_data.get("name", "Times New Roman"),
                font_size=font_data.get("size", 10),
                bold=font_data.get("bold", False),
                italic=font_data.get("italic", False),
                rotation=font_data.get("rotation", 0),
                underline=font_data.get("underline", False),
                strikeout=font_data.get("strikeout", False),
            )
            font_id_map[int(source_id)] = target_id
        return font_id_map

    def _apply_template_visual_sheet_settings(
        self,
        template_doc: "AltiumSchDoc",
        font_id_map: dict[int, int],
    ) -> None:
        """
        Copy opt-in visual sheet context from a SchDot into this document.
        """
        if self.sheet is None:
            raise ValueError("apply_template requires a schematic sheet record")
        if template_doc.sheet is None:
            return

        source = template_doc.sheet
        target = self.sheet
        visual_attrs = (
            "sheet_style",
            "use_custom_sheet",
            "custom_x",
            "custom_y",
            "custom_x_zones",
            "custom_y_zones",
            "custom_margin_width",
            "border_on",
            "title_block_on",
            "reference_zones_on",
            "reference_zone_style",
            "document_border_style",
            "workspace_orientation",
            "display_unit",
            "snap_grid_on",
            "snap_grid_size",
            "visible_grid_on",
            "visible_grid_size",
            "hot_spot_grid_on",
            "hot_spot_grid_size",
            "hot_spot_grid_size_frac",
            "color",
            "area_color",
            "sheet_number_space_size",
        )
        for attr in visual_attrs:
            if hasattr(source, attr):
                setattr(target, attr, deepcopy(getattr(source, attr)))

        for flag in (
            "_has_custom_x_zones",
            "_has_custom_y_zones",
            "_has_custom_margin_width",
        ):
            setattr(target, flag, bool(getattr(source, flag, False)))

        source_system_font = int(getattr(source, "system_font", 0) or 0)
        if source_system_font in font_id_map:
            target.system_font = font_id_map[source_system_font]

    def _template_content_from_document(
        self,
        template_doc: "AltiumSchDoc",
        template_filename: str,
    ) -> tuple[AltiumSchTemplate, list[object], list[AltiumSchParameter]]:
        """
        Extract the container, visual children, and document parameters from SchDot.
        """
        source_template = template_doc.get_template()
        if source_template is None:
            template = AltiumSchTemplate()
            source_children = [
                obj
                for obj in template_doc.all_objects
                if obj is not template_doc.sheet
                and not isinstance(obj, AltiumSchParameter)
            ]
        else:
            cloned_template = self._clone_detached_schematic_object(source_template)
            if not isinstance(cloned_template, AltiumSchTemplate):
                raise TypeError("template clone did not produce an AltiumSchTemplate")
            template = cloned_template
            source_children = template_doc._template_child_objects(source_template)

        template.filename = template_filename
        template._has_filename = True

        child_objects: list[object] = []
        for source_child in source_children:
            child = self._clone_detached_schematic_object(source_child)
            if (
                isinstance(child, AltiumSchImage)
                and child.filename
                and not child.image_data
            ):
                child.image_data = template_doc.embedded_images.get(child.filename)
            child_objects.append(child)

        template_parameters: list[AltiumSchParameter] = []
        for obj in template_doc.all_objects:
            if not self._is_top_level_parameter(obj):
                continue
            parameter = self._clone_detached_schematic_object(obj)
            if not isinstance(parameter, AltiumSchParameter):
                raise TypeError("template parameter clone changed record type")
            template_parameters.append(parameter)
        return template, child_objects, template_parameters

    def _existing_top_level_parameter_names(self) -> set[str]:
        """
        Return lower-case sheet/document-level parameter names already present.
        """
        names: set[str] = set()
        for param in self.parameters:
            if not self._is_top_level_parameter(param):
                continue
            name = getattr(param, "name", "")
            if name:
                names.add(str(name).lower())
        return names

    def _merge_missing_template_parameters(
        self,
        source_parameters: list[AltiumSchParameter],
        font_id_map: dict[int, int],
    ) -> int:
        """
        Add missing SchDot document parameters without overwriting target values.
        """
        existing_names = self._existing_top_level_parameter_names()
        inserted_count = 0
        for source_param in source_parameters:
            name = str(getattr(source_param, "name", "") or "")
            if not name:
                continue
            key = name.lower()
            if key in existing_names:
                continue
            param = self._clone_detached_schematic_object(source_param)
            remap_font_ids(param, font_id_map)
            self.add_object(param)
            existing_names.add(key)
            inserted_count += 1
        return inserted_count

    def clear_template(self) -> int:
        """
        Remove the schematic template and all template-owned objects.

        This removes the `AltiumSchTemplate` record, child graphics/text/images
        whose owner is the template, and sheet-level template metadata such as
        `TemplateFileName` and `ShowTemplateGraphics`. Template-owned embedded
        image storage is resynchronized immediately after removal.

        Returns:
            Number of records removed, including the template record itself.
            Returns 0 when the document has no template record.
        """
        templates = [
            obj for obj in self.all_objects if isinstance(obj, AltiumSchTemplate)
        ]
        if not templates:
            if self.sheet is not None:
                self.sheet.clear_template_references()
            return 0

        template_children = {
            id(child): child
            for template in templates
            for child in self._template_child_objects(template)
        }

        removed_count = 0
        for obj in reversed(list(self.all_objects)):
            if id(obj) not in template_children:
                continue
            if self.remove_object(obj):
                removed_count += 1
        for template in reversed(templates):
            if template in self.all_objects and self.remove_object(template):
                removed_count += 1

        if self.sheet is not None:
            self.sheet.clear_template_references()
        self._sync_embedded_images_from_objects()
        return removed_count

    def apply_template(
        self,
        template_path: str | Path,
        *,
        clear_existing: bool = True,
        merge_parameters: bool = True,
        template_filename: str | Path | None = None,
        apply_visual_sheet_settings: bool = False,
    ) -> int:
        """
        Apply a schematic `.SchDot` template to this document.

        The `.SchDot` file is parsed as a normal schematic, because Altium uses
        the same binary format for `.SchDoc` and `.SchDot`. Existing template
        graphics are cleared by default, the new template content is inserted
        after the sheet record, template font IDs are remapped into this
        document's font table, and sheet-level template metadata is updated.

        Args:
            template_path: Path to the `.SchDot` template file.
            clear_existing: If True, clear the current template before applying.
            merge_parameters: If True, add missing document-level parameters
                from the template without overwriting existing target values.
            template_filename: Optional filename/path to store in the SchDoc
                template metadata. If omitted, stores ``template_path`` exactly
                as passed after normal ``Path`` string conversion.
            apply_visual_sheet_settings: If True, copy visual sheet settings
                such as sheet size/style, grids, colors, border/title-zone
                settings, display unit, and remapped system font from the
                template document.

        Returns:
            Number of records inserted, including the template container,
            template-owned children, and any newly added document parameters.
        """
        if self.sheet is None:
            raise ValueError("apply_template requires a schematic sheet record")

        resolved_template_path = Path(template_path)
        if not resolved_template_path.exists():
            raise FileNotFoundError(f"Template not found: {resolved_template_path}")

        template_doc = AltiumSchDoc(resolved_template_path)
        stored_template_filename = str(
            template_filename
            if template_filename is not None
            else resolved_template_path
        )
        prepared = self._template_content_from_document(
            template_doc,
            stored_template_filename,
        )
        existing_templates = [
            obj for obj in self.all_objects if isinstance(obj, AltiumSchTemplate)
        ]
        if not clear_existing and len(existing_templates) > 1:
            raise ValueError(
                "apply_template(clear_existing=False) requires at most one "
                "existing template container"
            )

        staged, object_map = self._clone_for_staging()
        inserted_count = staged._apply_prepared_template(
            template_doc,
            prepared,
            clear_existing=clear_existing,
            merge_parameters=merge_parameters,
            stored_template_filename=stored_template_filename,
            apply_visual_sheet_settings=apply_visual_sheet_settings,
        )
        self._commit_authoring_candidate(staged, object_map)
        return inserted_count

    def _apply_prepared_template(
        self,
        template_doc: "AltiumSchDoc",
        prepared: tuple[AltiumSchTemplate, list[object], list[AltiumSchParameter]],
        *,
        clear_existing: bool,
        merge_parameters: bool,
        stored_template_filename: str,
        apply_visual_sheet_settings: bool,
    ) -> int:
        """Apply fully loaded template content to a private candidate document."""
        sheet = self.sheet
        if sheet is None:
            raise ValueError("template candidate requires a schematic sheet record")
        if clear_existing:
            self.clear_template()

        existing_templates = [
            obj for obj in self.all_objects if isinstance(obj, AltiumSchTemplate)
        ]
        if len(existing_templates) > 1:
            raise ValueError("candidate contains multiple template containers")

        font_id_map = self._merge_template_fonts(template_doc)
        if apply_visual_sheet_settings:
            self._apply_template_visual_sheet_settings(template_doc, font_id_map)

        prepared_template, child_objects, source_parameters = prepared
        if existing_templates:
            template = existing_templates[0]
            template.filename = stored_template_filename
            template._has_filename = True
            current_children = self._template_child_objects(template)
            insert_index = (
                max(
                    [self.all_objects.index(template)]
                    + [self.all_objects.index(child) for child in current_children]
                )
                + 1
            )
            inserted_count = 0
        else:
            template = prepared_template
            insert_index = self.all_objects.index(self.sheet) + 1
            self.insert_object(template, insert_index)
            insert_index += 1
            inserted_count = 1

        for child in child_objects:
            remap_font_ids(child, font_id_map)
            self.insert_object(child, insert_index, owner=template)
            insert_index += 1
            inserted_count += 1

        if merge_parameters:
            inserted_count += self._merge_missing_template_parameters(
                source_parameters,
                font_id_map,
            )

        sheet.clear_template_references()
        sheet.show_template_graphics = True
        sheet.template_filename = stored_template_filename
        self._sync_embedded_images_from_objects()
        return inserted_count

    def extract_template(
        self,
        output_path: str | Path,
        *,
        include_parameters: bool = True,
    ) -> int:
        """
        Extract the embedded schematic template to a standalone `.SchDot` file.

        The output `.SchDot` is written as a normal schematic-template document:
        the sheet record is copied from the source schematic with template
        metadata cleared, template-owned visual objects are unwrapped back to
        top-level objects, and embedded image storage is copied from the image
        records. This is the inverse of `apply_template()` for template
        graphics.

        Args:
            output_path: Destination `.SchDot` path.
            include_parameters: If True, copy document-level parameters from
                the source schematic into the output template file.

        Returns:
            Number of records extracted, excluding the sheet record.

        Raises:
            ValueError: If the document has no sheet or no embedded template.
        """
        if self.sheet is None:
            raise ValueError("extract_template requires a schematic sheet record")

        template = self.get_template()
        if template is None:
            raise ValueError("extract_template requires an embedded template record")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        template_doc = AltiumSchDoc(create_sheet=False)
        sheet = deepcopy(self.sheet)
        sheet.clear_template_references()
        sheet.owner_index = 0
        sheet.index_in_sheet = -1
        sheet.parent = None
        if hasattr(sheet, "_bound_schematic_context"):
            sheet._bound_schematic_context = None
        template_doc.sheet = sheet
        template_doc._objects.append(sheet)
        template_doc._file_unique_id = self._file_unique_id

        extracted_count = 0
        if include_parameters:
            for source_param in self.all_objects:
                if not self._is_top_level_parameter(source_param):
                    continue
                param = self._clone_detached_schematic_object(source_param)
                template_doc.add_object(param)
                extracted_count += 1

        for source_child in self._template_child_objects(template):
            child = self._clone_detached_schematic_object(source_child)
            if (
                isinstance(child, AltiumSchImage)
                and child.filename
                and not child.image_data
            ):
                child.image_data = self.embedded_images.get(child.filename)
            template_doc.add_object(child)
            extracted_count += 1

        template_doc._sync_embedded_images_from_objects()
        template_doc.save(output_path)
        return extracted_count

    def get_summary(self) -> str:
        """
        Get a summary string of the schematic contents.

        Returns:
            Multi-line summary string
        """
        lines = []
        lines.append(
            f"SchDoc File: {self.filepath.name if self.filepath else 'Unknown'}"
        )

        if self.sheet:
            lines.append(f"  Sheet: {self.sheet.sheet_name or 'Unnamed'}")
            lines.append(
                f"  Size: {self.sheet.custom_x} x {self.sheet.custom_y} (style={self.sheet.sheet_style})"
            )
            lines.append(f"  Fonts: {self.sheet.font_id_count}")

        lines.append("\nObjects:")
        lines.append(f"  Components: {len(self.components)}")
        lines.append(f"  Wires: {len(self.wires)}")
        lines.append(f"  Buses: {len(self.buses)}")
        lines.append(f"  Net Labels: {len(self.net_labels)}")
        lines.append(f"  Power Ports: {len(self.power_ports)}")
        lines.append(f"  Cross-Sheet Connectors: {len(self.cross_sheet_connectors)}")
        lines.append(f"  Junctions: {len(self.junctions)}")
        lines.append(f"  Ports: {len(self.ports)}")
        lines.append(f"  Sheet Symbols: {len(self.sheet_symbols)}")
        lines.append(f"  Sheet Entries: {len(self.sheet_entries)}")
        lines.append(f"  Labels: {len(self.labels)}")
        lines.append(f"  Graphics: {len(self.graphics)}")
        lines.append(f"  Images: {len(self.images)}")
        lines.append(f"  Embedded Images: {len(self.embedded_images)}")
        lines.append(f"\nTotal Objects: {len(self.all_objects)}")

        return "\n".join(lines)

    def _append_top_level_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        is_component_owned: Any,
        is_template_owned: Any,
    ) -> None:
        """
        Append top-level geometry records for a sorted object collection.
        """
        for obj in sorted(
            geometry_ctx._source_admission.admitted(objects),
            key=lambda item: str(getattr(item, "unique_id", "")),
        ):
            if is_component_owned(obj) or is_template_owned(obj):
                continue
            if _is_parent_bound_geometry_child(obj):
                continue
            geometry_record = obj.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(
                        cast("SchGeometryRecord", geometry_record),
                        obj,
                    )
                )

    def _source_object_index(self, source_object: object) -> int | None:
        cached_position = self._geometry_source_positions.get(id(source_object))
        if (
            cached_position is not None
            and 0 <= cached_position < len(self.all_objects)
            and self.all_objects[cached_position] is source_object
        ):
            return cached_position
        record_index = _optional_int(getattr(source_object, "_record_index", None))
        if (
            record_index is not None
            and 0 <= record_index < len(self.all_objects)
            and self.all_objects[record_index] is source_object
        ):
            return record_index
        return next(
            (
                position
                for position, candidate in enumerate(self.all_objects)
                if candidate is source_object
            ),
            None,
        )

    def _tag_source_geometry_record(
        self,
        record: "SchGeometryRecord",
        source_object: object,
    ) -> "SchGeometryRecord":
        """Attach render-local source correspondence without changing public IR."""
        render_group_id = str(
            record.unique_id or ""
        ) or self._geometry_render_group_ids.get(
            id(source_object),
            "",
        )
        from .altium_sch_geometry_oracle import (
            _source_render_group_identity,
            _with_private_record_group_identity,
        )

        if not render_group_id:
            return replace(
                record,
                source_object_index=self._source_object_index(source_object),
            )
        tagged = _with_private_record_group_identity(
            record,
            render_source_id=id(source_object),
            render_group_id=render_group_id,
            render_group_identity=_source_render_group_identity(
                source_object,
                render_group_id,
            ),
        )
        return replace(
            tagged,
            source_object_index=self._source_object_index(source_object),
        )

    def _refresh_harness_bundle_field_identities(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> None:
        from .altium_sch_paint_order import (
            _harness_bundle_field_role,
        )

        for source_object in source_admission.admitted(self.all_objects):
            if type(source_admission.parent(source_object)) is AltiumSchHarnessBundle:
                role = _harness_bundle_field_role(source_object)
                if role is not None:
                    _as_dynamic(
                        source_object
                    )._harness_bundle_field_candidate_role = role
        selected_fields = self._selected_harness_bundle_fields(
            tuple(source_admission.admitted(self.all_objects)), source_admission
        )
        selected_with_roles: list[tuple[object, str]] = []
        for field in selected_fields:
            role = _harness_bundle_field_role(field)
            if role is not None:
                selected_with_roles.append((field, role))
        for source_object in source_admission.admitted(self.all_objects):
            if hasattr(source_object, "_harness_bundle_field_role"):
                delattr(source_object, "_harness_bundle_field_role")
        for field, role in selected_with_roles:
            _as_dynamic(field)._harness_bundle_field_role = role
            if role == "length" and isinstance(field, AltiumSchParameter):
                field.name = "Length"

    @staticmethod
    def _selected_harness_bundle_fields(
        source_objects: Collection[object],
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> tuple[object, ...]:
        from .altium_sch_paint_order import _harness_bundle_field_objects

        children_by_bundle: dict[int, list[object]] = {}
        for source_object in source_objects:
            parent = source_admission.parent(source_object)
            if type(parent) is AltiumSchHarnessBundle:
                children_by_bundle.setdefault(id(parent), []).append(source_object)
        selected: list[object] = []
        for children in children_by_bundle.values():
            selected.extend(_harness_bundle_field_objects(children))
        return tuple(selected)

    def _append_callable_geometry_records(
        self,
        records: list[Any],
        objects: Any,
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        should_skip: Any,
    ) -> None:
        """
        Append geometry for a sorted collection with optional ownership filtering.
        """
        for obj in sorted(
            geometry_ctx._source_admission.admitted(objects),
            key=lambda item: str(getattr(item, "unique_id", "")),
        ):
            if should_skip(obj):
                continue
            if _is_parent_bound_geometry_child(obj):
                continue
            to_geometry = getattr(obj, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(
                        cast("SchGeometryRecord", geometry_record),
                        obj,
                    )
                )

    def _append_image_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        native_svg_hidden_image_ids: set[str],
        image_parameter_owned_ids: Collection[int],
    ) -> None:
        """
        Append image geometry while tracking hidden template-owned image ids.
        """
        for image in sorted(
            geometry_ctx._source_admission.admitted(self.images),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(image):
                continue
            if ownership.is_template_owned(image):
                if not ownership.show_template_graphics:
                    native_svg_hidden_image_ids.add(
                        str(getattr(image, "unique_id", "") or "")
                    )
                continue
            if (
                ownership.template_idx is not None
                and getattr(image, "owner_index", None) == ownership.template_idx
                and not ownership.show_template_graphics
            ):
                native_svg_hidden_image_ids.add(
                    str(getattr(image, "unique_id", "") or "")
                )
            geometry_record = image.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_independent_image_geometry_record(
                        geometry_record,
                        image,
                        image_parameter_owned_ids,
                    )
                )

    def _tag_independent_image_geometry_record(
        self,
        record: "SchGeometryRecord",
        image: AltiumSchImage,
        image_parameter_owned_ids: Collection[int],
    ) -> "SchGeometryRecord":
        tagged = self._tag_source_geometry_record(record, image)
        if id(image) not in image_parameter_owned_ids:
            return tagged
        return replace(tagged, extras={**tagged.extras, "skip_svg": True})

    def _append_image_parameter_model_target_records(
        self,
        records: list[SchGeometryRecord],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        image_parameter_owned_ids: Collection[int],
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryRecord

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
        for source in geometry_ctx._source_admission.admitted(self.all_objects):
            if id(source) in emitted_ids:
                continue
            if (
                not isinstance(source, AltiumSchImageParameter)
                and getattr(source, "record_type", None) not in model_types
            ):
                continue
            if id(source) not in image_parameter_owned_ids:
                continue
            to_geometry = getattr(source, "to_geometry", None)
            if not callable(to_geometry):
                continue
            raw_record = to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if not isinstance(raw_record, SchGeometryRecord):
                continue
            tagged = self._tag_source_geometry_record(raw_record, source)
            records.append(replace(tagged, extras={**tagged.extras, "skip_svg": True}))

    def _append_special_graphic_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append note/text-frame/no-erc/parameter-set geometry records.
        """
        special_graphics = (
            obj
            for obj in self.objects
            if isinstance(
                obj,
                (
                    AltiumSchTextFrame,
                    AltiumSchNote,
                    AltiumSchNoErc,
                    AltiumSchParameterSet,
                ),
            )
        )
        self._append_callable_geometry_records(
            records,
            special_graphics,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda obj: (
                ownership.is_component_owned(obj) or ownership.is_template_owned(obj)
            ),
        )

    def _build_geometry_ownership_state(
        self, geometry_ctx: Any
    ) -> _SchGeometryOwnershipState:
        """
        Collect ownership and multipart component metadata for geometry export.
        """
        from ._sch_source_projection import _logical_component_suffix_ids
        from itertools import chain

        template_obj = next(
            (
                obj
                for obj in geometry_ctx._source_admission.admitted(
                    geometry_ctx._source_admission.source_objects
                )
                if isinstance(obj, AltiumSchTemplate)
            ),
            None,
        )
        template_idx_raw = (
            getattr(template_obj, "_record_index", None)
            if template_obj is not None
            else None
        )
        try:
            template_idx = (
                int(template_idx_raw) if template_idx_raw is not None else None
            )
        except (TypeError, ValueError):
            template_idx = None

        show_template_graphics = bool(self.sheet and self.sheet.show_template_graphics)
        multipart_suffix_component_ids = _logical_component_suffix_ids(
            chain(
                geometry_ctx._source_admission.admitted(
                    geometry_ctx._source_admission.source_objects
                ),
                chain.from_iterable(
                    geometry_ctx._source_admission.admitted(
                        comp.children
                        or chain(comp.graphics, comp.pins, comp.parameters)
                    )
                    for comp in geometry_ctx._source_admission.admitted(self.components)
                ),
            ),
            geometry_ctx._source_admission.admitted(self.components),
        )

        component_owner_indexes: set[int] = set()
        pin_owner_indexes: set[int] = set()
        for index, obj in enumerate(
            geometry_ctx._source_admission.source_objects, start=1
        ):
            if isinstance(obj, AltiumSchComponent):
                component_owner_indexes.add(index)
                for attr_name in ("_record_index", "index_in_sheet"):
                    attr_value = getattr(obj, attr_name, None)
                    if attr_value is None:
                        continue
                    try:
                        component_owner_indexes.add(int(attr_value))
                    except (TypeError, ValueError):
                        continue
            elif isinstance(obj, AltiumSchPin):
                pin_owner_indexes.add(index)
                attr_value = getattr(obj, "_record_index", None)
                if attr_value is None:
                    continue
                try:
                    pin_owner_indexes.add(int(attr_value))
                except (TypeError, ValueError):
                    continue

        return _SchGeometryOwnershipState(
            template_obj=template_obj,
            template_idx=template_idx,
            show_template_graphics=show_template_graphics,
            multipart_suffix_component_ids=multipart_suffix_component_ids,
            component_owner_indexes=component_owner_indexes,
            pin_owner_indexes=pin_owner_indexes,
            source_admission=geometry_ctx._source_admission,
        )

    def _append_passive_top_level_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        requested_render_options: Any,
        native_svg_hidden_image_ids: set[str],
    ) -> None:
        """
        Append top-level non-component records that do not need part-aware handling.
        """
        image_parameter_owned_ids = (
            geometry_ctx._source_admission.descendant_ids_of_type(
                geometry_ctx._source_admission.source_objects,
                AltiumSchImageParameter,
            )
        )
        self._append_callable_geometry_records(
            records,
            self.buses,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda _obj: False,
        )
        self._append_callable_geometry_records(
            records,
            self.graphics,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            should_skip=lambda obj: (
                ownership.is_component_owned(obj) or ownership.is_template_owned(obj)
            ),
        )
        self._append_image_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
            image_parameter_owned_ids=image_parameter_owned_ids,
        )
        self._append_image_parameter_model_target_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            image_parameter_owned_ids=image_parameter_owned_ids,
        )
        self._append_special_graphic_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )

        self._append_template_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            ownership=ownership,
            requested_render_options=requested_render_options,
        )

        self._append_top_level_geometry_records(
            records,
            self.labels,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.net_labels,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )

    def _append_template_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        requested_render_options: Any,
    ) -> None:
        """
        Append template-owned geometry and the wrapper template record.
        """
        if not (
            ownership.show_template_graphics
            and ownership.template_obj is not None
            and ownership.template_idx is not None
        ):
            return

        template_geometry_ctx = geometry_ctx
        if (
            requested_render_options.truncate_font_size_for_baseline
            and not requested_render_options.fallback_project_parameters_for_star
        ):
            template_geometry_ctx = geometry_ctx.copy()
            template_geometry_ctx.options = replace(
                requested_render_options,
                fallback_project_parameters_for_star=True,
            )

        template_child_records = []
        for source_position, obj in enumerate(self.all_objects):
            if not geometry_ctx._source_admission.admits(obj):
                continue
            if getattr(obj, "owner_index", None) != ownership.template_idx:
                continue
            geometry_record = self._template_child_geometry_record(
                obj,
                template_geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                template_index=ownership.template_idx,
                source_position=source_position,
                fallback_project_parameters_for_star=(
                    requested_render_options.fallback_project_parameters_for_star
                ),
            )
            if geometry_record is None:
                continue
            geometry_record = self._tag_source_geometry_record(geometry_record, obj)
            records.append(geometry_record)
            template_child_records.append(geometry_record)

        template_record = ownership.template_obj.to_geometry(
            template_geometry_ctx,
            document_id=document_id,
            child_records=template_child_records,
            unique_id_override=f"TPL{int(ownership.template_idx):05d}",
            units_per_px=units_per_px,
        )
        if template_record is not None:
            records.append(
                self._tag_source_geometry_record(
                    template_record,
                    ownership.template_obj,
                )
            )

    def _template_child_geometry_record(
        self,
        obj: object,
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        template_index: int,
        source_position: int,
        fallback_project_parameters_for_star: bool,
    ) -> "SchGeometryRecord | None":
        """Build one renderable template child with a render-local identity."""
        if (
            isinstance(obj, AltiumSchLabel)
            and str(getattr(obj, "text", "") or "").startswith("=PCB_")
            and not fallback_project_parameters_for_star
        ):
            return None
        # These children need parent-relative arguments unavailable here and
        # should never be direct template primitives.
        if isinstance(obj, (AltiumSchHarnessEntry, AltiumSchSheetEntry)):
            return None
        to_geometry = getattr(obj, "to_geometry", None)
        if not callable(to_geometry):
            return None
        geometry_record = to_geometry(
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        if geometry_record is None:
            return None
        return _with_template_render_identity(
            cast("SchGeometryRecord", geometry_record),
            document_id=document_id,
            template_index=template_index,
            source_index=int(getattr(obj, "_record_index", None) or source_position),
            units_per_px=units_per_px,
        )

    @staticmethod
    def _component_render_parameters(
        component: AltiumSchComponent,
        admission: _SourceAdmission,
    ) -> list[AltiumSchParameter]:
        children = (
            component._projected_and_legacy_geometry_children(admission)
            if admission.parent_by_source_id is not None
            else component.children or component.parameters
        )
        return [
            child
            for child in admission.admitted(children)
            if isinstance(child, AltiumSchParameter)
        ]

    def _prepare_project_component_bounds(
        self,
        geometry_ctx: SchSvgRenderContext,
        project_states: Mapping[int, _ComponentProjectRenderState],
    ) -> _PreparedProjectComponentBounds | None:
        if not any(state.variation_kind == 1 for state in project_states.values()):
            return None
        from functools import partial

        from ._altium_sch_bounds_accessibility import (
            _build_captured_bounds_accessibility_index,
        )
        from ._altium_sch_bounds_source_tree import _build_bounds_document_owner_index
        from ._altium_sch_bounds_traversal import _build_bounds_traversal_index
        from ._altium_sch_component_bounds import _portable_string_bounds
        from ._altium_sch_component_bounds_query import (
            _ComponentBoundsQueries,
        )

        sources = tuple(geometry_ctx._source_admission.source_objects)
        parents = geometry_ctx._source_admission.parent_by_source_id
        if parents is None:
            parents = {
                id(source): getattr(source, "parent", None) for source in sources
            }
        tree = _build_bounds_source_tree(sources, parents, max_sources=len(sources))
        traversal = _build_bounds_traversal_index(tree)
        accessibility = _build_captured_bounds_accessibility_index(
            tree, self._bounds_import_accessibility
        )
        labels, parameter_sets = self._project_bounds_text_captures(
            sources, geometry_ctx
        )
        document_states = self._project_bounds_document_states(sources)
        document_owners = _build_bounds_document_owner_index(
            tree,
            tree.parents,
            document_states,
            max_sources=len(sources),
        )
        query = _ComponentBoundsQueries(
            traversal,
            accessibility,
            document_owners=document_owners,
            label_text=labels,
            parameter_set_text=parameter_sets,
            measure_text=partial(_portable_string_bounds, geometry_ctx),
        )
        return _PreparedProjectComponentBounds(
            query,
            {id(source): index for index, source in enumerate(sources)},
        )

    @staticmethod
    def _project_bounds_text_captures(
        sources: tuple[object, ...],
        geometry_ctx: SchSvgRenderContext,
    ) -> tuple[dict[int, _BoundsLabelText], dict[int, _BoundsParameterSetText]]:
        from ._altium_sch_component_bounds_query import (
            _BoundsLabelText,
            _BoundsParameterSetText,
        )

        labels = {
            index: _BoundsLabelText(
                source,
                geometry_ctx.substitute_parameters(source.text),
                int(source.font_id),
                None,
            )
            for index, source in enumerate(sources)
            if type(source) is AltiumSchLabel
        }
        parameter_sets = {
            index: _BoundsParameterSetText(
                source,
                source._get_display_string(geometry_ctx),
            )
            for index, source in enumerate(sources)
            if type(source) is AltiumSchParameterSet
        }
        return labels, parameter_sets

    def _project_bounds_document_states(
        self, sources: tuple[object, ...]
    ) -> dict[int, _BoundsDocumentOwnerState]:
        from ._altium_sch_bounds_source_tree import _BoundsDocumentOwnerState

        return {
            index: _BoundsDocumentOwnerState(source, True, True)
            for index, source in enumerate(sources)
            if source is self.sheet
        }

    def _apply_component_project_render_state(
        self,
        component: AltiumSchComponent,
        component_ctx: SchSvgRenderContext,
        state: _ComponentProjectRenderState,
        bounds_state: _PreparedProjectComponentBounds | None,
    ) -> SchSvgRenderContext:
        from ._altium_sch_component_overlay import (
            _ComponentDrawingColorState,
            _ComponentOverlayCapture,
            _component_overlay_palette,
        )
        from ._altium_sch_component_variant import _ComponentVariantCapture

        if state.source is not component:
            raise ValueError("component project state has a different source")
        prepared = component_ctx.copy()
        prepared._parameter_render_overrides = state.parameter_overrides
        if state.has_variant_component:
            alternate_context = prepared
            if state.alternate_document is not None:
                alternate_context = prepared.copy()
                alternate_context.font_manager = state.alternate_document.font_manager
                alternate_context._source_admission = (
                    state.alternate_document._prepare_render_source_admission()
                )
            prepared._component_variant_capture = _ComponentVariantCapture(
                source=component,
                has_variant_component=True,
                alternate=state.alternate,
                is_multi_variant_export=False,
                document_show_alternate_symbols=state.show_alternate_symbols,
                project_show_alternate_symbols=state.show_alternate_symbols,
                alternate_context=alternate_context,
            )
        if state.variation_kind != 1:
            return prepared
        if bounds_state is None:
            raise RuntimeError("not-fitted component bounds were not prepared")
        root = bounds_state.index_by_source_id.get(id(component))
        if root is None:
            raise ValueError("not-fitted component is outside the bounds source tree")
        colors, primitive_color = _component_overlay_palette(
            prepared,
            _ComponentDrawingColorState(
                owner_document_present=True,
                _draw_disabled=False,
                _draw_dimmed=False,
                _draw_compilation_masked=prepared.component_compile_masked is True,
                _draw_editable_in_current_view=True,
            ),
        )
        prepared._component_overlay_capture = _ComponentOverlayCapture(
            source=component,
            missing=False,
            variant_option_present=True,
            graphics="cross",
            use_text=False,
            bounds=bounds_state.query.calculate(root),
            colors=colors,
            primitive_color=primitive_color,
        )
        return prepared

    @staticmethod
    def _component_designator_record(
        source_parameters: Iterable[AltiumSchParameter],
    ) -> AltiumSchDesignator | None:
        return next(
            (
                parameter
                for parameter in source_parameters
                if isinstance(parameter, AltiumSchDesignator)
            ),
            None,
        )

    @staticmethod
    def _component_designator_override_keys(
        component: AltiumSchComponent,
        designator: AltiumSchDesignator,
    ) -> tuple[str, ...]:
        keys: list[str] = []
        if designator.unique_id:
            keys.append(str(designator.unique_id))
        if component.unique_id:
            keys.append(f"component:{component.unique_id}:designator")
        return tuple(keys)

    @staticmethod
    def _logical_designator_display(
        designator: AltiumSchDesignator,
        component_ctx: SchSvgRenderContext,
        component_parameters: Mapping[str, str],
    ) -> str:
        from .altium_netlist_common import _evaluate_altium_expression

        display = str(designator.text or "")
        if not display.startswith("="):
            return display
        parameters = dict(component_ctx.project_parameters)
        parameters.update(component_parameters)
        return _evaluate_altium_expression(display[1:], parameters)

    @staticmethod
    def _managed_component_part_suffix(
        component: AltiumSchComponent,
        component_ctx: SchSvgRenderContext,
        *,
        show_multipart_suffix: bool,
    ) -> str:
        from .altium_netlist_common import _component_part_alpha_suffix

        if not show_multipart_suffix or component.part_count <= 2:
            return ""
        method = component_ctx.options.multipart_naming_method
        if type(method) is not int or method not in (0, 1):
            raise ValueError("multipart naming method must be 0 or 1")
        separator = component_ctx.options.multipart_separator
        if not isinstance(separator, str):
            raise ValueError("multipart separator must be a string")
        if method != 0:
            return f"{separator}{component.current_part_id}"
        return _component_part_alpha_suffix(
            part_count=component.part_count - 1,
            current_part_id=component.current_part_id,
        )

    @classmethod
    def _with_component_designator_display(
        cls,
        component: AltiumSchComponent,
        component_ctx: SchSvgRenderContext,
        source_parameters: list[AltiumSchParameter],
        component_parameters: Mapping[str, str],
        *,
        show_multipart_suffix: bool,
    ) -> SchSvgRenderContext:
        designator = cls._component_designator_record(source_parameters)
        if designator is None:
            return component_ctx
        keys = cls._component_designator_override_keys(component, designator)
        if any(key in component_ctx.designator_text_overrides for key in keys):
            return component_ctx
        display = cls._logical_designator_display(
            designator, component_ctx, component_parameters
        ) + cls._managed_component_part_suffix(
            component,
            component_ctx,
            show_multipart_suffix=show_multipart_suffix,
        )
        if display == designator.text:
            return component_ctx
        prepared = component_ctx.copy()
        prepared.designator_text_overrides = dict(
            component_ctx.designator_text_overrides
        )
        for key in keys:
            prepared.designator_text_overrides[key] = display
        return prepared

    def _append_component_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
        component_project_states: Mapping[int, _ComponentProjectRenderState] | None,
    ) -> None:
        """
        Append component wrappers plus their part-aware child geometry.
        """
        from .altium_sch_paint_order import _component_bound_children

        image_parameter_owned_ids = (
            geometry_ctx._source_admission.descendant_ids_of_type(
                geometry_ctx._source_admission.source_objects,
                AltiumSchImageParameter,
            )
        )

        project_states = component_project_states or {}
        bounds_state = self._prepare_project_component_bounds(
            geometry_ctx,
            project_states,
        )
        for comp in sorted(
            geometry_ctx._source_admission.admitted(self.components),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if id(comp) in image_parameter_owned_ids:
                continue
            source_parameters = [
                parameter
                for parameter in _component_bound_children(
                    comp,
                    self._component_render_parameters(
                        comp, geometry_ctx._source_admission
                    ),
                )
                if isinstance(parameter, AltiumSchParameter)
            ]
            comp_ctx = geometry_ctx
            if getattr(comp, "override_colors", False):
                comp_ctx = comp_ctx.with_color_overrides(
                    area_color=getattr(comp, "area_color", None),
                    line_color=getattr(comp, "color", None),
                    pin_color=getattr(comp, "pin_color", None),
                )
            comp_ctx = comp_ctx.with_component_masking(
                self._component_is_compile_masked(
                    comp, geometry_ctx.compile_mask_bounds
                )
            )

            project_state = project_states.get(id(comp))
            if project_state is not None:
                comp_ctx = self._apply_component_project_render_state(
                    comp,
                    comp_ctx,
                    project_state,
                    bounds_state,
                )

            comp_params = {
                "LibReference": comp.lib_reference,
                "ComponentDescription": comp.component_description,
            }
            if not getattr(comp_ctx, "native_svg_export", False):
                comp_params["Value"] = comp.lib_reference
            for param in source_parameters:
                if (
                    hasattr(param, "name")
                    and hasattr(param, "text")
                    and not param.text.startswith("=")
                ):
                    comp_params[param.name] = param.text

            has_explicit_value_param = any(
                hasattr(param, "name")
                and isinstance(getattr(param, "name", None), str)
                and param.name.lower() == "value"
                for param in source_parameters
            )

            def resolve_component_param_value(name: str) -> str | None:
                name_lower = name.lower()
                for key, value in comp_params.items():
                    if key.lower() == name_lower:
                        return value
                return None

            show_multipart_suffix = id(
                comp
            ) in ownership.multipart_suffix_component_ids or isinstance(
                comp, AltiumSchHarnessComponent
            )

            comp_ctx = self._with_component_designator_display(
                comp,
                comp_ctx,
                source_parameters,
                comp_params,
                show_multipart_suffix=show_multipart_suffix,
            )
            text_restores: list[tuple[object, str]] = []
            for param in source_parameters:
                param_text = getattr(param, "text", None)
                if isinstance(param_text, str) and param_text.startswith("="):
                    param_name = param_text[1:]
                    resolved_text = resolve_component_param_value(param_name)
                    if resolved_text is not None:
                        text_restores.append((param, param_text))
                        _as_dynamic(param).text = resolved_text
                    elif (
                        getattr(comp_ctx, "native_svg_export", False)
                        and param_name.lower() == "value"
                        and not has_explicit_value_param
                    ):
                        text_restores.append((param, param_text))
                        _as_dynamic(param).text = ""
            try:
                self._append_single_component_geometry_records(
                    records,
                    comp,
                    comp_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
            finally:
                for param, original_text in reversed(text_restores):
                    _as_dynamic(param).text = original_text

    def _append_single_component_geometry_records(
        self,
        records: list[Any],
        comp: Any,
        comp_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
    ) -> None:
        """
        Append geometry for one component and its nested children.
        """
        geometry_record = comp.to_geometry(
            comp_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        if geometry_record is not None:
            records.append(self._tag_source_geometry_record(geometry_record, comp))

        for impl_list in sorted(
            (
                param
                for param in comp_ctx._source_admission.admitted(
                    getattr(comp, "parameters", [])
                )
                if isinstance(param, AltiumSchImplementationList)
            ),
            key=lambda obj: str(getattr(obj, "_record_index", "")),
        ):
            for implementation in sorted(
                comp_ctx._source_admission.admitted(getattr(impl_list, "children", [])),
                key=lambda obj: str(getattr(obj, "_record_index", "")),
            ):
                geometry_record = implementation.to_geometry(
                    comp_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                if geometry_record is not None:
                    records.append(
                        self._tag_source_geometry_record(
                            geometry_record,
                            implementation,
                        )
                    )

                for child in sorted(
                    comp_ctx._source_admission.admitted(
                        getattr(implementation, "children", [])
                    ),
                    key=lambda obj: str(getattr(obj, "_record_index", "")),
                ):
                    if not isinstance(child, AltiumSchMapDefinerList):
                        continue
                    geometry_record = child.to_geometry(
                        comp_ctx,
                        document_id=document_id,
                        units_per_px=units_per_px,
                    )
                    if geometry_record is not None:
                        records.append(
                            self._tag_source_geometry_record(
                                cast("SchGeometryRecord", geometry_record),
                                child,
                            )
                        )

        self._append_component_graphics_parameters_and_pins(
            records,
            comp,
            comp_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )

    def _append_component_graphics_parameters_and_pins(
        self,
        records: list[SchGeometryRecord],
        comp: AltiumSchComponent,
        comp_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> None:
        """
        Append geometry for component graphics, parameters, and pins.
        """
        from .altium_sch_paint_order import (
            _component_field_objects,
        )

        parameters = self._component_render_parameters(comp, comp_ctx._source_admission)
        field_sources = parameters
        field_ids = {
            id(field) for field in _component_field_objects(comp, field_sources)
        }
        if comp.show_hidden_fields:
            comp_ctx = comp_ctx.copy()
            comp_ctx._visible_hidden_parameter_ids = frozenset(
                id(parameter)
                for parameter in parameters
                if isinstance(parameter, AltiumSchParameter)
                and not isinstance(parameter, AltiumSchImageParameter)
            )
        component_display_mode = int(getattr(comp, "display_mode", 0) or 0)
        self._append_component_graphic_records(
            records,
            comp,
            comp_ctx,
            component_display_mode=component_display_mode,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        self._append_component_parameter_records(
            records,
            comp,
            comp_ctx,
            parameters=parameters,
            field_ids=field_ids,
            component_display_mode=component_display_mode,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        self._append_component_pin_records(
            records,
            comp,
            comp_ctx,
            component_display_mode=component_display_mode,
            document_id=document_id,
            units_per_px=units_per_px,
        )

    def _append_component_graphic_records(
        self,
        records: list[SchGeometryRecord],
        comp: AltiumSchComponent,
        comp_ctx: SchSvgRenderContext,
        *,
        component_display_mode: int,
        document_id: str,
        units_per_px: int,
    ) -> None:
        for graphic in sorted(
            comp_ctx._source_admission.admitted(getattr(comp, "graphics", [])),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if _is_parent_bound_geometry_child(graphic):
                continue
            if not record_belongs_to_display_mode(graphic, component_display_mode):
                continue
            owner_part = getattr(graphic, "owner_part_id", None)
            oracle_only_record = (
                owner_part is not None
                and owner_part > 0
                and owner_part != comp.current_part_id
            )
            to_geometry = getattr(graphic, "to_geometry", None)
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                geometry_record = cast("SchGeometryRecord", geometry_record)
                if oracle_only_record:
                    geometry_record = replace(
                        geometry_record,
                        extras={
                            **dict(getattr(geometry_record, "extras", {}) or {}),
                            "skip_svg": True,
                        },
                    )
                records.append(
                    self._tag_source_geometry_record(geometry_record, graphic)
                )
                if isinstance(
                    graphic, (AltiumSchHarnessConnector, AltiumSchSheetSymbol)
                ):
                    self._append_hierarchy_child_target_records(
                        records,
                        graphic,
                        comp_ctx,
                        document_id=document_id,
                        units_per_px=units_per_px,
                    )

    def _append_component_parameter_records(
        self,
        records: list[SchGeometryRecord],
        comp: AltiumSchComponent,
        comp_ctx: SchSvgRenderContext,
        *,
        parameters: list[AltiumSchParameter],
        field_ids: set[int],
        component_display_mode: int,
        document_id: str,
        units_per_px: int,
    ) -> None:
        from .altium_sch_paint_order import _component_field_role

        for param in sorted(
            parameters,
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if _component_field_role(param) is not None and id(param) not in field_ids:
                continue
            if not isinstance(param, AltiumSchImageParameter):
                if isinstance(param, AltiumSchParameter):
                    if param.is_hidden and not comp.show_hidden_fields:
                        continue
                elif not record_belongs_to_display_mode(param, component_display_mode):
                    continue
            to_geometry = getattr(param, "to_geometry", None)
            if (
                isinstance(param, AltiumSchImageParameter)
                and _component_field_role(param) == "comment"
                and id(param) in field_ids
            ):
                to_geometry = param._to_component_comment_geometry
            if not callable(to_geometry):
                continue
            geometry_record = to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(
                        cast("SchGeometryRecord", geometry_record),
                        param,
                    )
                )

    def _append_component_pin_records(
        self,
        records: list[SchGeometryRecord],
        comp: AltiumSchComponent,
        comp_ctx: SchSvgRenderContext,
        *,
        component_display_mode: int,
        document_id: str,
        units_per_px: int,
    ) -> None:
        for pin in sorted(
            comp_ctx._source_admission.admitted(getattr(comp, "pins", [])),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if not record_belongs_to_display_mode(pin, component_display_mode):
                continue
            owner_part = getattr(pin, "owner_part_id", None)
            oracle_only_record = (
                owner_part is not None
                and owner_part > 0
                and owner_part != comp.current_part_id
            )
            if getattr(pin, "is_hidden", False) and not getattr(
                comp, "show_hidden_pins", False
            ):
                continue
            geometry_record = pin.to_geometry(
                comp_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                if oracle_only_record:
                    geometry_record = replace(
                        geometry_record,
                        extras={
                            **dict(getattr(geometry_record, "extras", {}) or {}),
                            "skip_svg": True,
                        },
                    )
                records.append(self._tag_source_geometry_record(geometry_record, pin))

    def _restore_oracle_hidden_component_parameter_records(
        self,
        records: list[SchGeometryRecord],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryRecord

        # An isolated target never inherits its owner's show-hidden override.
        # Component records already hold their independently drawn child ops.
        targets = {
            self._source_object_index(parameter): parameter
            for parameter in geometry_ctx._source_admission.admitted(self.parameters)
            if type(parameter) in (AltiumSchParameter, AltiumSchDesignator)
            and parameter.is_hidden
            and ownership.is_component_owned(parameter)
        }
        emitted = {
            record.source_object_index: index
            for index, record in enumerate(records)
            if record.source_object_index in targets
        }
        for source_index, parameter in targets.items():
            record = parameter.to_geometry(
                geometry_ctx, document_id=document_id, units_per_px=units_per_px
            )
            assert isinstance(record, SchGeometryRecord)
            record = self._tag_source_geometry_record(record, parameter)
            existing_index = emitted.get(source_index)
            if existing_index is None:
                emitted[source_index] = len(records)
                records.append(record)
            else:
                records[existing_index] = record

    def _append_parameter_and_connector_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append top-level parameters plus port/power/cross-sheet geometry.
        """
        emitted_parameter_ids: set[str] = set()
        for param in sorted(
            geometry_ctx._source_admission.admitted(self.parameters),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if not self._is_document_parameter_source(
                param, geometry_ctx._source_admission
            ):
                continue
            if (
                ownership.is_component_owned(param)
                or ownership.is_template_owned(param)
                or ownership.is_pin_owned(param)
            ):
                continue
            geometry_record = param.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(self._tag_source_geometry_record(geometry_record, param))
            emitted_parameter_ids.add(str(getattr(param, "unique_id", "") or ""))

        for parameter_set in sorted(
            (
                obj
                for obj in geometry_ctx._source_admission.admitted(self.parameter_sets)
                if not ownership.is_component_owned(obj)
                and not ownership.is_template_owned(obj)
            ),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            for child_param in sorted(
                geometry_ctx._source_admission.admitted(
                    getattr(parameter_set, "parameters", [])
                ),
                key=lambda obj: str(getattr(obj, "unique_id", "")),
            ):
                child_unique_id = str(getattr(child_param, "unique_id", "") or "")
                if child_unique_id in emitted_parameter_ids:
                    continue
                emitted_parameter_ids.add(child_unique_id)
                geometry_record = child_param.to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                if geometry_record is not None:
                    records.append(
                        self._tag_source_geometry_record(
                            geometry_record,
                            child_param,
                        )
                    )

        self._append_top_level_geometry_records(
            records,
            self.ports,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.power_ports,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )
        self._append_top_level_geometry_records(
            records,
            self.cross_sheet_connectors,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            is_component_owned=ownership.is_component_owned,
            is_template_owned=ownership.is_template_owned,
        )

    @staticmethod
    def _hierarchy_geometry_children(
        owner: AltiumSchHarnessConnector | AltiumSchSheetSymbol,
        admission: _SourceAdmission,
    ) -> list[object]:
        from ._sch_source_projection import _hierarchy_render_children

        return _hierarchy_render_children(owner, admission)

    def _append_hierarchy_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append harness connector and sheet symbol geometry.
        """
        admission = geometry_ctx._source_admission
        for harness_connector in sorted(
            geometry_ctx._source_admission.admitted(self.harness_connectors),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(
                harness_connector
            ) or ownership.is_template_owned(harness_connector):
                continue
            connector_children = self._hierarchy_geometry_children(
                harness_connector,
                admission,
            )
            geometry_record = harness_connector.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(
                        geometry_record,
                        harness_connector,
                    )
                )
            self._append_hierarchy_child_target_records(
                records,
                harness_connector,
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                children=connector_children,
            )

        for sheet_symbol in sorted(
            geometry_ctx._source_admission.admitted(self.sheet_symbols),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(sheet_symbol):
                continue
            symbol_children = self._hierarchy_geometry_children(
                sheet_symbol,
                admission,
            )
            geometry_record = sheet_symbol.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(geometry_record, sheet_symbol)
                )
            self._append_hierarchy_child_target_records(
                records,
                sheet_symbol,
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                children=symbol_children,
            )

    def _append_hierarchy_child_target_records(
        self,
        records: list[SchGeometryRecord],
        owner: AltiumSchHarnessConnector | AltiumSchSheetSymbol,
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        children: list[object] | None = None,
    ) -> None:
        from ._sch_source_projection import _hierarchy_bound_children
        from .altium_sch_geometry_oracle import SchGeometryRecord

        if children is None:
            children = self._hierarchy_geometry_children(
                owner, geometry_ctx._source_admission
            )
        if isinstance(owner, AltiumSchHarnessConnector):
            parent_x, parent_y = geometry_ctx.transform_point(
                owner.location.x, owner.location.y
            )
            child_records = owner._child_geometry_records(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                parent_x=parent_x,
                parent_y=parent_y,
                parent_width=owner.xsize * geometry_ctx.scale,
                parent_height=owner.ysize * geometry_ctx.scale,
            )
        else:
            child_records = []
            parent_x, parent_y = geometry_ctx.transform_point(
                owner.location.x, owner.location.y
            )
            for child in _hierarchy_bound_children(
                owner,
                children,
                parent_by_source_id=geometry_ctx._source_admission.parent_by_source_id,
            ):
                kwargs: dict[str, object] = {}
                if isinstance(child, AltiumSchSheetEntry):
                    kwargs = {
                        "parent_x": parent_x,
                        "parent_y": parent_y,
                        "parent_width": owner.x_size * geometry_ctx.scale,
                        "parent_height": owner.y_size * geometry_ctx.scale,
                    }
                to_geometry = getattr(child, "to_geometry", None)
                if not callable(to_geometry):
                    continue
                child_record = to_geometry(
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                    **kwargs,
                )
                if isinstance(child_record, SchGeometryRecord):
                    child_records.append((child, child_record))
        records.extend(
            self._tag_source_geometry_record(record, child)
            for child, record in child_records
        )

    def _append_signal_and_wire_geometry_records(
        self,
        records: list[Any],
        geometry_ctx: Any,
        *,
        document_id: str,
        units_per_px: int,
        ownership: _SchGeometryOwnershipState,
    ) -> None:
        """
        Append signal harness and wire geometry.
        """
        for signal_harness in sorted(
            geometry_ctx._source_admission.admitted(self.signal_harnesses),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if ownership.is_component_owned(
                signal_harness
            ) or ownership.is_template_owned(signal_harness):
                continue
            if isinstance(
                geometry_ctx._source_admission.parent(signal_harness),
                AltiumSchHarnessBundle,
            ):
                continue
            geometry_record = signal_harness.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(
                    self._tag_source_geometry_record(geometry_record, signal_harness)
                )

        for wire in sorted(
            geometry_ctx._source_admission.admitted(self.wires),
            key=lambda obj: str(getattr(obj, "unique_id", "")),
        ):
            if isinstance(
                geometry_ctx._source_admission.parent(wire), AltiumSchHarnessBundle
            ):
                continue
            geometry_record = wire.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if geometry_record is not None:
                records.append(self._tag_source_geometry_record(geometry_record, wire))

    @classmethod
    def _append_harness_layout_geometry_records_for_source_objects(
        cls,
        records: list[SchGeometryRecord],
        source_objects: Collection[object],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        eligible_source_ids: Collection[int] | None = None,
        parent_by_source_id: Mapping[int, object | None] | None = None,
        refresh_compile_mask_state: bool = False,
    ) -> None:
        """Apply the SchDoc harness runtime to another schematic container."""
        source_list = list(source_objects)
        projection = cls(create_sheet=False)
        # The adapter owns only the shallow membership list; source objects and
        # their container relationships remain owned by the caller.
        projection._objects = ObjectCollection(source_list)
        projection._geometry_source_positions = {
            id(source_object): index for index, source_object in enumerate(source_list)
        }
        projection._geometry_render_group_ids = dict(geometry_ctx.render_group_ids)
        projection._geometry_parent_by_source_id = (
            dict(parent_by_source_id) if parent_by_source_id is not None else None
        )
        projection._geometry_refresh_compile_mask_state = refresh_compile_mask_state
        projection._append_harness_layout_geometry_records(
            records,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            eligible_source_ids=eligible_source_ids,
        )

    def _append_harness_layout_geometry_records(
        self,
        records: list[SchGeometryRecord],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        eligible_source_ids: Collection[int] | None = None,
    ) -> None:
        """Append supported first-level harness-layout painter records."""
        admission = geometry_ctx._source_admission
        if (
            self._geometry_parent_by_source_id is None
            and admission.parent_by_source_id is not None
        ):
            self._append_harness_layout_geometry_records_for_source_objects(
                records,
                admission.source_objects,
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                eligible_source_ids=eligible_source_ids,
                parent_by_source_id=admission.parent_by_source_id,
                refresh_compile_mask_state=self._geometry_refresh_compile_mask_state,
            )
            return
        source_objects = self._eligible_harness_layout_source_objects(
            tuple(geometry_ctx._source_admission.admitted(self.all_objects)),
            eligible_source_ids,
        )
        owner_children = self._harness_layout_owner_children(
            source_objects,
            self._geometry_parent_by_source_id,
        )
        runtime_owners = self._harness_layout_root_reachable_owners(
            source_objects,
            owner_children,
        )
        bundle_index = self._harness_connection_bundle_index(
            source_objects,
            runtime_owners,
            self._geometry_parent_by_source_id,
        )
        component_index = self._harness_component_index(
            source_objects,
            runtime_owners,
            self._geometry_parent_by_source_id,
            geometry_ctx._source_admission,
        )
        covering_topology = self._harness_covering_topology_index(
            source_objects,
            runtime_owners,
            self._geometry_parent_by_source_id,
        )
        compile_mask_index = (
            self._harness_compile_mask_index(
                source_objects,
                runtime_owners,
            )
            if self._geometry_refresh_compile_mask_state
            else None
        )
        records_by_source_id = self._geometry_records_by_source_id(records)
        parameter_models = self._harness_image_parameter_projections(
            records,
            records_by_source_id,
            geometry_ctx,
            source_objects=source_objects,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        physical_models = self._harness_physical_model_records(
            parameter_models,
            source_objects,
        )
        owner_children = self._without_explicit_main_physical_model_children(
            owner_children,
            physical_models,
        )
        shadowed_fields = self._shadowed_harness_owner_field_ids(source_objects)
        shadowed_record_ids = {
            id(records_by_source_id.pop(source_id))
            for source_id in shadowed_fields
            if source_id in records_by_source_id
        }
        model_child_ids = self._harness_physical_model_child_ids(
            source_objects, geometry_ctx._source_admission
        )
        shadowed_record_ids.update(
            id(records_by_source_id[source_id])
            for source_id in model_child_ids
            if source_id in records_by_source_id
        )
        if shadowed_record_ids:
            records[:] = [
                record for record in records if id(record) not in shadowed_record_ids
            ]
        self._append_harness_layout_owner_geometry_records(
            records,
            records_by_source_id,
            owner_children,
            source_objects,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            bundle_index=bundle_index,
            component_index=component_index,
            covering_topology=covering_topology,
            physical_models=physical_models,
            parameter_models=parameter_models,
            compile_mask_index=compile_mask_index,
        )

    @staticmethod
    def _eligible_harness_layout_source_objects(
        source_objects: Collection[object],
        eligible_source_ids: Collection[int] | None,
    ) -> tuple[object, ...]:
        if eligible_source_ids is None:
            return tuple(source_objects)
        eligible_ids = frozenset(eligible_source_ids)
        return tuple(
            source_object
            for source_object in source_objects
            if id(source_object) in eligible_ids
        )

    @staticmethod
    def _harness_bundle_children(
        source_objects: Collection[object],
    ) -> dict[int, tuple[object, ...]]:
        indexed = AltiumSchDoc._harness_layout_owner_children(source_objects)
        bundle_ids = {
            id(source_object)
            for source_object in source_objects
            if type(source_object) is AltiumSchHarnessBundle
        }
        return {
            owner_id: children
            for owner_id, children in indexed.items()
            if owner_id in bundle_ids
        }

    @staticmethod
    def _harness_layout_owner_children(
        source_objects: Collection[object],
        parent_by_source_id: Mapping[int, object | None] | None = None,
    ) -> dict[int, tuple[object, ...]]:
        from .altium_sch_paint_order import order_harness_layout_children_by_source

        owners = {
            id(source_object): source_object
            for source_object in source_objects
            if type(source_object)
            in (
                *HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES,
                *HARNESS_LAYOUT_CHILD_CONTAINER_TYPES,
            )
        }
        children: dict[int, list[object]] = {}
        for source_object in source_objects:
            parent = (
                parent_by_source_id[id(source_object)]
                if parent_by_source_id is not None
                and id(source_object) in parent_by_source_id
                else getattr(source_object, "parent", None)
            )
            parent_id = id(parent)
            owner = owners.get(parent_id)
            if AltiumSchDoc._is_modeled_harness_owner_child(owner, source_object):
                children.setdefault(parent_id, []).append(source_object)
        return {
            parent_id: tuple(
                order_harness_layout_children_by_source(
                    owners[parent_id], items, parent_by_source_id=parent_by_source_id
                )
            )
            for parent_id, items in children.items()
        }

    def _harness_layout_root_reachable_owners(
        self,
        source_objects: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
    ) -> tuple[object, ...]:
        roots = [
            source_object
            for source_object in source_objects
            if type(source_object) in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES
            and self._harness_layout_parent(source_object) is None
        ]
        ordered: list[object] = []
        seen: set[int] = set()
        stack = list(reversed(roots))
        while stack:
            owner = stack.pop()
            owner_id = id(owner)
            if owner_id in seen:
                continue
            seen.add(owner_id)
            ordered.append(owner)
            nested = [
                child
                for child in owner_children.get(owner_id, ())
                if type(child) in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES
            ]
            stack.extend(reversed(nested))
        return tuple(ordered)

    def _harness_layout_parent(self, source_object: object) -> object | None:
        parent_by_id = self._geometry_parent_by_source_id
        if parent_by_id is not None and id(source_object) in parent_by_id:
            return parent_by_id[id(source_object)]
        return getattr(source_object, "parent", None)

    @staticmethod
    def _is_modeled_harness_owner_child(owner: object, child: object) -> bool:
        from .altium_sch_paint_order import _harness_named_owner_field_role

        if not isinstance(child, SchPrimitive):
            return False
        if type(owner) is AltiumSchHarnessConnector:
            return type(child) in (AltiumSchHarnessEntry, AltiumSchHarnessType)
        if type(owner) is AltiumSchSheetSymbol:
            return type(child) in (
                AltiumSchFileName,
                AltiumSchSheetEntry,
                AltiumSchSheetName,
            )
        if type(owner) not in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES:
            return False
        if isinstance(child, AltiumSchImageParameter):
            return type(owner) is AltiumSchHarnessLayoutConnectionPoint
        return (
            child.record_type in HARNESS_LAYOUT_SPATIAL_CHILD_RECORD_TYPES
            or _harness_named_owner_field_role(owner, child) is not None
        )

    def _shadowed_harness_bundle_field_ids(
        self,
        source_objects: Collection[object],
    ) -> set[int]:
        from .altium_sch_paint_order import _harness_bundle_field_role

        selected_ids = {
            id(field)
            for field in self._selected_harness_bundle_fields_for_render(source_objects)
        }
        return {
            id(source_object)
            for source_object in source_objects
            if type(self._harness_layout_parent(source_object))
            is AltiumSchHarnessBundle
            and _harness_bundle_field_role(source_object) is not None
            and id(source_object) not in selected_ids
        }

    def _shadowed_harness_owner_field_ids(
        self,
        source_objects: Collection[object],
    ) -> set[int]:
        shadowed = self._shadowed_harness_bundle_field_ids(source_objects)
        children_by_owner: dict[int, list[object]] = {}
        for source_object in source_objects:
            parent = self._harness_layout_parent(source_object)
            if type(parent) in {
                AltiumSchHarnessConnector,
                AltiumSchHarnessLayoutConnectionPoint,
                AltiumSchHarnessLayoutCovering,
                AltiumSchHarnessLayoutLabel,
                AltiumSchHarnessSplice,
                AltiumSchSheetSymbol,
            }:
                children_by_owner.setdefault(id(parent), []).append(source_object)
        for children in children_by_owner.values():
            owner = self._harness_layout_parent(children[0])
            shadowed.update(
                self._shadowed_harness_fields_for_owner(
                    owner, children, self._geometry_parent_by_source_id
                )
            )
        return shadowed

    @staticmethod
    def _shadowed_harness_fields_for_owner(
        owner: object,
        children: Collection[object],
        parent_by_source_id: Mapping[int, object | None] | None = None,
    ) -> set[int]:
        from .altium_sch_paint_order import (
            _harness_named_owner_field_objects,
            _harness_named_owner_field_role,
            _is_owner_field_candidate,
            _owner_field_objects,
        )

        if isinstance(owner, (AltiumSchHarnessConnector, AltiumSchSheetSymbol)):
            selected = _owner_field_objects(
                owner, tuple(children), parent_by_source_id=parent_by_source_id
            )
            candidates = [
                child for child in children if _is_owner_field_candidate(owner, child)
            ]
        else:
            selected = _harness_named_owner_field_objects(owner, tuple(children))
            candidates = [
                child
                for child in children
                if _harness_named_owner_field_role(owner, child) is not None
            ]
        selected_ids = {id(field) for field in selected}
        return {
            id(candidate)
            for candidate in candidates
            if id(candidate) not in selected_ids
        }

    def _selected_harness_bundle_fields_for_render(
        self,
        source_objects: Collection[object],
    ) -> tuple[object, ...]:
        from .altium_sch_paint_order import _harness_bundle_field_objects

        children_by_bundle: dict[int, list[object]] = {}
        for source_object in source_objects:
            parent = self._harness_layout_parent(source_object)
            if type(parent) is AltiumSchHarnessBundle:
                children_by_bundle.setdefault(id(parent), []).append(source_object)
        return tuple(
            field
            for children in children_by_bundle.values()
            for field in _harness_bundle_field_objects(children)
        )

    def _append_harness_layout_owner_geometry_records(
        self,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        owner_children: Mapping[int, tuple[object, ...]],
        source_objects: Collection[object],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        bundle_index: _HarnessConnectionBundleIndex | None,
        component_index: _HarnessComponentIndex | None,
        covering_topology: _HarnessCoveringTopologyIndex | None,
        physical_models: Mapping[int, _HarnessPhysicalModelProjection],
        parameter_models: Mapping[int, _HarnessPhysicalModelProjection],
        compile_mask_index: _HarnessCompileMaskIndex | None,
    ) -> None:
        owners = [
            source_object
            for source_object in source_objects
            if type(source_object) in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES
        ]
        self._harness_layout_owner_postorder(owners, owner_children)
        roots = [
            owner for owner in owners if self._harness_layout_parent(owner) is None
        ]
        postorder = self._harness_layout_owner_postorder(roots, owner_children)
        self._append_harness_layout_owner_own_records(
            records,
            records_by_source_id,
            postorder,
            owner_children,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            bundle_index=bundle_index,
            component_index=component_index,
            covering_topology=covering_topology,
            physical_models=physical_models,
            parameter_models=parameter_models,
            compile_mask_index=compile_mask_index,
        )
        self._replace_harness_layout_root_operations(
            records,
            records_by_source_id,
            roots,
            owner_children,
            units_per_px=units_per_px,
        )

    def _append_harness_layout_owner_own_records(
        self,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        owners: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        bundle_index: _HarnessConnectionBundleIndex | None,
        component_index: _HarnessComponentIndex | None,
        covering_topology: _HarnessCoveringTopologyIndex | None,
        physical_models: Mapping[int, _HarnessPhysicalModelProjection],
        parameter_models: Mapping[int, _HarnessPhysicalModelProjection],
        compile_mask_index: _HarnessCompileMaskIndex | None,
    ) -> None:
        suppressed_coverings: list[object] = []
        record_positions = {id(record): index for index, record in enumerate(records)}
        covering_cache_budget = _CoveringCacheBudget(
            max_work=_MAX_COVERING_RENDER_CACHE_WORK
        )
        covering_geometry_ctx = (
            geometry_ctx
            if covering_topology is None
            else self._harness_covering_render_context(geometry_ctx)
        )
        for owner in owners:
            covering_projection = self._prepare_harness_covering_autoposition(
                owner,
                owner_children.get(id(owner), ()),
                covering_topology,
                records,
                records_by_source_id,
                record_positions,
                covering_geometry_ctx,
                covering_cache_budget,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            child_records = self._harness_layout_child_records(
                records,
                records_by_source_id,
                owner_children.get(id(owner), ()),
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                parameter_models=parameter_models,
            )
            geometry_record = self._harness_layout_geometry_record(
                cast(
                    AltiumSchHarnessLayoutConnectionPoint
                    | AltiumSchHarnessLayoutCovering
                    | AltiumSchHarnessLayoutLabel
                    | AltiumSchHarnessSplice
                    | AltiumSchHarnessBundle,
                    owner,
                ),
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                bundle_index=bundle_index,
                component_index=component_index,
                covering_topology=covering_topology,
                covering_projection=covering_projection,
                physical_model=physical_models.get(id(owner)),
                compile_mask_index=compile_mask_index,
                child_records=(),
                emit_empty=bool(child_records),
            )
            self._store_harness_layout_owner_record(
                owner,
                geometry_record,
                records,
                records_by_source_id,
                record_positions,
                suppressed_coverings,
            )
        records[:] = [record for record in records if record is not None]
        suppressed_record_ids = self._suppressed_harness_covering_record_ids(
            suppressed_coverings,
            owner_children,
            records_by_source_id,
        )
        if suppressed_record_ids:
            records[:] = [
                record for record in records if id(record) not in suppressed_record_ids
            ]

    def _store_harness_layout_owner_record(
        self,
        owner: object,
        geometry_record: "SchGeometryRecord | None",
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        record_positions: dict[int, int],
        suppressed_coverings: list[object],
    ) -> None:
        existing = records_by_source_id.pop(id(owner), None)
        existing_position = None
        if existing is not None:
            existing_position = record_positions.pop(id(existing))
            # Stable positions are required until the owner pass finishes; the
            # caller removes these temporary tombstones before returning.
            cast(list[SchGeometryRecord | None], records)[existing_position] = None
        if geometry_record is None:
            if type(owner) is AltiumSchHarnessLayoutCovering:
                suppressed_coverings.append(owner)
            return
        tagged = self._tag_source_geometry_record(geometry_record, owner)
        if existing_position is None:
            records.append(tagged)
            record_positions[id(tagged)] = len(records) - 1
        else:
            records[existing_position] = tagged
            record_positions[id(tagged)] = existing_position
        records_by_source_id[id(owner)] = tagged

    def _prepare_harness_covering_autoposition(
        self,
        owner: object,
        children: tuple[object, ...],
        topology: _HarnessCoveringTopologyIndex | None,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        record_positions: dict[int, int],
        geometry_ctx: SchSvgRenderContext,
        covering_cache_budget: _CoveringCacheBudget,
        *,
        document_id: str,
        units_per_px: int,
    ) -> _HarnessCoveringProjection | None:
        if type(owner) is not AltiumSchHarnessLayoutCovering or topology is None:
            return None
        covering = cast(AltiumSchHarnessLayoutCovering, owner)
        if not bool(getattr(covering, "enable_draw", True)):
            return None
        from .altium_sch_paint_order import _harness_named_owner_field_objects

        fields = _harness_named_owner_field_objects(covering, children)
        designator, comment = self._covering_autoposition_fields(fields)
        prepared = _prepare_covering_render(
            covering,
            topology,
            designator,
            comment,
            comment_y_size=lambda: self._harness_covering_comment_y_size(
                comment if comment is not None and comment.auto_position else None,
                geometry_ctx,
            ),
            budget=covering_cache_budget,
        )
        if prepared is None:
            return None
        moved: list[
            tuple[
                AltiumSchDesignator | AltiumSchParameter,
                AltiumSchDesignator | AltiumSchParameter,
            ]
        ] = []
        if designator is not None and prepared.designator is not None:
            moved.append((designator, prepared.designator))
        if comment is not None and prepared.comment is not None:
            moved.append((comment, prepared.comment))
        self._refresh_harness_autoposition_records(
            moved,
            records,
            records_by_source_id,
            record_positions,
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        return prepared.projection

    @staticmethod
    def _harness_covering_render_context(
        geometry_ctx: SchSvgRenderContext,
    ) -> SchSvgRenderContext:
        render_geometry_ctx = geometry_ctx.copy()
        font_manager = geometry_ctx.font_manager
        if font_manager is not None:
            from .altium_font_manager import FontIDManager

            render_geometry_ctx.font_manager = FontIDManager.from_font_dict(
                font_manager.fonts
            )
        return render_geometry_ctx

    @staticmethod
    def _covering_autoposition_fields(
        fields: Collection[object],
    ) -> tuple[AltiumSchDesignator | None, AltiumSchParameter | None]:
        designator = next(
            (field for field in fields if type(field) is AltiumSchDesignator),
            None,
        )
        comment = next(
            (field for field in fields if type(field) is AltiumSchParameter),
            None,
        )
        return designator, comment

    def _refresh_harness_autoposition_records(
        self,
        fields: Collection[
            tuple[
                AltiumSchDesignator | AltiumSchParameter,
                AltiumSchDesignator | AltiumSchParameter,
            ]
        ],
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        record_positions: dict[int, int],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> None:
        for source_field, render_field in fields:
            self._refresh_harness_autoposition_record(
                render_field,
                records,
                records_by_source_id,
                record_positions,
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                source_field=source_field,
            )

    @staticmethod
    def _harness_covering_comment_y_size(
        comment: AltiumSchParameter | None,
        geometry_ctx: SchSvgRenderContext,
    ) -> int:
        if comment is None:
            return 0
        from .altium_text_metrics import measure_gdi_typographic_bounds

        display_text = comment._display_text(geometry_ctx)
        font_name, _, is_bold, is_italic, _ = geometry_ctx.get_font_info(
            comment.font_id
        )
        width_px, height_px = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(display_text, 8192),
            geometry_ctx.get_font_size_for_width(comment.font_id),
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        del width_px
        return _abs_safe_i32(_float_to_i32(height_px * 100_000, rounded=True))

    def _refresh_harness_autoposition_record(
        self,
        field: AltiumSchDesignator | AltiumSchParameter,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        record_positions: dict[int, int],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        source_field: AltiumSchDesignator | AltiumSchParameter | None = None,
    ) -> bool:
        from .altium_sch_geometry_oracle import SchGeometryRecord

        self._set_harness_field_horizontal_font(field, geometry_ctx)
        raw_record = field.to_geometry(
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        source = source_field if source_field is not None else field
        replacement = (
            self._tag_source_geometry_record(raw_record, source)
            if isinstance(raw_record, SchGeometryRecord)
            else None
        )
        existing = records_by_source_id.pop(id(source), None)
        if existing is None:
            if replacement is not None:
                records.append(replacement)
                record_positions[id(replacement)] = len(records) - 1
                records_by_source_id[id(source)] = replacement
            return False
        position = record_positions.pop(id(existing))
        # Keep downstream positions stable until the enclosing owner pass
        # removes temporary tombstones.
        cast(list[SchGeometryRecord | None], records)[position] = replacement
        if replacement is None:
            return True
        record_positions[id(replacement)] = position
        records_by_source_id[id(source)] = replacement
        return False

    @staticmethod
    def _set_harness_field_horizontal_font(
        field: AltiumSchDesignator | AltiumSchParameter,
        geometry_ctx: SchSvgRenderContext,
    ) -> None:
        font_manager = geometry_ctx.font_manager
        if font_manager is None:
            return
        font = font_manager.get_font_info(field.font_id)
        if font is None:
            return
        field.font_id = font_manager.get_or_create_font(
            font_name=font["name"],
            font_size=font["size"],
            bold=font["bold"],
            italic=font["italic"],
            rotation=0,
            underline=font["underline"],
            strikeout=font["strikeout"],
        )

    @staticmethod
    def _suppressed_harness_covering_record_ids(
        owners: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
        records_by_source_id: dict[int, "SchGeometryRecord"],
    ) -> set[int]:
        suppressed_record_ids: set[int] = set()
        seen: set[int] = set()
        stack = [
            child
            for owner in owners
            for child in reversed(owner_children.get(id(owner), ()))
        ]
        while stack:
            child = stack.pop()
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            descendant_record = records_by_source_id.pop(child_id, None)
            if descendant_record is not None:
                suppressed_record_ids.add(id(descendant_record))
            stack.extend(reversed(owner_children.get(child_id, ())))
        return suppressed_record_ids

    def _replace_harness_layout_root_operations(
        self,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        roots: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
        *,
        units_per_px: int,
    ) -> None:
        from .altium_sch_geometry_oracle import wrap_record_operations

        replacements = self._harness_layout_descendant_record_replacements(
            roots,
            owner_children,
            records_by_source_id,
        )
        for root in roots:
            root_record = records_by_source_id.get(id(root))
            if root_record is None:
                continue
            operations = self._harness_layout_tree_operations(
                root,
                owner_children,
                records_by_source_id,
            )
            replacement = replace(
                root_record,
                operations=wrap_record_operations(
                    root_record.unique_id,
                    operations,
                    units_per_px=units_per_px,
                ),
            )
            replacements[id(root_record)] = replacement
            records_by_source_id[id(root)] = replacement
        if replacements:
            records[:] = [replacements.get(id(record), record) for record in records]

    @staticmethod
    def _harness_layout_descendant_record_replacements(
        roots: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
        records_by_source_id: dict[int, "SchGeometryRecord"],
    ) -> dict[int, "SchGeometryRecord"]:
        replacements: dict[int, SchGeometryRecord] = {}
        seen: set[int] = set()
        stack = [
            child
            for root in roots
            for child in reversed(owner_children.get(id(root), ()))
        ]
        while stack:
            child = stack.pop()
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            record = records_by_source_id.get(child_id)
            if record is not None and record.extras.get("skip_svg") is not True:
                replacement = replace(
                    record,
                    extras={**record.extras, "skip_svg": True},
                )
                replacements[id(record)] = replacement
                records_by_source_id[child_id] = replacement
            stack.extend(reversed(owner_children.get(child_id, ())))
        return replacements

    @staticmethod
    def _harness_layout_tree_operations(
        root: object,
        owner_children: Mapping[int, tuple[object, ...]],
        records_by_source_id: dict[int, "SchGeometryRecord"],
    ) -> list["SchGeometryOp"]:
        from .altium_sch_geometry_oracle import SchGeometryOp, unwrap_record_operations

        root_record = records_by_source_id[id(root)]
        operations = list(
            unwrap_record_operations(root_record, unique_id=root_record.unique_id)
        )
        stack: list[tuple[object | None, int]] = [
            (child, -1) for child in reversed(owner_children.get(id(root), ()))
        ]
        while stack:
            child, group_index = stack.pop()
            if child is None:
                AltiumSchDoc._finish_harness_layout_group(operations, group_index)
                continue
            child_data = AltiumSchDoc._harness_layout_child_tree_data(
                child,
                owner_children,
                records_by_source_id,
            )
            if child_data is None:
                continue
            child_record, child_operations, nested = child_data
            group_index = len(operations)
            operations.append(
                SchGeometryOp.begin_group(
                    child_record.unique_id,
                    render_group_id=child_record.render_group_id,
                    render_group_identity=child_record.render_group_identity,
                    render_source_id=child_record.render_source_id,
                )
            )
            operations.extend(child_operations)
            if nested:
                stack.append((None, group_index))
                stack.extend((item, -1) for item in reversed(nested))
            else:
                AltiumSchDoc._finish_harness_layout_group(operations, group_index)
        return operations

    @staticmethod
    def _harness_layout_child_tree_data(
        child: object,
        owner_children: Mapping[int, tuple[object, ...]],
        records_by_source_id: Mapping[int, "SchGeometryRecord"],
    ) -> tuple["SchGeometryRecord", list["SchGeometryOp"], tuple[object, ...]] | None:
        from .altium_sch_geometry_oracle import unwrap_record_operations

        child_record = records_by_source_id.get(id(child))
        if child_record is None:
            return None
        child_operations = [
            operation
            for operation in unwrap_record_operations(
                child_record,
                unique_id=child_record.unique_id,
            )
            if operation.payload.get("transparent_back") is not True
        ]
        nested = owner_children.get(id(child), ())
        if type(child) in (AltiumSchHarnessConnector, AltiumSchSheetSymbol):
            embedded_ids = {
                operation.render_source_id
                for operation in child_operations
                if operation.render_source_id is not None
            }
            nested = tuple(
                nested_child
                for nested_child in nested
                if id(nested_child) not in embedded_ids
            )
        return child_record, child_operations, nested

    @staticmethod
    def _finish_harness_layout_group(
        operations: list["SchGeometryOp"], group_index: int
    ) -> None:
        from .altium_sch_geometry_oracle import SchGeometryOp

        if len(operations) == group_index + 1:
            operations.pop()
        else:
            operations.append(SchGeometryOp.end_group())

    @staticmethod
    def _harness_layout_owner_postorder(
        owners: Collection[object],
        owner_children: Mapping[int, tuple[object, ...]],
    ) -> tuple[object, ...]:
        ordered: list[object] = []
        completed: set[int] = set()
        active: set[int] = set()
        for root in owners:
            stack: list[tuple[object, bool]] = [(root, False)]
            while stack:
                owner, exiting = stack.pop()
                owner_id = id(owner)
                if exiting:
                    active.discard(owner_id)
                    if owner_id not in completed:
                        completed.add(owner_id)
                        ordered.append(owner)
                    continue
                if owner_id in completed:
                    continue
                if owner_id in active:
                    raise ValueError("cyclic harness-layout ownership")
                active.add(owner_id)
                stack.append((owner, True))
                nested = [
                    child
                    for child in owner_children.get(owner_id, ())
                    if type(child) in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES
                ]
                stack.extend((child, False) for child in reversed(nested))
        return tuple(ordered)

    def _geometry_records_by_source_id(
        self,
        records: Collection["SchGeometryRecord"],
    ) -> dict[int, "SchGeometryRecord"]:
        result: dict[int, SchGeometryRecord] = {}
        for record in records:
            source_index = record.source_object_index
            if source_index is None or not 0 <= source_index < len(self.all_objects):
                continue
            result.setdefault(id(self.all_objects[source_index]), record)
        return result

    def _harness_image_parameter_projections(
        self,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        geometry_ctx: SchSvgRenderContext,
        *,
        source_objects: Collection[object],
        document_id: str,
        units_per_px: int,
    ) -> dict[int, _HarnessPhysicalModelProjection]:
        result: dict[int, _HarnessPhysicalModelProjection] = {}
        for parameters in self._harness_connection_image_parameters(
            source_objects
        ).values():
            for parameter in parameters:
                if parameter.is_hidden:
                    continue
                model = parameter._model_source(geometry_ctx._source_admission)
                model_record = self._harness_physical_model_geometry_record(
                    model,
                    records,
                    records_by_source_id,
                    geometry_ctx,
                    document_id=document_id,
                    units_per_px=units_per_px,
                )
                result[id(parameter)] = _HarnessPhysicalModelProjection(
                    parameter=parameter,
                    model=model,
                    model_record=model_record,
                    bounds=self._harness_physical_model_bounds(
                        parameter,
                        model,
                        geometry_ctx,
                    ),
                )
        return result

    def _harness_physical_model_geometry_record(
        self,
        model: object | None,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> "SchGeometryRecord | None":
        if model is None or not geometry_ctx._source_admission.admits(model):
            return None
        existing = records_by_source_id.get(id(model))
        if existing is not None:
            return existing
        if not isinstance(
            model,
            (_AltiumSchLineView, _AltiumSchHarnessCavityComponent),
        ):
            return None
        generated = model.to_geometry(
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        tagged = self._tag_source_geometry_record(generated, model)
        records.append(tagged)
        records_by_source_id[id(model)] = tagged
        return tagged

    def _harness_physical_model_records(
        self,
        parameter_models: Mapping[int, _HarnessPhysicalModelProjection],
        source_objects: Collection[object],
    ) -> dict[int, _HarnessPhysicalModelProjection]:
        candidates = self._harness_connection_image_parameters(source_objects)
        result: dict[int, _HarnessPhysicalModelProjection] = {}
        for owner_id, parameters in candidates.items():
            selected = self._visible_main_image_parameter(parameters)
            if selected is None:
                continue
            projection = parameter_models.get(id(selected))
            if projection is not None:
                result[owner_id] = projection
        return result

    @staticmethod
    def _without_explicit_main_physical_model_children(
        owner_children: Mapping[int, tuple[object, ...]],
        physical_models: Mapping[int, _HarnessPhysicalModelProjection],
    ) -> dict[int, tuple[object, ...]]:
        result = dict(owner_children)
        for owner_id, projection in physical_models.items():
            result[owner_id] = tuple(
                child
                for child in result.get(owner_id, ())
                if child is not projection.parameter
            )
        return result

    @staticmethod
    def _harness_physical_model_bounds(
        parameter: AltiumSchImageParameter,
        model: SchPrimitive | None,
        geometry_ctx: SchSvgRenderContext,
    ) -> "SchGeometryBounds":
        from .altium_sch_geometry_oracle import SchGeometryBounds

        own_bounds = AltiumSchDoc._harness_graphical_own_bounds(
            model,
            geometry_ctx,
        )
        bounds = own_bounds
        stack = list(
            reversed(
                tuple(
                    geometry_ctx._source_admission.children(
                        parameter, parameter.children
                    )
                )
            )
        )
        seen: set[int] = {id(model)} if model is not None else set()
        while stack:
            child = stack.pop()
            child_id = id(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            if not geometry_ctx._source_admission.admits(child):
                continue
            if isinstance(child, AltiumSchParameter):
                continue
            if bool(getattr(child, "is_hidden", False)):
                continue
            child_bounds = AltiumSchDoc._harness_graphical_own_bounds(
                child,
                geometry_ctx,
            )
            if child_bounds is not None:
                bounds = AltiumSchDoc._union_optional_harness_bounds(
                    bounds,
                    child_bounds,
                )
            if not isinstance(child, _AltiumSchHarnessCavityComponent):
                stack.extend(
                    reversed(
                        tuple(
                            geometry_ctx._source_admission.children(
                                child, getattr(child, "children", ())
                            )
                        )
                    )
                )
        if bounds is not None:
            return bounds
        if isinstance(model, _AltiumSchLineView):
            x, y = _harness_internal_location(parameter.location)
            return SchGeometryBounds(
                left=_unchecked_i32_offset(x, -500_000),
                top=_unchecked_i32_offset(y, 500_000),
                right=_unchecked_i32_offset(x, 500_000),
                bottom=_unchecked_i32_offset(y, -500_000),
            )
        return AltiumSchDoc._harness_parameter_text_bounds(parameter, geometry_ctx)

    @staticmethod
    def _union_optional_harness_bounds(
        first: "SchGeometryBounds | None",
        second: "SchGeometryBounds",
    ) -> "SchGeometryBounds":
        from .altium_sch_geometry_oracle import SchGeometryBounds

        if first is None:
            return second
        return SchGeometryBounds(
            left=min(first.left, second.left),
            top=max(first.top, second.top),
            right=max(first.right, second.right),
            bottom=min(first.bottom, second.bottom),
        )

    @staticmethod
    def _harness_graphical_own_bounds(
        graphical_object: object | None,
        geometry_ctx: SchSvgRenderContext,
    ) -> "SchGeometryBounds | None":
        from .altium_sch_geometry_oracle import SchGeometryBounds

        if graphical_object is None or not geometry_ctx._source_admission.admits(
            graphical_object
        ):
            return None
        if isinstance(graphical_object, _AltiumSchHarnessCavityComponent):
            return graphical_object.own_bounds_internal(
                source_admission=geometry_ctx._source_admission
            )
        get_bounds = getattr(graphical_object, "own_bounds_internal", None)
        if callable(get_bounds):
            bounds = get_bounds()
            if bounds is None or isinstance(bounds, SchGeometryBounds):
                return bounds
        location = getattr(graphical_object, "location", None)
        if not isinstance(location, CoordPoint):
            return None
        location_x, location_y = _harness_internal_location(location)
        corner = getattr(graphical_object, "corner", None)
        if isinstance(corner, CoordPoint):
            corner_x, corner_y = _harness_internal_location(corner)
            return SchGeometryBounds(
                left=min(location_x, corner_x),
                top=max(location_y, corner_y),
                right=max(location_x, corner_x),
                bottom=min(location_y, corner_y),
            )
        return SchGeometryBounds(
            left=location_x - 5,
            top=location_y + 5,
            right=location_x + 5,
            bottom=location_y - 5,
        )

    @staticmethod
    def _harness_parameter_text_bounds(
        parameter: AltiumSchImageParameter,
        geometry_ctx: SchSvgRenderContext,
    ) -> "SchGeometryBounds":
        from ._altium_sch_component_bounds import _label_own_bounds_from_size
        from .altium_text_metrics import measure_gdi_typographic_bounds

        display_text = parameter._display_text(geometry_ctx)
        font_name, _, is_bold, is_italic, _ = geometry_ctx.get_font_info(
            parameter.font_id
        )
        width_px, height_px = measure_gdi_typographic_bounds(
            _utf16_code_unit_prefix(display_text, 8192),
            geometry_ctx.get_font_size_for_width(parameter.font_id),
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        width = _abs_safe_i32(_float_to_i32(width_px * 100_000, rounded=True))
        height = _abs_safe_i32(_float_to_i32(height_px * 100_000, rounded=True))
        return _label_own_bounds_from_size(
            _harness_internal_location(parameter.location),
            width,
            height,
            parameter.justification.value,
            parameter.orientation.value,
        )

    def _harness_connection_image_parameters(
        self,
        source_objects: Collection[object],
    ) -> dict[int, list[AltiumSchImageParameter]]:
        candidates: dict[int, list[AltiumSchImageParameter]] = {}
        for source_object in source_objects:
            if not isinstance(source_object, AltiumSchImageParameter):
                continue
            parent = self._harness_layout_parent(source_object)
            if type(parent) is AltiumSchHarnessLayoutConnectionPoint:
                candidates.setdefault(id(parent), []).append(source_object)
        return candidates

    def _harness_physical_model_child_ids(
        self,
        source_objects: Collection[object],
        source_admission: _SourceAdmission = _SourceAdmission(),
    ) -> set[int]:
        result: set[int] = set()
        parameters = self._harness_connection_image_parameters(source_objects).values()
        stack = [
            child
            for owner_parameters in parameters
            for parameter in owner_parameters
            for child in reversed(
                tuple(source_admission.children(parameter, parameter.children))
            )
        ]
        seen: set[int] = set()
        while stack:
            source_object = stack.pop()
            source_id = id(source_object)
            if source_id in seen:
                continue
            seen.add(source_id)
            if isinstance(
                source_object,
                (
                    AltiumSchImage,
                    _AltiumSchLineView,
                    _AltiumSchHarnessCavity,
                    _AltiumSchHarnessCavityComponent,
                ),
            ):
                result.add(source_id)
            stack.extend(
                reversed(
                    tuple(
                        source_admission.children(
                            source_object, getattr(source_object, "children", ())
                        )
                    )
                )
            )
        return result

    @staticmethod
    def _visible_main_image_parameter(
        parameters: list[AltiumSchImageParameter],
    ) -> AltiumSchImageParameter | None:
        selected = next(
            (item for item in parameters if item.is_main_model),
            None,
        )
        return None if selected is None or selected.is_hidden else selected

    def _harness_layout_child_records(
        self,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        children: Iterable[object],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        parameter_models: Mapping[int, _HarnessPhysicalModelProjection],
    ) -> tuple["SchGeometryRecord", ...]:
        child_records: list[SchGeometryRecord] = []
        for child in children:
            child_record = self._resolve_harness_layout_child_record(
                child,
                records,
                records_by_source_id,
                parameter_models,
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            if child_record is None:
                continue
            child_records.append(child_record)
        return tuple(child_records)

    def _resolve_harness_layout_child_record(
        self,
        child: object,
        records: list[SchGeometryRecord],
        records_by_source_id: dict[int, "SchGeometryRecord"],
        parameter_models: Mapping[int, _HarnessPhysicalModelProjection],
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import SchGeometryRecord

        existing = records_by_source_id.get(id(child))
        if existing is not None:
            return existing
        if isinstance(child, AltiumSchImageParameter):
            record = self._resolve_harness_image_parameter_child_record(
                child,
                records,
                parameter_models.get(id(child)),
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
        elif type(child) is AltiumSchHarnessBundle or _is_parent_bound_geometry_child(
            child
        ):
            return None
        else:
            to_geometry = getattr(child, "to_geometry", None)
            if not callable(to_geometry):
                return None
            raw_record = to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
            )
            record = (
                self._tag_source_geometry_record(raw_record, child)
                if isinstance(raw_record, SchGeometryRecord)
                else None
            )
            if record is not None:
                records.append(record)
        if record is not None:
            records_by_source_id[id(child)] = record
        return record

    def _resolve_harness_image_parameter_child_record(
        self,
        parameter: AltiumSchImageParameter,
        records: list[SchGeometryRecord],
        projection: _HarnessPhysicalModelProjection | None,
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import SchGeometryRecord

        if projection is not None:
            return self._harness_image_parameter_record(
                parameter,
                projection,
                document_id=document_id,
                units_per_px=units_per_px,
            )
        raw_record = parameter.to_geometry(
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
        )
        if not isinstance(raw_record, SchGeometryRecord):
            return None
        record = self._tag_source_geometry_record(raw_record, parameter)
        records.append(record)
        return record

    def _harness_image_parameter_record(
        self,
        parameter: AltiumSchImageParameter,
        projection: _HarnessPhysicalModelProjection | None,
        *,
        document_id: str,
        units_per_px: int,
    ) -> "SchGeometryRecord | None":
        from .altium_sch_geometry_oracle import (
            SchGeometryRecord,
            wrap_record_operations,
        )

        if projection is None:
            return None
        model_operations = (
            _harness_bundle_child_operations((projection.model_record,))
            if projection.model_record is not None
            else []
        )
        record = SchGeometryRecord(
            handle=f"{document_id}\\{parameter.unique_id or ''}",
            unique_id=str(parameter.unique_id or ""),
            kind="image_parameter",
            object_id="eImageParameter",
            bounds=projection.bounds,
            operations=wrap_record_operations(
                str(parameter.unique_id or ""),
                model_operations,
                units_per_px=units_per_px,
            ),
        )
        return self._tag_source_geometry_record(record, parameter)

    @staticmethod
    def _harness_connection_bundle_index(
        source_objects: Iterable[object],
        active_owners: Iterable[object] | None = None,
        parent_by_source_id: Mapping[int, object | None] | None = None,
    ) -> _HarnessConnectionBundleIndex | None:
        sources = tuple(source_objects)
        owners = sources if active_owners is None else active_owners
        needs_bundle_index = any(
            type(source_object) is AltiumSchHarnessLayoutConnectionPoint
            and source_object.style is HarnessLayoutConnectionPointStyle.INSULATOR
            and bool(source_object.connected_bundles_unique_ids)
            for source_object in owners
        )
        if not needs_bundle_index:
            return None
        return _HarnessConnectionBundleIndex(
            source_object
            for source_object in sources
            if type(source_object) is AltiumSchHarnessBundle
            and (
                parent_by_source_id.get(id(source_object))
                if parent_by_source_id is not None
                else getattr(source_object, "parent", None)
            )
            is None
        )

    @staticmethod
    def _harness_component_index(
        source_objects: Iterable[object],
        active_owners: Iterable[object],
        parent_by_source_id: Mapping[int, object | None] | None = None,
        source_admission: _SourceAdmission | None = None,
    ) -> _HarnessComponentIndex | None:
        if not any(
            type(owner) is AltiumSchHarnessLayoutConnectionPoint
            and bool(owner.connectors)
            for owner in active_owners
        ):
            return None
        return _HarnessComponentIndex(
            (
                source_object
                for source_object in source_objects
                if type(source_object) is AltiumSchHarnessComponent
                and (
                    parent_by_source_id.get(id(source_object))
                    if parent_by_source_id is not None
                    else getattr(source_object, "parent", None)
                )
                is None
            ),
            source_admission,
        )

    @staticmethod
    def _harness_covering_topology_index(
        source_objects: Collection[object],
        active_owners: Iterable[object],
        parent_by_source_id: Mapping[int, object | None] | None = None,
    ) -> _HarnessCoveringTopologyIndex | None:
        if not any(
            type(owner) is AltiumSchHarnessLayoutCovering
            and bool(getattr(owner, "covered_items", ()))
            and getattr(owner, "_covering_update_count", 0) == 0
            and AltiumSchDoc._harness_owner_chain_is_enabled(
                owner,
                parent_by_source_id,
            )
            for owner in active_owners
        ):
            return None
        return _HarnessCoveringTopologyIndex(
            source_object
            for source_object in source_objects
            if (
                parent_by_source_id.get(id(source_object))
                if parent_by_source_id is not None
                else getattr(source_object, "parent", None)
            )
            is None
        )

    @staticmethod
    def _harness_owner_chain_is_enabled(
        owner: object,
        parent_by_source_id: Mapping[int, object | None] | None = None,
    ) -> bool:
        current: object | None = owner
        while type(current) in HARNESS_LAYOUT_DRAW_WITH_CHILDREN_TYPES:
            if not bool(getattr(current, "enable_draw", True)):
                return False
            current = (
                parent_by_source_id.get(id(current))
                if parent_by_source_id is not None
                else getattr(current, "parent", None)
            )
        return True

    @staticmethod
    def _harness_compile_mask_index(
        source_objects: Collection[object],
        active_owners: Iterable[object] | None = None,
    ) -> _HarnessCompileMaskIndex | None:
        owners = source_objects if active_owners is None else active_owners
        if not any(
            type(source_object) is AltiumSchHarnessBundle for source_object in owners
        ):
            return None

        def compile_masks() -> Iterable[tuple[int, int, int, int]]:
            for source_object in source_objects:
                if (
                    isinstance(source_object, AltiumSchCompileMask)
                    and not source_object.is_collapsed
                ):
                    location = _harness_internal_location(source_object.location)
                    corner = _harness_internal_location(source_object.corner)
                    yield (*location, *corner)

        return _HarnessCompileMaskIndex(compile_masks())

    @staticmethod
    def _harness_layout_geometry_record(
        source_object: (
            AltiumSchHarnessLayoutConnectionPoint
            | AltiumSchHarnessLayoutCovering
            | AltiumSchHarnessLayoutLabel
            | AltiumSchHarnessSplice
            | AltiumSchHarnessBundle
        ),
        geometry_ctx: SchSvgRenderContext,
        *,
        document_id: str,
        units_per_px: int,
        bundle_index: _HarnessConnectionBundleIndex | None,
        component_index: _HarnessComponentIndex | None,
        covering_topology: _HarnessCoveringTopologyIndex | None,
        covering_projection: _HarnessCoveringProjection | None,
        physical_model: _HarnessPhysicalModelProjection | None,
        compile_mask_index: _HarnessCompileMaskIndex | None,
        child_records: Collection["SchGeometryRecord"],
        emit_empty: bool = False,
    ) -> "SchGeometryRecord | None":
        if type(source_object) is AltiumSchHarnessLayoutConnectionPoint:
            return source_object.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                document_bundles=bundle_index or (),
                document_components=component_index,
                physical_model_active=physical_model is not None,
                physical_model_record=(
                    physical_model.model_record if physical_model else None
                ),
                physical_model_unique_id=(
                    str(physical_model.parameter.unique_id or "")
                    if physical_model is not None
                    else ""
                ),
                physical_model_bounds=(
                    physical_model.bounds if physical_model is not None else None
                ),
                child_records=tuple(child_records),
            )
        if type(source_object) is AltiumSchHarnessBundle:
            return source_object.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                compile_mask_index=compile_mask_index,
                child_records=tuple(child_records),
                emit_empty=emit_empty,
            )
        if type(source_object) is AltiumSchHarnessLayoutCovering:
            if covering_topology is None or covering_projection is None:
                return None
            return source_object.to_geometry(
                geometry_ctx,
                document_id=document_id,
                units_per_px=units_per_px,
                topology=covering_topology,
                projection=covering_projection,
                child_records=tuple(child_records),
            )
        spatial_owner = cast(
            AltiumSchHarnessLayoutLabel | AltiumSchHarnessSplice,
            source_object,
        )
        return spatial_owner.to_geometry(
            geometry_ctx,
            document_id=document_id,
            units_per_px=units_per_px,
            child_records=tuple(child_records),
        )

    def _build_sheet_geometry_setup(
        self,
        *,
        doc_unique_id: str,
        units_per_px: int,
        workspace_bottom_units: int,
    ) -> _SchSheetGeometrySetup:
        """
        Resolve sheet-size and border settings for geometry export.
        """
        from .altium_record_sch__sheet import ONSCREEN_SHEET_ZONES

        if self.sheet:
            sheet_raw = getattr(self.sheet, "_raw_record", {}) or {}
            has_explicit_border_on = any(
                str(key).lower() == "borderon" for key in sheet_raw
            )
            sheet_width_mils, sheet_height_mils = self.sheet.get_sheet_size_units()
            sheet_unique_id = getattr(self.sheet, "unique_id", "") or doc_unique_id
            area_color = int(self.sheet.area_color)
            margin = self.sheet.get_margin_units()
            reference_zones_on = bool(self.sheet.reference_zones_on)
            reference_zone_style = int(self.sheet.reference_zone_style)
            title_block_on = bool(self.sheet.title_block_on)
            border_on = (
                bool(self.sheet.border_on)
                if (has_explicit_border_on or reference_zones_on or title_block_on)
                else False
            )
            default_x_zones, default_y_zones, _default_margin = (
                ONSCREEN_SHEET_ZONES.get(
                    self.sheet.sheet_style,
                    (4, 4, 20),
                )
            )
            if self.sheet.use_custom_sheet:
                x_zones = (
                    self.sheet.custom_x_zones
                    if self.sheet.custom_x_zones > 0
                    else default_x_zones
                )
                y_zones = (
                    self.sheet.custom_y_zones
                    if self.sheet.custom_y_zones > 0
                    else default_y_zones
                )
            else:
                x_zones = default_x_zones
                y_zones = default_y_zones
        else:
            sheet_width_mils = 1100
            sheet_height_mils = 850
            sheet_unique_id = doc_unique_id
            area_color = 16317695
            border_on = True
            margin = 20
            reference_zones_on = False
            reference_zone_style = 0
            title_block_on = False
            x_zones = 4
            y_zones = 4
            has_explicit_border_on = True

        outer_top_units = (
            (workspace_bottom_units // units_per_px) - sheet_height_mils
        ) * units_per_px
        outer_right_units = sheet_width_mils * units_per_px
        return _SchSheetGeometrySetup(
            sheet_width_mils=sheet_width_mils,
            sheet_height_mils=sheet_height_mils,
            sheet_unique_id=sheet_unique_id,
            area_color=area_color,
            use_custom_sheet=bool(self.sheet.use_custom_sheet)
            if self.sheet is not None
            else False,
            border_on=border_on,
            margin=margin,
            reference_zones_on=reference_zones_on,
            reference_zone_style=reference_zone_style,
            title_block_on=title_block_on,
            x_zones=x_zones,
            y_zones=y_zones,
            units_per_px=units_per_px,
            workspace_bottom_units=workspace_bottom_units,
            outer_top_units=outer_top_units,
            outer_right_units=outer_right_units,
            has_explicit_border_on=has_explicit_border_on,
        )

    @staticmethod
    def _is_document_parameter_source(
        parameter: AltiumSchParameter, source_admission: _SourceAdmission
    ) -> bool:
        parents = source_admission.parent_by_source_id
        parent = source_admission.parent(parameter)
        if parents is not None and id(parameter) in parents:
            return parent is None
        return not isinstance(
            parent, (AltiumSchComponent, AltiumSchPin, AltiumSchTemplate)
        )

    def _collect_top_level_geometry_parameters(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> dict[str, str]:
        """
        Collect top-level schematic parameters for geometry rendering.
        """
        param_dict: dict[str, str] = {}
        for param in source_admission.admitted(self.parameters):
            if not hasattr(param, "name") or not hasattr(param, "text"):
                continue
            if not self._is_document_parameter_source(param, source_admission):
                continue
            param_dict[param.name] = param.text
        return param_dict

    def _append_reference_zone_geometry(
        self,
        sheet_operations: list[Any],
        setup: _SchSheetGeometrySetup,
        *,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
    ) -> None:
        """
        Append reference-zone primitives to the sheet record.
        """
        from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions
        from .altium_text_metrics import measure_text_height, measure_text_width

        if not (
            setup.reference_zones_on
            and setup.x_zones > 0
            and setup.y_zones > 0
            and self.sheet is not None
        ):
            return

        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
        )

        def coord(x: float, y: float) -> tuple[float, float]:
            return svg_coord_to_geometry(
                x,
                y,
                sheet_height_px=setup.sheet_height_mils,
                units_per_px=setup.units_per_px,
            )

        ctx = SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=setup.sheet_height_mils,
            sheet_width=setup.sheet_width_mils,
            font_manager=self.font_manager,
            options=SchSvgRenderOptions.native_altium(),
            parameters=parameters,
            project_parameters=project_parameters or {},
        )
        if ctx.font_manager is None:
            raise ValueError("Reference zone geometry requires a font manager")

        font_id = int(self.sheet.system_font or 0)
        if font_id <= 0:
            font_id = ctx.font_manager.get_default_font_id()

        font_spec = ctx.font_manager.get_font_info(font_id)
        if font_spec is None:
            raise ValueError(
                f"Reference zone font ID {font_id} is not present in the document font table"
            )

        font_name, font_size_px, is_bold, is_italic, is_underline = ctx.get_font_info(
            font_id
        )
        render_font_size = ctx.get_baseline_font_size(font_size_px)
        text_height_px = measure_text_height(
            font_size_px,
            font_name,
            bold=is_bold,
            italic=is_italic,
        )
        text_height_int = max(1, int(text_height_px))
        text_height_box_int = max(1, math.ceil(text_height_px))
        zone_text_baseline_offset = (text_height_int - 1) // 2
        zone_w = setup.sheet_width_mils / setup.x_zones
        zone_h = setup.sheet_height_mils / setup.y_zones
        zone_pen = make_pen(0x000000)
        zone_brush = make_solid_brush(0x000000)
        zone_font = make_font_payload(
            name=str(font_spec.get("name", font_name)),
            size_px=font_size_px,
            units_per_px=setup.units_per_px,
            rotation=float(font_spec.get("rotation", 0) or 0.0),
            underline=bool(font_spec.get("underline", is_underline)),
            italic=bool(font_spec.get("italic", is_italic)),
            bold=bool(font_spec.get("bold", is_bold)),
            strikeout=bool(font_spec.get("strikeout", False)),
        )

        def append_zone_line(x1: float, y1: float, x2: float, y2: float) -> None:
            sheet_operations.append(
                SchGeometryOp.lines(
                    [
                        coord(x1, y1),
                        coord(x2, y2),
                    ],
                    pen=zone_pen,
                )
            )

        def append_zone_text(label: str, x_text: float, y_baseline: float) -> None:
            text_x, text_y = coord(x_text, y_baseline - render_font_size)
            sheet_operations.append(
                SchGeometryOp.string(
                    x=text_x,
                    y=text_y,
                    text=label,
                    font=zone_font,
                    brush=zone_brush,
                )
            )

        if setup.reference_zone_style == 1:
            column_specs = [
                (
                    setup.sheet_width_mils - zone_index * zone_w,
                    str(zone_index),
                    setup.sheet_width_mils - (zone_index - 0.5) * zone_w,
                )
                for zone_index in range(1, setup.x_zones + 1)
            ]
            row_specs = [
                (
                    zone_index * zone_h,
                    chr(ord("A") + setup.y_zones - zone_index),
                    (zone_index - 0.5) * zone_h
                    + zone_text_baseline_offset
                    + text_height_box_int
                    + (1 if setup.use_custom_sheet else 0),
                )
                for zone_index in range(1, setup.y_zones + 1)
            ]
        else:
            column_specs = [
                (
                    zone_index * zone_w,
                    str(zone_index),
                    (zone_index - 0.5) * zone_w,
                )
                for zone_index in range(1, setup.x_zones + 1)
            ]
            row_specs = [
                (
                    (setup.y_zones - zone_index) * zone_h,
                    chr(ord("A") + setup.y_zones - zone_index),
                    (setup.y_zones - zone_index + 0.5) * zone_h
                    + zone_text_baseline_offset,
                )
                for zone_index in range(1, setup.y_zones + 1)
            ]

        for x_divider, label, x_center in column_specs:
            text_width = measure_text_width(
                label,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            x_text = (
                x_center + text_width
                if setup.reference_zone_style == 1
                else x_center - text_width
            )
            append_zone_line(
                x_divider,
                setup.sheet_height_mils,
                x_divider,
                setup.sheet_height_mils - setup.margin,
            )
            append_zone_text(
                label,
                x_text,
                setup.sheet_height_mils - setup.margin / 2 + zone_text_baseline_offset,
            )
            append_zone_line(x_divider, 0, x_divider, setup.margin)
            append_zone_text(
                label, x_text, setup.margin / 2 + zone_text_baseline_offset
            )

        for y_divider, letter, y_center in row_specs:
            text_width = measure_text_width(
                letter,
                font_size_px,
                font_name,
                bold=is_bold,
                italic=is_italic,
            )
            append_zone_line(0, y_divider, setup.margin, y_divider)
            append_zone_text(letter, setup.margin / 2 - text_width / 2, y_center)
            append_zone_line(
                setup.sheet_width_mils,
                y_divider,
                setup.sheet_width_mils - setup.margin,
                y_divider,
            )
            append_zone_text(
                letter,
                setup.sheet_width_mils - setup.margin / 2 - text_width / 2,
                y_center,
            )

    def _build_sheet_operations(
        self,
        setup: _SchSheetGeometrySetup,
        *,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
        doc_unique_id: str,
    ) -> list[Any]:
        """
        Build the sheet wrapper operations for the geometry document.
        """
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            _geometry_item_length,
            _geometry_item_point,
            make_pen,
            make_solid_brush,
        )

        def rectangle_operation(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            *,
            brush: dict[str, object] | None = None,
            pen: dict[str, object] | None = None,
        ) -> SchGeometryOp:
            center_x, center_y = _geometry_item_point(
                (x1 + x2) / 2.0,
                (y1 + y2) / 2.0,
                units_per_px=setup.units_per_px,
            )
            return SchGeometryOp.rounded_rectangle_from_item(
                center_x=center_x,
                center_y=center_y,
                half_width=_geometry_item_length(
                    abs(x2 - x1) / (2.0 * setup.units_per_px),
                    units_per_px=setup.units_per_px,
                ),
                half_height=_geometry_item_length(
                    abs(y2 - y1) / (2.0 * setup.units_per_px),
                    units_per_px=setup.units_per_px,
                ),
                brush=brush,
                pen=pen,
            )

        sheet_operations = [
            SchGeometryOp.push_transform(
                [1, 0, 0, 1, 0, -setup.workspace_bottom_units]
            ),
            SchGeometryOp.begin_group(),
            SchGeometryOp.begin_group("DocumentMainGroup"),
            SchGeometryOp.begin_group(doc_unique_id),
            rectangle_operation(
                0,
                setup.outer_top_units,
                setup.outer_right_units,
                setup.workspace_bottom_units,
                brush=make_solid_brush(setup.area_color),
            ),
        ]

        if setup.render_border_rects:
            sheet_operations.append(
                rectangle_operation(
                    0,
                    setup.outer_top_units,
                    setup.outer_right_units,
                    setup.workspace_bottom_units,
                    pen=make_pen(0x000000),
                )
            )
        elif not setup.border_on and setup.working_margin == 0:
            sheet_operations.append(
                rectangle_operation(
                    0,
                    setup.outer_top_units,
                    setup.outer_right_units,
                    setup.workspace_bottom_units,
                    brush=make_solid_brush(setup.area_color),
                )
            )

        if setup.border_on or setup.working_margin > 0:
            inset_units = setup.working_margin * setup.units_per_px
            sheet_operations.append(
                rectangle_operation(
                    inset_units,
                    setup.outer_top_units + inset_units,
                    setup.outer_right_units - inset_units,
                    setup.workspace_bottom_units - inset_units,
                    brush=make_solid_brush(setup.area_color),
                )
            )
            if setup.render_border_rects:
                sheet_operations.append(
                    rectangle_operation(
                        inset_units,
                        setup.outer_top_units + inset_units,
                        setup.outer_right_units - inset_units,
                        setup.workspace_bottom_units - inset_units,
                        pen=make_pen(0x000000),
                    )
                )

        self._append_reference_zone_geometry(
            sheet_operations,
            setup,
            parameters=parameters,
            project_parameters=project_parameters,
        )

        if setup.title_block_on and self.sheet is not None:
            self._append_title_block_geometry(
                sheet_operations,
                sheet_width_mils=setup.sheet_width_mils,
                sheet_height_mils=setup.sheet_height_mils,
                margin=setup.margin,
                units_per_px=setup.units_per_px,
                parameters=parameters,
                project_parameters=project_parameters,
            )

        sheet_operations.extend(
            [
                SchGeometryOp.begin_group("DocumentItemsGroup"),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.end_group(),
                SchGeometryOp.pop_transform(),
            ]
        )
        return sheet_operations

    def _resolve_geometry_render_options(
        self,
        *,
        render_options: SchSvgRenderOptions | None,
        ir_profile: str | SchIrRenderProfile | None,
    ) -> tuple[Any, Any, Any]:
        """
        Resolve geometry profile and render options for IR generation.
        """
        from .altium_sch_geometry_oracle import (
            SchIrRenderProfile,
            normalize_sch_ir_render_profile,
        )
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        if ir_profile is None:
            resolved_ir_profile = (
                SchIrRenderProfile.ONSCREEN
                if render_options is not None
                else SchIrRenderProfile.ORACLE
            )
        else:
            resolved_ir_profile = normalize_sch_ir_render_profile(ir_profile)

        if resolved_ir_profile == SchIrRenderProfile.ONSCREEN:
            base_render_options = SchSvgRenderOptions.onscreen_compiled()
        else:
            base_render_options = replace(
                SchSvgRenderOptions.onscreen(),
                image_background_to_alpha=False,
                fallback_project_parameters_for_star=False,
            )

        requested_render_options = render_options or base_render_options
        geometry_render_options = replace(requested_render_options)
        return resolved_ir_profile, requested_render_options, geometry_render_options

    def _build_geometry_context(
        self,
        setup: _SchSheetGeometrySetup,
        *,
        source_admission: _SourceAdmission = _SourceAdmission(),
        geometry_render_options: Any,
        requested_render_options: Any,
        parameters: dict[str, str],
        project_parameters: dict[str, str] | None,
        connection_points: set[tuple[int, int]],
        explicit_junction_points: set[tuple[int, int]],
        harness_junction_points: set[tuple[int, int]],
        harness_port_colors: dict[str, int],
        harness_sheet_entry_colors: dict[str, int],
        wire_segments: list[Any],
        designator_text_overrides: dict[str, str] | None = None,
        blanket_metafile_line_patterns: bool = False,
    ) -> Any:
        """
        Build the shared rendering context used by geometry exporters.
        """
        from .altium_sch_svg_renderer import SchSvgRenderContext
        from ._sch_source_projection import _source_compile_mask_bounds

        context = SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=setup.sheet_height_mils,
            sheet_width=setup.sheet_width_mils,
            font_manager=self.font_manager,
            _horizontal_system_font_id=int(getattr(self.sheet, "system_font", 1) or 1),
            options=geometry_render_options,
            sheet_area_color=setup.area_color
            if setup.area_color is not None
            else 0xFFFFFF,
            parameters=parameters,
            project_parameters=project_parameters or {},
            designator_text_overrides=dict(designator_text_overrides or {}),
            render_group_ids=dict(self._geometry_render_group_ids),
            connection_points=connection_points,
            explicit_junction_points=explicit_junction_points,
            harness_junction_points=harness_junction_points,
            harness_port_colors=harness_port_colors,
            harness_sheet_entry_colors=harness_sheet_entry_colors,
            compile_mask_bounds=_source_compile_mask_bounds(
                source_admission.admitted(self.all_objects), precise=False
            ),
            wire_segments=wire_segments,
            document_path=str(self.filepath) if self.filepath else None,
            object_definitions=self.object_definitions,
            native_svg_export=bool(
                requested_render_options.truncate_font_size_for_baseline
            ),
        )
        context._source_admission = source_admission
        context._capture_parameter_set_state()
        context._blanket_metafile_line_patterns = blanket_metafile_line_patterns
        return context

    def _build_sheet_geometry_record(
        self,
        *,
        setup: _SchSheetGeometrySetup,
        doc_unique_id: str,
        sheet_operations: list[Any],
    ) -> Any:
        """
        Build the wrapper sheet record for the geometry document.
        """
        from .altium_sch_geometry_oracle import SchGeometryBounds, SchGeometryRecord

        return SchGeometryRecord(
            handle=f"{doc_unique_id}\\{setup.sheet_unique_id}",
            unique_id=setup.sheet_unique_id,
            kind="sheet",
            object_id="eSheet",
            bounds=SchGeometryBounds(left=0, top=0, right=0, bottom=0),
            operations=sheet_operations,
        )

    def _build_geometry_render_hints(
        self,
        *,
        resolved_ir_profile: Any,
        geometry_render_options: Any,
        geometry_ctx: Any,
    ) -> dict[str, Any] | None:
        """
        Build optional render hints for IR consumers.
        """
        from .altium_sch_geometry_oracle import SchIrRenderProfile
        from .altium_sch_svg_renderer import (
            COMPILED_COMPILE_MASK_OVERLAY_OPACITY,
            SchCompileMaskRenderMode,
        )
        from .altium_font_resolver import get_font_resolution_diagnostics

        render_hints: dict[str, Any] | None = None
        if resolved_ir_profile == SchIrRenderProfile.ONSCREEN:
            render_hints = {"ir_profile": SchIrRenderProfile.ONSCREEN.value}
        if (
            geometry_render_options.compile_mask_render_mode
            == SchCompileMaskRenderMode.COMPILED_VISUAL
            and geometry_ctx.compile_mask_bounds
        ):
            if render_hints is None:
                render_hints = {}
            render_hints["compile_mask"] = {
                "render_mode": "compiled_visual",
                "bounds": [list(bounds) for bounds in geometry_ctx.compile_mask_bounds],
                "background_color": str(geometry_ctx.background_color),
                "overlay_opacity": COMPILED_COMPILE_MASK_OVERLAY_OPACITY,
            }
        font_diagnostics = sorted(
            (diagnostic.to_dict() for diagnostic in get_font_resolution_diagnostics()),
            key=_font_resolution_hint_sort_key,
        )
        if font_diagnostics:
            if render_hints is None:
                render_hints = {}
            render_hints["font_resolution"] = {
                "schema": "wn.altium.font_resolution.a0",
                "diagnostics": font_diagnostics,
            }
        return render_hints

    def _build_runtime_image_hrefs(
        self,
        geometry_ctx: Any,
        *,
        doc_unique_id: str,
    ) -> dict[str, str]:
        """
        Build runtime image data URIs for image-backed geometry records.
        """
        import base64

        runtime_image_hrefs: dict[str, str] = {}
        for image in geometry_ctx._source_admission.admitted(self.all_objects):
            if not isinstance(image, AltiumSchImage) or not getattr(
                image, "image_data", None
            ):
                continue
            runtime_payload = image._runtime_image_payload(
                document_path=str(self.filepath) if self.filepath else None,
            )
            if runtime_payload is None:
                continue
            mime_type, image_data = runtime_payload
            runtime_key = (
                image.runtime_image_key(doc_unique_id)
                if hasattr(image, "runtime_image_key")
                else str(getattr(image, "unique_id", "") or "")
            )
            runtime_image_hrefs[runtime_key] = (
                f"data:{mime_type};base64,"
                + base64.b64encode(image_data).decode("ascii")
            )
        return runtime_image_hrefs

    def _build_geometry_document(
        self,
        *,
        records: list[Any],
        setup: _SchSheetGeometrySetup,
        doc_unique_id: str,
        resolved_ir_profile: Any,
        geometry_render_options: Any,
        geometry_ctx: Any,
        native_svg_hidden_image_ids: set[str],
    ) -> SchGeometryDocument:
        """
        Finalize the geometry document from collected records.
        """
        from .altium_sch_geometry_oracle import SchGeometryDocument

        from .altium_sch_paint_order import (
            _harness_splice_runtime_record_order,
            order_geometry_records_by_source,
        )

        source_objects = geometry_ctx._source_admission.source_objects
        records = order_geometry_records_by_source(
            records,
            source_objects,
            eligible_source_objects=geometry_ctx._source_admission.admitted(
                source_objects
            ),
        )
        records = _harness_splice_runtime_record_order(
            records,
            source_objects,
            show_template_graphics=bool(
                self.sheet and self.sheet.show_template_graphics
            ),
            native_svg_export=bool(getattr(geometry_ctx, "native_svg_export", False)),
            source_admission=geometry_ctx._source_admission,
        )
        render_hints = self._build_geometry_render_hints(
            resolved_ir_profile=resolved_ir_profile,
            geometry_render_options=geometry_render_options,
            geometry_ctx=geometry_ctx,
        )
        document = SchGeometryDocument(
            records=records,
            source_path=str(self.filepath) if self.filepath else None,
            source_kind="SCH",
            include_kinds=["all"],
            failed_renders=sum(
                isinstance(error, str) and bool(error)
                for record in records
                if (error := record.extras.get("error")) is not None
            ),
            coordinate_space={
                "kind": "screen_px_fixed",
                "units_per_px": setup.units_per_px,
                "y_axis_down": True,
            },
            canvas={
                "width_px": setup.sheet_width_mils,
                "height_px": setup.sheet_height_mils,
            },
            document_id=doc_unique_id,
            workspace_background_color="#E3E3E3",
            render_hints=render_hints,
            extras={
                "native_svg_hidden_image_ids": sorted(native_svg_hidden_image_ids),
            },
        )
        object.__setattr__(
            document,
            "_runtime_image_hrefs",
            self._build_runtime_image_hrefs(
                geometry_ctx,
                doc_unique_id=doc_unique_id,
            ),
        )
        return document

    def to_ir(
        self,
        project_parameters: dict[str, str] | None = None,
        *,
        profile: str | SchIrRenderProfile = "onscreen",
        render_options: SchSvgRenderOptions | None = None,
        designator_text_overrides: dict[str, str] | None = None,
    ) -> SchGeometryDocument:
        """
        Build schematic IR using a named render profile.

        `onscreen` is the default application profile. `oracle` preserves the
        stricter geometry contract used by comparison and export flows.
        """
        return self.to_geometry(
            project_parameters=project_parameters,
            render_options=render_options,
            ir_profile=profile,
            designator_text_overrides=designator_text_overrides,
        )

    def _render_physical_ir(
        self,
        project_parameters: dict[str, str] | None = None,
        *,
        profile: str | SchIrRenderProfile = "onscreen",
        render_options: SchSvgRenderOptions | None = None,
        designator_text_overrides: dict[str, str] | None = None,
        component_project_states: Mapping[int, _ComponentProjectRenderState]
        | None = None,
    ) -> SchGeometryDocument:
        """Render prepared project state without widening the public SchDoc API."""
        return self._render_geometry(
            self._prepare_render_source_admission(),
            project_parameters=project_parameters,
            render_options=render_options,
            ir_profile=profile,
            designator_text_overrides=designator_text_overrides,
            component_project_states=component_project_states,
        )

    def to_geometry(
        self,
        project_parameters: dict[str, str] | None = None,
        render_options: SchSvgRenderOptions | None = None,
        ir_profile: str | SchIrRenderProfile | None = None,
        designator_text_overrides: dict[str, str] | None = None,
    ) -> SchGeometryDocument:
        """
        Build a schematic IR document.

        The current implementation covers:
        - sheet/document borders and reference-zone primitives
        - sheet title-block geometry (standard and ANSI)
        - template wrappers and template-owned geometry children
        - arc, elliptical-arc, and pie primitives
        - simple shape primitives (lines, polygons, polylines, rectangles, rounded rectangles)
        - free labels
        - net labels, wires, buses, bus entries
        - standard ports and cross-sheet connectors
        - wrapper-only document parameters

        Use `to_ir()` when you want the named-profile wrapper. `to_geometry()`
        remains the lower-level IR construction entry point.
        """
        return self._render_geometry(
            self._prepare_render_source_admission(),
            project_parameters=project_parameters,
            render_options=render_options,
            ir_profile=ir_profile,
            designator_text_overrides=designator_text_overrides,
        )

    def _prepare_render_source_admission(self) -> _SourceAdmission:
        from ._sch_source_projection import _document_source_parents

        return _SourceAdmission.for_rendering(
            self.all_objects,
            parents=_document_source_parents(self),
            document_root=self.sheet,
        )

    def _render_geometry(
        self,
        source_admission: _SourceAdmission,
        *,
        project_parameters: dict[str, str] | None = None,
        render_options: SchSvgRenderOptions | None = None,
        ir_profile: str | SchIrRenderProfile | None = None,
        designator_text_overrides: dict[str, str] | None = None,
        component_project_states: Mapping[int, _ComponentProjectRenderState]
        | None = None,
    ) -> SchGeometryDocument:
        from .altium_font_resolver import clear_font_resolution_diagnostics
        from .altium_sch_geometry_oracle import (
            SchIrRenderProfile,
            _render_group_ids_for_sources,
        )

        clear_font_resolution_diagnostics()
        self._refresh_harness_bundle_field_identities(source_admission)
        units_per_px = 64
        workspace_bottom_px = 1000
        workspace_bottom_units = workspace_bottom_px * units_per_px
        doc_unique_id = self._file_unique_id or "AAAAAAAA"
        setup = self._build_sheet_geometry_setup(
            doc_unique_id=doc_unique_id,
            units_per_px=units_per_px,
            workspace_bottom_units=workspace_bottom_units,
        )
        param_dict = self._collect_top_level_geometry_parameters(
            source_admission=source_admission
        )
        resolved_ir_profile, requested_render_options, geometry_render_options = (
            self._resolve_geometry_render_options(
                render_options=render_options,
                ir_profile=ir_profile,
            )
        )
        sheet_operations = self._build_sheet_operations(
            setup,
            parameters=param_dict,
            project_parameters=project_parameters,
            doc_unique_id=doc_unique_id,
        )

        self._compute_port_connected_ends(source_admission=source_admission)
        connection_points = self._compute_connection_points(
            source_admission=source_admission
        )
        explicit_junction_points = {
            (junction.location.x, junction.location.y)
            for junction in source_admission.admitted(self.junctions)
            if getattr(junction, "index_in_sheet", None) is not None
            and int(getattr(junction, "index_in_sheet", -1)) >= 0
        }
        harness_junction_points = self._compute_harness_junction_points(
            source_admission=source_admission
        )
        harness_port_colors = self._compute_signal_harness_port_colors(
            source_admission=source_admission
        )
        harness_sheet_entry_colors = self._compute_signal_harness_sheet_entry_colors(
            source_admission=source_admission
        )
        wire_segments = self._collect_wire_segments(source_admission=source_admission)

        self._geometry_source_positions = {
            id(source_object): position
            for position, source_object in enumerate(source_admission.source_objects)
        }
        self._geometry_render_group_ids = _render_group_ids_for_sources(
            source_admission.source_objects
        )
        records: list[Any] = []
        geometry_ctx = self._build_geometry_context(
            setup,
            geometry_render_options=geometry_render_options,
            requested_render_options=requested_render_options,
            parameters=param_dict,
            project_parameters=project_parameters,
            connection_points=connection_points,
            explicit_junction_points=explicit_junction_points,
            harness_junction_points=harness_junction_points,
            harness_port_colors=harness_port_colors,
            harness_sheet_entry_colors=harness_sheet_entry_colors,
            wire_segments=wire_segments,
            designator_text_overrides=designator_text_overrides,
            source_admission=source_admission,
            blanket_metafile_line_patterns=(
                resolved_ir_profile is SchIrRenderProfile.ORACLE
            ),
        )
        ownership = self._build_geometry_ownership_state(geometry_ctx)
        native_svg_hidden_image_ids: set[str] = set()

        self._append_passive_top_level_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
            requested_render_options=requested_render_options,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
        )
        self._append_component_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
            component_project_states=component_project_states,
        )
        self._append_parameter_and_connector_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        if resolved_ir_profile is SchIrRenderProfile.ORACLE:
            self._restore_oracle_hidden_component_parameter_records(
                records,
                geometry_ctx,
                document_id=doc_unique_id,
                units_per_px=units_per_px,
                ownership=ownership,
            )
        self._append_hierarchy_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        self._append_harness_layout_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
        )
        sheet_record = self._build_sheet_geometry_record(
            setup=setup,
            doc_unique_id=doc_unique_id,
            sheet_operations=sheet_operations,
        )
        if self.sheet is not None:
            sheet_record = self._tag_source_geometry_record(sheet_record, self.sheet)
        records.append(sheet_record)
        self._append_signal_and_wire_geometry_records(
            records,
            geometry_ctx,
            document_id=doc_unique_id,
            units_per_px=units_per_px,
            ownership=ownership,
        )
        from .altium_record_sch__blanket import _complete_blanket_record_bounds

        records = _complete_blanket_record_bounds(
            records,
            geometry_ctx,
            document_kind="schematic",
        )
        return self._build_geometry_document(
            records=records,
            setup=setup,
            doc_unique_id=doc_unique_id,
            resolved_ir_profile=resolved_ir_profile,
            geometry_render_options=geometry_render_options,
            geometry_ctx=geometry_ctx,
            native_svg_hidden_image_ids=native_svg_hidden_image_ids,
        )

    def _append_title_block_geometry(
        self,
        sheet_operations: list[Any],
        *,
        sheet_width_mils: float,
        sheet_height_mils: float,
        margin: float,
        units_per_px: int,
        parameters: dict[str, str] | None = None,
        project_parameters: dict[str, str] | None = None,
    ) -> None:
        """
        Append title-block primitives to the sheet geometry record.
        """
        import datetime
        from .altium_record_sch__sheet import (
            DocumentBorderStyle,
            SHEET_STYLE_DESCRIPTIONS,
        )
        from .altium_sch_geometry_oracle import (
            SchGeometryOp,
            make_font_payload,
            make_pen,
            make_solid_brush,
            svg_coord_to_geometry,
        )
        from .altium_sch_svg_renderer import SchSvgRenderContext, SchSvgRenderOptions

        if self.sheet is None or not self.sheet.title_block_on:
            return

        title_ctx = SchSvgRenderContext(
            scale=1.0,
            flip_y=True,
            sheet_height=sheet_height_mils,
            sheet_width=sheet_width_mils,
            font_manager=self.font_manager,
            options=SchSvgRenderOptions.native_altium(),
            parameters=parameters or {},
            project_parameters=project_parameters or {},
            document_path=str(self.filepath) if self.filepath else None,
        )

        font_id = int(getattr(self.sheet, "system_font", 0) or 0)
        if font_id > 0 and title_ctx.font_manager is not None:
            font_spec = title_ctx.font_manager.get_font_info(font_id)
            if font_spec is not None:
                font_name, font_size_px, is_bold, is_italic, is_underline = (
                    title_ctx.get_font_info(font_id)
                )
            else:
                font_name, font_size_px, is_bold, is_italic, is_underline = (
                    title_ctx.get_system_font_info()
                )
        else:
            font_name, font_size_px, is_bold, is_italic, is_underline = (
                title_ctx.get_system_font_info()
            )

        render_font_size = title_ctx.get_baseline_font_size(font_size_px)
        text_brush = make_solid_brush(0x000000)
        line_pen = make_pen(0x000000)
        font_payload = make_font_payload(
            name=font_name,
            size_px=font_size_px,
            units_per_px=units_per_px,
            underline=is_underline,
            italic=is_italic,
            bold=is_bold,
        )

        def coord(x: float, y: float) -> tuple[float, float]:
            return svg_coord_to_geometry(
                x,
                y,
                sheet_height_px=sheet_height_mils,
                units_per_px=units_per_px,
            )

        def append_line(x1: float, y1: float, x2: float, y2: float) -> None:
            sheet_operations.append(
                SchGeometryOp.lines(
                    [
                        coord(x1, y1),
                        coord(x2, y2),
                    ],
                    pen=line_pen,
                )
            )

        def append_text(text: str, x: float, y: float) -> None:
            display_text = title_ctx.substitute_parameters(text)
            text_x, text_y = coord(x, y - render_font_size)
            sheet_operations.append(
                SchGeometryOp.string(
                    x=text_x,
                    y=text_y,
                    text=display_text,
                    font=font_payload,
                    brush=text_brush,
                )
            )

        def build_truncated_file_path() -> str:
            if not self.filepath:
                return ""
            return _shorten_managed_title_block_path(str(self.filepath))

        if self.sheet.document_border_style == DocumentBorderStyle.ANSI:
            tb_height = 175
            tb_width = 625
            tb_right_section = 425
            row1_top = 25
            row2_top = 63
            row3_top = 125

            tb_right = sheet_width_mils - margin
            tb_bottom = sheet_height_mils - margin
            tb_left = tb_right - tb_width
            tb_top = tb_bottom - tb_height
            tb_middle = tb_right - tb_right_section

            y_row1 = tb_bottom - row1_top
            y_row2 = tb_bottom - row2_top
            y_row3 = tb_bottom - row3_top
            x_size_fcsm = tb_right - 387
            x_scale_sheet = tb_right - 175
            x_fcsm_dwg = tb_right - 276
            x_dwg_rev = tb_right - 36

            append_line(tb_right, tb_top, tb_left, tb_top)
            append_line(tb_left, tb_bottom, tb_left, tb_top)
            append_line(tb_middle, tb_bottom, tb_middle, tb_top)
            append_line(tb_right, y_row3, tb_middle, y_row3)
            append_line(tb_right, y_row2, tb_middle, y_row2)
            append_line(tb_right, y_row1, tb_middle, y_row1)
            append_line(tb_middle, y_row2, tb_right - 0.00625, y_row2)
            append_line(tb_middle, y_row1, tb_right - 0.00625, y_row1)
            append_line(x_size_fcsm, y_row1, x_size_fcsm, y_row2)
            append_line(tb_right - 325, tb_bottom, tb_right - 325, y_row1)
            append_line(x_scale_sheet, tb_bottom, x_scale_sheet, y_row1)
            append_line(x_fcsm_dwg, y_row1, x_fcsm_dwg, y_row2)
            append_line(x_dwg_rev, y_row1, x_dwg_rev, y_row2)

            append_text("Scale", tb_middle + 5, y_row1 + 9)
            append_text("Sheet", x_scale_sheet + 5, y_row1 + 9)
            append_text("Size", tb_middle + 5, y_row2 + 9)
            append_text("FCSM No.", x_size_fcsm + 5, y_row2 + 9)
            append_text("DWG No.", x_fcsm_dwg + 5, y_row2 + 9)
            append_text("Rev", x_dwg_rev + 5, y_row2 + 9)
            append_text(
                SHEET_STYLE_DESCRIPTIONS.get(self.sheet.sheet_style, "Custom"),
                tb_middle + 5,
                y_row2 + 22,
            )
            return

        tb_width = 350
        tb_height = 80
        tb_right = sheet_width_mils - margin
        tb_bottom = sheet_height_mils - margin
        tb_left = tb_right - tb_width
        tb_top = tb_bottom - tb_height
        x_size_number = tb_left + 50
        x_number_revision = tb_right - 100
        x_sheet_divider = tb_right - 150
        now = datetime.datetime.now()
        date_str = f"{now.month}/{now.day:02d}/{now.year}"
        file_path = build_truncated_file_path()

        append_line(tb_left, tb_top, tb_right, tb_top)
        append_line(tb_left, tb_top + 30, tb_right, tb_top + 30)
        append_line(tb_left, tb_top + 60, tb_right, tb_top + 60)
        append_line(tb_left, tb_top + 70, tb_right, tb_top + 70)
        append_line(tb_left, tb_top, tb_left, tb_bottom)
        append_line(x_number_revision, tb_top + 30, x_number_revision, tb_top + 60)
        append_line(x_size_number, tb_top + 30, x_size_number, tb_top + 60)
        append_line(x_sheet_divider, tb_top + 60, x_sheet_divider, tb_bottom)

        append_text("Title", tb_left + 5, tb_top + 9)
        append_text("Number", x_size_number + 5, tb_top + 39)
        append_text("Revision", x_number_revision + 5, tb_top + 39)
        append_text("Size", tb_left + 5, tb_top + 39)
        append_text(
            SHEET_STYLE_DESCRIPTIONS.get(self.sheet.sheet_style, "Custom"),
            tb_left + 10,
            tb_top + 54,
        )
        append_text("Date:", tb_left + 5, tb_top + 69)
        append_text(date_str, x_size_number, tb_top + 69)
        append_text("Sheet   of", x_sheet_divider + 5, tb_top + 69)
        append_text("File:", tb_left + 5, tb_top + 79)
        append_text(file_path, x_size_number, tb_top + 79)
        append_text("Drawn By:", x_sheet_divider + 5, tb_top + 79)

    def to_svg(
        self,
        include_border: bool = True,
        scale: float = 1.0,
        options: SchSvgRenderOptions | None = None,
        project_parameters: dict[str, str] | None = None,
        wrap_components: bool = False,
    ) -> str:
        """
        Render schematic to SVG.

        Args:
            include_border: Include sheet border, reference zones, and title block
            scale: Scale factor (1.0 = default)
            options: SchSvgRenderOptions for customizing rendering (junction colors, z-order, etc.)
            project_parameters: Project-level parameters (from PrjPcb) for substitution.
                               These are used when schematic-level parameters don't have a match.
            wrap_components: If True, wrap each component in a `<g>` element
                with `id=unique_id` and `data-designator` metadata.

        Returns:
            Complete SVG document as string
        """
        from .altium_sch_geometry_renderer import (
            SchGeometrySvgRenderOptions,
            SchGeometrySvgRenderer,
        )
        from .altium_sch_svg_renderer import SchSvgRenderOptions

        if abs(scale - 1.0) > 1e-9:
            raise ValueError("Schematic IR SVG rendering currently requires scale=1.0")

        render_options = options if options is not None else SchSvgRenderOptions()
        ir_profile = (
            "oracle" if render_options.truncate_font_size_for_baseline else "onscreen"
        )

        source_admission = self._prepare_render_source_admission()
        document = self._render_geometry(
            source_admission,
            project_parameters=project_parameters,
            ir_profile=ir_profile,
            render_options=render_options,
        )
        runtime_image_hrefs = getattr(document, "_runtime_image_hrefs", None)

        if not include_border:
            document = replace(
                document,
                records=[
                    record
                    for record in document.records
                    if str(getattr(record, "kind", "") or "") != "sheet"
                ],
                workspace_background_color=None,
            )
            if isinstance(runtime_image_hrefs, dict):
                object.__setattr__(
                    document, "_runtime_image_hrefs", runtime_image_hrefs
                )

        if include_border and render_options.truncate_font_size_for_baseline:
            canvas = document.canvas or {}
            manual_junction_status = (
                self._build_native_manual_junction_status_render_hints(
                    sheet_height_px=int(canvas.get("height_px", 0) or 0),
                    source_admission=source_admission,
                )
            )
            if manual_junction_status:
                render_hints = dict(document.render_hints or {})
                render_hints["manual_junction_status"] = manual_junction_status
                document = replace(document, render_hints=render_hints)
                if isinstance(runtime_image_hrefs, dict):
                    object.__setattr__(
                        document, "_runtime_image_hrefs", runtime_image_hrefs
                    )

        return SchGeometrySvgRenderer(
            SchGeometrySvgRenderOptions(
                include_workspace_background=include_border,
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
        ).render(document)

    def _collect_compile_mask_bounds(self) -> list[tuple[int, int, int, int]]:
        """Collect expanded compile-mask bounds in Altium coordinates."""
        from ._sch_source_projection import _source_compile_mask_bounds

        return _source_compile_mask_bounds(self.all_objects, precise=False)

    def _collect_compile_mask_precise_bounds(
        self,
    ) -> list[tuple[int, int, int, int]]:
        """Collect expanded compile-mask bounds as fixed-point numerators."""
        from ._sch_source_projection import _source_compile_mask_bounds

        return _source_compile_mask_bounds(self.all_objects, precise=True)

    def _collect_wire_segments(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> list[tuple[int, int, int, int]]:
        """
        Collect all wire segments in Altium coordinates.
        """
        segments: list[tuple[int, int, int, int]] = []

        for wire in source_admission.admitted(self.wires):
            for start, end in zip(wire.points, wire.points[1:], strict=False):
                segments.append((start.x, start.y, end.x, end.y))

        return segments

    @staticmethod
    def _point_in_compile_mask(
        bounds: list[tuple[int, int, int, int]],
        x: int,
        y: int,
    ) -> bool:
        return any(
            min_x < x < max_x and min_y < y < max_y
            for min_x, min_y, max_x, max_y in bounds
        )

    @staticmethod
    def _point_on_segment_inclusive(
        px: int,
        py: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
    ) -> bool:
        """
        Return True when a point touches a segment endpoint or interior.
        """
        cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
        if abs(cross) > 1:
            return False

        if x1 == x2 and y1 == y2:
            return px == x1 and py == y1
        if x1 != x2:
            t = (px - x1) / (x2 - x1)
        else:
            t = (py - y1) / (y2 - y1)
        return -0.01 <= t <= 1.01

    @staticmethod
    def _port_is_vertical(port: object) -> bool:
        style = getattr(port, "style", 0)
        try:
            port_style = PortStyle(int(style))
        except (TypeError, ValueError):
            return False
        return port_style in {
            PortStyle.NONE_VERTICAL,
            PortStyle.TOP,
            PortStyle.BOTTOM,
            PortStyle.TOP_BOTTOM,
        }

    def _port_connection_endpoints(
        self, port: object
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        location = getattr(port, "location", None)
        if location is None:
            return ((0, 0), (0, 0))

        x = int(getattr(location, "x", 0))
        y = int(getattr(location, "y", 0))
        width = int(getattr(port, "width", 0) or 0)
        if self._port_is_vertical(port):
            return ((x, y), (x, y + width))
        return ((x, y), (x + width, y))

    def _compute_signal_harness_port_colors(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> dict[str, int]:
        """
        Map page-port IDs to the signal-harness color that makes them render
        as compiled harness objects.
        """
        harness_port_colors: dict[str, int] = {}
        signal_harnesses = list(source_admission.admitted(self.signal_harnesses))
        if not signal_harnesses:
            return harness_port_colors

        for port in source_admission.admitted(self.ports):
            port_id = str(getattr(port, "unique_id", "") or "")
            if not port_id:
                continue
            endpoints = self._port_connection_endpoints(port)
            for signal_harness in signal_harnesses:
                points = list(getattr(signal_harness, "points", []) or [])
                if len(points) < 2:
                    continue
                for start, end in zip(points, points[1:], strict=False):
                    if any(
                        self._point_on_segment_inclusive(
                            endpoint_x,
                            endpoint_y,
                            int(start.x),
                            int(start.y),
                            int(end.x),
                            int(end.y),
                        )
                        for endpoint_x, endpoint_y in endpoints
                    ):
                        harness_port_colors[port_id] = int(
                            getattr(signal_harness, "color", None) or 0
                        )
                        break
                if port_id in harness_port_colors:
                    break

        return harness_port_colors

    @staticmethod
    def _sheet_entry_connection_point(
        sheet_symbol: object,
        entry: object,
    ) -> tuple[int, int]:
        side = int(getattr(entry, "side", 0) or 0)
        distance_from_top = getattr(entry, "_distance_from_top_native_units", None)
        offset = (
            _optional_float(distance_from_top())
            if callable(distance_from_top)
            else None
        )
        if offset is None:
            offset = 0.0
        location = getattr(sheet_symbol, "location", None)
        parent_x = int(getattr(location, "x", 0)) if location is not None else 0
        parent_y = int(getattr(location, "y", 0)) if location is not None else 0
        parent_width = int(getattr(sheet_symbol, "x_size", 0) or 0)
        parent_height = int(getattr(sheet_symbol, "y_size", 0) or 0)

        if side == 1:
            return (parent_x + parent_width, int(round(parent_y - offset)))
        if side == 2:
            return (int(round(parent_x + offset)), parent_y)
        if side == 3:
            return (int(round(parent_x + offset)), parent_y - parent_height)
        return (parent_x, int(round(parent_y - offset)))

    def _compute_signal_harness_sheet_entry_colors(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> dict[str, int]:
        """
        Map sheet-entry IDs to connected signal-harness colors.
        """
        harness_entry_colors: dict[str, int] = {}
        signal_harnesses = list(source_admission.admitted(self.signal_harnesses))
        if not signal_harnesses:
            return harness_entry_colors

        for sheet_symbol in source_admission.admitted(self.sheet_symbols):
            for entry in source_admission.admitted(
                getattr(sheet_symbol, "entries", [])
            ):
                entry_id = str(getattr(entry, "unique_id", "") or "")
                if not entry_id:
                    continue
                endpoint_x, endpoint_y = self._sheet_entry_connection_point(
                    sheet_symbol,
                    entry,
                )
                for signal_harness in signal_harnesses:
                    points = list(getattr(signal_harness, "points", []) or [])
                    if len(points) < 2:
                        continue
                    for start, end in zip(points, points[1:], strict=False):
                        if self._point_on_segment_inclusive(
                            endpoint_x,
                            endpoint_y,
                            int(start.x),
                            int(start.y),
                            int(end.x),
                            int(end.y),
                        ):
                            harness_entry_colors[entry_id] = int(
                                getattr(signal_harness, "color", None) or 0
                            )
                            break
                    if entry_id in harness_entry_colors:
                        break

        return harness_entry_colors

    def _component_is_compile_masked(
        self,
        comp: AltiumSchComponent,
        compile_mask_bounds: list[tuple[int, int, int, int]],
    ) -> bool:
        """
        Approximate the component-level compilation-masked state for SVG rendering.

        Components are treated as masked only when the placement location and all
        rendered pin endpoints/hotspots fall inside compile masks. This preserves
        the observed native distinction between fully-contained components and
        straddling ones.
        """
        if not compile_mask_bounds:
            return False

        relevant_pins = [
            pin
            for pin in comp.pins
            if pin_belongs_to_component_view(pin, comp)
            and pin_is_managed_part_member(pin)
            and not pin_is_runtime_hidden(pin, comp)
        ]
        points = [comp.location, *(pin.get_hot_spot() for pin in relevant_pins)]
        return any(
            all(min_x < point.x < max_x and min_y < point.y < max_y for point in points)
            for min_x, min_y, max_x, max_y in compile_mask_bounds
        )

    def _compute_port_connected_ends(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> None:
        """
        Compute which end of each port is connected to a wire.

        Port endpoint mapping behavior:
          Origin is the record location.
          Extremity is ``Location + Width`` along the port's rendered axis.

        Sets _computed_connected_end on each port:
          0 = None (not connected to any wire/bus endpoint)
          1 = Origin has wire/bus endpoint
          2 = Extremity has wire/bus endpoint
          3 = Both ends connected
        """
        # Gather all wire endpoints
        wire_endpoints: set[tuple[int, int]] = set()
        for wire in source_admission.admitted(self.wires):
            for pt in wire.points:
                wire_endpoints.add((pt.x, pt.y))
        for bus in source_admission.admitted(self.buses):
            for pt in bus.points:
                wire_endpoints.add((pt.x, pt.y))

        for port in source_admission.admitted(self.ports):
            origin, extremity = self._port_connection_endpoints(port)
            has_origin = origin in wire_endpoints
            has_extremity = extremity in wire_endpoints

            if has_origin and has_extremity:
                _as_dynamic(port)._computed_connected_end = 3
            elif has_origin:
                _as_dynamic(port)._computed_connected_end = 1
            elif has_extremity:
                _as_dynamic(port)._computed_connected_end = 2
            else:
                _as_dynamic(port)._computed_connected_end = 0

    def _compute_connection_points(
        self,
        *,
        source_admission: _SourceAdmission = _SourceAdmission(),
        include_explicit_junctions: bool = True,
    ) -> set[tuple[int, int]]:
        """
        Compute all connection points where junctions should be rendered.

        Uses the current ObjectsCount >= 3 junction rule:
        - 3+ wire/bus endpoints meeting at same point -> render junction
        - Wire endpoint lying on another wire's segment (T-junction) -> render junction
        - 2 wire endpoints meeting (L-junction or continuation) -> no junction

        The key insight is that native Altium counts a wire passing through a point
        as 2 objects (segments before and after), while endpoints count as 1 each.

        Returns:
            Set of (x, y) tuples in Altium coordinates where junctions render
        """
        from collections import Counter

        wires = tuple(source_admission.admitted(self.wires))
        buses = tuple(source_admission.admitted(self.buses))
        signal_harnesses = tuple(source_admission.admitted(self.signal_harnesses))
        junctions = tuple(source_admission.admitted(self.junctions))

        def object_points_signature(
            objects: Iterable[object],
        ) -> tuple[tuple[tuple[int, int], ...], ...]:
            return tuple(
                tuple(
                    (int(point.x), int(point.y)) for point in getattr(obj, "points", ())
                )
                for obj in objects
            )

        connectable_signature = (
            *object_points_signature(wires),
            *object_points_signature(buses),
            *object_points_signature(signal_harnesses),
        )
        explicit_junction_signature = (
            tuple(
                (int(junction.location.x), int(junction.location.y))
                for junction in junctions
            )
            if include_explicit_junctions
            else ()
        )
        cache_key = (
            include_explicit_junctions,
            connectable_signature,
            explicit_junction_signature,
        )
        cached = self._connection_points_cache.get(cache_key)
        if cached is not None:
            return set(cached)

        def point_on_segment(
            px: int, py: int, x1: int, y1: int, x2: int, y2: int
        ) -> bool:
            """
            Check if point (px, py) lies strictly BETWEEN segment endpoints.
            """
            # Check if point is collinear with segment
            cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
            if abs(cross) > 1:  # Allow small tolerance for rounding
                return False

            # Check if point is within segment bounds
            if x1 != x2:
                t = (px - x1) / (x2 - x1)
            elif y1 != y2:
                t = (py - y1) / (y2 - y1)
            else:
                return False  # Degenerate segment (zero length)

            # Point must be strictly BETWEEN endpoints (not at endpoints)
            # This ensures we only detect T-junctions, not endpoint meetings
            return 0.01 < t < 0.99

        # Count wire SEGMENTS at each point, not wire objects
        # - Endpoint (first/last vertex): contributes 1 segment
        # - Corner (intermediate vertex): contributes 2 segments (end of one, start of next)
        # This matches native Altium's ObjectsCount which counts segments, not objects
        point_counts: Counter[tuple[int, int]] = Counter()

        # Collect all wire/bus/harness objects for segment checking
        all_connectable = [
            obj
            for obj in (list(wires) + list(buses) + list(signal_harnesses))
            if isinstance(obj, AltiumSchWire)
        ]

        def count_object_points(obj: AltiumSchWire) -> None:
            """
            Count points from a wire/bus/harness object.
            """
            if len(obj.points) == 0:
                return
            elif len(obj.points) == 1:
                # Single point - counts as 1
                pt = obj.points[0]
                point_counts[(pt.x, pt.y)] += 1
            else:
                # Multi-point: endpoints count 1, intermediate points count 2
                for i, pt in enumerate(obj.points):
                    if i == 0 or i == len(obj.points) - 1:
                        # Endpoint: 1 segment
                        point_counts[(pt.x, pt.y)] += 1
                    else:
                        # Intermediate/corner: 2 segments (end of one, start of next)
                        point_counts[(pt.x, pt.y)] += 2

        # Collect points from wires
        for wire in wires:
            count_object_points(wire)

        # Collect points from buses
        for bus in buses:
            count_object_points(bus)

        # Collect points from signal harnesses
        for harness in signal_harnesses:
            count_object_points(harness)

        # Find T-junctions: wire/bus endpoint lies ON another object's segment
        # These count as 3+ objects in native Altium (endpoint + 2 segment halves)
        t_junctions: set[tuple[int, int]] = set()
        vertical_segments: dict[int, list[tuple[object, int, int, int, int]]] = {}
        horizontal_segments: dict[int, list[tuple[object, int, int, int, int]]] = {}
        diagonal_segments: list[tuple[object, int, int, int, int]] = []

        for other in all_connectable:
            if len(other.points) < 2:
                continue
            for i in range(len(other.points) - 1):
                p1 = other.points[i]
                p2 = other.points[i + 1]
                segment = (other, p1.x, p1.y, p2.x, p2.y)
                if p1.x == p2.x:
                    vertical_segments.setdefault(p1.x, []).append(segment)
                elif p1.y == p2.y:
                    horizontal_segments.setdefault(p1.y, []).append(segment)
                else:
                    diagonal_segments.append(segment)

        def any_indexed_segment_contains_point(
            obj: object,
            px: int,
            py: int,
        ) -> bool:
            for other, x1, y1, x2, y2 in vertical_segments.get(px, ()):
                if other is obj:
                    continue
                if point_on_segment(px, py, x1, y1, x2, y2):
                    return True
            for other, x1, y1, x2, y2 in horizontal_segments.get(py, ()):
                if other is obj:
                    continue
                if point_on_segment(px, py, x1, y1, x2, y2):
                    return True
            for other, x1, y1, x2, y2 in diagonal_segments:
                if other is obj:
                    continue
                if point_on_segment(px, py, x1, y1, x2, y2):
                    return True
            return False

        for obj in all_connectable:
            if len(obj.points) < 1:
                continue
            # Check each endpoint of this object
            for pt in obj.points:
                px, py = pt.x, pt.y
                if any_indexed_segment_contains_point(obj, px, py):
                    t_junctions.add((px, py))

        # Connection points where junctions render:
        # 1. Points with 3+ endpoints (star junctions)
        # 2. T-junctions (endpoint on another's segment)
        # 3. Explicit junction records
        connection_points = {pt for pt, count in point_counts.items() if count >= 3}
        connection_points.update(t_junctions)

        # Add explicit junction locations (manual junctions always render)
        if include_explicit_junctions:
            for junction in junctions:
                connection_points.add((junction.location.x, junction.location.y))

        self._connection_points_cache[cache_key] = frozenset(connection_points)
        return connection_points

    def _build_native_manual_junction_status_render_hints(
        self,
        *,
        source_admission: _SourceAdmission = _SourceAdmission(),
        sheet_height_px: int,
    ) -> list[dict[str, float | str]]:
        """
        Build native SVG manual-junction-status overlays.

        Native Altium emits an extra document-level junction pass before
        ``DocumentItemsGroup`` for persisted manual junction records that are
        also active connection points. Imported/grid-generated junction dots
        in the FRDM corpus are serialized as transient ``IndexInSheet=-1``
        records and do not participate in this pass. The dots use the
        painter's manual-junction preference color (``0x800000`` -> ``#000080``)
        in stored BGR byte order, not the junction record color.
        """
        if sheet_height_px <= 0:
            return []

        from .altium_record_types import color_to_hex

        connection_points = self._compute_connection_points(
            include_explicit_junctions=False, source_admission=source_admission
        )
        overlays: list[dict[str, float | str]] = []
        seen: set[tuple[int, int]] = set()
        manual_junction_color_hex = color_to_hex(0x800000)

        for junction in source_admission.admitted(self.junctions):
            junction_index = getattr(junction, "index_in_sheet", None)
            if junction_index is None or int(junction_index) < 0:
                continue
            center = (junction.location.x, junction.location.y)
            if center not in connection_points or center in seen:
                continue
            seen.add(center)
            overlays.append(
                {
                    "x": float(junction.location.x - 2),
                    "y": float(sheet_height_px - junction.location.y - 2),
                    "width": 4.0,
                    "height": 4.0,
                    "rx": 2.0,
                    "ry": 2.0,
                    "fill": manual_junction_color_hex,
                    "stroke": manual_junction_color_hex,
                }
            )

        return overlays

    def _compute_harness_junction_points(
        self, source_admission: _SourceAdmission = _SourceAdmission()
    ) -> set[tuple[int, int]]:
        """
        Compute signal harness junction points (T-junctions).

        A harness junction is rendered when one harness's START point lies
        geometrically on another harness's line segment. This creates a T-junction
        where the "stem" harness connects to the "cross" harness.

        Unlike wire/bus junctions (which require explicit vertex sharing),
        signal harness junctions are determined by geometric intersection.

        Returns:
            Set of (x, y) tuples in Altium coordinates where junctions should render
        """
        signal_harnesses = tuple(source_admission.admitted(self.signal_harnesses))
        if len(signal_harnesses) < 2:
            return set()

        junction_points: set[tuple[int, int]] = set()

        def point_on_segment(
            px: int, py: int, x1: int, y1: int, x2: int, y2: int
        ) -> bool:
            """
            Check if point (px, py) lies on line segment (x1,y1)-(x2,y2).
            """
            # Check if point is collinear with segment
            cross = (py - y1) * (x2 - x1) - (px - x1) * (y2 - y1)
            if abs(cross) > 1:  # Allow small tolerance
                return False

            # Check if point is within segment bounds
            if x1 != x2:
                t = (px - x1) / (x2 - x1)
            elif y1 != y2:
                t = (py - y1) / (y2 - y1)
            else:
                return px == x1 and py == y1  # Degenerate segment

            # Point must be strictly BETWEEN endpoints (not at endpoints)
            return 0.01 < t < 0.99

        # For each harness, check if its start point lies on any other harness's segment
        for harness in signal_harnesses:
            if len(harness.points) < 1:
                continue
            start_pt = harness.points[0]
            start_x, start_y = start_pt.x, start_pt.y

            for other in signal_harnesses:
                if other is harness:
                    continue
                # Check each segment of the other harness
                for i in range(len(other.points) - 1):
                    p1 = other.points[i]
                    p2 = other.points[i + 1]
                    if point_on_segment(start_x, start_y, p1.x, p1.y, p2.x, p2.y):
                        junction_points.add((start_x, start_y))
                        break

        return junction_points

    def _save_roundtrip(self, filepath: Path, debug: bool = False) -> bool:
        """
        Save SchDoc to file (round-trip support).

        Uses OleWriter (same approach as SchLibCleaner) to write modified OLE file.

        Args:
            filepath: Output path (defaults to self.filepath)
            debug: Enable debug output

        Returns:
            True if save was successful, False otherwise
        """
        if filepath is None:
            filepath = self.filepath

        if filepath is None:
            raise ValueError("No filepath specified for save")

        filepath = Path(filepath)
        log.info(f"Saving SchDoc to: {filepath}")
        try:
            staged, object_map = self._clone_for_staging()
            ole_writer = staged._stage_roundtrip_writer(debug=debug)
            commit_plan = self._prepare_commit_plan(
                staged, ole_writer, filepath, object_map=object_map
            )
            staged._write_staged_container(ole_writer, filepath)
            self._apply_commit_plan(commit_plan)
            log.info(f"  Saved successfully: {len(self.all_objects)} objects")
            return True

        except Exception as e:
            log.error(f"  Error saving {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            return False

    def _clone_for_staging(self) -> tuple["AltiumSchDoc", dict[int, object]]:
        live_objects = [*self.all_objects, *self._object_definition_objects]
        staged = deepcopy(self)
        staged_objects = [
            *staged.all_objects,
            *staged._object_definition_objects,
        ]
        if len(live_objects) != len(staged_objects):
            raise SchDocContainerError(
                "malformed", "staged SchDoc object inventory changed during cloning"
            )
        return staged, {
            id(staged_object): live_object
            for staged_object, live_object in zip(
                staged_objects, live_objects, strict=True
            )
        }

    def _commit_authoring_candidate(
        self,
        staged: "AltiumSchDoc",
        object_map: dict[int, object],
    ) -> None:
        """Commit a fully validated candidate while preserving live identities."""
        staged_objects = [
            *staged.all_objects,
            *staged._object_definition_objects,
        ]
        memo = dict(object_map)
        candidate_state = deepcopy(vars(staged), memo)

        for staged_object in staged_objects:
            live_object = object_map.get(id(staged_object))
            if live_object is None:
                continue
            live_state = deepcopy(vars(staged_object), memo)
            vars(live_object).clear()
            vars(live_object).update(live_state)

        live_collection = self._objects
        candidate_objects = list(candidate_state["_objects"])
        live_collection.clear()
        live_collection.extend(candidate_objects)
        candidate_state["_objects"] = live_collection

        live_font_manager = getattr(self, "_font_manager", None)
        candidate_font_manager = candidate_state.get("_font_manager")
        if live_font_manager is not None and candidate_font_manager is not None:
            vars(live_font_manager).clear()
            vars(live_font_manager).update(vars(candidate_font_manager))
            candidate_state["_font_manager"] = live_font_manager

        vars(self).clear()
        vars(self).update(candidate_state)
        vars(self).pop("_schematic_binding_context", None)
        self._bind_all_objects_to_context()

    def _stage_roundtrip_writer(self, *, debug: bool) -> AltiumOleWriter:
        """Build a complete SchDoc writer using only staged object state."""
        from .altium_ole import AltiumOleWriter

        self._bind_all_objects_to_context()
        source_images = dict(self.embedded_images)
        self._sync_embedded_images_from_objects()
        storage_dirty = self.embedded_images != source_images
        indices_recalculated = bool(
            self._index_sync_dirty
            or self._dirty_index_owner_ids
            or self._stream_weight_dirty
        )
        if indices_recalculated:
            self._recalculate_indices()

        writer = AltiumOleWriter()
        for storage in self._source_storages:
            writer.addEntry(storage, storage=True)
        for path, payload in self._source_streams.items():
            writer.add_stream(path, payload)

        fileheader_objects = self._objects_for_stream("FileHeader")
        additional_objects = self._objects_for_stream("Additional")
        if indices_recalculated:
            self._sync_dirty_stream_owner_indices(
                fileheader_objects,
                "FileHeader",
                base_objects=fileheader_objects,
            )
            self._sync_dirty_stream_owner_indices(
                additional_objects,
                "Additional",
                base_objects=fileheader_objects,
            )
        self._fileheader_objects = list(fileheader_objects)
        self._additional_objects = list(additional_objects)
        fileheader_data = self._build_stream_data(
            fileheader_objects, "FileHeader", debug
        )
        self._edit_writer_root_stream(
            writer,
            "FileHeader",
            data=self._reuse_context_stream_if_unchanged("FileHeader", fileheader_data),
        )
        if additional_objects or self._writer_has_root_stream(writer, "Additional"):
            additional_data = self._build_stream_data(
                additional_objects, "Additional", debug
            )
            additional_data = self._reuse_context_stream_if_unchanged(
                "Additional", additional_data
            )
            if self._writer_has_root_stream(writer, "Additional"):
                self._edit_writer_root_stream(
                    writer, "Additional", data=additional_data
                )
            else:
                writer.addEntry("Additional", data=additional_data)

        self._refresh_staged_owner_map()
        self._stage_object_definitions_stream(writer)
        self._stage_storage_stream(writer, storage_dirty=storage_dirty, debug=debug)
        self._stage_harness_connection_point_stream(writer, fileheader_objects)
        self._validate_staged_writer(writer)
        return writer

    @staticmethod
    def _stage_harness_connection_point_stream(
        writer: AltiumOleWriter,
        fileheader_objects: Iterable[object],
    ) -> None:
        points = [
            obj
            for obj in fileheader_objects
            if type(obj) is AltiumSchHarnessLayoutConnectionPoint
        ]
        path = _root_stream_path(writer._streams, "HarnessConnectionPointConnector")
        if not points:
            if path is not None:
                writer._remove_stream(path)
            return
        rows = tuple(
            HarnessConnectionPointData(
                connection_point_id=str(point.unique_id or ""),
                connectors=tuple(
                    HarnessConnectionPointConnectorData(
                        connector_id=connector.connector_id,
                        pin_ids=tuple(connector.pins),
                    )
                    for connector in point.connectors
                ),
            )
            for point in points
        )
        data = encode_harness_connection_point_stream(rows)
        if path is None:
            writer.addEntry("HarnessConnectionPointConnector", data=data)
        else:
            writer.editEntry(path, data=data)

    def _stage_storage_stream(
        self, writer: AltiumOleWriter, *, storage_dirty: bool, debug: bool
    ) -> None:
        source_storage = _root_stream(self._source_streams, "Storage")
        storage_weight_stale = source_storage is not None and _storage_weight_is_stale(
            source_storage, _SchDocBudget(self._read_limits)
        )
        storage_missing = not self._writer_has_root_stream(writer, "Storage")
        if not (
            storage_dirty
            or storage_weight_stale
            or self.embedded_images
            and storage_missing
        ):
            return
        storage_data = self._build_storage_stream(debug)
        if storage_missing:
            writer.addEntry("Storage", data=storage_data)
            return
        self._edit_writer_root_stream(writer, "Storage", data=storage_data)

    def _stage_object_definitions_stream(self, writer: AltiumOleWriter) -> None:
        source = _root_stream(self._source_streams, "ObjectDefinitions")
        if source is None:
            return
        from .altium_utilities import encode_altium_record

        warehouse = _parse_warehouse(
            source,
            "ObjectDefinitions",
            _SchDocBudget(self._read_limits),
            weight_required=False,
        )
        header = self._updated_stream_header(
            warehouse.header, "ObjectDefinitions", len(warehouse.records)
        )
        self._object_definitions_header = header
        if not warehouse.weight_is_stale:
            return
        rebuilt = self._replace_first_frame(source, encode_altium_record(header))
        self._edit_writer_root_stream(writer, "ObjectDefinitions", data=rebuilt)

    @staticmethod
    def _writer_has_root_stream(writer: AltiumOleWriter, name: str) -> bool:
        return _root_stream_path(writer._streams, name) is not None

    @staticmethod
    def _edit_writer_root_stream(
        writer: AltiumOleWriter, name: str, *, data: bytes
    ) -> None:
        path = _root_stream_path(writer._streams, name)
        writer.editEntry(path or name, data=data)

    def _refresh_staged_owner_map(self) -> None:
        self._fileheader_raw_records = self._raw_records_for_objects(
            self._fileheader_objects
        )
        self._additional_raw_records = self._raw_records_for_objects(
            self._additional_objects
        )
        self._validate_and_assign_owner_refs()

    def _reuse_context_stream_if_unchanged(
        self, stream: str, candidate: bytes
    ) -> bytes:
        source = _root_stream(self._source_streams, stream)
        if source is None:
            return candidate
        original = _parse_warehouse(
            source,
            stream,
            _SchDocBudget(self._read_limits),
            weight_required=stream == "FileHeader",
        )
        rebuilt = _parse_warehouse(
            candidate,
            stream,
            _SchDocBudget(self._read_limits),
            weight_required=stream == "FileHeader",
        )
        if original.header == rebuilt.header and original.records == rebuilt.records:
            return source
        if original.records == rebuilt.records:
            return self._replace_first_frame(source, candidate)
        return candidate

    @staticmethod
    def _replace_first_frame(source: bytes, candidate: bytes) -> bytes:
        source_end = 4 + int.from_bytes(source[:4], "little")
        candidate_end = 4 + int.from_bytes(candidate[:4], "little")
        return candidate[:candidate_end] + source[source_end:]

    def _objects_for_stream(self, stream: str) -> list[object]:
        """Return the staged physical vector without collapsing stream partitions."""
        if not self._index_sync_dirty and stream not in self._stream_weight_dirty:
            return list(
                self._fileheader_objects
                if stream == "FileHeader"
                else self._additional_objects
            )
        objects = [
            obj
            for obj in self.all_objects
            if getattr(obj, "_source_stream", "FileHeader") == stream
        ]
        self._sync_dirty_stream_owner_indices(
            objects,
            stream,
            base_objects=self._fileheader_objects,
        )
        return objects

    def _sync_dirty_stream_owner_indices(
        self,
        objects: list[object],
        stream: str,
        *,
        base_objects: Collection[object],
    ) -> None:
        """Apply owner positions within one selected physical stream vector."""
        local_positions = {id(obj): index for index, obj in enumerate(objects)}
        base_positions = {id(obj): index for index, obj in enumerate(base_objects)}
        for obj in objects:
            parent = getattr(obj, "parent", None)
            if parent is None:
                continue
            owner = local_positions.get(id(parent))
            uses_additional = stream == "Additional" and owner is not None
            if owner is None and stream == "Additional":
                owner = base_positions.get(id(parent))
            if owner is None:
                continue
            self._set_owned_object_owner_index(obj, owner)
            if hasattr(obj, "owner_index_additional_list"):
                _as_dynamic(obj).owner_index_additional_list = uses_additional

    def _validate_staged_writer(self, writer: AltiumOleWriter) -> None:
        limits = self._read_limits.validate()
        if len(writer._streams) > limits.max_streams_per_container:
            raise SchDocContainerError("limit", "output has too many streams")
        directory_entries = 1 + len(writer._streams) + len(writer._storages)
        if directory_entries > limits.max_ole_directory_entries:
            raise SchDocContainerError(
                "limit", "output has too many OLE directory entries"
            )
        aggregate = 0
        for path, payload in writer._streams.items():
            if len(payload) > limits.max_stream_bytes:
                raise SchDocContainerError(
                    "limit", "output stream exceeds stream limit", stream=path
                )
            aggregate += len(payload)
            if aggregate > limits.max_container_bytes:
                raise SchDocContainerError(
                    "limit", "output aggregate stream bytes exceed container limit"
                )
        budget = _SchDocBudget(limits)
        fileheader_data = _root_stream(writer._streams, "FileHeader")
        if fileheader_data is None:
            raise SchDocContainerError(
                "missing", "output is missing FileHeader", stream="FileHeader"
            )
        fileheader = _parse_warehouse(
            fileheader_data, "FileHeader", budget, weight_required=True
        )
        _validate_fileheader_records(fileheader.records)
        additional_data = _root_stream(writer._streams, "Additional")
        if additional_data is not None:
            _parse_warehouse(
                additional_data,
                "Additional",
                budget,
                weight_required=False,
            )
        definitions_data = _root_stream(writer._streams, "ObjectDefinitions")
        if definitions_data is not None:
            _parse_warehouse(
                definitions_data,
                "ObjectDefinitions",
                budget,
                weight_required=False,
            )
        _parse_storage(_root_stream(writer._streams, "Storage"), budget)

    def _write_staged_container(self, writer: AltiumOleWriter, filepath: Path) -> None:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{filepath.name}.", suffix=".tmp", dir=filepath.parent
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            writer.write(temporary)
            if temporary.stat().st_size > self._read_limits.max_container_bytes:
                raise SchDocContainerError(
                    "limit", "output OLE container exceeds container byte limit"
                )
            AltiumSchDoc(temporary, _read_limits=self._read_limits)
            os.replace(temporary, filepath)
        finally:
            temporary.unlink(missing_ok=True)

    def _prepare_commit_plan(
        self,
        staged: "AltiumSchDoc",
        writer: AltiumOleWriter,
        filepath: Path,
        *,
        object_map: dict[int, object],
    ) -> _SchDocCommitPlan:
        source_streams = dict(writer._streams)
        return _SchDocCommitPlan(
            object_states=self._mapped_commit_states(staged, object_map),
            objects=ObjectCollection(self._mapped_objects(staged.objects, object_map)),
            sheet=self._mapped_object(staged.sheet, object_map),
            fileheader_objects=self._mapped_objects(
                staged._fileheader_objects, object_map
            ),
            additional_objects=self._mapped_objects(
                staged._additional_objects, object_map
            ),
            definition_objects=self._mapped_objects(
                staged._object_definition_objects, object_map
            ),
            fileheader_raw_records=self._raw_records_for_objects(
                list(staged._fileheader_objects)
            ),
            additional_raw_records=self._raw_records_for_objects(
                list(staged._additional_objects)
            ),
            normalized_owner_refs={
                id(object_map[staged_id]): reference
                for staged_id, reference in staged._normalized_owner_refs.items()
                if staged_id in object_map
            },
            fileheader_header=self._copy_optional_header(staged._fileheader_header),
            additional_header=self._copy_optional_header(staged._additional_header),
            object_definitions_header=self._copy_optional_header(
                staged._object_definitions_header
            ),
            file_weight=staged._file_weight,
            embedded_images=dict(staged.embedded_images),
            source_streams=source_streams,
            source_storages=tuple(sorted(writer._storages)),
            raw_storage_entries=self._prepared_raw_storage_entries(source_streams),
            filepath=filepath,
        )

    @staticmethod
    def _mapped_object(target: object, object_map: dict[int, object]) -> object:
        live = object_map.get(id(target))
        if live is None:
            raise SchDocContainerError(
                "malformed", "staged SchDoc object has no live counterpart"
            )
        return live

    def _mapped_objects(
        self, targets: Iterable[object], object_map: dict[int, object]
    ) -> list[object]:
        return [self._mapped_object(target, object_map) for target in targets]

    def _mapped_commit_states(
        self, staged: "AltiumSchDoc", object_map: dict[int, object]
    ) -> tuple[_SchObjectSyncState, ...]:
        states = staged._capture_local_sync_state().objects
        return tuple(
            replace(state, target=self._mapped_object(state.target, object_map))
            for state in states
        )

    @staticmethod
    def _copy_optional_header(
        header: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return dict(header) if header is not None else None

    def _prepared_raw_storage_entries(
        self, source_streams: dict[str, bytes]
    ) -> dict[str, tuple[bytes, bytes]]:
        entries = _parse_storage(
            _root_stream(source_streams, "Storage"),
            _SchDocBudget(self._read_limits),
        )
        return {
            entry.name: (entry.binary_header, entry.compressed_data)
            for entry in entries
        }

    def _apply_commit_plan(self, plan: _SchDocCommitPlan) -> None:
        for state in plan.object_states:
            self._apply_object_sync_state(state.target, state)
        self._objects.clear()
        self._objects.extend(plan.objects)
        self.sheet = cast(AltiumSchSheet, plan.sheet)
        self._fileheader_objects = plan.fileheader_objects
        self._additional_objects = plan.additional_objects
        self._object_definition_objects = plan.definition_objects
        self._fileheader_raw_records = plan.fileheader_raw_records
        self._additional_raw_records = plan.additional_raw_records
        self._normalized_owner_refs = plan.normalized_owner_refs
        self._fileheader_header = plan.fileheader_header
        self._additional_header = plan.additional_header
        self._object_definitions_header = plan.object_definitions_header
        self._file_weight = plan.file_weight
        self.embedded_images = plan.embedded_images
        self._source_streams = plan.source_streams
        self._source_storages = plan.source_storages
        self._raw_storage_entries = plan.raw_storage_entries
        self.filepath = plan.filepath
        self._finish_local_sync()

    @staticmethod
    def _raw_records_for_objects(objects: list[object]) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        for obj in objects:
            raw = getattr(obj, "_raw_record", None)
            if not isinstance(raw, dict):
                raise SchDocContainerError(
                    "malformed", "staged SchDoc object is missing its raw record"
                )
            records.append(dict(raw))
        return records

    def to_schdoc(self, filepath: Path | str, debug: bool = False) -> bool:
        """
        Write SchDoc to file.

        This is the single API for saving SchDoc files to disk.

        Behavior:
            - If loaded from an existing file via the constructor, uses round-trip mode
              to preserve the original OLE structure as much as possible.
            - If created fresh via `AltiumSchDoc()`, creates a new OLE file
              from scratch with proper FileHeader and Storage streams.

        Args:
            filepath: Output path for .SchDoc file
            debug: Enable debug output

        Returns:
            True if save was successful, False otherwise

        Raises:
            ValueError: If SchDoc doesn't meet minimum requirements (no Sheet)
        """
        # Validate minimum requirements
        if self.sheet is None:
            raise ValueError(
                "SchDoc must have a Sheet record. Use AltiumSchDoc() constructor to auto-create one."
            )

        filepath = Path(filepath)

        # Auto-detect mode: use round-trip if we have an original file
        if self.filepath and Path(self.filepath).exists():
            log.info(f"Saving SchDoc (round-trip mode): {filepath}")
            return self._save_roundtrip(filepath, debug)
        else:
            log.info(f"Creating SchDoc (from scratch): {filepath}")
            return self._create_new(filepath, debug)

    def save(self, filepath: Path | str, debug: bool = False) -> bool:
        """
        Save to binary SchDoc format.

        This is the canonical public write path. Prefer `save()` over
        format-specific helpers such as `to_schdoc()`.

                Args:
                    filepath: Output file path.
                    debug: Enable debug output.

                Returns:
                    True if successful.
        """
        return self.to_schdoc(Path(filepath), debug=debug)

    def _create_new(self, filepath: Path, debug: bool = False) -> bool:
        """
        Create new SchDoc from scratch (internal method).

        Called by to_schdoc() when no original file exists.

        Harness objects (RECORD 215-218) are written to the Additional stream,
        all other objects go to FileHeader stream. This is required for proper
        parent-child linking via OwnerIndexAdditionalList.
        """
        try:
            staged, object_map = self._clone_for_staging()
            ole_writer = staged._stage_new_writer(debug=debug)
            commit_plan = self._prepare_commit_plan(
                staged, ole_writer, filepath, object_map=object_map
            )
            staged._write_staged_container(ole_writer, filepath)
            self._apply_commit_plan(commit_plan)
            log.info(
                f"  Created successfully: {len(self.all_objects)} objects "
                f"(FileHeader: {len(staged._objects_for_stream('FileHeader'))}, "
                f"Additional: {len(staged._objects_for_stream('Additional'))})"
            )
            return True

        except Exception as e:
            log.error(f"  Error creating {filepath.name}: {e}")
            if debug:
                import traceback

                traceback.print_exc()
            return False

    def _stage_new_writer(self, *, debug: bool) -> AltiumOleWriter:
        from .altium_ole import AltiumOleWriter
        from .altium_utilities import encode_altium_record

        self._bind_all_objects_to_context()
        self._sync_embedded_images_from_objects()
        self._recalculate_indices()
        fileheader_objects = self._objects_for_stream("FileHeader")
        additional_objects = self._objects_for_stream("Additional")
        self._fileheader_objects = list(fileheader_objects)
        self._additional_objects = list(additional_objects)
        writer = AltiumOleWriter()
        writer.addEntry(
            "FileHeader",
            data=self._build_stream_data(fileheader_objects, "FileHeader", debug),
        )
        writer.addEntry(
            "Additional",
            data=self._build_stream_data(additional_objects, "Additional", debug),
        )
        self._refresh_staged_owner_map()
        storage_data = (
            self._build_storage_stream(debug)
            if self.embedded_images
            else encode_altium_record({"HEADER": "Icon storage"})
        )
        writer.addEntry("Storage", data=storage_data)
        self._stage_harness_connection_point_stream(writer, fileheader_objects)
        self._validate_staged_writer(writer)
        return writer

    def _build_stream_data(
        self,
        objects: list[Any],
        stream_type: str,
        debug: bool = False,
    ) -> bytes:
        """
        Build stream data for either FileHeader or Additional stream.

        Args:
            objects: List of objects to serialize
            stream_type: 'FileHeader' or 'Additional'
            debug: Enable debug output

        Returns:
            Bytes for the stream
        """
        from .altium_utilities import encode_altium_record

        from .altium_sch_stream_sync import _derive_stream_weight

        _derive_stream_weight(len(objects))
        self._refresh_harness_bundle_field_identities()
        staged_records = self._stage_stream_records(objects)
        weight = _derive_stream_weight(len(staged_records))
        records_data: list[bytes] = []

        header_record = self._stream_header_with_weight(stream_type, weight)
        records_data.append(encode_altium_record(header_record))

        records_data.extend(staged_records)

        # Combine all records
        stream_data = b"".join(records_data)
        if stream_type == "FileHeader":
            self._file_weight = weight
        self._stream_weight_dirty.discard(stream_type)
        return stream_data

    def _stream_header_with_weight(
        self, stream_type: str, weight: int
    ) -> dict[str, object]:
        source = (
            self._fileheader_header
            if stream_type == "FileHeader"
            else self._additional_header
        )
        header = (
            self._new_stream_header(stream_type, weight)
            if source is None
            else self._updated_stream_header(source, stream_type, weight)
        )
        if stream_type == "FileHeader":
            self._fileheader_header = header
        else:
            self._additional_header = header
        return header

    def _new_stream_header(self, stream_type: str, weight: int) -> dict[str, object]:
        header: dict[str, object] = {
            "HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"
        }
        if stream_type == "FileHeader":
            header.update(
                {
                    "Weight": str(weight),
                    "MinorVersion": "13",
                    "UniqueID": self._file_unique_id or "AAAAAAAA",
                }
            )
        elif weight:
            header["Weight"] = str(weight)
        return header

    @staticmethod
    def _updated_stream_header(
        source: dict[str, object], stream_type: str, weight: int
    ) -> dict[str, object]:
        header = dict(source)
        weight_key = next((key for key in header if key.casefold() == "weight"), None)
        if weight_key is not None:
            header[weight_key] = str(weight)
        elif stream_type == "FileHeader" or weight:
            header["Weight"] = str(weight)
        return header

    @staticmethod
    def _stage_stream_records(objects: list[object]) -> tuple[bytes, ...]:
        """Serialize every physical record before Weight or header state changes."""
        from .altium_utilities import encode_altium_record

        staged: list[bytes] = []
        for index, obj in enumerate(objects):
            serialize_to_record = getattr(obj, "serialize_to_record", None)
            if callable(serialize_to_record):
                record_obj = serialize_to_record()
                if not isinstance(record_obj, dict):
                    raise TypeError(
                        f"record {index} serialize_to_record() did not return a dict"
                    )
                record = {str(key): value for key, value in record_obj.items()}
            elif isinstance(obj, dict):
                record = obj
            else:
                raise TypeError(f"record {index} has no supported serializer")
            record = AltiumSchDoc._collapse_equivalent_field_duplicates(record)
            if getattr(obj, "_harness_bundle_field_role", None) == "length":
                name_key = next(
                    (key for key in record if key.casefold() == "name"), "Name"
                )
                record[name_key] = "LengthParameter"
            if not isinstance(obj, dict):
                _as_dynamic(obj)._raw_record = dict(record)
            staged.append(encode_altium_record(record))
        return tuple(staged)

    @staticmethod
    def _collapse_equivalent_field_duplicates(
        record: dict[str, object],
    ) -> dict[str, object]:
        """Retain the first spelling of equal case-insensitive output fields."""
        result: dict[str, object] = {}
        seen: dict[str, object] = {}
        for key, value in record.items():
            folded = key.casefold()
            if folded not in seen:
                seen[folded] = value
                result[key] = value
                continue
            if seen[folded] != value:
                raise SchDocContainerError(
                    "duplicate", f"conflicting output field {key}"
                )
        return result

    def _build_additional_stream_objects(self) -> list[Any]:
        """
        Build the canonical Additional-stream object order from connector-owned state.
        """
        additional_objects: list[Any] = []
        top_level_additional = [
            obj
            for obj in self.all_objects
            if isinstance(obj, (AltiumSchHarnessConnector, AltiumSchSignalHarness))
        ]

        for obj in top_level_additional:
            self._mark_default_source_stream(obj)
            if isinstance(obj, AltiumSchSignalHarness):
                additional_objects.append(obj)
                continue

            connector_index = len(additional_objects)
            additional_objects.append(obj)

            for entry in list(getattr(obj, "entries", [])):
                self._prepare_harness_connector_child(obj, entry)
                entry.owner_index = connector_index if connector_index > 0 else 0
                entry.owner_index_additional_list = True
                additional_objects.append(entry)

            type_label = getattr(obj, "type_label", None)
            if type_label is not None:
                self._prepare_harness_connector_child(obj, type_label)
                type_label.owner_index = connector_index if connector_index > 0 else 0
                type_label.owner_index_additional_list = True
                type_label.not_auto_position = True
                additional_objects.append(type_label)

        return additional_objects

    def _build_additional_stream(self, debug: bool = False) -> bytes:
        """
        Build the Additional stream from current harness and signal-harness objects.
        """
        additional_objects = self._build_additional_stream_objects()
        if additional_objects:
            return self._build_stream_data(additional_objects, "Additional", debug)

        from .altium_utilities import encode_altium_record

        additional_header = {
            "HEADER": "Protel for Windows - Schematic Capture Binary File Version 5.0"
        }
        return encode_altium_record(additional_header)

    def _build_fileheader_stream(self, debug: bool = False) -> bytes:
        """
        Build the FileHeader stream from all_objects.

        Note: This method is used for round-trip mode where the Additional stream
        is preserved from the original file. Only objects originally from FileHeader
        should be included here.

        Returns:
            Bytes for FileHeader stream
        """
        # Exclude objects that came from Additional stream (they're preserved separately)
        fileheader_objects = [
            obj
            for obj in self.all_objects
            if getattr(obj, "_source_stream", "FileHeader") != "Additional"
        ]
        return self._build_stream_data(fileheader_objects, "FileHeader", debug)

    def _build_storage_stream(self, debug: bool = False) -> bytes:
        """
        Build the Storage stream from embedded_images.

        Uses raw storage entries if available (byte-perfect round-trip),
        otherwise falls back to recompressing image data.

        Storage stream format used by Altium schematic image storage:
        - Header record: 4-byte length + "|HEADER=Icon storage|Weight=N"
        - For each embedded object:
            - 4 bytes: binary header (record_size | 0x01000000)
            - 1 byte: 0xD0 (208) - BINARY marker
            - 1 byte: filename length
            - N bytes: filename (full path, UTF-8)
            - 4 bytes: compressed data length (uint32 LE)
            - M bytes: zlib-compressed image data

        Returns:
            Bytes for Storage stream
        """
        from .altium_utilities import create_storage_stream

        return create_storage_stream(
            self.embedded_images,
            raw_entries=self._raw_storage_entries,
        )

    def to_json(self, filepath: Path | str | None = None) -> dict:
        """
        Export SchDoc to JSON format for interoperability testing.

        Creates a JSON structure compatible with native AltiumInterop.JsonTests.
        Format:
            {
                "Header": {
                    "Filename": "schematic.SchDoc",
                    "ObjectCount": N,
                },
                "Sheet": {...},  # Sheet properties
                "Objects": [...]  # All records as JSON
            }

        Args:
            filepath: Optional path to write JSON file.
                     If provided, writes to file and returns dict.
                     If None, just returns the dict.

        Returns:
            JSON-serializable dict of the schematic structure
        """
        result: dict[str, object] = {
            "Header": {
                "Filename": self.filepath.name
                if self.filepath
                else self._json_filename,
                "ObjectCount": len(self.all_objects),
                "ComponentCount": len(self.components),
                "WireCount": len(self.wires),
                "BusCount": len(self.buses),
                "NetLabelCount": len(self.net_labels),
                "PowerPortCount": len(self.power_ports),
                "CrossSheetConnectorCount": len(self.cross_sheet_connectors),
                "JunctionCount": len(self.junctions),
                "PortCount": len(self.ports),
                "SheetSymbolCount": len(self.sheet_symbols),
                "ImageCount": len(self.images),
                "EmbeddedImageCount": (
                    len(self.embedded_images)
                    if self.embedded_images or self.filepath is not None
                    else self._json_embedded_image_count
                ),
            },
            "Objects": [],
        }

        # Add font table if present
        header = cast(dict[str, object], result["Header"])
        if self.sheet and self.font_manager.fonts:
            header["FontCount"] = len(self.font_manager.fonts)
            header["Fonts"] = _schdoc_json_fonts(self.font_manager.fonts)

        # Export all objects in order
        for i, obj in enumerate(self.all_objects):
            json_obj = _schdoc_json_object(obj, i)
            if json_obj:
                cast(list[dict[str, object]], result["Objects"]).append(json_obj)

        # Write to file if path provided
        if filepath is not None:
            output_path = Path(filepath)
            with output_path.open("w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
            log.info("Exported SchDoc to JSON: %s", output_path)

        return result

    def to_tagged_json(self, filepath: Path | str | None = None) -> dict[str, object]:
        """Export the explicit versioned SchDoc interoperability envelope."""
        from .altium_sch_interop_contract import SCHDOC_INTEROP_SCHEMA

        result: dict[str, object] = {
            "schema": SCHDOC_INTEROP_SCHEMA,
            "document": self.to_json(),
        }
        if filepath is not None:
            output_path = Path(filepath)
            with output_path.open("w", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
            log.info("Exported tagged SchDoc JSON: %s", output_path)
        return result

    @classmethod
    def from_json(
        cls,
        source: Path | str | dict,
    ) -> AltiumSchDoc:
        """
        Create a new SchDoc from JSON data.

                This is the public alternate-format ingest path. The returned document
                is reconstructed from the JSON payload without requiring a binary
                template file.
                To mutate an existing binary-backed document or template, instantiate
                ``AltiumSchDoc`` first and then call ``apply_json()``.

                Args:
                    source: JSON file path (Path or str) or parsed dict.

                Returns:
                    AltiumSchDoc instance with data loaded from JSON.
        """
        data = cls._load_json_source(source)
        instance = cls(create_sheet=False)
        instance._load_from_json_document(data)
        instance._validate_json_staged()
        return instance

    @staticmethod
    def _load_json_source(source: Path | str | dict) -> dict:
        """
        Load and validate a SchDoc JSON payload.
        """
        from .altium_sch_interop_contract import _unwrap_schdoc_interop_json

        data = load_bounded_json_source(source, limits=_SchDocReadLimits())
        data = _unwrap_schdoc_interop_json(data)
        unexpected = set(data).difference({"Header", "Objects"})
        if unexpected:
            raise ValueError(
                f"Invalid JSON format: unexpected root key {min(unexpected)!r}"
            )
        if "Header" not in data:
            raise ValueError("Invalid JSON format: missing 'Header' key")
        if "Objects" not in data:
            raise ValueError("Invalid JSON format: missing 'Objects' key")

        return data

    def _validate_json_staged(self) -> None:
        """Serialize and reopen the complete candidate before JSON commit."""
        validation = deepcopy(self)
        writer = (
            validation._stage_roundtrip_writer(debug=False)
            if validation._source_streams
            else validation._stage_new_writer(debug=False)
        )
        with tempfile.TemporaryDirectory(prefix="altium-monkey-json-") as directory:
            validation._write_staged_container(
                writer, Path(directory) / "candidate.SchDoc"
            )

    def _load_from_json_document(
        self, data: dict, *, embedded_image_count: int | None = None
    ) -> None:
        """
        Rebuild this SchDoc from JSON without requiring a binary template.
        """
        self._reset_json_document_state()
        header = data["Header"]
        self._apply_json_document_header(header)
        rows = json_object_rows(
            data["Objects"],
            context="Objects",
            max_rows=self._read_limits.max_total_records_per_document,
        )
        if embedded_image_count is None:
            self._reject_unrepresentable_scratch_images(header, rows)
        self._validate_json_root_rows(rows)
        self._validate_json_declared_count(header, "ObjectCount", len(rows))
        records = self._json_document_records(data["Objects"])
        self._parse_json_document_records(records)
        self._finish_json_document_rebuild()
        self._validate_json_document_header(
            header, embedded_image_count=embedded_image_count
        )

    def _reset_json_document_state(self) -> None:
        self.filepath = None
        self.sheet = None
        self._objects = ObjectCollection()
        self.embedded_images = {}
        self._raw_storage_entries = {}
        self.object_definitions = {}
        self._object_definition_records = []
        self._fileheader_objects = []
        self._additional_objects = []
        self._fileheader_raw_records = []
        self._additional_raw_records = []
        self._normalized_owner_refs = {}
        self._bounds_import_accessibility = {}
        self._font_manager = None

    def _apply_json_document_header(self, header: object) -> None:
        if not isinstance(header, Mapping):
            raise ValueError("SchDoc JSON Header must be an object")
        allowed = {
            "Filename",
            "ObjectCount",
            "ComponentCount",
            "WireCount",
            "BusCount",
            "NetLabelCount",
            "PowerPortCount",
            "CrossSheetConnectorCount",
            "JunctionCount",
            "PortCount",
            "SheetSymbolCount",
            "ImageCount",
            "EmbeddedImageCount",
            "FontCount",
            "Fonts",
            "UniqueID",
            "Weight",
        }
        unexpected = set(header).difference(allowed)
        if unexpected:
            raise ValueError(
                f"SchDoc JSON Header has unexpected field {min(unexpected)!r}"
            )
        filename = header.get("Filename")
        if not isinstance(filename, str) or not filename:
            raise ValueError("SchDoc JSON Header.Filename must be a non-empty string")
        self._json_filename = filename
        embedded_count = header.get("EmbeddedImageCount")
        if (
            isinstance(embedded_count, bool)
            or not isinstance(embedded_count, int)
            or embedded_count < 0
        ):
            raise ValueError(
                "SchDoc JSON Header.EmbeddedImageCount must be a non-negative integer"
            )
        self._json_embedded_image_count = embedded_count
        self._file_unique_id = None
        if "UniqueID" in header:
            unique_id = header["UniqueID"]
            if not isinstance(unique_id, str):
                raise ValueError("SchDoc JSON Header.UniqueID must be a string")
            self._file_unique_id = unique_id
        self._file_weight = None
        if "Weight" in header:
            weight_value = header["Weight"]
            if (
                isinstance(weight_value, bool)
                or not isinstance(weight_value, int)
                or not 0 <= weight_value <= 2**63 - 1
            ):
                raise ValueError(
                    "SchDoc JSON Header.Weight must be a bounded non-negative integer"
                )
            self._file_weight = weight_value

    @staticmethod
    def _reject_unrepresentable_scratch_images(
        header: Mapping[str, object],
        rows: tuple[Mapping[str, object], ...],
    ) -> None:
        if header.get("EmbeddedImageCount") != 0:
            raise ValueError("SchDoc scratch JSON cannot carry embedded image payloads")
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
                    f"Objects[{index}] embeds an image but scratch JSON has no payload"
                )

    @staticmethod
    def _validate_json_root_rows(
        rows: tuple[Mapping[str, object], ...],
    ) -> None:
        if any(
            row.get("ObjectType") == SchJsonObjectType.FILE_HEADER.value for row in rows
        ):
            raise ValueError(
                "SchDoc JSON Objects cannot contain FileHeader pseudo-rows"
            )
        sheet_count = sum(
            row.get("ObjectType") == SchJsonObjectType.SHEET.value for row in rows
        )
        if (
            sheet_count != 1
            or not rows
            or rows[0].get("ObjectType") != SchJsonObjectType.SHEET.value
        ):
            raise ValueError(
                "SchDoc JSON must contain exactly one Sheet object at index zero"
            )

    @staticmethod
    def _validate_json_declared_count(header: object, field: str, actual: int) -> None:
        if not isinstance(header, Mapping):
            raise ValueError("SchDoc JSON Header must be an object")
        if field not in header:
            raise ValueError(f"SchDoc JSON Header.{field} is required")
        declared = header[field]
        if (
            isinstance(declared, bool)
            or not isinstance(declared, int)
            or declared != actual
        ):
            raise ValueError(f"SchDoc JSON Header.{field} is inconsistent")

    def _validate_json_document_header(
        self, header: object, *, embedded_image_count: int | None
    ) -> None:
        if not isinstance(header, Mapping):
            raise ValueError("SchDoc JSON Header must be an object")
        counts = {
            "ComponentCount": len(self.components),
            "WireCount": len(self.wires),
            "BusCount": len(self.buses),
            "NetLabelCount": len(self.net_labels),
            "PowerPortCount": len(self.power_ports),
            "CrossSheetConnectorCount": len(self.cross_sheet_connectors),
            "JunctionCount": len(self.junctions),
            "PortCount": len(self.ports),
            "SheetSymbolCount": len(self.sheet_symbols),
            "ImageCount": len(self.images),
            "EmbeddedImageCount": (
                self._json_embedded_image_count
                if embedded_image_count is None
                else embedded_image_count
            ),
        }
        for field, actual in counts.items():
            self._validate_json_declared_count(header, field, actual)
        self._validate_json_fonts(header)

    def _validate_json_fonts(self, header: Mapping[str, object]) -> None:
        fonts_value = header.get("Fonts")
        count_value = header.get("FontCount")
        if fonts_value is None and count_value is None:
            if self.font_manager.fonts:
                raise ValueError("SchDoc JSON Header font table is missing")
            return
        font_rows = self._validated_json_font_rows(fonts_value, count_value)
        seen_ids: set[int] = set()
        for index, value in enumerate(font_rows):
            font_id, expected = self._validate_json_font_row(value, index)
            if font_id in seen_ids:
                raise ValueError("SchDoc JSON Header.Fonts has a duplicate FontID")
            self._validate_json_font_match(font_id, expected)
            seen_ids.add(font_id)

    def _validated_json_font_rows(
        self, fonts_value: object, count_value: object
    ) -> list[object]:
        if isinstance(count_value, bool) or not isinstance(count_value, int):
            raise ValueError("SchDoc JSON Header.FontCount must be an integer")
        if not isinstance(fonts_value, list):
            raise ValueError("SchDoc JSON Header.Fonts must be a list")
        if count_value != len(fonts_value):
            raise ValueError("SchDoc JSON Header.FontCount is inconsistent")
        if count_value != len(self.font_manager.fonts):
            raise ValueError("SchDoc JSON Header font table is inconsistent")
        return fonts_value

    @staticmethod
    def _validate_json_font_row(
        value: object, index: int
    ) -> tuple[int, dict[str, object]]:
        context = f"SchDoc JSON Header.Fonts[{index}]"
        if not isinstance(value, Mapping):
            raise ValueError(f"{context} must be an object")
        unexpected = set(value).difference(
            {"FontID", "FontName", "FontSize", "Bold", "Italic"}
        )
        if unexpected:
            raise ValueError(f"{context} has unexpected field {min(unexpected)!r}")
        font_id = value.get("FontID")
        font_name = value.get("FontName")
        font_size = value.get("FontSize")
        if isinstance(font_id, bool) or not isinstance(font_id, int):
            raise ValueError(f"{context}.FontID is invalid")
        if font_id != index + 1:
            raise ValueError("SchDoc JSON Header.Fonts must use dense FontID values")
        if not isinstance(font_name, str) or not font_name:
            raise ValueError(f"{context}.FontName is invalid")
        if isinstance(font_size, bool) or not isinstance(font_size, int):
            raise ValueError(f"{context}.FontSize is invalid")
        AltiumSchDoc._validate_optional_json_bool_fields(value, context)
        return font_id, {
            "name": font_name,
            "size": font_size,
            "bold": value.get("Bold", False),
            "italic": value.get("Italic", False),
        }

    def _validate_json_font_match(
        self, font_id: int, expected: dict[str, object]
    ) -> None:
        actual = self.font_manager.fonts.get(font_id)
        normalized_actual = None
        if actual is not None:
            normalized_actual = {
                "name": actual.get("name"),
                "size": actual.get("size"),
                "bold": actual.get("bold", False),
                "italic": actual.get("italic", False),
            }
        if normalized_actual != expected:
            raise ValueError("SchDoc JSON Header font table is inconsistent")

    @staticmethod
    def _validate_optional_json_bool_fields(
        value: Mapping[str, object], context: str
    ) -> None:
        for field in ("Bold", "Italic"):
            if field in value and not isinstance(value[field], bool):
                raise ValueError(f"{context}.{field} must be a boolean")

    def _json_document_records(self, value: object) -> list[dict[str, object]]:
        rows = json_object_rows(
            value,
            context="Objects",
            max_rows=self._read_limits.max_total_records_per_document,
        )
        records: list[dict[str, Any]] = []
        blob_budget = JsonBlobBudget(
            self._read_limits.max_total_decompressed_blob_bytes
        )
        for index, json_obj in enumerate(rows):
            records.append(
                json_record_from_object(
                    json_obj,
                    context=f"Objects[{index}]",
                    max_binary_bytes=self._read_limits.max_record_bytes,
                    blob_budget=blob_budget,
                )
            )
        return records

    def _parse_json_document_records(self, records: list[dict[str, object]]) -> None:
        additional_types = {
            SchRecordType.HARNESS_CONNECTOR.value,
            SchRecordType.HARNESS_ENTRY.value,
            SchRecordType.HARNESS_TYPE.value,
            SchRecordType.SIGNAL_HARNESS.value,
            SchRecordType.BLANKET.value,
        }
        first_additional = next(
            (
                index
                for index, record in enumerate(records)
                if self._json_record_number(record) in additional_types
            ),
            len(records),
        )
        if any(
            self._json_record_number(record) not in additional_types
            for record in records[first_additional:]
        ):
            raise ValueError(
                "SchDoc JSON Additional-stream records must form a final block"
            )
        self._parse_records(records[:first_additional], source_stream="FileHeader")
        if first_additional < len(records):
            self._parse_records(records[first_additional:], source_stream="Additional")

    @staticmethod
    def _json_record_number(record: Mapping[str, object]) -> int:
        value = record.get("RECORD")
        if isinstance(value, bool) or not isinstance(value, int | str):
            raise ValueError("SchDoc JSON object has an invalid record type")
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError("SchDoc JSON object has an invalid record type") from exc

    def _finish_json_document_rebuild(self) -> None:
        if len(self._object_view(lambda obj: isinstance(obj, AltiumSchSheet))) != 1:
            raise ValueError("SchDoc JSON must contain exactly one Sheet object")
        self._rebuild_json_runtime_context()
        self._bind_all_objects_to_context()
        self._lock_loaded_object_identities()
        self._preserve_loaded_index_in_sheet = True

    def _rebuild_json_runtime_context(self) -> None:
        for obj in self.all_objects:
            if hasattr(obj, "parent"):
                _as_dynamic(obj).parent = None
        self._refresh_json_raw_record_context()
        self._validate_and_assign_owner_refs()
        self._capture_base_bounds_accessibility()
        self._build_component_hierarchy()
        self._build_parameterset_hierarchy()
        self._build_implementation_hierarchy()
        self._build_harness_and_sheet_hierarchy()
        self._link_embedded_images()
        self._set_all_parent_references()
        self._hydrate_harness_physical_models()

    def _update_from_json(self, data: dict) -> None:
        """
        Build and validate a complete replacement document from JSON.
        """
        rows = json_object_rows(
            data.get("Objects"),
            context="Objects",
            max_rows=self._read_limits.max_total_records_per_document,
        )
        if len(rows) != len(self.all_objects):
            raise ValueError(
                f"SchDoc JSON object count {len(rows)} does not match "
                f"template count {len(self.all_objects)}"
            )

        rebuilt = type(self)(create_sheet=False)
        rebuilt._read_limits = self._read_limits
        rebuilt._load_from_json_document(
            data, embedded_image_count=len(self.embedded_images)
        )
        if len(rebuilt.all_objects) != len(self.all_objects):
            raise ValueError("SchDoc JSON changes the template object inventory")
        self._validate_json_object_type_order(rebuilt)
        header = data["Header"]
        if not isinstance(header, Mapping):
            raise ValueError("SchDoc JSON Header must be an object")
        if "UniqueID" in header:
            self._file_unique_id = rebuilt._file_unique_id
        if "Weight" in header:
            self._file_weight = rebuilt._file_weight
        self._json_filename = rebuilt._json_filename
        self._apply_json_object_rows(rows)
        self._preserve_loaded_index_in_sheet = True
        self._rebuild_json_runtime_context()

    def _validate_json_object_type_order(self, rebuilt: AltiumSchDoc) -> None:
        for index, (current, replacement) in enumerate(
            zip(self.all_objects, rebuilt.all_objects, strict=True)
        ):
            if type(current) is not type(replacement):
                raise ValueError(f"SchDoc JSON changes object type at index {index}")

    def _apply_json_object_rows(self, rows: tuple[Mapping[str, object], ...]) -> None:
        blob_budget = JsonBlobBudget(
            self._read_limits.max_total_decompressed_blob_bytes
        )
        for index, (obj, row) in enumerate(zip(self.all_objects, rows, strict=True)):
            record = json_record_from_object(
                row,
                context=f"Objects[{index}]",
                max_binary_bytes=self._read_limits.max_record_bytes,
                blob_budget=blob_budget,
            )
            parser = getattr(obj, "parse_from_record", None)
            if not callable(parser):
                raise ValueError(f"SchDoc object at index {index} is not JSON mutable")
            parser(record, font_manager=self.font_manager)

    def _refresh_json_raw_record_context(self) -> None:
        self._fileheader_objects = self._objects_for_stream("FileHeader")
        self._additional_objects = self._objects_for_stream("Additional")
        self._fileheader_raw_records = self._raw_records_for_objects(
            self._fileheader_objects
        )
        self._additional_raw_records = self._raw_records_for_objects(
            self._additional_objects
        )
        self._link_embedded_images()

    def _commit_json_update(self, staged: object) -> None:
        if not isinstance(staged, AltiumSchDoc):
            raise TypeError("staged JSON update must be an AltiumSchDoc")
        self._adopt_json_state(staged)

    def _adopt_json_state(self, staged: AltiumSchDoc) -> None:
        old_objects = list(self._objects)
        staged_objects = list(staged._objects)
        for obj in old_objects:
            self._set_managed_unique_id_lock(obj, False)
            if hasattr(obj, "_bound_schematic_context"):
                _as_dynamic(obj)._bound_schematic_context = None
        for name, value in staged.__dict__.items():
            if name not in {"_objects", "_schematic_binding_context"}:
                setattr(self, name, value)
        self._objects.clear()
        self._objects.extend(staged_objects)
        self._schematic_binding_context = SchematicBindingContext(self, kind="schdoc")
        self._bind_all_objects_to_context()
        self._lock_loaded_object_identities()

    def _json_object_type_to_record_num(self, object_type: str) -> int:
        """
        Convert ObjectType string back to RECORD number.
        """
        record_type = sch_record_type_from_json_object_type(object_type)
        if record_type is None:
            return 0
        return record_type.value

    def extract_symbols(
        self,
        output_dir: Path,
        combined_schlib: bool = False,
        split_schlibs: bool = True,
        debug: bool = False,
        strip_parameters: bool = True,
        strip_implementations: bool = True,
    ) -> dict[str, bool]:
        """
        Extract symbols from placed components in this schematic.

        This implements Altium's "Make SchLib" functionality - extracting the
        embedded symbol definitions from placed component instances.

        Args:
            output_dir: Directory to save extracted SchLib files
            combined_schlib: If True, create a single multi-symbol SchLib file
                            named after the schematic (e.g., myschematic.SchLib)
            split_schlibs: If True, create individual SchLib files for each symbol
            debug: Enable debug output
            strip_parameters: If True (default), omit component PARAMETER records
                              from the extracted symbols. Use False when the
                              extracted SchLib should preserve source metadata.
            strip_implementations: If True (default), omit implementation/model
                                   records from the extracted symbols. Use False
                                   when the extracted SchLib should preserve
                                   footprint/model links.

        Returns:
            Dict mapping symbol name -> success status

        Notes:
            - Uses DesignItemId (actual part number) over LibReference (symbol name)
              for database library components
            - Emits one symbol per DesignItemId/LibReference. Multiple placed
              components with the same identifier share one extracted symbol even
              if raw Comment expressions, source library metadata, or extra
              parameters differ. Altium's interactive exporter may optionally emit
              suffixed duplicate variants for those cases; this API currently
              treats them as one functional symbol.
            - Handles coordinate translation and rotation un-rotation
            - Preserves multipart symbol structure (PartCount)
            - Embedded images from symbols are extracted to the SchLib files
        """
        from .altium_schdoc_symbol_extractor import write_extracted_symbols

        if self.filepath is None:
            raise ValueError("Cannot extract symbols: SchDoc has no filepath")
        log.info(f"Extracting symbols from: {self.filepath.name}")
        return write_extracted_symbols(
            self,
            Path(output_dir),
            combined_schlib=combined_schlib,
            split_schlibs=split_schlibs,
            debug=debug,
            strip_parameters=strip_parameters,
            strip_implementations=strip_implementations,
        )

    def extract_schlib(
        self,
        debug: bool = False,
        strip_parameters: bool = True,
        strip_implementations: bool = True,
    ) -> "AltiumSchLib":
        """
        Extract placed component symbols into an in-memory SchLib.

        This is the non-writing counterpart to `extract_symbols(...)`.
        """
        from .altium_schdoc_symbol_extractor import extract_schlib_from_schdoc

        return extract_schlib_from_schdoc(
            self,
            debug=debug,
            strip_parameters=strip_parameters,
            strip_implementations=strip_implementations,
        )

    def _symbol_asset_summaries(self) -> tuple[AltiumAssetSummary, ...]:
        """
        Return extractable SchDoc symbol summaries in extractor grouping order.
        """
        from .altium_schdoc_symbol_extractor import (
            _group_components_by_symbol,
            _get_actual_part_count,
            _sanitize_filename,
            _select_best_instance,
            _symbol_display_name,
            _symbol_storage_name,
        )

        source_path = str(self.filepath) if self.filepath is not None else None
        source_instance_id = source_instance_id_for(self, source_path)
        summaries: list[AltiumAssetSummary] = []
        for index, (symbol_key, instances) in enumerate(
            _group_components_by_symbol(self.components).items()
        ):
            template = _select_best_instance(instances)
            display_name = _symbol_display_name(symbol_key, template)
            storage_name = _symbol_storage_name(display_name)
            safe_name = _sanitize_filename(storage_name)
            kind = "sch_symbol"
            summaries.append(
                AltiumAssetSummary(
                    ref=AltiumAssetRef(
                        source_kind="schdoc",
                        source_path=source_path,
                        kind=kind,
                        key=semantic_asset_key(kind, symbol_key),
                        index=index,
                        name=symbol_key,
                        source_instance_id=source_instance_id,
                    ),
                    kind=kind,
                    name=symbol_key,
                    extraction_filename=f"{safe_name}.SchLib",
                    native_extension="SchLib",
                    can_extract=True,
                    payload_available=False,
                    details=SchSymbolAssetDetails(
                        display_name=display_name,
                        safe_name=safe_name,
                        component_count=len(instances),
                        component_designators=tuple(
                            self._component_designator_text(comp) for comp in instances
                        ),
                        selected_designator=self._component_designator_text(template),
                        lib_reference=str(getattr(template, "lib_reference", "") or ""),
                        design_item_id=str(
                            getattr(template, "design_item_id", "") or ""
                        ),
                        original_name=symbol_key,
                        pin_count=len(getattr(template, "pins", []) or []),
                        object_count=self._component_symbol_inventory_object_count(
                            template
                        ),
                        part_count=_get_actual_part_count(template),
                    ),
                )
            )
        return tuple(summaries)

    @staticmethod
    def _component_symbol_inventory_object_count(component: object) -> int:
        """
        Return the default extracted-symbol object count for inventory display.

        The default symbol extractor strips ordinary parameters and
        implementation/model records but retains pins, designators, and
        geometry. Match that public extraction profile so the inventory count
        is stable across Python and C++.
        """
        count = 1
        for child in getattr(component, "children", []) or []:
            if isinstance(child, AltiumSchParameter) and not isinstance(
                child, AltiumSchDesignator
            ):
                continue
            if isinstance(
                child,
                (
                    AltiumSchImplementationList,
                    AltiumSchImplementation,
                    AltiumSchMapDefiner,
                    AltiumSchMapDefinerList,
                    AltiumSchImplParams,
                ),
            ):
                continue
            count += 1
        return count

    @staticmethod
    def _component_designator_text(component: object) -> str:
        finder = getattr(component, "_find_designator_record", None)
        if callable(finder):
            designator = finder()
            if designator is not None:
                return str(getattr(designator, "text", "") or "")
        return str(getattr(component, "designator", "") or "")

    def asset_inventory(self, *, include_hashes: bool = False) -> AltiumAssetInventory:
        """
        Return extractable asset inventory for this SchDoc.
        """
        _ = include_hashes
        source_path = str(self.filepath) if self.filepath is not None else None
        return AltiumAssetInventory(
            source_kind="schdoc",
            source_path=source_path,
            assets=self._symbol_asset_summaries(),
        )

    def extract_symbol(
        self,
        ref_or_name_or_index: object,
        *,
        strip_parameters: bool = True,
        strip_implementations: bool = True,
        debug: bool = False,
    ) -> "AltiumSchLib":
        """
        Extract one placed SchDoc symbol as a single-symbol SchLib.
        """
        from .altium_schdoc_symbol_extractor import (
            _create_schlib_symbol_from_template,
            _first_instance_current_part_id,
            _group_components_by_symbol,
            _select_best_instance,
        )

        groups = list(_group_components_by_symbol(self.components).items())
        summaries = self._symbol_asset_summaries()
        index = selected_asset_index(
            ref_or_name_or_index,
            summaries=summaries,
            expected_source_kind="schdoc",
            expected_kind="sch_symbol",
        )
        symbol_key, instances = groups[index]
        template = _select_best_instance(instances)
        schlib, _safe_name, _symbol = _create_schlib_symbol_from_template(
            symbol_key,
            template,
            self,
            current_part_id=_first_instance_current_part_id(instances),
            strip_parameters=strip_parameters,
            strip_implementations=strip_implementations,
            debug=debug,
        )
        return schlib

    def extract_asset(self, ref: AltiumAssetRef) -> AltiumExtractedAsset:
        """
        Extract one asset selected from `asset_inventory()`.
        """
        if ref.source_kind != "schdoc":
            raise ValueError(
                f"asset reference source mismatch: expected schdoc, got {ref.source_kind}"
            )
        if ref.kind != "sch_symbol":
            raise ValueError(f"unsupported SchDoc extractable asset kind: {ref.kind}")

        summaries = self._symbol_asset_summaries()
        index = selected_asset_index(
            ref,
            summaries=summaries,
            expected_source_kind="schdoc",
            expected_kind="sch_symbol",
        )
        return AltiumExtractedAsset(
            ref=ref,
            filename=summaries[index].extraction_filename
            or f"symbol_{index:03d}.SchLib",
            schlib=self.extract_symbol(ref),
        )

    # Clean SchDoc API - user-friendly access methods

    # Component access

    def get_components(self) -> list[SchComponentInfo]:
        """
        Get all components with resolved children.

                Returns:
                    List of SchComponentInfo wrappers with designator, pins,
                    parameters, and footprint pre-resolved.
        """
        return [SchComponentInfo(record=comp) for comp in self.components]

    def get_component(self, designator: str) -> SchComponentInfo | None:
        """
        Find component by designator.

                Args:
                    designator: Component designator (e.g., "U1", "R1")

                Returns:
                    SchComponentInfo if found, None otherwise.
        """
        for comp in self.get_components():
            if comp.designator == designator:
                return comp
        return None

    # Pin access

    def get_all_pins(self) -> list[SchPinInfo]:
        """
        Get all pins across all components with component context.

                Returns:
                    List of SchPinInfo with pin data and parent component context.
        """
        result = []
        for comp_info in self.get_components():
            for pin in comp_info.pins:
                result.append(SchPinInfo(pin=pin, component=comp_info))
        return result

    def get_pins_for_component(self, designator: str) -> list[AltiumSchPin]:
        """
        Get pins for a specific component.

                Args:
                    designator: Component designator

                Returns:
                    List of AltiumSchPin records, empty if component not found.
        """
        comp = self.get_component(designator)
        return comp.pins if comp else []

    # Connectivity objects

    def get_wires(self) -> list[AltiumSchWire]:
        """
        Get all wire segments.
        """
        return list(self.wires)

    def get_net_labels(self) -> list[SchNetLabelInfo]:
        """
        Get all net labels with connection points.
        """
        return [SchNetLabelInfo(record=nl) for nl in self.net_labels]

    def get_power_ports(self) -> list[SchPowerPortInfo]:
        """
        Get all power port symbols with connection points.
        """
        return [SchPowerPortInfo(record=pp) for pp in self.power_ports]

    def get_cross_sheet_connectors(self) -> list[SchCrossSheetConnectorInfo]:
        """
        Get all off-sheet connectors with connection points.
        """
        return [
            SchCrossSheetConnectorInfo(record=connector)
            for connector in self.cross_sheet_connectors
        ]

    def get_ports(self) -> list[SchPortInfo]:
        """
        Get all sheet ports with connection points.
        """
        return [SchPortInfo(record=p) for p in self.ports]

    def get_junctions(self) -> list[AltiumSchJunction]:
        """
        Get all junction points.
        """
        return list(self.junctions)

    def get_buses(self) -> list[AltiumSchBus]:
        """
        Get all buses.
        """
        return list(self.buses)

    # Harness objects

    def get_harness_connectors(self) -> list[SchHarnessInfo]:
        """
        Get all harness connectors with their entries.
        """
        result = []
        for hc in self.harness_connectors:
            info = SchHarnessInfo(record=hc, entries=list(getattr(hc, "entries", [])))
            result.append(info)
        return result

    def get_signal_harnesses(self) -> list[AltiumSchSignalHarness]:
        """
        Get all signal harness wires.
        """
        return list(self.signal_harnesses)

    # -----------------------------------------------------------------
    # Hierarchy Objects
    # -----------------------------------------------------------------

    def get_sheet_symbols(self) -> list[SchSheetSymbolInfo]:
        """
        Get all sheet symbols with resolved entries.
        """
        result = []
        for ss in self.sheet_symbols:
            info = SchSheetSymbolInfo(
                record=ss, entries=list(getattr(ss, "entries", []))
            )
            result.append(info)
        return result

    # -----------------------------------------------------------------
    # Graphics Objects
    # -----------------------------------------------------------------

    def get_labels(self) -> list[AltiumSchLabel]:
        """
        Get all text labels (sheet-level only).
        """
        return list(self.labels)

    def get_rectangles(self) -> list[AltiumSchRectangle]:
        """
        Get all rectangles (sheet-level).
        """
        return [obj for obj in self.graphics if isinstance(obj, AltiumSchRectangle)]

    def get_lines(self) -> list[AltiumSchLine]:
        """
        Get all lines (sheet-level).
        """
        return [obj for obj in self.graphics if isinstance(obj, AltiumSchLine)]

    def __repr__(self) -> str:
        return f"<AltiumSchDoc {self.filepath.name if self.filepath else 'Unknown'}: {len(self.all_objects)} objects>"
