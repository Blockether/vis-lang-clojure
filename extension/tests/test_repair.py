"""Keep structural repair separate from full Clojure reader validation."""

import json
import runpy
from pathlib import Path

import blockether.vis.extension as vis
import pytest

from vis_lang_clojure import bridge
from vis_lang_clojure.repair import repair_source

CASES = json.loads((Path(__file__).parent / "fixtures/reader_repairs.json").read_text())


@pytest.mark.parametrize(
    "case", CASES, ids=lambda case: repr(case["input"]["source"][:36])
)
def test_repair_matches_the_full_reader_reference(case):
    result = repair_source(**case["input"], parses_clean=case["reads"].__getitem__)
    if case["expected"] is None:
        assert result is None
    else:
        assert result.source == case["expected"]["content"]
        assert list(result.notes) == case["expected"]["notes"]


def test_balanced_delimiters_do_not_replace_a_reader_verdict():
    assert (
        repair_source("{:a 1 :a 2", spans=((1, 1),), parses_clean=lambda _: False)
        is None
    )


def test_a_reader_failure_does_not_authorize_a_repair():
    def unavailable(source):
        raise RuntimeError("Reader unavailable")

    with pytest.raises(RuntimeError, match="Reader unavailable"):
        repair_source("(a", spans=((1, 1),), parses_clean=unavailable)


@pytest.fixture
def extension(monkeypatch, tmp_path):
    registered = []
    valid = {"(a)\n", "(b)\n", "(ns app)\n"}

    def check(verb, arg, *, root, timeout_s):
        assert verb == "check"
        return {
            "files": len(arg["sources"]),
            "problems": [
                {"file": name, "line": 1, "column": 1, "message": "Reader error"}
                for name, source in arg["sources"].items()
                if source not in valid
            ],
        }

    (tmp_path / "deps.edn").write_text("{}\n")
    monkeypatch.setattr(bridge, "call", check)
    monkeypatch.setattr(vis, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(vis, "state", {})
    monkeypatch.setattr(vis, "register_extension", registered.append)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    return registered[0]


def test_hook_proposes_a_repair_before_writing(extension, tmp_path):
    path = tmp_path / "core.clj"
    path.write_text("(a)\n")
    decision = extension.op_hooks[0].fn(
        {
            "op": "patch",
            "preview": {
                "path": str(path),
                "before": "(a)\n",
                "after": "(b\n",
                "spans": [[1, 1]],
            },
        }
    )
    assert decision["marker"] == "repair"
    assert decision["source"] == "(b)\n"
    assert decision["notes"] == ["line 1 added `)` → `(b)`"]
    assert path.read_text() == "(a)\n"


def test_hook_refuses_a_structural_candidate_the_reader_rejects(extension, tmp_path):
    path = tmp_path / "core.edn"
    path.write_text("(a)\n")
    decision = extension.op_hooks[0].fn(
        {
            "op": "patch",
            "preview": {
                "path": str(path),
                "before": "(a)\n",
                "after": "{:a 1 :a 2\n",
                "spans": [[1, 1]],
            },
        }
    )
    assert decision["marker"] == "block"
    assert path.read_text() == "(a)\n"


def test_hook_repairs_a_file_only_after_the_block(extension, tmp_path):
    path = tmp_path / "core.clj"
    path.write_text("(a)\n")
    before, after = extension.op_hooks[1:]
    call = {"op": "python_execution", "args": [{"code": "pass"}], "result": {}}
    before.fn(call)
    path.write_text("(b\n")
    assert path.read_text() == "(b\n"
    after.fn(call)
    assert path.read_text() == "(b)\n"
    context = extension.ctx()
    assert "clojure_syntax_errors" not in context
    assert context["clojure_syntax_repairs"]


def test_hook_keeps_an_unrepairable_write_visible(extension, tmp_path):
    path = tmp_path / "core.clj"
    path.write_text("(a)\n")
    before, after = extension.op_hooks[1:]
    call = {"op": "python_execution", "args": [], "result": {}}
    before.fn(call)
    path.write_text("(a]\n")
    after.fn(call)
    assert path.read_text() == "(a]\n"
    assert extension.ctx()["clojure_syntax_errors"]
