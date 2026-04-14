from __future__ import annotations

import re
from typing import Any

from .altium_pcb_enums import (
    PcbV7LayerPartition,
    PcbV7SavedLayerId,
    pcb_mechanical_layer_number_to_v7_saved_layer_id,
)
from .altium_record_types import PcbLayer
from .altium_resolved_layer_stack import legacy_layer_to_v7_save_id

# Standard top/bottom pairs always normalize to the top-side library form.
# Mechanical pairs only normalize when the source board explicitly defines a
# MECHPAIR relationship.
_BASE_LAYER_FLIP_MAP = {
    PcbLayer.TOP.value: PcbLayer.BOTTOM.value,
    PcbLayer.BOTTOM.value: PcbLayer.TOP.value,
    PcbLayer.TOP_OVERLAY.value: PcbLayer.BOTTOM_OVERLAY.value,
    PcbLayer.BOTTOM_OVERLAY.value: PcbLayer.TOP_OVERLAY.value,
    PcbLayer.TOP_PASTE.value: PcbLayer.BOTTOM_PASTE.value,
    PcbLayer.BOTTOM_PASTE.value: PcbLayer.TOP_PASTE.value,
    PcbLayer.TOP_SOLDER.value: PcbLayer.BOTTOM_SOLDER.value,
    PcbLayer.BOTTOM_SOLDER.value: PcbLayer.TOP_SOLDER.value,
}

_MECHPAIR_KEY_RE = re.compile(r"MECHPAIR(\d+)L([12])$", re.IGNORECASE)
_MECHANICAL_LAYER_NAME_RE = re.compile(r"MECHANICAL\s*(\d+)$", re.IGNORECASE)

_BASE_V7_LAYER_FLIP_MAP = {
    legacy_layer_to_v7_save_id(PcbLayer.TOP): legacy_layer_to_v7_save_id(PcbLayer.BOTTOM),
    legacy_layer_to_v7_save_id(PcbLayer.BOTTOM): legacy_layer_to_v7_save_id(PcbLayer.TOP),
    int(PcbV7SavedLayerId.TOP_OVERLAY): int(PcbV7SavedLayerId.BOTTOM_OVERLAY),
    int(PcbV7SavedLayerId.BOTTOM_OVERLAY): int(PcbV7SavedLayerId.TOP_OVERLAY),
    int(PcbV7SavedLayerId.TOP_PASTE): int(PcbV7SavedLayerId.BOTTOM_PASTE),
    int(PcbV7SavedLayerId.BOTTOM_PASTE): int(PcbV7SavedLayerId.TOP_PASTE),
    int(PcbV7SavedLayerId.TOP_SOLDER): int(PcbV7SavedLayerId.BOTTOM_SOLDER),
    int(PcbV7SavedLayerId.BOTTOM_SOLDER): int(PcbV7SavedLayerId.TOP_SOLDER),
}


def _mechanical_name_to_legacy_layer_id(layer_name: Any) -> int | None:
    match = _MECHANICAL_LAYER_NAME_RE.fullmatch(str(layer_name or "").strip())
    if not match:
        return None

    mech_number = int(match.group(1))
    if not 1 <= mech_number <= 16:
        return None

    return PcbLayer.MECHANICAL_1.value + mech_number - 1


def _mechanical_name_to_v7_save_id(layer_name: Any) -> int | None:
    match = _MECHANICAL_LAYER_NAME_RE.fullmatch(str(layer_name or "").strip())
    if not match:
        return None

    mech_number = int(match.group(1))
    return pcb_mechanical_layer_number_to_v7_saved_layer_id(mech_number)


def _build_component_layer_flip_map(board_raw_record: dict[str, Any] | None) -> dict[int, int]:
    layer_flip_map = dict(_BASE_LAYER_FLIP_MAP)
    if not isinstance(board_raw_record, dict):
        return layer_flip_map

    mech_pairs: dict[int, dict[str, int]] = {}
    for key, value in board_raw_record.items():
        if (match := _MECHPAIR_KEY_RE.fullmatch(key)) is None:
            continue
        pair_index = int(match.group(1))
        endpoint = match.group(2)
        layer_id = _mechanical_name_to_legacy_layer_id(value)
        if layer_id is None:
            continue
        mech_pairs.setdefault(pair_index, {})[endpoint] = layer_id

    for pair in mech_pairs.values():
        layer_1 = pair.get("1")
        layer_2 = pair.get("2")
        if layer_1 is None or layer_2 is None or layer_1 == layer_2:
            continue
        layer_flip_map[layer_1] = layer_2
        layer_flip_map[layer_2] = layer_1

    return layer_flip_map


def _build_component_v7_layer_flip_map(board_raw_record: dict[str, Any] | None) -> dict[int, int]:
    flip_map = dict(_BASE_V7_LAYER_FLIP_MAP)
    if not isinstance(board_raw_record, dict):
        return flip_map

    mech_pairs: dict[int, dict[str, int]] = {}
    for key, value in board_raw_record.items():
        if (match := _MECHPAIR_KEY_RE.fullmatch(key)) is None:
            continue
        pair_index = int(match.group(1))
        endpoint = match.group(2)
        save_id = _mechanical_name_to_v7_save_id(value)
        if save_id is None:
            continue
        mech_pairs.setdefault(pair_index, {})[endpoint] = save_id

    for pair in mech_pairs.values():
        save_1 = pair.get("1")
        save_2 = pair.get("2")
        if save_1 is None or save_2 is None or save_1 == save_2:
            continue
        flip_map[save_1] = save_2
        flip_map[save_2] = save_1

    return flip_map


def _flip_layer(layer: int, layer_flip_map: dict[int, int] | None = None) -> int:
    flip_map = layer_flip_map or _BASE_LAYER_FLIP_MAP
    return flip_map.get(layer, layer)


def _clamp_i32(value: int) -> int:
    return max(min(int(value), 0x7FFFFFFF), -0x80000000)


def _clear_raw_cache(prim: object) -> None:
    for attr in ("_raw_binary", "_raw_binary_signature"):
        if hasattr(prim, attr):
            setattr(prim, attr, None)


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
        int(PcbV7LayerPartition.TOP_OVERLAY): PcbLayer.TOP_OVERLAY.value,
        int(PcbV7LayerPartition.BOTTOM_OVERLAY): PcbLayer.BOTTOM_OVERLAY.value,
        int(PcbV7LayerPartition.TOP_PASTE): PcbLayer.TOP_PASTE.value,
        int(PcbV7LayerPartition.BOTTOM_PASTE): PcbLayer.BOTTOM_PASTE.value,
        int(PcbV7LayerPartition.TOP_SOLDER): PcbLayer.TOP_SOLDER.value,
        int(PcbV7LayerPartition.BOTTOM_SOLDER): PcbLayer.BOTTOM_SOLDER.value,
        int(PcbV7LayerPartition.DRILL_GUIDE): PcbLayer.DRILL_GUIDE.value,
        int(PcbV7LayerPartition.KEEPOUT_LAYER): PcbLayer.KEEPOUT.value,
        int(PcbV7LayerPartition.DRILL_DRAWING): PcbLayer.DRILL_DRAWING.value,
        int(PcbV7LayerPartition.MULTI_LAYER): PcbLayer.MULTI_LAYER.value,
        int(PcbV7LayerPartition.CONNECT_LAYER): PcbLayer.CONNECT.value,
    }
    if (saved & 0xFFFF0000) == 0x01030000:
        return misc_partitions.get(saved & 0xFFFF)
    return None


def _sync_saved_layer_id(
    prim: object,
    attr_name: str,
    *,
    flipped: bool,
    v7_layer_flip_map: dict[int, int] | None = None,
) -> None:
    """
    Synchronize a saved V7 layer id field, updating the legacy layer byte too.
    """
    if not hasattr(prim, attr_name):
        return

    def _sync_legacy_layer_from_saved_id(saved_layer_id: int | None) -> None:
        if not hasattr(prim, "layer"):
            return
        legacy_layer = _legacy_layer_id_from_v7_save_id(saved_layer_id)
        if legacy_layer is not None:
            prim.layer = legacy_layer

    try:
        current = getattr(prim, attr_name)
        if flipped and current not in (None, 0):
            flip_map = v7_layer_flip_map or _BASE_V7_LAYER_FLIP_MAP
            mapped = flip_map.get(int(current))
            if mapped is not None:
                setattr(prim, attr_name, mapped)
                _sync_legacy_layer_from_saved_id(mapped)
                return
            setattr(prim, attr_name, int(current))
            _sync_legacy_layer_from_saved_id(int(current))
            return
        if current not in (None, 0):
            setattr(prim, attr_name, int(current))
            _sync_legacy_layer_from_saved_id(int(current))
            return
        synthesized = legacy_layer_to_v7_save_id(int(getattr(prim, "layer")))
        setattr(prim, attr_name, synthesized)
        _sync_legacy_layer_from_saved_id(synthesized)
    except Exception:
        return


def _sync_pad_saved_layer_state(
    pad: object,
    flipped: bool,
    v7_layer_flip_map: dict[int, int],
) -> None:
    """
    Pads carry a hidden saved-layer field outside the visible legacy layer byte.
    """
    if flipped and hasattr(pad, "layer"):
        pad.layer = _flip_layer(int(getattr(pad, "layer")), _BASE_LAYER_FLIP_MAP)

    if hasattr(pad, "layer_v7_save_id"):
        _sync_saved_layer_id(
            pad,
            "layer_v7_save_id",
            flipped=flipped,
            v7_layer_flip_map=v7_layer_flip_map,
        )
    elif hasattr(pad, "layer"):
        try:
            pad.layer_v7_save_id = legacy_layer_to_v7_save_id(int(getattr(pad, "layer")))
        except Exception:
            pass
