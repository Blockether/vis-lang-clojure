"""Whether Clojure source reads, decided without a JVM.

This module ports the verdict of Clojure 1.12's `LispReader`, read with the
settings that a static check needs. Nothing is evaluated or loaded:

- `*read-eval*` is off, so `#=` and a record literal such as `#my.Rec{}` are
  reported instead of evaluated.
- An alias in `::alias/k`, `#::alias{}` or a syntax-quoted symbol reads as
  written, because the file's `ns` form is never loaded.
- An unknown tagged literal reads as a tagged-literal value. `#inst` and `#uuid`
  keep their own readers, which refuse a malformed value.
- Reader conditionals are allowed in every file, with the `:clj` platform.
  Branches for other platforms are still read, so their delimiters, strings and
  literals are checked too.

A regex literal compiles as a Java pattern, so a pattern that only JavaScript
accepts is reported even in a `.cljs` file. Line and column follow the reader:
columns count UTF-16 code units, as Java does.

Some message text can differ from a JVM. A printed set or large map keeps its
source order, where a JVM prints hash order. Gensym numbers, the hash of a
pattern, class-loader details and the time zone of a date also differ.

A syntax-quote expands each nested syntax-quote again, so its work grows
exponentially with nesting. A check stops after `_QUOTE_BUDGET` steps for one
outermost syntax-quote and gives no verdict, where a JVM reads for a long time.
"""

import bisect
import datetime
import itertools
import math
import re
import struct
import sys
import threading
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from . import _jregex
from ._parinfer import _text, _units

_RUNTIME = "java.lang.RuntimeException"
_NUMBER_FORMAT = "java.lang.NumberFormatException"
_ILLEGAL_ARGUMENT = "java.lang.IllegalArgumentException"
_ILLEGAL_STATE = "java.lang.IllegalStateException"
_ARITHMETIC = "java.lang.ArithmeticException"
_INDEX = "java.lang.ArrayIndexOutOfBoundsException"
_PATTERN = "java.util.regex.PatternSyntaxException"
_CLASS_CAST = "java.lang.ClassCastException"
_NULL_POINTER = "java.lang.NullPointerException"
_READER = "clojure.lang.LispReader$ReaderException"

_JAVA_SPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2008\u2009\u200a\u2028\u2029\u205f\u3000"
)
_SPACE = frozenset(_JAVA_SPACE + ",")
_MACROS = frozenset("\"';@^`~()[]{}\\%#")
_TERMINATING = frozenset('";@^`~()[]{}\\')


def _class_of(chars):
    return re.compile("[" + re.escape("".join(sorted(chars))) + "]")


_TOKEN_END = _class_of(_SPACE | _TERMINATING)
_NUMBER_END = _class_of(_SPACE | _MACROS)
_NOT_SPACE = re.compile("[^" + re.escape("".join(sorted(_SPACE))) + "]")
_STRING_STOP = re.compile(r'["\\]')
_NEWLINE = re.compile("\n")
_SURROGATE = re.compile("[\ud800-\udfff]")
_SIMPLE_ESCAPES = {
    "t": "\t",
    "r": "\r",
    "n": "\n",
    "\\": "\\",
    '"': '"',
    "b": "\b",
    "f": "\f",
}
_LINE_BREAKS = re.compile("\r?\n")

# Java's `.` stops at these line terminators; Python's `.` stops only at \n.
_DOT = "[^\n\r\u0085\u2028\u2029]"
_SYMBOL = re.compile("[:]?([^0-9/]" + _DOT + "*/)?(/|[^0-9/][^/]*)")
_ARRAY_SYMBOL = re.compile("([^0-9/:]" + _DOT + "*)/([1-9])")
_INT = re.compile(
    "([-+]?)(?:(0)|([1-9][0-9]*)|0[xX]([0-9A-Fa-f]+)|0([0-7]+)"
    "|([1-9][0-9]?)[rR]([0-9A-Za-z]+)|0[0-9]+)(N)?"
)
_RATIO = re.compile("([-+]?[0-9]+)/([0-9]+)")
_FLOAT = re.compile(r"([-+]?[0-9]+(\.[0-9]*)?([eE][-+]?[0-9]+)?)(M)?")
_ARG = re.compile("%(?:(&)|([1-9][0-9]*))?")
_TIMESTAMP = re.compile(
    "([0-9]{4})(?:-([0-9]{2})(?:-([0-9]{2})(?:[T]([0-9]{2})(?::([0-9]{2})"
    "(?::([0-9]{2})(?:[.]([0-9]+))?)?)?)?)?)?(?:[Z]|([-+])([0-9]{2}):([0-9]{2}))?"
)
_HEX = frozenset("0123456789abcdefABCDEF")
_DIGITS_PER_INT = (0, 0, 30, 19, 15, 13, 11, 11, 10, 9, 9, 8, 8, 8, 8, 7, 7, 7, 7)
_DIGITS_PER_INT += (7, 7, 7, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 5)
_INT_MAX = 0x7FFFFFFF
_LONG_MAX = 0x7FFFFFFFFFFFFFFF


@dataclass(frozen=True)
class Problem:
    """Where source stops reading: 1-based line and column, and the reason."""

    line: int
    column: int
    message: str


class _Thrown(Exception):
    """A Java exception from the reader: its class name and message."""

    def __init__(self, cls, message, line=None, column=None, cause=None):
        super().__init__(message)
        self.cls = cls
        self.message = message
        self.line = line
        self.column = column
        self.cause = cause

    def __str__(self):
        return self.cls if self.message is None else f"{self.cls}: {self.message}"


class _NoVerdict(Exception):
    """The source needs more work than a check allows, so it gets no verdict."""


class _Marker:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


_NOOP = _Marker("noop")
_READ_EOF = _Marker("eof")
_READ_FINISHED = _Marker("finished")
_READ_STARTED = _Marker("started")


class Sym:
    __slots__ = ("ns", "name", "meta")

    def __init__(self, ns, name, meta=None):
        self.ns = ns
        self.name = name
        self.meta = meta

    def __str__(self):
        return self.name if self.ns is None else f"{self.ns}/{self.name}"


class Kw:
    __slots__ = ("ns", "name")

    def __init__(self, ns, name):
        self.ns = ns
        self.name = name

    def __str__(self):
        return ":" + (self.name if self.ns is None else f"{self.ns}/{self.name}")


class Char:
    __slots__ = ("unit",)

    def __init__(self, unit):
        self.unit = unit


class Num:
    """A number read as Clojure does: kind is int, double, ratio or decimal."""

    __slots__ = ("kind", "value", "big")

    def __init__(self, kind, value, big=False):
        self.kind = kind
        self.value = value
        self.big = big


class Coll:
    """A list, vector, map or set. A map holds its entries as key-value pairs."""

    __slots__ = ("kind", "items", "meta", "cons")

    def __init__(self, kind, items, meta=None, cons=False):
        self.kind = kind
        self.items = items
        self.meta = meta
        self.cons = cons


class Tagged:
    __slots__ = ("tag", "form")

    def __init__(self, tag, form):
        self.tag = tag
        self.form = form


class Regex:
    __slots__ = ("pattern",)

    def __init__(self, pattern):
        self.pattern = pattern


class Inst:
    __slots__ = ("millis",)

    def __init__(self, millis):
        self.millis = millis


class Uuid:
    __slots__ = ("bits",)

    def __init__(self, bits):
        self.bits = bits


def _sym_intern(text):
    i = text.find("/")
    if i == -1 or text == "/":
        return Sym(None, text)
    return Sym(text[:i], text[i + 1 :])


def _list(*items):
    """Clojure's `RT.list`, which builds a `clojure.lang.Cons` from two or more items."""
    return Coll("list", list(items), cons=len(items) > 1)


_QUOTE = Sym(None, "quote")
_THE_VAR = Sym(None, "var")
_FN = Sym(None, "fn*")
_AMP = Sym(None, "&")
_UNQUOTE = Sym("clojure.core", "unquote")
_UNQUOTE_SPLICING = Sym("clojure.core", "unquote-splicing")
_CONCAT = Sym("clojure.core", "concat")
_SEQ = Sym("clojure.core", "seq")
_LIST = Sym("clojure.core", "list")
_APPLY = Sym("clojure.core", "apply")
_HASHMAP = Sym("clojure.core", "hash-map")
_HASHSET = Sym("clojure.core", "hash-set")
_VECTOR = Sym("clojure.core", "vector")
_WITH_META = Sym("clojure.core", "with-meta")
_DEREF = Sym("clojure.core", "deref")
_TAG = Kw(None, "tag")
_PARAM_TAGS = Kw(None, "param-tags")
_SPECIALS = frozenset(
    "def loop* recur if case* let* letfn* do fn* quote var . set! deftype* reify*"
    " try throw monitor-enter monitor-exit catch finally new &".split()
)
# One value for each name, as in the reader's own map: two `##NaN` are one key.
_SYMBOLIC = {
    "Inf": Num("double", math.inf),
    "-Inf": Num("double", -math.inf),
    "NaN": Num("double", math.nan),
}


def _is_digit(ch):
    return "0" <= ch <= "9" or (ch > "\x7f" and unicodedata.category(ch) == "Nd")


def _digit(ch, radix):
    """Java's `Character.digit`: the value of `ch` in `radix`, or -1."""
    if not ch:
        return -1
    code = ord(ch)
    if 48 <= code <= 57:
        value = code - 48
    elif 65 <= code <= 90:
        value = code - 55
    elif 97 <= code <= 122:
        value = code - 87
    elif 0xFF21 <= code <= 0xFF3A:
        value = code - 0xFF21 + 10
    elif 0xFF41 <= code <= 0xFF5A:
        value = code - 0xFF41 + 10
    elif code > 0x7F and unicodedata.category(ch) == "Nd":
        value = unicodedata.decimal(ch, -1)
    else:
        return -1
    return value if 0 <= value < radix else -1


def _bit_length(value):
    return value.bit_length() if value >= 0 else (~value).bit_length()


def _input_string(text, radix):
    suffix = "" if radix == 10 else f" under radix {radix}"
    return _Thrown(_NUMBER_FORMAT, f'For input string: "{text}"{suffix}')


def _parse_int(text):
    """Java's `Integer.parseInt` for a token of ASCII digits."""
    value = int(text)
    if value > _INT_MAX:
        raise _input_string(text, 10)
    return value


def _big_integer(text, radix):
    """Java's `new BigInteger(text, radix)` for a token without a sign."""
    if radix < 2 or radix > 36:
        raise _Thrown(_NUMBER_FORMAT, "Radix out of range")
    cursor = 0
    while cursor < len(text) and _digit(text[cursor], radix) == 0:
        cursor += 1
    if cursor == len(text):
        return 0
    size = _DIGITS_PER_INT[radix]
    first = (len(text) - cursor) % size or size
    starts = [cursor, *range(cursor + first, len(text), size)]
    for start in starts:
        group = text[start : first + cursor if start == cursor else start + size]
        if any(_digit(char, radix) < 0 for char in group):
            raise _input_string(group, radix)
    return int(text[cursor:], radix)


def _integer(value, big=False):
    return Num("int", value, big or _bit_length(value) >= 64)


def _match_number(text):
    m = _INT.fullmatch(text)
    if m:
        if m.group(2) is not None:
            return _integer(0, m.group(8) is not None)
        if m.group(3) is not None:
            digits, radix = m.group(3), 10
        elif m.group(4) is not None:
            digits, radix = m.group(4), 16
        elif m.group(5) is not None:
            digits, radix = m.group(5), 8
        elif m.group(7) is not None:
            digits, radix = m.group(7), int(m.group(6))
        else:
            return None
        value = _big_integer(digits, radix)
        if m.group(1) == "-":
            value = -value
        return _integer(value, m.group(8) is not None)
    m = _FLOAT.fullmatch(text)
    if m:
        if m.group(4) is not None:
            return Num("decimal", Decimal(m.group(1)))
        return Num("double", float(text))
    m = _RATIO.fullmatch(text)
    if m:
        numerator, denominator = int(m.group(1)), int(m.group(2))
        if denominator == 0:
            raise _Thrown(_ARITHMETIC, "Divide by zero")
        value = Fraction(numerator, denominator)
        big = _bit_length(numerator) >= 64 or _bit_length(denominator) >= 64
        if value.denominator == 1:
            return _integer(value.numerator, big)
        return Num("ratio", value, big)
    return None


def _match_symbol(text, resolver):
    m = _SYMBOL.fullmatch(text)
    if m:
        ns, name = m.group(1), m.group(2)
        if (ns is not None and ns.endswith(":/")) or name.endswith(":"):
            return None
        if text.find("::", 1) != -1:
            return None
        if text.startswith("::"):
            ks = _sym_intern(text[2:])
            if ks.ns is not None:
                # The resolver reads an alias as itself. Without the resolver the
                # current namespace, user, has no alias to resolve it with.
                return Kw(ks.ns, ks.name) if resolver else None
            return Kw("user", ks.name)
        if text.startswith(":"):
            sym = _sym_intern(text[1:])
            return Kw(sym.ns, sym.name)
        return _sym_intern(text)
    m = _ARRAY_SYMBOL.fullmatch(text)
    if m:
        return Sym(m.group(1), m.group(2))
    return None


def _interpret_token(text, resolver):
    if text == "nil":
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    found = _match_symbol(text, resolver)
    if found is not None:
        return found
    raise _Thrown(_RUNTIME, "Invalid token: " + text)


def _double_text(value):
    """Java's `Double.toString`."""
    if value != value:
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    sign = "-" if value < 0 else ""
    size = abs(value)
    exact = Decimal(repr(size))
    digits = "".join(map(str, exact.as_tuple().digits)).rstrip("0") or "0"
    point = exact.adjusted() + 1
    if 1e-3 <= size < 1e7:
        if point <= 0:
            return f"{sign}0.{'0' * -point}{digits}"
        whole = digits[:point].ljust(point, "0")
        return f"{sign}{whole}.{digits[point:] or '0'}"
    return f"{sign}{digits[0]}.{digits[1:] or '0'}E{point - 1}"


def _decimal_text(value):
    text = str(value)
    return text[1:] if value == 0 and text.startswith("-") else text


def _number_text(number, readably):
    kind, value = number.kind, number.value
    if kind == "int":
        return f"{value}N" if readably and number.big else str(value)
    if kind == "double":
        if readably and (math.isinf(value) or value != value):
            return "##NaN" if value != value else ("##Inf" if value > 0 else "##-Inf")
        return _double_text(value)
    if kind == "ratio":
        return f"{value.numerator}/{value.denominator}"
    return _decimal_text(value) + ("M" if readably else "")


_CHAR_NAMES = {
    "\n": "newline",
    "\t": "tab",
    " ": "space",
    "\b": "backspace",
    "\f": "formfeed",
    "\r": "return",
}
_STRING_ESCAPES = {
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    '"': '\\"',
    "\\": "\\\\",
    "\f": "\\f",
    "\b": "\\b",
}


def _uuid_text(bits):
    text = f"{bits:032x}"
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}"


def _pr(value):
    """Clojure's `pr-str`, for a value inside a printed collection."""
    if value is None:
        return "nil"
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + "".join(_STRING_ESCAPES.get(c, c) for c in value) + '"'
    if isinstance(value, Char):
        name = _CHAR_NAMES.get(value.unit)
        return "\\" + (name or _text(value.unit))
    if isinstance(value, Num):
        return _number_text(value, True)
    if isinstance(value, Coll):
        if value.kind == "map":
            body = ", ".join([f"{_pr(k)} {_pr(v)}" for k, v in value.items])
            return "{" + body + "}"
        body = " ".join([_pr(item) for item in value.items])
        if value.kind == "list":
            return "(" + body + ")"
        if value.kind == "vector":
            return "[" + body + "]"
        return "#{" + body + "}"
    if isinstance(value, Tagged):
        return f"#{_pr(value.tag)} {_pr(value.form)}"
    if isinstance(value, Regex):
        return '#"' + value.pattern + '"'
    if isinstance(value, Uuid):
        return f'#uuid "{_uuid_text(value.bits)}"'
    if isinstance(value, Inst):
        return f'#inst "{_inst_text(value.millis)}"'
    return str(value)


def _to_string(value):
    """Java's `String.valueOf`, as a reader message shows a value."""
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    if isinstance(value, Char):
        return _text(value.unit)
    if isinstance(value, Num):
        return _number_text(value, False)
    if isinstance(value, Regex):
        return value.pattern
    if isinstance(value, Uuid):
        return _uuid_text(value.bits)
    if isinstance(value, Inst):
        return _date_text(value.millis)
    if isinstance(value, Tagged):
        return f"clojure.lang.TaggedLiteral@{_java_hash(value) & 0xFFFFFFFF:x}"
    return _pr(value)


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def _date_text(millis):
    """Java's `Date.toString`, in the time zone of this process."""
    try:
        moment = datetime.datetime.fromtimestamp(millis / 1000).astimezone()
    except (OverflowError, OSError, ValueError):
        epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        moment = epoch + datetime.timedelta(milliseconds=millis)
    clock = f"{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    day = f"{_WEEKDAYS[moment.weekday()]} {_MONTHS[moment.month - 1]} {moment.day:02d}"
    return f"{day} {clock} {moment.tzname()} {moment.year}"


def _java_class(value):
    if value is True or value is False:
        return "java.lang.Boolean"
    if isinstance(value, str):
        return "java.lang.String"
    if isinstance(value, Num):
        if value.kind == "int":
            return "clojure.lang.BigInt" if value.big else "java.lang.Long"
        return {
            "double": "java.lang.Double",
            "ratio": "clojure.lang.Ratio",
            "decimal": "java.math.BigDecimal",
        }[value.kind]
    if isinstance(value, Coll):
        if value.kind == "list":
            if value.cons:
                return "clojure.lang.Cons"
            return "clojure.lang.PersistentList" + ("" if value.items else "$EmptyList")
        if value.kind == "map":
            many = len(value.items) > 8
            return "clojure.lang.Persistent" + ("HashMap" if many else "ArrayMap")
        return "clojure.lang.Persistent" + (
            "Vector" if value.kind == "vector" else "HashSet"
        )
    return {
        Char: "java.lang.Character",
        Kw: "clojure.lang.Keyword",
        Sym: "clojure.lang.Symbol",
        Tagged: "clojure.lang.TaggedLiteral",
        Regex: "java.util.regex.Pattern",
        Inst: "java.util.Date",
        Uuid: "java.util.UUID",
    }[type(value)]


def _num_equal(a, b):
    if a.kind != b.kind:
        return False
    return a.value == b.value


def _seq_equiv(items, other):
    if len(items) != len(other):
        return False
    for x, y in zip(items, other, strict=True):
        if not _equiv(x, y):
            return False
    return True


def _map_get(pairs, key):
    for k, v in pairs:
        if _equal_key(k, key):
            return True, v
    return False, None


def _coll_equiv(coll, other):
    if not isinstance(other, Coll):
        return False
    if coll.kind in ("list", "vector"):
        return other.kind in ("list", "vector") and _seq_equiv(coll.items, other.items)
    if coll.kind != other.kind or len(coll.items) != len(other.items):
        return False
    if coll.kind == "map":
        for key, value in coll.items:
            found, other_value = _map_get(other.items, key)
            if not found or not _equiv(value, other_value):
                return False
        return True
    for y in other.items:
        for x in coll.items:
            if _equiv(x, y):
                break
        else:
            return False
    return True


def _equals(a, b):
    """Java's `equals` for values that are neither numbers nor collections."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (Sym, Kw)):
        return a.ns == b.ns and a.name == b.name
    if isinstance(a, Char):
        return a.unit == b.unit
    if isinstance(a, Tagged):
        return _equals(a.tag, b.tag) and _strict_equals(a.form, b.form)
    if isinstance(a, Inst):
        return a.millis == b.millis
    if isinstance(a, Uuid):
        return a.bits == b.bits
    if isinstance(a, Regex):
        return a is b
    return a == b


def _strict_equals(a, b):
    """Java's `equals`, where 1 and 1N or 1.0M and 1.00M differ."""
    if a is b:
        return True
    if isinstance(a, Num) and isinstance(b, Num):
        if a.kind != b.kind or (a.kind == "int" and a.big != b.big):
            return False
        if a.kind == "double":
            return (
                a.value == b.value
                and math.copysign(1, a.value) == math.copysign(1, b.value)
                or (a.value != a.value and b.value != b.value)
            )
        if a.kind == "decimal":
            return a.value.as_tuple() == b.value.as_tuple()
        return a.value == b.value
    if isinstance(a, Coll) and isinstance(b, Coll):
        if a.kind in ("list", "vector") and b.kind in ("list", "vector"):
            if len(a.items) != len(b.items):
                return False
            for x, y in zip(a.items, b.items, strict=True):
                if not _strict_equals(x, y):
                    return False
            return True
        return _coll_equiv(a, b)
    if (
        a is None
        or b is None
        or isinstance(a, (Num, Coll))
        or isinstance(b, (Num, Coll))
    ):
        return False
    return _equals(a, b)


def _equiv(a, b):
    """Clojure's `Util.equiv`."""
    if a is b:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, Num) and isinstance(b, Num):
        return _num_equal(a, b)
    if isinstance(a, Coll):
        return _coll_equiv(a, b)
    if isinstance(b, Coll):
        return _coll_equiv(b, a)
    if isinstance(a, Num) or isinstance(b, Num):
        return False
    return _equals(a, b)


def _equal_key(a, b):
    if isinstance(a, Kw):
        return isinstance(b, Kw) and a.ns == b.ns and a.name == b.name
    return _equiv(a, b)


def _hasheq(value):
    """A hash that agrees with `_equiv`: equivalent values hash alike."""
    if value is None:
        return 0
    if value is True or value is False:
        return hash(("boolean", value))
    if isinstance(value, Num):
        return hash(value.value) if value.value == value.value else id(value)
    if isinstance(value, (Sym, Kw)):
        return hash((type(value).__name__, value.ns, value.name))
    if isinstance(value, Char):
        return hash(("char", value.unit))
    if isinstance(value, Coll):
        if value.kind in ("list", "vector"):
            return hash(("seq", *[_hasheq(item) for item in value.items]))
        if value.kind == "map":
            return sum([hash((_hasheq(k), _hasheq(v))) for k, v in value.items])
        return sum([_hasheq(item) for item in value.items])
    if isinstance(value, Tagged):
        return hash(("tagged", _hasheq(value.tag)))
    if isinstance(value, Inst):
        return hash(value.millis)
    if isinstance(value, Uuid):
        return hash(value.bits)
    if isinstance(value, str):
        return hash(value)
    return id(value)


def _i32(value):
    """Java's `(int)` cast: the low 32 bits as a signed number."""
    value &= 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def _string_hash(text):
    """Java's `String.hashCode`, over UTF-16 code units."""
    code = 0
    data = text.encode("utf-16-be", "surrogatepass")
    for i in range(0, len(data), 2):
        code = _i32(31 * code + (data[i] << 8 | data[i + 1]))
    return code


def _symbol_hash(ns, name):
    """`Symbol.hashCode`: `Util.hashCombine` of the name and namespace hashes."""
    seed = _string_hash(name)
    other = 0 if ns is None else _string_hash(ns)
    return _i32(seed ^ (other + 0x9E3779B9 + (seed << 6) + (seed >> 2)))


def _long_hash(value):
    """`Long.hashCode`, which a `BigInt` in the long range also answers."""
    bits = value & 0xFFFFFFFFFFFFFFFF
    return _i32(bits ^ (bits >> 32))


def _big_hash(value):
    """`BigInteger.hashCode`, over the 32-bit words of the magnitude."""
    code, magnitude = 0, abs(value)
    for shift in range((magnitude.bit_length() + 31) // 32 * 32 - 32, -1, -32):
        code = _i32(31 * code + (magnitude >> shift & 0xFFFFFFFF))
    return _i32(code * ((value > 0) - (value < 0)))


def _number_hash(number):
    """The `hashCode` of a number as the reader builds it."""
    if number.kind == "int":
        if -(1 << 63) <= number.value < 1 << 63:
            return _long_hash(number.value)
        return _big_hash(number.value)
    if number.kind == "double":
        value = number.value
        if value != value:
            bits = 0x7FF8000000000000
        else:
            bits = struct.unpack(">Q", struct.pack(">d", value))[0]
        return _i32(bits ^ (bits >> 32))
    if number.kind == "ratio":
        return _big_hash(number.value.numerator) ^ _big_hash(number.value.denominator)
    sign, digits, exponent = number.value.as_tuple()
    return _i32(31 * _big_hash(int(Decimal((sign, digits, 0)))) - exponent)


def _java_hash(value):
    """Java's `hashCode`, which `TaggedLiteral.toString` shows in hexadecimal.

    A pattern has no stable hash on a JVM, so it answers its identity here too.
    """
    if value is None:
        return 0
    if value is True or value is False:
        return 1231 if value else 1237
    if isinstance(value, str):
        return _string_hash(value)
    if isinstance(value, Char):
        return ord(value.unit)
    if isinstance(value, Sym):
        return _symbol_hash(value.ns, value.name)
    if isinstance(value, Kw):
        return _i32(_symbol_hash(value.ns, value.name) + 0x9E3779B9)
    if isinstance(value, Num):
        return _number_hash(value)
    if isinstance(value, Coll):
        if value.kind in ("list", "vector"):
            code = 1
            for item in value.items:
                code = _i32(31 * code + _java_hash(item))
            return code
        if value.kind == "map":
            return _i32(sum([_java_hash(k) ^ _java_hash(v) for k, v in value.items]))
        return _i32(sum([_java_hash(item) for item in value.items]))
    if isinstance(value, Tagged):
        return _i32(31 * _java_hash(value.tag) + _java_hash(value.form))
    if isinstance(value, Inst):
        return _i32(value.millis ^ (value.millis >> 32))
    if isinstance(value, Uuid):
        mixed = (value.bits >> 64) ^ value.bits
        return _i32((mixed >> 32) ^ mixed)
    return _i32(id(value))


def _duplicate(key):
    return _Thrown(_ILLEGAL_ARGUMENT, "Duplicate key: " + _to_string(key))


def _check_unique(keys):
    """A hash map or hash set adds keys in order and names the first repeat."""
    buckets = {}
    for key in keys:
        bucket = buckets.setdefault(_hasheq(key), [])
        for other in bucket:
            if _equiv(other, key):
                raise _duplicate(key)
        bucket.append(key)


def _rt_map(flat):
    """`RT.map`: an array map up to 8 entries names the earlier duplicate key."""
    if len(flat) <= 16:
        for i in range(0, len(flat), 2):
            for j in range(i + 2, len(flat), 2):
                if _equal_key(flat[i], flat[j]):
                    raise _duplicate(flat[i])
    else:
        _check_unique(flat[0::2])
    return Coll("map", list(zip(flat[0::2], flat[1::2], strict=True)))


def _ensure(pending):
    return [] if pending is None else pending


def _is_seq_of(form, head):
    if not isinstance(form, Coll) or form.kind != "list" or not form.items:
        return False
    first = form.items[0]
    return isinstance(first, Sym) and first.ns == head.ns and first.name == head.name


def _second(form):
    return form.items[1] if len(form.items) > 1 else None


def _with_meta(target, meta):
    merged = list(target.meta or [])
    for key, value in meta:
        merged = [(k, v) for k, v in merged if not _equal_key(k, key)]
        merged.append((key, value))
    if isinstance(target, Sym):
        return Sym(target.ns, target.name, merged)
    return Coll(target.kind, target.items, merged, target.cons)


def _days_from_civil(year, month, day):
    year -= month <= 2
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


# Days in each month of a common year, by month number. Index 0 is not a month.
_MONTH_DAYS = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_GREGORIAN_START = _days_from_civil(1582, 10, 15)


def _julian_days(year, month, day):
    """Days from 1970-01-01 to a date of the Julian calendar."""
    shifted = year + 4800 - (month <= 2)
    march = month + (9 if month <= 2 else -3)
    return day + (153 * march + 2) // 5 + 365 * shifted + shifted // 4 - 32083 - 2440588


def _calendar_day(year, month, day):
    """The day that Java's `GregorianCalendar` gives a date: Julian before 1582-10-15."""
    day_number = _days_from_civil(year, month, day)
    return (
        day_number if day_number >= _GREGORIAN_START else _julian_days(year, month, day)
    )


def _civil_date(days):
    """The year, month and day that `GregorianCalendar` gives `days` after 1970-01-01."""
    number = days + 2440588
    if days >= _GREGORIAN_START:
        shifted = number + 32044
        century = (4 * shifted + 3) // 146097
        rest = shifted - 146097 * century // 4
    else:
        century, rest = 0, number + 32082
    years = (4 * rest + 3) // 1461
    day_of_year = rest - 1461 * years // 4
    march = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * march + 2) // 5 + 1
    return (
        100 * century + years - 4800 + march // 10,
        march + 3 - 12 * (march // 10),
        day,
    )


def _inst_text(millis):
    """How Clojure prints an `#inst` value: in UTC, with a Julian date before 1582-10-15."""
    days, rest = divmod(millis, 86_400_000)
    year, month, day = _civil_date(days)
    seconds, milli = divmod(rest, 1000)
    hour, seconds = divmod(seconds, 3600)
    minute, second = divmod(seconds, 60)
    era_year = year if year > 0 else 1 - year
    return (
        f"{era_year:04d}-{month:02d}-{day:02d}"
        f"T{hour:02d}:{minute:02d}:{second:02d}.{milli:03d}-00:00"
    )


def _read_inst(form):
    """`clojure.instant/read-instant-date`, which validates every field."""
    if form is None:
        raise _Thrown(
            _NULL_POINTER,
            'Cannot invoke "java.lang.CharSequence.length()" because "this.text" is null',
        )
    if not isinstance(form, str):
        cls = _java_class(form)
        raise _Thrown(
            _CLASS_CAST, f"class {cls} cannot be cast to class java.lang.CharSequence"
        )
    m = _TIMESTAMP.fullmatch(form)
    if not m:
        raise _Thrown(_RUNTIME, "Unrecognized date/time syntax: " + form)
    years = int(m.group(1))
    months, days, hours, minutes, seconds = (
        int(m.group(i)) if m.group(i) else default
        for i, default in ((2, 1), (3, 1), (4, 0), (5, 0), (6, 0))
    )
    fraction = m.group(7)
    nanos = int(fraction[:9].ljust(9, "0")) if fraction else 0
    sign = {"-": -1, "+": 1}.get(m.group(8), 0)
    offset_hours = int(m.group(9)) if m.group(9) else 0
    offset_minutes = int(m.group(10)) if m.group(10) else 0
    days_in_month = 29 if months == 2 and _leap(years) else None
    checks = (
        (1 <= months <= 12, "(<= 1 months 12)"),
        (
            lambda: 1 <= days <= (days_in_month or _MONTH_DAYS[months]),
            "(<= 1 days (days-in-month months (leap-year? years)))",
        ),
        (0 <= hours <= 23, "(<= 0 hours 23)"),
        (0 <= minutes <= 59, "(<= 0 minutes 59)"),
        (
            0 <= seconds <= (60 if minutes == 59 else 59),
            "(<= 0 seconds (if (= minutes 59) 60 59))",
        ),
        (0 <= nanos <= 999999999, "(<= 0 nanoseconds 999999999)"),
        (True, "(<= -1 offset-sign 1)"),
        (0 <= offset_hours <= 23, "(<= 0 offset-hours 23)"),
        (0 <= offset_minutes <= 59, "(<= 0 offset-minutes 59)"),
    )
    for passed, test in checks:
        if not (passed() if callable(passed) else passed):
            raise _Thrown(_RUNTIME, "failed: " + test)
    day = _calendar_day(years, months, days)
    local = ((day * 24 + hours) * 60 + minutes) * 60 + seconds
    offset = sign * (offset_hours * 60 + offset_minutes) * 60
    return Inst((local - offset) * 1000 + nanos // 1_000_000)


def _parse_long(text, begin, end, radix):
    """Java's `Long.parseLong(CharSequence, begin, end, radix)` of JDK 25."""
    if begin == end:
        under = "" if radix == 10 else f" under radix {radix}"
        raise _Thrown(_NUMBER_FORMAT, f'For input string: ""{under}')
    sign = digit = ~0xFF
    first, i = text[begin], begin + 1
    if first not in "-+":
        digit = _digit(first, radix)
    if digit >= 0 or digit == sign and end - begin > 1:
        limit = -_LONG_MAX if first != "-" else -_LONG_MAX - 1
        multmin = -(-limit // radix)
        result = -(digit & 0xFF)
        in_range = True
        while i < end:
            digit = _digit(text[i], radix)
            i += 1
            if digit < 0:
                break
            in_range = result > multmin or (
                result == multmin and digit <= radix * multmin - limit
            )
            if not in_range:
                break
            result = radix * result - digit
        if in_range and i == end and digit >= 0:
            return -result if first != "-" else result
    index = i - (0 if digit < -1 else 1) - begin
    raise _Thrown(_NUMBER_FORMAT, f'Error at index {index} in: "{text[begin:end]}"')


def _read_uuid(form):
    """`clojure.uuid/default-uuid-reader`, through `UUID.fromString`."""
    if not isinstance(form, str):
        raise _Thrown(_ILLEGAL_ARGUMENT, "#uuid data reader expected string")
    name = form
    if (
        len(name) == 36
        and name[8] == name[13] == name[18] == name[23] == "-"
        and all(name[i] in _HEX for i in range(36) if i not in (8, 13, 18, 23))
    ):
        return Uuid(int(name.replace("-", ""), 16))
    if len(name) > 36:
        raise _Thrown(_ILLEGAL_ARGUMENT, "UUID string too large")
    dash1 = name.find("-", 0)
    dash2 = name.find("-", dash1 + 1)
    dash3 = name.find("-", dash2 + 1)
    dash4 = name.find("-", dash3 + 1)
    dash5 = name.find("-", dash4 + 1)
    if dash4 < 0 or dash5 >= 0:
        raise _Thrown(_ILLEGAL_ARGUMENT, "Invalid UUID string: " + name)
    most = _parse_long(name, 0, dash1, 16) & 0xFFFFFFFF
    most = (most << 16) | (_parse_long(name, dash1 + 1, dash2, 16) & 0xFFFF)
    most = (most << 16) | (_parse_long(name, dash2 + 1, dash3, 16) & 0xFFFF)
    least = (_parse_long(name, dash3 + 1, dash4, 16) & 0xFFFF) << 48
    least |= _parse_long(name, dash4 + 1, len(name), 16) & 0xFFFFFFFFFFFF
    return Uuid(((most & 0xFFFFFFFFFFFFFFFF) << 64) | least)


class _Reader:
    """`LispReader` over a `LineNumberingPushbackReader` of UTF-16 code units."""

    def __init__(self, units):
        self.s = units
        self.n = len(units)
        self.i = 0
        self.pushed = False
        self.col = 1
        self.eof = False
        self.newlines = [m.start() for m in _NEWLINE.finditer(units)]
        self.ends_open = bool(units) and units[-1] != "\n"
        self.astral = _SURROGATE.search(units) is not None
        self.arg_env = None
        self.gensyms = None
        self.quote_steps = 0
        self.suppress = False
        self.ids = itertools.count(1)

    def line(self):
        # A pushed-back newline was already counted; end of input ends a last line.
        line = 1 + bisect.bisect_left(self.newlines, self.i + self.pushed)
        return line + (self.eof and self.ends_open)

    def text(self, units):
        return _text(units) if self.astral else units

    def read1(self):
        i = self.i
        if i < self.n:
            ch = self.s[i]
            self.i = i + 1
            self.pushed = False
            self.col = 1 if ch == "\n" else self.col + 1
            return ch
        self.eof = True
        self.col = 1
        return ""

    def unread(self, ch):
        if ch:
            self.i -= 1
            self.pushed = True
            self.col -= 1

    def skip_to(self, pattern):
        """Read every unit before the next match of `pattern`, and return them."""
        start = self.i
        m = pattern.search(self.s, start)
        end = m.start() if m else self.n
        if end == start:
            return ""
        chunk = self.s[start:end]
        last = chunk.rfind("\n")
        self.col = self.col + (end - start) if last < 0 else end - start - last
        self.i = end
        self.pushed = False
        return chunk

    def read_past(self, pattern, initch):
        """`readToken` and `readNumber`: read to `pattern`, then push it back."""
        chunk = self.skip_to(pattern)
        self.unread(self.read1())
        return initch + chunk

    def nonspace(self):
        ch = self.read1()
        if ch in _SPACE:
            self.skip_to(_NOT_SPACE)
            ch = self.read1()
        return ch

    def read(self, eof_is_error, eof_value, is_recursive, pending):
        return self.read_full(
            eof_is_error, eof_value, None, None, is_recursive, _ensure(pending)
        )

    def read_full(
        self,
        eof_is_error,
        eof_value,
        return_on,
        return_value,
        is_recursive,
        pending,
        resolver=True,
    ):
        try:
            while True:
                if pending:
                    return pending.pop(0)
                ch = self.nonspace()
                if not ch:
                    if eof_is_error:
                        raise _Thrown(_RUNTIME, "EOF while reading")
                    return eof_value
                if return_on is not None and ch == return_on:
                    return return_value
                if _is_digit(ch):
                    return self.read_number(ch)
                macro = _MACRO_TABLE.get(ch)
                if macro is not None:
                    found = macro(self, ch, pending)
                    if found is _NOOP:
                        continue
                    return found
                if ch in "+-":
                    ch2 = self.read1()
                    if _is_digit(ch2):
                        self.unread(ch2)
                        return self.read_number(ch)
                    self.unread(ch2)
                token = self.text(self.read_past(_TOKEN_END, ch))
                return _interpret_token(token, resolver)
        except _Thrown as thrown:
            if is_recursive:
                raise
            raise _Thrown(_READER, str(thrown), self.line(), self.col, thrown) from None

    def read_number(self, initch):
        text = self.text(self.read_past(_NUMBER_END, initch))
        number = _match_number(text)
        if number is None:
            raise _Thrown(_NUMBER_FORMAT, "Invalid number: " + text)
        return number

    def unicode_char(self, initch, base, length, exact):
        value = _digit(initch, base)
        if value == -1:
            raise _Thrown(_ILLEGAL_ARGUMENT, "Invalid digit: " + initch)
        i = 1
        while i < length:
            ch = self.read1()
            if not ch or ch in _SPACE or ch in _MACROS:
                self.unread(ch)
                break
            digit = _digit(ch, base)
            if digit == -1:
                raise _Thrown(_ILLEGAL_ARGUMENT, "Invalid digit: " + ch)
            value = value * base + digit
            i += 1
        if i != length and exact:
            message = f"Invalid character length: {i}, should be: {length}"
            raise _Thrown(_ILLEGAL_ARGUMENT, message)
        return value

    def delimited(self, delim, pending):
        firstline = self.line()
        forms = []
        while True:
            form = self.read_full(
                False, _READ_EOF, delim, _READ_FINISHED, True, pending
            )
            if form is _READ_EOF:
                raise _Thrown(
                    _RUNTIME, f"EOF while reading, starting at line {firstline}"
                )
            if form is _READ_FINISHED:
                return forms
            forms.append(form)

    def string(self, _ch, _pending):
        parts = []
        while True:
            parts.append(self.skip_to(_STRING_STOP))
            ch = self.read1()
            if ch == '"':
                return self.text("".join(parts))
            if not ch:
                raise _Thrown(_RUNTIME, "EOF while reading string")
            ch = self.read1()
            if not ch:
                raise _Thrown(_RUNTIME, "EOF while reading string")
            if ch in _SIMPLE_ESCAPES:
                ch = _SIMPLE_ESCAPES[ch]
            elif ch == "u":
                ch = self.read1()
                if _digit(ch, 16) == -1:
                    raise _Thrown(
                        _RUNTIME, "Invalid unicode escape: \\u" + (ch or "\uffff")
                    )
                ch = chr(self.unicode_char(ch, 16, 4, True))
            elif _is_digit(ch):
                code = self.unicode_char(ch, 8, 3, False)
                if code > 0o377:
                    message = "Octal escape sequence must be in range [0, 377]."
                    raise _Thrown(_RUNTIME, message)
                ch = chr(code)
            else:
                raise _Thrown(_RUNTIME, "Unsupported escape character: \\" + ch)
            parts.append(ch)

    def regex(self, _ch, _pending):
        parts = []
        while True:
            parts.append(self.skip_to(_STRING_STOP))
            ch = self.read1()
            if ch == '"':
                break
            if not ch:
                raise _Thrown(_RUNTIME, "EOF while reading regex")
            parts.append(ch)
            ch = self.read1()
            if not ch:
                raise _Thrown(_RUNTIME, "EOF while reading regex")
            parts.append(ch)
        pattern = self.text("".join(parts))
        message = _jregex.problem(pattern)
        if message is not None:
            raise _Thrown(_PATTERN, message)
        return Regex(pattern)

    def comment(self, _ch, _pending):
        self.skip_to(_NEWLINE)
        self.read1()
        return _NOOP

    def discard(self, _ch, pending):
        self.read(True, None, True, pending)
        return _NOOP

    def meta(self, _ch, pending):
        pending = _ensure(pending)
        meta = self.read(True, None, True, pending)
        if isinstance(meta, (Sym, str)):
            entries = [(_TAG, meta)]
        elif isinstance(meta, Kw):
            entries = [(meta, True)]
        elif isinstance(meta, Coll) and meta.kind == "vector":
            entries = [(_PARAM_TAGS, meta)]
        elif isinstance(meta, Coll) and meta.kind == "map":
            entries = meta.items
        else:
            message = "Metadata must be Symbol,Keyword,String,Vector or Map"
            raise _Thrown(_ILLEGAL_ARGUMENT, message)
        target = self.read(True, None, True, pending)
        if isinstance(target, (Sym, Coll)):
            return _with_meta(target, entries)
        raise _Thrown(_ILLEGAL_ARGUMENT, "Metadata can only be applied to IMetas")

    def syntax_quote(self, _ch, pending):
        saved = self.gensyms
        self.gensyms = {}
        if saved is None:
            self.quote_steps = _QUOTE_BUDGET
        try:
            form = self.read(True, None, True, pending)
            return self.quote_form(form)
        finally:
            self.gensyms = saved

    def quote_form(self, form):
        self.quote_steps -= 1
        if self.quote_steps < 0:
            raise _NoVerdict()
        if isinstance(form, Sym) and (
            (form.ns is None and form.name in _SPECIALS)
            or (form.ns == "clojure.core" and form.name == "import*")
        ):
            quoted = _list(_QUOTE, form)
        elif isinstance(form, Sym):
            if form.ns is None and form.name.endswith("#"):
                gensyms = self.gensyms
                if gensyms is None:
                    raise _Thrown(_ILLEGAL_STATE, "Gensym literal not in syntax-quote")
                sym = gensyms.get(form.name)
                if sym is None:
                    sym = Sym(None, f"{form.name[:-1]}__{next(self.ids)}__auto__")
                    gensyms[form.name] = sym
            elif (
                form.ns is None
                and form.name.startswith(".")
                and not form.name.endswith(".")
            ):
                sym = form
            elif form.ns is None and not form.name.endswith("."):
                sym = Sym("user", form.name)
            else:
                sym = Sym(form.ns, form.name)
            quoted = _list(_QUOTE, sym)
        elif _is_seq_of(form, _UNQUOTE):
            return _second(form)
        elif _is_seq_of(form, _UNQUOTE_SPLICING):
            raise _Thrown(_ILLEGAL_STATE, "splice not in list")
        elif isinstance(form, Coll):
            if form.kind == "map":
                flat = [part for pair in form.items for part in pair]
                quoted = _list(
                    _APPLY, _HASHMAP, _list(_SEQ, _list(_CONCAT, *self.expand(flat)))
                )
            elif form.kind == "vector":
                quoted = _list(
                    _APPLY,
                    _VECTOR,
                    _list(_SEQ, _list(_CONCAT, *self.expand(form.items))),
                )
            elif form.kind == "set":
                quoted = _list(
                    _APPLY,
                    _HASHSET,
                    _list(_SEQ, _list(_CONCAT, *self.expand(form.items))),
                )
            elif not form.items:
                quoted = _list(_LIST)
            else:
                quoted = _list(_SEQ, _list(_CONCAT, *self.expand(form.items)))
        elif isinstance(form, (Kw, Num, Char, str)):
            quoted = form
        else:
            quoted = _list(_QUOTE, form)
        meta = getattr(form, "meta", None) if isinstance(form, (Sym, Coll)) else None
        if meta:
            return _list(_WITH_META, quoted, self.quote_form(Coll("map", meta)))
        return quoted

    def expand(self, items):
        expanded = []
        for item in items:
            if _is_seq_of(item, _UNQUOTE):
                expanded.append(_list(_LIST, _second(item)))
            elif _is_seq_of(item, _UNQUOTE_SPLICING):
                expanded.append(_second(item))
            else:
                expanded.append(_list(_LIST, self.quote_form(item)))
        return expanded

    def unquote(self, _ch, pending):
        ch = self.read1()
        if not ch:
            raise _Thrown(_RUNTIME, "EOF while reading character")
        pending = _ensure(pending)
        if ch == "@":
            return _list(_UNQUOTE_SPLICING, self.read(True, None, True, pending))
        self.unread(ch)
        return _list(_UNQUOTE, self.read(True, None, True, pending))

    def character(self, _ch, _pending):
        ch = self.read1()
        if not ch:
            raise _Thrown(_RUNTIME, "EOF while reading character")
        token = self.read_past(_TOKEN_END, ch)
        if len(token) == 1:
            return Char(token)
        named = {
            "newline": "\n",
            "space": " ",
            "tab": "\t",
            "backspace": "\b",
            "formfeed": "\f",
            "return": "\r",
        }
        if token in named:
            return Char(named[token])
        if token.startswith("u"):
            code = self.token_char(token, 1, 4, 16)
            if 0xD800 <= code <= 0xDFFF:
                raise _Thrown(_RUNTIME, f"Invalid character constant: \\u{code:x}")
            return Char(chr(code))
        if token.startswith("o"):
            size = len(token) - 1
            if size > 3:
                raise _Thrown(_RUNTIME, f"Invalid octal escape sequence length: {size}")
            code = self.token_char(token, 1, size, 8)
            if code > 0o377:
                message = "Octal escape sequence must be in range [0, 377]."
                raise _Thrown(_RUNTIME, message)
            return Char(chr(code))
        raise _Thrown(_RUNTIME, "Unsupported character: \\" + self.text(token))

    def token_char(self, token, offset, length, base):
        if len(token) != offset + length:
            message = "Invalid unicode character: \\" + self.text(token)
            raise _Thrown(_ILLEGAL_ARGUMENT, message)
        value = 0
        for unit in token[offset : offset + length]:
            digit = _digit(unit, base)
            if digit == -1:
                raise _Thrown(_ILLEGAL_ARGUMENT, "Invalid digit: " + unit)
            value = value * base + digit
        return value

    def list_form(self, _ch, pending):
        return Coll("list", self.delimited(")", _ensure(pending)))

    def vector(self, _ch, pending):
        return Coll("vector", self.delimited("]", _ensure(pending)))

    def map_form(self, _ch, pending):
        forms = self.delimited("}", _ensure(pending))
        if len(forms) & 1:
            raise _Thrown(_RUNTIME, "Map literal must contain an even number of forms")
        return _rt_map(forms)

    def set_form(self, _ch, pending):
        forms = self.delimited("}", _ensure(pending))
        _check_unique(forms)
        return Coll("set", forms)

    def unmatched(self, ch, _pending):
        raise _Thrown(_RUNTIME, "Unmatched delimiter: " + ch)

    def arg(self, _ch, _pending):
        token = self.text(self.read_past(_TOKEN_END, "%"))
        if self.arg_env is None:
            return _interpret_token(token, False)
        m = _ARG.fullmatch(token)
        if not m:
            raise _Thrown(_ILLEGAL_STATE, "arg literal must be %, %& or %integer")
        if m.group(1) is not None:
            return self.register_arg(-1)
        return self.register_arg(1 if m.group(2) is None else _parse_int(m.group(2)))

    def garg(self, n):
        prefix = "rest" if n == -1 else f"p{n}"
        return Sym(None, f"{prefix}__{next(self.ids)}#")

    def register_arg(self, n):
        arg_env = self.arg_env
        if arg_env is None:
            raise _Thrown(_ILLEGAL_STATE, "arg literal not in #()")
        sym = arg_env.get(n)
        if sym is None:
            sym = arg_env[n] = self.garg(n)
        return sym

    def fn(self, _ch, pending):
        if self.arg_env is not None:
            raise _Thrown(_ILLEGAL_STATE, "Nested #()s are not allowed")
        self.arg_env = {}
        try:
            self.unread("(")
            form = self.read(True, None, True, pending)
            args = []
            if self.arg_env:
                high = max(self.arg_env)
                for i in range(1, high + 1):
                    args.append(self.arg_env.get(i) or self.garg(i))
                if -1 in self.arg_env:
                    args += [_AMP, self.arg_env[-1]]
            return _list(_FN, Coll("vector", args), form)
        finally:
            self.arg_env = None

    def dispatch(self, _ch, pending):
        ch = self.read1()
        if not ch:
            raise _Thrown(_RUNTIME, "EOF while reading character")
        if ord(ch) >= 256:
            raise _Thrown(_INDEX, f"Index {ord(ch)} out of bounds for length 256")
        macro = _DISPATCH_TABLE.get(ch)
        if macro is None:
            self.unread(ch)
            return self.ctor(_ensure(pending))
        return macro(self, ch, pending)

    def ctor(self, pending):
        name = self.read(True, None, False, pending)
        if not isinstance(name, Sym):
            raise _Thrown(_RUNTIME, "Reader tag must be a symbol")
        form = self.read(True, None, True, pending)
        if self.suppress:
            return Tagged(name, form)
        if "." in name.name:
            message = (
                "Record construction syntax can only be used when *read-eval* == true"
            )
            raise _Thrown(_RUNTIME, message)
        if name.ns is None and name.name == "inst":
            return _read_inst(form)
        if name.ns is None and name.name == "uuid":
            return _read_uuid(form)
        return Tagged(name, form)

    def eval_form(self, _ch, _pending):
        raise _Thrown(_RUNTIME, "EvalReader not allowed when *read-eval* is false.")

    def unreadable(self, _ch, _pending):
        raise _Thrown(_RUNTIME, "Unreadable form")

    def symbolic(self, _ch, pending):
        form = self.read(True, None, True, pending)
        if not isinstance(form, Sym):
            raise _Thrown(_RUNTIME, "Invalid token: ##" + _to_string(form))
        if form.ns is None and form.name in _SYMBOLIC:
            return _SYMBOLIC[form.name]
        raise _Thrown(_RUNTIME, "Unknown symbolic value: ##" + str(form))

    def conditional(self, _ch, pending):
        ch = self.read1()
        if not ch:
            raise _Thrown(_RUNTIME, "EOF while reading character")
        splicing = ch == "@"
        if splicing:
            ch = self.read1()
        while ch in _SPACE:
            ch = self.read1()
        if not ch:
            raise _Thrown(_RUNTIME, "EOF while reading character")
        if ch != "(":
            raise _Thrown(_RUNTIME, "read-cond body must be a list")
        return self.read_cond(splicing, pending)

    def read_cond(self, splicing, pending):
        result = _READ_STARTED
        toplevel = pending is None
        pending = _ensure(pending)
        firstline = self.line()
        eof = f"EOF while reading, starting at line {firstline}"
        while True:
            if result is _READ_STARTED:
                form = self.read_full(
                    False, _READ_EOF, ")", _READ_FINISHED, True, pending, resolver=False
                )
                if form is _READ_EOF:
                    raise _Thrown(_RUNTIME, eof)
                if form is _READ_FINISHED:
                    break
                if (
                    isinstance(form, Kw)
                    and form.ns is None
                    and form.name in ("else", "none")
                ):
                    raise _Thrown(_RUNTIME, f"Feature name {form} is reserved.")
                if not isinstance(form, Kw):
                    raise _Thrown(
                        _RUNTIME, "Feature should be a keyword: " + _to_string(form)
                    )
                if form.ns is None and form.name in ("default", "clj"):
                    form = self.read_full(
                        False, _READ_EOF, ")", _READ_FINISHED, True, pending
                    )
                    if form is _READ_EOF:
                        raise _Thrown(_RUNTIME, eof)
                    if form is _READ_FINISHED:
                        message = f"read-cond starting on line {firstline} requires an even number of forms"
                        raise _Thrown(_RUNTIME, message)
                    result = form
            saved = self.suppress
            self.suppress = True
            try:
                form = self.read_full(
                    False, _READ_EOF, ")", _READ_FINISHED, True, pending
                )
            finally:
                self.suppress = saved
            if form is _READ_EOF:
                raise _Thrown(_RUNTIME, eof)
            if form is _READ_FINISHED:
                break
        if result is _READ_STARTED:
            return _NOOP
        if splicing:
            if not (isinstance(result, Coll) and result.kind in ("list", "vector")):
                message = "Spliced form list in read-cond-splicing must implement java.util.List"
                raise _Thrown(_RUNTIME, message)
            if toplevel:
                message = "Reader conditional splicing not allowed at the top level."
                raise _Thrown(_RUNTIME, message)
            pending[0:0] = result.items
            return _NOOP
        return result

    def namespace_map(self, _ch, pending):
        auto = False
        ch = self.read1()
        if ch == ":":
            auto = True
        else:
            self.unread(ch)
        sym = None
        ch = self.read1()
        if ch in _SPACE:
            if auto:
                while ch in _SPACE:
                    ch = self.read1()
                if ch != "{":
                    self.unread(ch)
                    raise _Thrown(_RUNTIME, "Namespaced map must specify a namespace")
            else:
                self.unread(ch)
                raise _Thrown(_RUNTIME, "Namespaced map must specify a namespace")
        elif ch != "{":
            self.unread(ch)
            sym = self.read(True, None, False, pending)
            ch = self.read1()
            while ch in _SPACE:
                ch = self.read1()
        if ch != "{":
            raise _Thrown(_RUNTIME, "Namespaced map must specify a map")
        if auto and sym is None:
            ns = "user"
        elif not isinstance(sym, Sym) or sym.ns is not None:
            message = "Namespaced map must specify a valid namespace: " + _to_string(
                sym
            )
            raise _Thrown(_RUNTIME, message)
        else:
            ns = sym.name
        forms = self.delimited("}", _ensure(pending))
        if len(forms) & 1:
            message = "Namespaced map literal must contain an even number of forms"
            raise _Thrown(_RUNTIME, message)
        flat = []
        for key, value in zip(forms[0::2], forms[1::2], strict=True):
            if isinstance(key, (Kw, Sym)):
                if key.ns is None:
                    key = type(key)(ns, key.name)
                elif key.ns == "_":
                    key = type(key)(None, key.name)
            flat += [key, value]
        return _rt_map(flat)


def _wrapping(sym):
    def read_wrapped(reader, _ch, pending):
        return _list(sym, reader.read(True, None, True, pending))

    return read_wrapped


_MACRO_TABLE = {
    '"': _Reader.string,
    ";": _Reader.comment,
    "'": _wrapping(_QUOTE),
    "@": _wrapping(_DEREF),
    "^": _Reader.meta,
    "`": _Reader.syntax_quote,
    "~": _Reader.unquote,
    "(": _Reader.list_form,
    ")": _Reader.unmatched,
    "[": _Reader.vector,
    "]": _Reader.unmatched,
    "{": _Reader.map_form,
    "}": _Reader.unmatched,
    "\\": _Reader.character,
    "%": _Reader.arg,
    "#": _Reader.dispatch,
}
_DISPATCH_TABLE = {
    "^": _Reader.meta,
    "#": _Reader.symbolic,
    "'": _wrapping(_THE_VAR),
    '"': _Reader.regex,
    "(": _Reader.fn,
    "{": _Reader.set_form,
    "=": _Reader.eval_form,
    "!": _Reader.comment,
    "<": _Reader.unreadable,
    "_": _Reader.discard,
    "?": _Reader.conditional,
    ":": _Reader.namespace_map,
}


def _java_lines(text):
    parts = _LINE_BREAKS.split(text)
    if len(parts) > 1:
        while parts and not parts[-1]:
            parts.pop()
    return parts


def _message(thrown):
    said = thrown.message or ""
    if all(char in _JAVA_SPACE for char in said):
        return re.split(r"[.$]", thrown.cls)[-1]
    return _LINE_BREAKS.split(said)[0]


# One check adds this many frames to the recursion limit. The reader uses 3 to 12
# frames for each nesting level, so it reads 5,000 to 20,000 levels: as deep as the
# Clojure reader reads on a JVM thread stack. A function that recurses into nested
# values calls itself from Python code, never through a C builtin such as `map`,
# `sum`, `all` or `str.join` over a generator. On Python 3.11 such a builtin adds a
# C stack frame for each level and can overflow the C stack before this limit.
_FRAME_BUDGET = 60_000
# One outermost syntax-quote expands in at most this many steps. Real code needs a
# few hundred; the largest in clojure.core, core.async and core.logic needs 142.
_QUOTE_BUDGET = 100_000
# The recursion limit and the integer digit limit belong to the whole process.
_CHECK_LOCK = threading.Lock()


def problem(source):
    """Where `source` stops reading as Clojure, or None when it reads to the end.

    Line and column are 1-based and mark where the reader stopped. For a form still
    open at the end of the source that is the end, and the message names the line
    the form started on. Source nested too deeply for the reader, or a syntax-quote
    that expands past `_QUOTE_BUDGET` steps, answers None: there is no verdict to
    report.
    """
    text = str(source)
    units = _units(text.replace("\r\n", "\n").replace("\r", "\n"))
    reader = _Reader(units)
    with _CHECK_LOCK:
        limit, digits = sys.getrecursionlimit(), sys.get_int_max_str_digits()
        sys.setrecursionlimit(limit + _FRAME_BUDGET)
        # A JVM reads an integer of any length.
        sys.set_int_max_str_digits(0)
        try:
            while (
                reader.read_full(False, _READ_EOF, None, None, False, None)
                is not _READ_EOF
            ):
                pass
        except (RecursionError, _NoVerdict):
            return None
        except _Thrown as thrown:
            # The outermost read records where it stopped. Without it, report 1:1.
            line = 1 if thrown.line is None else thrown.line
            column = 1 if thrown.column is None else thrown.column
            lines = _java_lines(text)
            if lines and line > len(lines):
                line, column = len(lines), len(_units(lines[-1])) + 1
            return Problem(line, column, _message(thrown.cause))
        finally:
            sys.set_int_max_str_digits(digits)
            if sys.getrecursionlimit() == limit + _FRAME_BUDGET:
                sys.setrecursionlimit(limit)
    return None


def parses_clean(source):
    """True when `source` reads as Clojure from end to end; see `problem`."""
    return problem(source) is None
