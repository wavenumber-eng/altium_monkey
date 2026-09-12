"""Managed-compatible collation helpers used by compiled design."""

from __future__ import annotations

import atexit
import ctypes
import re
import sys
from collections.abc import Callable
from ctypes.util import find_library
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

_IcuOpen = Callable[[bytes, object], int | None]
_IcuCompare = Callable[[object, bytes, int, bytes, int, object], int]
_IcuClose = Callable[[object], None]
_WINDOWS_ICU_LIBRARY = ".".join(("icu", "dll"))


@dataclass(frozen=True, slots=True)
class _IcuCollator:
    library: ctypes.CDLL
    handle: int
    compare: _IcuCompare


def _icu_library_candidates() -> tuple[str, ...]:
    if sys.platform == "win32":
        return (_WINDOWS_ICU_LIBRARY,)
    discovered = find_library("icui18n")
    if discovered is not None:
        return (discovered,)
    if sys.platform == "darwin":
        apple_icu = find_library("icucore")
        return (apple_icu or "libicucore.dylib",)
    return ("libicui18n.so",)


def _icu_symbol(library: ctypes.CDLL, base_name: str, library_name: str) -> object:
    suffix_match = re.search(r"(?:\.so\.|\.)(\d+)(?:\D|$)", Path(library_name).name)
    candidates = [base_name]
    if suffix_match is not None:
        candidates.append(f"{base_name}_{suffix_match.group(1)}")
    for name in candidates:
        try:
            return cast(object, getattr(library, name))
        except AttributeError:
            continue
    raise RuntimeError(f"ICU library does not export {base_name}")


def _load_icu_library() -> tuple[ctypes.CDLL, str]:
    errors: list[str] = []
    loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
    for candidate in _icu_library_candidates():
        try:
            return loader(candidate), candidate
        except OSError as error:
            errors.append(str(error))
    detail = "; ".join(errors) or "no ICU library candidate was found"
    raise RuntimeError(f"managed hierarchy collation requires ICU: {detail}")


@lru_cache(maxsize=1)
def _en_us_collator() -> _IcuCollator:
    library, library_name = _load_icu_library()
    open_symbol = _icu_symbol(library, "ucol_open", library_name)
    setattr(
        open_symbol,
        "argtypes",
        [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int32)],
    )
    setattr(open_symbol, "restype", ctypes.c_void_p)
    open_collator = cast(_IcuOpen, open_symbol)

    compare_symbol = _icu_symbol(library, "ucol_strcollUTF8", library_name)
    setattr(
        compare_symbol,
        "argtypes",
        [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int32,
            ctypes.c_char_p,
            ctypes.c_int32,
            ctypes.POINTER(ctypes.c_int32),
        ],
    )
    setattr(compare_symbol, "restype", ctypes.c_int32)
    compare = cast(_IcuCompare, compare_symbol)

    close_symbol = _icu_symbol(library, "ucol_close", library_name)
    setattr(close_symbol, "argtypes", [ctypes.c_void_p])
    setattr(close_symbol, "restype", None)
    close = cast(_IcuClose, close_symbol)

    status = ctypes.c_int32(0)
    handle = open_collator(b"en-US", ctypes.byref(status))
    if handle is None or status.value > 0:
        raise RuntimeError(f"ICU could not create en-US collator: {status.value}")
    atexit.register(close, ctypes.c_void_p(handle))
    # Keep the owning library alive for as long as its collator and function
    # pointers can be reached.
    return _IcuCollator(library=library, handle=handle, compare=compare)


def managed_en_us_compare(left: str, right: str) -> int:
    """Compare text with the pinned AD26 en-US current-culture contract."""
    left_bytes = left.encode("utf-8")
    right_bytes = right.encode("utf-8")
    if len(left_bytes) > (1 << 31) - 1 or len(right_bytes) > (1 << 31) - 1:
        raise ValueError("managed collation input exceeds the ICU Int32 boundary")
    collator = _en_us_collator()
    status = ctypes.c_int32(0)
    result = collator.compare(
        ctypes.c_void_p(collator.handle),
        left_bytes,
        len(left_bytes),
        right_bytes,
        len(right_bytes),
        ctypes.byref(status),
    )
    if status.value > 0:
        raise RuntimeError(f"ICU hierarchy comparison failed: {status.value}")
    return (result > 0) - (result < 0)
