"""
AltiumOleFile - Unified OLE Reader/Writer for Altium Files

A minimal, self-contained OLE (Compound Document) reader and writer
specifically optimized for Altium files (SchLib, PcbLib, SchDoc, PcbDoc).

Features:
- Read: Parse OLE structure, list directories, read streams
- Write: In-place stream modification for byte-identical round-trips
- No external dependencies (replaces olefile)

OLE/CFB Format Reference:
- Microsoft [MS-CFB] Compound File Binary Format
- Sector sizes: 512 bytes (v3) or 4096 bytes (v4)
- Mini sectors: 64 bytes for streams < 4096 bytes
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

# =============================================================================
# Constants
# =============================================================================

OLE_MAGIC = b'\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1'

# Special sector values
MAXREGSECT = 0xFFFFFFFA  # Maximum regular sector
DIFSECT = 0xFFFFFFFC     # DIFAT sector
FATSECT = 0xFFFFFFFD     # FAT sector
ENDOFCHAIN = 0xFFFFFFFE  # End of chain
FREESECT = 0xFFFFFFFF    # Free sector

# Directory entry types
STGTY_EMPTY = 0
STGTY_STORAGE = 1
STGTY_STREAM = 2
STGTY_LOCKBYTES = 3
STGTY_PROPERTY = 4
STGTY_ROOT = 5

# Default sizes
HEADER_SIZE = 512
DIR_ENTRY_SIZE = 128
MINI_SECTOR_SIZE = 64
MINI_STREAM_CUTOFF = 4096

# Altium's root entry CLSID (Class ID)
# This identifies the file as an Altium document
ALTIUM_CLSID = "F11B58A9-94DD-4D1F-8408-D28E027CC9D8"

# CLSID as bytes (little-endian format for OLE)
# CLSID format: {DWORD-WORD-WORD-BYTE[8]}
# Stored as: DWORD LE, WORD LE, WORD LE, 8 bytes
ALTIUM_CLSID_BYTES = bytes([
    0xA9, 0x58, 0x1B, 0xF1,  # F11B58A9 as LE DWORD
    0xDD, 0x94,              # 94DD as LE WORD
    0x1F, 0x4D,              # 4D1F as LE WORD
    0x84, 0x08,              # 8408 (big endian - not swapped)
    0xD2, 0x8E, 0x02, 0x7C, 0xC9, 0xD8  # D28E027CC9D8
])

OleFileSource = str | Path | bytes | BinaryIO


# =============================================================================
# CLSID Utilities
# =============================================================================

def clsid_to_bytes(clsid_str: str) -> bytes:
    """
    Convert a CLSID string to bytes in OLE format.
    
    Args:
        clsid_str: CLSID like "F11B58A9-94DD-4D1F-8408-D28E027CC9D8"
    
    Returns:
        16-byte CLSID in OLE format (mixed endianness)
    """
    # Remove dashes
    clsid = clsid_str.replace('-', '')

    # Parse parts (OLE uses mixed endianness)
    # First 3 parts are little-endian, last 2 parts are big-endian
    return bytes([
        # DWORD (little-endian)
        int(clsid[6:8], 16),
        int(clsid[4:6], 16),
        int(clsid[2:4], 16),
        int(clsid[0:2], 16),
        # WORD (little-endian)
        int(clsid[10:12], 16),
        int(clsid[8:10], 16),
        # WORD (little-endian)
        int(clsid[14:16], 16),
        int(clsid[12:14], 16),
        # 8 bytes (big-endian / as-is)
        int(clsid[16:18], 16),
        int(clsid[18:20], 16),
        int(clsid[20:22], 16),
        int(clsid[22:24], 16),
        int(clsid[24:26], 16),
        int(clsid[26:28], 16),
        int(clsid[28:30], 16),
        int(clsid[30:32], 16),
    ])


def bytes_to_clsid(data: bytes) -> str:
    """
    Convert 16 bytes of CLSID data to string format.
    
    Args:
        data: 16 bytes of CLSID in OLE format
    
    Returns:
        CLSID string like "F11B58A9-94DD-4D1F-8408-D28E027CC9D8"
    """
    if len(data) != 16:
        return "00000000-0000-0000-0000-000000000000"

    return (
        f"{data[3]:02X}{data[2]:02X}{data[1]:02X}{data[0]:02X}-"
        f"{data[5]:02X}{data[4]:02X}-"
        f"{data[7]:02X}{data[6]:02X}-"
        f"{data[8]:02X}{data[9]:02X}-"
        f"{data[10]:02X}{data[11]:02X}{data[12]:02X}{data[13]:02X}{data[14]:02X}{data[15]:02X}"
    )


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class OleDirEntry:
    """
    Parsed directory entry.
    """
    sid: int                      # Entry index (sector ID)
    name: str                     # Entry name (decoded)
    name_raw: bytes               # Raw UTF-16LE name
    entry_type: int               # 0=empty, 1=storage, 2=stream, 5=root
    color: int                    # Red-black tree color
    sid_left: int                 # Left sibling
    sid_right: int                # Right sibling
    sid_child: int                # First child (for storages)
    clsid: bytes                  # Class ID (16 bytes)
    user_flags: int
    create_time: int
    modify_time: int
    start_sector: int             # First sector of stream
    size: int                     # Stream size in bytes

    # Computed
    is_mini: bool = False         # True if stored in mini stream
    path: str = ""                # Full path like "Symbol/Data"

    @property
    def is_stream(self) -> bool:
        return self.entry_type == STGTY_STREAM

    @property
    def is_storage(self) -> bool:
        return self.entry_type == STGTY_STORAGE

    @property
    def is_root(self) -> bool:
        return self.entry_type == STGTY_ROOT


# =============================================================================
# AltiumOleFile Class
# =============================================================================

class AltiumOleFile:
    """
    Unified OLE reader/writer for Altium files.
    
    Usage (read):
        with AltiumOleFile("component.SchLib") as ole:
            for path in ole.listdir():
                print(path)
            data = ole.openstream("FileHeader")
    
    Usage (write/round-trip):
        with AltiumOleFile("input.SchLib") as ole:
            data = ole.openstream("Symbol/Data")
            # modify data...
            ole.modify_stream("Symbol/Data", modified_data)
            ole.write("output.SchLib")
    """

    def __init__(self, filename: OleFileSource | None = None) -> None:
        """
        Initialize OLE file.
        
        Args:
            filename: Path to file, bytes content, or file-like object
        """
        # Raw file data
        self._data: bytearray | None = None
        self._filepath: Path | None = None

        # Parsed structures
        self._sector_size: int = 512
        self._mini_sector_size: int = MINI_SECTOR_SIZE
        self._mini_cutoff: int = MINI_STREAM_CUTOFF
        self._fat: list[int] = []
        self._minifat: list[int] = []
        self._directory: list[OleDirEntry] = []
        self._root: OleDirEntry | None = None

        # Directory index by path
        self._path_index: dict[str, int] = {}

        # Pending modifications
        self._modifications: dict[str, bytes] = {}

        # Header info
        self._dll_version: int = 0
        self._first_dir_sector: int = 0
        self._first_minifat_sector: int = 0
        self._num_minifat_sectors: int = 0
        self._first_difat_sector: int = 0
        self._num_difat_sectors: int = 0
        self._header_difat: list[int] = []

        if filename is not None:
            self.open(filename)

    def open(self, filename: OleFileSource) -> 'AltiumOleFile':
        """
        Open and parse an OLE file.
        
        Args:
            filename: Path, bytes, or file-like object
        
        Returns:
            self for chaining
        """
        # Load data
        if isinstance(filename, (str, Path)):
            self._filepath = Path(filename)
            self._data = bytearray(self._filepath.read_bytes())
        elif isinstance(filename, bytes):
            self._data = bytearray(filename)
        elif hasattr(filename, 'read'):
            self._data = bytearray(filename.read())
        else:
            raise ValueError(f"Unsupported filename type: {type(filename)}")

        # Parse
        self._parse_header()
        self._parse_fat()
        self._parse_directory()
        self._parse_minifat()
        self._build_path_index()

        return self

    def _require_data(self) -> bytearray:
        """
        Return the loaded file data or raise if the file is closed.
        """
        if self._data is None:
            raise ValueError("No OLE file is open")
        return self._data

    # =========================================================================
    # Parsing
    # =========================================================================

    def _parse_header(self) -> None:
        """
        Parse the 512-byte OLE header.
        """
        data = self._require_data()
        if len(data) < HEADER_SIZE:
            raise ValueError(f"File too small: {len(data)} bytes")

        header = data[:HEADER_SIZE]

        # Check magic
        if header[:8] != OLE_MAGIC:
            raise ValueError("Not an OLE file (invalid magic)")

        # Parse header fields
        # Offset 0x1C: minor version (2 bytes)
        # Offset 0x1E: major version (2 bytes) - 3 or 4
        # Offset 0x1E: byte order (2 bytes) - 0xFFFE = little-endian
        # Offset 0x1E: sector shift (2 bytes) - 9 = 512, 12 = 4096
        struct.unpack_from('<H', header, 0x18)[0]
        major_ver = struct.unpack_from('<H', header, 0x1A)[0]
        struct.unpack_from('<H', header, 0x1C)[0]
        sector_shift = struct.unpack_from('<H', header, 0x1E)[0]
        mini_sector_shift = struct.unpack_from('<H', header, 0x20)[0]

        self._dll_version = major_ver
        self._sector_size = 1 << sector_shift
        self._mini_sector_size = 1 << mini_sector_shift

        # More header fields
        # 0x2C: number of FAT sectors (4 bytes)
        # 0x30: first directory sector (4 bytes)
        # 0x38: mini stream cutoff size (4 bytes)
        # 0x3C: first mini FAT sector (4 bytes)
        # 0x40: number of mini FAT sectors (4 bytes)
        # 0x44: first DIFAT sector (4 bytes)
        # 0x48: number of DIFAT sectors (4 bytes)
        # 0x4C: DIFAT array (109 entries * 4 bytes = 436 bytes)

        struct.unpack_from('<I', header, 0x2C)[0]
        self._first_dir_sector = struct.unpack_from('<I', header, 0x30)[0]
        self._mini_cutoff = struct.unpack_from('<I', header, 0x38)[0]
        self._first_minifat_sector = struct.unpack_from('<I', header, 0x3C)[0]
        self._num_minifat_sectors = struct.unpack_from('<I', header, 0x40)[0]
        self._first_difat_sector = struct.unpack_from('<I', header, 0x44)[0]
        self._num_difat_sectors = struct.unpack_from('<I', header, 0x48)[0]

        # Read DIFAT from header (109 entries)
        self._header_difat = []
        for i in range(109):
            sector = struct.unpack_from('<I', header, 0x4C + i * 4)[0]
            self._header_difat.append(sector)

    def _parse_fat(self) -> None:
        """
        Parse the File Allocation Table.
        """
        self._fat = []

        # Collect FAT sector numbers from DIFAT
        fat_sectors = []

        # First 109 FAT sectors are in header DIFAT
        for sector in self._header_difat:
            if sector != FREESECT and sector < MAXREGSECT:
                fat_sectors.append(sector)

        # Additional DIFAT sectors if needed
        if self._num_difat_sectors > 0 and self._first_difat_sector != ENDOFCHAIN:
            difat_sector = self._first_difat_sector
            for _ in range(self._num_difat_sectors):
                if difat_sector == ENDOFCHAIN or difat_sector >= MAXREGSECT:
                    break

                difat_data = self._read_sector(difat_sector)
                # Each DIFAT sector has (sector_size/4 - 1) FAT entries
                # Last 4 bytes point to next DIFAT sector
                num_entries = (self._sector_size // 4) - 1
                for i in range(num_entries):
                    sector = struct.unpack_from('<I', difat_data, i * 4)[0]
                    if sector != FREESECT and sector < MAXREGSECT:
                        fat_sectors.append(sector)

                # Next DIFAT sector
                difat_sector = struct.unpack_from('<I', difat_data, num_entries * 4)[0]

        # Read FAT from FAT sectors
        for fat_sector in fat_sectors:
            fat_data = self._read_sector(fat_sector)
            # Each sector contains sector_size/4 FAT entries
            for i in range(self._sector_size // 4):
                entry = struct.unpack_from('<I', fat_data, i * 4)[0]
                self._fat.append(entry)

    def _parse_minifat(self) -> None:
        """
        Parse the Mini FAT.
        """
        self._minifat = []

        if self._first_minifat_sector == ENDOFCHAIN:
            return

        # Follow chain of MiniFAT sectors
        sector = self._first_minifat_sector
        count = 0
        while sector != ENDOFCHAIN and sector < MAXREGSECT and count < 10000:
            minifat_data = self._read_sector(sector)
            for i in range(self._sector_size // 4):
                entry = struct.unpack_from('<I', minifat_data, i * 4)[0]
                self._minifat.append(entry)

            # Next sector
            if sector < len(self._fat):
                sector = self._fat[sector]
            else:
                break
            count += 1

    def _parse_directory(self) -> None:
        """
        Parse directory entries.
        """
        self._directory = []

        # Read all directory sectors following FAT chain
        dir_data = self._read_stream_by_sector(self._first_dir_sector, use_fat=True)

        # Parse each 128-byte directory entry
        num_entries = len(dir_data) // DIR_ENTRY_SIZE
        for sid in range(num_entries):
            offset = sid * DIR_ENTRY_SIZE
            entry_data = dir_data[offset:offset + DIR_ENTRY_SIZE]

            if len(entry_data) < DIR_ENTRY_SIZE:
                break

            entry = self._parse_dir_entry(entry_data, sid)
            self._directory.append(entry)

            if entry.is_root and self._root is None:
                self._root = entry

    def _parse_dir_entry(self, data: bytes, sid: int) -> OleDirEntry:
        """
        Parse a single 128-byte directory entry.
        """
        # Directory entry structure:
        # 0x00: name (64 bytes, UTF-16LE, null-terminated)
        # 0x40: name length in bytes including null (2 bytes)
        # 0x42: entry type (1 byte)
        # 0x43: color (1 byte, 0=red, 1=black)
        # 0x44: left sibling SID (4 bytes)
        # 0x48: right sibling SID (4 bytes)
        # 0x4C: child SID (4 bytes)
        # 0x50: CLSID (16 bytes)
        # 0x60: user flags (4 bytes)
        # 0x64: create time (8 bytes)
        # 0x6C: modify time (8 bytes)
        # 0x74: start sector (4 bytes)
        # 0x78: size low (4 bytes)
        # 0x7C: size high (4 bytes)

        name_raw = data[0x00:0x40]
        name_len = struct.unpack_from('<H', data, 0x40)[0]
        entry_type = data[0x42]
        color = data[0x43]
        sid_left = struct.unpack_from('<I', data, 0x44)[0]
        sid_right = struct.unpack_from('<I', data, 0x48)[0]
        sid_child = struct.unpack_from('<I', data, 0x4C)[0]
        clsid = data[0x50:0x60]
        user_flags = struct.unpack_from('<I', data, 0x60)[0]
        create_time = struct.unpack_from('<Q', data, 0x64)[0]
        modify_time = struct.unpack_from('<Q', data, 0x6C)[0]
        start_sector = struct.unpack_from('<I', data, 0x74)[0]
        size_low = struct.unpack_from('<I', data, 0x78)[0]
        size_high = struct.unpack_from('<I', data, 0x7C)[0]

        # Decode name (UTF-16LE, excluding null terminator)
        if name_len > 2:
            name = name_raw[:name_len - 2].decode('utf-16-le', errors='replace')
        else:
            name = ""

        # Calculate size (use high bits only for v4 / 4096 sector files)
        if self._sector_size == 4096:
            size = size_low + (size_high << 32)
        else:
            size = size_low

        # Determine if mini stream
        is_mini = (entry_type == STGTY_STREAM and size < self._mini_cutoff)

        return OleDirEntry(
            sid=sid,
            name=name,
            name_raw=name_raw[:name_len] if name_len <= 64 else name_raw,
            entry_type=entry_type,
            color=color,
            sid_left=sid_left,
            sid_right=sid_right,
            sid_child=sid_child,
            clsid=clsid,
            user_flags=user_flags,
            create_time=create_time,
            modify_time=modify_time,
            start_sector=start_sector,
            size=size,
            is_mini=is_mini,
        )

    def _build_path_index(self) -> None:
        """
        Build path index for quick lookup.
        """
        self._path_index = {}

        # Build tree structure
        def walk(entry: OleDirEntry, parent_path: str) -> None:
            if entry.entry_type == STGTY_EMPTY:
                return

            if entry.is_root:
                path = ""
            else:
                path = f"{parent_path}/{entry.name}" if parent_path else entry.name

            entry.path = path
            if path:
                self._path_index[path] = entry.sid

            # Process children
            if entry.sid_child != FREESECT and entry.sid_child < len(self._directory):
                self._walk_siblings(entry.sid_child, path)

        if self._root:
            walk(self._root, "")

    def _walk_siblings(
        self,
        sid: int,
        parent_path: str,
        visited: set[int] | None = None,
    ) -> None:
        """
        Walk red-black tree of siblings.
        """
        if visited is None:
            visited = set()

        if sid == FREESECT or sid >= len(self._directory) or sid in visited:
            return

        visited.add(sid)
        entry = self._directory[sid]

        if entry.entry_type == STGTY_EMPTY:
            return

        # Left siblings first
        if entry.sid_left != FREESECT:
            self._walk_siblings(entry.sid_left, parent_path, visited)

        # This entry
        path = f"{parent_path}/{entry.name}" if parent_path else entry.name
        entry.path = path
        self._path_index[path] = entry.sid

        # Process children
        if entry.sid_child != FREESECT and entry.sid_child < len(self._directory):
            self._walk_siblings(entry.sid_child, path, set())

        # Right siblings
        if entry.sid_right != FREESECT:
            self._walk_siblings(entry.sid_right, parent_path, visited)

    # =========================================================================
    # Low-level I/O
    # =========================================================================

    def _read_sector(self, sector: int) -> bytes:
        """
        Read a single sector.
        """
        if sector >= MAXREGSECT:
            return b'\x00' * self._sector_size

        # Sector 0 starts after the 512-byte header
        data = self._require_data()
        offset = HEADER_SIZE + sector * self._sector_size
        return bytes(data[offset:offset + self._sector_size])

    def _read_stream_by_sector(self, start_sector: int, size: int | None = None,
                               use_fat: bool = True) -> bytes:
        """
        Read stream data following FAT or MiniFAT chain.
        """
        if start_sector == ENDOFCHAIN or start_sector >= MAXREGSECT:
            return b''

        data = []
        sector = start_sector
        count = 0
        fat = self._fat if use_fat else self._minifat

        while sector != ENDOFCHAIN and sector < MAXREGSECT and count < 100000:
            if use_fat:
                sector_data = self._read_sector(sector)
            else:
                sector_data = self._read_mini_sector(sector)

            data.append(sector_data)

            if sector < len(fat):
                sector = fat[sector]
            else:
                break
            count += 1

        result = b''.join(data)
        if size is not None:
            result = result[:size]
        return result

    def _read_mini_sector(self, mini_sector: int) -> bytes:
        """
        Read a mini sector from the mini stream.
        """
        if self._root is None:
            return b'\x00' * self._mini_sector_size

        # Mini stream is stored as root entry's data
        # First, get the root's sector chain
        mini_stream = self._read_stream_by_sector(
            self._root.start_sector,
            self._root.size,
            use_fat=True
        )

        # Read mini sector from mini stream
        offset = mini_sector * self._mini_sector_size
        return mini_stream[offset:offset + self._mini_sector_size]

    def _get_sector_chain(self, start_sector: int, use_fat: bool = True) -> list[int]:
        """
        Get the chain of sectors for a stream.
        """
        chain = []
        sector = start_sector
        fat = self._fat if use_fat else self._minifat
        count = 0

        while sector != ENDOFCHAIN and sector < MAXREGSECT and count < 100000:
            chain.append(sector)
            if sector < len(fat):
                sector = fat[sector]
            else:
                break
            count += 1

        return chain

    # =========================================================================
    # Public Read API
    # =========================================================================

    def listdir(self, streams: bool = True, storages: bool = False) -> list[list[str]]:
        """
        List directory entries.
        
        Args:
            streams: Include streams
            storages: Include storages
        
        Returns:
            List of paths as lists of path components
        """
        result = []

        for entry in self._directory:
            if entry.entry_type == STGTY_EMPTY:
                continue
            if entry.is_root:
                continue

            include = False
            if streams and entry.is_stream:
                include = True
            if storages and entry.is_storage:
                include = True

            if include and entry.path:
                result.append(entry.path.split('/'))

        return result

    def exists(self, path: str | list[str]) -> bool:
        """
        Check if a path exists.
        """
        if isinstance(path, list):
            path = '/'.join(path)
        return path in self._path_index

    def get_type(self, path: str | list[str]) -> int:
        """
        Get entry type (for olefile API compatibility).
        
        Returns:
            0: Empty/not found
            1: Storage
            2: Stream
            5: Root storage
        """
        if isinstance(path, list):
            path = '/'.join(path)

        if path not in self._path_index:
            return 0  # STGTY_EMPTY

        entry = self._directory[self._path_index[path]]
        return entry.entry_type

    def get_size(self, path: str | list[str]) -> int:
        """
        Get stream size.
        """
        if isinstance(path, list):
            path = '/'.join(path)

        if path not in self._path_index:
            raise ValueError(f"Path not found: {path}")

        entry = self._directory[self._path_index[path]]
        return entry.size

    def openstream(self, path: str | list[str]) -> bytes:
        """
        Read stream data.
        
        Args:
            path: Stream path as string or list
        
        Returns:
            Stream data as bytes
        
        Note:
            This method is lenient about entry types - it will attempt to read
            data from any entry that has a valid start sector and size, not just
            strict stream entries (type 2). This matches olefile behavior and
            handles Altium's non-standard OLE usage.
        """
        if isinstance(path, list):
            path = '/'.join(path)

        if path not in self._path_index:
            raise ValueError(f"Stream not found: {path}")

        entry = self._directory[self._path_index[path]]

        # Be lenient - allow reading from any entry with data, not just streams
        # Altium sometimes marks data entries with non-standard types
        if entry.is_storage and entry.size == 0:
            raise ValueError(f"Not a stream (storage with no data): {path}")

        if entry.size == 0:
            return b''

        if entry.is_mini:
            # Read from mini stream
            return self._read_stream_by_sector(
                entry.start_sector,
                entry.size,
                use_fat=False
            )
        else:
            # Read from regular sectors
            return self._read_stream_by_sector(
                entry.start_sector,
                entry.size,
                use_fat=True
            )

    @property
    def root(self) -> OleDirEntry | None:
        """
        Get root directory entry.
        """
        return self._root

    @property
    def sectorsize(self) -> int:
        """
        Get sector size.
        """
        return self._sector_size

    @property
    def minisectorcutoff(self) -> int:
        """
        Get mini stream cutoff size.
        """
        return self._mini_cutoff

    # =========================================================================
    # Write API
    # =========================================================================

    def modify_stream(self, path: str | list[str], data: bytes) -> None:
        """
        Mark a stream for modification.
        
        Args:
            path: Stream path
            data: New stream data (must be same size as original)
        
        Raises:
            ValueError: If stream not found or size mismatch
        """
        if isinstance(path, list):
            path = '/'.join(path)

        if path not in self._path_index:
            raise ValueError(f"Stream not found: {path}")

        entry = self._directory[self._path_index[path]]

        if len(data) != entry.size:
            raise ValueError(
                f"Stream size changed: {path} ({entry.size} -> {len(data)}). "
                "In-place modification requires identical size."
            )

        self._modifications[path] = data

    def write(self, filepath: str | Path) -> None:
        """
        Write OLE file with modifications.
        
        Args:
            filepath: Output file path
        """
        # Apply modifications
        for path, data in self._modifications.items():
            self._write_stream_inplace(path, data)

        # Write to file
        filepath = Path(filepath)
        filepath.write_bytes(bytes(self._require_data()))

    def _write_stream_inplace(self, path: str, data: bytes) -> None:
        """
        Write stream data in-place.
        """
        entry = self._directory[self._path_index[path]]

        if entry.is_mini:
            self._write_mini_stream_inplace(entry, data)
        else:
            self._write_regular_stream_inplace(entry, data)

    def _write_regular_stream_inplace(self, entry: OleDirEntry, data: bytes) -> None:
        """
        Write data to regular stream sectors.
        """
        chain = self._get_sector_chain(entry.start_sector, use_fat=True)
        offset = 0
        file_data = self._require_data()

        for sector in chain:
            file_offset = HEADER_SIZE + sector * self._sector_size
            chunk_size = min(self._sector_size, len(data) - offset)

            if chunk_size <= 0:
                break

            file_data[file_offset:file_offset + chunk_size] = data[offset:offset + chunk_size]
            offset += chunk_size

    def _write_mini_stream_inplace(self, entry: OleDirEntry, data: bytes) -> None:
        """
        Write data to mini stream sectors.
        """
        if self._root is None:
            return

        # Get mini stream chain
        mini_chain = self._get_sector_chain(entry.start_sector, use_fat=False)

        # Get root's sector chain (contains mini stream)
        root_chain = self._get_sector_chain(self._root.start_sector, use_fat=True)

        offset = 0
        file_data = self._require_data()
        for mini_sector in mini_chain:
            # Calculate which regular sector contains this mini sector
            mini_per_sector = self._sector_size // self._mini_sector_size
            containing_idx = mini_sector // mini_per_sector
            offset_in_sector = (mini_sector % mini_per_sector) * self._mini_sector_size

            if containing_idx >= len(root_chain):
                break

            containing_sector = root_chain[containing_idx]
            file_offset = HEADER_SIZE + containing_sector * self._sector_size + offset_in_sector

            chunk_size = min(self._mini_sector_size, len(data) - offset)
            if chunk_size <= 0:
                break

            file_data[file_offset:file_offset + chunk_size] = data[offset:offset + chunk_size]
            offset += chunk_size

    # =========================================================================
    # Context Manager
    # =========================================================================

    def close(self) -> None:
        """
        Close the OLE file.
        """
        self._data = None
        self._fat = []
        self._minifat = []
        self._directory = []
        self._path_index = {}
        self._modifications = {}

    def __enter__(self) -> 'AltiumOleFile':
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


# =============================================================================
# Utility Functions
# =============================================================================

def is_ole_file(filename: str | Path | bytes) -> bool:
    """
    Check if a file is an OLE file.
    """
    if isinstance(filename, (str, Path)):
        with open(filename, 'rb') as f:
            header = f.read(8)
    else:
        header = filename[:8]

    return header == OLE_MAGIC


# =============================================================================
# AltiumOleWriter - Creates new OLE files
# =============================================================================

@dataclass
class _WriterEntry:
    """
    Internal directory entry for writer.
    """
    name: str
    path: str
    entry_type: int  # STGTY_STREAM, STGTY_STORAGE, STGTY_ROOT
    data: bytes = b''
    clsid: bytes = field(default_factory=lambda: b'\x00' * 16)

    # Tree links (set during build)
    sid: int = -1
    left_sid: int = FREESECT
    right_sid: int = FREESECT
    child_sid: int = FREESECT
    color: int = 1  # 1 = black, 0 = red
    start_sector: int = ENDOFCHAIN

    @property
    def is_mini(self) -> bool:
        return self.entry_type == STGTY_STREAM and len(self.data) < MINI_STREAM_CUTOFF


class AltiumOleWriter:
    """
    Creates new OLE files for Altium formats.
    
    Features:
    - Version 3 format (512-byte sectors)
    - Altium CLSID by default
    - Handles mini-stream for small data (<4096 bytes)
    - Automatic storage creation from paths
    """

    def __init__(self, clsid: bytes | None = None) -> None:
        """
        Initialize writer.
        
        Args:
            clsid: Root entry CLSID (defaults to Altium CLSID)
        """
        self._clsid = clsid or ALTIUM_CLSID_BYTES
        self._streams: dict[str, bytes] = {}
        self._storages: set[str] = set()

        # Build parameters (version 3)
        self._sector_size = 512
        self._mini_sector_size = MINI_SECTOR_SIZE
        self._mini_cutoff = MINI_STREAM_CUTOFF

    def add_stream(self, path: str, data: bytes) -> None:
        """
        Add a stream to the file.
        
        Args:
            path: Stream path (e.g., "FileHeader" or "Symbol/Data")
            data: Stream data
        """
        # Normalize path
        path = path.replace('\\', '/')

        # Extract and create storage paths
        parts = path.split('/')
        for i in range(len(parts) - 1):
            storage_path = '/'.join(parts[:i + 1])
            self._storages.add(storage_path)

        self._streams[path] = data

    def addEntry(self, path: str, data: bytes | None = None, storage: bool = False) -> None:
        """
        Compatibility method matching OleWriter API.
        
        Args:
            path: Entry path
            data: Stream data (ignored for storages)
            storage: If True, creates a storage instead of stream
        """
        if storage:
            self._storages.add(path.replace('\\', '/'))
        else:
            self.add_stream(path, data or b'')

    def editEntry(self, path: str | list[str], data: bytes | None = None) -> None:
        """
        Edit an existing stream entry (for compatibility with OleWriter API).
        
        Args:
            path: Entry path (can be string or list)
            data: New stream data
        """
        # Normalize path
        if isinstance(path, list):
            path = '/'.join(path)
        path = path.replace('\\', '/')

        if path in self._streams:
            if data is not None:
                self._streams[path] = data
        else:
            # If doesn't exist yet, add it
            self.add_stream(path, data or b'')

    def fromOleFile(self, ole: Any) -> None:
        """
        Copy all streams and storages from an existing OLE file.
        
        Args:
            ole: AltiumOleFile or olefile.OleFileIO instance (already open)
        """
        # Copy CLSID from root entry
        if hasattr(ole, 'root') and ole.root:
            clsid = getattr(ole.root, 'clsid', None)
            if clsid:
                self._clsid = clsid_to_bytes(clsid) if isinstance(clsid, str) else (clsid or self._clsid)

        # List all entries
        for path in ole.listdir(streams=True, storages=True):
            path_str = '/'.join(path)

            # Get entry info
            try:
                entry_type = ole.get_type(path)
            except (OSError, AttributeError):
                # Fallback: try to read as stream
                try:
                    data = ole.openstream(path)
                    # AltiumOleFile returns bytes, olefile returns file-like object
                    if not isinstance(data, bytes):
                        data = data.read()
                    self.add_stream(path_str, data)
                except Exception:
                    self._storages.add(path_str)
                continue

            # Check entry type
            if entry_type == 1:  # Storage
                self._storages.add(path_str)
            elif entry_type == 2:  # Stream
                try:
                    data = ole.openstream(path)
                    # AltiumOleFile returns bytes, olefile returns file-like object
                    if not isinstance(data, bytes):
                        data = data.read()
                    self.add_stream(path_str, data)
                except Exception:
                    pass
            else:
                # Unknown type, skip
                try:
                    with ole.openstream(path) as f:
                        data = f.read()
                    self.add_stream(path_str, data)
                except Exception:
                    self._storages.add(path_str)

    def write(self, filepath: str | Path) -> None:
        """
        Write the OLE file to disk.
        
        Args:
            filepath: Output file path
        """
        filepath = Path(filepath)

        # Build entries list
        entries = self._build_entries()

        # Calculate layout
        layout = self._calculate_layout(entries)

        # Build file data
        file_data = self._build_file(entries, layout)

        # Write to disk
        filepath.write_bytes(file_data)

    def _build_entries(self) -> list[_WriterEntry]:
        """
        Build sorted list of directory entries.
        """
        entries = []

        # Root entry
        root = _WriterEntry(
            name="Root Entry",
            path="",
            entry_type=STGTY_ROOT,
            clsid=self._clsid,
        )
        entries.append(root)

        # Collect all storages (including implicit ones)
        all_storages = set(self._storages)
        for path in self._streams:
            parts = path.split('/')
            for i in range(len(parts) - 1):
                all_storages.add('/'.join(parts[:i + 1]))

        # Add storages
        for path in sorted(all_storages):
            name = path.split('/')[-1]
            entries.append(_WriterEntry(
                name=name,
                path=path,
                entry_type=STGTY_STORAGE,
            ))

        # Add streams
        for path, data in sorted(self._streams.items()):
            name = path.split('/')[-1]
            entries.append(_WriterEntry(
                name=name,
                path=path,
                entry_type=STGTY_STREAM,
                data=data,
            ))

        # Assign SIDs
        for i, entry in enumerate(entries):
            entry.sid = i

        # Build tree structure using red-black tree rules
        self._build_tree(entries)

        return entries

    def _build_tree(self, entries: list[_WriterEntry]) -> None:
        """
        Build directory tree structure.
        """
        # Group entries by parent path
        children_by_parent: dict[str, list[_WriterEntry]] = {}

        for entry in entries:
            if entry.entry_type == STGTY_ROOT:
                continue

            # Get parent path
            if '/' in entry.path:
                parent_path = '/'.join(entry.path.split('/')[:-1])
            else:
                parent_path = ""  # Root's children

            if parent_path not in children_by_parent:
                children_by_parent[parent_path] = []
            children_by_parent[parent_path].append(entry)

        # Build binary tree for each parent
        for parent_path, children in children_by_parent.items():
            if not children:
                continue

            # Find parent entry
            parent = None
            for entry in entries:
                if entry.path == parent_path or (parent_path == "" and entry.entry_type == STGTY_ROOT):
                    parent = entry
                    break

            if parent is None:
                continue

            # Sort children by (length, uppercase name) - OLE spec
            children.sort(key=lambda e: (len(e.name), e.name.upper()))

            # Build balanced binary tree
            root_child = self._build_balanced_tree(children, 0, len(children) - 1)
            if root_child:
                parent.child_sid = root_child.sid

    def _build_balanced_tree(self, children: list[_WriterEntry],
                             lo: int, hi: int) -> _WriterEntry | None:
        """
        Build balanced binary tree recursively.
        """
        if lo > hi:
            return None

        mid = (lo + hi) // 2
        node = children[mid]

        # Set color (simplified: root black, all others black)
        node.color = 1  # black

        # Build subtrees
        left = self._build_balanced_tree(children, lo, mid - 1)
        right = self._build_balanced_tree(children, mid + 1, hi)

        node.left_sid = left.sid if left else FREESECT
        node.right_sid = right.sid if right else FREESECT

        return node

    def _calculate_layout(self, entries: list[_WriterEntry]) -> dict:
        """
        Calculate sector layout for the file.
        """
        layout = {
            'dir_sectors': 0,
            'fat_sectors': 0,
            'difat_sectors': 0,
            'minifat_sectors': 0,
            'mini_stream_sectors': 0,
            'large_entries': [],
            'mini_entries': [],
        }

        # Classify entries
        for entry in entries:
            if entry.entry_type == STGTY_STREAM:
                if entry.is_mini and len(entry.data) > 0:
                    layout['mini_entries'].append(entry)
                elif len(entry.data) > 0:
                    layout['large_entries'].append(entry)

        # Calculate directory sectors
        layout['dir_sectors'] = self._ceil_div(len(entries), 4)  # 4 entries per sector

        # Calculate mini stream sectors
        #
        # CRITICAL: Each entry needs its own mini sector(s) - entries cannot share mini sectors.
        # The OLE mini stream stores small streams (<4096 bytes) in 64-byte mini sectors.
        # Each entry's data is padded to a mini sector boundary and stored contiguously.
        #
        # Entries cannot share mini sectors. This calculation used to undercount:
        #     total_mini_size = sum(len(e.data) for e in mini_entries)  # e.g., 47 bytes
        #     mini_sectors_needed = ceil(47/64) = 1
        #
        # This was WRONG because it assumed entries could share mini sectors.
        # With 9 entries of ~5 bytes each, we need 9 mini sectors (one per entry),
        # not 1 mini sector for all 47 bytes combined.
        #
        # This bug caused sector collisions: mini stream would allocate too few file sectors,
        # and large stream entries would start at a sector already used by mini stream data,
        # corrupting the file when 10+ entries were present.
        #
        # CORRECT: Sum the mini sectors needed for each entry individually:
        mini_sectors_needed = sum(
            self._ceil_div(len(e.data), self._mini_sector_size)
            for e in layout['mini_entries']
        )
        layout['mini_stream_sectors'] = self._ceil_div(
            mini_sectors_needed * self._mini_sector_size,
            self._sector_size
        )

        # Calculate mini FAT sectors
        layout['minifat_sectors'] = self._ceil_div(mini_sectors_needed, 128)  # 128 entries per sector

        # Calculate large stream sectors
        large_sectors = 0
        for entry in layout['large_entries']:
            large_sectors += self._ceil_div(len(entry.data), self._sector_size)
        layout['large_stream_sectors'] = large_sectors

        # Total non-FAT sectors excluding DIFAT (which depends on FAT count).
        nonfat_base = (
            layout['dir_sectors'] +
            layout['minifat_sectors'] +
            layout['mini_stream_sectors'] +
            large_sectors
        )

        # FAT/DIFAT sizing is coupled:
        # - FAT sector count depends on total sector count.
        # - DIFAT sector count depends on FAT sector count when FAT > 109.
        fat_sectors = max(1, self._ceil_div(nonfat_base + 1, 128))
        for _ in range(32):
            difat_sectors = self._ceil_div(max(0, fat_sectors - 109), 127)
            total_sectors = nonfat_base + difat_sectors + fat_sectors
            needed_fat = self._ceil_div(total_sectors, 128)
            if needed_fat == fat_sectors:
                break
            fat_sectors = max(1, needed_fat)
        else:  # pragma: no cover - defensive guard
            raise RuntimeError("Failed to converge FAT/DIFAT sector sizing")

        layout['fat_sectors'] = fat_sectors
        layout['difat_sectors'] = self._ceil_div(max(0, fat_sectors - 109), 127)

        return layout

    def _ceil_div(self, a: int, b: int) -> int:
        """
        Ceiling division.
        """
        if b == 0:
            return 0
        return (a + b - 1) // b

    def _build_file(self, entries: list[_WriterEntry], layout: dict) -> bytes:
        """
        Build the complete OLE file.
        """
        # Calculate sector positions
        fat_start = 0
        difat_start = fat_start + layout['fat_sectors']
        dir_start = difat_start + layout['difat_sectors']
        minifat_start = dir_start + layout['dir_sectors']
        ministream_start = minifat_start + layout['minifat_sectors']
        large_start = ministream_start + layout['mini_stream_sectors']

        # Assign start sectors to entries
        self._assign_sectors(entries, layout, ministream_start, large_start)

        # Build file
        data = bytearray()

        # 1. Header
        data.extend(self._build_header(layout, dir_start, minifat_start, difat_start))

        # 2. FAT sectors
        data.extend(self._build_fat(entries, layout, dir_start, minifat_start,
                                    ministream_start, large_start, difat_start))

        # 3. DIFAT sectors (only when FAT > 109 sectors)
        data.extend(self._build_difat(layout, difat_start))

        # 4. Directory sectors
        data.extend(self._build_directory(entries, layout, ministream_start))

        # 5. Mini FAT sectors
        data.extend(self._build_minifat(layout))

        # 6. Mini stream
        data.extend(self._build_mini_stream(layout))

        # 7. Large streams
        data.extend(self._build_large_streams(layout))

        return bytes(data)

    def _assign_sectors(self, entries: list[_WriterEntry], layout: dict,
                       ministream_start: int, large_start: int) -> None:
        """
        Assign start sectors to stream entries.
        """
        # Mini stream entries - start sector is mini sector index
        mini_sector = 0
        for entry in layout['mini_entries']:
            entry.start_sector = mini_sector
            mini_sector += self._ceil_div(len(entry.data), self._mini_sector_size)

        # Large stream entries
        current_sector = large_start
        for entry in layout['large_entries']:
            entry.start_sector = current_sector
            current_sector += self._ceil_div(len(entry.data), self._sector_size)

        # Root entry points to mini stream
        if layout['mini_stream_sectors'] > 0:
            entries[0].start_sector = ministream_start
        else:
            entries[0].start_sector = ENDOFCHAIN

    def _build_header(self, layout: dict, dir_start: int, minifat_start: int,
                      difat_start: int) -> bytes:
        """
        Build 512-byte OLE header.
        """
        header = bytearray(HEADER_SIZE)

        # Magic
        header[0:8] = OLE_MAGIC

        # CLSID (16 bytes at offset 8) - leave as zeros

        # Minor version (offset 0x18)
        struct.pack_into('<H', header, 0x18, 0x003E)

        # Major version (offset 0x1A) - version 3
        struct.pack_into('<H', header, 0x1A, 0x0003)

        # Byte order (offset 0x1C) - little endian
        struct.pack_into('<H', header, 0x1C, 0xFFFE)

        # Sector shift (offset 0x1E) - 9 = 512 bytes
        struct.pack_into('<H', header, 0x1E, 0x0009)

        # Mini sector shift (offset 0x20) - 6 = 64 bytes
        struct.pack_into('<H', header, 0x20, 0x0006)

        # Reserved (6 bytes at 0x22)

        # Number of directory sectors (offset 0x28) - must be 0 for v3
        struct.pack_into('<I', header, 0x28, 0)

        # Number of FAT sectors (offset 0x2C)
        struct.pack_into('<I', header, 0x2C, layout['fat_sectors'])

        # First directory sector (offset 0x30)
        struct.pack_into('<I', header, 0x30, dir_start)

        # Transaction signature (offset 0x34)
        struct.pack_into('<I', header, 0x34, 0)

        # Mini stream cutoff (offset 0x38)
        struct.pack_into('<I', header, 0x38, self._mini_cutoff)

        # First mini FAT sector (offset 0x3C)
        if layout['minifat_sectors'] > 0:
            struct.pack_into('<I', header, 0x3C, minifat_start)
        else:
            struct.pack_into('<I', header, 0x3C, ENDOFCHAIN)

        # Number of mini FAT sectors (offset 0x40)
        struct.pack_into('<I', header, 0x40, layout['minifat_sectors'])

        # First DIFAT sector (offset 0x44)
        if layout['difat_sectors'] > 0:
            struct.pack_into('<I', header, 0x44, difat_start)
        else:
            struct.pack_into('<I', header, 0x44, ENDOFCHAIN)

        # Number of DIFAT sectors (offset 0x48)
        struct.pack_into('<I', header, 0x48, layout['difat_sectors'])

        # DIFAT array (109 entries starting at 0x4C)
        for i in range(109):
            if i < layout['fat_sectors']:
                struct.pack_into('<I', header, 0x4C + i * 4, i)
            else:
                struct.pack_into('<I', header, 0x4C + i * 4, FREESECT)

        return bytes(header)

    def _build_fat(self, entries: list[_WriterEntry], layout: dict,
                   dir_start: int, minifat_start: int,
                   ministream_start: int, large_start: int,
                   difat_start: int) -> bytes:
        """
        Build FAT sectors.
        """
        # Initialize FAT
        fat = [FREESECT] * (layout['fat_sectors'] * 128)

        # FAT sectors are marked as FATSECT
        for i in range(layout['fat_sectors']):
            fat[i] = FATSECT

        # DIFAT sectors are marked as DIFSECT
        for i in range(layout['difat_sectors']):
            fat[difat_start + i] = DIFSECT

        # Directory chain
        for i in range(layout['dir_sectors']):
            sector = dir_start + i
            if i < layout['dir_sectors'] - 1:
                fat[sector] = sector + 1
            else:
                fat[sector] = ENDOFCHAIN

        # Mini FAT chain
        for i in range(layout['minifat_sectors']):
            sector = minifat_start + i
            if i < layout['minifat_sectors'] - 1:
                fat[sector] = sector + 1
            else:
                fat[sector] = ENDOFCHAIN

        # Mini stream chain (root entry data)
        for i in range(layout['mini_stream_sectors']):
            sector = ministream_start + i
            if i < layout['mini_stream_sectors'] - 1:
                fat[sector] = sector + 1
            else:
                fat[sector] = ENDOFCHAIN

        # Large stream chains
        current_sector = large_start
        for entry in layout['large_entries']:
            sectors_needed = self._ceil_div(len(entry.data), self._sector_size)
            for i in range(sectors_needed):
                if i < sectors_needed - 1:
                    fat[current_sector + i] = current_sector + i + 1
                else:
                    fat[current_sector + i] = ENDOFCHAIN
            current_sector += sectors_needed

        # Build FAT data
        fat_data = bytearray()
        for entry in fat[:layout['fat_sectors'] * 128]:
            fat_data.extend(struct.pack('<I', entry))

        return bytes(fat_data)

    def _build_difat(self, layout: dict, difat_start: int) -> bytes:
        """
        Build DIFAT sectors when FAT requires more than 109 sector pointers.
        """
        difat_sectors = layout.get('difat_sectors', 0)
        if difat_sectors <= 0:
            return b''

        fat_sectors = int(layout['fat_sectors'])
        extra_fat = list(range(109, fat_sectors))
        payload = bytearray()
        cursor = 0
        entries_per_sector = (self._sector_size // 4) - 1  # 127 for 512-byte sectors

        for i in range(difat_sectors):
            sector = bytearray(self._sector_size)

            for j in range(entries_per_sector):
                idx = cursor + j
                value = extra_fat[idx] if idx < len(extra_fat) else FREESECT
                struct.pack_into('<I', sector, j * 4, value)

            if i < difat_sectors - 1:
                next_difat = difat_start + i + 1
            else:
                next_difat = ENDOFCHAIN
            struct.pack_into('<I', sector, entries_per_sector * 4, next_difat)

            payload.extend(sector)
            cursor += entries_per_sector

        return bytes(payload)

    def _build_directory(self, entries: list[_WriterEntry], layout: dict,
                        ministream_start: int) -> bytes:
        """
        Build directory sectors.
        """
        dir_data = bytearray()

        for entry in entries:
            entry_data = bytearray(DIR_ENTRY_SIZE)

            # Name (64 bytes UTF-16LE with null terminator)
            name_bytes = entry.name.encode('utf-16-le')[:62]
            entry_data[0:len(name_bytes)] = name_bytes

            # Name length (including null terminator)
            struct.pack_into('<H', entry_data, 0x40, len(name_bytes) + 2)

            # Entry type
            entry_data[0x42] = entry.entry_type

            # Color (0 = red, 1 = black)
            entry_data[0x43] = entry.color

            # Left sibling
            struct.pack_into('<I', entry_data, 0x44, entry.left_sid)

            # Right sibling
            struct.pack_into('<I', entry_data, 0x48, entry.right_sid)

            # Child
            struct.pack_into('<I', entry_data, 0x4C, entry.child_sid)

            # CLSID
            entry_data[0x50:0x60] = entry.clsid

            # User flags (offset 0x60)
            struct.pack_into('<I', entry_data, 0x60, 0)

            # Create time (offset 0x64)
            struct.pack_into('<Q', entry_data, 0x64, 0)

            # Modify time (offset 0x6C)
            struct.pack_into('<Q', entry_data, 0x6C, 0)

            # Start sector (offset 0x74)
            struct.pack_into('<I', entry_data, 0x74, entry.start_sector)

            # Size (offset 0x78)
            if entry.entry_type == STGTY_ROOT:
                # Root entry size is mini stream size (sum of padded entry sizes)
                total_mini_sectors = 0
                for e in layout['mini_entries']:
                    total_mini_sectors += self._ceil_div(len(e.data), self._mini_sector_size)
                struct.pack_into('<Q', entry_data, 0x78, total_mini_sectors * self._mini_sector_size)
            elif entry.entry_type == STGTY_STREAM:
                struct.pack_into('<Q', entry_data, 0x78, len(entry.data))
            else:
                struct.pack_into('<Q', entry_data, 0x78, 0)

            dir_data.extend(entry_data)

        # Pad to sector boundary
        while len(dir_data) % self._sector_size != 0:
            # Empty directory entry
            empty = bytearray(DIR_ENTRY_SIZE)
            empty[0x42] = STGTY_EMPTY
            struct.pack_into('<I', empty, 0x44, FREESECT)
            struct.pack_into('<I', empty, 0x48, FREESECT)
            struct.pack_into('<I', empty, 0x4C, FREESECT)
            dir_data.extend(empty)

        return bytes(dir_data)

    def _build_minifat(self, layout: dict) -> bytes:
        """
        Build mini FAT sectors.
        """
        if layout['minifat_sectors'] == 0:
            return b''

        # Calculate total mini sectors needed
        total_mini_size = sum(len(e.data) for e in layout['mini_entries'])
        self._ceil_div(total_mini_size, self._mini_sector_size)

        # Build mini FAT
        minifat = []
        current_sector = 0
        for entry in layout['mini_entries']:
            sectors_needed = self._ceil_div(len(entry.data), self._mini_sector_size)
            for i in range(sectors_needed):
                if i < sectors_needed - 1:
                    minifat.append(current_sector + i + 1)
                else:
                    minifat.append(ENDOFCHAIN)
            current_sector += sectors_needed

        # Pad to sector boundary
        while len(minifat) % 128 != 0:
            minifat.append(FREESECT)

        # Build data
        minifat_data = bytearray()
        for entry in minifat:
            minifat_data.extend(struct.pack('<I', entry))

        return bytes(minifat_data)

    def _build_mini_stream(self, layout: dict) -> bytes:
        """
        Build mini stream (concatenated small stream data).
        """
        if layout['mini_stream_sectors'] == 0:
            return b''

        mini_data = bytearray()
        for entry in layout['mini_entries']:
            mini_data.extend(entry.data)
            # Pad to mini sector boundary
            remainder = len(entry.data) % self._mini_sector_size
            if remainder:
                mini_data.extend(b'\x00' * (self._mini_sector_size - remainder))

        # Pad to sector boundary
        while len(mini_data) % self._sector_size != 0:
            mini_data.extend(b'\x00' * self._mini_sector_size)

        return bytes(mini_data)

    def _build_large_streams(self, layout: dict) -> bytes:
        """
        Build large stream data.
        """
        large_data = bytearray()
        for entry in layout['large_entries']:
            large_data.extend(entry.data)
            # Pad to sector boundary
            remainder = len(entry.data) % self._sector_size
            if remainder:
                large_data.extend(b'\x00' * (self._sector_size - remainder))

        return bytes(large_data)
