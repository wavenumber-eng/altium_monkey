"""
Shared font resolution policy for Altium text rendering.

This module keeps font-family semantics separate from text-metrics math. The
parsed model can continue to use logical Altium family names while this layer
decides which on-disk font file should satisfy a request on the current host.
"""

from __future__ import annotations

import json
import os
import platform
import struct
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Mapping


class FontResolutionMode(str, Enum):
    """
    Runtime policy for missing font families.
    """

    STRICT = "strict"
    COMPATIBLE = "compatible"
    BEST_EFFORT = "best_effort"


class FontResolutionStatus(str, Enum):
    """
    Resolution result classification.
    """

    EXACT = "exact"
    STYLE_FALLBACK = "style_fallback"
    SUBSTITUTED = "substituted"
    GENERIC_FALLBACK = "generic_fallback"
    MISSING = "missing"


class FontResolutionSource(str, Enum):
    """
    Where the resolver found the font file.
    """

    EXPLICIT_PATH = "explicit_path"
    ALIAS = "alias"
    TEST_ASSET = "test_asset"
    CONFIGURED_DIR = "configured_dir"
    SYSTEM_FONT = "system_font"
    BUNDLED_FONT = "bundled_font"
    SEARCH_DIR = "search_dir"
    MISSING = "missing"


@dataclass(frozen=True)
class FontRequest:
    """
    Logical font request from the model/rendering layer.
    """

    family: str
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class FontReplacementRule:
    """
    Explicit logical family replacement for portable rendering.
    """

    requested_name: str
    effective_name: str

    def to_dict(self) -> dict[str, str]:
        """
        Return the JSON-compatible replacement-rule shape used by WASM callers.
        """
        return {
            "requested_name": self.requested_name,
            "effective_name": self.effective_name,
        }


@dataclass(frozen=True)
class FontResolverConfig:
    """
    Optional overrides for the shared font resolver.
    """

    mode: FontResolutionMode | None = None
    search_dirs: tuple[Path, ...] | None = None
    configured_dirs: tuple[Path, ...] = ()
    system_dirs: tuple[Path, ...] = ()
    bundled_dirs: tuple[Path, ...] = ()
    alias_to_path: Mapping[str, Path] = field(default_factory=dict)
    substitutions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    generic_sans_fallbacks: tuple[str, ...] | None = None
    generic_serif_fallbacks: tuple[str, ...] | None = None
    generic_mono_fallbacks: tuple[str, ...] | None = None


@dataclass(frozen=True)
class FontResolution:
    """
    Detailed result for a font lookup.
    """

    request: FontRequest
    resolved_family: str | None
    resolved_name: str | None
    path: Path | None
    status: FontResolutionStatus
    source: FontResolutionSource
    tried_families: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """
        Return JSON-compatible diagnostic metadata.
        """
        return {
            "requested_family": self.request.family,
            "requested_bold": self.request.bold,
            "requested_italic": self.request.italic,
            "resolved_family": self.resolved_family,
            "resolved_name": self.resolved_name,
            "status": self.status.value,
            "source": self.source.value,
            "path": None if self.path is None else str(self.path),
            "tried_families": list(self.tried_families),
        }


@dataclass(frozen=True)
class FontResolutionCacheStats:
    """
    Counters for the process-local default-path resolution result cache.
    """

    hits: int
    misses: int
    size: int


def _resolve_test_fonts_dir() -> Path:
    package_fonts_dir = (
        Path(__file__).resolve().parents[3] / "tests" / "common" / "assets" / "fonts"
    )
    if package_fonts_dir.exists():
        return package_fonts_dir

    suites_root = os.environ.get("WN_TEST_SUITES_ROOT")
    if suites_root:
        external_fonts_dir = (
            Path(suites_root).expanduser()
            / "suites"
            / "altium_monkey"
            / "tests"
            / "common"
            / "assets"
            / "fonts"
        )
        if external_fonts_dir.exists():
            return external_fonts_dir

    return package_fonts_dir


TEST_FONTS_DIR = _resolve_test_fonts_dir()
PACKAGE_FONT_ROOT = Path(__file__).resolve().parent / "data" / "fonts"
_PACKAGE_BUNDLED_FONT_RELATIVE_PATHS: tuple[str, ...] = (
    "arimo/Arimo-Regular.ttf",
    "arimo/Arimo-Bold.ttf",
    "arimo/Arimo-Italic.ttf",
    "arimo/Arimo-BoldItalic.ttf",
    "tinos/Tinos-Regular.ttf",
    "tinos/Tinos-Bold.ttf",
    "tinos/Tinos-Italic.ttf",
    "tinos/Tinos-BoldItalic.ttf",
    "cousine/Cousine-Regular.ttf",
    "cousine/Cousine-Bold.ttf",
    "cousine/Cousine-Italic.ttf",
    "cousine/Cousine-BoldItalic.ttf",
)


def get_bundled_font_search_dirs() -> tuple[Path, ...]:
    """
    Return package-data font directories used for portable fallback rendering.
    """
    croscore_root = PACKAGE_FONT_ROOT / "croscore"
    return _dedupe_paths(
        [
            croscore_root / "arimo",
            croscore_root / "tinos",
            croscore_root / "cousine",
        ]
    )


@lru_cache(maxsize=1)
def get_package_bundled_font_inventory() -> tuple[Path, ...]:
    """Return canonical package-owned font files eligible for SVG attachment."""
    try:
        root = (PACKAGE_FONT_ROOT / "croscore").resolve(strict=True)
    except (OSError, RuntimeError):
        return ()
    inventory: list[Path] = []
    for relative_path in _PACKAGE_BUNDLED_FONT_RELATIVE_PATHS:
        try:
            candidate = (root / relative_path).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if candidate.is_file() and candidate.is_relative_to(root):
            inventory.append(candidate)
    return tuple(inventory)


def canonical_package_bundled_font_path(value: Path | str) -> Path | None:
    """Return the canonical eligible package font matching ``value``, if any."""
    try:
        candidate = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return next(
        (path for path in get_package_bundled_font_inventory() if path == candidate),
        None,
    )


KNOWN_FONT_FILE_MAP: dict[str, str] = {
    "Arial": "arial.ttf",
    "Arial Bold": "arialbd.ttf",
    "Arial Italic": "ariali.ttf",
    "Arial Bold Italic": "arialbi.ttf",
    "Arial Narrow": "arialn.ttf",
    "Arial Narrow Bold": "arialnb.ttf",
    "Arial Narrow Italic": "arialni.ttf",
    "Arial Narrow Bold Italic": "arialnbi.ttf",
    "Arial Black": "ariblk.ttf",
    "Archivo Black": "archivoblack-regular.ttf",
    "Arial Rounded MT Bold": "ARLRDBD.TTF",
    "Times New Roman": "times.ttf",
    "Times New Roman Bold": "timesbd.ttf",
    "Times New Roman Italic": "timesi.ttf",
    "Times New Roman Bold Italic": "timesbi.ttf",
    "Courier New": "cour.ttf",
    "Courier New Bold": "courbd.ttf",
    "Courier New Italic": "couri.ttf",
    "Courier New Bold Italic": "courbi.ttf",
    "Verdana": "verdana.ttf",
    "Verdana Bold": "verdanab.ttf",
    "Verdana Italic": "verdanai.ttf",
    "Verdana Bold Italic": "verdanaz.ttf",
    "Tahoma": "tahoma.ttf",
    "Tahoma Bold": "tahomabd.ttf",
    "Georgia": "georgia.ttf",
    "Georgia Bold": "georgiab.ttf",
    "Georgia Italic": "georgiai.ttf",
    "Georgia Bold Italic": "georgiaz.ttf",
    "Trebuchet MS": "trebuc.ttf",
    "Trebuchet MS Bold": "trebucbd.ttf",
    "Trebuchet MS Italic": "trebucit.ttf",
    "Trebuchet MS Bold Italic": "trebucbi.ttf",
    "Segoe UI": "segoeui.ttf",
    "Segoe UI Bold": "segoeuib.ttf",
    "Segoe UI Italic": "segoeuii.ttf",
    "Segoe UI Bold Italic": "segoeuiz.ttf",
    "Calibri": "calibri.ttf",
    "Calibri Bold": "calibrib.ttf",
    "Calibri Italic": "calibrii.ttf",
    "Calibri Bold Italic": "calibriz.ttf",
    "Calibri Light": "calibril.ttf",
    "Calibri Light Italic": "calibrili.ttf",
    "Century Gothic": "GOTHIC.TTF",
    "Century Gothic Bold": "GOTHICB.TTF",
    "Century Gothic Italic": "GOTHICI.TTF",
    "Century Gothic Bold Italic": "GOTHICBI.TTF",
    "Bahnschrift": "bahnschrift.ttf",
    "Bahnschrift SemiBold": "bahnschrift.ttf",
    "Bahnschrift Light": "bahnschrift.ttf",
    "Bahnschrift Light Condensed": "bahnschrift.ttf",
    "Consolas": "consola.ttf",
    "Consolas Bold": "consolab.ttf",
    "Consolas Italic": "consolai.ttf",
    "Consolas Bold Italic": "consolaz.ttf",
    "Impact": "impact.ttf",
    "Comic Sans MS": "comic.ttf",
    "Comic Sans MS Bold": "comicbd.ttf",
    "Lucida Sans Unicode": "l_10646.ttf",
    "Microsoft Sans Serif": "micross.ttf",
    "Symbol": "symbol.ttf",
    "Arimo": "Arimo-Regular.ttf",
    "Arimo Bold": "Arimo-Bold.ttf",
    "Arimo Italic": "Arimo-Italic.ttf",
    "Arimo Bold Italic": "Arimo-BoldItalic.ttf",
    "Tinos": "Tinos-Regular.ttf",
    "Tinos Bold": "Tinos-Bold.ttf",
    "Tinos Italic": "Tinos-Italic.ttf",
    "Tinos Bold Italic": "Tinos-BoldItalic.ttf",
    "Cousine": "Cousine-Regular.ttf",
    "Cousine Bold": "Cousine-Bold.ttf",
    "Cousine Italic": "Cousine-Italic.ttf",
    "Cousine Bold Italic": "Cousine-BoldItalic.ttf",
    "Carlito": "Carlito-Regular.ttf",
    "Carlito Bold": "Carlito-Bold.ttf",
    "Carlito Italic": "Carlito-Italic.ttf",
    "Carlito Bold Italic": "Carlito-BoldItalic.ttf",
    "Liberation Sans": "LiberationSans-Regular.ttf",
    "Liberation Sans Bold": "LiberationSans-Bold.ttf",
    "Liberation Sans Italic": "LiberationSans-Italic.ttf",
    "Liberation Sans Bold Italic": "LiberationSans-BoldItalic.ttf",
    "Liberation Serif": "LiberationSerif-Regular.ttf",
    "Liberation Serif Bold": "LiberationSerif-Bold.ttf",
    "Liberation Serif Italic": "LiberationSerif-Italic.ttf",
    "Liberation Serif Bold Italic": "LiberationSerif-BoldItalic.ttf",
    "Liberation Mono": "LiberationMono-Regular.ttf",
    "Liberation Mono Bold": "LiberationMono-Bold.ttf",
    "Liberation Mono Italic": "LiberationMono-Italic.ttf",
    "Liberation Mono Bold Italic": "LiberationMono-BoldItalic.ttf",
    "DejaVu Sans": "DejaVuSans.ttf",
    "DejaVu Sans Bold": "DejaVuSans-Bold.ttf",
    "DejaVu Sans Italic": "DejaVuSans-Oblique.ttf",
    "DejaVu Sans Bold Italic": "DejaVuSans-BoldOblique.ttf",
    "DejaVu Serif": "DejaVuSerif.ttf",
    "DejaVu Serif Bold": "DejaVuSerif-Bold.ttf",
    "DejaVu Serif Italic": "DejaVuSerif-Italic.ttf",
    "DejaVu Serif Bold Italic": "DejaVuSerif-BoldItalic.ttf",
    "DejaVu Sans Mono": "DejaVuSansMono.ttf",
    "DejaVu Sans Mono Bold": "DejaVuSansMono-Bold.ttf",
    "DejaVu Sans Mono Italic": "DejaVuSansMono-Oblique.ttf",
    "DejaVu Sans Mono Bold Italic": "DejaVuSansMono-BoldOblique.ttf",
}

TEST_FONT_FILE_MAP: dict[str, Path] = {
    "Berkeley Mono": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Regular.ttf",
    "BerkeleyMono": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Regular.ttf",
    "Berkeley Mono Regular": TEST_FONTS_DIR
    / "BerkeleyMono"
    / "BerkeleyMono-Regular.ttf",
    "BerkeleyMono Regular": TEST_FONTS_DIR
    / "BerkeleyMono"
    / "BerkeleyMono-Regular.ttf",
    "Berkeley Mono Bold": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Bold.ttf",
    "BerkeleyMono Bold": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Bold.ttf",
    "Mooretronics": TEST_FONTS_DIR / "ElectronicSymbols.ttf",
    "ElectronicSymbols": TEST_FONTS_DIR / "ElectronicSymbols.ttf",
    "Old Stamper": TEST_FONTS_DIR / "old_stamper.ttf",
}

_PORTABLE_FONT_REPLACEMENT_RULES: tuple[FontReplacementRule, ...] = (
    FontReplacementRule("Arial", "Arimo"),
    FontReplacementRule("Arial Black", "Archivo Black"),
    FontReplacementRule("Helvetica", "Arimo"),
    FontReplacementRule("Microsoft Sans Serif", "Arimo"),
    FontReplacementRule("Times", "Tinos"),
    FontReplacementRule("Times New Roman", "Tinos"),
    FontReplacementRule("Courier", "Cousine"),
    FontReplacementRule("Courier New", "Cousine"),
)

ALTIUM_PORTABLE_FONT_REPLACEMENTS: tuple[dict[str, str], ...] = tuple(
    rule.to_dict() for rule in _PORTABLE_FONT_REPLACEMENT_RULES
)

DEFAULT_FAMILY_SUBSTITUTIONS: dict[str, tuple[str, ...]] = {
    "Arial": ("Arimo", "Liberation Sans", "DejaVu Sans"),
    "Arial Black": ("Archivo Black", "Arimo", "Liberation Sans", "DejaVu Sans"),
    "Arial Bold": ("Arimo", "Liberation Sans", "DejaVu Sans"),
    "Arial Italic": ("Arimo", "Liberation Sans", "DejaVu Sans"),
    "Arial Bold Italic": ("Arimo", "Liberation Sans", "DejaVu Sans"),
    "Times New Roman": ("Tinos", "Liberation Serif", "DejaVu Serif"),
    "Courier New": ("Cousine", "Liberation Mono", "DejaVu Sans Mono"),
    "Microsoft Sans Serif": ("Arimo", "Liberation Sans", "DejaVu Sans"),
    "Calibri": ("Carlito", "Arimo", "Liberation Sans"),
    "Calibri Light": ("Carlito", "Arimo", "Liberation Sans"),
}

DEFAULT_SANS_FALLBACKS: tuple[str, ...] = (
    "Arimo",
    "Liberation Sans",
    "DejaVu Sans",
    "Arial",
    "Microsoft Sans Serif",
)
DEFAULT_SERIF_FALLBACKS: tuple[str, ...] = (
    "Tinos",
    "Liberation Serif",
    "DejaVu Serif",
    "Times New Roman",
    "Arimo",
)
DEFAULT_MONO_FALLBACKS: tuple[str, ...] = (
    "Cousine",
    "Liberation Mono",
    "DejaVu Sans Mono",
    "Courier New",
    "Arimo",
)

_KNOWN_FONT_FILE_MAP_NORMALIZED = {
    " ".join(name.strip().strip("\"'").split()).lower(): filename
    for name, filename in KNOWN_FONT_FILE_MAP.items()
}
_TEST_FONT_FILE_MAP_NORMALIZED = {
    " ".join(name.strip().strip("\"'").split()).lower(): path
    for name, path in TEST_FONT_FILE_MAP.items()
}
_FONT_RESOLVER_OVERRIDE: FontResolverConfig | None = None
_FONT_RESOLUTION_DIAGNOSTICS: list[FontResolution] = []
_FONT_RESOLUTION_DIAGNOSTIC_KEYS: set[tuple[object, ...]] = set()
_FONT_RESOLUTION_RESULT_CACHE: dict[tuple[object, ...], FontResolution] = {}
_FONT_RESOLUTION_RESULT_CACHE_HITS: int = 0
_FONT_RESOLUTION_RESULT_CACHE_MISSES: int = 0

# The default resolver configuration is rebuilt from os.environ on every
# lookup, so cached default-path results are only valid while these
# environment inputs are unchanged. They participate in the cache key.
_RESOLVER_ENV_FINGERPRINT_VARS: tuple[str, ...] = (
    "ALTIUM_FONT_MODE",
    "ALTIUM_FONT_DIRS",
    "ALTIUM_FONT_DISABLE_SYSTEM",
    "LOCALAPPDATA",
    "HOME",
    "USERPROFILE",
)


def _default_resolver_env_fingerprint() -> tuple[str | None, ...]:
    return tuple(os.environ.get(name) for name in _RESOLVER_ENV_FINGERPRINT_VARS)


def clear_font_resolution_result_cache() -> None:
    """
    Clear the process-local default-path font resolution result cache.
    """
    global _FONT_RESOLUTION_RESULT_CACHE_HITS
    global _FONT_RESOLUTION_RESULT_CACHE_MISSES
    _FONT_RESOLUTION_RESULT_CACHE.clear()
    _FONT_RESOLUTION_RESULT_CACHE_HITS = 0
    _FONT_RESOLUTION_RESULT_CACHE_MISSES = 0


def font_resolution_result_cache_stats() -> FontResolutionCacheStats:
    """
    Return hit/miss/size counters for the default-path result cache.
    """
    return FontResolutionCacheStats(
        hits=_FONT_RESOLUTION_RESULT_CACHE_HITS,
        misses=_FONT_RESOLUTION_RESULT_CACHE_MISSES,
        size=len(_FONT_RESOLUTION_RESULT_CACHE),
    )


def _normalize_family_name(name: str) -> str:
    return " ".join(name.strip().strip("\"'").split())


def _normalize_family_key(name: str) -> str:
    return _normalize_family_name(name).lower()


def _diagnostic_key(resolution: FontResolution) -> tuple[object, ...]:
    return (
        resolution.request.family,
        resolution.request.bold,
        resolution.request.italic,
        resolution.resolved_family,
        resolution.resolved_name,
        resolution.status.value,
        resolution.source.value,
        None if resolution.path is None else str(resolution.path),
    )


def _record_font_resolution_diagnostic(resolution: FontResolution) -> FontResolution:
    key = _diagnostic_key(resolution)
    if key not in _FONT_RESOLUTION_DIAGNOSTIC_KEYS:
        _FONT_RESOLUTION_DIAGNOSTIC_KEYS.add(key)
        _FONT_RESOLUTION_DIAGNOSTICS.append(resolution)
    return resolution


def clear_font_resolution_diagnostics() -> None:
    """
    Clear process-local font resolution diagnostics.
    """
    _FONT_RESOLUTION_DIAGNOSTICS.clear()
    _FONT_RESOLUTION_DIAGNOSTIC_KEYS.clear()


def get_font_resolution_diagnostics() -> tuple[FontResolution, ...]:
    """
    Return process-local font resolution diagnostics.
    """
    return tuple(_FONT_RESOLUTION_DIAGNOSTICS)


def font_resolution_diagnostics_json() -> str:
    """
    Return process-local font resolution diagnostics as stable JSON.
    """
    return json.dumps(
        [diagnostic.to_dict() for diagnostic in _FONT_RESOLUTION_DIAGNOSTICS],
        indent=2,
        sort_keys=True,
    )


def _make_font_resolution(
    *,
    request: FontRequest,
    resolved_family: str | None,
    resolved_name: str | None,
    path: Path | None,
    status: FontResolutionStatus,
    source: FontResolutionSource,
    tried_families: tuple[str, ...],
) -> FontResolution:
    return _record_font_resolution_diagnostic(
        FontResolution(
            request=request,
            resolved_family=resolved_family,
            resolved_name=resolved_name,
            path=path,
            status=status,
            source=source,
            tried_families=tried_families,
        )
    )


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []

    for path in paths:
        expanded = path.expanduser()
        key = str(expanded)
        if key in seen:
            continue
        seen.add(key)
        result.append(expanded)

    return tuple(result)


def _path_is_under(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved_path = path.expanduser().resolve(strict=False)
    for root in roots:
        try:
            resolved_path.relative_to(root.expanduser().resolve(strict=False))
        except ValueError:
            continue
        return True
    return False


def _source_for_resolved_path(
    path: Path,
    config: FontResolverConfig,
) -> FontResolutionSource:
    if _path_is_under(path, config.bundled_dirs):
        return FontResolutionSource.BUNDLED_FONT
    if _path_is_under(path, config.configured_dirs):
        return FontResolutionSource.CONFIGURED_DIR
    if _path_is_under(path, config.system_dirs):
        return FontResolutionSource.SYSTEM_FONT
    return FontResolutionSource.SEARCH_DIR


def _resolve_child_case_insensitive(search_dir: Path, file_name: str) -> Path | None:
    candidate = search_dir / file_name
    if candidate.exists():
        return candidate
    try:
        for child in search_dir.iterdir():
            if child.name.lower() == file_name.lower() and child.exists():
                return child
    except OSError:
        return None
    return None


def _known_file_candidates(name: str) -> tuple[str, ...]:
    normalized_name = _normalize_family_name(name)
    candidates: list[str] = []
    mapped_name = _KNOWN_FONT_FILE_MAP_NORMALIZED.get(_normalize_family_key(name))
    if mapped_name is not None:
        candidates.append(mapped_name)
    for suffix in (".ttf", ".otf", ".TTF", ".OTF"):
        candidates.append(f"{normalized_name}{suffix}")

    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return tuple(result)


def _decode_font_name_record(
    raw: bytes,
    *,
    platform_id: int,
    encoding_id: int,
) -> str | None:
    if not raw:
        return None
    encodings: tuple[str, ...]
    if platform_id in (0, 3):
        encodings = ("utf-16-be", "utf-8")
    elif platform_id == 1:
        encodings = ("mac_roman", "utf-8")
    else:
        encodings = ("utf-8", "utf-16-be")

    for encoding in encodings:
        try:
            decoded = raw.decode(encoding).replace("\x00", "").strip()
        except UnicodeDecodeError:
            continue
        if decoded:
            return decoded
    return None


def _font_name_table_names(path: Path) -> tuple[str, ...]:
    try:
        data = path.read_bytes()
    except OSError:
        return ()
    if len(data) < 12 or data[:4] == b"ttcf":
        return ()

    num_tables = struct.unpack(">H", data[4:6])[0]
    name_table_offset: int | None = None
    name_table_length: int | None = None
    directory_offset = 12
    for _ in range(num_tables):
        if directory_offset + 16 > len(data):
            return ()
        tag = data[directory_offset : directory_offset + 4]
        if tag == b"name":
            name_table_offset = struct.unpack(
                ">I", data[directory_offset + 8 : directory_offset + 12]
            )[0]
            name_table_length = struct.unpack(
                ">I", data[directory_offset + 12 : directory_offset + 16]
            )[0]
            break
        directory_offset += 16
    if name_table_offset is None or name_table_length is None:
        return ()
    if name_table_offset + name_table_length > len(data):
        return ()

    table = data[name_table_offset : name_table_offset + name_table_length]
    if len(table) < 6:
        return ()
    record_count = struct.unpack(">H", table[2:4])[0]
    string_offset = struct.unpack(">H", table[4:6])[0]
    names: list[str] = []
    for index in range(record_count):
        record_offset = 6 + index * 12
        if record_offset + 12 > len(table):
            break
        platform_id, encoding_id, _language_id, name_id, length, offset = struct.unpack(
            ">HHHHHH", table[record_offset : record_offset + 12]
        )
        if name_id not in {1, 2, 4, 16, 17}:
            continue
        start = string_offset + offset
        end = start + length
        if start < 0 or end > len(table):
            continue
        decoded = _decode_font_name_record(
            table[start:end],
            platform_id=platform_id,
            encoding_id=encoding_id,
        )
        if decoded is not None:
            names.append(decoded)
    return tuple(dict.fromkeys(names))


def _iter_font_files(search_dir: Path) -> tuple[Path, ...]:
    root = search_dir.expanduser()
    if not root.exists():
        return ()
    font_paths: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if Path(filename).suffix.lower() not in {".ttf", ".otf"}:
                continue
            font_paths.append(Path(dirpath) / filename)
    return tuple(font_paths)


@lru_cache(maxsize=16)
def _build_font_name_index(search_dir_keys: tuple[str, ...]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for search_dir_key in search_dir_keys:
        for font_path in _iter_font_files(Path(search_dir_key)):
            index.setdefault(_normalize_family_key(font_path.stem), font_path)
            for name in _font_name_table_names(font_path):
                index.setdefault(_normalize_family_key(name), font_path)
    return index


def _resolve_indexed_font_path(
    name: str,
    search_dirs: tuple[Path, ...],
) -> Path | None:
    search_dir_keys = tuple(str(path.expanduser()) for path in search_dirs)
    if not search_dir_keys:
        return None
    return _build_font_name_index(search_dir_keys).get(_normalize_family_key(name))


def _style_suffix(bold: bool, italic: bool) -> str:
    if bold and italic:
        return " Bold Italic"
    if bold:
        return " Bold"
    if italic:
        return " Italic"
    return ""


def _style_hints_from_family_name(family: str) -> tuple[bool, bool]:
    key = _normalize_family_key(family)
    tokens = set(key.replace("-", " ").replace("_", " ").split())
    bold = bool(
        tokens.intersection(
            {
                "black",
                "bold",
                "demibold",
                "extrabold",
                "heavy",
                "semibold",
                "ultrabold",
            }
        )
    )
    italic = bool(tokens.intersection({"italic", "oblique"}))
    return bold, italic


def _family_candidates_for_request(
    family: str, bold: bool, italic: bool
) -> tuple[tuple[str, str, bool], ...]:
    normalized_family = _normalize_family_name(family)
    styled_name = f"{normalized_family}{_style_suffix(bold, italic)}"
    if styled_name == normalized_family:
        return ((normalized_family, normalized_family, True),)
    return (
        (normalized_family, styled_name, True),
        (normalized_family, normalized_family, False),
    )


def get_platform_font_search_dirs(
    system_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """
    Return default font roots for the target platform.
    """
    env = os.environ if environ is None else environ
    if env.get("ALTIUM_FONT_DISABLE_SYSTEM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return ()
    system_value = platform.system() if system_name is None else system_name
    paths: list[Path] = []

    if system_value == "Windows":
        local_app_data = env.get("LOCALAPPDATA", "")
        paths.append(Path("C:/Windows/Fonts"))
        if local_app_data:
            local_path = Path(local_app_data)
            paths.append(local_path / "Microsoft" / "Windows" / "Fonts")
            paths.append(local_path / "Fonts")
        return _dedupe_paths(paths)

    home_value = env.get("HOME") or env.get("USERPROFILE") or "~"
    home_path = Path(home_value).expanduser()

    if system_value == "Darwin":
        paths.extend(
            (
                Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"),
                home_path / "Library" / "Fonts",
            )
        )
        return _dedupe_paths(paths)

    paths.extend(
        (
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home_path / ".local" / "share" / "fonts",
            home_path / ".fonts",
        )
    )
    return _dedupe_paths(paths)


def get_configured_font_dirs(
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """
    Return caller-configured font roots from ``ALTIUM_FONT_DIRS``.
    """
    env = os.environ if environ is None else environ
    raw_value = env.get("ALTIUM_FONT_DIRS", "").strip()
    if not raw_value:
        return ()
    return _dedupe_paths(
        [Path(part) for part in raw_value.split(os.pathsep) if part.strip()]
    )


def build_default_font_resolver_config(
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolverConfig:
    """
    Build the default host-aware font resolver configuration.
    """
    env = os.environ if environ is None else environ
    raw_mode = (
        env.get("ALTIUM_FONT_MODE", FontResolutionMode.COMPATIBLE.value).strip().lower()
    )
    try:
        mode = FontResolutionMode(raw_mode)
    except ValueError:
        mode = FontResolutionMode.COMPATIBLE

    configured_dirs = get_configured_font_dirs(env)
    system_dirs = get_platform_font_search_dirs(system_name, env)
    bundled_dirs = get_bundled_font_search_dirs()
    search_dirs = configured_dirs + system_dirs + bundled_dirs
    return FontResolverConfig(
        mode=mode,
        search_dirs=search_dirs,
        configured_dirs=configured_dirs,
        system_dirs=system_dirs,
        bundled_dirs=bundled_dirs,
        substitutions=DEFAULT_FAMILY_SUBSTITUTIONS,
        generic_sans_fallbacks=DEFAULT_SANS_FALLBACKS,
        generic_serif_fallbacks=DEFAULT_SERIF_FALLBACKS,
        generic_mono_fallbacks=DEFAULT_MONO_FALLBACKS,
    )


def portable_font_replacements() -> tuple[FontReplacementRule, ...]:
    """
    Return the default portable family replacements used by Altium WASM rendering.
    """
    return _PORTABLE_FONT_REPLACEMENT_RULES


def set_default_font_resolver_config(config: FontResolverConfig | None) -> None:
    """
    Set or clear a process-wide font resolver override.
    """
    global _FONT_RESOLVER_OVERRIDE
    _FONT_RESOLVER_OVERRIDE = config
    # reset_default_font_resolver_config() and configure_font_resolver() both
    # funnel through here, so this is the single invalidation point for the
    # default-path result cache.
    clear_font_resolution_result_cache()


def reset_default_font_resolver_config() -> None:
    """
    Clear any process-wide font resolver override.
    """
    set_default_font_resolver_config(None)


def configure_font_resolver(
    *,
    mode: FontResolutionMode | str | None = None,
    search_dirs: tuple[Path, ...] | list[Path] | None = None,
    alias_to_path: Mapping[str, Path] | None = None,
    substitutions: Mapping[str, tuple[str, ...]] | None = None,
    generic_sans_fallbacks: tuple[str, ...] | None = None,
    generic_serif_fallbacks: tuple[str, ...] | None = None,
    generic_mono_fallbacks: tuple[str, ...] | None = None,
) -> None:
    """
    Convenience wrapper for installing process-wide font resolver overrides.
    """
    parsed_mode: FontResolutionMode | None = None
    if mode is not None:
        parsed_mode = (
            mode
            if isinstance(mode, FontResolutionMode)
            else FontResolutionMode(str(mode))
        )
    set_default_font_resolver_config(
        FontResolverConfig(
            mode=parsed_mode,
            search_dirs=tuple(search_dirs) if search_dirs is not None else None,
            alias_to_path={} if alias_to_path is None else alias_to_path,
            substitutions={} if substitutions is None else substitutions,
            generic_sans_fallbacks=generic_sans_fallbacks,
            generic_serif_fallbacks=generic_serif_fallbacks,
            generic_mono_fallbacks=generic_mono_fallbacks,
        )
    )


def get_effective_font_resolver_config(
    config: FontResolverConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolverConfig:
    """
    Return the merged font resolver configuration for a lookup.
    """
    base = build_default_font_resolver_config(environ=environ, system_name=system_name)
    override = _FONT_RESOLVER_OVERRIDE if config is None else config

    if override is None:
        return base

    mode = base.mode if override.mode is None else override.mode
    search_dirs = (
        base.search_dirs
        if override.search_dirs is None
        else _dedupe_paths(list(override.search_dirs))
    )
    configured_dirs = (
        base.configured_dirs
        if override.search_dirs is None
        else override.configured_dirs
    )
    system_dirs = (
        base.system_dirs if override.search_dirs is None else override.system_dirs
    )
    bundled_dirs = (
        base.bundled_dirs if override.search_dirs is None else override.bundled_dirs
    )
    substitutions = dict(base.substitutions)
    substitutions.update(override.substitutions)

    alias_to_path: dict[str, Path] = {}
    alias_to_path.update(base.alias_to_path)
    alias_to_path.update(override.alias_to_path)

    return FontResolverConfig(
        mode=mode,
        search_dirs=search_dirs,
        configured_dirs=configured_dirs,
        system_dirs=system_dirs,
        bundled_dirs=bundled_dirs,
        alias_to_path=alias_to_path,
        substitutions=substitutions,
        generic_sans_fallbacks=(
            base.generic_sans_fallbacks
            if override.generic_sans_fallbacks is None
            else override.generic_sans_fallbacks
        ),
        generic_serif_fallbacks=(
            base.generic_serif_fallbacks
            if override.generic_serif_fallbacks is None
            else override.generic_serif_fallbacks
        ),
        generic_mono_fallbacks=(
            base.generic_mono_fallbacks
            if override.generic_mono_fallbacks is None
            else override.generic_mono_fallbacks
        ),
    )


def _resolve_known_name_path(
    name: str, config: FontResolverConfig
) -> tuple[Path | None, FontResolutionSource]:
    normalized_key = _normalize_family_key(name)

    test_font_path = _TEST_FONT_FILE_MAP_NORMALIZED.get(normalized_key)
    if test_font_path is not None and test_font_path.exists():
        return test_font_path, FontResolutionSource.TEST_ASSET

    search_dirs = config.search_dirs or ()
    for file_name in _known_file_candidates(name):
        for search_dir in search_dirs:
            candidate = _resolve_child_case_insensitive(search_dir, file_name)
            if candidate is not None:
                return candidate, _source_for_resolved_path(candidate, config)

    indexed_path = _resolve_indexed_font_path(name, search_dirs)
    if indexed_path is not None and indexed_path.exists():
        return indexed_path, _source_for_resolved_path(indexed_path, config)

    return None, FontResolutionSource.MISSING


def _resolve_font_name_path(
    name: str, config: FontResolverConfig
) -> tuple[Path | None, FontResolutionSource]:
    path_candidate = Path(name).expanduser()
    if path_candidate.is_absolute() and path_candidate.exists():
        return path_candidate, FontResolutionSource.EXPLICIT_PATH

    normalized_key = _normalize_family_key(name)
    for alias_name, alias_path in config.alias_to_path.items():
        if _normalize_family_key(alias_name) != normalized_key:
            continue
        expanded_alias = Path(alias_path).expanduser()
        if expanded_alias.exists():
            return expanded_alias, FontResolutionSource.ALIAS

    known_path, known_source = _resolve_known_name_path(name, config)
    if known_path is not None:
        return known_path, known_source

    return None, FontResolutionSource.MISSING


def _font_category(family: str) -> str:
    key = _normalize_family_key(family)
    if any(token in key for token in ("courier", "mono", "consolas", "code")):
        return "mono"
    if any(token in key for token in ("times", "serif", "georgia", "roman")):
        return "serif"
    return "sans"


def _generic_fallback_families(
    family: str, config: FontResolverConfig
) -> tuple[str, ...]:
    category = _font_category(family)
    if category == "mono":
        return config.generic_mono_fallbacks or ()
    if category == "serif":
        return config.generic_serif_fallbacks or ()
    return config.generic_sans_fallbacks or ()


def resolve_font(
    request: FontRequest,
    config: FontResolverConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolution:
    """
    Resolve a logical font request to an on-disk font file.

    Default-path lookups (no explicit ``config``, ``environ``, or
    ``system_name``) are served from a process-local result cache keyed by
    the request and the resolver-relevant environment variables. The cache
    is cleared by :func:`set_default_font_resolver_config` and
    :func:`clear_font_resolution_result_cache`.
    """
    global _FONT_RESOLUTION_RESULT_CACHE_HITS
    global _FONT_RESOLUTION_RESULT_CACHE_MISSES
    if config is not None or environ is not None or system_name is not None:
        return _resolve_font_uncached(
            request,
            config,
            environ=environ,
            system_name=system_name,
        )

    cache_key: tuple[object, ...] = (
        request.family,
        request.bold,
        request.italic,
        _default_resolver_env_fingerprint(),
    )
    cached = _FONT_RESOLUTION_RESULT_CACHE.get(cache_key)
    if cached is not None:
        _FONT_RESOLUTION_RESULT_CACHE_HITS += 1
        # Re-record so diagnostics behave exactly as the uncached path,
        # including after clear_font_resolution_diagnostics().
        return _record_font_resolution_diagnostic(cached)

    _FONT_RESOLUTION_RESULT_CACHE_MISSES += 1
    resolution = _resolve_font_uncached(request)
    _FONT_RESOLUTION_RESULT_CACHE[cache_key] = resolution
    return resolution


def _resolve_font_uncached(
    request: FontRequest,
    config: FontResolverConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolution:
    effective_config = get_effective_font_resolver_config(
        config,
        environ=environ,
        system_name=system_name,
    )
    substitution_map = {
        _normalize_family_key(name): tuple(values)
        for name, values in effective_config.substitutions.items()
    }

    tried_families: list[str] = []
    requested_family = _normalize_family_name(request.family)

    def resolve_family(
        family: str,
        *,
        bold: bool = request.bold,
        italic: bool = request.italic,
    ) -> tuple[Path | None, str | None, FontResolutionSource, bool]:
        for (
            base_family,
            candidate_name,
            style_matched,
        ) in _family_candidates_for_request(
            family,
            bold,
            italic,
        ):
            path, source = _resolve_font_name_path(candidate_name, effective_config)
            tried_families.append(candidate_name)
            if path is not None:
                return path, candidate_name, source, style_matched
        return None, None, FontResolutionSource.MISSING, False

    path, resolved_name, source, style_matched = resolve_family(requested_family)
    if path is not None:
        return _make_font_resolution(
            request=request,
            resolved_family=requested_family,
            resolved_name=resolved_name,
            path=path,
            status=FontResolutionStatus.EXACT
            if style_matched
            else FontResolutionStatus.STYLE_FALLBACK,
            source=source,
            tried_families=tuple(tried_families),
        )

    if effective_config.mode in (
        FontResolutionMode.COMPATIBLE,
        FontResolutionMode.BEST_EFFORT,
    ):
        substitution_chain = substitution_map.get(
            _normalize_family_key(requested_family), ()
        )
        seen_substitutes: set[str] = set()
        family_bold_hint, family_italic_hint = _style_hints_from_family_name(
            requested_family
        )
        for substitute in substitution_chain:
            normalized_substitute = _normalize_family_name(substitute)
            if normalized_substitute in seen_substitutes:
                continue
            seen_substitutes.add(normalized_substitute)
            path, resolved_name, source, _ = resolve_family(
                normalized_substitute,
                bold=request.bold or family_bold_hint,
                italic=request.italic or family_italic_hint,
            )
            if path is not None:
                return _make_font_resolution(
                    request=request,
                    resolved_family=normalized_substitute,
                    resolved_name=resolved_name,
                    path=path,
                    status=FontResolutionStatus.SUBSTITUTED,
                    source=source,
                    tried_families=tuple(tried_families),
                )

    if effective_config.mode in (
        FontResolutionMode.COMPATIBLE,
        FontResolutionMode.BEST_EFFORT,
    ):
        seen_fallbacks: set[str] = set()
        family_bold_hint, family_italic_hint = _style_hints_from_family_name(
            requested_family
        )
        for fallback_family in _generic_fallback_families(
            requested_family, effective_config
        ):
            normalized_fallback = _normalize_family_name(fallback_family)
            if (
                normalized_fallback in seen_fallbacks
                or normalized_fallback == requested_family
            ):
                continue
            seen_fallbacks.add(normalized_fallback)
            path, resolved_name, source, _ = resolve_family(
                normalized_fallback,
                bold=request.bold or family_bold_hint,
                italic=request.italic or family_italic_hint,
            )
            if path is not None:
                return _make_font_resolution(
                    request=request,
                    resolved_family=normalized_fallback,
                    resolved_name=resolved_name,
                    path=path,
                    status=FontResolutionStatus.GENERIC_FALLBACK,
                    source=source,
                    tried_families=tuple(tried_families),
                )

    return _make_font_resolution(
        request=request,
        resolved_family=None,
        resolved_name=None,
        path=None,
        status=FontResolutionStatus.MISSING,
        source=FontResolutionSource.MISSING,
        tried_families=tuple(tried_families),
    )


def resolve_font_with_style(
    font_name: str,
    bold: bool = False,
    italic: bool = False,
    *,
    config: FontResolverConfig | None = None,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolution:
    """
    Convenience wrapper around :func:`resolve_font` for family/style lookups.
    """
    return resolve_font(
        FontRequest(font_name, bold=bold, italic=italic),
        config=config,
        environ=environ,
        system_name=system_name,
    )
