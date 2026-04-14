"""
Resolved layer-stack model for IPC-first consumers.

This module centralizes Altium V9/V7/legacy layer resolution into one object
so downstream code can read stable IDs and friendly display names without
re-implementing layer decoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

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


def _normalize_layer_token(value: str) -> str:
    """
    Normalize display names/tokens for resilient lookup.
    """
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


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
        return PcbLayer.TOP.value + int(mid_match.group(1))

    plane_match = re.match(r"^(?:INTERNAL)?PLANE(\d+)$", normalized)
    if plane_match:
        return PcbLayer.INTERNAL_PLANE_1.value + int(plane_match.group(1)) - 1

    mech_match = re.match(r"^MECHANICAL(\d+)$", normalized)
    if mech_match:
        return PcbLayer.MECHANICAL_1.value + int(mech_match.group(1)) - 1

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
        token_map: dict[str, ResolvedLayer] = {}
        for layer in self.layers:
            token_map.setdefault(_normalize_layer_token(layer.display_name), layer)
            if layer.legacy_id is not None:
                legacy_map.setdefault(layer.legacy_id, layer)
                standard_token = _standard_layer_token(layer.legacy_id)
                if standard_token:
                    token_map.setdefault(standard_token, layer)
        self._layers_by_legacy = legacy_map
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
            return PcbLayer.TOP.value + index - 1, group
        if group == 1:
            return PcbLayer.INTERNAL_PLANE_1.value + index - 1, group
        if group == 2:
            return PcbLayer.MECHANICAL_1.value + index - 1, group
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
        return substack_layer_context_value(
            source_stackup_ref=source_stackup_ref,
            layer_index=layer_index,
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
        seed.legacy_thickness_mils[layer_name] = float(entry["thickness_mils"] or 0.0)

        legacy_id = entry["legacy_id"]
        if legacy_id is not None:
            seed.layer_id_map[legacy_id] = layer_name
            if legacy_id == PcbLayer.TOP.value:
                seed.top_layer_name = layer_name
            elif legacy_id == PcbLayer.BOTTOM.value:
                seed.bottom_layer_name = layer_name

        group = entry["group"]
        if group is not None:
            seed.layer_v9_group[layer_name] = int(group)

        material = entry["material"]
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
    board: Any,
    *,
    drill_pairs: set[tuple[int, int]],
    layer_pairs: tuple[Any, ...],
    layer_names: list[str],
    layer_id_map: dict[int, str],
) -> tuple[dict[tuple[int, int], tuple[str, str]], list[ResolvedDrillPair]]:
    drill_pairs.add((PcbLayer.TOP.value, PcbLayer.BOTTOM.value))
    pair_substack_refs_by_span: dict[tuple[int, int], tuple[str, ...]] = {}
    pair_backdrill_by_span: dict[tuple[int, int], bool] = {}
    for pair in layer_pairs:
        lo_id = _legacy_layer_id_from_token(
            str(getattr(pair, "low_layer_token", "") or "")
        )
        hi_id = _legacy_layer_id_from_token(
            str(getattr(pair, "high_layer_token", "") or "")
        )
        if lo_id is None or hi_id is None:
            continue
        pair_span = (min(lo_id, hi_id), max(lo_id, hi_id))
        drill_pairs.add(pair_span)
        substack_refs = tuple(getattr(pair, "source_substack_refs", ()) or ())
        if substack_refs:
            pair_substack_refs_by_span[pair_span] = substack_refs
        if getattr(pair, "is_backdrill", None):
            pair_backdrill_by_span[pair_span] = True

    drill_pair_layer_names: dict[tuple[int, int], tuple[str, str]] = {}
    drill_pair_items: list[ResolvedDrillPair] = []
    for start_id, end_id in sorted(drill_pairs):
        start_name = layer_id_map.get(start_id, _layer_display_name(start_id, board))
        end_name = layer_id_map.get(end_id, _layer_display_name(end_id, board))
        is_backdrill = pair_backdrill_by_span.get((start_id, end_id), False)
        span = f"{'[BD] ' if is_backdrill else ''}{start_name} - {end_name}"
        drawing_name = f"Drill Drawing ({span})"
        guide_name = f"Drill Guide ({span})"
        if drawing_name not in layer_names:
            layer_names.append(drawing_name)
        if guide_name not in layer_names:
            layer_names.append(guide_name)
        drill_pair_layer_names[(start_id, end_id)] = (drawing_name, guide_name)
        drill_pair_items.append(
            ResolvedDrillPair(
                start_legacy_id=start_id,
                end_legacy_id=end_id,
                start_layer_name=start_name,
                end_layer_name=end_name,
                drawing_layer_name=drawing_name,
                guide_layer_name=guide_name,
                is_backdrill=is_backdrill,
                substack_refs=pair_substack_refs_by_span.get((start_id, end_id), ()),
            )
        )
    return drill_pair_layer_names, drill_pair_items


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
    v9_thickness_mils: dict[str, float] = {}
    v9_material_by_name: dict[str, str] = {}
    if v9_stack:
        for v9 in v9_stack:
            if not v9.name or v9.name in v9_stack_index:
                continue
            v9_stack_index[v9.name] = v9.stack_index
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
            v7_id = v9_reverse_name.get(layer_name)
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
                is_flex=substack_fields.get("is_flex"),
                field_family=str(substack_fields.get("field_family", "") or ""),
                show_top_dielectric=substack_fields.get("show_top_dielectric"),
                show_bottom_dielectric=substack_fields.get("show_bottom_dielectric"),
                service_stackup=substack_fields.get("service_stackup"),
                used_by_primitives=substack_fields.get("used_by_primitives"),
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


def resolved_layer_stack_from_pcbdoc(pcbdoc: object) -> ResolvedLayerStack:
    """
    Build a fully resolved layer stack from parsed Altium PcbDoc data.
    """
    return resolved_layer_stack_from_board(
        getattr(pcbdoc, "board", None),
        primitive_layer_ids=_collect_pcbdoc_primitive_layer_ids(pcbdoc),
        drill_pairs=_collect_pcbdoc_drill_pairs(pcbdoc),
        board_regions=getattr(pcbdoc, "board_regions", None),
    )
