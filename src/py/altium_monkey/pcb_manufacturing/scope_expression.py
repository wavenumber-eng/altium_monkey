"""Parse bounded PCB rule scope expressions without evaluating them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

_TokenKind = Literal[
    "identifier",
    "single_string",
    "double_string",
    "left",
    "right",
    "equals",
    "end",
]
_PredicateForm = Literal["bare", "call", "equals"]
_QuoteStyle = Literal["single", "double"]

_MAX_SCOPE_SOURCE_CHARS = 8192
_MAX_SCOPE_TOKENS = 2048
_MAX_SCOPE_NODES = 512
_MAX_SCOPE_NESTING = 64


@dataclass(frozen=True)
class _ScopeExpressionSyntaxError(ValueError):
    detail: str
    position: int


@dataclass(frozen=True)
class _ScopePredicate:
    source_text: str
    start: int
    end: int
    name: str
    form: _PredicateForm
    argument: str | None = None
    quote_style: _QuoteStyle | None = None


@dataclass(frozen=True)
class _ScopeNot:
    source_text: str
    start: int
    end: int
    operand: _ScopeExpression


@dataclass(frozen=True)
class _ScopeAnd:
    source_text: str
    start: int
    end: int
    left: _ScopeExpression
    right: _ScopeExpression


@dataclass(frozen=True)
class _ScopeOr:
    source_text: str
    start: int
    end: int
    left: _ScopeExpression
    right: _ScopeExpression


_ScopeExpression: TypeAlias = _ScopePredicate | _ScopeNot | _ScopeAnd | _ScopeOr


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    text: str
    start: int
    end: int


class _ScopeParser:
    def __init__(self, source_text: str) -> None:
        self._source_text = source_text
        self._position = 0
        self._token_count = 0
        self._node_count = 0
        self._nesting = 0
        self._current = self._next_token()

    def parse(self) -> _ScopeExpression:
        if self._current.kind == "end":
            raise _ScopeExpressionSyntaxError("scope expression is empty", 0)
        expression = self._parse_binary()
        if self._current.kind != "end":
            self._fail("unexpected trailing scope token")
        return _replace_expression_source(
            expression,
            self._source_text,
            0,
            len(self._source_text),
        )

    def _parse_binary(self) -> _ScopeExpression:
        expression = self._parse_unary()
        while self._is_identifier("and") or self._is_identifier("or"):
            operator = self._take()
            right = self._parse_unary()
            self._reserve_node(operator.start)
            source_text = self._source_text[
                _expression_start(expression) : _expression_end(right)
            ]
            if operator.text.casefold() == "and":
                expression = _ScopeAnd(
                    source_text,
                    _expression_start(expression),
                    _expression_end(right),
                    expression,
                    right,
                )
            else:
                expression = _ScopeOr(
                    source_text,
                    _expression_start(expression),
                    _expression_end(right),
                    expression,
                    right,
                )
        return expression

    def _parse_unary(self) -> _ScopeExpression:
        if not self._is_identifier("not"):
            return self._parse_primary()
        start = self._take().start
        self._enter_nesting(start)
        try:
            operand = self._parse_unary()
        finally:
            self._leave_nesting()
        self._reserve_node(start)
        return _ScopeNot(
            self._source_text[start : _expression_end(operand)],
            start,
            _expression_end(operand),
            operand,
        )

    def _parse_primary(self) -> _ScopeExpression:
        if self._current.kind == "left":
            start = self._take().start
            self._enter_nesting(start)
            try:
                expression = self._parse_binary()
                closing = self._expect("right", "closing parenthesis is required")
            finally:
                self._leave_nesting()
            return _replace_expression_source(
                expression,
                self._source_text[start : closing.end],
                start,
                closing.end,
            )
        return self._parse_predicate()

    def _parse_predicate(self) -> _ScopePredicate:
        name = self._expect("identifier", "scope predicate is required")
        self._reserve_node(name.start)
        if self._current.kind == "left":
            return self._parse_call(name)
        if self._current.kind == "equals":
            self._take()
            argument = self._quoted_value("quoted comparison value is required")
            return _ScopePredicate(
                self._source_text[name.start : argument.end],
                name.start,
                argument.end,
                name.text.casefold(),
                "equals",
                argument.text,
                _quote_style(argument),
            )
        return _ScopePredicate(
            name.text,
            name.start,
            name.end,
            name.text.casefold(),
            "bare",
        )

    def _parse_call(self, name: _Token) -> _ScopePredicate:
        self._take()
        argument = None
        quote_style = None
        if self._current.kind in {"single_string", "double_string"}:
            argument_token = self._take()
            argument = argument_token.text
            quote_style = _quote_style(argument_token)
        closing = self._expect("right", "closing predicate parenthesis is required")
        return _ScopePredicate(
            self._source_text[name.start : closing.end],
            name.start,
            closing.end,
            name.text.casefold(),
            "call",
            argument,
            quote_style,
        )

    def _quoted_value(self, detail: str) -> _Token:
        if self._current.kind not in {"single_string", "double_string"}:
            self._fail(detail)
        return self._take()

    def _is_identifier(self, value: str) -> bool:
        return (
            self._current.kind == "identifier"
            and self._current.text.casefold() == value
        )

    def _expect(self, kind: _TokenKind, detail: str) -> _Token:
        if self._current.kind != kind:
            self._fail(detail)
        return self._take()

    def _take(self) -> _Token:
        current = self._current
        self._current = self._next_token()
        return current

    def _next_token(self) -> _Token:
        self._skip_whitespace()
        start = self._position
        if start == len(self._source_text):
            return self._token("end", "", start, start)
        current = self._source_text[start]
        if _is_ascii_letter(current) or current == "_":
            return self._identifier_token()
        if current in {"'", '"'}:
            return self._string_token(current)
        punctuation: dict[str, _TokenKind] = {
            "(": "left",
            ")": "right",
            "=": "equals",
        }
        kind = punctuation.get(current)
        if kind is None:
            raise _ScopeExpressionSyntaxError(
                f"unsupported scope character {current!r}",
                start,
            )
        self._position += 1
        return self._token(kind, current, start, self._position)

    def _skip_whitespace(self) -> None:
        while (
            self._position < len(self._source_text)
            and self._source_text[self._position].isspace()
        ):
            self._position += 1

    def _identifier_token(self) -> _Token:
        start = self._position
        self._position += 1
        while self._position < len(self._source_text):
            current = self._source_text[self._position]
            if not _is_identifier_character(current):
                break
            self._position += 1
        return self._token(
            "identifier",
            self._source_text[start : self._position],
            start,
            self._position,
        )

    def _string_token(self, delimiter: str) -> _Token:
        start = self._position
        self._position += 1
        value_start = self._position
        while self._position < len(self._source_text):
            current = self._source_text[self._position]
            if current == delimiter:
                value = self._source_text[value_start : self._position]
                self._position += 1
                if not value or "\r" in value or "\n" in value:
                    raise _ScopeExpressionSyntaxError(
                        "quoted scope value must be nonempty and single-line",
                        start,
                    )
                kind: _TokenKind = (
                    "single_string" if delimiter == "'" else "double_string"
                )
                return self._token(kind, value, start, self._position)
            self._position += 1
        raise _ScopeExpressionSyntaxError("quoted scope value is not closed", start)

    def _fail(self, detail: str) -> None:
        raise _ScopeExpressionSyntaxError(detail, self._current.start)

    def _token(self, kind: _TokenKind, text: str, start: int, end: int) -> _Token:
        self._token_count += 1
        if self._token_count > _MAX_SCOPE_TOKENS:
            raise _ScopeExpressionSyntaxError(
                f"scope expression exceeds token safety budget {_MAX_SCOPE_TOKENS}",
                start,
            )
        return _Token(kind, text, start, end)

    def _reserve_node(self, position: int) -> None:
        self._node_count += 1
        if self._node_count > _MAX_SCOPE_NODES:
            raise _ScopeExpressionSyntaxError(
                f"scope expression exceeds node safety budget {_MAX_SCOPE_NODES}",
                position,
            )

    def _enter_nesting(self, position: int) -> None:
        self._nesting += 1
        if self._nesting > _MAX_SCOPE_NESTING:
            raise _ScopeExpressionSyntaxError(
                f"scope expression exceeds unary/group nesting safety budget "
                f"{_MAX_SCOPE_NESTING}",
                position,
            )

    def _leave_nesting(self) -> None:
        self._nesting -= 1


def _parse_scope_expression(source_text: str) -> _ScopeExpression:
    if type(source_text) is not str:
        raise _ScopeExpressionSyntaxError("scope expression must be text", 0)
    if len(source_text) > _MAX_SCOPE_SOURCE_CHARS:
        raise _ScopeExpressionSyntaxError(
            f"scope expression exceeds source-length safety budget "
            f"{_MAX_SCOPE_SOURCE_CHARS}",
            _MAX_SCOPE_SOURCE_CHARS,
        )
    return _ScopeParser(source_text).parse()


def _is_ascii_letter(value: str) -> bool:
    return "a" <= value <= "z" or "A" <= value <= "Z"


def _is_identifier_character(value: str) -> bool:
    return (
        _is_ascii_letter(value) or value.isascii() and value.isdigit() or value == "_"
    )


def _quote_style(token: _Token) -> _QuoteStyle:
    return "single" if token.kind == "single_string" else "double"


def _expression_start(expression: _ScopeExpression) -> int:
    return expression.start


def _expression_end(expression: _ScopeExpression) -> int:
    return expression.end


def _replace_expression_source(
    expression: _ScopeExpression,
    source_text: str,
    start: int,
    end: int,
) -> _ScopeExpression:
    if isinstance(expression, _ScopePredicate):
        return _ScopePredicate(
            source_text,
            start,
            end,
            expression.name,
            expression.form,
            expression.argument,
            expression.quote_style,
        )
    if isinstance(expression, _ScopeNot):
        return _ScopeNot(source_text, start, end, expression.operand)
    if isinstance(expression, _ScopeAnd):
        return _ScopeAnd(source_text, start, end, expression.left, expression.right)
    return _ScopeOr(source_text, start, end, expression.left, expression.right)


__all__: tuple[str, ...] = ()
