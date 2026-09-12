"""Shared framing, limits, and errors for schematic JSON contracts."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import NoReturn


_I64_MIN = -(2**63)
_I64_MAX = 2**63 - 1


@dataclass(frozen=True, slots=True)
class SchematicContractLimits:
    """Caller-lowerable resource ceilings for schematic contract codecs."""

    max_input_bytes: int = 1_073_741_824
    max_output_bytes: int = 1_073_741_824
    max_recursion_depth: int = 128
    max_components: int = 1_000_000
    max_nets: int = 1_000_000
    max_terminals: int = 4_000_000
    max_endpoints: int = 4_000_000
    max_graphical_links: int = 4_000_000
    max_source_pages: int = 8_000_000
    max_aliases: int = 8_000_000
    max_hierarchy_paths: int = 8_000_000
    max_hierarchy_levels: int = 8_000_000
    max_parameter_pairs: int = 32_000_000
    max_json_values: int = 32_000_000
    max_array_elements: int = 32_000_000
    max_object_members: int = 32_000_000
    max_string_bytes: int = 16_777_215
    max_total_string_bytes: int = 8_589_934_592

    def __post_init__(self) -> None:
        for contract_field in fields(self):
            value = getattr(self, contract_field.name)
            maximum = contract_field.default
            if not isinstance(maximum, int):
                raise RuntimeError(
                    "schematic contract limit is missing an integer default"
                )
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or value > maximum
            ):
                raise SchematicContractError(
                    "invalid_limits",
                    f"/{contract_field.name}",
                    f"must be an integer from 0 through {maximum}",
                )


class SchematicContractError(ValueError):
    """Stable schematic transport failure with code and JSON Pointer path."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        location = path or "<root>"
        super().__init__(f"{code} at {location}: {detail}")


def resolve_limits(
    limits: SchematicContractLimits | None,
) -> SchematicContractLimits:
    """Return validated caller limits or the immutable defaults."""
    return limits if limits is not None else SchematicContractLimits()


def pointer_child(path: str, value: str | int) -> str:
    """Append one RFC 6901 path segment."""
    segment = str(value).replace("~", "~0").replace("/", "~1")
    return f"{path}/{segment}"


@dataclass(frozen=True, slots=True)
class JsonPreflightResult:
    """Small routing result retained by the allocation-bounded lexical scan."""

    schema: str | None
    schema_present: bool


class _ContractPreflightState:
    """Shared string-budget and error behavior for contract preflight passes."""

    limits: SchematicContractLimits
    total_string_bytes: int

    def _charge_string(self, value: str, path: str) -> None:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            self._error("invalid_json", path, "lone surrogate in string")
        if size > self.limits.max_string_bytes:
            self._error("string_limit", path, "decoded string limit exceeded")
        self.total_string_bytes += size
        if self.total_string_bytes > self.limits.max_total_string_bytes:
            self._error("total_string_limit", path, "total string limit exceeded")

    @staticmethod
    def _error(code: str, path: str, detail: str) -> NoReturn:
        raise SchematicContractError(code, path, detail)


class _JsonScanner(_ContractPreflightState):
    def __init__(self, text: str, limits: SchematicContractLimits) -> None:
        self.text = text
        self.limits = limits
        self.index = 0
        self.total_string_bytes = 0
        self.json_values = 0
        self.array_elements = 0
        self.object_members = 0
        self.schema: str | None = None
        self.schema_present = False

    def scan(self) -> JsonPreflightResult:
        self._skip_whitespace()
        if self.index == len(self.text):
            self._error("invalid_json", "", "empty JSON input")
        self._parse_value("", 0)
        self._skip_whitespace()
        if self.index != len(self.text):
            self._error("invalid_json", "", "trailing data")
        return JsonPreflightResult(self.schema, self.schema_present)

    def _parse_value(self, path: str, depth: int) -> None:
        self.json_values += 1
        if self.json_values > self.limits.max_json_values:
            self._error("member_limit", path, "JSON value limit exceeded")
        if self.index >= len(self.text):
            self._error("invalid_json", path, "unexpected end of input")
        token = self.text[self.index]
        if token == "{":
            self._parse_object(path, depth + 1)
        elif token == "[":
            self._parse_array(path, depth + 1)
        else:
            self._parse_scalar(token, path)

    def _parse_scalar(self, token: str, path: str) -> None:
        if self._starts_with_nonfinite_number():
            self._error("invalid_json", "", "non-finite numbers are invalid JSON")
        if token == '"':
            value = self._parse_string(path)
            if path == "/schema":
                self.schema = value
        elif token == "t":
            self._literal("true", path)
        elif token == "f":
            self._literal("false", path)
        elif token == "n":
            self._literal("null", path)
        elif token == "-" or token.isdigit():
            self._parse_number(path)
        else:
            self._error("invalid_json", path, f"unexpected token {token!r}")

    def _starts_with_nonfinite_number(self) -> bool:
        return any(
            self.text.startswith(token, self.index)
            for token in ("NaN", "Infinity", "-Infinity")
        )

    def _parse_object(self, path: str, depth: int) -> None:
        self._check_depth(path, depth)
        self.index += 1
        self._skip_whitespace()
        keys: set[str] = set()
        if self._consume("}"):
            return
        while True:
            self._parse_object_member(path, depth, keys)
            self._skip_whitespace()
            if self._consume("}"):
                return
            if not self._consume(","):
                self._error("invalid_json", path, "missing object separator")
            self._skip_whitespace()

    def _parse_object_member(self, path: str, depth: int, keys: set[str]) -> None:
        if self.index >= len(self.text) or self.text[self.index] != '"':
            self._error("invalid_json", path, "object key must be a string")
        key = self._parse_string(path)
        if key in keys:
            self._error("duplicate_key", path, f"duplicate object key {key!r}")
        keys.add(key)
        self.object_members += 1
        if self.object_members > self.limits.max_object_members:
            self._error("member_limit", path, "object member limit exceeded")
        self._skip_whitespace()
        if not self._consume(":"):
            self._error("invalid_json", path, "missing colon after object key")
        self._skip_whitespace()
        child_path = pointer_child(path, key)
        if path == "" and key == "schema":
            self.schema_present = True
        self._parse_value(child_path, depth)

    def _parse_array(self, path: str, depth: int) -> None:
        self._check_depth(path, depth)
        self.index += 1
        self._skip_whitespace()
        local_index = 0
        if self._consume("]"):
            return
        while True:
            self.array_elements += 1
            if self.array_elements > self.limits.max_array_elements:
                self._error("member_limit", path, "array element limit exceeded")
            self._parse_value(pointer_child(path, local_index), depth)
            local_index += 1
            self._skip_whitespace()
            if self._consume("]"):
                return
            if not self._consume(","):
                self._error("invalid_json", path, "missing array separator")
            self._skip_whitespace()

    def _parse_string(self, path: str) -> str:
        start = self.index
        self.index += 1
        escaped = False
        while self.index < len(self.text):
            character = self.text[self.index]
            if escaped:
                self._consume_string_escape(character, path)
                escaped = False
                continue
            if character == "\\":
                escaped = True
                self.index += 1
                continue
            if character == '"':
                self.index += 1
                return self._decode_string(start, path)
            if ord(character) < 0x20:
                self._error("invalid_json", path, "unescaped control character")
            self.index += 1
        self._error("invalid_json", path, "unterminated string")

    def _consume_string_escape(self, character: str, path: str) -> None:
        if character == "u":
            digits = self.text[self.index + 1 : self.index + 5]
            if len(digits) != 4 or any(
                digit not in "0123456789abcdefABCDEF" for digit in digits
            ):
                self._error("invalid_json", path, "invalid Unicode escape")
            self.index += 5
        elif character in '"\\/bfnrt':
            self.index += 1
        else:
            self._error("invalid_json", path, "invalid string escape")

    def _decode_string(self, start: int, path: str) -> str:
        token = self.text[start : self.index]
        try:
            decoded = json.loads(token)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            self._error("invalid_json", path, str(error))
        if not isinstance(decoded, str):
            self._error("invalid_json", path, "invalid string token")
        self._charge_string(decoded, path)
        return decoded

    def _parse_number(self, path: str) -> None:
        start = self.index
        self._parse_number_sign(path)
        self._parse_integer_part(path)
        integral = not self._parse_fraction(path)
        if self._parse_exponent(path):
            integral = False
        if integral:
            self._validate_integer_range(start, path)

    def _parse_number_sign(self, path: str) -> None:
        if self._consume("-") and self.index == len(self.text):
            self._error("invalid_json", path, "incomplete number")

    def _parse_integer_part(self, path: str) -> None:
        if self._consume("0"):
            if self.index < len(self.text) and self.text[self.index].isdigit():
                self._error("invalid_json", path, "leading zero in number")
            return
        self._consume_required_digits(path, "invalid number")

    def _parse_fraction(self, path: str) -> bool:
        if not self._consume("."):
            return False
        self._consume_required_digits(path, "invalid fractional number")
        return True

    def _parse_exponent(self, path: str) -> bool:
        if self.index >= len(self.text) or self.text[self.index] not in "eE":
            return False
        self.index += 1
        if self.index < len(self.text) and self.text[self.index] in "+-":
            self.index += 1
        self._consume_required_digits(path, "invalid exponent")
        return True

    def _consume_required_digits(self, path: str, detail: str) -> None:
        if self.index >= len(self.text) or not self.text[self.index].isdigit():
            self._error("invalid_json", path, detail)
        while self.index < len(self.text) and self.text[self.index].isdigit():
            self.index += 1

    def _validate_integer_range(self, start: int, path: str) -> None:
        value = int(self.text[start : self.index])
        if value < _I64_MIN or value > _I64_MAX:
            self._error("integer_range", path, "integer is outside signed 64-bit")

    def _literal(self, expected: str, path: str) -> None:
        if not self.text.startswith(expected, self.index):
            self._error(
                "invalid_json", path, f"invalid literal starting with {expected[0]!r}"
            )
        self.index += len(expected)

    def _check_depth(self, path: str, depth: int) -> None:
        if depth > self.limits.max_recursion_depth:
            self._error("depth_limit", path, "JSON recursion depth exceeded")

    def _skip_whitespace(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _consume(self, token: str) -> bool:
        if self.index < len(self.text) and self.text[self.index] == token:
            self.index += 1
            return True
        return False


def preflight_json_text(
    text: str, limits: SchematicContractLimits | None = None
) -> JsonPreflightResult:
    """Lexically validate JSON text before its one materializing DTO decode."""
    active = resolve_limits(limits)
    if not isinstance(text, str):
        raise SchematicContractError("type_mismatch", "", "expected JSON text")
    if text.startswith("\ufeff"):
        raise SchematicContractError("json_bom", "", "UTF-8 BOM is forbidden")
    size = 0
    for character in text:
        try:
            size += len(character.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise SchematicContractError(
                "invalid_json", "", "lone surrogate outside an admitted string"
            ) from error
        if size > active.max_input_bytes:
            raise SchematicContractError(
                "input_too_large", "", "JSON input byte limit exceeded"
            )
    return _JsonScanner(text, active).scan()


def preflight_json_bytes(
    raw: bytes, limits: SchematicContractLimits | None = None
) -> tuple[str, JsonPreflightResult]:
    """Validate UTF-8 framing and lexically scan caller-owned JSON bytes."""
    active = resolve_limits(limits)
    if not isinstance(raw, bytes):
        raise SchematicContractError("type_mismatch", "", "expected JSON bytes")
    if len(raw) > active.max_input_bytes:
        raise SchematicContractError(
            "input_too_large", "", "JSON input byte limit exceeded"
        )
    if raw.startswith(b"\xef\xbb\xbf"):
        raise SchematicContractError("json_bom", "", "UTF-8 BOM is forbidden")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SchematicContractError("invalid_utf8", "", str(error)) from error
    return text, _JsonScanner(text, active).scan()


def preflight_mapping(
    mapping: Mapping[str, object], limits: SchematicContractLimits | None = None
) -> JsonPreflightResult:
    """Validate an already-materialized JSON-like mapping and its budgets."""
    active = resolve_limits(limits)
    if not isinstance(mapping, Mapping):
        raise SchematicContractError("type_mismatch", "", "expected object mapping")
    state = _MappingPreflight(active)
    state.visit(mapping, "", 0)
    schema_present = "schema" in mapping
    schema_value = mapping.get("schema")
    return JsonPreflightResult(
        schema_value if isinstance(schema_value, str) else None,
        schema_present,
    )


class _MappingPreflight(_ContractPreflightState):
    def __init__(self, limits: SchematicContractLimits) -> None:
        self.limits = limits
        self.active: set[int] = set()
        self.values = 0
        self.array_elements = 0
        self.object_members = 0
        self.total_string_bytes = 0

    def visit(self, value: object, path: str, depth: int) -> None:
        self.values += 1
        if self.values > self.limits.max_json_values:
            self._error("member_limit", path, "JSON value limit exceeded")
        if self._visit_scalar(value, path):
            return
        if isinstance(value, Mapping):
            self._visit_mapping(value, path, depth + 1)
        elif isinstance(value, list):
            self._visit_list(value, path, depth + 1)
        else:
            self._error("type_mismatch", path, "value is not JSON-compatible")

    def _visit_scalar(self, value: object, path: str) -> bool:
        if isinstance(value, str):
            self._charge_string(value, path)
            return True
        if value is None or isinstance(value, bool):
            return True
        if isinstance(value, int):
            if value < _I64_MIN or value > _I64_MAX:
                self._error("integer_range", path, "integer is outside signed 64-bit")
            return True
        if isinstance(value, float):
            if not math.isfinite(value):
                self._error("type_mismatch", path, "non-finite number")
            return True
        return False

    def _visit_mapping(
        self, value: Mapping[object, object], path: str, depth: int
    ) -> None:
        self._check_container(value, path, depth)
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    self._error("type_mismatch", path, "object key must be a string")
                self._charge_string(key, path)
                self.object_members += 1
                if self.object_members > self.limits.max_object_members:
                    self._error("member_limit", path, "object member limit exceeded")
                self.visit(child, pointer_child(path, key), depth)
        finally:
            self.active.remove(id(value))

    def _visit_list(self, value: list[object], path: str, depth: int) -> None:
        self._check_container(value, path, depth)
        try:
            for index, child in enumerate(value):
                self.array_elements += 1
                if self.array_elements > self.limits.max_array_elements:
                    self._error("member_limit", path, "array element limit exceeded")
                self.visit(child, pointer_child(path, index), depth)
        finally:
            self.active.remove(id(value))

    def _check_container(self, value: object, path: str, depth: int) -> None:
        if depth > self.limits.max_recursion_depth:
            self._error("depth_limit", path, "JSON recursion depth exceeded")
        identity = id(value)
        if identity in self.active:
            self._error("invariant", path, "cyclic JSON container")
        self.active.add(identity)


def enforce_output_limit(raw: bytes, limits: SchematicContractLimits | None) -> bytes:
    """Reject an encoded payload above the caller's output ceiling."""
    active = resolve_limits(limits)
    if len(raw) > active.max_output_bytes:
        raise SchematicContractError(
            "output_too_large", "", "JSON output byte limit exceeded"
        )
    return raw


def enforce_collection_limit(count: int, maximum: int, path: str, detail: str) -> None:
    """Apply one domain collection ceiling with a stable error."""
    if count > maximum:
        raise SchematicContractError("collection_limit", path, detail)


__all__ = (
    "JsonPreflightResult",
    "SchematicContractError",
    "SchematicContractLimits",
    "enforce_collection_limit",
    "enforce_output_limit",
    "pointer_child",
    "preflight_json_bytes",
    "preflight_json_text",
    "preflight_mapping",
    "resolve_limits",
)
