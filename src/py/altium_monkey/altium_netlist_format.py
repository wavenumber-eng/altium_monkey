"""
Altium WireList netlist serialization helpers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .altium_netlist_common import _normalize_text

if TYPE_CHECKING:
    from .altium_netlist_model import Netlist, Terminal

log = logging.getLogger(__name__)


@dataclass
class NetlistComponent:
    """
    Represents a component in the netlist.

    Attributes:
        designator: Component designator (e.g., "R1", "U3", "J1")
        footprint: Footprint/model name from Implementation record
        comment: Comment/Value field
    """

    designator: str
    footprint: str
    comment: str

    def to_wirelist_line(self) -> str:
        """
        Generate WireList component line.

        Format: Comment (32 chars) + Designator (15 chars) + Footprint

        Returns:
            Single line for component list section
        """
        comment_col = (self.comment or "").ljust(32)
        designator_col = self.designator.ljust(15)
        footprint_col = self.footprint or ""

        return f"{comment_col}{designator_col}{footprint_col}"


@dataclass
class NetlistPin:
    """
    Represents a pin connection within a net.

    Attributes:
        designator: Component designator (e.g., "R1")
        pin_number: Pin number/designator (e.g., "1", "GND")
        pin_name: Pin name (often same as pin_number)
        pin_type: Electrical type (e.g., "PASSIVE", "INPUT", "OUTPUT")
        part_value: Component value/comment
    """

    designator: str
    pin_number: str
    pin_name: str
    pin_type: str = "PASSIVE"
    part_value: str = ""

    def to_wirelist_line(self) -> str:
        """
        Generate WireList pin line.

        Format: 8 spaces + Reference (11) + PIN # (8) + PIN NAME (15) + PIN TYPE (12) + PART VALUE

        Returns:
            Single line for wire list section
        """
        indent = " " * 8
        ref_col = self.designator.ljust(11)
        pin_num_col = str(self.pin_number).ljust(8)
        pin_name_col = (self.pin_name or str(self.pin_number)).ljust(15)
        pin_type_col = self.pin_type.ljust(12)
        part_value_col = self.part_value or ""

        return f"{indent}{ref_col}{pin_num_col}{pin_name_col}{pin_type_col}{part_value_col}"

    @staticmethod
    def sort_key(pin: "NetlistPin") -> tuple:
        """
        Sort key for pins within a net.

        Sorts by:
        1. Designator prefix (letters)
        2. Designator number
        3. Pin number (numeric if possible)
        """
        # Split designator into prefix and number
        prefix = ""
        num_str = ""
        for ch in pin.designator:
            if ch.isdigit():
                num_str += ch
            else:
                if num_str:
                    prefix += num_str + ch
                    num_str = ""
                else:
                    prefix += ch

        des_num = int(num_str) if num_str else 0

        # Parse pin number
        try:
            pin_int = int(pin.pin_number)
        except ValueError:
            pin_int = 0

        return (prefix, des_num, pin_int)


@dataclass
class NetlistNet:
    """
    Represents a net with its connected pins.

    Attributes:
        name: Net name (from NetLabel, PowerPort, or auto-generated)
        node_number: 5-digit node number for WireList format
        pins: List of pin connections
        is_auto_named: True if name was auto-generated (not from NetLabel/PowerPort)
    """

    name: str
    node_number: int
    pins: list[NetlistPin] = field(default_factory=list)
    is_auto_named: bool = False

    def to_wirelist_lines(self) -> list[str]:
        """
        Generate WireList lines for this net.

        Returns:
            List of lines: node header + sorted pin lines + blank line
        """
        lines = []

        # Node header: [NNNNN] NetName
        node_str = f"[{self.node_number:05d}]"
        lines.append(f"{node_str} {self.name}")

        # Sort pins and add lines
        sorted_pins = sorted(self.pins, key=NetlistPin.sort_key)
        for pin in sorted_pins:
            lines.append(pin.to_wirelist_line())

        # Blank line after net
        lines.append("")

        return lines


@dataclass
class NetlistData:
    """
    Complete netlist data structure.

    Attributes:
        components: List of components
        nets: List of nets with pin connections
    """

    components: list[NetlistComponent] = field(default_factory=list)
    nets: list[NetlistNet] = field(default_factory=list)

    def get_floating_pins(self) -> list[tuple[str, str]]:
        """
        Get pins that are effectively floating (unconnected).

        A pin is considered floating if it's on an auto-named net with only
        one pin - meaning it's connected to a wire but that wire doesn't
        connect to any other pins or named nets.

        Returns:
            List of (designator, pin_number) tuples for floating pins
        """
        floating = []
        for net in self.nets:
            if net.is_auto_named and len(net.pins) == 1:
                pin = net.pins[0]
                floating.append((pin.designator, pin.pin_number))
        return floating

    def get_single_pin_nets(self) -> list[NetlistNet]:
        """
        Get all nets that have only a single pin.

        This includes both explicitly named single-pin nets (from NetLabels)
        and auto-named single-pin nets (floating pins).

        Returns:
            List of NetlistNet objects with only one pin
        """
        return [net for net in self.nets if len(net.pins) == 1]

    def to_wirelist(self, allow_single_pin_nets: bool = False) -> str:
        """
        Generate complete WireList format string.

        Components are sorted alphabetically by designator.
        Nets maintain their insertion order (generator determines order).

        Args:
            allow_single_pin_nets: If True, include single-pin nets in output.
                                  Mirrors Altium's "NetlistSinglePinNets" project option.

        Returns:
            Complete .NET file content as string
        """
        lines = []

        # Header
        lines.append("Wire List")
        lines.append("")

        # Component section
        lines.append("<<< Component List >>>")

        # Sort components by designator
        sorted_components = sorted(
            self.components, key=lambda c: self._designator_sort_key(c.designator)
        )

        for comp in sorted_components:
            lines.append(comp.to_wirelist_line())

        lines.append("")

        # Wire section
        lines.append("<<< Wire List >>>")
        lines.append("")

        # Column header
        lines.append("  NODE  REFERENCE  PIN #   PIN NAME       PIN TYPE    PART VALUE")
        lines.append("")

        # Nets (preserve insertion order)
        # NOTE: Altium suppresses single-pin auto-named nets in WireList output by default
        # (they represent unconnected pins, not meaningful connectivity)
        # Explicitly named single-pin nets (via NetLabel/PowerPort) ARE output
        # When allow_single_pin_nets=True (project option), all single-pin nets are output
        for net in self.nets:
            if len(net.pins) < 2:
                # Single-pin net - check if we should include it
                if allow_single_pin_nets:
                    # Project option allows single-pin nets - include all
                    pass
                elif net.is_auto_named:
                    # No project option and auto-named - suppress
                    log.debug(
                        f"Suppressing single-pin auto-named net '{net.name}' in WireList output"
                    )
                    continue
                # Explicitly named single-pin nets are always included
            lines.extend(net.to_wirelist_lines())

        # Join with newlines
        return "\n".join(lines)

    @staticmethod
    def _designator_sort_key(designator: str) -> tuple:
        """
        Natural sort key for designators like "R1", "D1_1", "D10_2", etc.

        Splits into alternating text/number segments for natural ordering.
        """
        parts = []
        current = ""
        in_digits = False
        for ch in designator:
            is_digit = ch.isdigit()
            if is_digit != in_digits and current:
                parts.append(int(current) if in_digits else current)
                current = ""
            current += ch
            in_digits = is_digit
        if current:
            parts.append(int(current) if in_digits else current)
        return tuple(parts)


# =============================================================================
# New netlist-model support
# =============================================================================


# Character mappings for netlist output (Altium converts special chars)
# Note: Altium converts Omega to 'O', but preserves the micro sign.
CHAR_REPLACEMENTS = {
    "Ω": "O",  # Greek capital omega (U+03A9) to O
    # Note: U+00B5 and U+03BC are preserved by Altium.
}


# Pin type name mappings for WireList format
# Altium uses specific formatting: I/O, OPEN COLLECTOR, OPEN EMITTER (with spaces)
PIN_TYPE_WIRELIST_NAMES = {
    "IO": "I/O",
    "OPEN_COLLECTOR": "OPEN COLLECTOR",
    "OPEN_EMITTER": "OPEN EMITTER",
}


def _normalize_text_for_netlist(text: str, strict: bool = True) -> str:
    """
    Normalize text for netlist output.
    """
    return _normalize_text(text, strict)


def _designator_sort_key(designator: str) -> tuple:
    """
    Natural sort key for designators like "R1", "D1_1", "D10_2", etc.

    Splits into alternating text/number segments for natural ordering.
    Each segment becomes a (type_flag, value) pair to ensure comparable types.
    """
    parts = []
    current = ""
    in_digits = False
    for ch in designator:
        is_digit = ch.isdigit()
        if is_digit != in_digits and current:
            parts.append((1, int(current)) if in_digits else (0, current))
            current = ""
        current += ch
        in_digits = is_digit
    if current:
        parts.append((1, int(current)) if in_digits else (0, current))
    return tuple(parts)


def _terminal_sort_key(terminal: Terminal) -> tuple[tuple[int, str | int], ...]:
    """
    Sort key for terminals within a net.

    Sorts by designator (natural sort) then pin (natural sort).
    """
    return (
        _designator_sort_key(terminal.designator),
        _designator_sort_key(terminal.pin),
    )


def netlist_to_wirelist(
    netlist: Netlist,
    strict: bool = True,
    allow_single_pin_nets: bool = False,
) -> str:
    """
    Convert a Netlist object to WireList format string.

    This function takes the new generic Netlist model (from altium_netlist_model.py)
    and produces the WireList (.NET) format that Altium exports.

    Args:
        netlist: Netlist object with nets and components
        strict: If True, normalize special chars to ASCII
        allow_single_pin_nets: If True, include single-pin auto-named nets.
                               Mirrors Altium's "NetlistSinglePinNets" project option.
                               When False (default), single-pin auto-named nets are suppressed.

    Returns:
        WireList format string

    Column widths:
        Component List:
            - Comment/Value: 32 chars (left-aligned)
            - Designator: 15 chars (left-aligned)
            - Footprint: variable

        Wire List Pin Rows:
            - Indent: 8 spaces
            - Reference: 11 chars (left-aligned)
            - PIN #: 8 chars (left-aligned)
            - PIN NAME: 15 chars (left-aligned)
            - PIN TYPE: 12 chars (left-aligned)
            - PART VALUE: variable
    """
    lines = []

    # Header
    lines.append("Wire List")
    lines.append("")

    # Component section
    lines.append("<<< Component List >>>")

    # Sort components by designator
    sorted_components = sorted(
        netlist.components, key=lambda c: _designator_sort_key(c.designator)
    )

    for comp in sorted_components:
        value = comp.value or ""
        if strict:
            value = _normalize_text_for_netlist(value)
        comment_col = value.ljust(32)
        designator_col = comp.designator.ljust(15)
        # Altium truncates footprint name to 29 characters in component list
        footprint_col = (comp.footprint or "")[:29]
        lines.append(f"{comment_col}{designator_col}{footprint_col}")

    lines.append("")

    # Wire section
    lines.append("<<< Wire List >>>")
    lines.append("")

    # Column header
    lines.append("  NODE  REFERENCE  PIN #   PIN NAME       PIN TYPE    PART VALUE")
    lines.append("")

    # Filter nets based on allow_single_pin_nets option
    # When False, suppress single-pin auto-named nets (they represent unconnected wires)
    # Explicitly named single-pin nets (NetLabel, PowerPort) are always included
    # Exception: nets marked auto_named=False by multisheet merger are always included
    nets_to_output = []
    for net in netlist.nets:
        if len(net.terminals) == 1 and net.auto_named and not allow_single_pin_nets:
            # Skip single-pin auto-named nets when option is False
            log.debug(
                f"Suppressing single-pin auto-named net '{net.name}' (allow_single_pin_nets=False)"
            )
            continue
        nets_to_output.append(net)

    # Generate node numbers (counting down from total filtered nets)
    node_counter = len(nets_to_output)

    # Nets
    for net in nets_to_output:
        # Net header: [NNNNN] NetName
        lines.append(f"[{node_counter:05d}] {net.name}")
        node_counter -= 1

        # Sort terminals and add pin lines
        sorted_terminals = sorted(net.terminals, key=_terminal_sort_key)

        for term in sorted_terminals:
            # Get part value from netlist components
            comp = netlist.get_component(term.designator)
            part_value = comp.value if comp else ""
            if strict:
                part_value = _normalize_text_for_netlist(part_value)

            # Pin type name (use enum name, with Altium-specific formatting)
            pin_type_name = PIN_TYPE_WIRELIST_NAMES.get(
                term.pin_type.name, term.pin_type.name
            )

            # Format pin row
            indent = " " * 8
            ref_col = term.designator.ljust(11)
            # Truncate pin number to 7 chars max, strip trailing space, then pad to 8
            # This matches Altium's Wire List format for multi-pad pin designators
            pin_num_truncated = term.pin[:7].rstrip()
            pin_num_col = pin_num_truncated.ljust(8)
            # Truncate pin name to 15 chars max (matches Altium Wire List format)
            # PIN NAME column: use pin_name, empty if not set (don't fallback to pin designator)
            pin_name = (term.pin_name or "")[:15]
            pin_name_col = pin_name.ljust(15)
            pin_type_col = pin_type_name.ljust(12)

            lines.append(
                f"{indent}{ref_col}{pin_num_col}{pin_name_col}{pin_type_col}{part_value}"
            )

        # Blank line after net
        lines.append("")

    return "\n".join(lines)
