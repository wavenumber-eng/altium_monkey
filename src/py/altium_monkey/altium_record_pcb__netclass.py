"""
Altium PCB Net Class Record

Parse net class definitions from Classes6/Data stream.

Net classes group related objects (nets, components, pads, layers, etc.) together
for applying design rules and organization.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from .altium_pcb_enums import PcbNetClassKind

log = logging.getLogger(__name__)

@dataclass
class AltiumPcbNetClass:
    """
    PCB net class definition.
    
    Net classes group objects (nets, components, pads, layers, polygons, etc.)
    together for design rule application and organization.
    
    Attributes:
        name: Class name (e.g., "All Nets", "Power Nets", "Signal Nets")
        kind: Class type (0=Net, 1=Component, 2=From-To, 3=Pad, 4=Layer, 6=Diff Pair, 7=Polygon)
        member_count: Number of members in this class
        members: List of member names/IDs
        enabled: Is class enabled
        unique_id: Unique identifier
    
        _raw_record: Complete raw record dict
    
        # Access properties
        print(f"Class: {net_class.name} (Kind: {net_class.kind.name})")
        print(f"Members: {net_class.member_count}")
    
        # Serialize back
        record = net_class.to_record()
    """

    # Core properties
    name: str = ""
    kind: PcbNetClassKind = PcbNetClassKind.NET
    member_count: int = 0
    members: list[str] = field(default_factory=list)

    # Properties
    enabled: bool = True
    unique_id: str = ""

    # Raw record
    _raw_record: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'AltiumPcbNetClass':
        """
        Parse net class from Classes6/Data text record.
        
        Args:
            record: Text record dict from get_records_in_section()
        
        Returns:
            AltiumPcbNetClass instance
        """
        net_class = cls(_raw_record=record.copy())

        # Parse basic properties
        net_class.name = record.get('NAME', '')

        # Parse kind
        try:
            kind_val = int(record.get('KIND', 0))
            net_class.kind = PcbNetClassKind(kind_val)
        except (ValueError, KeyError):
            net_class.kind = PcbNetClassKind.NET

        # Parse member count
        try:
            net_class.member_count = int(record.get('MEMBERCOUNT', 0))
        except ValueError:
            net_class.member_count = 0

        # Parse members (M0, M1, M2, ...)
        members = []
        i = 0
        while f'M{i}' in record:
            member = record[f'M{i}']
            members.append(member)
            i += 1
        net_class.members = members

        # Parse properties
        net_class.enabled = record.get('ENABLED', 'TRUE') == 'TRUE'
        net_class.unique_id = record.get('UNIQUEID', '')

        return net_class

    def to_record(self) -> dict[str, Any]:
        """
        Serialize net class to Classes6/Data text record format.
        
        Returns:
            Text record dict
        """
        record = self._raw_record.copy()

        # Update basic properties
        record['NAME'] = self.name
        record['KIND'] = str(int(self.kind))
        record['MEMBERCOUNT'] = str(self.member_count)

        # Update members
        for i, member in enumerate(self.members):
            record[f'M{i}'] = member

        # Update properties
        record['ENABLED'] = 'TRUE' if self.enabled else 'FALSE'
        if self.unique_id:
            record['UNIQUEID'] = self.unique_id

        return record

    def __repr__(self) -> str:
        """
        String representation.
        """
        try:
            kind_name = self.kind.name
        except ValueError:
            kind_name = str(int(self.kind))

        return (f"AltiumPcbNetClass(name='{self.name}', kind={kind_name}, "
                f"members={self.member_count})")
