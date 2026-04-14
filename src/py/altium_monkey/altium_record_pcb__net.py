"""
Altium PCB Net Record

Parse net definitions from Nets6/Data stream.

Nets define electrical connectivity in the PCB design. Each net has a unique
name and can have associated properties like color, keepout status, etc.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class AltiumPcbNet:
    """
    PCB net definition.
    
    Represents a single electrical net in the PCB design.
    
    Attributes:
        name: Net name (e.g., "GND", "VCC", "NetR1_1")
        unique_id: Unique identifier for this net
        color: Net color (RGB value)
        keepout: Is this a keepout net
        locked: Is net locked
        visible: Is net visible
        user_routed: Is net manually routed
        loop_removal: Enable loop removal
        jumpers_visible: Show jumpers
        override_color: Override color for drawing
        layer: Associated layer (if any)
        polygon_outline: Show polygon outline
        union_index: Union group index
    
        _raw_record: Complete raw record dict
    
        # Access properties
        print(f"Net: {net.name} (ID: {net.unique_id})")
    
        # Serialize back
        record = net.to_record()
    """

    # Core properties
    name: str = ""
    unique_id: str = ""

    # Appearance
    color: int = 65535  # RGB color value
    visible: bool = True
    override_color: bool = False

    # Flags
    keepout: bool = False
    locked: bool = False
    user_routed: bool = True
    loop_removal: bool = True
    jumpers_visible: bool = True
    polygon_outline: bool = False

    # Advanced
    layer: str = ""
    union_index: int = 0

    # Raw record
    _raw_record: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'AltiumPcbNet':
        """
        Parse net from Nets6/Data text record.
        
        Args:
            record: Text record dict from get_records_in_section()
        
        Returns:
            AltiumPcbNet instance
        """
        net = cls(_raw_record=record.copy())

        # Parse basic properties
        net.name = record.get('NAME', '')
        net.unique_id = record.get('UNIQUEID', '')

        # Parse appearance
        try:
            net.color = int(record.get('COLOR', 65535))
        except ValueError:
            net.color = 65535

        net.visible = record.get('VISIBLE', 'TRUE') == 'TRUE'
        net.override_color = record.get('OVERRIDECOLORFORDRAW', 'FALSE') == 'TRUE'

        # Parse flags
        net.keepout = record.get('KEEPOUT', 'FALSE') == 'TRUE'
        net.locked = record.get('LOCKED', 'FALSE') == 'TRUE'
        net.user_routed = record.get('USERROUTED', 'TRUE') == 'TRUE'
        net.loop_removal = record.get('LOOPREMOVAL', 'TRUE') == 'TRUE'
        net.jumpers_visible = record.get('JUMPERSVISIBLE', 'TRUE') == 'TRUE'
        net.polygon_outline = record.get('POLYGONOUTLINE', 'FALSE') == 'TRUE'

        # Parse advanced
        net.layer = record.get('LAYER', '')
        try:
            net.union_index = int(record.get('UNIONINDEX', 0))
        except ValueError:
            net.union_index = 0

        return net

    def to_record(self) -> dict[str, Any]:
        """
        Serialize net to Nets6/Data text record format.
        
        Returns:
            Text record dict
        """
        record = self._raw_record.copy()

        # Update basic properties
        record['NAME'] = self.name
        record['UNIQUEID'] = self.unique_id

        # Update appearance
        record['COLOR'] = str(self.color)
        record['VISIBLE'] = 'TRUE' if self.visible else 'FALSE'
        record['OVERRIDECOLORFORDRAW'] = 'TRUE' if self.override_color else 'FALSE'

        # Update flags
        record['KEEPOUT'] = 'TRUE' if self.keepout else 'FALSE'
        record['LOCKED'] = 'TRUE' if self.locked else 'FALSE'
        record['USERROUTED'] = 'TRUE' if self.user_routed else 'FALSE'
        record['LOOPREMOVAL'] = 'TRUE' if self.loop_removal else 'FALSE'
        record['JUMPERSVISIBLE'] = 'TRUE' if self.jumpers_visible else 'FALSE'
        record['POLYGONOUTLINE'] = 'TRUE' if self.polygon_outline else 'FALSE'

        # Update advanced
        if self.layer:
            record['LAYER'] = self.layer
        record['UNIONINDEX'] = str(self.union_index)

        return record

    def __repr__(self) -> str:
        """
        String representation.
        """
        return f"AltiumPcbNet(name='{self.name}', id='{self.unique_id}')"
