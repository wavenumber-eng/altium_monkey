"""
Shared solder/paste rule helpers for PCB exporters.

These helpers centralize expansion/tenting decisions so SVG and IPC-2581
outputs can stay in lock-step for pad/via aperture generation.
"""

from __future__ import annotations

from typing import Literal


# Default solder mask expansion: 4 mil = 40000 internal units.
DEFAULT_SOLDER_MASK_EXPANSION_IU = 40000

# Near-zero paste aperture threshold (~0.001 mm in Altium internal units).
MIN_PASTE_OPENING_IU = 400


def get_pad_mask_expansion_iu(pad: object) -> int:
    """
    Return effective pad solder-mask expansion in internal units.
    """
    if bool(getattr(pad, "_has_mask_expansion", False)):
        if int(getattr(pad, "soldermask_expansion_mode", 0)) in (1, 2):
            return int(getattr(pad, "soldermask_expansion_manual", 0) or 0)
    return DEFAULT_SOLDER_MASK_EXPANSION_IU


def get_via_mask_expansion_iu(via: object, side: Literal["top", "bottom"]) -> int:
    """
    Return effective via solder-mask expansion for top/bottom side.
    """
    if side not in {"top", "bottom"}:
        raise ValueError(f"Invalid via solder-mask side: {side!r}")

    has_front = bool(getattr(via, "_has_soldermask_expansion_front", False))
    has_back = bool(getattr(via, "_has_soldermask_expansion_back", False))
    front = int(getattr(via, "soldermask_expansion_front", 0) or 0)
    back = int(getattr(via, "soldermask_expansion_back", 0) or 0)
    linked = bool(getattr(via, "soldermask_expansion_linked", False))

    if side == "top":
        if has_front:
            return front
        if linked and has_back:
            return back
        return DEFAULT_SOLDER_MASK_EXPANSION_IU

    if linked:
        if has_front:
            return front
        if has_back:
            return back
        return DEFAULT_SOLDER_MASK_EXPANSION_IU

    if has_back:
        return back
    if has_front:
        return front
    return DEFAULT_SOLDER_MASK_EXPANSION_IU


def get_pad_paste_expansion_iu(pad: object) -> int:
    """
    Return effective pad paste expansion in internal units.
    """
    if bool(getattr(pad, "_has_mask_expansion", False)):
        if int(getattr(pad, "pastemask_expansion_mode", 0)) in (1, 2):
            return int(getattr(pad, "pastemask_expansion_manual", 0) or 0)
    return 0


def has_pad_paste_opening(
    pad: object,
    width_iu: int,
    height_iu: int,
    *,
    min_opening_iu: int = MIN_PASTE_OPENING_IU,
) -> bool:
    """
    Return whether the effective paste opening remains positive.
    """
    if width_iu <= 0 or height_iu <= 0:
        return False

    if bool(getattr(pad, "_has_mask_expansion", False)):
        if int(getattr(pad, "pastemask_expansion_mode", 0)) in (1, 2):
            exp = int(getattr(pad, "pastemask_expansion_manual", 0) or 0)
            if width_iu + 2 * exp < min_opening_iu:
                return False
            if height_iu + 2 * exp < min_opening_iu:
                return False
    return True


def is_pad_solder_mask_only(pad: object) -> bool:
    """
    Return True when a side-specific SMD pad is intentionally mask-only.
    
        This is intentionally narrow. In the real corpus, testpoint flags and
        manual paste expansions also appear on ordinary copper pads. The only
        safe pattern we currently treat as mask-only is:
        - no drilled hole
        - top or bottom SMD pad
        - side-specific testpoint-like flag set
        - no effective paste opening on that side
    """
    try:
        hole_size = int(getattr(pad, "hole_size", 0) or 0)
    except (TypeError, ValueError):
        return False
    if hole_size > 0:
        return False

    try:
        layer = int(getattr(pad, "layer", 0) or 0)
        width_iu = int(getattr(pad, "top_width", 0) or 0)
        height_iu = int(getattr(pad, "top_height", 0) or 0)
    except (TypeError, ValueError):
        return False

    if width_iu <= 0 or height_iu <= 0:
        return False

    def _has_test_flag(side: str) -> bool:
        suffix = "top" if side == "top" else "bottom"
        return any(
            bool(getattr(pad, attr, False))
            for attr in (
                f"is_assy_test_point_{suffix}",
                f"is_fab_test_point_{suffix}",
                f"is_test_fab_{suffix}",
                f"is_test_{suffix}",
            )
        )

    if has_pad_paste_opening(pad, width_iu, height_iu):
        return False

    if layer == 1 and _has_test_flag("top"):
        return True
    if layer == 32 and _has_test_flag("bottom"):
        return True
    return False
