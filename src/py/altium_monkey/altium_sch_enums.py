"""Schematic-facing enum definitions and display labels."""

from enum import IntEnum


class IeeeSymbol(IntEnum):
    """
    IEEE symbol types for pin decorations.

    Used for symbol_inner, symbol_outer, symbol_inner_edge, symbol_outer_edge.
    These decorations indicate signal characteristics (clock, active low, etc.).
    """

    NONE = 0
    DOT = 1  # Inversion dot
    RIGHT_LEFT_SIGNAL_FLOW = 2  # Signal flow arrow (right to left)
    CLOCK = 3  # Clock edge indicator
    ACTIVE_LOW_INPUT = 4  # Active low input bar
    ANALOG_SIGNAL_IN = 5  # Analog input indicator
    NOT_LOGIC_CONNECTION = 6  # Non-logic connection
    SHIFT_RIGHT = 7  # Shift right indicator
    POSTPONED_OUTPUT = 8  # Postponed output
    OPEN_COLLECTOR = 9  # Open collector output
    HIZ = 10  # High impedance
    HIGH_CURRENT = 11  # High current driver
    PULSE = 12  # Pulse indicator
    SCHMITT = 13  # Schmitt trigger input
    DELAY = 14  # Delay element
    GROUP_LINE = 15  # Group line indicator
    GROUP_BIN = 16  # Group binary indicator
    ACTIVE_LOW_OUTPUT = 17  # Active low output bar
    PI_SYMBOL = 18  # Pi symbol
    GREATER_EQUAL = 19  # Greater than or equal
    LESS_EQUAL = 20  # Less than or equal
    SIGMA = 21  # Sigma (summation)
    OPEN_COLLECTOR_PULL_UP = 22  # Open collector with pull-up
    OPEN_EMITTER = 23  # Open emitter output
    OPEN_EMITTER_PULL_UP = 24  # Open emitter with pull-up
    DIGITAL_SIGNAL_IN = 25  # Digital input indicator
    AND = 26  # AND gate symbol
    INVERTER = 27  # Inverter triangle
    OR = 28  # OR gate symbol
    XOR = 29  # XOR gate symbol
    SHIFT_LEFT = 30  # Shift left indicator
    INPUT_OUTPUT = 31  # Bidirectional I/O
    OPEN_CIRCUIT_OUTPUT = 32  # Open circuit output
    LEFT_RIGHT_SIGNAL_FLOW = 33  # Signal flow arrow (left to right)
    BIDIRECTIONAL_SIGNAL_FLOW = 34  # Bidirectional signal flow
    INTERNAL_PULL_UP = 35  # Internal pull-up resistor
    INTERNAL_PULL_DOWN = 36  # Internal pull-down resistor


class PinElectrical(IntEnum):
    """
    Pin electrical types.

    Defines the electrical characteristics of a pin for ERC checking.
    """

    INPUT = 0  # Input only
    IO = 1  # Bidirectional I/O
    OUTPUT = 2  # Output only
    OPEN_COLLECTOR = 3  # Open collector output
    PASSIVE = 4  # Passive (resistor, capacitor, etc.)
    HIZ = 5  # High impedance / tri-state
    OPEN_EMITTER = 6  # Open emitter output
    POWER = 7  # Power pin (VCC, GND)


class PinItemMode(IntEnum):
    """
    Pin text item mode (default vs custom).

    Controls whether name/designator use default or custom font/position.
    """

    DEFAULT = 0  # Use system defaults
    CUSTOM = 1  # Use custom settings


class PinTextAnchor(IntEnum):
    """
    Pin text rotation anchor point.

    Determines reference point for text rotation calculation.
    """

    PIN = 0  # Rotate relative to pin
    COMPONENT = 1  # Rotate relative to component


class PinTextRotation(IntEnum):
    """
    Pin text rotation values.

    Only 0 and 90 degrees are valid for pin name/designator text.
    Used in AltiumSchPin constructor for name_rotation and designator_rotation.

    Note: This uses actual degree values (0, 90), not indices like Rotation90.
    The PinTextData stream stores rotation as a flag bit, not this value directly.
    """

    HORIZONTAL = 0  # 0 degrees - horizontal text (default)
    VERTICAL = 90  # 90 degrees - vertical text


class PinTextOrientation(IntEnum):
    """
    Pin text orientation in degree values.

    Used by PinTextData helper surfaces that expose the stream orientation as
    concrete degrees rather than quarter-turn indices.
    """

    DEG_0 = 0
    DEG_90 = 90
    DEG_180 = 180
    DEG_270 = 270


class Rotation90(IntEnum):
    """
    Rotation in 90-degree increments.

    Used for pin orientation and text rotation.
    """

    DEG_0 = 0  # 0 degrees (right)
    DEG_90 = 1  # 90 degrees (up)
    DEG_180 = 2  # 180 degrees (left)
    DEG_270 = 3  # 270 degrees (down)


class TextOrientation(IntEnum):
    """
    Text orientation in 90-degree increments.

    Used for Label, Parameter, Designator, PowerPort, TextFrame orientation.
    Same values as Rotation90 but with DEGREES_* naming for text contexts.
    """

    DEGREES_0 = 0  # 0 degrees (horizontal, left-to-right)
    DEGREES_90 = 1  # 90 degrees (vertical, bottom-to-top)
    DEGREES_180 = 2  # 180 degrees (horizontal, right-to-left)
    DEGREES_270 = 3  # 270 degrees (vertical, top-to-bottom)


class PinOrientation(IntEnum):
    """
    Pin orientation/direction.

    Used for schematic pin placement indicating which direction the pin points.
    Same values as Rotation90 but with directional naming for pin contexts.
    """

    RIGHT = 0  # Pin points right - 0 degrees
    UP = 1  # Pin points up - 90 degrees
    LEFT = 2  # Pin points left - 180 degrees
    DOWN = 3  # Pin points down - 270 degrees


class SymbolLineWidth(IntEnum):
    """
    Symbol/line width sizes.

    Used for pin symbol line thickness.
    """

    ZERO = 0  # Zero/thinnest
    SMALL = 1  # Small thickness
    MEDIUM = 2  # Medium thickness
    LARGE = 3  # Large thickness


class StdLogicState(IntEnum):
    """
    Standard logic state for formal type specification.

    Used for simulation and formal verification.
    """

    UNINITIALIZED = 0  # Uninitialized
    FORCING_UNKNOWN = 1  # Forcing unknown (X)
    FORCING_0 = 2  # Forcing logic 0
    FORCING_1 = 3  # Forcing logic 1
    HIZ = 4  # High impedance (Z)
    WEAK_UNKNOWN = 5  # Weak unknown (W)
    WEAK_0 = 6  # Weak logic 0 (L)
    WEAK_1 = 7  # Weak logic 1 (H)
    DONT_CARE = 8  # Don't care (-)


class ParameterSetStyle(IntEnum):
    """
    Parameter set display style.

    Controls how a parameter set is displayed on the schematic.

    Native Altium schematic parameter sets only expose two styles:
    large and tiny. There is no native medium style.
    """

    LARGE = 0  # Large display style
    TINY = 1  # Tiny/compact display style


# Convenience mappings for display strings

IEEE_SYMBOL_NAMES = {
    IeeeSymbol.NONE: "None",
    IeeeSymbol.DOT: "Dot (Negation)",
    IeeeSymbol.RIGHT_LEFT_SIGNAL_FLOW: "Right-Left Signal Flow",
    IeeeSymbol.CLOCK: "Clock",
    IeeeSymbol.ACTIVE_LOW_INPUT: "Active Low Input",
    IeeeSymbol.ANALOG_SIGNAL_IN: "Analog Signal In",
    IeeeSymbol.NOT_LOGIC_CONNECTION: "Not Logic Connection",
    IeeeSymbol.SHIFT_RIGHT: "Shift Right",
    IeeeSymbol.POSTPONED_OUTPUT: "Postponed Output",
    IeeeSymbol.OPEN_COLLECTOR: "Open Collector",
    IeeeSymbol.HIZ: "High-Z",
    IeeeSymbol.HIGH_CURRENT: "High Current",
    IeeeSymbol.PULSE: "Pulse",
    IeeeSymbol.SCHMITT: "Schmitt Trigger",
    IeeeSymbol.DELAY: "Delay",
    IeeeSymbol.GROUP_LINE: "Group Line",
    IeeeSymbol.GROUP_BIN: "Group Binary",
    IeeeSymbol.ACTIVE_LOW_OUTPUT: "Active Low Output",
    IeeeSymbol.PI_SYMBOL: "Pi Symbol",
    IeeeSymbol.GREATER_EQUAL: "Greater or Equal",
    IeeeSymbol.LESS_EQUAL: "Less or Equal",
    IeeeSymbol.SIGMA: "Sigma",
    IeeeSymbol.OPEN_COLLECTOR_PULL_UP: "Open Collector with Pull-Up",
    IeeeSymbol.OPEN_EMITTER: "Open Emitter",
    IeeeSymbol.OPEN_EMITTER_PULL_UP: "Open Emitter with Pull-Up",
    IeeeSymbol.DIGITAL_SIGNAL_IN: "Digital Signal In",
    IeeeSymbol.AND: "AND Gate",
    IeeeSymbol.INVERTER: "Inverter",
    IeeeSymbol.OR: "OR Gate",
    IeeeSymbol.XOR: "XOR Gate",
    IeeeSymbol.SHIFT_LEFT: "Shift Left",
    IeeeSymbol.INPUT_OUTPUT: "Input/Output",
    IeeeSymbol.OPEN_CIRCUIT_OUTPUT: "Open Circuit Output",
    IeeeSymbol.LEFT_RIGHT_SIGNAL_FLOW: "Left-Right Signal Flow",
    IeeeSymbol.BIDIRECTIONAL_SIGNAL_FLOW: "Bidirectional Signal Flow",
    IeeeSymbol.INTERNAL_PULL_UP: "Internal Pull-Up",
    IeeeSymbol.INTERNAL_PULL_DOWN: "Internal Pull-Down",
}

PIN_ELECTRICAL_NAMES = {
    PinElectrical.INPUT: "Input",
    PinElectrical.IO: "I/O",
    PinElectrical.OUTPUT: "Output",
    PinElectrical.OPEN_COLLECTOR: "Open Collector",
    PinElectrical.PASSIVE: "Passive",
    PinElectrical.HIZ: "Hi-Z",
    PinElectrical.OPEN_EMITTER: "Open Emitter",
    PinElectrical.POWER: "Power",
}

ROTATION_NAMES = {
    Rotation90.DEG_0: "Right (0 deg)",
    Rotation90.DEG_90: "Up (90 deg)",
    Rotation90.DEG_180: "Left (180 deg)",
    Rotation90.DEG_270: "Down (270 deg)",
}


class PowerObjectStyle(IntEnum):
    """
    Power port symbol styles.

    Defines the visual representation of power port symbols on schematics.
    Used in AltiumSchPowerPort.style field.
    """

    CIRCLE = 0  # ePowerCircle - rounded circle
    ARROW = 1  # ePowerArrow - triangle with base line
    BAR = 2  # ePowerBar - simple perpendicular line
    WAVE = 3  # ePowerWave - two arcs forming S-curve
    GND_POWER = 4  # ePowerGndPower - 4 decreasing horizontal lines (classic GND)
    GND_SIGNAL = 5  # ePowerGndSignal - line ending in filled triangle
    GND_EARTH = 6  # ePowerGndEarth - line with 3 diagonal lines
    GOST_ARROW = 7  # eGOSTPowerArrow - triangle without base line
    GOST_GND_POWER = 8  # eGOSTPowerGndPower - GOST standard ground power
    GOST_GND_EARTH = 9  # eGOSTPowerGndEarth - GOST standard earth ground
    GOST_BAR = 10  # eGOSTPowerBar - longer perpendicular line


POWER_OBJECT_STYLE_NAMES = {
    PowerObjectStyle.CIRCLE: "Circle",
    PowerObjectStyle.ARROW: "Arrow",
    PowerObjectStyle.BAR: "Bar",
    PowerObjectStyle.WAVE: "Wave",
    PowerObjectStyle.GND_POWER: "Ground (Power)",
    PowerObjectStyle.GND_SIGNAL: "Ground (Signal)",
    PowerObjectStyle.GND_EARTH: "Ground (Earth)",
    PowerObjectStyle.GOST_ARROW: "GOST Arrow",
    PowerObjectStyle.GOST_GND_POWER: "GOST Ground (Power)",
    PowerObjectStyle.GOST_GND_EARTH: "GOST Ground (Earth)",
    PowerObjectStyle.GOST_BAR: "GOST Bar",
}


class OffSheetConnectorStyle(IntEnum):
    """
    Off-sheet connector symbol styles.

    These match Altium's stored values for cross-sheet connectors.
    """

    LEFT = 0
    RIGHT = 1


OFF_SHEET_CONNECTOR_STYLE_NAMES = {
    OffSheetConnectorStyle.LEFT: "Left",
    OffSheetConnectorStyle.RIGHT: "Right",
}


class PortStyle(IntEnum):
    """
    Port arrow/shape styles.

    Defines the visual shape of hierarchical port connectors.
    Used in AltiumSchPort.style field.
    """

    NONE_HORIZONTAL = 0  # ePortNone - horizontal flat end
    LEFT = 1  # ePortArrowLeft - arrow pointing left
    RIGHT = 2  # ePortArrowRight - arrow pointing right
    LEFT_RIGHT = 3  # ePortArrowLeftRight - arrows both directions
    NONE_VERTICAL = 4  # ePortNoneVertical - vertical flat end
    TOP = 5  # ePortTop - arrow pointing up
    BOTTOM = 6  # ePortBottom - arrow pointing down
    TOP_BOTTOM = 7  # ePortTopBottom - arrows both vertical directions


PORT_STYLE_NAMES = {
    PortStyle.NONE_HORIZONTAL: "None (Horizontal)",
    PortStyle.LEFT: "Left Arrow",
    PortStyle.RIGHT: "Right Arrow",
    PortStyle.LEFT_RIGHT: "Left & Right",
    PortStyle.NONE_VERTICAL: "None (Vertical)",
    PortStyle.TOP: "Top",
    PortStyle.BOTTOM: "Bottom",
    PortStyle.TOP_BOTTOM: "Top & Bottom",
}


class PortIOType(IntEnum):
    """
    Port I/O direction type.

    Defines the electrical direction of a hierarchical port for ERC.
    Used in AltiumSchPort.io_type field.
    """

    UNSPECIFIED = 0  # ePortUnspecified - no direction specified
    OUTPUT = 1  # ePortOutput - output port
    INPUT = 2  # ePortInput - input port
    BIDIRECTIONAL = 3  # ePortBidirectional - bidirectional port


PORT_IO_TYPE_NAMES = {
    PortIOType.UNSPECIFIED: "Unspecified",
    PortIOType.OUTPUT: "Output",
    PortIOType.INPUT: "Input",
    PortIOType.BIDIRECTIONAL: "Bidirectional",
}


class SchHorizontalAlign(IntEnum):
    """
    Horizontal alignment for schematic note and text-frame content.

    These values match Altium's horizontal alignment storage for schematic text
    frames and notes.
    """

    CENTER = 0
    LEFT = 1
    RIGHT = 2


SCH_HORIZONTAL_ALIGN_NAMES = {
    SchHorizontalAlign.CENTER: "Center",
    SchHorizontalAlign.LEFT: "Left",
    SchHorizontalAlign.RIGHT: "Right",
}


class TextJustification(IntEnum):
    """
    Text horizontal justification.

    Controls horizontal alignment of text elements.
    """

    BOTTOM_LEFT = 0  # eJustify_BottomLeft
    BOTTOM_CENTER = 1  # eJustify_BottomCenter
    BOTTOM_RIGHT = 2  # eJustify_BottomRight
    CENTER_LEFT = 3  # eJustify_CenterLeft
    CENTER_CENTER = 4  # eJustify_CenterCenter
    CENTER_RIGHT = 5  # eJustify_CenterRight
    TOP_LEFT = 6  # eJustify_TopLeft
    TOP_CENTER = 7  # eJustify_TopCenter
    TOP_RIGHT = 8  # eJustify_TopRight


TEXT_JUSTIFICATION_NAMES = {
    TextJustification.BOTTOM_LEFT: "Bottom Left",
    TextJustification.BOTTOM_CENTER: "Bottom Center",
    TextJustification.BOTTOM_RIGHT: "Bottom Right",
    TextJustification.CENTER_LEFT: "Center Left",
    TextJustification.CENTER_CENTER: "Center",
    TextJustification.CENTER_RIGHT: "Center Right",
    TextJustification.TOP_LEFT: "Top Left",
    TextJustification.TOP_CENTER: "Top Center",
    TextJustification.TOP_RIGHT: "Top Right",
}
