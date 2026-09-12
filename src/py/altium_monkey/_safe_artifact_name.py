"""Private filename rules shared by schematic extraction adapters."""

from __future__ import annotations

import hashlib
import re
import string
from pathlib import PurePosixPath, PureWindowsPath

_INVALID_FILENAME_CHARACTERS = frozenset('<>:"/\\|?*')
_RESERVED_DEVICE_STEM = re.compile(
    r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}]|LPT[1-9\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}])$"
)
_DIGEST_SUFFIX = re.compile(r"^(.*)(_[0-9a-f]{16})$")
_FORMATTER = string.Formatter()
_MAX_NAME_UTF8_BYTES = 200
_MAX_NAME_UTF16_UNITS = 200


def _name_size_is_bounded(value: str) -> bool:
    return len(value.encode("utf-8")) <= _MAX_NAME_UTF8_BYTES and (
        len(value.encode("utf-16-le")) // 2 <= _MAX_NAME_UTF16_UNITS
    )


def _split_extension(value: str) -> tuple[str, str]:
    position = value.rfind(".")
    if position <= 0:
        return value, ""
    return value[:position], value[position:]


def _is_reserved_device_name(value: str) -> bool:
    device_stem = value.partition(".")[0].rstrip(" .")
    return _RESERVED_DEVICE_STEM.fullmatch(device_stem.upper()) is not None


def _disarm_reserved_device_name(value: str) -> str:
    if not _is_reserved_device_name(value):
        return value
    device_stem, separator, remainder = value.partition(".")
    suffix = f"{separator}{remainder}" if separator else ""
    return f"{device_stem.rstrip(' .')}_{suffix}"


def _truncate_name(value: str) -> str:
    if _name_size_is_bounded(value):
        return value
    stem, extension = _split_extension(value)
    marker = "_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    if not _name_size_is_bounded(marker + extension):
        stem = value
        extension = ""
    suffix = marker + extension
    return _fit_stem_with_suffix(stem, suffix)


def _fit_stem_with_suffix(stem: str, suffix: str) -> str:
    suffix_utf8_bytes = len(suffix.encode("utf-8"))
    suffix_utf16_units = len(suffix.encode("utf-16-le")) // 2
    if (
        suffix_utf8_bytes > _MAX_NAME_UTF8_BYTES
        or suffix_utf16_units > _MAX_NAME_UTF16_UNITS
    ):
        raise ValueError("artifact filename extension exceeds the portable limit")
    available_utf8_bytes = _MAX_NAME_UTF8_BYTES - suffix_utf8_bytes
    available_utf16_units = _MAX_NAME_UTF16_UNITS - suffix_utf16_units
    prefix_characters: list[str] = []
    used_utf8_bytes = 0
    used_utf16_units = 0
    for character in stem:
        character_utf8_bytes = len(character.encode("utf-8"))
        character_utf16_units = len(character.encode("utf-16-le")) // 2
        if (
            used_utf8_bytes + character_utf8_bytes > available_utf8_bytes
            or used_utf16_units + character_utf16_units > available_utf16_units
        ):
            break
        prefix_characters.append(character)
        used_utf8_bytes += character_utf8_bytes
        used_utf16_units += character_utf16_units
    return "".join(prefix_characters) + suffix


def safe_artifact_basename(value: str, *, fallback: str) -> str:
    """Return one deterministic cross-platform-safe artifact basename."""
    cleaned = "".join(
        "_"
        if character in _INVALID_FILENAME_CHARACTERS or ord(character) < 32
        else character
        for character in value
    ).rstrip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = fallback
    cleaned = _disarm_reserved_device_name(cleaned)
    return _truncate_name(cleaned)


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 for character in value)


def _is_single_relative_basename(value: str) -> bool:
    if value in {"", ".", ".."} or "/" in value or "\\" in value:
        return False
    windows_path = PureWindowsPath(value)
    return not (
        PurePosixPath(value).is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
    )


def _validate_schlib_name_pattern(name_pattern: str) -> None:
    field_count = 0
    for _literal, field_name, format_spec, conversion in _FORMATTER.parse(name_pattern):
        if field_name is None:
            continue
        field_count += 1
        if (
            field_name != "symbol_name"
            or format_spec
            or conversion is not None
            or field_count > 1
        ):
            raise ValueError(
                "SchLib split name_pattern permits at most one plain "
                "{symbol_name} placeholder"
            )


def safe_schlib_output_name(name_pattern: str, symbol_name: str) -> str:
    """Format and validate one contained SchLib split output basename."""
    if _contains_control_character(name_pattern) or _contains_control_character(
        symbol_name
    ):
        raise ValueError("SchLib split names must not contain control characters")
    _validate_schlib_name_pattern(name_pattern)
    formatted = name_pattern.format(symbol_name=symbol_name)
    if not _is_single_relative_basename(formatted):
        raise ValueError("SchLib split name_pattern must produce one relative basename")
    return safe_artifact_basename(formatted, fallback="symbol.SchLib")


def _collision_stem_and_suffix(
    value: str,
    index: int,
) -> tuple[str, str]:
    stem, extension = _split_extension(value)
    digest_match = _DIGEST_SUFFIX.fullmatch(stem)
    if digest_match is None:
        return stem, f"_{index}{extension}"
    return digest_match.group(1), f"{digest_match.group(2)}_{index}{extension}"


def dedupe_artifact_basename(value: str, used: dict[str, int]) -> str:
    """Reserve one case-folded basename, adding a bounded numeric suffix."""
    base_key = value.casefold()
    index = used.get(base_key)
    if index is None:
        used[base_key] = 2
        return value
    while True:
        stem, suffix = _collision_stem_and_suffix(value, index)
        candidate = _fit_stem_with_suffix(stem, suffix)
        candidate_key = candidate.casefold()
        if candidate_key not in used:
            used[base_key] = index + 1
            used[candidate_key] = 2
            return candidate
        index += 1


__all__: list[str] = []
