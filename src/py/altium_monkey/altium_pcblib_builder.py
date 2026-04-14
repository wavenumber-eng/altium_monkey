"""
Dedicated PcbLib builder for programmatic library creation.

This path is intentionally separate from any PcbDoc-derived writer logic.
The default builder profile is code-owned and template-free at runtime, while
all output streams are constructed by dedicated builder code.
"""

from __future__ import annotations

import copy
import re
import struct
import uuid
import zlib
from datetime import datetime
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .altium_pcblib_defaults import DEFAULT_PCBLIB_FILE_HEADER_MAGIC
from .altium_pcblib_defaults import DEFAULT_PCBLIB_PAD_VIA_LIBRARY_GUID
from .altium_pcblib_defaults import build_default_pcblib_library_data_segments
from .altium_pcb_stream_helpers import (
    build_length_prefixed_ascii as _build_length_prefixed_ascii,
)
from .altium_pcb_stream_helpers import (
    count_length_prefixed_records as _count_length_prefixed_records,
)
from .altium_pcb_stream_helpers import format_bool_text as _format_bool_text
from .altium_pcb_stream_helpers import PcbKeyValueTextEntryMixin
from .altium_pcblib_sections import PcbLibComponentParamsToc
from .altium_pcblib_sections import PcbLibComponentParamsTocEntry
from .altium_pcblib_sections import PcbLibCountHeader
from .altium_pcblib_sections import PcbLibFileHeader
from .altium_pcblib_sections import PcbLibFileVersionInfo
from .altium_pcblib_sections import PcbLibLayerKindMapping
from .altium_pcblib_sections import PcbLibPadViaLibrary
from .altium_pcblib_sections import PcbLibSectionKeyEntry
from .altium_pcblib_sections import PcbLibSectionKeys
from .altium_ole import AltiumOleFile
from .altium_pcb_custom_shapes import (
    attach_custom_pad_shape,
    build_pcblib_custom_pad_extended_info,
    build_pcblib_custom_pad_region_properties,
)
from .altium_pcb_model_checksum import compute_altium_model_checksum
from .altium_pcblib import (
    AltiumPcbFootprint,
    AltiumPcbLib,
    _altium_ole_truncate,
    _sanitize_ole_name,
)
from .altium_pcb_pad_authoring import (
    SLOT_HOLE_SHAPE,
    apply_authored_pad_shape,
    validate_non_negative,
)
from .altium_resolved_layer_stack import legacy_layer_to_v7_save_id
from .altium_record_pcb__arc import AltiumPcbArc
from .altium_pcb_enums import PcbBodyProjection
from .altium_pcb_enums import PcbRegionKind
from .altium_record_pcb__component_body import AltiumPcbComponentBody
from .altium_record_pcb__fill import AltiumPcbFill
from .altium_record_pcb__model import AltiumPcbModel
from .altium_pcb_enums import PadShape
from .altium_record_pcb__pad import AltiumPcbPad
from .altium_record_pcb__region import AltiumPcbRegion, RegionVertex
from .altium_record_pcb__shapebased_region import (
    AltiumPcbShapeBasedRegion,
    PcbExtendedVertex,
)
from .altium_record_pcb__text import AltiumPcbText
from .altium_record_pcb__track import AltiumPcbTrack
from .altium_record_pcb__via import AltiumPcbVia
from .altium_record_types import PcbLayer, generate_unique_id

_PAD_SUBRECORD2_DEFAULT = b"\x00"
_PAD_SUBRECORD3_DEFAULT = b"\x04|&|0"
_PAD_SUBRECORD4_DEFAULT = b"\x00"


def _build_library_data(header_bytes: bytes, footprint_names: list[str]) -> bytes:
    buf = bytearray()
    buf.extend(struct.pack("<I", len(header_bytes)))
    buf.extend(header_bytes)
    buf.extend(struct.pack("<I", len(footprint_names)))
    for name in footprint_names:
        name_bytes = name.encode("latin-1")
        subrecord = bytes([len(name_bytes)]) + name_bytes
        buf.extend(struct.pack("<I", len(subrecord)))
        buf.extend(subrecord)
    return bytes(buf)


def _build_footprint_parameters(spec: "PcbLibFootprintSpec") -> bytes:
    body = (
        f"|PATTERN={spec.footprint.name}"
        f"|HEIGHT={spec.height}"
        f"|DESCRIPTION={spec.description}"
        f"|ITEMGUID={spec.item_guid}"
        f"|REVISIONGUID={spec.revision_guid}\x00"
    )
    return _build_length_prefixed_ascii(body)


def _build_footprint_widestrings(strings: dict[int, str] | None = None) -> bytes:
    if not strings:
        return _build_length_prefixed_ascii("\x00")

    parts = []
    for index, text in sorted(strings.items()):
        csv = ",".join(str(ord(ch)) for ch in text)
        parts.append(f"ENCODEDTEXT{index}={csv}")
    return _build_length_prefixed_ascii("|" + "|".join(parts) + "\x00")


def _build_primitive_guid_record(type_id: int, index: int, guid: uuid.UUID) -> bytes:
    return struct.pack("<II", type_id, index) + guid.bytes_le


def _strip_record_terminator(value: str | None) -> str | None:
    if value is None:
        return None
    return value.rstrip("\r")


@dataclass(frozen=True)
class PcbLibLibraryDataSegment(PcbKeyValueTextEntryMixin):
    raw: str


@dataclass(frozen=True)
class PcbLibLibraryDataRecord:
    record_type: str
    segments: tuple[PcbLibLibraryDataSegment, ...]
    start_index: int
    end_index: int

    @property
    def property_segments(self) -> tuple[PcbLibLibraryDataSegment, ...]:
        return self.segments[1:]

    def get_value(
        self, key: str, default: str | None = None, occurrence: int = 0
    ) -> str | None:
        seen = 0
        for segment in self.property_segments:
            if segment.key != key:
                continue
            if seen == occurrence:
                return segment.value
            seen += 1
        return default

    def with_updated_value(
        self, key: str, value: str, occurrence: int = 0
    ) -> "PcbLibLibraryDataRecord":
        seen = 0
        updated = False
        new_segments: list[PcbLibLibraryDataSegment] = []
        for segment in self.segments:
            if segment.key == key and seen == occurrence:
                new_segments.append(PcbLibLibraryDataSegment(raw=f"{key}={value}"))
                updated = True
            else:
                new_segments.append(segment)
            if segment.key == key:
                seen += 1

        if not updated:
            raise KeyError(f"Record key not found: {key}")

        return PcbLibLibraryDataRecord(
            record_type=self.record_type,
            segments=tuple(new_segments),
            start_index=self.start_index,
            end_index=self.end_index,
        )


@dataclass(frozen=True)
class PcbLibNestedConfig:
    segments: tuple[PcbLibLibraryDataSegment, ...]
    leading_backtick: bool = True

    @classmethod
    def from_value(cls, value: str) -> "PcbLibNestedConfig":
        leading_backtick = value.startswith("`")
        if leading_backtick:
            value = value[1:]
        return cls(
            segments=tuple(
                PcbLibLibraryDataSegment(raw=part) for part in value.split("`")
            ),
            leading_backtick=leading_backtick,
        )

    def serialize(self) -> str:
        text = "`".join(segment.raw for segment in self.segments)
        if self.leading_backtick:
            text = "`" + text
        return text

    def get_value(
        self, key: str, default: str | None = None, occurrence: int = 0
    ) -> str | None:
        seen = 0
        for segment in self.segments:
            if segment.key != key:
                continue
            if seen == occurrence:
                return segment.value
            seen += 1
        return default

    def with_updated_value(
        self, key: str, value: str, occurrence: int = 0
    ) -> "PcbLibNestedConfig":
        seen = 0
        updated = False
        new_segments: list[PcbLibLibraryDataSegment] = []
        for segment in self.segments:
            if segment.key == key and seen == occurrence:
                new_segments.append(PcbLibLibraryDataSegment(raw=f"{key}={value}"))
                updated = True
            else:
                new_segments.append(segment)
            if segment.key == key:
                seen += 1

        if not updated:
            raise KeyError(f"Nested config key not found: {key}")

        return PcbLibNestedConfig(
            segments=tuple(new_segments),
            leading_backtick=self.leading_backtick,
        )


@dataclass(frozen=True)
class PcbLibLibraryMetadata:
    filename: str | None = None
    kind: str | None = None
    version: str | None = None
    date: str | None = None
    time: str | None = None


class PcbLibViewState(StrEnum):
    TWO_D = "2D"
    THREE_D = "3D"

    @classmethod
    def from_text(cls, value: str | None) -> "PcbLibViewState | None":
        if value is None:
            return None
        normalized = _strip_record_terminator(value)
        if normalized is None:
            return None
        try:
            return cls(normalized.upper())
        except ValueError:
            return None


@dataclass(frozen=True)
class PcbLibViewport:
    low_x: str
    high_x: str
    low_y: str
    high_y: str


@dataclass(frozen=True)
class PcbLibConfigurationBlock:
    nested_config: PcbLibNestedConfig
    configuration_kind: str | None = None
    configuration_description: str | None = None

    @classmethod
    def from_nested_config(
        cls,
        nested_config: PcbLibNestedConfig,
    ) -> "PcbLibConfigurationBlock":
        return cls(
            nested_config=nested_config,
            configuration_kind=nested_config.get_value("CFGALL.CONFIGURATIONKIND"),
            configuration_description=nested_config.get_value(
                "CFGALL.CONFIGURATIONDESC"
            ),
        )

    def to_nested_config(self) -> PcbLibNestedConfig:
        updated = self.nested_config
        if self.configuration_kind is not None:
            updated = updated.with_updated_value(
                "CFGALL.CONFIGURATIONKIND",
                self.configuration_kind,
            )
        if self.configuration_description is not None:
            updated = updated.with_updated_value(
                "CFGALL.CONFIGURATIONDESC",
                self.configuration_description,
            )
        return updated


@dataclass(frozen=True)
class PcbLibViewConfiguration:
    config_type: str | None
    full_filename: str | None
    configuration: PcbLibConfigurationBlock | None


def _parse_bool_text(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = _strip_record_terminator(value)
    if normalized is None:
        return None
    return normalized.upper() == "TRUE"


def _parse_int_text(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = _strip_record_terminator(value)
    if normalized in (None, ""):
        return None
    return int(normalized)


def _parse_float_text(value: str | None) -> float | None:
    if value is None:
        return None
    normalized = _strip_record_terminator(value)
    if normalized in (None, ""):
        return None
    return float(normalized)


def _format_fixed_float(value: float, places: int = 6) -> str:
    return f"{value:.{places}f}"


def _parse_bool_mask_text(value: str | None) -> tuple[bool, ...] | None:
    if value is None:
        return None
    normalized = _strip_record_terminator(value)
    if normalized in (None, ""):
        return None
    return tuple(ch == "1" for ch in normalized)


def _format_bool_mask_text(values: tuple[bool, ...]) -> str:
    return "".join("1" if value else "0" for value in values)


def _parse_float_series_text(
    value: str | None,
    *,
    delimiter: str = "?",
) -> tuple[float, ...] | None:
    if value is None:
        return None
    normalized = _strip_record_terminator(value)
    if normalized in (None, ""):
        return None
    parts = [part for part in normalized.split(delimiter) if part != ""]
    return tuple(float(part) for part in parts)


def _format_float_series_text(
    values: tuple[float, ...],
    *,
    delimiter: str = "?",
    places: int = 2,
) -> str:
    if not values:
        return ""
    return delimiter.join(f"{value:.{places}f}" for value in values) + delimiter


@dataclass(frozen=True)
class PcbLibGridSettings:
    big_visible_grid_size: float
    visible_grid_size: float
    snap_grid_size: float
    snap_grid_size_x: float
    snap_grid_size_y: float
    electrical_grid_range: str
    electrical_grid_enabled: bool
    dot_grid: bool
    dot_grid_large: bool
    display_unit: int


@dataclass(frozen=True)
class PcbLibCameraSettings:
    look_at_x: float
    look_at_y: float
    look_at_z: float
    eye_rotation_x: float
    eye_rotation_y: float
    eye_rotation_z: float
    zoom_multiplier: float
    view_size_x: int
    view_size_y: int
    electrical_grid_range: str
    electrical_grid_multiplier: float
    electrical_grid_enabled: bool
    electrical_grid_snap_to_board_outline: bool
    electrical_grid_snap_to_arc_centers: bool
    electrical_grid_use_all_layers: bool
    object_guide_snap_enabled: bool
    midpoint_guide_snap_enabled: bool
    point_guide_enabled: bool
    grid_snap_enabled: bool
    near_objects_enabled: bool
    far_objects_enabled: bool


@dataclass(frozen=True)
class PcbLib2DViewSettings:
    current_layer: str | None = None
    display_special_strings: bool | None = None
    show_test_points: bool | None = None
    show_origin_marker: bool | None = None
    eye_distance: int | None = None
    show_status_info: bool | None = None
    show_pad_nets: bool | None = None
    show_pad_numbers: bool | None = None
    show_via_nets: bool | None = None
    show_via_span: bool | None = None
    use_transparent_layers: bool | None = None
    plane_draw_mode: int | None = None
    single_layer_mode_state: int | None = None


@dataclass(frozen=True)
class PcbLib3DViewSettings:
    show_component_bodies: bool | None = None
    show_component_step_models: bool | None = None
    component_model_preference: int | None = None
    show_component_axes: bool | None = None
    show_board_core: bool | None = None
    show_board_prepreg: bool | None = None
    show_top_silkscreen: bool | None = None
    show_bottom_silkscreen: bool | None = None
    show_origin_marker: bool | None = None
    eye_distance: int | None = None
    show_cutouts: bool | None = None
    show_route_tool_path: bool | None = None
    show_rooms_3d: bool | None = None
    use_system_colors: bool | None = None


@dataclass(frozen=True)
class PcbLibLayerOpacityEntry:
    layer_name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class PcbLibLayerOpacityTable:
    entries: tuple[PcbLibLayerOpacityEntry, ...]

    def entry(self, layer_name: str) -> PcbLibLayerOpacityEntry | None:
        return next(
            (entry for entry in self.entries if entry.layer_name == layer_name), None
        )


@dataclass(frozen=True)
class PcbLibToggleLayerSettings:
    toggle_layers: tuple[bool, ...]
    toggle_layers_set: str | None
    all_connections_in_single_layer_mode: bool | None
    mechanical_layers_in_single_layer_mode: tuple[bool, ...]
    mechanical_layers_in_single_layer_mode_set: str | None
    mechanical_layers_linked_to_sheet: tuple[bool, ...]
    mechanical_layers_linked_to_sheet_set: str | None
    mechanical_cover_layer_updated: bool | None


@dataclass(frozen=True)
class PcbLibLayerSet:
    index: int
    name: str
    layers: tuple[str, ...]
    active_layer: str
    is_current: bool
    is_locked: bool
    flip_board: bool


@dataclass(frozen=True)
class PcbLibLayerSets:
    sets: tuple[PcbLibLayerSet, ...]

    def layer_set(self, index: int) -> PcbLibLayerSet | None:
        return next((entry for entry in self.sets if entry.index == index), None)


_LAYER_ENTRY_FIELDS = (
    "NAME",
    "PREV",
    "NEXT",
    "MECHENABLED",
    "COPTHICK",
    "DIELTYPE",
    "DIELCONST",
    "DIELHEIGHT",
    "DIELMATERIAL",
)
_LEGACY_LAYER_KEY_RE = re.compile(
    r"^LAYER(\d+)(NAME|PREV|NEXT|MECHENABLED|COPTHICK|DIELTYPE|DIELCONST|DIELHEIGHT|DIELMATERIAL)$"
)
_V7_LAYER_KEY_RE = re.compile(
    r"^LAYERV7_(\d+)(LAYERID|NAME|PREV|NEXT|MECHENABLED|COPTHICK|DIELTYPE|DIELCONST|DIELHEIGHT|DIELMATERIAL)$"
)
_LAYER_OPACITY_KEY_RE = re.compile(r"^CFG2D\.LAYEROPACITY\.(.+)$")
_LAYER_SET_KEY_RE = re.compile(
    r"^LAYERSET(\d+)(NAME|LAYERS|ACTIVELAYER\.7|ISCURRENT|ISLOCKED|FLIPBOARD)$"
)


@dataclass(frozen=True)
class PcbLibLegacyLayerEntry:
    layer_number: int
    name: str
    previous_layer: int
    next_layer: int
    mechanical_enabled: bool
    copper_thickness: str
    dielectric_type: int
    dielectric_constant: str
    dielectric_height: str
    dielectric_material: str


@dataclass(frozen=True)
class PcbLibV7LayerEntry:
    index: int
    layer_id: int
    name: str
    previous_layer: int
    next_layer: int
    mechanical_enabled: bool
    copper_thickness: str
    dielectric_type: int
    dielectric_constant: str
    dielectric_height: str
    dielectric_material: str


@dataclass(frozen=True)
class PcbLibLayerTable:
    legacy_layers: tuple[PcbLibLegacyLayerEntry, ...]
    v7_layers: tuple[PcbLibV7LayerEntry, ...]

    def legacy_layer(self, layer_number: int) -> PcbLibLegacyLayerEntry | None:
        return next(
            (
                entry
                for entry in self.legacy_layers
                if entry.layer_number == layer_number
            ),
            None,
        )

    def v7_layer(self, index: int) -> PcbLibV7LayerEntry | None:
        return next((entry for entry in self.v7_layers if entry.index == index), None)


@dataclass(frozen=True)
class PcbLibLibraryData:
    """
    Ordered representation of the large `Library/Data` text header blob.

    This is not yet a fully semantic model of every field, but it is already a
    synthesized composition path: parse into ordered segments, then rebuild the
    exact byte stream from those segments plus the footprint catalog.
    """

    segments: tuple[PcbLibLibraryDataSegment, ...]
    leading_pipe: bool = True
    trailing_nul: bool = True

    @classmethod
    def from_bytes(cls, data: bytes) -> "PcbLibLibraryData":
        text = data.decode("latin-1")
        trailing_nul = text.endswith("\x00")
        if trailing_nul:
            text = text[:-1]

        leading_pipe = text.startswith("|")
        if leading_pipe:
            text = text[1:]

        segments = tuple(PcbLibLibraryDataSegment(raw=part) for part in text.split("|"))
        return cls(
            segments=segments, leading_pipe=leading_pipe, trailing_nul=trailing_nul
        )

    @classmethod
    def default(cls) -> "PcbLibLibraryData":
        """Return the code-owned default Library/Data model for new PcbLib builds."""
        return cls(
            segments=tuple(
                PcbLibLibraryDataSegment(raw=segment)
                for segment in build_default_pcblib_library_data_segments()
            ),
            leading_pipe=True,
            trailing_nul=True,
        )

    def serialize(self) -> bytes:
        text = "|".join(segment.raw for segment in self.segments)
        if self.leading_pipe:
            text = "|" + text
        if self.trailing_nul:
            text += "\x00"
        return text.encode("latin-1")

    def build_stream(self, footprint_names: list[str]) -> bytes:
        return _build_library_data(self.serialize(), footprint_names)

    def _first_record_index(self) -> int | None:
        for index, segment in enumerate(self.segments):
            if segment.raw.startswith("RECORD="):
                return index
        return None

    @property
    def top_level_segments(self) -> tuple[PcbLibLibraryDataSegment, ...]:
        first_record_index = self._first_record_index()
        if first_record_index is None:
            return self.segments
        return self.segments[:first_record_index]

    @property
    def record_blocks(self) -> tuple[PcbLibLibraryDataRecord, ...]:
        first_record_index = self._first_record_index()
        if first_record_index is None:
            return ()

        records: list[PcbLibLibraryDataRecord] = []
        current_start = first_record_index
        for index in range(first_record_index + 1, len(self.segments)):
            if self.segments[index].raw.startswith("RECORD="):
                records.append(self._build_record(current_start, index))
                current_start = index
        records.append(self._build_record(current_start, len(self.segments)))
        return tuple(records)

    def _build_record(self, start: int, end: int) -> PcbLibLibraryDataRecord:
        marker = self.segments[start].raw
        record_type = marker.split("=", 1)[1] if "=" in marker else ""
        return PcbLibLibraryDataRecord(
            record_type=record_type,
            segments=self.segments[start:end],
            start_index=start,
            end_index=end,
        )

    def get_value(
        self, key: str, default: str | None = None, occurrence: int = 0
    ) -> str | None:
        seen = 0
        for segment in self.top_level_segments:
            if segment.key != key:
                continue
            if seen == occurrence:
                return segment.value
            seen += 1
        return default

    def with_updated_value(
        self, key: str, value: str, occurrence: int = 0
    ) -> "PcbLibLibraryData":
        seen = 0
        updated = False
        new_segments: list[PcbLibLibraryDataSegment] = []
        for segment in self.segments:
            if segment.key == key and seen == occurrence:
                new_segments.append(PcbLibLibraryDataSegment(raw=f"{key}={value}"))
                updated = True
            else:
                new_segments.append(segment)
            if segment.key == key:
                seen += 1

        if not updated:
            raise KeyError(f"Library/Data key not found: {key}")

        return PcbLibLibraryData(
            segments=tuple(new_segments),
            leading_pipe=self.leading_pipe,
            trailing_nul=self.trailing_nul,
        )

    def with_basic_metadata(
        self,
        *,
        filename: str | None = None,
        kind: str | None = None,
        version: str | None = None,
        date: str | None = None,
        time: str | None = None,
    ) -> "PcbLibLibraryData":
        updated = self
        if filename is not None:
            updated = updated.with_updated_value("FILENAME", filename)
        if kind is not None:
            updated = updated.with_updated_value("KIND", kind)
        if version is not None:
            updated = updated.with_updated_value("VERSION", version)
        if date is not None:
            updated = updated.with_updated_value("DATE", date)
        if time is not None:
            updated = updated.with_updated_value("TIME", time)
        return updated

    @property
    def metadata(self) -> PcbLibLibraryMetadata:
        return PcbLibLibraryMetadata(
            filename=self.get_value("FILENAME"),
            kind=self.get_value("KIND"),
            version=self.get_value("VERSION"),
            date=self.get_value("DATE"),
            time=self.get_value("TIME"),
        )

    def with_metadata(self, metadata: PcbLibLibraryMetadata) -> "PcbLibLibraryData":
        return self.with_basic_metadata(
            filename=metadata.filename,
            kind=metadata.kind,
            version=metadata.version,
            date=metadata.date,
            time=metadata.time,
        )

    def with_output_metadata(
        self, output_path: Path, when: datetime
    ) -> "PcbLibLibraryData":
        filename = str(output_path.resolve().with_suffix(".$$$"))
        date = f"{when.month}/{when.day}/{when.year}"
        hour12 = when.hour % 12 or 12
        time = f"{hour12}:{when.minute:02d}:{when.second:02d} {'AM' if when.hour < 12 else 'PM'}"
        return self.with_metadata(
            PcbLibLibraryMetadata(
                filename=filename,
                date=date,
                time=time,
            )
        )

    def get_board_record(
        self, key: str, occurrence: int = 0
    ) -> PcbLibLibraryDataRecord | None:
        seen = 0
        for record in self.record_blocks:
            if record.get_value(key) is None:
                continue
            if seen == occurrence:
                return record
            seen += 1
        return None

    def with_board_record_value(
        self, key: str, value: str, occurrence: int = 0
    ) -> "PcbLibLibraryData":
        record = self.get_board_record(key, occurrence=occurrence)
        if record is None:
            raise KeyError(f"Board record key not found: {key}")
        return self._replace_record(record.with_updated_value(key, value))

    @property
    def view_config_full_filenames(self) -> dict[str, str] | None:
        keys = ("2DCONFIGFULLFILENAME", "3DCONFIGFULLFILENAME")
        result: dict[str, str] = {}
        for key in keys:
            record = self.get_board_record(key)
            if record is None:
                continue
            value = _strip_record_terminator(record.get_value(key))
            if value is not None:
                result[key] = value
        return result or None

    @property
    def normalized_current_view_state(self) -> str | None:
        return _strip_record_terminator(self.current_view_state)

    @property
    def view_state(self) -> PcbLibViewState | None:
        return PcbLibViewState.from_text(self.current_view_state)

    def with_view_state(self, state: PcbLibViewState) -> "PcbLibLibraryData":
        return self.with_board_record_value("CURRENT2D3DVIEWSTATE", state.value)

    def get_nested_config(
        self, key: str, occurrence: int = 0
    ) -> PcbLibNestedConfig | None:
        record = self.get_board_record(key, occurrence=occurrence)
        if record is None:
            return None
        value = record.get_value(key)
        if value is None:
            return None
        return PcbLibNestedConfig.from_value(value)

    def with_nested_config_value(
        self,
        record_key: str,
        nested_key: str,
        value: str,
        occurrence: int = 0,
    ) -> "PcbLibLibraryData":
        record = self.get_board_record(record_key, occurrence=occurrence)
        if record is None:
            raise KeyError(f"Board record key not found: {record_key}")
        nested = self.get_nested_config(record_key, occurrence=occurrence)
        if nested is None:
            raise KeyError(
                f"Nested config not found for board record key: {record_key}"
            )
        updated_nested = nested.with_updated_value(nested_key, value)
        return self._replace_record(
            record.with_updated_value(record_key, updated_nested.serialize())
        )

    def _replace_record(
        self, updated_record: PcbLibLibraryDataRecord
    ) -> "PcbLibLibraryData":
        new_segments = (
            self.segments[: updated_record.start_index]
            + updated_record.segments
            + self.segments[updated_record.end_index :]
        )
        return PcbLibLibraryData(
            segments=new_segments,
            leading_pipe=self.leading_pipe,
            trailing_nul=self.trailing_nul,
        )

    def with_view_config_paths(
        self,
        *,
        config_2d_full_filename: str | Path | None = None,
        config_3d_full_filename: str | Path | None = None,
        current_view_state: str | None = None,
    ) -> "PcbLibLibraryData":
        updated = self
        if config_2d_full_filename is not None:
            current = updated.config_2d
            if current is None:
                raise KeyError("2D config record not found")
            updated = updated.with_config_2d(
                PcbLibViewConfiguration(
                    config_type=current.config_type,
                    full_filename=str(config_2d_full_filename),
                    configuration=current.configuration,
                )
            )
        if config_3d_full_filename is not None:
            current = updated.config_3d
            if current is None:
                raise KeyError("3D config record not found")
            updated = updated.with_config_3d(
                PcbLibViewConfiguration(
                    config_type=current.config_type,
                    full_filename=str(config_3d_full_filename),
                    configuration=current.configuration,
                )
            )
        if current_view_state is not None:
            updated = updated.with_view_state(
                PcbLibViewState(current_view_state.strip().upper())
            )
        return updated

    def with_synthesized_view_configuration(
        self,
        output_path: Path,
        *,
        current_view_state: str = "2D",
        config_2d_full_filename: str | Path | None = None,
        config_3d_full_filename: str | Path | None = None,
    ) -> "PcbLibLibraryData":
        resolved_output = Path(output_path).resolve()
        two_d_path = (
            str(config_2d_full_filename)
            if config_2d_full_filename is not None
            else str(resolved_output.with_suffix(".config_2dsimple"))
        )
        three_d_path = (
            str(config_3d_full_filename)
            if config_3d_full_filename is not None
            else "(Not Saved)"
        )
        return self.with_view_config_paths(
            config_2d_full_filename=two_d_path,
            config_3d_full_filename=three_d_path,
            current_view_state=current_view_state,
        )

    @property
    def current_view_state(self) -> str | None:
        record = self.get_board_record("CURRENT2D3DVIEWSTATE")
        return None if record is None else record.get_value("CURRENT2D3DVIEWSTATE")

    @property
    def viewport_bounds(self) -> dict[str, str] | None:
        viewport = self.viewport
        if viewport is None:
            return None
        return {
            "VP.LX": viewport.low_x,
            "VP.HX": viewport.high_x,
            "VP.LY": viewport.low_y,
            "VP.HY": viewport.high_y,
        }

    @property
    def viewport(self) -> PcbLibViewport | None:
        record = self.get_board_record("VP.LX")
        if record is None:
            return None
        return PcbLibViewport(
            low_x=record.get_value("VP.LX", "") or "",
            high_x=record.get_value("VP.HX", "") or "",
            low_y=record.get_value("VP.LY", "") or "",
            high_y=record.get_value("VP.HY", "") or "",
        )

    def with_viewport(self, viewport: PcbLibViewport) -> "PcbLibLibraryData":
        updated = self
        updated = updated.with_board_record_value("VP.LX", viewport.low_x)
        updated = updated.with_board_record_value("VP.HX", viewport.high_x)
        updated = updated.with_board_record_value("VP.LY", viewport.low_y)
        updated = updated.with_board_record_value("VP.HY", viewport.high_y)
        return updated

    @property
    def grid_settings(self) -> PcbLibGridSettings | None:
        record = self.get_board_record("BIGVISIBLEGRIDSIZE")
        if record is None:
            return None
        return PcbLibGridSettings(
            big_visible_grid_size=_parse_float_text(
                record.get_value("BIGVISIBLEGRIDSIZE")
            )
            or 0.0,
            visible_grid_size=_parse_float_text(record.get_value("VISIBLEGRIDSIZE"))
            or 0.0,
            snap_grid_size=_parse_float_text(record.get_value("SNAPGRIDSIZE")) or 0.0,
            snap_grid_size_x=_parse_float_text(record.get_value("SNAPGRIDSIZEX"))
            or 0.0,
            snap_grid_size_y=_parse_float_text(record.get_value("SNAPGRIDSIZEY"))
            or 0.0,
            electrical_grid_range=record.get_value("ELECTRICALGRIDRANGE", "") or "",
            electrical_grid_enabled=_parse_bool_text(
                record.get_value("ELECTRICALGRIDENABLED")
            )
            or False,
            dot_grid=_parse_bool_text(record.get_value("DOTGRID")) or False,
            dot_grid_large=_parse_bool_text(record.get_value("DOTGRIDLARGE")) or False,
            display_unit=_parse_int_text(record.get_value("DISPLAYUNIT")) or 0,
        )

    def with_grid_settings(
        self,
        settings: PcbLibGridSettings,
    ) -> "PcbLibLibraryData":
        updated = self
        updated = updated.with_board_record_value(
            "BIGVISIBLEGRIDSIZE",
            _format_fixed_float(settings.big_visible_grid_size, places=3),
        )
        updated = updated.with_board_record_value(
            "VISIBLEGRIDSIZE",
            _format_fixed_float(settings.visible_grid_size, places=3),
        )
        updated = updated.with_board_record_value(
            "SNAPGRIDSIZE",
            _format_fixed_float(settings.snap_grid_size),
        )
        updated = updated.with_board_record_value(
            "SNAPGRIDSIZEX",
            _format_fixed_float(settings.snap_grid_size_x),
        )
        updated = updated.with_board_record_value(
            "SNAPGRIDSIZEY",
            _format_fixed_float(settings.snap_grid_size_y),
        )
        updated = updated.with_board_record_value(
            "ELECTRICALGRIDRANGE",
            settings.electrical_grid_range,
        )
        updated = updated.with_board_record_value(
            "ELECTRICALGRIDENABLED",
            _format_bool_text(settings.electrical_grid_enabled),
        )
        updated = updated.with_board_record_value(
            "DOTGRID", _format_bool_text(settings.dot_grid)
        )
        updated = updated.with_board_record_value(
            "DOTGRIDLARGE",
            _format_bool_text(settings.dot_grid_large),
        )
        updated = updated.with_board_record_value(
            "DISPLAYUNIT", str(settings.display_unit)
        )
        return updated

    @property
    def camera_settings(self) -> PcbLibCameraSettings | None:
        record = self.get_board_record("LOOKAT.X")
        if record is None:
            return None
        return PcbLibCameraSettings(
            look_at_x=_parse_float_text(record.get_value("LOOKAT.X")) or 0.0,
            look_at_y=_parse_float_text(record.get_value("LOOKAT.Y")) or 0.0,
            look_at_z=_parse_float_text(record.get_value("LOOKAT.Z")) or 0.0,
            eye_rotation_x=_parse_float_text(record.get_value("EYEROTATION.X")) or 0.0,
            eye_rotation_y=_parse_float_text(record.get_value("EYEROTATION.Y")) or 0.0,
            eye_rotation_z=_parse_float_text(record.get_value("EYEROTATION.Z")) or 0.0,
            zoom_multiplier=_parse_float_text(record.get_value("ZOOMMULT")) or 0.0,
            view_size_x=_parse_int_text(record.get_value("VIEWSIZE.X")) or 0,
            view_size_y=_parse_int_text(record.get_value("VIEWSIZE.Y")) or 0,
            electrical_grid_range=record.get_value("EGRANGE", "") or "",
            electrical_grid_multiplier=_parse_float_text(record.get_value("EGMULT"))
            or 0.0,
            electrical_grid_enabled=_parse_bool_text(record.get_value("EGENABLED"))
            or False,
            electrical_grid_snap_to_board_outline=_parse_bool_text(
                record.get_value("EGSNAPTOBOARDOUTLINE")
            )
            or False,
            electrical_grid_snap_to_arc_centers=_parse_bool_text(
                record.get_value("EGSNAPTOARCCENTERS")
            )
            or False,
            electrical_grid_use_all_layers=_parse_bool_text(
                record.get_value("EGUSEALLLAYERS")
            )
            or False,
            object_guide_snap_enabled=_parse_bool_text(
                record.get_value("OGSNAPENABLED")
            )
            or False,
            midpoint_guide_snap_enabled=_parse_bool_text(
                record.get_value("MGSNAPENABLED")
            )
            or False,
            point_guide_enabled=_parse_bool_text(record.get_value("POINTGUIDEENABLED"))
            or False,
            grid_snap_enabled=_parse_bool_text(record.get_value("GRIDSNAPENABLED"))
            or False,
            near_objects_enabled=_parse_bool_text(
                record.get_value("NEAROBJECTSENABLED")
            )
            or False,
            far_objects_enabled=_parse_bool_text(record.get_value("FAROBJECTSENABLED"))
            or False,
        )

    def with_camera_settings(
        self,
        settings: PcbLibCameraSettings,
    ) -> "PcbLibLibraryData":
        updated = self
        float_updates = {
            "LOOKAT.X": settings.look_at_x,
            "LOOKAT.Y": settings.look_at_y,
            "LOOKAT.Z": settings.look_at_z,
            "EYEROTATION.X": settings.eye_rotation_x,
            "EYEROTATION.Y": settings.eye_rotation_y,
            "EYEROTATION.Z": settings.eye_rotation_z,
            "ZOOMMULT": settings.zoom_multiplier,
            "EGMULT": settings.electrical_grid_multiplier,
        }
        for key, value in float_updates.items():
            updated = updated.with_board_record_value(key, _format_fixed_float(value))
        int_updates = {
            "VIEWSIZE.X": settings.view_size_x,
            "VIEWSIZE.Y": settings.view_size_y,
        }
        for key, value in int_updates.items():
            updated = updated.with_board_record_value(key, str(value))
        updated = updated.with_board_record_value(
            "EGRANGE", settings.electrical_grid_range
        )
        bool_updates = {
            "EGENABLED": settings.electrical_grid_enabled,
            "EGSNAPTOBOARDOUTLINE": settings.electrical_grid_snap_to_board_outline,
            "EGSNAPTOARCCENTERS": settings.electrical_grid_snap_to_arc_centers,
            "EGUSEALLLAYERS": settings.electrical_grid_use_all_layers,
            "OGSNAPENABLED": settings.object_guide_snap_enabled,
            "MGSNAPENABLED": settings.midpoint_guide_snap_enabled,
            "POINTGUIDEENABLED": settings.point_guide_enabled,
            "GRIDSNAPENABLED": settings.grid_snap_enabled,
            "NEAROBJECTSENABLED": settings.near_objects_enabled,
            "FAROBJECTSENABLED": settings.far_objects_enabled,
        }
        for key, value in bool_updates.items():
            updated = updated.with_board_record_value(key, _format_bool_text(value))
        return updated

    def _get_view_configuration(
        self,
        prefix: str,
    ) -> PcbLibViewConfiguration | None:
        config_type_record = self.get_board_record(f"{prefix}CONFIGTYPE")
        full_filename_record = self.get_board_record(f"{prefix}CONFIGFULLFILENAME")
        nested_config = self.get_nested_config(f"{prefix}CONFIGURATION")
        if (
            config_type_record is None
            and full_filename_record is None
            and nested_config is None
        ):
            return None
        return PcbLibViewConfiguration(
            config_type=(
                None
                if config_type_record is None
                else _strip_record_terminator(
                    config_type_record.get_value(f"{prefix}CONFIGTYPE")
                )
            ),
            full_filename=(
                None
                if full_filename_record is None
                else _strip_record_terminator(
                    full_filename_record.get_value(f"{prefix}CONFIGFULLFILENAME")
                )
            ),
            configuration=(
                None
                if nested_config is None
                else PcbLibConfigurationBlock.from_nested_config(nested_config)
            ),
        )

    def _with_view_configuration(
        self,
        prefix: str,
        config: PcbLibViewConfiguration,
    ) -> "PcbLibLibraryData":
        updated = self
        if config.config_type is not None:
            updated = updated.with_board_record_value(
                f"{prefix}CONFIGTYPE",
                config.config_type,
            )
        if config.full_filename is not None:
            updated = updated.with_board_record_value(
                f"{prefix}CONFIGFULLFILENAME",
                config.full_filename,
            )
        if config.configuration is not None:
            record = updated.get_board_record(f"{prefix}CONFIGURATION")
            if record is None:
                raise KeyError(f"{prefix} config record not found")
            updated = updated._replace_record(
                record.with_updated_value(
                    f"{prefix}CONFIGURATION",
                    config.configuration.to_nested_config().serialize(),
                )
            )
        return updated

    @property
    def config_2d(self) -> PcbLibViewConfiguration | None:
        return self._get_view_configuration("2D")

    def with_config_2d(
        self,
        config: PcbLibViewConfiguration,
    ) -> "PcbLibLibraryData":
        return self._with_view_configuration("2D", config)

    @property
    def config_3d(self) -> PcbLibViewConfiguration | None:
        return self._get_view_configuration("3D")

    def with_config_3d(
        self,
        config: PcbLibViewConfiguration,
    ) -> "PcbLibLibraryData":
        return self._with_view_configuration("3D", config)

    def _get_2d_view_settings(self) -> PcbLib2DViewSettings | None:
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            return None
        nested = configuration.configuration.nested_config
        return PcbLib2DViewSettings(
            current_layer=nested.get_value("CFG2D.CURRENTLAYER"),
            display_special_strings=_parse_bool_text(
                nested.get_value("CFG2D.DISPLAYSPECIALSTRINGS")
            ),
            show_test_points=_parse_bool_text(nested.get_value("CFG2D.SHOWTESTPOINTS")),
            show_origin_marker=_parse_bool_text(
                nested.get_value("CFG2D.SHOWORIGINMARKER")
            ),
            eye_distance=_parse_int_text(nested.get_value("CFG2D.EYEDIST")),
            show_status_info=_parse_bool_text(nested.get_value("CFG2D.SHOWSTATUSINFO")),
            show_pad_nets=_parse_bool_text(nested.get_value("CFG2D.SHOWPADNETS")),
            show_pad_numbers=_parse_bool_text(nested.get_value("CFG2D.SHOWPADNUMBERS")),
            show_via_nets=_parse_bool_text(nested.get_value("CFG2D.SHOWVIANETS")),
            show_via_span=_parse_bool_text(nested.get_value("CFG2D.SHOWVIASPAN")),
            use_transparent_layers=_parse_bool_text(
                nested.get_value("CFG2D.USETRANSPARENTLAYERS")
            ),
            plane_draw_mode=_parse_int_text(nested.get_value("CFG2D.PLANEDRAWMODE")),
            single_layer_mode_state=_parse_int_text(
                nested.get_value("CFG2D.SINGLELAYERMODESTATE")
            ),
        )

    @property
    def config_2d_settings(self) -> PcbLib2DViewSettings | None:
        return self._get_2d_view_settings()

    def with_config_2d_settings(
        self,
        settings: PcbLib2DViewSettings,
    ) -> "PcbLibLibraryData":
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            raise KeyError("2D config record not found")
        nested = configuration.configuration.nested_config
        updates: list[tuple[str, str]] = []
        if settings.current_layer is not None:
            updates.append(("CFG2D.CURRENTLAYER", settings.current_layer))
        if settings.display_special_strings is not None:
            updates.append(
                (
                    "CFG2D.DISPLAYSPECIALSTRINGS",
                    _format_bool_text(settings.display_special_strings),
                )
            )
        if settings.show_test_points is not None:
            updates.append(
                ("CFG2D.SHOWTESTPOINTS", _format_bool_text(settings.show_test_points))
            )
        if settings.show_origin_marker is not None:
            updates.append(
                (
                    "CFG2D.SHOWORIGINMARKER",
                    _format_bool_text(settings.show_origin_marker),
                )
            )
        if settings.eye_distance is not None:
            updates.append(("CFG2D.EYEDIST", str(settings.eye_distance)))
        if settings.show_status_info is not None:
            updates.append(
                ("CFG2D.SHOWSTATUSINFO", _format_bool_text(settings.show_status_info))
            )
        if settings.show_pad_nets is not None:
            updates.append(
                ("CFG2D.SHOWPADNETS", _format_bool_text(settings.show_pad_nets))
            )
        if settings.show_pad_numbers is not None:
            updates.append(
                ("CFG2D.SHOWPADNUMBERS", _format_bool_text(settings.show_pad_numbers))
            )
        if settings.show_via_nets is not None:
            updates.append(
                ("CFG2D.SHOWVIANETS", _format_bool_text(settings.show_via_nets))
            )
        if settings.show_via_span is not None:
            updates.append(
                ("CFG2D.SHOWVIASPAN", _format_bool_text(settings.show_via_span))
            )
        if settings.use_transparent_layers is not None:
            updates.append(
                (
                    "CFG2D.USETRANSPARENTLAYERS",
                    _format_bool_text(settings.use_transparent_layers),
                )
            )
        if settings.plane_draw_mode is not None:
            updates.append(("CFG2D.PLANEDRAWMODE", str(settings.plane_draw_mode)))
        if settings.single_layer_mode_state is not None:
            updates.append(
                ("CFG2D.SINGLELAYERMODESTATE", str(settings.single_layer_mode_state))
            )
        for key, value in updates:
            nested = nested.with_updated_value(key, value)
        return self.with_config_2d(
            PcbLibViewConfiguration(
                config_type=configuration.config_type,
                full_filename=configuration.full_filename,
                configuration=PcbLibConfigurationBlock.from_nested_config(nested),
            )
        )

    def _get_3d_view_settings(self) -> PcbLib3DViewSettings | None:
        configuration = self.config_3d
        if configuration is None or configuration.configuration is None:
            return None
        nested = configuration.configuration.nested_config
        return PcbLib3DViewSettings(
            show_component_bodies=_parse_bool_text(
                nested.get_value("CFG3D.SHOWCOMPONENTBODIES")
            ),
            show_component_step_models=_parse_bool_text(
                nested.get_value("CFG3D.SHOWCOMPONENTSTEPMODELS")
            ),
            component_model_preference=_parse_int_text(
                nested.get_value("CFG3D.COMPONENTMODELPREFERENCE")
            ),
            show_component_axes=_parse_bool_text(
                nested.get_value("CFG3D.SHOWCOMPONENTAXES")
            ),
            show_board_core=_parse_bool_text(nested.get_value("CFG3D.SHOWBOARDCORE")),
            show_board_prepreg=_parse_bool_text(
                nested.get_value("CFG3D.SHOWBOARDPREPREG")
            ),
            show_top_silkscreen=_parse_bool_text(
                nested.get_value("CFG3D.SHOWTOPSILKSCREEN")
            ),
            show_bottom_silkscreen=_parse_bool_text(
                nested.get_value("CFG3D.SHOWBOTSILKSCREEN")
            ),
            show_origin_marker=_parse_bool_text(
                nested.get_value("CFG3D.SHOWORIGINMARKER")
            ),
            eye_distance=_parse_int_text(nested.get_value("CFG3D.EYEDIST")),
            show_cutouts=_parse_bool_text(nested.get_value("CFG3D.SHOWCUTOUTS")),
            show_route_tool_path=_parse_bool_text(
                nested.get_value("CFG3D.SHOWROUTETOOLPATH")
            ),
            show_rooms_3d=_parse_bool_text(nested.get_value("CFG3D.SHOWROOMS3D")),
            use_system_colors=_parse_bool_text(
                nested.get_value("CFG3D.USESYSCOLORSFOR3D")
            ),
        )

    @property
    def config_3d_settings(self) -> PcbLib3DViewSettings | None:
        return self._get_3d_view_settings()

    def with_config_3d_settings(
        self,
        settings: PcbLib3DViewSettings,
    ) -> "PcbLibLibraryData":
        configuration = self.config_3d
        if configuration is None or configuration.configuration is None:
            raise KeyError("3D config record not found")
        nested = configuration.configuration.nested_config
        updates: list[tuple[str, str]] = []
        if settings.show_component_bodies is not None:
            updates.append(
                (
                    "CFG3D.SHOWCOMPONENTBODIES",
                    _format_bool_text(settings.show_component_bodies),
                )
            )
        if settings.show_component_step_models is not None:
            updates.append(
                (
                    "CFG3D.SHOWCOMPONENTSTEPMODELS",
                    _format_bool_text(settings.show_component_step_models),
                )
            )
        if settings.component_model_preference is not None:
            updates.append(
                (
                    "CFG3D.COMPONENTMODELPREFERENCE",
                    str(settings.component_model_preference),
                )
            )
        if settings.show_component_axes is not None:
            updates.append(
                (
                    "CFG3D.SHOWCOMPONENTAXES",
                    _format_bool_text(settings.show_component_axes),
                )
            )
        if settings.show_board_core is not None:
            updates.append(
                ("CFG3D.SHOWBOARDCORE", _format_bool_text(settings.show_board_core))
            )
        if settings.show_board_prepreg is not None:
            updates.append(
                (
                    "CFG3D.SHOWBOARDPREPREG",
                    _format_bool_text(settings.show_board_prepreg),
                )
            )
        if settings.show_top_silkscreen is not None:
            updates.append(
                (
                    "CFG3D.SHOWTOPSILKSCREEN",
                    _format_bool_text(settings.show_top_silkscreen),
                )
            )
        if settings.show_bottom_silkscreen is not None:
            updates.append(
                (
                    "CFG3D.SHOWBOTSILKSCREEN",
                    _format_bool_text(settings.show_bottom_silkscreen),
                )
            )
        if settings.show_origin_marker is not None:
            updates.append(
                (
                    "CFG3D.SHOWORIGINMARKER",
                    _format_bool_text(settings.show_origin_marker),
                )
            )
        if settings.eye_distance is not None:
            updates.append(("CFG3D.EYEDIST", str(settings.eye_distance)))
        if settings.show_cutouts is not None:
            updates.append(
                ("CFG3D.SHOWCUTOUTS", _format_bool_text(settings.show_cutouts))
            )
        if settings.show_route_tool_path is not None:
            updates.append(
                (
                    "CFG3D.SHOWROUTETOOLPATH",
                    _format_bool_text(settings.show_route_tool_path),
                )
            )
        if settings.show_rooms_3d is not None:
            updates.append(
                ("CFG3D.SHOWROOMS3D", _format_bool_text(settings.show_rooms_3d))
            )
        if settings.use_system_colors is not None:
            updates.append(
                (
                    "CFG3D.USESYSCOLORSFOR3D",
                    _format_bool_text(settings.use_system_colors),
                )
            )
        for key, value in updates:
            nested = nested.with_updated_value(key, value)
        return self.with_config_3d(
            PcbLibViewConfiguration(
                config_type=configuration.config_type,
                full_filename=configuration.full_filename,
                configuration=PcbLibConfigurationBlock.from_nested_config(nested),
            )
        )

    @property
    def layer_opacity_table(self) -> PcbLibLayerOpacityTable | None:
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            return None
        nested = configuration.configuration.nested_config
        entries: list[PcbLibLayerOpacityEntry] = []
        for segment in nested.segments:
            key = segment.key
            if key is None or segment.value is None:
                continue
            match = _LAYER_OPACITY_KEY_RE.match(key)
            if match is None:
                continue
            values = _parse_float_series_text(segment.value)
            if values is None:
                continue
            entries.append(
                PcbLibLayerOpacityEntry(
                    layer_name=match.group(1),
                    values=values,
                )
            )
        return PcbLibLayerOpacityTable(entries=tuple(entries))

    def with_layer_opacity_table(
        self,
        table: PcbLibLayerOpacityTable,
    ) -> "PcbLibLibraryData":
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            raise KeyError("2D config record not found")
        nested = configuration.configuration.nested_config
        for entry in table.entries:
            nested = nested.with_updated_value(
                f"CFG2D.LAYEROPACITY.{entry.layer_name}",
                _format_float_series_text(entry.values),
            )
        return self.with_config_2d(
            PcbLibViewConfiguration(
                config_type=configuration.config_type,
                full_filename=configuration.full_filename,
                configuration=PcbLibConfigurationBlock.from_nested_config(nested),
            )
        )

    @property
    def toggle_layer_settings(self) -> PcbLibToggleLayerSettings | None:
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            return None
        nested = configuration.configuration.nested_config
        toggle_layers = _parse_bool_mask_text(nested.get_value("CFG2D.TOGGLELAYERS"))
        mechanical_layers_in_single_layer_mode = _parse_bool_mask_text(
            nested.get_value("CFG2D.MECHLAYERINSINGLELAYERMODE")
        )
        mechanical_layers_linked_to_sheet = _parse_bool_mask_text(
            nested.get_value("CFG2D.MECHLAYERLINKEDTOSHEET")
        )
        if (
            toggle_layers is None
            or mechanical_layers_in_single_layer_mode is None
            or mechanical_layers_linked_to_sheet is None
        ):
            return None
        return PcbLibToggleLayerSettings(
            toggle_layers=toggle_layers,
            toggle_layers_set=nested.get_value("CFG2D.TOGGLELAYERS.SET"),
            all_connections_in_single_layer_mode=_parse_bool_text(
                nested.get_value("CFG2D.ALLCONNECTIONSINSINGLELAYERMODE")
            ),
            mechanical_layers_in_single_layer_mode=mechanical_layers_in_single_layer_mode,
            mechanical_layers_in_single_layer_mode_set=nested.get_value(
                "CFG2D.MECHLAYERINSINGLELAYERMODE.SET"
            ),
            mechanical_layers_linked_to_sheet=mechanical_layers_linked_to_sheet,
            mechanical_layers_linked_to_sheet_set=nested.get_value(
                "CFG2D.MECHLAYERLINKEDTOSHEET.SET"
            ),
            mechanical_cover_layer_updated=_parse_bool_text(
                nested.get_value("CFG2D.MECHCOVERLAYERUPDATED")
            ),
        )

    def with_toggle_layer_settings(
        self,
        settings: PcbLibToggleLayerSettings,
    ) -> "PcbLibLibraryData":
        configuration = self.config_2d
        if configuration is None or configuration.configuration is None:
            raise KeyError("2D config record not found")
        nested = configuration.configuration.nested_config
        nested = nested.with_updated_value(
            "CFG2D.TOGGLELAYERS",
            _format_bool_mask_text(settings.toggle_layers),
        )
        if settings.toggle_layers_set is not None:
            nested = nested.with_updated_value(
                "CFG2D.TOGGLELAYERS.SET",
                settings.toggle_layers_set,
            )
        if settings.all_connections_in_single_layer_mode is not None:
            nested = nested.with_updated_value(
                "CFG2D.ALLCONNECTIONSINSINGLELAYERMODE",
                _format_bool_text(settings.all_connections_in_single_layer_mode),
            )
        nested = nested.with_updated_value(
            "CFG2D.MECHLAYERINSINGLELAYERMODE",
            _format_bool_mask_text(settings.mechanical_layers_in_single_layer_mode),
        )
        if settings.mechanical_layers_in_single_layer_mode_set is not None:
            nested = nested.with_updated_value(
                "CFG2D.MECHLAYERINSINGLELAYERMODE.SET",
                settings.mechanical_layers_in_single_layer_mode_set,
            )
        nested = nested.with_updated_value(
            "CFG2D.MECHLAYERLINKEDTOSHEET",
            _format_bool_mask_text(settings.mechanical_layers_linked_to_sheet),
        )
        if settings.mechanical_layers_linked_to_sheet_set is not None:
            nested = nested.with_updated_value(
                "CFG2D.MECHLAYERLINKEDTOSHEET.SET",
                settings.mechanical_layers_linked_to_sheet_set,
            )
        if settings.mechanical_cover_layer_updated is not None:
            nested = nested.with_updated_value(
                "CFG2D.MECHCOVERLAYERUPDATED",
                _format_bool_text(settings.mechanical_cover_layer_updated),
            )
        return self.with_config_2d(
            PcbLibViewConfiguration(
                config_type=configuration.config_type,
                full_filename=configuration.full_filename,
                configuration=PcbLibConfigurationBlock.from_nested_config(nested),
            )
        )

    @property
    def layer_sets(self) -> PcbLibLayerSets:
        record = self.get_board_record("LAYERSETSCOUNT")
        if record is None:
            return PcbLibLayerSets(sets=())
        count = _parse_int_text(record.get_value("LAYERSETSCOUNT")) or 0
        sets: list[PcbLibLayerSet] = []
        for index in range(1, count + 1):
            prefix = f"LAYERSET{index}"
            layers_value = record.get_value(f"{prefix}LAYERS") or ""
            sets.append(
                PcbLibLayerSet(
                    index=index,
                    name=record.get_value(f"{prefix}NAME") or "",
                    layers=tuple(
                        part for part in layers_value.split(",") if part != ""
                    ),
                    active_layer=record.get_value(f"{prefix}ACTIVELAYER.7") or "",
                    is_current=_parse_bool_text(record.get_value(f"{prefix}ISCURRENT"))
                    or False,
                    is_locked=_parse_bool_text(record.get_value(f"{prefix}ISLOCKED"))
                    or False,
                    flip_board=_parse_bool_text(record.get_value(f"{prefix}FLIPBOARD"))
                    or False,
                )
            )
        return PcbLibLayerSets(sets=tuple(sets))

    def with_layer_sets(
        self,
        layer_sets: PcbLibLayerSets,
    ) -> "PcbLibLibraryData":
        record = self.get_board_record("LAYERSETSCOUNT")
        if record is None:
            raise KeyError("Layer sets record not found")
        updated = self.with_board_record_value(
            "LAYERSETSCOUNT", str(len(layer_sets.sets))
        )
        for layer_set in layer_sets.sets:
            prefix = f"LAYERSET{layer_set.index}"
            updated = updated.with_board_record_value(f"{prefix}NAME", layer_set.name)
            updated = updated.with_board_record_value(
                f"{prefix}LAYERS",
                ",".join(layer_set.layers),
            )
            updated = updated.with_board_record_value(
                f"{prefix}ACTIVELAYER.7",
                layer_set.active_layer,
            )
            updated = updated.with_board_record_value(
                f"{prefix}ISCURRENT",
                _format_bool_text(layer_set.is_current),
            )
            updated = updated.with_board_record_value(
                f"{prefix}ISLOCKED",
                _format_bool_text(layer_set.is_locked),
            )
            updated = updated.with_board_record_value(
                f"{prefix}FLIPBOARD",
                _format_bool_text(layer_set.flip_board),
            )
        return updated

    @staticmethod
    def _is_layer_table_record(record: PcbLibLibraryDataRecord) -> bool:
        return any(
            (
                seg.key is not None
                and (
                    _LEGACY_LAYER_KEY_RE.match(seg.key) is not None
                    or _V7_LAYER_KEY_RE.match(seg.key) is not None
                )
            )
            for seg in record.property_segments
        )

    @property
    def layer_table(self) -> PcbLibLayerTable:
        legacy_rows: dict[int, dict[str, str]] = {}
        v7_rows: dict[int, dict[str, str]] = {}
        for record in self.record_blocks:
            if not self._is_layer_table_record(record):
                continue
            for segment in record.property_segments:
                key = segment.key
                if key is None or segment.value is None:
                    continue
                legacy_match = _LEGACY_LAYER_KEY_RE.match(key)
                if legacy_match is not None:
                    layer_number = int(legacy_match.group(1))
                    field_name = legacy_match.group(2)
                    legacy_rows.setdefault(layer_number, {})[field_name] = segment.value
                    continue
                v7_match = _V7_LAYER_KEY_RE.match(key)
                if v7_match is not None:
                    index = int(v7_match.group(1))
                    field_name = v7_match.group(2)
                    v7_rows.setdefault(index, {})[field_name] = segment.value

        legacy_layers = tuple(
            PcbLibLegacyLayerEntry(
                layer_number=layer_number,
                name=values["NAME"],
                previous_layer=int(values["PREV"]),
                next_layer=int(values["NEXT"]),
                mechanical_enabled=_parse_bool_text(values["MECHENABLED"]) or False,
                copper_thickness=values["COPTHICK"],
                dielectric_type=int(values["DIELTYPE"]),
                dielectric_constant=values["DIELCONST"],
                dielectric_height=values["DIELHEIGHT"],
                dielectric_material=values["DIELMATERIAL"],
            )
            for layer_number, values in sorted(legacy_rows.items())
        )
        v7_layers = tuple(
            PcbLibV7LayerEntry(
                index=index,
                layer_id=int(values["LAYERID"]),
                name=values["NAME"],
                previous_layer=int(values["PREV"]),
                next_layer=int(values["NEXT"]),
                mechanical_enabled=_parse_bool_text(values["MECHENABLED"]) or False,
                copper_thickness=values["COPTHICK"],
                dielectric_type=int(values["DIELTYPE"]),
                dielectric_constant=values["DIELCONST"],
                dielectric_height=values["DIELHEIGHT"],
                dielectric_material=values["DIELMATERIAL"],
            )
            for index, values in sorted(v7_rows.items())
        )
        return PcbLibLayerTable(legacy_layers=legacy_layers, v7_layers=v7_layers)

    def with_layer_table(
        self,
        layer_table: PcbLibLayerTable,
    ) -> "PcbLibLibraryData":
        update_map: dict[str, str] = {}
        for entry in layer_table.legacy_layers:
            prefix = f"LAYER{entry.layer_number}"
            update_map[f"{prefix}NAME"] = entry.name
            update_map[f"{prefix}PREV"] = str(entry.previous_layer)
            update_map[f"{prefix}NEXT"] = str(entry.next_layer)
            update_map[f"{prefix}MECHENABLED"] = _format_bool_text(
                entry.mechanical_enabled
            )
            update_map[f"{prefix}COPTHICK"] = entry.copper_thickness
            update_map[f"{prefix}DIELTYPE"] = str(entry.dielectric_type)
            update_map[f"{prefix}DIELCONST"] = entry.dielectric_constant
            update_map[f"{prefix}DIELHEIGHT"] = entry.dielectric_height
            update_map[f"{prefix}DIELMATERIAL"] = entry.dielectric_material
        for entry in layer_table.v7_layers:
            prefix = f"LAYERV7_{entry.index}"
            update_map[f"{prefix}LAYERID"] = str(entry.layer_id)
            update_map[f"{prefix}NAME"] = entry.name
            update_map[f"{prefix}PREV"] = str(entry.previous_layer)
            update_map[f"{prefix}NEXT"] = str(entry.next_layer)
            update_map[f"{prefix}MECHENABLED"] = _format_bool_text(
                entry.mechanical_enabled
            )
            update_map[f"{prefix}COPTHICK"] = entry.copper_thickness
            update_map[f"{prefix}DIELTYPE"] = str(entry.dielectric_type)
            update_map[f"{prefix}DIELCONST"] = entry.dielectric_constant
            update_map[f"{prefix}DIELHEIGHT"] = entry.dielectric_height
            update_map[f"{prefix}DIELMATERIAL"] = entry.dielectric_material

        updated = self
        for record in self.record_blocks:
            if not self._is_layer_table_record(record):
                continue
            changed = False
            new_segments: list[PcbLibLibraryDataSegment] = [record.segments[0]]
            for segment in record.property_segments:
                key = segment.key
                if key is not None and key in update_map:
                    new_segments.append(
                        PcbLibLibraryDataSegment(raw=f"{key}={update_map[key]}")
                    )
                    changed = True
                else:
                    new_segments.append(segment)
            if changed:
                updated = updated._replace_record(
                    PcbLibLibraryDataRecord(
                        record_type=record.record_type,
                        segments=tuple(new_segments),
                        start_index=record.start_index,
                        end_index=record.end_index,
                    )
                )
        return updated


@dataclass(frozen=True)
class PcbLibBuildProfile:
    library_data: PcbLibLibraryData
    file_header: PcbLibFileHeader
    pad_via_library: PcbLibPadViaLibrary

    @property
    def library_data_header(self) -> bytes:
        return self.library_data.serialize()

    @property
    def file_header_magic(self) -> str:
        return self.file_header.magic

    @property
    def pad_via_library_guid(self) -> uuid.UUID:
        return self.pad_via_library.library_id

    @classmethod
    def from_pcblib(cls, path: Path) -> "PcbLibBuildProfile":
        ole = AltiumOleFile(str(path))
        try:
            library_data = ole.openstream("Library/Data")
            header_len = struct.unpack("<I", library_data[:4])[0]
            header_bytes = library_data[4 : 4 + header_len]

            return cls(
                library_data=PcbLibLibraryData.from_bytes(header_bytes),
                file_header=(
                    PcbLibFileHeader.from_bytes(ole.openstream("FileHeader"))
                    if ole.exists("FileHeader")
                    else PcbLibFileHeader.random_default()
                ),
                pad_via_library=(
                    PcbLibPadViaLibrary.from_bytes(
                        ole.openstream("Library/PadViaLibrary/Data")
                    )
                    if ole.exists("Library/PadViaLibrary/Data")
                    else PcbLibPadViaLibrary(library_id=uuid.uuid4())
                ),
            )
        finally:
            ole.close()

    @classmethod
    def default(cls) -> "PcbLibBuildProfile":
        return cls(
            library_data=PcbLibLibraryData.default(),
            file_header=PcbLibFileHeader.default(
                magic=DEFAULT_PCBLIB_FILE_HEADER_MAGIC,
            ),
            pad_via_library=PcbLibPadViaLibrary(
                library_id=DEFAULT_PCBLIB_PAD_VIA_LIBRARY_GUID
            ),
        )


@dataclass
class PcbLibFootprintSpec:
    footprint: AltiumPcbFootprint
    height: str = "0mil"
    description: str = ""
    item_guid: str = ""
    revision_guid: str = ""
    component_guid: uuid.UUID = field(default_factory=uuid.uuid4)
    widestrings: dict[int, str] = field(default_factory=dict)
    primitive_guids: dict[object, uuid.UUID] = field(default_factory=dict)
    primitive_unique_ids: dict[object, str] = field(default_factory=dict)


@dataclass
class PcbLibModelSpec:
    model: AltiumPcbModel
    embedded_payload: bytes


class PcbLibBuilder:
    """
    Dedicated builder for constructing PcbLib containers and footprint streams.

    The builder emits file-level OLE streams without going through any
    `PcbDoc -> PcbLib` path.
    """

    def __init__(self, profile: PcbLibBuildProfile | None = None) -> None:
        self.profile = profile or PcbLibBuildProfile.default()
        self._footprints: list[PcbLibFootprintSpec] = []
        self._embedded_models: list[PcbLibModelSpec] = []

    @staticmethod
    def _format_model_id(value: uuid.UUID | str | None = None) -> str:
        if value is None:
            value = uuid.uuid4()
        if isinstance(value, uuid.UUID):
            return "{" + str(value).upper() + "}"
        text = str(value).strip()
        if text.startswith("{") and text.endswith("}"):
            return text.upper()
        return "{" + text.upper() + "}"

    @staticmethod
    def _encode_identifier(text: str) -> str:
        return ",".join(str(ord(ch)) for ch in text)

    def add_footprint(
        self,
        name: str,
        *,
        height: str = "0mil",
        description: str = "",
        item_guid: str = "",
        revision_guid: str = "",
        component_guid: uuid.UUID | None = None,
    ) -> AltiumPcbFootprint:
        """
        Create and register a new footprint owned by this library builder.
        """
        footprint = AltiumPcbFootprint(name)
        footprint.parameters.update(
            {
                "PATTERN": name,
                "HEIGHT": height,
                "DESCRIPTION": description,
                "ITEMGUID": item_guid,
                "REVISIONGUID": revision_guid,
            }
        )
        spec = PcbLibFootprintSpec(
            footprint=footprint,
            height=height,
            description=description,
            item_guid=item_guid,
            revision_guid=revision_guid,
            component_guid=component_guid or uuid.uuid4(),
        )
        self._footprints.append(spec)
        footprint._bind_authoring_builder(self)
        return footprint

    def add_existing_footprint(
        self,
        footprint: AltiumPcbFootprint,
        *,
        height: str | None = None,
        description: str | None = None,
        item_guid: str | None = None,
        revision_guid: str | None = None,
        component_guid: uuid.UUID | None = None,
        copy_footprint: bool = True,
    ) -> AltiumPcbFootprint:
        owned_footprint = copy.deepcopy(footprint) if copy_footprint else footprint

        if not owned_footprint._record_order:
            for collection in (
                owned_footprint.pads,
                owned_footprint.tracks,
                owned_footprint.arcs,
                owned_footprint.vias,
                owned_footprint.fills,
                owned_footprint.texts,
                owned_footprint.regions,
                owned_footprint.component_bodies,
            ):
                owned_footprint._record_order.extend(collection)

        resolved_height = (
            height
            if height is not None
            else owned_footprint.parameters.get("HEIGHT", "0mil")
        )
        resolved_description = (
            description
            if description is not None
            else owned_footprint.parameters.get("DESCRIPTION", "")
        )
        resolved_item_guid = (
            item_guid
            if item_guid is not None
            else owned_footprint.parameters.get("ITEMGUID", "")
        )
        resolved_revision_guid = (
            revision_guid
            if revision_guid is not None
            else owned_footprint.parameters.get("REVISIONGUID", "")
        )

        owned_footprint.parameters.update(
            {
                "PATTERN": owned_footprint.name,
                "HEIGHT": resolved_height,
                "DESCRIPTION": resolved_description,
                "ITEMGUID": resolved_item_guid,
                "REVISIONGUID": resolved_revision_guid,
            }
        )

        spec = PcbLibFootprintSpec(
            footprint=owned_footprint,
            height=resolved_height,
            description=resolved_description,
            item_guid=resolved_item_guid,
            revision_guid=resolved_revision_guid,
            component_guid=component_guid or uuid.uuid4(),
        )

        self._sync_footprint_widestrings(spec)
        for primitive in owned_footprint._record_order:
            self._ensure_primitive_guid(spec, primitive)
            if isinstance(primitive, AltiumPcbPad):
                self._ensure_primitive_unique_id(spec, primitive)

        self._footprints.append(spec)
        owned_footprint._bind_authoring_builder(self)
        return owned_footprint

    @staticmethod
    def _mil_to_internal_units(value_mil: float) -> int:
        return int(round(float(value_mil) * 10000.0))

    def _spec_for_footprint(self, footprint: AltiumPcbFootprint) -> PcbLibFootprintSpec:
        for spec in self._footprints:
            if spec.footprint is footprint:
                return spec
        raise KeyError(f"Footprint is not owned by this builder: {footprint.name}")

    def _next_widestring_index(self, spec: PcbLibFootprintSpec) -> int:
        if not spec.widestrings:
            return 0
        return max(spec.widestrings) + 1

    def _sync_footprint_widestrings(self, spec: PcbLibFootprintSpec) -> None:
        synced_widestrings: dict[int, str] = {}
        used_indices: set[int] = set()
        for text_record in spec.footprint.texts:
            wide_index = (
                int(text_record.widestring_index)
                if text_record.widestring_index is not None
                else 0
            )
            if text_record.widestring_index is None:
                text_record.widestring_index = wide_index
            if wide_index in used_indices:
                wide_index = 0
                while wide_index in used_indices:
                    wide_index += 1
                text_record.widestring_index = wide_index
            used_indices.add(wide_index)
            synced_widestrings[wide_index] = text_record.text_content or ""
        spec.widestrings = synced_widestrings

    def _ensure_primitive_guid(
        self, spec: PcbLibFootprintSpec, primitive: object
    ) -> uuid.UUID:
        guid = spec.primitive_guids.get(primitive)
        if guid is None:
            guid = uuid.uuid4()
            spec.primitive_guids[primitive] = guid
        return guid

    def _ensure_primitive_unique_id(
        self, spec: PcbLibFootprintSpec, primitive: object
    ) -> str:
        unique_id = spec.primitive_unique_ids.get(primitive)
        if unique_id is None:
            unique_id = generate_unique_id()
            spec.primitive_unique_ids[primitive] = unique_id
        return unique_id

    @staticmethod
    def _primitive_guid_type_id(primitive: object) -> int:
        if isinstance(primitive, AltiumPcbArc):
            return 0x01
        if isinstance(primitive, AltiumPcbPad):
            return 0x02
        if isinstance(primitive, AltiumPcbVia):
            return 0x03
        if isinstance(primitive, AltiumPcbTrack):
            return 0x04
        if isinstance(primitive, AltiumPcbText):
            return 0x05
        if isinstance(primitive, AltiumPcbFill):
            return 0x06
        if isinstance(primitive, AltiumPcbRegion):
            return 0x0B
        if isinstance(primitive, AltiumPcbShapeBasedRegion):
            return 0x0B
        if isinstance(primitive, AltiumPcbComponentBody):
            return 0x5A
        raise TypeError(f"Unsupported primitive GUID type: {type(primitive).__name__}")

    def _build_footprint_primitive_guids(self, spec: PcbLibFootprintSpec) -> bytes:
        records = bytearray()
        for index, primitive in enumerate(spec.footprint._record_order):
            records.extend(
                _build_primitive_guid_record(
                    self._primitive_guid_type_id(primitive),
                    index,
                    self._ensure_primitive_guid(spec, primitive),
                )
            )
        records.extend(_build_primitive_guid_record(0x55, 0, spec.component_guid))
        return bytes(records)

    def _build_footprint_uniqueid_info(self, spec: PcbLibFootprintSpec) -> bytes | None:
        records = bytearray()
        for index, primitive in enumerate(spec.footprint._record_order):
            if not isinstance(primitive, AltiumPcbPad):
                continue
            body = (
                f"|PRIMITIVEINDEX={index}"
                f"|PRIMITIVEOBJECTID=Pad"
                f"|UNIQUEID={self._ensure_primitive_unique_id(spec, primitive)}\x00"
            ).encode("ascii")
            records.extend(struct.pack("<I", len(body)))
            records.extend(body)
        return bytes(records) if records else None

    def _append_primitive(
        self, footprint: AltiumPcbFootprint, primitive: object
    ) -> None:
        spec = self._spec_for_footprint(footprint)
        if isinstance(primitive, AltiumPcbPad):
            footprint.pads.append(primitive)
        elif isinstance(primitive, AltiumPcbVia):
            footprint.vias.append(primitive)
        elif isinstance(primitive, AltiumPcbTrack):
            footprint.tracks.append(primitive)
        elif isinstance(primitive, AltiumPcbArc):
            footprint.arcs.append(primitive)
        elif isinstance(primitive, AltiumPcbFill):
            footprint.fills.append(primitive)
        elif isinstance(primitive, AltiumPcbText):
            footprint.texts.append(primitive)
        elif isinstance(primitive, AltiumPcbRegion):
            footprint.regions.append(primitive)
        elif isinstance(primitive, AltiumPcbShapeBasedRegion):
            footprint.regions.append(primitive)
        elif isinstance(primitive, AltiumPcbComponentBody):
            footprint.component_bodies.append(primitive)
        else:
            raise TypeError(
                f"Unsupported footprint primitive type: {type(primitive).__name__}"
            )
        footprint._record_order.append(primitive)
        self._ensure_primitive_guid(spec, primitive)
        if isinstance(primitive, AltiumPcbPad):
            self._ensure_primitive_unique_id(spec, primitive)

    def add_embedded_model(
        self,
        *,
        name: str,
        model_data: bytes,
        model_id: uuid.UUID | str | None = None,
        rotation_x_degrees: float = 0.0,
        rotation_y_degrees: float = 0.0,
        rotation_z_degrees: float = 0.0,
        z_offset_mil: float = 0.0,
        checksum: int | None = None,
        model_source: str = "Undefined",
        data_is_compressed: bool = False,
    ) -> AltiumPcbModel:
        """
        Add an embedded 3D model payload to the library.

        When `checksum` is omitted, the checksum is computed with Altium's
        native byte-weighted model checksum algorithm. Pass `checksum` only
        when preserving source metadata exactly during a copy workflow.

        Args:
            name: Model filename stored in `Library/Models/Data`.
            model_data: Model payload bytes. Pass uncompressed bytes by default,
                or zlib-compressed payload stream bytes when
                `data_is_compressed=True`.
            model_id: Optional model GUID. A new GUID is generated when omitted;
                pass a deterministic GUID only for repeatable generated output.
            rotation_x_degrees: Default model X-axis rotation in degrees.
            rotation_y_degrees: Default model Y-axis rotation in degrees.
            rotation_z_degrees: Default model Z-axis rotation in degrees.
            z_offset_mil: Default model Z offset in mils.
            checksum: Optional native checksum override for metadata-preserving
                copy workflows.
            model_source: Altium model source string.
            data_is_compressed: True when `model_data` is already a compressed
                `Library/Models/<n>` payload.

        Returns:
            The authored embedded model metadata object.
        """
        model = AltiumPcbModel()
        model.name = name
        model.id = self._format_model_id(model_id)
        model.is_embedded = True
        model.model_source = model_source
        model.rotation_x = float(rotation_x_degrees)
        model.rotation_y = float(rotation_y_degrees)
        model.rotation_z = float(rotation_z_degrees)
        model.z_offset = float(self._mil_to_internal_units(z_offset_mil))

        if data_is_compressed:
            embedded_payload = bytes(model_data)
            try:
                checksum_source = zlib.decompress(embedded_payload)
            except zlib.error:
                checksum_source = embedded_payload
        else:
            checksum_source = bytes(model_data)
            embedded_payload = zlib.compress(checksum_source)

        model.checksum = (
            compute_altium_model_checksum(checksum_source)
            if checksum is None
            else int(checksum)
        )
        model.embedded_data = checksum_source
        self._embedded_models.append(
            PcbLibModelSpec(model=model, embedded_payload=embedded_payload)
        )
        return model

    def add_pad(
        self,
        footprint: AltiumPcbFootprint,
        *,
        designator: str,
        x_mil: float,
        y_mil: float,
        width_mil: float,
        height_mil: float,
        layer: int | PcbLayer = PcbLayer.TOP,
        shape: int = PadShape.RECTANGLE,
        rotation_degrees: float = 0.0,
        hole_size_mil: float = 0.0,
        plated: bool | None = None,
        corner_radius_percent: int | None = None,
        slot_length_mil: float = 0.0,
        slot_rotation_degrees: float = 0.0,
    ) -> AltiumPcbPad:
        """
        Add a simple pad primitive to a footprint.
        """
        pad = AltiumPcbPad()
        layer_id = int(layer)
        validate_non_negative(width_mil, "width_mil")
        validate_non_negative(height_mil, "height_mil")
        validate_non_negative(hole_size_mil, "hole_size_mil")
        validate_non_negative(slot_length_mil, "slot_length_mil")
        width_iu = self._mil_to_internal_units(width_mil)
        height_iu = self._mil_to_internal_units(height_mil)
        hole_iu = self._mil_to_internal_units(hole_size_mil)
        slot_iu = self._mil_to_internal_units(slot_length_mil)
        if slot_iu > 0 and hole_iu <= 0:
            raise ValueError("slot_length_mil requires a positive hole_size_mil")
        if slot_iu > 0 and slot_iu < hole_iu:
            raise ValueError(
                "slot_length_mil must be greater than or equal to hole_size_mil"
            )
        pad.designator = designator
        pad.layer = layer_id
        pad.x = self._mil_to_internal_units(x_mil)
        pad.y = self._mil_to_internal_units(y_mil)
        pad.width = width_iu
        pad.height = height_iu
        pad.top_width = width_iu
        pad.top_height = height_iu
        pad.mid_width = width_iu
        pad.mid_height = height_iu
        pad.bot_width = width_iu
        pad.bot_height = height_iu
        apply_authored_pad_shape(
            pad,
            shape=shape,
            width_iu=width_iu,
            height_iu=height_iu,
            corner_radius_percent=corner_radius_percent,
        )
        pad.rotation = float(rotation_degrees)
        pad.layer_v7_save_id = legacy_layer_to_v7_save_id(layer_id)
        pad.hole_size = hole_iu
        pad.is_plated = bool(plated) if plated is not None else False
        pad.net_index = None
        pad.component_index = None
        pad.polygon_index = 0xFFFF
        pad.union_index = 0xFFFFFFFF
        pad.pad_mode = 0
        pad.user_routed = True
        pad._flags = 0x000C
        pad._subrecord2_data = _PAD_SUBRECORD2_DEFAULT
        pad._subrecord3_data = _PAD_SUBRECORD3_DEFAULT
        pad._subrecord4_data = _PAD_SUBRECORD4_DEFAULT
        if slot_iu > 0:
            pad.hole_shape = SLOT_HOLE_SHAPE
            pad.slot_size = slot_iu
            pad.slot_rotation = float(slot_rotation_degrees)
        self._append_primitive(footprint, pad)
        return pad

    def add_custom_pad(
        self,
        footprint: AltiumPcbFootprint,
        *,
        designator: str,
        x_mil: float,
        y_mil: float,
        outline_points_mil: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.TOP,
        offset_x_mil: float = 0.0,
        offset_y_mil: float = 0.0,
        anchor_diameter_mil: float = 1.0,
        hole_points_mil: list[list[tuple[float, float]]] | None = None,
        outline_points_are_local: bool = True,
        paste_rule_expansion: bool = True,
        solder_rule_expansion: bool = True,
    ) -> AltiumPcbPad:
        """
        Author a custom pad as Altium stores it: tiny anchor pad plus region.
        """
        if len(outline_points_mil) < 3:
            raise ValueError("Custom pad outline requires at least 3 points")

        layer_id = int(layer)
        pad = self.add_pad(
            footprint,
            designator=designator,
            x_mil=x_mil,
            y_mil=y_mil,
            width_mil=anchor_diameter_mil,
            height_mil=anchor_diameter_mil,
            layer=layer_id,
            shape=PadShape.CIRCLE,
            rotation_degrees=0.0,
        )

        offset_x_iu = self._mil_to_internal_units(offset_x_mil)
        offset_y_iu = self._mil_to_internal_units(offset_y_mil)
        if offset_x_iu or offset_y_iu:
            pad.hole_offset_x = [offset_x_iu] * 32
            pad.hole_offset_y = [offset_y_iu] * 32
            pad.alt_shape = [int(PadShape.CIRCLE)] * 32
            pad.corner_radius = [0] * 32

        pad.pastemask_expansion_mode = 1 if paste_rule_expansion else 0
        pad.soldermask_expansion_mode = 1 if solder_rule_expansion else 0

        center_x_mil = float(x_mil) + float(offset_x_mil)
        center_y_mil = float(y_mil) + float(offset_y_mil)

        def _to_absolute(
            points: list[tuple[float, float]],
        ) -> list[tuple[float, float]]:
            if not outline_points_are_local:
                return list(points)
            return [
                (center_x_mil + float(px), center_y_mil + float(py))
                for px, py in points
            ]

        region = self.add_region(
            footprint,
            outline_points_mil=_to_absolute(outline_points_mil),
            layer=layer_id,
            hole_points_mil=[_to_absolute(hole) for hole in (hole_points_mil or [])],
            kind=0,
            is_shapebased=True,
        )
        shape_region = AltiumPcbShapeBasedRegion()
        shape_region.layer = layer_id
        shape_region.is_shapebased = True
        shape_region.properties = {
            "V7_LAYER": PcbLayer(layer_id).to_json_name(),
            "NAME": " ",
            "KIND": "0",
            "SUBPOLYINDEX": "-1",
            "UNIONINDEX": "0",
            "ARCRESOLUTION": "0.1mil",
            "ISSHAPEBASED": "TRUE",
            "CAVITYHEIGHT": "0mil",
            "PADINDEX": "1",
        }
        absolute_outline = _to_absolute(outline_points_mil)
        outline_vertices: list[PcbExtendedVertex] = []
        for point_x_mil, point_y_mil in absolute_outline:
            vertex = PcbExtendedVertex()
            vertex.is_round = False
            vertex.x = self._mil_to_internal_units(point_x_mil)
            vertex.y = self._mil_to_internal_units(point_y_mil)
            vertex.center_x = 0
            vertex.center_y = 0
            vertex.radius = 0
            vertex.start_angle = 0.0
            vertex.end_angle = 0.0
            outline_vertices.append(vertex)
        if outline_vertices:
            closing = PcbExtendedVertex()
            first = outline_vertices[0]
            closing.is_round = first.is_round
            closing.x = first.x
            closing.y = first.y
            closing.center_x = first.center_x
            closing.center_y = first.center_y
            closing.radius = first.radius
            closing.start_angle = first.start_angle
            closing.end_angle = first.end_angle
            outline_vertices.append(closing)
        shape_region.outline = outline_vertices

        attach_custom_pad_shape(
            pad,
            source="builder",
            region=region,
            shape_region=shape_region,
            shape_kind=int(PadShape.CUSTOM),
        )
        region.properties = build_pcblib_custom_pad_region_properties(
            region=region,
            shape_region=shape_region,
            pad_index=1,
        )
        primitive_index = footprint._record_order.index(region)
        footprint.extended_primitive_information.append(
            build_pcblib_custom_pad_extended_info(
                primitive_index=primitive_index,
                pad=pad,
            )
        )
        return pad

    def add_track(
        self,
        footprint: AltiumPcbFootprint,
        *,
        start_x_mil: float,
        start_y_mil: float,
        end_x_mil: float,
        end_y_mil: float,
        width_mil: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbTrack:
        """
        Add a track primitive to a footprint.
        """
        track = AltiumPcbTrack()
        layer_id = int(layer)
        track.layer = layer_id
        track.v7_layer_id = (
            legacy_layer_to_v7_save_id(layer_id)
            if v7_layer_id is None
            else int(v7_layer_id)
        )
        track.start_x = self._mil_to_internal_units(start_x_mil)
        track.start_y = self._mil_to_internal_units(start_y_mil)
        track.end_x = self._mil_to_internal_units(end_x_mil)
        track.end_y = self._mil_to_internal_units(end_y_mil)
        track.width = self._mil_to_internal_units(width_mil)
        track.component_index = None
        track.net_index = None
        track.polygon_index = 0
        track.subpoly_index = 0
        track.union_index = 0xFFFFFFFF
        track.is_locked = False
        track.is_keepout = False
        track.is_polygon_outline = False
        track.user_routed = True
        track.solder_mask_expansion = 0
        track.paste_mask_expansion = 0
        track.keepout_restrictions = 0
        track._original_content_len = 49
        self._append_primitive(footprint, track)
        return track

    def add_arc(
        self,
        footprint: AltiumPcbFootprint,
        *,
        center_x_mil: float,
        center_y_mil: float,
        radius_mil: float,
        start_angle_degrees: float,
        end_angle_degrees: float,
        width_mil: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbArc:
        arc = AltiumPcbArc()
        layer_id = int(layer)
        arc.layer = layer_id
        arc.v7_layer_id = (
            legacy_layer_to_v7_save_id(layer_id)
            if v7_layer_id is None
            else int(v7_layer_id)
        )
        arc.center_x = self._mil_to_internal_units(center_x_mil)
        arc.center_y = self._mil_to_internal_units(center_y_mil)
        arc.radius = self._mil_to_internal_units(radius_mil)
        arc.start_angle = float(start_angle_degrees)
        arc.end_angle = float(end_angle_degrees)
        arc.width = self._mil_to_internal_units(width_mil)
        arc.component_index = None
        arc.net_index = None
        arc.polygon_index = 0
        arc.subpoly_index = 0
        arc.union_index = 0xFFFFFFFF
        arc.is_locked = False
        arc.is_keepout = False
        arc.is_polygon_outline = False
        arc.user_routed = True
        arc.solder_mask_expansion = 0
        arc.paste_mask_expansion = 0
        arc.keepout_restrictions = 0
        arc._original_content_len = 60
        self._append_primitive(footprint, arc)
        return arc

    def add_fill(
        self,
        footprint: AltiumPcbFootprint,
        *,
        pos1_x_mil: float,
        pos1_y_mil: float,
        pos2_x_mil: float,
        pos2_y_mil: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        rotation_degrees: float = 0.0,
        v7_layer_id: int | None = None,
    ) -> AltiumPcbFill:
        fill = AltiumPcbFill()
        layer_id = int(layer)
        fill.layer = layer_id
        fill.v7_layer_id = (
            legacy_layer_to_v7_save_id(layer_id)
            if v7_layer_id is None
            else int(v7_layer_id)
        )
        fill.pos1_x = self._mil_to_internal_units(pos1_x_mil)
        fill.pos1_y = self._mil_to_internal_units(pos1_y_mil)
        fill.pos2_x = self._mil_to_internal_units(pos2_x_mil)
        fill.pos2_y = self._mil_to_internal_units(pos2_y_mil)
        fill.rotation = float(rotation_degrees)
        fill.component_index = None
        fill.net_index = None
        fill.polygon_index = 0xFFFF
        fill.union_index = 0xFFFFFFFF
        fill.is_locked = False
        fill.is_keepout = False
        fill.is_polygon_outline = False
        fill.user_routed = True
        fill.solder_mask_expansion = 0
        fill.paste_mask_expansion = 0
        fill.keepout_restrictions = 0
        fill._original_content_len = 50
        self._append_primitive(footprint, fill)
        return fill

    def add_via(
        self,
        footprint: AltiumPcbFootprint,
        *,
        x_mil: float,
        y_mil: float,
        diameter_mil: float,
        hole_size_mil: float,
        layer_start: int | PcbLayer = PcbLayer.TOP,
        layer_end: int | PcbLayer = PcbLayer.BOTTOM,
    ) -> AltiumPcbVia:
        via = AltiumPcbVia()
        via.layer = int(PcbLayer.MULTI_LAYER)
        via.net_index = None
        via.component_index = None
        via.polygon_index = 0xFFFF
        via.x = self._mil_to_internal_units(x_mil)
        via.y = self._mil_to_internal_units(y_mil)
        via.diameter = self._mil_to_internal_units(diameter_mil)
        via.hole_size = self._mil_to_internal_units(hole_size_mil)
        via.layer_start = int(layer_start)
        via.layer_end = int(layer_end)
        via.via_mode = 0
        via.union_index = 0
        via.diameter_by_layer = [0] * 32
        for layer_id in range(
            min(via.layer_start, via.layer_end), max(via.layer_start, via.layer_end) + 1
        ):
            if 1 <= layer_id <= 32:
                via.diameter_by_layer[layer_id - 1] = via.diameter
        self._append_primitive(footprint, via)
        return via

    def add_region(
        self,
        footprint: AltiumPcbFootprint,
        *,
        outline_points_mil: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.TOP,
        hole_points_mil: list[list[tuple[float, float]]] | None = None,
        kind: int = 0,
        is_board_cutout: bool = False,
        is_shapebased: bool = False,
        is_keepout: bool = False,
        keepout_restrictions: int = 0,
        subpoly_index: int = 0,
    ) -> AltiumPcbRegion:
        if len(outline_points_mil) < 3:
            raise ValueError("Region outline requires at least 3 points")

        region = AltiumPcbRegion()
        region.layer = int(layer)
        region.net_index = None
        region.component_index = None
        region.polygon_index = 0xFFFF
        region.is_locked = False
        region.is_keepout = bool(is_keepout)
        region.is_polygon_outline = False
        region.kind = int(kind)
        region.is_board_cutout = bool(is_board_cutout)
        region.is_shapebased = bool(is_shapebased)
        region.keepout_restrictions = int(keepout_restrictions)
        region.subpoly_index = int(subpoly_index)
        region.properties = {}
        region.outline_vertices = [
            RegionVertex(
                x_raw=float(self._mil_to_internal_units(x_mil)),
                y_raw=float(self._mil_to_internal_units(y_mil)),
            )
            for x_mil, y_mil in outline_points_mil
        ]
        region.hole_vertices = [
            [
                RegionVertex(
                    x_raw=float(self._mil_to_internal_units(x_mil)),
                    y_raw=float(self._mil_to_internal_units(y_mil)),
                )
                for x_mil, y_mil in hole
            ]
            for hole in (hole_points_mil or [])
        ]
        region.outline_vertex_count = len(region.outline_vertices)
        region.hole_count = len(region.hole_vertices)
        self._append_primitive(footprint, region)
        return region

    def add_text(
        self,
        footprint: AltiumPcbFootprint,
        *,
        text: str,
        x_mil: float,
        y_mil: float,
        height_mil: float,
        layer: int | PcbLayer = PcbLayer.TOP_OVERLAY,
        rotation_degrees: float = 0.0,
        stroke_width_mil: float = 10.0,
        font_name: str = "Arial",
        is_comment: bool = False,
        is_designator: bool = False,
        is_mirrored: bool = False,
    ) -> AltiumPcbText:
        """
        Add a text primitive to a footprint.
        """
        spec = self._spec_for_footprint(footprint)
        text_record = AltiumPcbText()
        layer_id = int(layer)
        wide_index = self._next_widestring_index(spec)
        spec.widestrings[wide_index] = text

        text_record.layer = layer_id
        text_record.net_index = None
        text_record.component_index = None
        text_record.polygon_index = 0xFFFF
        text_record.union_index = 0xFFFFFFFF
        text_record.text_union_index = 0
        text_record.user_routed = True
        text_record.is_locked = False
        text_record.is_keepout = False
        text_record.x = self._mil_to_internal_units(x_mil)
        text_record.y = self._mil_to_internal_units(y_mil)
        text_record.height = self._mil_to_internal_units(height_mil)
        text_record.rotation = float(rotation_degrees)
        text_record.stroke_width = self._mil_to_internal_units(stroke_width_mil)
        text_record.width = text_record.stroke_width
        text_record.is_mirrored = is_mirrored
        text_record.is_comment = is_comment
        text_record.is_designator = is_designator
        text_record.text_content = text
        text_record.widestring_index = wide_index
        text_record.font_type = 1
        text_record._font_type_offset43 = 1
        text_record.stroke_font_type = 1
        text_record.font_name = font_name
        text_record.textbox_rect_justification = 3
        text_record.is_justification_valid = True
        text_record.advance_snapping = False
        text_record.snap_point_x = text_record.x
        text_record.snap_point_y = text_record.y
        text_record.barcode_layer_v7 = legacy_layer_to_v7_save_id(layer_id)
        text_record._original_sr1_len = 252
        self._append_primitive(footprint, text_record)
        return text_record

    def add_component_body(
        self,
        footprint: AltiumPcbFootprint,
        *,
        outline_points_mil: list[tuple[float, float]],
        layer: int | PcbLayer = PcbLayer.MECHANICAL_1,
        overall_height_mil: float,
        standoff_height_mil: float = 0.5,
        cavity_height_mil: float = 0.0,
        body_projection: PcbBodyProjection = PcbBodyProjection.TOP,
        model: AltiumPcbModel | None = None,
        model_2d_x_mil: float = 0.0,
        model_2d_y_mil: float = 0.0,
        model_2d_rotation_degrees: float = 0.0,
        model_3d_rotx_degrees: float | None = None,
        model_3d_roty_degrees: float | None = None,
        model_3d_rotz_degrees: float | None = None,
        model_3d_dz_mil: float | None = None,
        model_checksum: int | None = None,
        identifier: str | None = None,
        name: str = " ",
        body_color_3d: int = 0x808080,
        body_opacity_3d: float = 1.0,
        model_type: int = 1,
        model_source: str | None = None,
    ) -> AltiumPcbComponentBody:
        if len(outline_points_mil) < 3:
            raise ValueError("Component body outline requires at least 3 points")

        layer_id = int(layer)
        body = AltiumPcbComponentBody()
        body.layer = layer_id
        body.net_index = None
        body.polygon_index = 0xFFFF
        body.component_index = None
        body.hole_count = 0
        body.is_locked = False
        body.is_keepout = False
        body.kind = PcbRegionKind.COPPER
        body.is_shapebased = False
        body.subpoly_index = -1
        body.union_index = 0
        body.standoff_height = self._mil_to_internal_units(standoff_height_mil)
        body.overall_height = self._mil_to_internal_units(overall_height_mil)
        body.cavity_height = self._mil_to_internal_units(cavity_height_mil)
        body.body_projection = body_projection
        body.body_color_3d = int(body_color_3d)
        body.body_opacity_3d = float(body_opacity_3d)
        body.identifier = identifier or self._encode_identifier(footprint.name)
        body.texture = ""
        body.texture_center_x = 0
        body.texture_center_y = 0
        body.texture_size_x = 0
        body.texture_size_y = 0
        body.texture_rotation = 0.0
        body.arc_resolution = 0.5
        body.v7_layer = (
            PcbLayer(layer_id).to_json_name()
            if isinstance(layer, PcbLayer)
            else PcbLayer(layer_id).to_json_name()
        )
        body.name = name
        body._geometry_variant = (False, False)

        outline: list[PcbExtendedVertex] = []
        for x_mil, y_mil in outline_points_mil:
            vertex = PcbExtendedVertex()
            vertex.is_round = False
            vertex.x = float(self._mil_to_internal_units(x_mil))
            vertex.y = float(self._mil_to_internal_units(y_mil))
            vertex.center_x = vertex.x
            vertex.center_y = vertex.y
            vertex.radius = 0
            vertex.start_angle = 0.0
            vertex.end_angle = 0.0
            outline.append(vertex)
        body.outline = outline
        body.holes = []

        if model is not None:
            body.model_id = str(model.id)
            body.model_checksum = (
                int(model.checksum) if model_checksum is None else int(model_checksum)
            )
            body.model_is_embedded = bool(model.is_embedded)
            body.model_name = str(model.name)
            body.model_2d_x = self._mil_to_internal_units(model_2d_x_mil)
            body.model_2d_y = self._mil_to_internal_units(model_2d_y_mil)
            body.model_2d_rotation = float(model_2d_rotation_degrees)
            body.model_3d_rotx = float(
                model.rotation_x
                if model_3d_rotx_degrees is None
                else model_3d_rotx_degrees
            )
            body.model_3d_roty = float(
                model.rotation_y
                if model_3d_roty_degrees is None
                else model_3d_roty_degrees
            )
            body.model_3d_rotz = float(
                model.rotation_z
                if model_3d_rotz_degrees is None
                else model_3d_rotz_degrees
            )
            body.model_3d_dz = (
                int(round(model.z_offset))
                if model_3d_dz_mil is None
                else self._mil_to_internal_units(model_3d_dz_mil)
            )
            body.model_type = int(model_type)
            body.model_source = (
                model.model_source if model_source is None else model_source
            )

        self._append_primitive(footprint, body)
        return body

    def add_component_body_rectangle(
        self,
        footprint: AltiumPcbFootprint,
        *,
        left_mil: float,
        bottom_mil: float,
        right_mil: float,
        top_mil: float,
        **kwargs: object,
    ) -> AltiumPcbComponentBody:
        return self.add_component_body(
            footprint,
            outline_points_mil=[
                (left_mil, bottom_mil),
                (right_mil, bottom_mil),
                (right_mil, top_mil),
                (left_mil, top_mil),
            ],
            **kwargs,
        )

    def _assign_storage_names(self) -> bytes | None:
        entries: list[tuple[str, str]] = []
        existing: set[str] = set()
        for spec in self._footprints:
            full_name = spec.footprint.name
            sanitized = _sanitize_ole_name(full_name)
            if len(sanitized) > 31:
                ole_name = _altium_ole_truncate(sanitized, existing_keys=existing)
            else:
                ole_name = sanitized
            spec.footprint._ole_storage_name = ole_name
            existing.add(ole_name)
            if ole_name != full_name:
                entries.append((full_name, ole_name))
        if not entries:
            return None
        return PcbLibSectionKeys(
            entries=tuple(
                PcbLibSectionKeyEntry(full_name=full_name, ole_key=ole_key)
                for full_name, ole_key in entries
            )
        ).to_bytes()

    def _build_component_params_toc(self) -> PcbLibComponentParamsToc:
        return PcbLibComponentParamsToc(
            entries=tuple(
                PcbLibComponentParamsTocEntry(
                    name=spec.footprint.name,
                    pad_count=len(spec.footprint.pads),
                    height=(
                        spec.height[:-3] if spec.height.endswith("mil") else spec.height
                    ),
                    description=spec.description,
                )
                for spec in self._footprints
            )
        )

    def build(self) -> AltiumPcbLib:
        if not self._footprints:
            raise ValueError("PcbLibBuilder requires at least one footprint")

        pcblib = AltiumPcbLib()
        pcblib.raw_file_header = self.profile.file_header.to_bytes()
        pcblib.raw_library_header = PcbLibCountHeader.one().to_bytes()
        pcblib.raw_embedded_fonts = PcbLibCountHeader.zero().to_bytes()
        embedded_models = list(self._embedded_models)
        pcblib.raw_models_header = PcbLibCountHeader(len(embedded_models)).to_bytes()
        pcblib.raw_models_data = b"".join(
            model_spec.model.serialize_to_binary() for model_spec in embedded_models
        )
        pcblib.raw_models = {
            index: model_spec.embedded_payload
            for index, model_spec in enumerate(embedded_models)
        }
        pcblib.raw_models_noembed_header = PcbLibCountHeader.zero().to_bytes()
        pcblib.raw_models_noembed_data = b""
        pcblib.raw_textures_header = PcbLibCountHeader.zero().to_bytes()
        pcblib.raw_textures_data = b""
        pcblib.raw_component_params_toc_header = PcbLibCountHeader.one().to_bytes()
        pcblib.raw_component_params_toc_data = (
            self._build_component_params_toc().to_bytes()
        )
        pcblib.raw_pad_via_library_header = PcbLibCountHeader.zero().to_bytes()
        pcblib.raw_pad_via_library_data = self.profile.pad_via_library.to_bytes()
        pcblib.raw_layer_kind_mapping_header = PcbLibCountHeader.one().to_bytes()
        pcblib.raw_layer_kind_mapping = PcbLibLayerKindMapping().to_bytes()
        pcblib.raw_file_version_info_header = PcbLibCountHeader.one().to_bytes()
        pcblib.raw_file_version_info = PcbLibFileVersionInfo.default().to_bytes()
        pcblib.raw_section_keys = self._assign_storage_names()
        pcblib.raw_library_data = self.profile.library_data.build_stream(
            [spec.footprint.name for spec in self._footprints]
        )

        for spec in self._footprints:
            footprint = spec.footprint
            primitive_count = len(footprint._record_order)
            self._sync_footprint_widestrings(spec)
            footprint.raw_data = footprint.serialize_data_stream()
            footprint.raw_header = struct.pack("<I", primitive_count)
            footprint.raw_parameters = _build_footprint_parameters(spec)
            footprint.raw_widestrings = _build_footprint_widestrings(spec.widestrings)
            footprint.raw_primitive_guids = self._build_footprint_primitive_guids(spec)
            footprint.raw_primitive_guids_header = struct.pack(
                "<I", primitive_count + 1
            )
            if footprint.extended_primitive_information:
                footprint.raw_extended_primitive_info = b"".join(
                    item.serialize_record()
                    for item in footprint.extended_primitive_information
                )
                footprint.raw_extended_primitive_info_header = struct.pack(
                    "<I", len(footprint.extended_primitive_information)
                )
            elif footprint.raw_extended_primitive_info is not None:
                footprint.raw_extended_primitive_info_header = (
                    footprint.raw_extended_primitive_info_header
                    if footprint.raw_extended_primitive_info_header is not None
                    else struct.pack(
                        "<I",
                        _count_length_prefixed_records(
                            footprint.raw_extended_primitive_info
                        ),
                    )
                )
            footprint.raw_uniqueid_info = self._build_footprint_uniqueid_info(spec)
            footprint.raw_uniqueid_info_header = (
                struct.pack("<I", len(footprint.pads))
                if footprint.raw_uniqueid_info is not None
                else None
            )
            pcblib.footprints.append(footprint)

        return pcblib

    def save(
        self,
        output_path: Path,
        *,
        synthesize_file_metadata: bool = False,
        metadata_timestamp: datetime | None = None,
        synthesize_view_configuration: bool = False,
        current_view_state: str | None = None,
        config_2d_full_filename: str | Path | None = None,
        config_3d_full_filename: str | Path | None = None,
    ) -> AltiumPcbLib:
        """
        Build the library and write it to disk.

        This is the canonical public write path for builder output.
        """
        pcblib = self.build()
        library_data = self.profile.library_data
        if synthesize_file_metadata:
            when = metadata_timestamp or datetime.now()
            library_data = library_data.with_output_metadata(output_path, when)

        should_synthesize_view_configuration = (
            synthesize_view_configuration
            or current_view_state is not None
            or config_2d_full_filename is not None
            or config_3d_full_filename is not None
        )
        if should_synthesize_view_configuration:
            library_data = library_data.with_synthesized_view_configuration(
                Path(output_path),
                current_view_state=current_view_state or "2D",
                config_2d_full_filename=config_2d_full_filename,
                config_3d_full_filename=config_3d_full_filename,
            )

        if synthesize_file_metadata or should_synthesize_view_configuration:
            pcblib.raw_library_data = library_data.build_stream(
                [spec.footprint.name for spec in self._footprints]
            )
        pcblib.save(output_path)
        return pcblib
