"""Pin the pure-Python Clojure reader to Clojure's own reader.

The fixtures hold the verdicts of Clojure 1.12.5 on Java 25. The JVM test
`test/com/blockether/vis/lang/clojure/syntax_test.clj` checks the same files against
the real reader, so a fixture cannot drift from Clojure.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from vis_lang_clojure import _jregex, reader
from vis_lang_clojure.reader import Problem

FIXTURES = Path(__file__).parent / "fixtures"
READER_CASES = json.loads((FIXTURES / "reader_verdicts.json").read_text())
REGEX_CASES = json.loads((FIXTURES / "regex_verdicts.json").read_text())


def verdict(source):
    """The reader's verdict on `source`, in the shape of the fixture."""
    found = reader.problem(source)
    return None if found is None else [found.line, found.column, found.message]


def skip_without_java_names(text, found, expected):
    """Skip a case that this Python cannot decide: Java 25 knows character names from
    Unicode 16.0, and an older table gives no verdict for a name in `\\N{...}`."""
    if found is None and expected is not None and "\\N{" in text:
        if not _jregex._KNOWS_NAMES:
            pytest.skip("This Python's Unicode table is older than Java 25's.")


@pytest.mark.parametrize(
    "case", READER_CASES, ids=lambda case: repr(case["source"][:36])
)
def test_reader_matches_clojure(case):
    found = verdict(case["source"])
    skip_without_java_names(case["source"], found, case["problem"])
    assert found == case["problem"]
    assert reader.parses_clean(case["source"]) is (case["problem"] is None)


@pytest.mark.parametrize(
    "case", REGEX_CASES, ids=lambda case: repr(case["pattern"][:36])
)
def test_regex_matches_java(case):
    found = _jregex.problem(case["pattern"])
    skip_without_java_names(case["pattern"], found, case["problem"])
    assert found == case["problem"]


@pytest.mark.parametrize("cases", [READER_CASES, REGEX_CASES], ids=["reader", "regex"])
def test_fixture_holds_both_verdicts(cases):
    verdicts = [case["problem"] for case in cases]
    assert verdicts.count(None) > 100
    assert len(verdicts) - verdicts.count(None) > 100


def test_reads_a_quoted_form_as_a_cons():
    # RT.list builds `'x` as a Cons. A JVM adds the module of each class after this.
    assert reader.problem("#inst 'x") == Problem(
        1, 9, "class clojure.lang.Cons cannot be cast to class java.lang.CharSequence"
    )


def test_prints_a_tagged_literal_with_its_java_hash():
    assert reader.problem("#{#foo 1 #foo 1}") == Problem(
        1, 17, "Duplicate key: clojure.lang.TaggedLiteral@34b5d2d5"
    )


def test_reads_an_inst_before_the_gregorian_change_as_a_julian_date():
    # GregorianCalendar reads 1582-10-05 as a Julian date, which is 1582-10-15.
    found = reader.problem('#{#inst "1582-10-05" #inst "1582-10-15"}')
    assert found is not None
    assert (found.line, found.column) == (1, 41)
    # The text of the date depends on the time zone.
    assert found.message.startswith("Duplicate key: ")
    assert reader.problem('#{#inst "1582-10-04" #inst "1582-10-15"}') is None


def test_reads_nesting_deeper_than_the_python_recursion_limit():
    depth = 5_000
    assert reader.problem("(" * depth + ")" * depth) is None
    assert reader.problem("[" * depth) == Problem(
        1, depth + 1, "EOF while reading, starting at line 1"
    )


def test_names_a_duplicate_key_nested_deeper_than_the_c_stack_allows():
    # Python 3.11 crashed here when a C builtin such as `map` drove the recursion.
    key = "[" * 20_000 + "]" * 20_000
    assert reader.problem(f"#{{{key} {key}}}") == Problem(
        1, 80_005, f"Duplicate key: {key}"
    )


def test_gives_no_verdict_for_nesting_too_deep_to_read():
    assert reader.problem("(" * 200_000) is None


def test_reads_an_integer_longer_than_the_python_digit_limit():
    digits = "9" * 5_000
    assert reader.problem(f"[{digits} -{digits}]") is None
    assert reader.problem(f"#{{{digits} {digits}}}") == Problem(
        1, 10_005, f"Duplicate key: {digits}"
    )


def test_gives_no_verdict_when_a_syntax_quote_expands_too_far():
    # Each nested syntax-quote expands the inner expansion again.
    started = time.monotonic()
    assert reader.problem("`" * 30 + "x") is None
    assert reader.problem("`(" * 30 + "x" + ")" * 30) is None
    assert time.monotonic() - started < 5
    assert reader.problem("[1 2) " + "`" * 30 + "x") == Problem(
        1, 6, "Unmatched delimiter: )"
    )


def test_restores_the_process_limits():
    limit, digits = sys.getrecursionlimit(), sys.get_int_max_str_digits()
    for source in ("(" * 200_000, "9" * 5_000, "{:a}", "`" * 30 + "x", "(a b)"):
        reader.problem(source)
    assert (sys.getrecursionlimit(), sys.get_int_max_str_digits()) == (limit, digits)


def test_checks_from_many_threads_at_once():
    sources = [case["source"] for case in READER_CASES[:200]]
    expected = [verdict(source) for source in sources]
    results = {}

    def check(worker):
        results[worker] = [verdict(source) for source in sources]

    threads = [threading.Thread(target=check, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == {n: expected for n in range(8)}
