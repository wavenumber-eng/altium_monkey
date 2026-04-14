"""
PCB special-string substitution helpers shared by SVG and IPC exporters.

PCB text primitives can include dot-prefixed project parameters (for example
`.PCB_MIXDOWN`). Altium also supports simple concatenation expressions where
segments are joined with `+`.
"""

from __future__ import annotations

from collections.abc import Mapping
import re

_PCB_SPECIAL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\.([A-Za-z_][A-Za-z0-9_]*)")


def normalize_project_parameters(
    project_parameters: Mapping[object, object] | None,
) -> dict[str, str]:
    """
    Normalize project parameters into a case-insensitive map.
    
    Keys are lower-cased; values are stringified.
    """
    result: dict[str, str] = {}
    if not project_parameters:
        return result

    for key, value in project_parameters.items():
        if key is None or value is None:
            continue
        norm_key = str(key).strip().lower()
        if not norm_key:
            continue
        result[norm_key] = str(value)
    return result


def _resolve_token(token_name: str, params_ci: dict[str, str]) -> str | None:
    key = token_name.lower()
    if key in params_ci:
        return params_ci[key]
    if key == "variantname":
        return "[No Variations]"
    return None


def _parse_plus_concat_terms(text: str) -> list[tuple[str, str]] | None:
    """
    Parse a simple PCB concatenation expression into ordered terms.
    
    Supported terms:
    - dot token: `.PARAM`
    - quoted literal: `'text'` or `"text"` (doubled quote escapes supported)
    - raw segment: unquoted text up to the next `+`
    """
    i = 0
    n = len(text)
    terms: list[tuple[str, str]] = []
    expect_term = True

    def _skip_ws(pos: int) -> int:
        while pos < n and text[pos].isspace():
            pos += 1
        return pos

    while True:
        i = _skip_ws(i)
        if i >= n:
            break

        if expect_term:
            ch = text[i]
            if ch in {"'", '"'}:
                quote = ch
                i += 1
                chars: list[str] = []
                while i < n:
                    cur = text[i]
                    if cur == quote:
                        if i + 1 < n and text[i + 1] == quote:
                            chars.append(quote)
                            i += 2
                            continue
                        i += 1
                        break
                    chars.append(cur)
                    i += 1
                else:
                    return None
                terms.append(("literal", "".join(chars)))
            elif ch == ".":
                j = i + 1
                if j >= n or not (text[j].isalpha() or text[j] == "_"):
                    return None
                j += 1
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                terms.append(("token", text[i + 1 : j]))
                i = j
            else:
                j = i
                while j < n and text[j] != "+":
                    j += 1
                raw = text[i:j].strip()
                if not raw:
                    return None
                terms.append(("raw", raw))
                i = j
            expect_term = False
            continue

        if text[i] != "+":
            return None
        i += 1
        expect_term = True

    if not terms or expect_term:
        return None
    return terms


def _substitute_tokens_only(text: str, params_ci: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        token = match.group(1)
        resolved = _resolve_token(token, params_ci)
        if resolved is None:
            return match.group(0)
        return resolved

    return _PCB_SPECIAL_TOKEN_RE.sub(_replace, text)


def substitute_pcb_special_strings(text: str, params_ci: dict[str, str]) -> str:
    """
    Substitute PCB special strings from a case-insensitive parameter map.
    
    Behavior:
    - Dot tokens resolve from `params_ci` (for example `.PCB_MIXDOWN`)
    - `.VariantName` defaults to `[No Variations]` when not provided
    - If the text is a concatenation expression (`a + b + c`) containing at
      least one dot token, expression terms are joined after substitution
    - Unresolved tokens are preserved literally
    """
    if not text or "." not in text:
        return text

    if "+" in text:
        terms = _parse_plus_concat_terms(text)
        if terms and any(kind == "token" for kind, _ in terms):
            out: list[str] = []
            for kind, value in terms:
                if kind == "token":
                    resolved = _resolve_token(value, params_ci)
                    out.append(resolved if resolved is not None else f".{value}")
                else:
                    out.append(value)
            return "".join(out)

    return _substitute_tokens_only(text, params_ci)
