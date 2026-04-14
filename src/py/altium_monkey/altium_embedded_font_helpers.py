"""
Shared helpers for embedded font extraction and caching.
"""

from __future__ import annotations

import re


def safe_embedded_font_filename_component(value: str) -> str:
    """Normalize a font name/style token for temp-file caching."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "font"
