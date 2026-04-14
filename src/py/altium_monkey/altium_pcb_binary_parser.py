"""
Altium PCB Binary Stream Parser

Parses raw binary streams from PcbDoc/PcbLib files.

IMPORTANT: PcbDoc/PcbLib streams use a different format than SchLib/SchDoc!
- SchLib: Length-prefixed records [4-byte length][type byte][data]
- PcbDoc: Raw binary stream [type byte][SubRecord1][SubRecord2]...[next type byte][SubRecord1]...

This module handles the PCB format.
"""

import logging
import struct
from typing import Any

from .altium_binary_utils import SubRecord, serialize_subrecords
from .altium_ole import AltiumOleFile

log = logging.getLogger(__name__)

def parse_pcb_binary_stream(data: bytes, parse_subrecords_flag: bool = True) -> list[dict[str, Any]]:
    """
    Parse raw binary PCB stream into records.
    
    PCB streams (from PcbDoc/PcbLib) are raw binary data without length prefixes.
    Format:
        [type byte] [SubRecord1] [SubRecord2] ... [next type byte] [SubRecord1] ...
    
    Each record starts with a type byte (1=ARC, 2=PAD, 3=VIA, 4=TRACK, etc.)
    followed by SubRecords.
    
    SubRecord format:
        [4 bytes] Length (content length, NOT including this field)
        [N bytes] Content
    
    Args:
        data: Raw binary stream data
        parse_subrecords_flag: If True, parse SubRecords (default: True)
    
    Returns:
        List of record dictionaries with keys:
            'RECORD': Record type byte
            '__BINARY_RECORD__': True
            '__BINARY_DATA__': Raw binary data for this record
            '__SUBRECORDS__': List of SubRecord objects (if parse_subrecords_flag=True)
    
        for rec in records:
            if rec['RECORD'] == 4:  # TRACK
                subrecords = rec['__SUBRECORDS__']
                print(f"Track with {len(subrecords)} SubRecords")
    """
    records = []
    offset = 0

    while offset < len(data):
        # Look for record type byte
        # Valid PCB record types: 1-6, 11-12 (and possibly others)
        record_start = offset

        if offset + 1 > len(data):
            break

        record_type = data[offset]
        offset += 1

        # Now read SubRecords until we hit the next record type byte
        # or end of data
        subrecords = []

        # Read SubRecords
        while offset < len(data):
            # Check if we have at least 4 bytes for SubRecord length
            if offset + 4 > len(data):
                break

            # Peek at SubRecord length
            subrecord_length = struct.unpack('<I', data[offset:offset+4])[0]

            # Sanity check: SubRecord length should be reasonable
            # If length is > 10MB or negative, probably not a SubRecord
            if subrecord_length > 10 * 1024 * 1024 or subrecord_length < 0:
                # This might be a new record type byte, not a SubRecord
                break

            # Check if we have enough data for this SubRecord
            if offset + 4 + subrecord_length > len(data):
                # Not enough data - might be end of stream or corruption
                break

            # Read SubRecord
            offset += 4  # Skip length field
            content = data[offset:offset+subrecord_length]
            offset += subrecord_length

            subrecords.append(SubRecord(content))

            # Check if next byte could be a record type (heuristic)
            if offset < len(data):
                next_byte = data[offset]
                # If next byte looks like a record type (1-20 typically), stop
                # This is a heuristic - may need tuning
                if subrecord_length == 0 and next_byte > 0 and next_byte < 20:
                    break

        # Extract binary data for this record
        record_end = offset
        binary_data = data[record_start:record_end]

        # Create record dictionary
        record = {
            'RECORD': record_type,
            '__BINARY_RECORD__': True,
            '__BINARY_DATA__': binary_data
        }

        # Add parsed SubRecords if requested
        if parse_subrecords_flag:
            record['__SUBRECORDS__'] = subrecords

        records.append(record)

    return records

def serialize_pcb_binary_stream(records: list[dict[str, Any]]) -> bytes:
    """
    Serialize PCB records back to raw binary stream.
    
    Inverse of parse_pcb_binary_stream().
    
    Args:
        records: List of record dictionaries (from parse_pcb_binary_stream)
    
    Returns:
        Raw binary stream data
    """
    result = bytearray()

    for record in records:
        if '__BINARY_DATA__' in record:
            # Use original binary data
            result.extend(record['__BINARY_DATA__'])

        elif '__SUBRECORDS__' in record:
            # Re-serialize from SubRecords
            record_type = record.get('RECORD', 0)
            subrecord_data = serialize_subrecords(record['__SUBRECORDS__'])

            # Format: [type byte] + [subrecords]
            result.append(record_type)
            result.extend(subrecord_data)

        else:
            log.error("PCB record missing both __BINARY_DATA__ and __SUBRECORDS__")

    return bytes(result)

def parse_pcb_stream_from_ole(ole: AltiumOleFile, stream_path: list[str],
                              parse_subrecords_flag: bool = True) -> list[dict[str, Any]]:
    """
    Parse PCB binary stream from OLE file.
    
    Convenience wrapper around parse_pcb_binary_stream().
    
    Args:
        ole: Open OLE file object
        stream_path: Stream path (e.g., ['Tracks6', 'Data'])
        parse_subrecords_flag: If True, parse SubRecords
    
    Returns:
        List of record dictionaries
    """
    if not ole.exists(stream_path):
        log.warning(f"Stream not found: {'/'.join(stream_path)}")
        return []

    data = ole.openstream(stream_path)
    return parse_pcb_binary_stream(data, parse_subrecords_flag=parse_subrecords_flag)
