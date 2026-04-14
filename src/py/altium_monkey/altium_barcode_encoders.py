"""
Barcode encoders for Code 39 and Code 128 bit patterns.
"""

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class BarcodeEncoding:
    """
    Result of barcode encoding.
    
        Attributes:
            bits: Bit pattern. True = bar (black), False = space (white).
            symbology: Human-readable name ("Code 39" or "Code 128").
            content: Original text content.
    """
    bits: list[bool]
    symbology: str
    content: str


# ================================================================== #
# Code 39
# ================================================================== #

# Encoding table: char -> (checksum_value, 12-bit pattern)
_CODE39_TABLE: dict[str, tuple[int, list[bool]]] = {
    '0': (0,  [True, False, True, False, False, True, True, False, True, True, False, True]),
    '1': (1,  [True, True, False, True, False, False, True, False, True, False, True, True]),
    '2': (2,  [True, False, True, True, False, False, True, False, True, False, True, True]),
    '3': (3,  [True, True, False, True, True, False, False, True, False, True, False, True]),
    '4': (4,  [True, False, True, False, False, True, True, False, True, False, True, True]),
    '5': (5,  [True, True, False, True, False, False, True, True, False, True, False, True]),
    '6': (6,  [True, False, True, True, False, False, True, True, False, True, False, True]),
    '7': (7,  [True, False, True, False, False, True, False, True, True, False, True, True]),
    '8': (8,  [True, True, False, True, False, False, True, False, True, True, False, True]),
    '9': (9,  [True, False, True, True, False, False, True, False, True, True, False, True]),
    'A': (10, [True, True, False, True, False, True, False, False, True, False, True, True]),
    'B': (11, [True, False, True, True, False, True, False, False, True, False, True, True]),
    'C': (12, [True, True, False, True, True, False, True, False, False, True, False, True]),
    'D': (13, [True, False, True, False, True, True, False, False, True, False, True, True]),
    'E': (14, [True, True, False, True, False, True, True, False, False, True, False, True]),
    'F': (15, [True, False, True, True, False, True, True, False, False, True, False, True]),
    'G': (16, [True, False, True, False, True, False, False, True, True, False, True, True]),
    'H': (17, [True, True, False, True, False, True, False, False, True, True, False, True]),
    'I': (18, [True, False, True, True, False, True, False, False, True, True, False, True]),
    'J': (19, [True, False, True, False, True, True, False, False, True, True, False, True]),
    'K': (20, [True, True, False, True, False, True, False, True, False, False, True, True]),
    'L': (21, [True, False, True, True, False, True, False, True, False, False, True, True]),
    'M': (22, [True, True, False, True, True, False, True, False, True, False, False, True]),
    'N': (23, [True, False, True, False, True, True, False, True, False, False, True, True]),
    'O': (24, [True, True, False, True, False, True, True, False, True, False, False, True]),
    'P': (25, [True, False, True, True, False, True, True, False, True, False, False, True]),
    'Q': (26, [True, False, True, False, True, False, True, True, False, False, True, True]),
    'R': (27, [True, True, False, True, False, True, False, True, True, False, False, True]),
    'S': (28, [True, False, True, True, False, True, False, True, True, False, False, True]),
    'T': (29, [True, False, True, False, True, True, False, True, True, False, False, True]),
    'U': (30, [True, True, False, False, True, False, True, False, True, False, True, True]),
    'V': (31, [True, False, False, True, True, False, True, False, True, False, True, True]),
    'W': (32, [True, True, False, False, True, True, False, True, False, True, False, True]),
    'X': (33, [True, False, False, True, False, True, True, False, True, False, True, True]),
    'Y': (34, [True, True, False, False, True, False, True, True, False, True, False, True]),
    'Z': (35, [True, False, False, True, True, False, True, True, False, True, False, True]),
    '-': (36, [True, False, False, True, False, True, False, True, True, False, True, True]),
    '.': (37, [True, True, False, False, True, False, True, False, True, True, False, True]),
    ' ': (38, [True, False, False, True, True, False, True, False, True, True, False, True]),
    '$': (39, [True, False, False, True, False, False, True, False, False, True, False, True]),
    '/': (40, [True, False, False, True, False, False, True, False, True, False, False, True]),
    '+': (41, [True, False, False, True, False, True, False, False, True, False, False, True]),
    '%': (42, [True, False, True, False, False, True, False, False, True, False, False, True]),
    '*': (-1, [True, False, False, True, False, True, True, False, True, True, False, True]),
}


def _code39_checksum(content: str) -> str:
    """
    Compute Code 39 mod-43 checksum character.
    """
    total = 0
    for ch in content:
        entry = _CODE39_TABLE.get(ch)
        if entry is None or entry[0] < 0:
            return '#'
        total += entry[0]
    total %= 43
    for ch, (val, _) in _CODE39_TABLE.items():
        if val == total:
            return ch
    return '#'


def encode_code39(text: str, include_checksum: bool = False) -> BarcodeEncoding:
    """
    Encode text as Code 39 barcode.
    
        Args:
            text: Text to encode (uppercase A-Z, 0-9, space, -.$/+%)
            include_checksum: If True, append mod-43 checksum character
    
        Returns:
            BarcodeEncoding with bit pattern
    
        Raises:
            ValueError: If text contains invalid characters
    """
    if '*' in text:
        raise ValueError("Code 39 content cannot contain '*'")

    # Wrap in start/stop characters
    encoded = '*' + text
    if include_checksum:
        encoded += _code39_checksum(text)
    encoded += '*'

    bits: list[bool] = []
    for i, ch in enumerate(encoded):
        if i > 0:
            bits.append(False)  # inter-character gap
        entry = _CODE39_TABLE.get(ch)
        if entry is None:
            raise ValueError(f"Code 39: invalid character '{ch}'")
        bits.extend(entry[1])

    return BarcodeEncoding(bits=bits, symbology="Code 39", content=text)


# ================================================================== #
# Code 128
# ================================================================== #

# Encoding table: 107 patterns, each 11 modules (stop = 13 modules)
_CODE128_PATTERNS: list[list[bool]] = [
    [True, True, False, True, True, False, False, True, True, False, False],            # 0
    [True, True, False, False, True, True, False, True, True, False, False],            # 1
    [True, True, False, False, True, True, False, False, True, True, False],            # 2
    [True, False, False, True, False, False, True, True, False, False, False],          # 3
    [True, False, False, True, False, False, False, True, True, False, False],          # 4
    [True, False, False, False, True, False, False, True, True, False, False],          # 5
    [True, False, False, True, True, False, False, True, False, False, False],          # 6
    [True, False, False, True, True, False, False, False, True, False, False],          # 7
    [True, False, False, False, True, True, False, False, True, False, False],          # 8
    [True, True, False, False, True, False, False, True, False, False, False],          # 9
    [True, True, False, False, True, False, False, False, True, False, False],          # 10
    [True, True, False, False, False, True, False, False, True, False, False],          # 11
    [True, False, True, True, False, False, True, True, True, False, False],            # 12
    [True, False, False, True, True, False, True, True, True, False, False],            # 13
    [True, False, False, True, True, False, False, True, True, True, False],            # 14
    [True, False, True, True, True, False, False, True, True, False, False],            # 15
    [True, False, False, True, True, True, False, True, True, False, False],            # 16
    [True, False, False, True, True, True, False, False, True, True, False],            # 17
    [True, True, False, False, True, True, True, False, False, True, False],            # 18
    [True, True, False, False, True, False, True, True, True, False, False],            # 19
    [True, True, False, False, True, False, False, True, True, True, False],            # 20
    [True, True, False, True, True, True, False, False, True, False, False],            # 21
    [True, True, False, False, True, True, True, False, True, False, False],            # 22
    [True, True, True, False, True, True, False, True, True, True, False],              # 23
    [True, True, True, False, True, False, False, True, True, False, False],            # 24
    [True, True, True, False, False, True, False, True, True, False, False],            # 25
    [True, True, True, False, False, True, False, False, True, True, False],            # 26
    [True, True, True, False, True, True, False, False, True, False, False],            # 27
    [True, True, True, False, False, True, True, False, True, False, False],            # 28
    [True, True, True, False, False, True, True, False, False, True, False],            # 29
    [True, True, False, True, True, False, True, True, False, False, False],            # 30
    [True, True, False, True, True, False, False, False, True, True, False],            # 31
    [True, True, False, False, False, True, True, False, True, True, False],            # 32
    [True, False, True, False, False, False, True, True, False, False, False],          # 33
    [True, False, False, False, True, False, True, True, False, False, False],          # 34
    [True, False, False, False, True, False, False, False, True, True, False],          # 35
    [True, False, True, True, False, False, False, True, False, False, False],          # 36
    [True, False, False, False, True, True, False, True, False, False, False],          # 37
    [True, False, False, False, True, True, False, False, False, True, False],          # 38
    [True, True, False, True, False, False, False, True, False, False, False],          # 39
    [True, True, False, False, False, True, False, True, False, False, False],          # 40
    [True, True, False, False, False, True, False, False, False, True, False],          # 41
    [True, False, True, True, False, True, True, True, False, False, False],            # 42
    [True, False, True, True, False, False, False, True, True, True, False],            # 43
    [True, False, False, False, True, True, False, True, True, True, False],            # 44
    [True, False, True, True, True, False, True, True, False, False, False],            # 45
    [True, False, True, True, True, False, False, False, True, True, False],            # 46
    [True, False, False, False, True, True, True, False, True, True, False],            # 47
    [True, True, True, False, True, True, True, False, True, True, False],              # 48
    [True, True, False, True, False, False, False, True, True, True, False],            # 49
    [True, True, False, False, False, True, False, True, True, True, False],            # 50
    [True, True, False, True, True, True, False, True, False, False, False],            # 51
    [True, True, False, True, True, True, False, False, False, True, False],            # 52
    [True, True, False, True, True, True, False, True, True, True, False],              # 53
    [True, True, True, False, True, False, True, True, False, False, False],            # 54
    [True, True, True, False, True, False, False, False, True, True, False],            # 55
    [True, True, True, False, False, False, True, False, True, True, False],            # 56
    [True, True, True, False, True, True, False, True, False, False, False],            # 57
    [True, True, True, False, True, True, False, False, False, True, False],            # 58
    [True, True, True, False, False, False, True, True, False, True, False],            # 59
    [True, True, True, False, True, True, True, True, False, True, False],              # 60
    [True, True, False, False, True, False, False, False, False, True, False],          # 61
    [True, True, True, True, False, False, False, True, False, True, False],            # 62
    [True, False, True, False, False, True, True, False, False, False, False],          # 63
    [True, False, True, False, False, False, False, True, True, False, False],          # 64
    [True, False, False, True, False, True, True, False, False, False, False],          # 65
    [True, False, False, True, False, False, False, False, True, True, False],          # 66
    [True, False, False, False, False, True, False, True, True, False, False],          # 67
    [True, False, False, False, False, True, False, False, True, True, False],          # 68
    [True, False, True, True, False, False, True, False, False, False, False],          # 69
    [True, False, True, True, False, False, False, False, True, False, False],          # 70
    [True, False, False, True, True, False, True, False, False, False, False],          # 71
    [True, False, False, True, True, False, False, False, False, True, False],          # 72
    [True, False, False, False, False, True, True, False, True, False, False],          # 73
    [True, False, False, False, False, True, True, False, False, True, False],          # 74
    [True, True, False, False, False, False, True, False, False, True, False],          # 75
    [True, True, False, False, True, False, True, False, False, False, False],          # 76
    [True, True, True, True, False, True, True, True, False, True, False],              # 77
    [True, True, False, False, False, False, True, False, True, False, False],          # 78
    [True, False, False, False, True, True, True, True, False, True, False],            # 79
    [True, False, True, False, False, True, True, True, True, False, False],            # 80
    [True, False, False, True, False, True, True, True, True, False, False],            # 81
    [True, False, False, True, False, False, True, True, True, True, False],            # 82
    [True, False, True, True, True, True, False, False, True, False, False],            # 83
    [True, False, False, True, True, True, True, False, True, False, False],            # 84
    [True, False, False, True, True, True, True, False, False, True, False],            # 85
    [True, True, True, True, False, True, False, False, True, False, False],            # 86
    [True, True, True, True, False, False, True, False, True, False, False],            # 87
    [True, True, True, True, False, False, True, False, False, True, False],            # 88
    [True, True, False, True, True, False, True, True, True, True, False],              # 89
    [True, True, False, True, True, True, True, False, True, True, False],              # 90
    [True, True, True, True, False, True, True, False, True, True, False],              # 91
    [True, False, True, False, True, True, True, True, False, False, False],            # 92
    [True, False, True, False, False, False, True, True, True, True, False],            # 93
    [True, False, False, False, True, False, True, True, True, True, False],            # 94
    [True, False, True, True, True, True, False, True, False, False, False],            # 95
    [True, False, True, True, True, True, False, False, False, True, False],            # 96
    [True, True, True, True, False, True, False, True, False, False, False],            # 97
    [True, True, True, True, False, True, False, False, False, True, False],            # 98
    [True, False, True, True, True, False, True, True, True, True, False],              # 99
    [True, False, True, True, True, True, False, True, True, True, False],              # 100
    [True, True, True, False, True, False, True, True, True, True, False],              # 101
    [True, True, True, True, False, True, False, True, True, True, False],              # 102
    [True, True, False, True, False, False, False, False, True, False, False],          # 103 StartA
    [True, True, False, True, False, False, True, False, False, False, False],          # 104 StartB
    [True, True, False, True, False, False, True, True, True, False, False],            # 105 StartC
    [True, True, False, False, False, True, True, True, False, True, False, True, True],  # 106 Stop (13 modules)
]

# Code 128 character set tables
_B_TABLE = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~\x7f'
_A_TABLE = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_' + ''.join(chr(i) for i in range(32))
_AB_TABLE = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_'
_A_ONLY_TABLE = ''.join(chr(i) for i in range(32))

_START_A = 103
_START_B = 104
_START_C = 105
_CODE_A = 101
_CODE_B = 100
_CODE_C = 99
_STOP = 106


def _should_use_c_table(next_chars: str, current_encoding: int) -> bool:
    """
    Check if Code C (digit pairs) should be used for next characters.
    
        Prefer Code C whenever the next two characters are digits.
    """
    if len(next_chars) < 2:
        return False
    if next_chars[0] < '0' or next_chars[0] > '9':
        return False
    return not (next_chars[1] < '0' or next_chars[1] > '9')


def _should_use_a_table(next_chars: str, current_encoding: int) -> bool:
    """
    Check if Code A should be used.
    
        Prefer Code A as the default code set for shared A/B characters. Code B is
        used only when the content requires it.
    """
    ch = next_chars[0]
    # Character requires Code A (not in the B table).
    if ch not in _B_TABLE and ch in _A_TABLE:
        return True
    # Already in Code A: stay if character is valid
    if current_encoding == _START_A and ch in _A_TABLE:
        return True
    # Initial code set selection: prefer A for shared A/B characters.
    if current_encoding == 0 and ch in _A_TABLE:
        return True
    # After Code C (digit pairs), prefer A for shared A/B characters
    if current_encoding == _START_C and ch in _A_TABLE:
        return True
    # After Code B for lowercase, switch back to A for shared characters
    if current_encoding == _START_B and ch in _AB_TABLE:
        # Only switch back to A if no more B-only characters follow
        has_b_only = False
        for c in next_chars:
            if c in _AB_TABLE:
                continue
            if c not in _A_TABLE:
                # B-only character (lowercase): stay in B.
                has_b_only = True
                break
            break
        if not has_b_only:
            return True
    return False


def _get_code_index_list(content: str) -> list[int] | None:
    """
    Build the Code 128 symbol index list with auto code-set switching.
    
        Returns list of code indices (start symbol, data codes, but NOT checksum/stop),
        or None if encoding fails.
    """
    indices: list[int] = []
    current = 0  # 0=unset, _START_A/_START_B/_START_C

    i = 0
    while i < len(content):
        remaining = content[i:]

        if _should_use_c_table(remaining, current):
            if current != _START_C:
                indices.append(_START_C if current == 0 else _CODE_C)
                current = _START_C
            # Encode digit pair
            val = (ord(content[i]) - 48) * 10 + (ord(content[i + 1]) - 48)
            indices.append(val)
            i += 2
            continue

        if _should_use_a_table(remaining, current):
            if current != _START_A:
                indices.append(_START_A if current == 0 else _CODE_A)
                current = _START_A
            idx = _A_TABLE.find(content[i])
            if idx < 0:
                return None
            indices.append(idx)
            i += 1
            continue

        # Default: Code B
        if current != _START_B:
            indices.append(_START_B if current == 0 else _CODE_B)
            current = _START_B
        idx = _B_TABLE.find(content[i])
        if idx < 0:
            return None
        indices.append(idx)
        i += 1

    return indices


def encode_code128(text: str) -> BarcodeEncoding:
    """
    Encode text as Code 128 barcode.
    
        Auto-selects Code A, B, or C subsets. Supports full ASCII printable range.
    
        Args:
            text: Text to encode (1-80 characters)
    
        Returns:
            BarcodeEncoding with bit pattern
    
        Raises:
            ValueError: If text is empty, too long, or contains unencodable characters
    """
    if not text or len(text) > 80:
        raise ValueError(f"Code 128 content length must be 1-80, got {len(text)}")

    indices = _get_code_index_list(text)
    if indices is None:
        raise ValueError(f"Code 128: cannot encode '{text}'")

    # Build bit pattern and compute checksum
    bits: list[bool] = []
    checksum = 0
    for pos, idx in enumerate(indices):
        if pos == 0:
            checksum = idx
        else:
            checksum += pos * idx
        bits.extend(_CODE128_PATTERNS[idx])

    # Append checksum pattern
    checksum %= 103
    bits.extend(_CODE128_PATTERNS[checksum])

    # Append stop pattern (13 modules)
    bits.extend(_CODE128_PATTERNS[_STOP])

    return BarcodeEncoding(bits=bits, symbology="Code 128", content=text)
