"""Pin the Python port to the legacy Java and Clojure engines.

The fixtures record upstream regression tests and seeded source mutations.
Parser cases cover every mode, UTF-16 positions, reader forms and deep nesting.
Balance cases replay the exact parser and balancer answers from upstream tests.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from vis_lang_clojure._balance import changed_span, rebalance
from vis_lang_clojure._parinfer import parse

FIXTURES = Path(__file__).parent / "fixtures"
PARSER_CASES = json.loads((FIXTURES / "parinfer.json").read_text())
BALANCE_CASES = json.loads((FIXTURES / "balance.json").read_text())


@pytest.mark.parametrize(
    "case", PARSER_CASES, ids=lambda case: repr(case["source"][:36])
)
def test_parser_matches_legacy(case):
    options = {k.replace("-", "_"): v for k, v in (case["options"] or {}).items()}
    result = parse(case["source"], **options)
    assert result.text == case["text"]
    assert result.changed == case["changed"]
    assert result.error == case["error"]
    assert [asdict(edit) for edit in result.edits] == case["edits"]


@pytest.mark.parametrize(
    "case", BALANCE_CASES, ids=lambda case: repr(case["input"]["source"][:36])
)
def test_balance_matches_legacy(case):
    options = dict(case["input"])
    options["balancer"] = case["balanced"].__getitem__ if case["has-balancer"] else None
    options["parses_clean"] = lambda source: bool(case["reads"][source])
    assert rebalance(**options) == case["expected"]


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ("(a)\n", "(a)\n", None),
        ("(a)\n(b)\n(c)\n", "(a)\n(B)\n(c)\n", (2, 2)),
        ("(a)\n(d)\n", "(a)\n(b)\n(c)\n(d)\n", (2, 3)),
        ("(a)\n(b)\n", "(a)\n", (1, 1)),
    ],
)
def test_changed_span_uses_after_lines(before, after, expected):
    assert changed_span(before, after) == expected


def test_parser_repairs_a_large_file():
    source = "".join(
        f"(defn handler-{index} [request]\n"
        "  (let [body (:body request)]\n    {:status 200 :body body}))\n\n"
        for index in range(5000)
    )
    broken = source.replace("{:status 200 :body body}))", "{:status 200 :body body})")
    assert parse(broken, mode="indent").text == source


def test_parser_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="Unknown parinfer mode"):
        parse("(a", mode="unknown")
