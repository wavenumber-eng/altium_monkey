"""
Internal binary parsing helpers shared by Altium readers and writers.
"""

import logging
import struct
from typing import Any

log = logging.getLogger(__name__)


class BinaryReader:
    """
    Position-based binary stream reader for Altium binary payloads.
    """

    def __init__(self, data: bytes, offset: int = 0) -> None:
        """
        Initialize binary reader.
        
        Args:
            data: Binary data to read
            offset: Starting offset (default: 0)
        """
        self.data = data
        self.pos = offset
        self.size = len(data)
        self.subrecord_end: int | None = None
        self.error = False

    def read_byte(self) -> int:
        """
        Read single byte (uint8). Returns 0 on error.
        """
        if self.pos + 1 > self.size:
            self.error = True
            return 0
        val = self.data[self.pos]
        self.pos += 1
        return val

    def read_int8(self) -> int:
        """
        Read 1-byte signed integer. Returns 0 on error.
        """
        if self.pos + 1 > self.size:
            self.error = True
            return 0
        val = struct.unpack('<b', self.data[self.pos:self.pos+1])[0]
        self.pos += 1
        return val

    def read_int16(self) -> int:
        """
        Read 2-byte signed integer (little-endian). Returns 0 on error.
        """
        if self.pos + 2 > self.size:
            self.error = True
            return 0
        val = struct.unpack('<h', self.data[self.pos:self.pos+2])[0]
        self.pos += 2
        return val

    def read_uint16(self) -> int:
        """
        Read 2-byte unsigned integer (little-endian). Returns 0 on error.
        """
        if self.pos + 2 > self.size:
            self.error = True
            return 0
        val = struct.unpack('<H', self.data[self.pos:self.pos+2])[0]
        self.pos += 2
        return val

    def read_int32(self) -> int:
        """
        Read 4-byte signed integer (little-endian). Returns 0 on error.
        """
        if self.pos + 4 > self.size:
            self.error = True
            return 0
        val = struct.unpack('<i', self.data[self.pos:self.pos+4])[0]
        self.pos += 4
        return val

    def read_uint32(self) -> int:
        """
        Read 4-byte unsigned integer (little-endian). Returns 0 on error.
        """
        if self.pos + 4 > self.size:
            self.error = True
            return 0
        val = struct.unpack('<I', self.data[self.pos:self.pos+4])[0]
        self.pos += 4
        return val

    def read_float(self) -> float:
        """
        Read 4-byte float (little-endian). Returns 0.0 on error.
        """
        if self.pos + 4 > self.size:
            self.error = True
            return 0.0
        val = struct.unpack('<f', self.data[self.pos:self.pos+4])[0]
        self.pos += 4
        return val

    def read_double(self) -> float:
        """
        Read 8-byte double (little-endian). Returns 0.0 on error.
        """
        if self.pos + 8 > self.size:
            self.error = True
            return 0.0
        val = struct.unpack('<d', self.data[self.pos:self.pos+8])[0]
        self.pos += 8
        return val

    def read_bytes(self, length: int) -> bytes:
        """
        Read N bytes. Returns empty bytes on error.
        """
        if self.pos + length > self.size:
            self.error = True
            return b''
        val = self.data[self.pos:self.pos+length]
        self.pos += length
        return val

    def skip(self, length: int) -> None:
        """
        Skip N bytes forward.
        """
        if self.pos + length > self.size:
            self.error = True
        else:
            self.pos += length

    def peek_byte(self) -> int:
        """
        Peek at next byte without advancing position. Returns 0 if at end.
        """
        if self.pos + 1 > self.size:
            return 0
        return self.data[self.pos]

    def remaining_bytes(self) -> int:
        """
        Get number of remaining bytes in stream.
        """
        return max(0, self.size - self.pos)

    def read_subrecord_length(self) -> int:
        """
        Read SubRecord length and set end pointer.
        
        SubRecord format:
            [4 bytes] Content length (NOT including this 4-byte field)
            [N bytes] Content
        
        After calling this, use remaining_subrecord_bytes() to check
        how much data is left in the current SubRecord.
        
        Returns:
            Content length (number of bytes in SubRecord content)
        """
        length = self.read_uint32()
        if not self.error:
            self.subrecord_end = self.pos + length
        return length

    def remaining_subrecord_bytes(self) -> int:
        """
        Get remaining bytes in current SubRecord.
        """
        if self.subrecord_end is None or self.subrecord_end <= self.pos:
            return 0
        return self.subrecord_end - self.pos

    def skip_subrecord(self) -> None:
        """
        Skip to end of current SubRecord.
        """
        if self.subrecord_end is None or self.subrecord_end < self.pos:
            self.error = True
        else:
            self.pos = self.subrecord_end
            self.subrecord_end = None

    def has_error(self) -> bool:
        """
        Check if any parsing error occurred.
        """
        return self.error


class SubRecord:
    """
    Represents a parsed SubRecord from binary PCB data.
    
    SubRecords are variable-length data blocks found in binary PCB records.
    Format:
        [4 bytes] Length (N) - content length, NOT including this field
        [N bytes] Content
    
    Attributes:
        content: Binary content of the SubRecord
        length: Length of content in bytes
    """

    def __init__(self, content: bytes) -> None:
        """
        Initialize SubRecord.
        
        Args:
            content: Binary content data
        """
        self.content = content
        self.length = len(content)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to dictionary for serialization.
        
        Returns:
            Dictionary with 'length' and 'content' keys
        """
        return {
            'length': self.length,
            'content': self.content
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SubRecord':
        """
        Create SubRecord from dictionary.
        
        Args:
            data: Dictionary with 'content' key
        
        Returns:
            SubRecord instance
        """
        return cls(data['content'])

    def __repr__(self) -> str:
        return f"SubRecord(length={self.length})"


def parse_subrecords(data: bytes, start_offset: int = 0) -> list[SubRecord]:
    """
    Parse all SubRecords from binary data.
    
    Reads length-prefixed binary SubRecords until end of data.
    Stops if incomplete SubRecord encountered.
    
    Args:
        data: Binary data containing SubRecords
        start_offset: Starting offset in data (default: 0)
    
    Returns:
        List of SubRecord objects.
    """
    reader = BinaryReader(data, start_offset)
    subrecords = []

    while reader.remaining_bytes() >= 4:
        length = reader.read_subrecord_length()

        if reader.has_error():
            break

        if length == 0:
            # Empty SubRecord
            subrecords.append(SubRecord(b''))
            continue

        if reader.remaining_bytes() < length:
            # Incomplete SubRecord - stop parsing
            break

        content = reader.read_bytes(length)
        if not reader.has_error():
            subrecords.append(SubRecord(content))

    return subrecords


def serialize_subrecords(subrecords: list[SubRecord]) -> bytes:
    """
    Serialize SubRecords back to binary format.
    
    Reconstructs binary stream from SubRecord objects.
    Format for each SubRecord:
        [4 bytes] Length (little-endian uint32, content length only)
        [N bytes] Content
    
    Args:
        subrecords: List of SubRecord objects
    
    Returns:
        Binary data ready to write to file
    """
    result = bytearray()

    for subrecord in subrecords:
        # Write length (NOT including this 4-byte field)
        length = len(subrecord.content)
        result.extend(struct.pack('<I', length))

        # Write content
        result.extend(subrecord.content)

    return bytes(result)


def parse_binary_record_basic(data: bytes) -> dict[str, Any]:
    """
    Parse binary record into basic structure (type + SubRecords).
    
    This is a convenience function that extracts the record type byte
    and parses all SubRecords.
    
    Args:
        data: Binary record data (including type byte)
    
    Returns:
        Dictionary with 'record_type' and 'subrecords' keys
    """
    if len(data) == 0:
        return {'record_type': 0, 'subrecords': []}

    record_type = data[0]
    subrecords = parse_subrecords(data, start_offset=1)

    return {
        'record_type': record_type,
        'subrecords': subrecords
    }


def serialize_binary_record_basic(record: dict[str, Any]) -> bytes:
    """
    Serialize binary record from basic structure (type + SubRecords).
    
    Inverse of parse_binary_record_basic().
    
    Args:
        record: Dictionary with 'record_type' and 'subrecords' keys
    
    Returns:
        Binary data (type byte + serialized SubRecords)
    """
    record_type = record.get('record_type', 0)
    subrecords = record.get('subrecords', [])

    result = bytearray()
    result.append(record_type)
    result.extend(serialize_subrecords(subrecords))

    return bytes(result)
