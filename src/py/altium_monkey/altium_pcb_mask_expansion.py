"""
Pad solder-mask and paste-mask expansion authoring helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .altium_record_pcb__pad import AltiumPcbPad


class PcbMaskExpansionMode(IntEnum):
    """
    Altium pad mask-expansion mode.

    Native PAD records encode these values as 0=None, 1=Rule, and 2=Manual.
    """

    NONE = 0
    RULE = 1
    MANUAL = 2


PcbMaskExpansionModeInput = PcbMaskExpansionMode | int | str


def normalize_pcb_mask_expansion_mode(
    value: PcbMaskExpansionModeInput,
) -> PcbMaskExpansionMode:
    """
    Normalize public mask-expansion mode input to `PcbMaskExpansionMode`.
    """
    if isinstance(value, PcbMaskExpansionMode):
        return value
    if isinstance(value, int):
        try:
            return PcbMaskExpansionMode(value)
        except ValueError as exc:
            raise ValueError(
                "mask expansion mode must be none, rule, or manual"
            ) from exc

    normalized = str(value).strip().lower().replace("_", "-")
    aliases = {
        "0": PcbMaskExpansionMode.NONE,
        "none": PcbMaskExpansionMode.NONE,
        "off": PcbMaskExpansionMode.NONE,
        "disabled": PcbMaskExpansionMode.NONE,
        "no": PcbMaskExpansionMode.NONE,
        "1": PcbMaskExpansionMode.RULE,
        "rule": PcbMaskExpansionMode.RULE,
        "rules": PcbMaskExpansionMode.RULE,
        "from-rule": PcbMaskExpansionMode.RULE,
        "2": PcbMaskExpansionMode.MANUAL,
        "manual": PcbMaskExpansionMode.MANUAL,
        "custom": PcbMaskExpansionMode.MANUAL,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError("mask expansion mode must be none, rule, or manual") from exc


@dataclass(frozen=True, slots=True)
class PcbMaskExpansion:
    """
    Public pad mask-expansion authoring value.

    `expansion_mils` is required for `MANUAL` and must be omitted for `NONE`
    and `RULE`.
    """

    mode: PcbMaskExpansionModeInput = PcbMaskExpansionMode.RULE
    expansion_mils: float | None = None

    def __post_init__(self) -> None:
        normalized = normalize_pcb_mask_expansion_mode(self.mode)
        object.__setattr__(self, "mode", normalized)
        if normalized == PcbMaskExpansionMode.MANUAL:
            if self.expansion_mils is None:
                raise ValueError("manual mask expansion requires expansion_mils")
            object.__setattr__(self, "expansion_mils", float(self.expansion_mils))
            return
        if self.expansion_mils is not None:
            raise ValueError("mask expansion mils are only valid with manual mode")

    @classmethod
    def none(cls) -> "PcbMaskExpansion":
        """Return a no-opening/no-rule mask-expansion setting."""
        return cls(PcbMaskExpansionMode.NONE)

    @classmethod
    def rule(cls) -> "PcbMaskExpansion":
        """Return a rule-driven mask-expansion setting."""
        return cls(PcbMaskExpansionMode.RULE)

    @classmethod
    def manual(cls, expansion_mils: float) -> "PcbMaskExpansion":
        """Return a manual signed expansion value in mils."""
        return cls(PcbMaskExpansionMode.MANUAL, float(expansion_mils))


PcbMaskExpansionInput = PcbMaskExpansion | PcbMaskExpansionModeInput | None


def resolve_pcb_mask_expansion(
    *,
    value: PcbMaskExpansionInput = None,
    mode: PcbMaskExpansionModeInput | None = None,
    expansion_mils: float | None = None,
    field_name: str,
    default_mode: PcbMaskExpansionMode = PcbMaskExpansionMode.RULE,
) -> PcbMaskExpansion:
    """
    Resolve dataclass/shortcut/keyword mask-expansion inputs.
    """
    if isinstance(value, PcbMaskExpansion):
        if mode is not None or expansion_mils is not None:
            raise ValueError(
                f"{field_name} cannot combine PcbMaskExpansion with mode/mils kwargs"
            )
        return value
    if value is not None and mode is not None:
        raise ValueError(f"{field_name} cannot specify both value and mode")

    selected_mode: PcbMaskExpansionModeInput = (
        value if value is not None else mode if mode is not None else default_mode
    )
    return PcbMaskExpansion(selected_mode, expansion_mils)


def resolve_pcb_mask_expansion_with_manual_alias(
    *,
    value: PcbMaskExpansionInput = None,
    mode: PcbMaskExpansionModeInput | None = None,
    expansion_mils: float | None = None,
    field_name: str,
    default_mode: PcbMaskExpansionMode = PcbMaskExpansionMode.RULE,
) -> PcbMaskExpansion:
    """
    Resolve mask-expansion inputs while preserving legacy mil-only manual calls.
    """
    selected_mode = mode
    if expansion_mils is not None and value is None and selected_mode is None:
        selected_mode = PcbMaskExpansionMode.MANUAL
    return resolve_pcb_mask_expansion(
        value=value,
        mode=selected_mode,
        expansion_mils=expansion_mils,
        field_name=field_name,
        default_mode=default_mode,
    )


def legacy_rule_expansion_to_mode(value: bool | None) -> PcbMaskExpansionMode | None:
    """
    Convert legacy custom-pad rule-expansion booleans to explicit modes.
    """
    if value is None:
        return None
    return PcbMaskExpansionMode.RULE if bool(value) else PcbMaskExpansionMode.NONE


def resolve_pcb_mask_expansion_with_legacy_alias(
    *,
    value: PcbMaskExpansionInput = None,
    mode: PcbMaskExpansionModeInput | None = None,
    expansion_mils: float | None = None,
    legacy_rule_expansion: bool | None = None,
    field_name: str,
    default_mode: PcbMaskExpansionMode = PcbMaskExpansionMode.RULE,
) -> PcbMaskExpansion:
    """
    Resolve new mask-expansion inputs plus legacy rule-expansion booleans.
    """
    has_explicit = value is not None or mode is not None or expansion_mils is not None
    legacy_mode = legacy_rule_expansion_to_mode(legacy_rule_expansion)
    if legacy_mode is None or not has_explicit:
        return resolve_pcb_mask_expansion(
            value=value,
            mode=legacy_mode if legacy_mode is not None else mode,
            expansion_mils=expansion_mils,
            field_name=field_name,
            default_mode=default_mode,
        )

    resolved = resolve_pcb_mask_expansion(
        value=value,
        mode=mode,
        expansion_mils=expansion_mils,
        field_name=field_name,
        default_mode=default_mode,
    )
    if resolved.mode != legacy_mode:
        raise ValueError(f"{field_name} conflicts with legacy rule-expansion boolean")
    return resolved


def apply_pcb_mask_expansion_to_pad(
    pad: AltiumPcbPad,
    *,
    paste: PcbMaskExpansion,
    solder: PcbMaskExpansion,
) -> None:
    """
    Apply resolved paste/solder mask expansion values to a PAD record.
    """
    _apply_one_mask_expansion(
        pad,
        mode_attr="pastemask_expansion_mode",
        manual_attr="pastemask_expansion_manual",
        expansion=paste,
    )
    _apply_one_mask_expansion(
        pad,
        mode_attr="soldermask_expansion_mode",
        manual_attr="soldermask_expansion_manual",
        expansion=solder,
    )
    pad._has_mask_expansion = True


def _apply_one_mask_expansion(
    pad: AltiumPcbPad,
    *,
    mode_attr: str,
    manual_attr: str,
    expansion: PcbMaskExpansion,
) -> None:
    setattr(pad, mode_attr, int(expansion.mode))
    manual_value = 0
    if expansion.mode == PcbMaskExpansionMode.MANUAL:
        assert expansion.expansion_mils is not None
        manual_value = int(round(float(expansion.expansion_mils) * 10000.0))
    setattr(pad, manual_attr, manual_value)
