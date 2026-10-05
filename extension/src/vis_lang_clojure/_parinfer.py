"""Pure Python port of the clj-parinferish lexer and repair parser.

This engine proposes structural changes. It is not a Clojure reader.
Positions use zero-based UTF-16 units, as the original engine does.
"""

from bisect import bisect_right
from dataclasses import dataclass

(
    NEWLINE,
    SPACE,
    SPECIAL,
    DELIMITER,
    STRING,
    CHARACTER,
    BACKSLASH,
    COMMENT,
    NUMBER,
    SYMBOL,
    RAW,
) = range(11)
WHITESPACE = {NEWLINE, SPACE, COMMENT, BACKSLASH}
JAVA_SPACE = " \t\n\v\f\r"
STOPS_SYMBOL = JAVA_SPACE + "[]{}()'\"`,;\\"
CLOSERS = {"(": ")", "[": "]", "{": "}", "#": "}"}


def _units(source):
    data = source.encode("utf-16-le", "surrogatepass")
    return "".join(chr(data[i] | data[i + 1] << 8) for i in range(0, len(data), 2))


def _text(units):
    return units.encode("utf-16-le", "surrogatepass").decode(
        "utf-16-le", "surrogatepass"
    )


@dataclass(frozen=True)
class Edit:
    action: str
    offset: int
    line: int
    column: int
    text: str


@dataclass(frozen=True)
class ParseResult:
    text: str
    changed: bool
    error: str | None
    edits: tuple[Edit, ...]


@dataclass(frozen=True)
class _Token:
    kind: int
    start: int
    end: int
    line: int
    column: int
    indent: int


class _Lexer:
    def __init__(self, source):
        self.source = source
        self.tokens = []
        self.error = None

    def scan(self):
        source = self.source
        length = len(source)
        pos = line = column = indent = 0
        last_kind = -1
        while pos < length:
            char = source[pos]
            end = pos + 1
            if char == "\n":
                kind = NEWLINE
                while end < length and source[end] == " ":
                    end += 1
            elif char in " \t\r,":
                kind = SPACE
                while end < length and source[end] in " \t\r,":
                    end += 1
            elif char in "'`~^@":
                kind = SPECIAL
            elif char in "[]{}()":
                kind = DELIMITER
            elif char == "#" and end < length and source[end] == "{":
                kind = DELIMITER
                end += 1
            elif char == '"':
                kind = STRING
                while end < length:
                    current = source[end]
                    if current == "\\":
                        end += 2 if end + 1 < length else 1
                    elif current == '"':
                        end += 1
                        break
                    else:
                        end += 1
            elif char == "\\" and end < length and source[end] not in JAVA_SPACE:
                kind = CHARACTER
                end += 1
            elif char == "\\":
                kind = BACKSLASH
            elif char == ";":
                kind = COMMENT
                while end < length and source[end] not in "\n\r\u0085\u2028\u2029":
                    end += 1
            else:
                end = pos + (char in "+-")
                if end < length and "0" <= source[end] <= "9":
                    kind = NUMBER
                    while end < length and "0" <= source[end] <= "9":
                        end += 1
                    if end < length and source[end] in "/.":
                        end += 1
                    while end < length and (
                        "a" <= source[end] <= "z"
                        or "A" <= source[end] <= "Z"
                        or "0" <= source[end] <= "9"
                    ):
                        end += 1
                else:
                    end = pos
                    while end < length and source[end] not in STOPS_SYMBOL:
                        end += 1
                    kind = SYMBOL if end > pos else RAW
                    end = max(end, pos + 1)
            if kind == NEWLINE:
                line += 1
                start_column = -1
                column = end - pos - 1
                indent = column
                if last_kind == BACKSLASH:
                    self.error = "Backslash at end of line"
            elif kind == STRING:
                last_newline = -1
                for index in range(pos, end):
                    if source[index] == "\n":
                        line += 1
                        last_newline = index
                start_column = column
                column = (
                    end - last_newline - 1 if last_newline >= 0 else column + end - pos
                )
                if source[end - 1] != '"':
                    self.error = "Unbalanced quote"
            else:
                start_column = column
                column += end - pos
                if kind == DELIMITER and (end - pos == 2 or char in "([{"):
                    indent = column
            self.tokens.append(_Token(kind, pos, end, line, start_column, indent))
            pos = end
            last_kind = kind


class _Parser:
    def __init__(self, lexer, mode, cursor_line, cursor_column):
        self.source = lexer.source
        self.tokens = lexer.tokens
        self.mode = mode
        self.cursor = cursor_line, cursor_column
        self.index = -1
        self.error = None
        # Each operation has kind, first value, second value, action and token index.
        self.ops = []
        self.node: _Token | None = None
        self.collection = False
        self.token_index = -1

    @property
    def current(self) -> _Token:
        """The token that the last successful `structured` call read."""
        if self.node is None:
            raise RuntimeError("The parser has not read a token yet.")
        return self.node

    def copy(self, index, action=None):
        token = self.tokens[index]
        self.ops.append(["copy", token.start, token.end, action, index])

    def rewind(self, index, count):
        self.index = index
        del self.ops[count:]

    def insert_closer(self, closer, fallback):
        index = next((op[4] for op in reversed(self.ops) if op[4] >= 0), fallback)
        self.ops.append(["char", closer, self.tokens[index].end, "insert", -1])

    def structured(self, min_indent=None, indent_change=0):
        self.index += 1
        index = self.index
        if index >= len(self.tokens):
            return False
        token = self.tokens[index]
        if (
            min_indent is not None
            and token.kind not in WHITESPACE
            and token.column < min_indent
        ):
            return False
        collection = token.kind == DELIMITER and (
            token.end - token.start == 2 or self.source[token.start] in "([{"
        )
        if collection:
            effective = self.mode
            if effective == "smart":
                effective = (
                    "indent" if (token.line, token.column) < self.cursor else "paren"
                )
            if effective == "indent":
                self.indent(index)
            elif effective == "paren":
                self.paren(index, indent_change)
            else:
                self.plain(index)
        self.node = token
        self.collection = collection
        self.token_index = index
        return True

    def indent(self, opener):
        token = self.tokens[opener]
        closer = CLOSERS[self.source[token.start]]
        indent = token.indent
        self.copy(opener)
        last_index, last_ops = self.index, len(self.ops)
        while True:
            if not self.structured(indent):
                self.rewind(last_index, last_ops)
                self.insert_closer(closer, opener)
                return
            if not self.collection and self.current.kind in WHITESPACE:
                self.copy(self.token_index)
                continue
            if self.current.indent < indent:
                self.rewind(last_index, last_ops)
                self.insert_closer(closer, opener)
                return
            if not self.collection and self.current.kind == DELIMITER:
                if self.source[self.current.start] == closer:
                    slot = len(self.ops)
                    closer_index = self.token_index
                    self.copy(closer_index)
                    if self.next_indented(indent):
                        self.ops[slot][3] = "remove"
                        index = self.ops[-1][4]
                        pos = self.tokens[index if index >= 0 else closer_index].end
                        self.ops.append(["char", closer, pos, "insert", -1])
                    return
                self.copy(self.token_index, "remove")
            elif not self.collection:
                self.copy(self.token_index)
            last_index, last_ops = self.index, len(self.ops)

    def next_indented(self, indent):
        last_index, last_ops = self.index, len(self.ops)
        newline = found = False
        while self.structured(indent):
            if not self.collection and self.current.kind == DELIMITER:
                self.copy(self.token_index, "remove")
                continue
            if not self.collection and self.current.kind in WHITESPACE:
                self.copy(self.token_index)
                newline = newline or self.current.kind == NEWLINE
                continue
            if not self.collection:
                self.copy(self.token_index)
            if newline:
                last_index, last_ops = self.index, len(self.ops)
                found = True
        self.rewind(last_index, last_ops)
        return found

    def paren(self, opener, indent_change):
        token = self.tokens[opener]
        closer = CLOSERS[self.source[token.start]]
        minimum = token.indent + indent_change
        maximum = None
        self.copy(opener)
        while True:
            if not self.structured(indent_change=indent_change):
                self.error = "EOF while reading"
                return
            token = self.current
            if not self.collection and token.kind == NEWLINE:
                current = token.indent
                change = (
                    minimum - current
                    if current < minimum
                    else (
                        maximum - current
                        if maximum is not None and current > maximum
                        else 0
                    )
                )
                if change > 0:
                    self.copy(self.token_index)
                    self.ops.append(["spaces", change, token.end, "insert", -1])
                elif change < 0:
                    self.ops.append(
                        [
                            "copy",
                            token.start,
                            token.end + change,
                            None,
                            self.token_index,
                        ]
                    )
                    self.ops.append(
                        [
                            "copy",
                            token.end + change,
                            token.end,
                            "remove",
                            self.token_index,
                        ]
                    )
                else:
                    self.copy(self.token_index)
                indent_change = change
                continue
            if self.collection:
                maximum = (
                    token.indent - 1
                    if maximum is None
                    else min(maximum, token.indent - 1)
                )
                continue
            self.copy(self.token_index)
            if token.kind == DELIMITER:
                if self.source[token.start] != closer:
                    self.error = "Unmatched delimiter"
                return

    def plain(self, opener):
        closer = CLOSERS[self.source[self.tokens[opener].start]]
        self.copy(opener)
        while True:
            if not self.structured():
                self.error = "EOF while reading"
                return
            if self.collection:
                continue
            self.copy(self.token_index)
            if self.current.kind == DELIMITER:
                if self.source[self.current.start] != closer:
                    self.error = "Unmatched delimiter"
                return

    def run(self):
        while self.structured():
            if self.collection:
                continue
            stray = (
                self.current.kind == DELIMITER
                and self.source[self.current.start] in ")]}"
            )
            if stray and self.mode in ("indent", "smart"):
                self.copy(self.token_index, "remove")
            else:
                self.copy(self.token_index)
                if stray:
                    self.error = "Unmatched delimiter"

    def result(self, disabled):
        parts, edits = [], []
        starts = [0] + [i + 1 for i, char in enumerate(self.source) if char == "\n"]
        for kind, first, second, action, _ in self.ops:
            text = (
                self.source[first:second]
                if kind == "copy"
                else (first if kind == "char" else " " * first)
            )
            if action != ("insert" if disabled else "remove"):
                parts.append(text)
            if not disabled and action is not None:
                offset = first if action == "remove" else second
                line = bisect_right(starts, offset) - 1
                edits.append(
                    Edit(action, offset, line, offset - starts[line], _text(text))
                )
        return _text("".join(parts)), tuple(edits)


def parse(source, *, mode=None, cursor_line=0, cursor_column=0):
    """Propose a repair in indent, paren or smart mode; None only checks balance."""
    if not isinstance(source, str):
        raise TypeError("source must be a string")
    if mode not in (None, "indent", "paren", "smart"):
        raise ValueError(f"Unknown parinfer mode: {mode!r}")
    lexer = _Lexer(_units(source))
    lexer.scan()
    effective = None if lexer.error else mode
    parser = _Parser(lexer, effective, cursor_line, cursor_column)
    parser.run()
    error = lexer.error or parser.error
    text, edits = parser.result(effective == "paren" and error is not None)
    return ParseResult(text, text != source, error, edits)
