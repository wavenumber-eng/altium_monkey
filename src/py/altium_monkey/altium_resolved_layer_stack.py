"""
Resolved layer-stack model for IPC-first consumers.

This module centralizes Altium V9/V7/legacy layer resolution into one object
so downstream code can read stable IDs and friendly display names without
re-implementing layer decoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any

from .altium_pcb_layer_ref import (
    PcbLayerFamily,
    PcbLayerRef,
    PcbLayerResolutionError,
    _normalize_layer_token,
)
from .altium_pcb_stream_helpers import parse_altium_int_token as _parse_altium_int_token
from .altium_record_types import PcbLayer


# System-layer V7 species -> legacy layer IDs used in primitives.
_SYS_V7_TO_LEGACY = {
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
}


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _optional_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value or 0.0)
    except ValueError:
        return 0.0


# Static fallback names matching IPC-2581 expectations.
_LEGACY_TO_DISPLAY: dict[int, str] = {
    PcbLayer.TOP.value: "Top Layer",
    PcbLayer.BOTTOM.value: "Bottom Layer",
    PcbLayer.TOP_OVERLAY.value: "Top Overlay",
    PcbLayer.BOTTOM_OVERLAY.value: "Bottom Overlay",
    PcbLayer.TOP_PASTE.value: "Top Paste",
    PcbLayer.BOTTOM_PASTE.value: "Bottom Paste",
    PcbLayer.TOP_SOLDER.value: "Top Solder",
    PcbLayer.BOTTOM_SOLDER.value: "Bottom Solder",
    PcbLayer.KEEPOUT.value: "Keep-Out Layer",
    PcbLayer.DRILL_GUIDE.value: "Drill Guide",
    PcbLayer.DRILL_DRAWING.value: "Drill Drawing",
    PcbLayer.MULTI_LAYER.value: "Multi-Layer",
}
for _i in range(1, 31):
    _LEGACY_TO_DISPLAY[PcbLayer.TOP.value + _i] = f"Mid-Layer {_i}"
for _i in range(1, 17):
    _LEGACY_TO_DISPLAY[PcbLayer.INTERNAL_PLANE_1.value + _i - 1] = (
        f"Internal Plane {_i}"
    )
for _i in range(1, 17):
    _LEGACY_TO_DISPLAY[PcbLayer.MECHANICAL_1.value + _i - 1] = f"Mechanical {_i}"


_STANDARD_TOKEN_TO_LEGACY: dict[str, int] = {
    "TOP": PcbLayer.TOP.value,
    "BOTTOM": PcbLayer.BOTTOM.value,
    "TOPOVERLAY": PcbLayer.TOP_OVERLAY.value,
    "BOTTOMOVERLAY": PcbLayer.BOTTOM_OVERLAY.value,
    "TOPPASTE": PcbLayer.TOP_PASTE.value,
    "BOTTOMPASTE": PcbLayer.BOTTOM_PASTE.value,
    "TOPSOLDER": PcbLayer.TOP_SOLDER.value,
    "BOTTOMSOLDER": PcbLayer.BOTTOM_SOLDER.value,
    "DRILLGUIDE": PcbLayer.DRILL_GUIDE.value,
    "KEEPOUT": PcbLayer.KEEPOUT.value,
    "DRILLDRAWING": PcbLayer.DRILL_DRAWING.value,
    "MULTILAYER": PcbLayer.MULTI_LAYER.value,
}
for _i in range(1, 31):
    _STANDARD_TOKEN_TO_LEGACY[f"MID{_i}"] = PcbLayer.TOP.value + _i
for _i in range(1, 17):
    _STANDARD_TOKEN_TO_LEGACY[f"PLANE{_i}"] = PcbLayer.INTERNAL_PLANE_1.value + _i - 1
for _i in range(1, 17):
    _STANDARD_TOKEN_TO_LEGACY[f"MECHANICAL{_i}"] = PcbLayer.MECHANICAL_1.value + _i - 1

_LEGACY_TO_STANDARD_TOKEN: dict[int, str] = {
    legacy_id: token for token, legacy_id in _STANDARD_TOKEN_TO_LEGACY.items()
}


def _standard_token_from_v7_key(v7_id: int | None) -> str | None:
    """
    Resolve the public layer token for a V7 saved-layer id.
    """
    if v7_id is None:
        return None

    layer_value = int(v7_id)
    if layer_value == 0x0100FFFF:
        return "BOTTOM"
    if layer_value < 0x01000000:
        return None

    group = (layer_value >> 16) & 0xFF
    species = layer_value & 0xFFFF
    if group == 0:
        if species == 1:
            return "TOP"
        if 2 <= species <= 0x7F:
            return f"MID{species - 1}"
        return None
    if group == 1 and 1 <= species <= 16:
        return f"PLANE{species}"
    if group == 2 and species > 0:
        return f"MECHANICAL{species}"

    legacy_id, _group = _legacy_id_from_saved_layer_id(layer_value)
    return _standard_layer_token(legacy_id)


def _legacy_to_v7_key(layer_id: int) -> int | None:
    """
    Convert legacy layer ID (1..74) to V7 cache key when defined.
    """
    if PcbLayer.TOP.value <= layer_id <= (PcbLayer.TOP.value + 30):
        return 0x01000000 + layer_id
    if layer_id == PcbLayer.BOTTOM.value:
        return 0x0100FFFF
    if PcbLayer.INTERNAL_PLANE_1.value <= layer_id <= PcbLayer.INTERNAL_PLANE_16.value:
        return 0x01010000 + (layer_id - PcbLayer.INTERNAL_PLANE_1.value + 1)
    if PcbLayer.MECHANICAL_1.value <= layer_id <= PcbLayer.MECHANICAL_16.value:
        return 0x01020000 + (layer_id - PcbLayer.MECHANICAL_1.value + 1)
    return None


def legacy_layer_to_v7_save_id(layer_id: int | PcbLayer) -> int:
    """
    Convert a legacy PCB layer ID to Altium's V7 saved-layer integer.

        Saved V7 layer fields use encoded layer-family integers here, not raw
        legacy layer IDs. For example, `Top Overlay` must be written as
        `0x01030006`, not `33`.

        The encoding is:
        - signal layers: `0x01000000 + number`, with bottom using species `0xFFFF`
        - internal planes: `0x01010000 + number`
        - mechanical layers: `0x01020000 + number`
        - misc/system layers: `0x01030000 + layer_partition`
    """
    layer_id = int(layer_id)
    if PcbLayer.TOP.value <= layer_id <= (PcbLayer.TOP.value + 30):
        return 0x01000000 + layer_id
    if layer_id == PcbLayer.BOTTOM.value:
        return 0x0100FFFF
    if PcbLayer.INTERNAL_PLANE_1.value <= layer_id <= PcbLayer.INTERNAL_PLANE_16.value:
        return 0x01010000 + (layer_id - PcbLayer.INTERNAL_PLANE_1.value + 1)
    if PcbLayer.MECHANICAL_1.value <= layer_id <= PcbLayer.MECHANICAL_16.value:
        return 0x01020000 + (layer_id - PcbLayer.MECHANICAL_1.value + 1)

    misc_partitions = {
        PcbLayer.TOP_OVERLAY.value: 6,
        PcbLayer.BOTTOM_OVERLAY.value: 7,
        PcbLayer.TOP_PASTE.value: 8,
        PcbLayer.BOTTOM_PASTE.value: 9,
        PcbLayer.TOP_SOLDER.value: 10,
        PcbLayer.BOTTOM_SOLDER.value: 11,
        PcbLayer.DRILL_GUIDE.value: 12,
        PcbLayer.KEEPOUT.value: 13,
        PcbLayer.DRILL_DRAWING.value: 14,
        PcbLayer.MULTI_LAYER.value: 15,
        PcbLayer.CONNECT.value: 16,
    }
    partition = misc_partitions.get(layer_id)
    if partition is not None:
        return 0x01030000 + partition

    raise ValueError(f"Unsupported legacy PCB layer for V7 save encoding: {layer_id}")


def _layer_display_name(layer_id: int, board: Any) -> str:
    """
    Resolve legacy layer ID to display name via the typed board model.
    """
    if board is not None:
        display_name_for_legacy_layer = getattr(
            board, "display_name_for_legacy_layer", None
        )
        if callable(display_name_for_legacy_layer):
            name = str(display_name_for_legacy_layer(layer_id) or "").strip()
            if name:
                return name
    return _LEGACY_TO_DISPLAY.get(layer_id, f"Unknown ({layer_id})")


def _legacy_layer_id_from_token(token: str) -> int | None:
    """
    Resolve standard Altium layer token/display name to legacy ID.
    """
    normalized = _normalize_layer_token(token)
    if not normalized:
        return None
    if normalized in _STANDARD_TOKEN_TO_LEGACY:
        return _STANDARD_TOKEN_TO_LEGACY[normalized]

    mid_match = re.match(r"^MID(?:LAYER)?(\d+)$", normalized)
    if mid_match:
        mid_number = int(mid_match.group(1))
        if 1 <= mid_number <= 30:
            return PcbLayer.TOP.value + mid_number
        return None

    plane_match = re.match(r"^(?:INTERNAL)?PLANE(\d+)$", normalized)
    if plane_match:
        plane_number = int(plane_match.group(1))
        if 1 <= plane_number <= 16:
            return PcbLayer.INTERNAL_PLANE_1.value + plane_number - 1
        return None

    mech_match = re.match(r"^MECHANICAL(\d+)$", normalized)
    if mech_match:
        mechanical_number = int(mech_match.group(1))
        if 1 <= mechanical_number <= 16:
            return PcbLayer.MECHANICAL_1.value + mechanical_number - 1
        return None

    return None


def _standard_layer_token(legacy_id: int | None) -> str | None:
    """
    Resolve a standard consumer-facing token for a legacy layer ID.
    """
    if legacy_id is None:
        return None
    return _LEGACY_TO_STANDARD_TOKEN.get(int(legacy_id))


def _simple_layer_key(
    display_name: str, legacy_id: int | None, v7_id: int | None
) -> str:
    """
    Build stable/simple layer key for consumer-facing IDs.
    """
    if legacy_id is not None:
        return f"L{legacy_id}"
    if v7_id is not None:
        return f"V7_{v7_id}"
    token = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_")
    if not token:
        token = "LAYER"
    return f"NAME_{token.upper()}"


@dataclass(frozen=True)
class ResolvedLayer:
    """
    Single resolved layer with stable ID and friendly display name.
    """

    layer_key: str
    display_name: str
    legacy_id: int | None = None
    v7_id: int | None = None
    v9_group: int | None = None
    stack_index: int | None = None
    thickness_mils: float = 0.0
    material: str | None = None
    layer_ref: PcbLayerRef | None = None
    family: str = ""
    side: str | None = None
    registry_ref: str = ""
    source_record_id: str = ""
    physical_row: bool = False


@dataclass(frozen=True)
class ResolvedLayerEnvelopeRow:
    """
    One local-Z physical row inside a resolved substack envelope.
    """

    layer_key: str
    display_name: str
    family: str
    stack_index: int | None
    thickness_mils: float
    z_top_mils: float
    z_bottom_mils: float
    z_center_mils: float

    def to_debug_json(self) -> dict[str, object]:
        return {
            "layer_key": self.layer_key,
            "display_name": self.display_name,
            "family": self.family,
            "stack_index": self.stack_index,
            "thickness_mils": self.thickness_mils,
            "z_top_mils": self.z_top_mils,
            "z_bottom_mils": self.z_bottom_mils,
            "z_center_mils": self.z_center_mils,
        }


@dataclass(frozen=True)
class ResolvedStackEnvelope:
    """
    Substack-local physical thickness envelope.

    Coordinates are local to the substack. The zero plane is the midpoint of
    that substack's enabled positive-thickness physical rows. This object does
    not describe global board Z placement, folding, or branch transforms.
    """

    source_stackup_ref: str
    substack_name: str
    is_flex: bool | None
    z_zero: str
    total_thickness_mils: float
    top_z_mils: float
    bottom_z_mils: float
    layers: tuple[ResolvedLayerEnvelopeRow, ...] = ()

    def to_debug_json(self) -> dict[str, object]:
        return {
            "source_stackup_ref": self.source_stackup_ref,
            "substack_name": self.substack_name,
            "is_flex": self.is_flex,
            "z_zero": self.z_zero,
            "total_thickness_mils": self.total_thickness_mils,
            "top_z_mils": self.top_z_mils,
            "bottom_z_mils": self.bottom_z_mils,
            "layers": [layer.to_debug_json() for layer in self.layers],
        }


@dataclass(frozen=True)
class ResolvedDrillPair:
    """
    Resolved drill span with generated draw/guide layer names.
    """

    start_legacy_id: int
    end_legacy_id: int
    start_layer_name: str
    end_layer_name: str
    drawing_layer_name: str
    guide_layer_name: str
    is_backdrill: bool = False
    substack_refs: tuple[str, ...] = ()
    start_layer_ref: PcbLayerRef | None = None
    end_layer_ref: PcbLayerRef | None = None


@dataclass(frozen=True)
class ResolvedSubstack:
    """
    Resolved rigid-flex substack with its enabled physical layers.
    """

    source_stackup_ref: str
    name: str
    is_flex: bool | None = None
    field_family: str = ""
    show_top_dielectric: bool | None = None
    show_bottom_dielectric: bool | None = None
    service_stackup: bool | None = None
    used_by_primitives: bool | None = None
    raw_stackup_type: str = ""
    layers: tuple[ResolvedLayer, ...] = ()
    drill_pairs: tuple[ResolvedDrillPair, ...] = ()

    @property
    def layer_names(self) -> tuple[str, ...]:
        return tuple(layer.display_name for layer in self.layers)


@dataclass(frozen=True)
class ResolvedBoardRegionContext:
    """
    Board-region linkage to a resolved substack.
    """

    name: str
    layerstack_id: str = ""
    substack_name: str = ""
    is_flex: bool | None = None
    layer_names: tuple[str, ...] = ()
    locked_3d: bool = False
    bend_line_count: int = 0


@dataclass
class ResolvedLayerStack:
    """
    Unified resolved layer stack for IPC and future consumers.
    """

    layers: tuple[ResolvedLayer, ...]
    layer_names: tuple[str, ...]
    legacy_id_to_name: dict[int, str]
    standard_layer_names: dict[str, str]
    v9_group_by_name: dict[str, int]
    top_layer_name: str
    bottom_layer_name: str
    inner_signal_layers: tuple[str, ...]
    mechanical_layer_names: tuple[str, ...]
    drill_pairs: tuple[ResolvedDrillPair, ...]
    drill_pair_layer_names: dict[tuple[int, int], tuple[str, str]]
    substacks: tuple[ResolvedSubstack, ...] = ()
    board_region_contexts: tuple[ResolvedBoardRegionContext, ...] = ()
    _layers_by_name: dict[str, ResolvedLayer] = field(
        init=False, repr=False, default_factory=dict
    )
    _layers_by_legacy: dict[int, ResolvedLayer] = field(
        init=False, repr=False, default_factory=dict
    )
    _layers_by_v7: dict[int, ResolvedLayer] = field(
        init=False, repr=False, default_factory=dict
    )
    _layers_by_token: dict[str, ResolvedLayer] = field(
        init=False, repr=False, default_factory=dict
    )
    _substacks_by_source_ref: dict[str, ResolvedSubstack] = field(
        init=False, repr=False, default_factory=dict
    )
    _substacks_by_name: dict[str, ResolvedSubstack] = field(
        init=False, repr=False, default_factory=dict
    )
    _region_contexts_by_name: dict[str, ResolvedBoardRegionContext] = field(
        init=False, repr=False, default_factory=dict
    )
    _region_contexts_by_layerstack_id: dict[
        str, tuple[ResolvedBoardRegionContext, ...]
    ] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._layers_by_name = {layer.display_name: layer for layer in self.layers}
        legacy_map: dict[int, ResolvedLayer] = {}
        v7_map: dict[int, ResolvedLayer] = {}
        token_map: dict[str, ResolvedLayer] = {}
        for layer in self.layers:
            token_map.setdefault(_normalize_layer_token(layer.display_name), layer)
            v7_token = _standard_token_from_v7_key(layer.v7_id)
            if v7_token:
                token_map.setdefault(v7_token, layer)
            if layer.legacy_id is not None:
                legacy_map.setdefault(layer.legacy_id, layer)
                standard_token = _standard_layer_token(layer.legacy_id)
                if standard_token:
                    token_map.setdefault(standard_token, layer)
            if layer.v7_id is not None:
                v7_map.setdefault(layer.v7_id, layer)
        self._layers_by_legacy = legacy_map
        self._layers_by_v7 = v7_map
        self._layers_by_token = token_map
        self._substacks_by_source_ref = {
            item.source_stackup_ref: item
            for item in self.substacks
            if item.source_stackup_ref
        }
        self._substacks_by_name = {
            item.name: item for item in self.substacks if item.name
        }
        self._region_contexts_by_name = {
            item.name: item for item in self.board_region_contexts if item.name
        }
        region_contexts_by_layerstack_id: dict[
            str, list[ResolvedBoardRegionContext]
        ] = {}
        for item in self.board_region_contexts:
            if not item.layerstack_id:
                continue
            region_contexts_by_layerstack_id.setdefault(item.layerstack_id, []).append(
                item
            )
        self._region_contexts_by_layerstack_id = {
            key: tuple(value) for key, value in region_contexts_by_layerstack_id.items()
        }

    def resolve_layer_name(self, legacy_id: int) -> str:
        """
        Resolve legacy layer ID to display name.
        """
        return self.legacy_id_to_name.get(legacy_id, f"Unknown ({legacy_id})")

    def layer_by_name(self, name: str) -> ResolvedLayer | None:
        """
        Lookup layer by display name.
        """
        return self._layers_by_name.get(name)

    def layer_by_legacy_id(self, legacy_id: int) -> ResolvedLayer | None:
        """
        Lookup layer by legacy primitive layer ID.
        """
        return self._layers_by_legacy.get(legacy_id)

    def layer_by_v7_id(self, v7_id: int) -> ResolvedLayer | None:
        """Lookup layer by exact saved V7 layer ID."""

        return self._layers_by_v7.get(v7_id)

    def layer_by_token(self, token: str) -> ResolvedLayer | None:
        """
        Lookup layer by standard token or display-name token.
        """
        return self._layers_by_token.get(_normalize_layer_token(token))

    def standard_layer_name(self, token: str) -> str | None:
        """
        Resolve a canonical standard token like TOPSOLDER or MID1.
        """
        return self.standard_layer_names.get(_normalize_layer_token(token))

    def display_name_for_token(self, token: str) -> str | None:
        """
        Resolve display name from standard token or direct display name.
        """
        layer = self.layer_by_token(token)
        if layer is None:
            return None
        return layer.display_name

    def substack_by_source_ref(
        self, source_stackup_ref: str
    ) -> ResolvedSubstack | None:
        """
        Lookup resolved substack by native Altium stackup GUID.
        """
        return self._substacks_by_source_ref.get(str(source_stackup_ref or "").strip())

    def substack_by_name(self, name: str) -> ResolvedSubstack | None:
        """
        Lookup resolved substack by display name.
        """
        return self._substacks_by_name.get(str(name or "").strip())

    def layers_for_substack(self, source_stackup_ref: str) -> tuple[ResolvedLayer, ...]:
        """
        Return enabled physical layers for the requested substack.
        """
        substack = self.substack_by_source_ref(source_stackup_ref)
        if substack is None:
            return ()
        return substack.layers

    def drill_pairs_for_substack(
        self, source_stackup_ref: str
    ) -> tuple[ResolvedDrillPair, ...]:
        """
        Return drill spans valid for the requested substack.
        """
        substack = self.substack_by_source_ref(source_stackup_ref)
        if substack is None:
            return ()
        return substack.drill_pairs

    def board_region_context_by_name(
        self, name: str
    ) -> ResolvedBoardRegionContext | None:
        """
        Lookup resolved region context by board-region name.
        """
        return self._region_contexts_by_name.get(str(name or "").strip())

    def board_region_contexts_for_layerstack_id(
        self,
        layerstack_id: str,
    ) -> tuple[ResolvedBoardRegionContext, ...]:
        """
        Return board regions bound to a given layerstack/substack GUID.
        """
        return self._region_contexts_by_layerstack_id.get(
            str(layerstack_id or "").strip(),
            (),
        )

    def substack_for_board_region(
        self,
        board_region_or_layerstack_id: Any,
    ) -> ResolvedSubstack | None:
        """
        Resolve a substack from a board-region object or raw layerstack GUID.
        """
        layerstack_id = ""
        if isinstance(board_region_or_layerstack_id, str):
            layerstack_id = board_region_or_layerstack_id
        else:
            layerstack_id = str(
                getattr(board_region_or_layerstack_id, "layerstack_id", "") or ""
            )
        return self.substack_by_source_ref(layerstack_id)

    def layers_for_board_region(
        self,
        board_region_or_layerstack_id: Any,
    ) -> tuple[ResolvedLayer, ...]:
        """
        Return enabled physical layers for a board region or layerstack GUID.
        """
        substack = self.substack_for_board_region(board_region_or_layerstack_id)
        if substack is None:
            return ()
        return substack.layers

    def stack_envelope_for_substack(
        self,
        source_stackup_ref: str,
        *,
        include_zero_thickness_layers: bool = False,
    ) -> ResolvedStackEnvelope | None:
        """
        Return a substack-local Z envelope for enabled physical rows.

        Positive-thickness physical rows are included by default. Set
        ``include_zero_thickness_layers`` to include overlay/paste or other
        zero-thickness rows in the ordered output without changing total
        thickness. The returned coordinates are local to the substack midplane.
        """
        substack = self.substack_by_source_ref(source_stackup_ref)
        if substack is None:
            return None
        return _stack_envelope_from_substack(
            substack,
            include_zero_thickness_layers=include_zero_thickness_layers,
        )

    def stack_envelope_for_board_region(
        self,
        board_region_or_layerstack_id: Any,
        *,
        include_zero_thickness_layers: bool = False,
    ) -> ResolvedStackEnvelope | None:
        """
        Return a substack-local Z envelope for a board region or layerstack GUID.
        """
        substack = self.substack_for_board_region(board_region_or_layerstack_id)
        if substack is None:
            return None
        return _stack_envelope_from_substack(
            substack,
            include_zero_thickness_layers=include_zero_thickness_layers,
        )

    def drill_pairs_for_board_region(
        self,
        board_region_or_layerstack_id: Any,
    ) -> tuple[ResolvedDrillPair, ...]:
        """
        Return drill spans valid for a board region or layerstack GUID.
        """
        substack = self.substack_for_board_region(board_region_or_layerstack_id)
        if substack is None:
            return ()
        return substack.drill_pairs

    def layer_enabled_for_substack(
        self, layer_token_or_name: str | int, source_stackup_ref: str
    ) -> bool:
        """
        Check whether a display-name/token/legacy-layer is enabled in a substack.
        """
        candidate = self._coerce_layer_candidate(layer_token_or_name)
        if candidate is None:
            return False
        return any(
            layer.layer_key == candidate.layer_key
            for layer in self.layers_for_substack(source_stackup_ref)
        )

    def layer_enabled_for_board_region(
        self,
        layer_token_or_name: str | int,
        board_region_or_layerstack_id: Any,
    ) -> bool:
        """
        Check whether a display-name/token/legacy-layer is enabled in a board region.
        """
        candidate = self._coerce_layer_candidate(layer_token_or_name)
        if candidate is None:
            return False
        return any(
            layer.layer_key == candidate.layer_key
            for layer in self.layers_for_board_region(board_region_or_layerstack_id)
        )

    def _coerce_layer_candidate(
        self, layer_token_or_name: str | int
    ) -> ResolvedLayer | None:
        if isinstance(layer_token_or_name, int):
            return self.layer_by_legacy_id(layer_token_or_name)
        token = str(layer_token_or_name or "").strip()
        if not token:
            return None
        layer = self.layer_by_token(token)
        if layer is not None:
            return layer
        return self.layer_by_name(token)


def _resolved_layer_envelope_family(layer: ResolvedLayer) -> str:
    if (
        layer.legacy_id is not None
        and PcbLayer.TOP.value <= layer.legacy_id <= PcbLayer.BOTTOM.value
    ):
        return "copper"
    if layer.material:
        return "dielectric"
    name_token = _normalize_layer_token(layer.display_name)
    if name_token.startswith("DIELECTRIC"):
        return "dielectric"
    if name_token in {"TOPLAYER", "BOTTOMLAYER"} or name_token.startswith("MID"):
        return "copper"
    return "physical"


def _stack_envelope_from_substack(
    substack: ResolvedSubstack,
    *,
    include_zero_thickness_layers: bool,
) -> ResolvedStackEnvelope:
    physical_layers = tuple(
        layer
        for layer in substack.layers
        if include_zero_thickness_layers or layer.thickness_mils > 0.0
    )
    total_thickness_mils = sum(
        layer.thickness_mils for layer in physical_layers if layer.thickness_mils > 0.0
    )
    top_z_mils = total_thickness_mils / 2.0
    bottom_z_mils = -total_thickness_mils / 2.0
    cursor_z_mils = top_z_mils
    envelope_rows: list[ResolvedLayerEnvelopeRow] = []
    for layer in physical_layers:
        thickness_mils = max(float(layer.thickness_mils), 0.0)
        row_top_z_mils = cursor_z_mils
        row_bottom_z_mils = cursor_z_mils - thickness_mils
        envelope_rows.append(
            ResolvedLayerEnvelopeRow(
                layer_key=layer.layer_key,
                display_name=layer.display_name,
                family=_resolved_layer_envelope_family(layer),
                stack_index=layer.stack_index,
                thickness_mils=thickness_mils,
                z_top_mils=row_top_z_mils,
                z_bottom_mils=row_bottom_z_mils,
                z_center_mils=(row_top_z_mils + row_bottom_z_mils) / 2.0,
            )
        )
        cursor_z_mils = row_bottom_z_mils
    return ResolvedStackEnvelope(
        source_stackup_ref=substack.source_stackup_ref,
        substack_name=substack.name,
        is_flex=substack.is_flex,
        z_zero="substack.local_midplane",
        total_thickness_mils=total_thickness_mils,
        top_z_mils=top_z_mils,
        bottom_z_mils=bottom_z_mils,
        layers=tuple(envelope_rows),
    )


def _collect_pcbdoc_primitive_layer_ids(pcbdoc: Any) -> set[int]:
    """
    Collect primitive-backed legacy layer IDs from a parsed PcbDoc.
    """
    used_layer_ids: set[int] = set()
    for track in getattr(pcbdoc, "tracks", []) or []:
        used_layer_ids.add(int(track.layer))
    for arc in getattr(pcbdoc, "arcs", []) or []:
        used_layer_ids.add(int(arc.layer))
    for fill in getattr(pcbdoc, "fills", []) or []:
        used_layer_ids.add(int(fill.layer))
    for region in getattr(pcbdoc, "regions", []) or []:
        used_layer_ids.add(int(region.layer))
    for text in getattr(pcbdoc, "texts", []) or []:
        if not getattr(text, "is_comment", False):
            used_layer_ids.add(int(text.layer))
    for body in getattr(pcbdoc, "component_bodies", []) or []:
        used_layer_ids.add(int(body.layer))
    return used_layer_ids


def _collect_pcbdoc_drill_pairs(pcbdoc: Any) -> set[tuple[int, int]]:
    """
    Collect drill spans evidenced by vias and plated through-hole pads.
    """
    drill_pairs: set[tuple[int, int]] = {(PcbLayer.TOP.value, PcbLayer.BOTTOM.value)}
    for via in getattr(pcbdoc, "vias", []) or []:
        start = getattr(via, "layer_start", PcbLayer.TOP.value)
        end = getattr(via, "layer_end", PcbLayer.BOTTOM.value)
        if start and end:
            drill_pairs.add((min(start, end), max(start, end)))
    for pad in getattr(pcbdoc, "pads", []) or []:
        if getattr(pad, "hole_size", 0) > 0:
            drill_pairs.add((PcbLayer.TOP.value, PcbLayer.BOTTOM.value))
    return drill_pairs


def _raw_mils_token(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text.removesuffix("mil"))
    except ValueError:
        return None


def _legacy_id_from_saved_layer_id(
    saved_layer_id: int | None,
) -> tuple[int | None, int | None]:
    """
    Decode a saved-layer integer to the legacy layer ID and group.
    """
    if saved_layer_id is None:
        return None, None

    layer_value = int(saved_layer_id)
    if layer_value >= 0x01000000:
        group = (layer_value >> 16) & 0xFF
        index = layer_value & 0xFFFF
        if group == 0:
            if index == 0xFFFF:
                return PcbLayer.BOTTOM.value, group
            if index == 1:
                return PcbLayer.TOP.value, group
            if 2 <= index <= 31:
                return PcbLayer.TOP.value + index - 1, group
            return None, group
        if group == 1:
            if 1 <= index <= 16:
                return PcbLayer.INTERNAL_PLANE_1.value + index - 1, group
            return None, group
        if group == 2:
            if 1 <= index <= 16:
                return PcbLayer.MECHANICAL_1.value + index - 1, group
            return None, group
        if group == 3:
            return _SYS_V7_TO_LEGACY.get(index), group
        return None, group

    if 1 <= layer_value <= 255:
        return layer_value, None
    return None, None


def _legacy_v8_physical_stack(raw_record: dict[str, object]) -> list[dict[str, object]]:
    """
    Return the ordered physical stack from legacy LAYER_V8_* records.
    """
    if not raw_record:
        return []

    indices = sorted(
        {
            int(match.group(1))
            for key in raw_record
            for match in [re.fullmatch(r"LAYER_V8_(\d+)NAME", key)]
            if match
        }
    )
    if not indices:
        return []

    entries: list[dict[str, object]] = []
    for index in indices:
        prefix = f"LAYER_V8_{index}"
        name = str(raw_record.get(f"{prefix}NAME", "") or "").strip()
        if not name:
            continue

        legacy_id, group = _legacy_id_from_saved_layer_id(
            _parse_altium_int_token(raw_record.get(f"{prefix}LAYERID"))
        )
        normalized_name = _normalize_layer_token(name)
        is_dielectric = normalized_name.startswith("DIELECTRIC")
        is_signal_or_plane = group in {0, 1}
        is_physical = is_dielectric or (
            legacy_id
            in {
                PcbLayer.TOP_OVERLAY.value,
                PcbLayer.BOTTOM_OVERLAY.value,
                PcbLayer.TOP_PASTE.value,
                PcbLayer.BOTTOM_PASTE.value,
                PcbLayer.TOP_SOLDER.value,
                PcbLayer.BOTTOM_SOLDER.value,
            }
            or legacy_id == PcbLayer.TOP.value
            or legacy_id == PcbLayer.BOTTOM.value
            or (
                legacy_id is not None
                and PcbLayer.TOP.value < legacy_id < PcbLayer.BOTTOM.value
            )
            or (
                legacy_id is not None
                and PcbLayer.INTERNAL_PLANE_1.value
                <= legacy_id
                <= PcbLayer.INTERNAL_PLANE_16.value
            )
            or is_signal_or_plane
        )
        if not is_physical:
            continue

        thickness_mils = 0.0
        if legacy_id in {
            PcbLayer.TOP_OVERLAY.value,
            PcbLayer.BOTTOM_OVERLAY.value,
            PcbLayer.TOP_PASTE.value,
            PcbLayer.BOTTOM_PASTE.value,
        }:
            thickness_mils = 0.0
        elif legacy_id in {PcbLayer.TOP_SOLDER.value, PcbLayer.BOTTOM_SOLDER.value}:
            thickness_mils = (
                _raw_mils_token(raw_record.get(f"{prefix}DIELHEIGHT")) or 0.4
            )
        elif is_dielectric:
            thickness_mils = (
                _raw_mils_token(raw_record.get(f"{prefix}DIELHEIGHT")) or 0.0
            )
        else:
            thickness_mils = _raw_mils_token(raw_record.get(f"{prefix}COPTHICK")) or 0.0
            if thickness_mils <= 0.0 and legacy_id is not None:
                thickness_mils = (
                    _raw_mils_token(raw_record.get(f"LAYER{legacy_id}COPTHICK")) or 0.0
                )

        material = (
            str(raw_record.get(f"{prefix}DIELMATERIAL", "") or "").strip() or None
        )
        entries.append(
            {
                "name": name,
                "legacy_id": legacy_id,
                "group": group,
                "thickness_mils": thickness_mils,
                "material": material,
            }
        )

    return entries


def _legacy_prev_next_dielectric_names(gap_count: int) -> list[str]:
    """
    Return native legacy dielectric display names for a PREV/NEXT stack.

        Old pre-V8 boards expose only the conductive-layer chain. Native IPC names
        the first dielectric from the top as `Dielectric1`, then numbers the
        remaining gaps from the bottom upwards (`Dielectric3`, `Dielectric2` on
        common four-layer boards).
    """
    if gap_count <= 0:
        return []
    names = ["Dielectric1"]
    for number in range(gap_count, 1, -1):
        names.append(f"Dielectric{number}")
    return names


def _legacy_prev_next_physical_stack(
    board: Any,
    raw_record: dict[str, object],
) -> list[dict[str, object]]:
    """
    Return ordered physical layers from legacy LAYER{n}PREV/NEXT links.
    """
    if not raw_record:
        return []

    top_id = PcbLayer.TOP.value
    bottom_id = PcbLayer.BOTTOM.value
    conductive_ids: list[int] = []
    seen: set[int] = set()
    current_id = top_id

    while current_id and current_id not in seen:
        name = str(raw_record.get(f"LAYER{current_id}NAME", "") or "").strip()
        cop_key = f"LAYER{current_id}COPTHICK"
        if not name and cop_key not in raw_record:
            return []

        seen.add(current_id)
        conductive_ids.append(current_id)
        if current_id == bottom_id:
            break

        next_id = _parse_altium_int_token(raw_record.get(f"LAYER{current_id}NEXT")) or 0
        if next_id <= 0:
            return []
        current_id = next_id

    if (
        not conductive_ids
        or conductive_ids[0] != top_id
        or conductive_ids[-1] != bottom_id
    ):
        return []

    if len(conductive_ids) == 2:
        # A simple top/bottom chain is already handled well by the generic
        # legacy fallback; keep this path for boards that expose real inner
        # conductive ordering only.
        return []

    entries: list[dict[str, object]] = []
    top_surface_layers = (
        (PcbLayer.TOP_PASTE.value, 0.0),
        (PcbLayer.TOP_OVERLAY.value, 0.0),
        (PcbLayer.TOP_SOLDER.value, 0.4),
    )
    for legacy_id, thickness_mils in top_surface_layers:
        entries.append(
            {
                "name": _layer_display_name(legacy_id, board),
                "legacy_id": legacy_id,
                "group": None,
                "thickness_mils": thickness_mils,
                "material": None,
            }
        )

    dielectric_names = _legacy_prev_next_dielectric_names(len(conductive_ids) - 1)
    for index, legacy_id in enumerate(conductive_ids):
        entries.append(
            {
                "name": _layer_display_name(legacy_id, board),
                "legacy_id": legacy_id,
                "group": None,
                "thickness_mils": _raw_mils_token(
                    raw_record.get(f"LAYER{legacy_id}COPTHICK")
                )
                or 0.0,
                "material": None,
            }
        )

        if index >= len(dielectric_names):
            continue

        dielectric_name = dielectric_names[index]
        dielectric_material = (
            str(raw_record.get(f"LAYER{legacy_id}DIELMATERIAL", "") or "").strip()
            or None
        )
        entries.append(
            {
                "name": dielectric_name,
                "legacy_id": None,
                "group": None,
                "thickness_mils": _raw_mils_token(
                    raw_record.get(f"LAYER{legacy_id}DIELHEIGHT")
                )
                or 0.0,
                "material": dielectric_material,
            }
        )

    bottom_surface_layers = (
        (PcbLayer.BOTTOM_SOLDER.value, 0.4),
        (PcbLayer.BOTTOM_OVERLAY.value, 0.0),
        (PcbLayer.BOTTOM_PASTE.value, 0.0),
    )
    for legacy_id, thickness_mils in bottom_surface_layers:
        entries.append(
            {
                "name": _layer_display_name(legacy_id, board),
                "legacy_id": legacy_id,
                "group": None,
                "thickness_mils": thickness_mils,
                "material": None,
            }
        )

    return entries


def _legacy_enabled_mechanical_layers(board: Any) -> tuple[tuple[int | None, str], ...]:
    """
    Return enabled mechanical/document layer names from raw legacy flags.
    """
    raw_record = getattr(board, "raw_record", {}) or {}
    enabled: list[tuple[int | None, str]] = []

    for legacy_id in range(
        PcbLayer.MECHANICAL_1.value, PcbLayer.MECHANICAL_16.value + 1
    ):
        key = f"LAYER{legacy_id}MECHENABLED"
        if str(raw_record.get(key, "") or "").strip().upper() != "TRUE":
            continue
        enabled.append((legacy_id, _layer_display_name(legacy_id, board)))

    for index in range(16):
        key = f"LAYERV7_{index}MECHENABLED"
        if str(raw_record.get(key, "") or "").strip().upper() != "TRUE":
            continue
        name = str(raw_record.get(f"LAYERV7_{index}NAME", "") or "").strip()
        if not name:
            name = f"Mechanical {17 + index}"
        enabled.append((None, name))

    return tuple(enabled)


def _substack_fields_from_board(board: Any) -> list[dict[str, object]]:
    substacks = list(getattr(board, "substacks", ()) or ())
    return [
        {
            "index": int(getattr(substack, "index", index) or index),
            "field_family": str(getattr(substack, "field_family", "") or ""),
            "source_stackup_ref": str(
                getattr(substack, "source_stackup_ref", "") or ""
            ),
            "name": str(getattr(substack, "name", "") or f"Board Layer Stack {index}"),
            "is_flex": getattr(substack, "is_flex", None),
            "show_top_dielectric": getattr(substack, "show_top_dielectric", None),
            "show_bottom_dielectric": getattr(substack, "show_bottom_dielectric", None),
            "service_stackup": getattr(substack, "service_stackup", None),
            "used_by_primitives": getattr(substack, "used_by_primitives", None),
            "raw_stackup_type": str(getattr(substack, "raw_stackup_type", "") or ""),
        }
        for index, substack in enumerate(substacks)
    ]


def _substack_context_value(
    board: Any,
    *,
    source_stackup_ref: str,
    layer_index: int,
) -> int | None:
    substack_layer_context_value = getattr(board, "substack_layer_context_value", None)
    if callable(substack_layer_context_value):
        return _optional_int(
            substack_layer_context_value(
                source_stackup_ref=source_stackup_ref,
                layer_index=layer_index,
            )
        )
    return None


def _filter_substack_layers(
    board: Any,
    *,
    source_stackup_ref: str,
    base_layers: tuple[ResolvedLayer, ...],
) -> tuple[ResolvedLayer, ...]:
    if not source_stackup_ref:
        return base_layers

    filtered: list[ResolvedLayer] = []
    found_context = False
    for fallback_index, layer in enumerate(base_layers):
        layer_index = (
            layer.stack_index if layer.stack_index is not None else fallback_index
        )
        context_value = _substack_context_value(
            board,
            source_stackup_ref=source_stackup_ref,
            layer_index=layer_index,
        )
        if context_value is not None:
            found_context = True
        if context_value not in (None, 0):
            continue
        filtered.append(layer)
    if filtered or found_context:
        return tuple(filtered)
    return base_layers


@dataclass
class _ResolvedLayerSeed:
    layer_names: list[str] = field(default_factory=list)
    layer_id_map: dict[int, str] = field(default_factory=dict)
    layer_v9_group: dict[str, int] = field(default_factory=dict)
    top_layer_name: str = "Top Layer"
    bottom_layer_name: str = "Bottom Layer"
    legacy_stack_index: dict[str, int] = field(default_factory=dict)
    legacy_thickness_mils: dict[str, float] = field(default_factory=dict)
    legacy_material_by_name: dict[str, str] = field(default_factory=dict)


def _empty_resolved_layer_stack() -> ResolvedLayerStack:
    return ResolvedLayerStack(
        layers=(),
        layer_names=(),
        legacy_id_to_name={},
        standard_layer_names={},
        v9_group_by_name={},
        top_layer_name="Top Layer",
        bottom_layer_name="Bottom Layer",
        inner_signal_layers=(),
        mechanical_layer_names=(),
        drill_pairs=(),
        drill_pair_layer_names={},
        substacks=(),
        board_region_contexts=(),
    )


def _build_legacy_conductive_sequence(
    primitive_layer_ids: set[int],
    drill_pairs: set[tuple[int, int]],
) -> list[int]:
    conductive_ids = {
        PcbLayer.TOP.value,
        PcbLayer.BOTTOM.value,
    }
    conductive_ids.update(
        lid
        for lid in primitive_layer_ids
        if PcbLayer.TOP.value < lid < PcbLayer.BOTTOM.value
    )
    for start_id, end_id in drill_pairs:
        if PcbLayer.TOP.value <= start_id <= PcbLayer.BOTTOM.value:
            conductive_ids.add(start_id)
        if PcbLayer.TOP.value <= end_id <= PcbLayer.BOTTOM.value:
            conductive_ids.add(end_id)
    conductive_sequence = sorted(
        lid
        for lid in conductive_ids
        if PcbLayer.TOP.value <= lid <= PcbLayer.BOTTOM.value
    )
    if conductive_sequence:
        return conductive_sequence
    return [PcbLayer.TOP.value, PcbLayer.BOTTOM.value]


def _populate_seed_from_legacy_physical_stack(
    seed: _ResolvedLayerSeed,
    legacy_physical_stack: list[dict[str, object]],
) -> None:
    for stack_index, entry in enumerate(legacy_physical_stack):
        layer_name = str(entry["name"])
        seed.layer_names.append(layer_name)
        seed.legacy_stack_index[layer_name] = stack_index
        seed.legacy_thickness_mils[layer_name] = _optional_float(
            entry.get("thickness_mils")
        )

        legacy_id = _optional_int(entry.get("legacy_id"))
        if legacy_id is not None:
            seed.layer_id_map[legacy_id] = layer_name
            if legacy_id == PcbLayer.TOP.value:
                seed.top_layer_name = layer_name
            elif legacy_id == PcbLayer.BOTTOM.value:
                seed.bottom_layer_name = layer_name

        group = _optional_int(entry.get("group"))
        if group is not None:
            seed.layer_v9_group[layer_name] = group

        material = entry.get("material")
        if material:
            seed.legacy_material_by_name[layer_name] = str(material)


def _append_legacy_surface_layers(
    seed: _ResolvedLayerSeed,
    *,
    layer_defs: tuple[tuple[int, str, float], ...],
) -> None:
    for legacy_id, layer_name, thickness_mils in layer_defs:
        seed.layer_names.append(layer_name)
        seed.layer_id_map[legacy_id] = layer_name
        seed.legacy_thickness_mils[layer_name] = thickness_mils


def _populate_seed_from_legacy_generic_fallback(
    seed: _ResolvedLayerSeed,
    *,
    board: Any,
    raw_record: dict[str, object],
    conductive_sequence: list[int],
) -> None:
    seed.top_layer_name = _layer_display_name(PcbLayer.TOP.value, board)
    seed.bottom_layer_name = _layer_display_name(PcbLayer.BOTTOM.value, board)

    _append_legacy_surface_layers(
        seed,
        layer_defs=(
            (
                PcbLayer.TOP_PASTE.value,
                _layer_display_name(PcbLayer.TOP_PASTE.value, board),
                0.0,
            ),
            (
                PcbLayer.TOP_OVERLAY.value,
                _layer_display_name(PcbLayer.TOP_OVERLAY.value, board),
                0.0,
            ),
            (
                PcbLayer.TOP_SOLDER.value,
                _layer_display_name(PcbLayer.TOP_SOLDER.value, board),
                0.4,
            ),
        ),
    )

    for index, legacy_id in enumerate(conductive_sequence):
        layer_name = _layer_display_name(legacy_id, board)
        seed.layer_names.append(layer_name)
        seed.layer_id_map[legacy_id] = layer_name
        seed.legacy_thickness_mils[layer_name] = (
            _raw_mils_token(raw_record.get(f"LAYER{legacy_id}COPTHICK")) or 0.0
        )
        if index >= len(conductive_sequence) - 1:
            continue
        dielectric_name = f"Dielectric{index + 1}"
        seed.layer_names.append(dielectric_name)
        seed.legacy_thickness_mils[dielectric_name] = (
            _raw_mils_token(raw_record.get(f"LAYER{legacy_id}DIELHEIGHT")) or 0.0
        )
        dielectric_material = str(
            raw_record.get(f"LAYER{legacy_id}DIELMATERIAL", "") or ""
        ).strip()
        if dielectric_material:
            seed.legacy_material_by_name[dielectric_name] = dielectric_material

    _append_legacy_surface_layers(
        seed,
        layer_defs=(
            (
                PcbLayer.BOTTOM_SOLDER.value,
                _layer_display_name(PcbLayer.BOTTOM_SOLDER.value, board),
                0.4,
            ),
            (
                PcbLayer.BOTTOM_OVERLAY.value,
                _layer_display_name(PcbLayer.BOTTOM_OVERLAY.value, board),
                0.0,
            ),
            (
                PcbLayer.BOTTOM_PASTE.value,
                _layer_display_name(PcbLayer.BOTTOM_PASTE.value, board),
                0.0,
            ),
        ),
    )

    for stack_index, layer_name in enumerate(seed.layer_names):
        seed.legacy_stack_index[layer_name] = stack_index


def _build_base_layer_seed(
    board: Any,
    *,
    primitive_layer_ids: set[int],
    drill_pairs: set[tuple[int, int]],
    v9_stack: list[Any],
) -> _ResolvedLayerSeed:
    seed = _ResolvedLayerSeed()
    if v9_stack:
        seed.layer_names.extend(v9.name for v9 in v9_stack if v9.name)
        return seed

    raw_record = getattr(board, "raw_record", {}) or {}
    legacy_v8_stack = _legacy_v8_physical_stack(raw_record)
    legacy_prev_next_stack: list[dict[str, object]] = []
    if not legacy_v8_stack:
        legacy_prev_next_stack = _legacy_prev_next_physical_stack(board, raw_record)
    legacy_physical_stack = legacy_v8_stack or legacy_prev_next_stack
    if legacy_physical_stack:
        _populate_seed_from_legacy_physical_stack(seed, legacy_physical_stack)
        return seed

    _populate_seed_from_legacy_generic_fallback(
        seed,
        board=board,
        raw_record=raw_record,
        conductive_sequence=_build_legacy_conductive_sequence(
            primitive_layer_ids, drill_pairs
        ),
    )
    return seed


def _apply_v9_layer_mappings(
    seed: _ResolvedLayerSeed,
    *,
    v9_stack: list[Any],
) -> None:
    for v9 in v9_stack:
        if not v9.name or not v9.layer_id:
            continue
        legacy_id, group = _legacy_id_from_saved_layer_id(int(v9.layer_id))
        if group is not None:
            seed.layer_v9_group[v9.name] = group
        if legacy_id == PcbLayer.BOTTOM.value:
            seed.bottom_layer_name = v9.name
        elif legacy_id == PcbLayer.TOP.value:
            seed.top_layer_name = v9.name
        if legacy_id is not None and v9.name in seed.layer_names:
            seed.layer_id_map[legacy_id] = v9.name


def _apply_legacy_name_mappings(seed: _ResolvedLayerSeed) -> None:
    name_to_legacy: dict[str, int] = {
        "Top Layer": PcbLayer.TOP.value,
        "Bottom Layer": PcbLayer.BOTTOM.value,
        "Top Overlay": PcbLayer.TOP_OVERLAY.value,
        "Bottom Overlay": PcbLayer.BOTTOM_OVERLAY.value,
        "Top Paste": PcbLayer.TOP_PASTE.value,
        "Bottom Paste": PcbLayer.BOTTOM_PASTE.value,
        "Top Solder": PcbLayer.TOP_SOLDER.value,
        "Bottom Solder": PcbLayer.BOTTOM_SOLDER.value,
    }
    for i in range(1, 31):
        name_to_legacy[f"Mid-Layer {i}"] = PcbLayer.TOP.value + i
    for i in range(1, 17):
        name_to_legacy[f"Internal Plane {i}"] = PcbLayer.INTERNAL_PLANE_1.value + i - 1
    for layer_name in seed.layer_names:
        legacy_id = name_to_legacy.get(layer_name)
        if legacy_id is not None:
            seed.layer_id_map[legacy_id] = layer_name


def _resolve_mechanical_layer_name(
    board: Any,
    *,
    v9_layer_cache: dict[int, str],
    legacy_id: int,
) -> str:
    v7_key = 0x01020000 + (legacy_id - PcbLayer.MECHANICAL_1.value + 1)
    if v9_layer_cache:
        name = v9_layer_cache.get(v7_key)
        if name:
            return name
    return _layer_display_name(legacy_id, board)


def _append_primitive_backed_extra_layers(
    seed: _ResolvedLayerSeed,
    *,
    board: Any,
    primitive_layer_ids: set[int],
    v9_stack: list[Any],
    v9_layer_cache: dict[int, str],
) -> None:
    skip_layer_ids = {
        PcbLayer.MULTI_LAYER.value,
        PcbLayer.DRILL_DRAWING.value,
        PcbLayer.DRILL_GUIDE.value,
    }
    if not v9_stack:
        skip_layer_ids.update(
            range(PcbLayer.MECHANICAL_1.value, PcbLayer.MECHANICAL_16.value + 1)
        )
    extra_layers: list[str] = []
    for lid in sorted(primitive_layer_ids):
        if lid in seed.layer_id_map or lid in skip_layer_ids:
            continue
        if PcbLayer.MECHANICAL_1.value <= lid <= PcbLayer.MECHANICAL_16.value:
            display = _resolve_mechanical_layer_name(
                board, v9_layer_cache=v9_layer_cache, legacy_id=lid
            )
        else:
            display = _layer_display_name(lid, board)
        if display.startswith("Unknown"):
            continue
        seed.layer_id_map[lid] = display
        extra_layers.append(display)
        if PcbLayer.MECHANICAL_1.value <= lid <= PcbLayer.MECHANICAL_16.value:
            seed.layer_v9_group[display] = 2
    seed.layer_names.extend(extra_layers)


def _append_required_mechanical_layers(
    seed: _ResolvedLayerSeed,
    *,
    board: Any,
    v9_layer_cache: dict[int, str],
    enabled_mechanical_v7_save_ids: tuple[int, ...],
) -> None:
    keepout_name = "Keep-Out Layer"
    if keepout_name not in seed.layer_names:
        seed.layer_names.append(keepout_name)
    seed.layer_id_map[PcbLayer.KEEPOUT.value] = keepout_name

    mech1_name = _resolve_mechanical_layer_name(
        board,
        v9_layer_cache=v9_layer_cache,
        legacy_id=PcbLayer.MECHANICAL_1.value,
    )
    if mech1_name not in seed.layer_names:
        seed.layer_names.append(mech1_name)
    seed.layer_id_map[PcbLayer.MECHANICAL_1.value] = mech1_name
    seed.layer_v9_group[mech1_name] = 2

    for v7_id in enabled_mechanical_v7_save_ids:
        if ((v7_id >> 16) & 0xFF) != 2:
            continue
        mech_num = v7_id & 0xFFFF
        display = v9_layer_cache.get(v7_id) if v9_layer_cache else None
        if not display:
            display = f"Mechanical {mech_num}"
        if display not in seed.layer_names:
            seed.layer_names.append(display)
            seed.layer_v9_group[display] = 2
        if 1 <= mech_num <= 16:
            key = PcbLayer.MECHANICAL_1.value + mech_num - 1
            seed.layer_id_map.setdefault(key, display)

    for legacy_id, display in _legacy_enabled_mechanical_layers(board):
        if display not in seed.layer_names:
            seed.layer_names.append(display)
            seed.layer_v9_group[display] = 2
        if legacy_id is not None:
            seed.layer_id_map.setdefault(legacy_id, display)


def _build_drill_pair_metadata(
    board: object,
    *,
    drill_pairs: set[tuple[int, int]],
    layer_pairs: tuple[object, ...],
    layer_names: list[str],
    layer_id_map: dict[int, str],
    v9_stack: list[object],
    v9_layer_cache: dict[int, str],
) -> tuple[dict[tuple[int, int], tuple[str, str]], list[ResolvedDrillPair]]:
    drill_pairs.add((PcbLayer.TOP.value, PcbLayer.BOTTOM.value))
    source_pairs = _source_drill_pair_seeds(layer_pairs, layer_id_map, v9_stack)
    exact_pairs = dict(source_pairs)
    for key, value in _legacy_drill_pair_seeds(
        drill_pairs, layer_id_map, v9_stack
    ).items():
        start_ref, end_ref, _, _ = key
        if any(
            candidate[0] == start_ref
            and candidate[1] == end_ref
            and candidate[2] is False
            for candidate in source_pairs
        ):
            continue
        exact_pairs[key] = value

    drill_pair_layer_names: dict[tuple[int, int], tuple[str, str]] = {}
    drill_pair_items: list[ResolvedDrillPair] = []
    for (start_ref, end_ref, _, _), (substack_refs, is_backdrill) in sorted(
        exact_pairs.items(), key=_drill_pair_order
    ):
        item = _resolved_drill_pair(
            start_ref,
            end_ref,
            substack_refs=substack_refs,
            is_backdrill=is_backdrill,
            board=board,
            layer_id_map=layer_id_map,
            v9_stack=v9_stack,
            v9_layer_cache=v9_layer_cache,
        )
        _append_unique(layer_names, item.drawing_layer_name)
        _append_unique(layer_names, item.guide_layer_name)
        if item.start_legacy_id and item.end_legacy_id:
            legacy_span = (
                min(item.start_legacy_id, item.end_legacy_id),
                max(item.start_legacy_id, item.end_legacy_id),
            )
            drill_pair_layer_names[legacy_span] = (
                item.drawing_layer_name,
                item.guide_layer_name,
            )
        drill_pair_items.append(item)
    return drill_pair_layer_names, drill_pair_items


def _legacy_drill_pair_seeds(
    drill_pairs: set[tuple[int, int]],
    layer_id_map: dict[int, str],
    v9_stack: list[object],
) -> dict[
    tuple[PcbLayerRef, PcbLayerRef, bool, tuple[str, ...]],
    tuple[tuple[str, ...], bool],
]:
    result: dict[
        tuple[PcbLayerRef, PcbLayerRef, bool, tuple[str, ...]],
        tuple[tuple[str, ...], bool],
    ] = {}
    for start_id, end_id in sorted(drill_pairs):
        start_ref = PcbLayerRef.from_legacy(start_id)
        end_ref = PcbLayerRef.from_legacy(end_id)
        start_ref, end_ref = _ordered_drill_endpoints(start_ref, end_ref)
        _require_drill_endpoint(start_ref, layer_id_map, v9_stack)
        _require_drill_endpoint(end_ref, layer_id_map, v9_stack)
        result[(start_ref, end_ref, False, ())] = ((), False)
    return result


def _source_drill_pair_seeds(
    layer_pairs: tuple[object, ...],
    layer_id_map: dict[int, str],
    v9_stack: list[object],
) -> dict[
    tuple[PcbLayerRef, PcbLayerRef, bool, tuple[str, ...]],
    tuple[tuple[str, ...], bool],
]:
    result: dict[
        tuple[PcbLayerRef, PcbLayerRef, bool, tuple[str, ...]],
        tuple[tuple[str, ...], bool],
    ] = {}
    for pair in layer_pairs:
        try:
            start_ref = PcbLayerRef.parse(
                str(getattr(pair, "low_layer_token", "") or "")
            )
            end_ref = PcbLayerRef.parse(
                str(getattr(pair, "high_layer_token", "") or "")
            )
        except PcbLayerResolutionError as exc:
            raise ValueError(
                "layer pair contains an unresolved endpoint token"
            ) from exc
        start_ref, end_ref = _ordered_drill_endpoints(start_ref, end_ref)
        _require_drill_endpoint(start_ref, layer_id_map, v9_stack)
        _require_drill_endpoint(end_ref, layer_id_map, v9_stack)
        substack_refs = tuple(getattr(pair, "source_substack_refs", ()) or ())
        is_backdrill = bool(getattr(pair, "is_backdrill", None))
        result[(start_ref, end_ref, is_backdrill, substack_refs)] = (
            substack_refs,
            is_backdrill,
        )
    return result


def _resolved_drill_pair(
    start_ref: PcbLayerRef,
    end_ref: PcbLayerRef,
    *,
    substack_refs: tuple[str, ...],
    is_backdrill: bool,
    board: object,
    layer_id_map: dict[int, str],
    v9_stack: list[object],
    v9_layer_cache: dict[int, str],
) -> ResolvedDrillPair:
    start_name = _drill_endpoint_display_name(
        start_ref,
        board=board,
        layer_id_map=layer_id_map,
        v9_stack=v9_stack,
        v9_layer_cache=v9_layer_cache,
    )
    end_name = _drill_endpoint_display_name(
        end_ref,
        board=board,
        layer_id_map=layer_id_map,
        v9_stack=v9_stack,
        v9_layer_cache=v9_layer_cache,
    )
    span = f"{'[BD] ' if is_backdrill else ''}{start_name} - {end_name}"
    return ResolvedDrillPair(
        start_legacy_id=start_ref.legacy_layer_id or 0,
        end_legacy_id=end_ref.legacy_layer_id or 0,
        start_layer_name=start_name,
        end_layer_name=end_name,
        drawing_layer_name=f"Drill Drawing ({span})",
        guide_layer_name=f"Drill Guide ({span})",
        is_backdrill=is_backdrill,
        substack_refs=substack_refs,
        start_layer_ref=start_ref,
        end_layer_ref=end_ref,
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _drill_pair_order(
    item: tuple[
        tuple[PcbLayerRef, PcbLayerRef, bool, tuple[str, ...]],
        tuple[tuple[str, ...], bool],
    ],
) -> tuple[int, str, int, str, bool, tuple[str, ...]]:
    start_ref, end_ref, is_backdrill, substack_refs = item[0]
    return (
        _drill_endpoint_order(start_ref),
        start_ref.token,
        _drill_endpoint_order(end_ref),
        end_ref.token,
        is_backdrill,
        substack_refs,
    )


def _ordered_drill_endpoints(
    start_ref: PcbLayerRef,
    end_ref: PcbLayerRef,
) -> tuple[PcbLayerRef, PcbLayerRef]:
    if _drill_endpoint_order(end_ref) < _drill_endpoint_order(start_ref):
        return end_ref, start_ref
    return start_ref, end_ref


def _drill_endpoint_order(layer_ref: PcbLayerRef) -> int:
    if layer_ref.token == "TOP":
        return 0
    if layer_ref.token.startswith("MID") and layer_ref.number is not None:
        return layer_ref.number
    if layer_ref.token == "BOTTOM":
        return 127
    if layer_ref.legacy_layer_id is not None:
        return 1_000 + layer_ref.legacy_layer_id
    if layer_ref.v7_saved_layer_id is not None:
        return 10_000 + layer_ref.v7_saved_layer_id
    return 2**63 - 1


def _require_drill_endpoint(
    layer_ref: PcbLayerRef,
    layer_id_map: dict[int, str],
    v9_stack: list[object],
) -> None:
    if layer_ref.family not in {
        PcbLayerFamily.SIGNAL,
        PcbLayerFamily.INTERNAL_PLANE,
    }:
        raise ValueError(f"drill endpoint {layer_ref.token} is not a copper layer")
    saved_id = layer_ref.v7_saved_layer_id
    if v9_stack and saved_id is not None:
        if any(int(getattr(row, "layer_id", 0) or 0) == saved_id for row in v9_stack):
            return
        raise ValueError(
            f"drill endpoint {layer_ref.token} is absent from the V9 stack"
        )
    legacy_id = layer_ref.legacy_layer_id
    if legacy_id is not None and legacy_id in layer_id_map:
        return
    raise ValueError(f"drill endpoint {layer_ref.token} is absent from the layer stack")


def _drill_endpoint_display_name(
    layer_ref: PcbLayerRef,
    *,
    board: object,
    layer_id_map: dict[int, str],
    v9_stack: list[object],
    v9_layer_cache: dict[int, str],
) -> str:
    legacy_id = layer_ref.legacy_layer_id
    if legacy_id is not None:
        return layer_id_map.get(legacy_id, _layer_display_name(legacy_id, board))
    saved_id = layer_ref.v7_saved_layer_id
    for row in v9_stack:
        if saved_id is not None and int(getattr(row, "layer_id", 0) or 0) == saved_id:
            display_name = str(getattr(row, "name", "") or "")
            if display_name:
                return display_name
    if saved_id is not None and saved_id in v9_layer_cache:
        return v9_layer_cache[saved_id]
    return layer_ref.token


def _dedupe_layer_names(layer_names: list[str]) -> list[str]:
    deduped_layer_names: list[str] = []
    seen: set[str] = set()
    for layer_name in layer_names:
        if layer_name in seen:
            continue
        seen.add(layer_name)
        deduped_layer_names.append(layer_name)
    return deduped_layer_names


def _collect_inner_signal_layers(
    *,
    v9_stack: list[Any],
    layer_names: list[str],
    layer_id_map: dict[int, str],
) -> list[str]:
    inner_signal_layers: list[str] = []
    if v9_stack:
        in_signal_range = False
        for v9 in v9_stack:
            if not v9.name or not v9.layer_id:
                continue
            if ((v9.layer_id >> 16) & 0xFF) != 0:
                continue
            idx = v9.layer_id & 0xFFFF
            if idx == 1:
                in_signal_range = True
                continue
            if idx == 0xFFFF:
                break
            if in_signal_range and v9.name in layer_names:
                inner_signal_layers.append(v9.name)
        return inner_signal_layers

    for lid in range(PcbLayer.TOP.value + 1, PcbLayer.BOTTOM.value):
        layer_name = layer_id_map.get(lid)
        if (
            layer_name
            and layer_name in layer_names
            and layer_name not in inner_signal_layers
        ):
            inner_signal_layers.append(layer_name)
    return inner_signal_layers


def _build_resolved_layer_metadata(
    seed: _ResolvedLayerSeed,
    *,
    v9_stack: list[Any],
    v9_layer_cache: dict[int, str],
) -> tuple[list[ResolvedLayer], dict[str, str], tuple[str, ...]]:
    name_to_legacy: dict[str, int] = {}
    for legacy_id, name in seed.layer_id_map.items():
        name_to_legacy.setdefault(name, legacy_id)

    v9_reverse_name: dict[str, int] = {}
    if v9_layer_cache:
        for v7_id, name in v9_layer_cache.items():
            v9_reverse_name.setdefault(name, v7_id)

    v9_stack_index: dict[str, int] = {}
    v9_layer_id_by_name: dict[str, int] = {}
    v9_thickness_mils: dict[str, float] = {}
    v9_material_by_name: dict[str, str] = {}
    if v9_stack:
        for v9 in v9_stack:
            if not v9.name or v9.name in v9_stack_index:
                continue
            v9_stack_index[v9.name] = v9.stack_index
            if v9.layer_id:
                v9_layer_id_by_name[v9.name] = int(v9.layer_id)
            if v9.copper_thickness > 0:
                v9_thickness_mils[v9.name] = v9.copper_thickness
            elif v9.diel_height > 0:
                v9_thickness_mils[v9.name] = v9.diel_height
            else:
                v9_thickness_mils[v9.name] = 0.0
            if v9.diel_material:
                v9_material_by_name[v9.name] = str(v9.diel_material)
    else:
        v9_stack_index.update(seed.legacy_stack_index)
        v9_thickness_mils.update(seed.legacy_thickness_mils)
        v9_material_by_name.update(seed.legacy_material_by_name)

    resolved_layers: list[ResolvedLayer] = []
    for layer_name in seed.layer_names:
        legacy_id = name_to_legacy.get(layer_name)
        v7_id = _legacy_to_v7_key(legacy_id) if legacy_id is not None else None
        if v7_id is None:
            v7_id = v9_reverse_name.get(layer_name) or v9_layer_id_by_name.get(
                layer_name
            )
        resolved_layers.append(
            ResolvedLayer(
                layer_key=_simple_layer_key(layer_name, legacy_id, v7_id),
                display_name=layer_name,
                legacy_id=legacy_id,
                v7_id=v7_id,
                v9_group=seed.layer_v9_group.get(layer_name),
                stack_index=v9_stack_index.get(layer_name),
                thickness_mils=v9_thickness_mils.get(layer_name, 0.0),
                material=v9_material_by_name.get(layer_name),
            )
        )

    standard_layer_names: dict[str, str] = {}
    for legacy_id, layer_name in seed.layer_id_map.items():
        token = _standard_layer_token(legacy_id)
        if token:
            standard_layer_names.setdefault(token, layer_name)
    for layer in resolved_layers:
        token = _standard_token_from_v7_key(layer.v7_id)
        if token:
            standard_layer_names.setdefault(token, layer.display_name)
    standard_layer_names.setdefault("TOP", seed.top_layer_name)
    standard_layer_names.setdefault("BOTTOM", seed.bottom_layer_name)

    mechanical_layer_names = tuple(
        layer.display_name
        for layer in resolved_layers
        if (
            layer.v9_group == 2
            or (
                layer.legacy_id is not None
                and PcbLayer.MECHANICAL_1.value
                <= layer.legacy_id
                <= PcbLayer.MECHANICAL_16.value
            )
        )
    )
    return resolved_layers, standard_layer_names, mechanical_layer_names


def _build_resolved_substacks(
    board: Any,
    *,
    resolved_layers: list[ResolvedLayer],
    drill_pair_items: list[ResolvedDrillPair],
) -> list[ResolvedSubstack]:
    physical_layers = tuple(
        layer for layer in resolved_layers if layer.stack_index is not None
    )
    substack_items: list[ResolvedSubstack] = []
    for substack_fields in _substack_fields_from_board(board):
        source_stackup_ref = str(
            substack_fields.get("source_stackup_ref", "") or ""
        ).strip()
        filtered_layers = _filter_substack_layers(
            board,
            source_stackup_ref=source_stackup_ref,
            base_layers=physical_layers,
        )
        filtered_drill_pairs = tuple(
            pair
            for pair in drill_pair_items
            if pair.substack_refs and source_stackup_ref in pair.substack_refs
        )
        substack_items.append(
            ResolvedSubstack(
                source_stackup_ref=source_stackup_ref,
                name=str(substack_fields.get("name", "") or ""),
                is_flex=_optional_bool(substack_fields.get("is_flex")),
                field_family=str(substack_fields.get("field_family", "") or ""),
                show_top_dielectric=_optional_bool(
                    substack_fields.get("show_top_dielectric")
                ),
                show_bottom_dielectric=_optional_bool(
                    substack_fields.get("show_bottom_dielectric")
                ),
                service_stackup=_optional_bool(substack_fields.get("service_stackup")),
                used_by_primitives=_optional_bool(
                    substack_fields.get("used_by_primitives")
                ),
                raw_stackup_type=str(substack_fields.get("raw_stackup_type", "") or ""),
                layers=filtered_layers,
                drill_pairs=filtered_drill_pairs,
            )
        )
    return substack_items


def _build_board_region_contexts(
    board_regions: list[object] | tuple[object, ...] | None,
    *,
    substack_items: list[ResolvedSubstack],
) -> list[ResolvedBoardRegionContext]:
    substack_by_ref = {
        item.source_stackup_ref: item
        for item in substack_items
        if item.source_stackup_ref
    }
    board_region_contexts: list[ResolvedBoardRegionContext] = []
    for region in list(board_regions or ()):
        layerstack_id = str(getattr(region, "layerstack_id", "") or "").strip()
        substack = substack_by_ref.get(layerstack_id)
        board_region_contexts.append(
            ResolvedBoardRegionContext(
                name=str(getattr(region, "name", "") or ""),
                layerstack_id=layerstack_id,
                substack_name=substack.name if substack is not None else "",
                is_flex=substack.is_flex if substack is not None else None,
                layer_names=substack.layer_names if substack is not None else (),
                locked_3d=bool(getattr(region, "locked_3d", False)),
                bend_line_count=int(getattr(region, "bending_line_count", 0) or 0),
            )
        )
    return board_region_contexts


def resolved_layer_stack_from_board(
    board: object,
    *,
    primitive_layer_ids: set[int] | None = None,
    drill_pairs: set[tuple[int, int]] | None = None,
    board_regions: list[object] | tuple[object, ...] | None = None,
) -> ResolvedLayerStack:
    """
    Build a fully resolved layer stack from board data plus optional evidence.
    """
    primitive_layer_ids = set(primitive_layer_ids or ())
    drill_pairs = set(drill_pairs or ())

    if board is None:
        return _empty_resolved_layer_stack()

    v9_stack = list(getattr(board, "v9_stack", []) or [])
    v9_layer_cache = dict(getattr(board, "v9_layer_cache", {}) or {})
    enabled_mechanical_v7_save_ids = tuple(
        getattr(board, "enabled_mechanical_v7_save_ids", ()) or ()
    )
    layer_pairs = tuple(getattr(board, "layer_pairs", ()) or ())
    seed = _build_base_layer_seed(
        board,
        primitive_layer_ids=primitive_layer_ids,
        drill_pairs=drill_pairs,
        v9_stack=v9_stack,
    )
    if v9_stack:
        _apply_v9_layer_mappings(seed, v9_stack=v9_stack)
    else:
        _apply_legacy_name_mappings(seed)

    _append_primitive_backed_extra_layers(
        seed,
        board=board,
        primitive_layer_ids=primitive_layer_ids,
        v9_stack=v9_stack,
        v9_layer_cache=v9_layer_cache,
    )
    _append_required_mechanical_layers(
        seed,
        board=board,
        v9_layer_cache=v9_layer_cache,
        enabled_mechanical_v7_save_ids=enabled_mechanical_v7_save_ids,
    )

    drill_pair_layer_names, drill_pair_items = _build_drill_pair_metadata(
        board,
        drill_pairs=drill_pairs,
        layer_pairs=layer_pairs,
        layer_names=seed.layer_names,
        layer_id_map=seed.layer_id_map,
        v9_stack=v9_stack,
        v9_layer_cache=v9_layer_cache,
    )
    seed.layer_names = _dedupe_layer_names(seed.layer_names)

    inner_signal_layers = _collect_inner_signal_layers(
        v9_stack=v9_stack,
        layer_names=seed.layer_names,
        layer_id_map=seed.layer_id_map,
    )
    resolved_layers, standard_layer_names, mechanical_layer_names = (
        _build_resolved_layer_metadata(
            seed,
            v9_stack=v9_stack,
            v9_layer_cache=v9_layer_cache,
        )
    )
    substack_items = _build_resolved_substacks(
        board,
        resolved_layers=resolved_layers,
        drill_pair_items=drill_pair_items,
    )
    board_region_contexts = _build_board_region_contexts(
        board_regions,
        substack_items=substack_items,
    )

    return ResolvedLayerStack(
        layers=tuple(resolved_layers),
        layer_names=tuple(seed.layer_names),
        legacy_id_to_name=seed.layer_id_map,
        standard_layer_names=standard_layer_names,
        v9_group_by_name=seed.layer_v9_group,
        top_layer_name=seed.top_layer_name,
        bottom_layer_name=seed.bottom_layer_name,
        inner_signal_layers=tuple(inner_signal_layers),
        mechanical_layer_names=mechanical_layer_names,
        drill_pairs=tuple(drill_pair_items),
        drill_pair_layer_names=drill_pair_layer_names,
        substacks=tuple(substack_items),
        board_region_contexts=tuple(board_region_contexts),
    )


def _resolved_layer_stack_from_layer_stack_document(
    document: object,
    *,
    primitive_layer_ids: set[int] | None = None,
    drill_pairs: set[tuple[int, int]] | None = None,
) -> ResolvedLayerStack:
    """
    Build the resolved consumer view from the source-aware layer-stack model.
    """
    from .altium_board import AltiumBoard

    source = getattr(document, "source", None)
    if source is None:
        return _empty_resolved_layer_stack()
    board_record = source.board_record_mapping()
    board = AltiumBoard.from_record(board_record)
    resolved = resolved_layer_stack_from_board(
        board,
        primitive_layer_ids=primitive_layer_ids,
        drill_pairs=drill_pairs,
        board_regions=getattr(document, "board_regions", None),
    )
    return _enrich_resolved_layers_from_document(resolved, document)


def _enrich_resolved_layers_from_document(
    resolved: ResolvedLayerStack,
    document: object,
) -> ResolvedLayerStack:
    """Retain source-aware registry identity in the resolved projection."""

    registry = getattr(document, "layer_registry", None)
    entries = tuple(getattr(registry, "entries", ()) or ())
    entries_by_v7 = {
        int(entry.v7_layer_id): entry
        for entry in entries
        if getattr(entry, "v7_layer_id", None) is not None
    }
    entries_by_legacy = {
        int(entry.legacy_layer_id): entry
        for entry in entries
        if getattr(entry, "legacy_layer_id", None) is not None
    }
    physical_by_stack_index: dict[int, object] = {}
    for stack in tuple(getattr(document, "physical_stacks", ()) or ()):
        for row in tuple(getattr(stack, "layers", ()) or ()):
            stack_index = getattr(row, "stack_index", None)
            if stack_index is not None:
                physical_by_stack_index[int(stack_index)] = row

    enriched: list[ResolvedLayer] = []
    for layer in resolved.layers:
        entry = entries_by_v7.get(layer.v7_id) if layer.v7_id is not None else None
        if entry is None and layer.legacy_id is not None:
            entry = entries_by_legacy.get(layer.legacy_id)
        physical_row = (
            physical_by_stack_index.get(layer.stack_index)
            if layer.stack_index is not None
            else None
        )
        registry_ref = str(
            getattr(physical_row, "registry_ref", "")
            or getattr(entry, "model_id", "")
            or ""
        )
        family = str(
            getattr(physical_row, "family", "") or getattr(entry, "family", "") or ""
        )
        enriched.append(
            replace(
                layer,
                layer_ref=_exact_layer_ref(layer),
                family=family,
                side=getattr(entry, "side", None),
                registry_ref=registry_ref,
                source_record_id=str(
                    getattr(physical_row, "source_record_id", "")
                    or getattr(entry, "source_record_id", "")
                    or ""
                ),
                physical_row=physical_row is not None,
            )
        )

    by_stack_index = {
        layer.stack_index: layer for layer in enriched if layer.stack_index is not None
    }
    by_key = {layer.layer_key: layer for layer in enriched}
    substacks = tuple(
        replace(
            substack,
            layers=tuple(
                _enriched_substack_layer(layer, by_stack_index, by_key)
                for layer in substack.layers
            ),
        )
        for substack in resolved.substacks
    )
    return replace(resolved, layers=tuple(enriched), substacks=substacks)


def _enriched_substack_layer(
    layer: ResolvedLayer,
    by_stack_index: dict[int, ResolvedLayer],
    by_key: dict[str, ResolvedLayer],
) -> ResolvedLayer:
    if layer.stack_index is not None:
        matched = by_stack_index.get(layer.stack_index)
        if matched is not None:
            return matched
    return by_key.get(layer.layer_key, layer)


def _exact_layer_ref(layer: ResolvedLayer) -> PcbLayerRef | None:
    if layer.v7_id is not None:
        return PcbLayerRef.from_v7_saved_layer_id(layer.v7_id)
    if layer.legacy_id is not None:
        return PcbLayerRef.from_legacy(layer.legacy_id)
    return None


def resolved_layer_stack_from_pcbdoc(pcbdoc: object) -> ResolvedLayerStack:
    """
    Build a fully resolved layer stack from parsed Altium PcbDoc data.
    """
    from .altium_layer_stack_document import AltiumLayerStackDocument

    board = getattr(pcbdoc, "board", None)
    if board is None:
        return _empty_resolved_layer_stack()

    document = AltiumLayerStackDocument.from_pcbdoc(pcbdoc)
    return _resolved_layer_stack_from_layer_stack_document(
        document,
        primitive_layer_ids=_collect_pcbdoc_primitive_layer_ids(pcbdoc),
        drill_pairs=_collect_pcbdoc_drill_pairs(pcbdoc),
    )
