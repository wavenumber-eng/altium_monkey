"""
Shared font resolution policy for Altium text rendering.

This module keeps font-family semantics separate from text-metrics math. The
parsed model can continue to use logical Altium family names while this layer
decides which on-disk font file should satisfy a request on the current host.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from enum import Enum
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
class FontResolverConfig:
    """
    Optional overrides for the shared font resolver.
    """

    mode: FontResolutionMode | None = None
    search_dirs: tuple[Path, ...] | None = None
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


TEST_FONTS_DIR = Path(__file__).resolve().parents[3] / "tests" / "common" / "assets" / "fonts"

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
    "Berkeley Mono Regular": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Regular.ttf",
    "BerkeleyMono Regular": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Regular.ttf",
    "Berkeley Mono Bold": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Bold.ttf",
    "BerkeleyMono Bold": TEST_FONTS_DIR / "BerkeleyMono" / "BerkeleyMono-Bold.ttf",
    "Mooretronics": TEST_FONTS_DIR / "ElectronicSymbols.ttf",
    "ElectronicSymbols": TEST_FONTS_DIR / "ElectronicSymbols.ttf",
    "Old Stamper": TEST_FONTS_DIR / "old_stamper.ttf",
}

DEFAULT_FAMILY_SUBSTITUTIONS: dict[str, tuple[str, ...]] = {
    "Arial": ("Arimo", "Liberation Sans", "DejaVu Sans"),
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
)
DEFAULT_MONO_FALLBACKS: tuple[str, ...] = (
    "Cousine",
    "Liberation Mono",
    "DejaVu Sans Mono",
    "Courier New",
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


def _normalize_family_name(name: str) -> str:
    return " ".join(name.strip().strip("\"'").split())


def _normalize_family_key(name: str) -> str:
    return _normalize_family_name(name).lower()


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


def _style_suffix(bold: bool, italic: bool) -> str:
    if bold and italic:
        return " Bold Italic"
    if bold:
        return " Bold"
    if italic:
        return " Italic"
    return ""


def _family_candidates_for_request(family: str, bold: bool, italic: bool) -> tuple[tuple[str, str, bool], ...]:
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


def get_configured_font_dirs(environ: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """
    Return caller-configured font roots from ``ALTIUM_FONT_DIRS``.
    """
    env = os.environ if environ is None else environ
    raw_value = env.get("ALTIUM_FONT_DIRS", "").strip()
    if not raw_value:
        return ()
    return _dedupe_paths([Path(part) for part in raw_value.split(os.pathsep) if part.strip()])


def build_default_font_resolver_config(
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
) -> FontResolverConfig:
    """
    Build the default host-aware font resolver configuration.
    """
    env = os.environ if environ is None else environ
    raw_mode = env.get("ALTIUM_FONT_MODE", FontResolutionMode.COMPATIBLE.value).strip().lower()
    try:
        mode = FontResolutionMode(raw_mode)
    except ValueError:
        mode = FontResolutionMode.COMPATIBLE

    search_dirs = get_configured_font_dirs(env) + get_platform_font_search_dirs(system_name, env)
    return FontResolverConfig(
        mode=mode,
        search_dirs=search_dirs,
        substitutions=DEFAULT_FAMILY_SUBSTITUTIONS,
        generic_sans_fallbacks=DEFAULT_SANS_FALLBACKS,
        generic_serif_fallbacks=DEFAULT_SERIF_FALLBACKS,
        generic_mono_fallbacks=DEFAULT_MONO_FALLBACKS,
    )


def set_default_font_resolver_config(config: FontResolverConfig | None) -> None:
    """
    Set or clear a process-wide font resolver override.
    """
    global _FONT_RESOLVER_OVERRIDE
    _FONT_RESOLVER_OVERRIDE = config


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
        parsed_mode = mode if isinstance(mode, FontResolutionMode) else FontResolutionMode(str(mode))
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
    search_dirs = base.search_dirs if override.search_dirs is None else _dedupe_paths(list(override.search_dirs))
    substitutions = dict(base.substitutions)
    substitutions.update(override.substitutions)

    alias_to_path: dict[str, Path] = {}
    alias_to_path.update(base.alias_to_path)
    alias_to_path.update(override.alias_to_path)

    return FontResolverConfig(
        mode=mode,
        search_dirs=search_dirs,
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


def _resolve_known_name_path(name: str, search_dirs: tuple[Path, ...]) -> Path | None:
    normalized_key = _normalize_family_key(name)

    test_font_path = _TEST_FONT_FILE_MAP_NORMALIZED.get(normalized_key)
    if test_font_path is not None and test_font_path.exists():
        return test_font_path

    file_name = _KNOWN_FONT_FILE_MAP_NORMALIZED.get(normalized_key)
    if file_name is None:
        return None

    for search_dir in search_dirs:
        candidate = search_dir / file_name
        if candidate.exists():
            return candidate

    return None


def _resolve_font_name_path(name: str, config: FontResolverConfig) -> tuple[Path | None, FontResolutionSource]:
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

    test_font_path = _TEST_FONT_FILE_MAP_NORMALIZED.get(normalized_key)
    if test_font_path is not None and test_font_path.exists():
        return test_font_path, FontResolutionSource.TEST_ASSET

    known_path = _resolve_known_name_path(name, config.search_dirs or ())
    if known_path is not None:
        return known_path, FontResolutionSource.SEARCH_DIR

    return None, FontResolutionSource.MISSING


def _font_category(family: str) -> str:
    key = _normalize_family_key(family)
    if any(token in key for token in ("courier", "mono", "consolas", "code")):
        return "mono"
    if any(token in key for token in ("times", "serif", "georgia", "roman")):
        return "serif"
    return "sans"


def _generic_fallback_families(family: str, config: FontResolverConfig) -> tuple[str, ...]:
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
    """
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

    def resolve_family(family: str) -> tuple[Path | None, str | None, FontResolutionSource, bool]:
        for base_family, candidate_name, style_matched in _family_candidates_for_request(
            family,
            request.bold,
            request.italic,
        ):
            path, source = _resolve_font_name_path(candidate_name, effective_config)
            tried_families.append(candidate_name)
            if path is not None:
                return path, candidate_name, source, style_matched
        return None, None, FontResolutionSource.MISSING, False

    path, resolved_name, source, style_matched = resolve_family(requested_family)
    if path is not None:
        return FontResolution(
            request=request,
            resolved_family=requested_family,
            resolved_name=resolved_name,
            path=path,
            status=FontResolutionStatus.EXACT if style_matched else FontResolutionStatus.STYLE_FALLBACK,
            source=source,
            tried_families=tuple(tried_families),
        )

    if effective_config.mode in (FontResolutionMode.COMPATIBLE, FontResolutionMode.BEST_EFFORT):
        substitution_chain = substitution_map.get(_normalize_family_key(requested_family), ())
        seen_substitutes: set[str] = set()
        for substitute in substitution_chain:
            normalized_substitute = _normalize_family_name(substitute)
            if normalized_substitute in seen_substitutes:
                continue
            seen_substitutes.add(normalized_substitute)
            path, resolved_name, source, _ = resolve_family(normalized_substitute)
            if path is not None:
                return FontResolution(
                    request=request,
                    resolved_family=normalized_substitute,
                    resolved_name=resolved_name,
                    path=path,
                    status=FontResolutionStatus.SUBSTITUTED,
                    source=source,
                    tried_families=tuple(tried_families),
                )

    if effective_config.mode == FontResolutionMode.BEST_EFFORT:
        seen_fallbacks: set[str] = set()
        for fallback_family in _generic_fallback_families(requested_family, effective_config):
            normalized_fallback = _normalize_family_name(fallback_family)
            if normalized_fallback in seen_fallbacks or normalized_fallback == requested_family:
                continue
            seen_fallbacks.add(normalized_fallback)
            path, resolved_name, source, _ = resolve_family(normalized_fallback)
            if path is not None:
                return FontResolution(
                    request=request,
                    resolved_family=normalized_fallback,
                    resolved_name=resolved_name,
                    path=path,
                    status=FontResolutionStatus.GENERIC_FALLBACK,
                    source=source,
                    tried_families=tuple(tried_families),
                )

    return FontResolution(
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
