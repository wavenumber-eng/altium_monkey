"""Exact unit conversion for PCB manufacturing materialization."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN

PCB_SOURCE_UNIT_NM_NUMERATOR = 127
PCB_SOURCE_UNIT_NM_DENOMINATOR = 50
PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM = 500


def pcb_internal_to_nm(value: int) -> int:
    """Convert one Altium PCB internal-unit value to integer nm, ties to even."""

    return _round_ratio_ties_even(
        int(value) * PCB_SOURCE_UNIT_NM_NUMERATOR,
        PCB_SOURCE_UNIT_NM_DENOMINATOR,
    )


def pcb_mils_to_nm(value: float | str | Decimal) -> int:
    """Convert a resolved mil value to integer nm with decimal ties to even."""

    nanometers = Decimal(str(value)) * Decimal(25_400)
    return int(nanometers.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _round_ratio_ties_even(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    doubled = remainder * 2
    if doubled > denominator or doubled == denominator and quotient % 2 == 1:
        quotient += 1
    return sign * quotient


__all__ = (
    "PCB_DIRECT_QUANTIZATION_MAX_ERROR_PM",
    "PCB_SOURCE_UNIT_NM_DENOMINATOR",
    "PCB_SOURCE_UNIT_NM_NUMERATOR",
    "pcb_internal_to_nm",
    "pcb_mils_to_nm",
)
