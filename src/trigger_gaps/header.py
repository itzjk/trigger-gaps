from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator


ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def fold(value: str) -> str:
    return value.translate(ASCII_LOWER)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    raw: str
    offset: int
    line: int
    column: int

    def location(self) -> dict:
        return {"offset": self.offset, "line": self.line, "column": self.column,
                "length": len(self.raw)}

    def is_word(self, word: str) -> bool:
        return self.kind == "word" and fold(self.value) == fold(word)


class UnsupportedHeader(Exception):
    def __init__(self, reason: str, token: Token):
        super().__init__(reason)
        self.reason = reason
        self.token = token


def tokens(sql: str, tick: Callable[[], None]) -> Iterator[Token]:
    i, line, column = 0, 1, 1
    while i < len(sql):
        tick()
        start, start_line, start_column = i, line, column
        c = sql[i]
        kind = "symbol"
        if c in " \t\r\n\f":
            i += 1
            while i < len(sql) and sql[i] in " \t\r\n\f":
                i += 1
            kind = "skip"
        elif sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end
            kind = "skip"
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = len(sql) if end < 0 else end + 2
            kind = "invalid" if end < 0 else "skip"
        elif c in "\"'`[":
            closing = "]" if c == "[" else c
            i += 1
            closed = False
            while i < len(sql):
                if i % 4096 == 0:
                    tick()
                if sql[i] == closing:
                    i += 1
                    if closing != "]" and i < len(sql) and sql[i] == closing:
                        i += 1
                        continue
                    closed = True
                    break
                i += 1
            kind = ({'"': "identifier", "'": "string"}.get(c, "unsupported")
                    if closed else "invalid")
        elif c.isascii() and (c.isalpha() or c == "_"):
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in "_$" or not sql[i].isascii()):
                i += 1
            kind = "word" if sql[start:i].isascii() else "unsupported"
        elif not c.isascii():
            i += 1
            while i < len(sql) and (sql[i].isalnum() or sql[i] in "_$" or not sql[i].isascii()):
                i += 1
            kind = "unsupported"
        else:
            i += 1
        raw = sql[start:i]
        breaks = raw.count("\n")
        line += breaks
        column = len(raw.rsplit("\n", 1)[-1]) + 1 if breaks else column + len(raw)
        if kind != "skip":
            value = raw[1:-1].replace('""', '"') if kind == "identifier" else raw
            yield Token(kind, value, raw, start, start_line, start_column)
    yield Token("eof", "", "", i, line, column)


@dataclass
class Header:
    name: Token
    timing: str
    event: str
    table: Token
    columns: list[Token] | None


class Parser:
    def __init__(self, sql: str, tick: Callable[[], None]):
        self.stream = tokens(sql, tick)
        self.current = next(self.stream)

    def advance(self) -> Token:
        old = self.current
        self.current = next(self.stream)
        return old

    def accept(self, word: str) -> bool:
        if self.current.is_word(word):
            self.advance()
            return True
        return False

    def expect(self, word: str) -> None:
        if not self.accept(word):
            raise UnsupportedHeader("expected-" + word.lower(), self.current)

    def identifier(self) -> Token:
        if self.current.kind not in {"word", "identifier"}:
            raise UnsupportedHeader("unsupported-identifier", self.current)
        return self.advance()

    def qualified(self) -> Token:
        first = self.identifier()
        if self.current.raw == ".":
            self.advance()
            if fold(first.value) != "main":
                raise UnsupportedHeader("non-main-schema", first)
            return self.identifier()
        return first

    def parse(self) -> Header:
        self.expect("CREATE")
        if self.current.is_word("TEMP") or self.current.is_word("TEMPORARY"):
            raise UnsupportedHeader("temporary-trigger", self.current)
        self.expect("TRIGGER")
        if self.accept("IF"):
            self.expect("NOT")
            self.expect("EXISTS")
        name = self.qualified()
        timing = "BEFORE"
        if self.accept("BEFORE"):
            timing = "BEFORE"
        elif self.accept("AFTER"):
            timing = "AFTER"
        elif self.current.is_word("INSTEAD"):
            raise UnsupportedHeader("instead-of-trigger", self.current)
        event_token = self.current
        if not any(event_token.is_word(x) for x in ("UPDATE", "INSERT", "DELETE")):
            raise UnsupportedHeader("unsupported-event", event_token)
        event = self.advance().value.upper()
        columns = None
        if event == "UPDATE" and self.accept("OF"):
            columns = [self.identifier()]
            while self.current.raw == ",":
                self.advance()
                columns.append(self.identifier())
        self.expect("ON")
        table = self.qualified()
        if self.accept("FOR"):
            self.expect("EACH")
            self.expect("ROW")
        if not (self.current.is_word("WHEN") or self.current.is_word("BEGIN")):
            raise UnsupportedHeader("unsupported-header-tail", self.current)
        return Header(name, timing, event, table, columns)


def parse_header(sql: str, tick: Callable[[], None] = lambda: None) -> Header:
    return Parser(sql, tick).parse()
