"""Every tool answers the contract shape, from what the library reported."""

import runpy
from pathlib import Path

import blockether.vis.extension as vis
import pytest
from vis_lang_interface import Diagnostic

from vis_lang_clojure import bridge
from vis_lang_clojure.tools import _check_syntax


def test_formatting_a_source_string_returns_the_formatted_text(tools):
    clj, fake = tools
    fake.answer(
        "format",
        {
            "changed": True,
            "text": "(defn f [x] (* x 2))\n",
            "formatter": "zprint",
        },
    )
    result = clj.format_code(source="(defn f [x]\n(* x 2))", cwd=fake.cwd)
    assert result.language == "clojure"
    assert result.source == "(defn f [x] (* x 2))\n"
    assert result.is_written is False
    assert (result.lines_added, result.lines_removed) == (1, 2)
    assert fake.sent("format")["arg"] == {"code": "(defn f [x]\n(* x 2))"}


def test_checking_files_reports_what_changed_and_writes_nothing(tools):
    clj, fake = tools
    fake.answer(
        "format",
        {
            "files": [
                {
                    "path": "src/a.clj",
                    "changed": True,
                    "before": "(defn f [x]\n(* x 2))\n",
                    "after": "(defn f [x]\n  (* x 2))\n",
                },
                {"path": "src/b.clj", "changed": False},
                {
                    "path": "src/c.clj",
                    "changed": True,
                    "before": "(ns c)\n(def x 1)",
                    "after": "(ns c)\n\n(def x 1)\n",
                },
            ],
            "changed": 2,
        },
    )
    result = clj.format_code(["src"], cwd=fake.cwd)
    assert result.changed == ("src/a.clj", "src/c.clj")
    assert result.unchanged == ("src/b.clj",)
    assert result.is_written is False
    assert (result.lines_added, result.lines_removed) == (3, 2)
    assert fake.sent("format")["arg"] == {"paths": ["src"], "write": False}


# Blockether/vis#321: clj.format_code takes is_written, like py.format_code.
def test_formatting_files_with_is_written_rewrites_them(tools):
    clj, fake = tools
    fake.answer(
        "format",
        {
            "files": [
                {
                    "path": "src/a.clj",
                    "changed": True,
                    "wrote": True,
                    "before": "(defn f [x]\n(* x 2))\n",
                    "after": "(defn f [x]\n  (* x 2))\n",
                },
                {"path": "src/b.clj", "changed": False, "wrote": False},
            ],
            "changed": 1,
        },
    )
    result = clj.format_code(["src"], cwd=fake.cwd, is_written=True)
    assert result.changed == ("src/a.clj",)
    assert result.is_written is True
    assert fake.sent("format")["arg"] == {"paths": ["src"], "write": True}


def test_formatting_a_formatted_tree_writes_nothing(tools):
    clj, fake = tools
    fake.answer(
        "format", {"files": [{"path": "src/b.clj", "changed": False}], "changed": 0}
    )
    result = clj.format_code(["src"], cwd=fake.cwd, is_written=True)
    assert (result.changed, result.is_written) == ((), False)


def test_formatting_nothing_checks_the_whole_project(tools):
    clj, fake = tools
    fake.answer("format", {"files": [], "changed": 0})
    assert clj.format_code(cwd=fake.cwd).changed == ()
    assert fake.sent("format")["arg"] == {"write": False}


def test_lint_findings_become_diagnostics(tools):
    clj, fake = tools
    fake.answer(
        "lint",
        {
            "files": 2,
            "findings": [
                {
                    "file": "src/a.clj",
                    "row": 7,
                    "col": 3,
                    "level": "error",
                    "type": "invalid-arity",
                    "message": "a/f is called with 2 args but expects 1",
                    "provider": "clj-kondo",
                },
                {
                    "file": "src/a.clj",
                    "row": 9,
                    "col": 1,
                    "level": "warning",
                    "message": "reflection on java.util.Date",
                    "provider": "general",
                },
            ],
        },
    )
    result = clj.lint_code(["src"], cwd=fake.cwd)
    assert (result.files, result.errors, result.warnings, result.is_clean) == (
        2,
        1,
        1,
        False,
    )
    first, second = result.diagnostics
    assert (first.path, first.line, first.column, first.level) == (
        "src/a.clj",
        7,
        3,
        "error",
    )
    assert first.rule == "invalid-arity"
    assert second.rule == "general"
    assert fake.sent("lint")["arg"] == {"paths": ["src"]}


def test_a_clean_lint_of_a_source_string_says_so(tools):
    clj, fake = tools
    fake.answer("lint", {"files": 1, "findings": [], "snippet": "(inc 1)"})
    result = clj.lint_code(source="(inc 1)", cwd=fake.cwd)
    assert result.is_clean is True
    assert result.diagnostics == ()
    assert fake.sent("lint")["arg"] == {"code": "(inc 1)"}


def test_a_syntax_check_reports_where_each_file_stops_reading(fake):
    sources = {"z.edn": "{:a}", "ok.cljc": "#?(:clj 1)", "a.clj": "(a"}
    result = _check_syntax(sources, fake.directory)
    assert (result.language, result.files, result.is_clean) == ("clojure", 3, False)
    assert result.diagnostics == (
        Diagnostic("a.clj", 1, 3, "error", "EOF while reading, starting at line 1"),
        Diagnostic(
            "z.edn", 1, 5, "error", "Map literal must contain an even number of forms"
        ),
    )


def test_a_source_string_that_parses_is_clean(fake):
    result = _check_syntax({"src/a.clj": "(inc 1)"}, fake.directory)
    assert (result.files, result.is_clean, result.diagnostics) == (1, True, ())


def test_the_guard_check_reads_without_a_jvm(fake):
    result = _check_syntax(
        {"src/a.clj": "(a))", "src/b.cljs": "#js {}"}, fake.directory
    )
    assert [(row.path, row.message) for row in result.diagnostics] == [
        ("src/a.clj", "Unmatched delimiter: )")
    ]
    assert fake.requests() == []


def test_the_guard_check_answers_clean_for_no_sources(fake):
    result = _check_syntax({}, fake.directory)
    assert (result.files, result.is_clean) == (0, True)


def test_a_green_run_is_counted_the_way_the_runner_counted_it(tools):
    clj, fake = tools
    fake.answer(
        "test",
        {
            "total": 10,
            "selected": 10,
            "fail": 0,
            "errored": 0,
            "is_pass": True,
            "output": "0 failures.",
        },
    )
    result = clj.run_tests(["test"], cwd=fake.cwd)
    assert (result.total, result.passed, result.failed, result.skipped) == (
        10,
        10,
        0,
        0,
    )
    assert result.is_passed is True
    assert result.output == "0 failures."
    assert result.duration_ms >= 0


def test_a_failing_run_lists_its_failures(tools):
    clj, fake = tools
    fake.answer(
        "test",
        {
            "total": 4,
            "fail": 1,
            "skipped": 1,
            "is_pass": False,
            "failures": [
                {
                    "ns": "a.core-test",
                    "test": "adds",
                    "file": "test/a/core_test.clj",
                    "line": 12,
                    "message": "expected 2 actual 3",
                }
            ],
        },
    )
    result = clj.run_tests(cwd=fake.cwd)
    assert (result.total, result.passed, result.failed, result.skipped) == (4, 3, 1, 1)
    assert result.is_passed is False
    failure = result.failures[0]
    assert (failure.test, failure.path, failure.line) == (
        "adds",
        "test/a/core_test.clj",
        12,
    )
    assert failure.message == "expected 2 actual 3"


def test_a_focused_run_counts_only_the_tests_that_ran(tools):
    # The REPL reports unselected tests as skipped, outside the total.
    clj, fake = tools
    fake.answer(
        "test",
        {"total": 1, "selected": 1, "fail": 0, "skipped": 2, "is_pass": True},
    )
    result = clj.run_tests(vars=["a.core-test/adds"], cwd=fake.cwd)
    assert (result.total, result.passed, result.failed, result.skipped) == (1, 1, 0, 2)
    assert result.is_passed is True


def test_a_run_that_broke_without_counts_never_reads_green(tools):
    clj, fake = tools
    fake.answer(
        "test", {"is_pass": False, "exit": 1, "output": "Syntax error compiling"}
    )
    result = clj.run_tests(cwd=fake.cwd)
    assert result.failed == 1
    assert result.is_passed is False


def test_a_refused_run_says_why(tools):
    # Regression: a run the library refused before any test started answered
    # only an `error`, which was dropped, so it read as one failure and no output.
    clj, fake = tools
    why = "this runner has no supported focus adapter; no tests started"
    fake.answer("test", {"mode": "cli", "is_pass": False, "error": why})
    result = clj.run_tests(cwd=fake.cwd, namespaces=["a.core-test"])
    assert (result.failed, result.is_passed) == (1, False)
    assert result.output == why


def test_a_run_error_leads_what_the_runner_printed(tools):
    clj, fake = tools
    fake.answer(
        "test", {"is_pass": False, "error": "no summary", "output": "Ran 0 tests"}
    )
    assert clj.run_tests(cwd=fake.cwd).output == "no summary\n\nRan 0 tests"


def test_a_run_carries_its_selection_to_the_library(tools):
    clj, fake = tools
    fake.answer("test", {"total": 1, "fail": 0, "is_pass": True})
    clj.run_tests(
        ["test/a/core_test.clj"],
        cwd=fake.cwd,
        namespaces=["a.core-test"],
        vars=["adds"],
        include=["integration"],
        exclude=["slow"],
        aliases=["dev", "test"],
        build="app",
    )
    assert fake.sent("test")["arg"] == {
        "paths": ["test/a/core_test.clj"],
        "namespaces": ["a.core-test"],
        "vars": ["adds"],
        "include": ["integration"],
        "exclude": ["slow"],
        "aliases": ["dev", "test"],
        "build": "app",
    }


def test_starting_a_repl_reports_the_process_it_started(tools):
    clj, fake = tools
    fake.script(
        {
            "repl:start": {
                "ok": True,
                "result": {
                    "result": "started",
                    "id": "nrepl:~/project",
                    "cwd": fake.cwd,
                    "status": "up",
                    "port": 7888,
                    "pid": 42,
                    "cmd": ["clojure", "-M:dev:test"],
                },
            }
        }
    )
    session = clj.repl_start(cwd=fake.cwd, aliases=["dev"])
    assert session.is_running is True
    assert session.id == "nrepl:~/project"
    assert session.directory == fake.cwd
    assert session.command == ("clojure", "-M:dev:test")
    assert session.detail == "started · port 7888 · pid 42"
    assert fake.sent("repl")["arg"] == {"aliases": ["dev"]}


def test_a_project_without_a_repl_reports_it_is_down(tools):
    clj, fake = tools
    fake.script(
        {
            "repl:status": {
                "ok": True,
                "result": {
                    "result": "status",
                    "id": "nrepl:~/project",
                    "cwd": fake.cwd,
                    "status": "down",
                },
            }
        }
    )
    session = clj.repl_status(cwd=fake.cwd)
    assert session.is_running is False
    assert session.detail == "not running"


def test_stopping_a_repl_says_it_stopped(tools):
    clj, fake = tools
    fake.script(
        {
            "repl:stop": {
                "ok": True,
                "result": {"result": "stopped", "cwd": fake.cwd, "status": "down"},
            }
        }
    )
    session = clj.repl_stop(cwd=fake.cwd)
    assert session.is_running is False
    assert session.detail == "stopped"
    assert fake.sent("repl")["op"] == "stop"


def test_session_details_read_as_plain_words(tools):
    clj, fake = tools
    fake.script(
        {
            "repl:status": {
                "ok": True,
                "result": {
                    "result": "status",
                    "cwd": fake.cwd,
                    "status": "up",
                    "port": 7888,
                },
            },
            "repl:start": {
                "ok": True,
                "result": {
                    "result": "already-running",
                    "cwd": fake.cwd,
                    "status": "up",
                },
            },
        }
    )
    assert clj.repl_status(cwd=fake.cwd).detail == "running · port 7888"
    assert clj.repl_start(cwd=fake.cwd).detail == "already running"


def test_attaching_passes_the_port_and_the_build(tools):
    clj, fake = tools
    fake.script(
        {
            "repl:connect": {
                "ok": True,
                "result": {
                    "result": "attached",
                    "cwd": fake.cwd,
                    "status": "up",
                    "port": 9630,
                },
            }
        }
    )
    session = clj.repl_connect(cwd=fake.cwd, port=9630, build="app")
    assert session.is_running is True
    assert session.detail == "attached · port 9630"
    assert fake.sent("repl")["arg"] == {"port": 9630, "build": "app"}


def test_an_evaluation_returns_its_value_and_what_it_printed(tools):
    clj, fake = tools
    fake.answer(
        "repl-eval",
        {
            "value": "2",
            "out": "hello\n",
            "ms": 12,
            "repl": "nrepl:~/project",
            "code": "(+ 1\n   1)",
        },
    )
    result = clj.repl_eval("(+ 1 1)", cwd=fake.cwd, ns="user")
    assert (result.value, result.output, result.error) == ("2", "hello\n", "")
    assert (result.duration_ms, result.is_running) == (12, True)
    assert result.id == "nrepl:~/project"
    assert result.code == "(+ 1\n   1)"
    assert fake.sent("repl-eval")["arg"] == {
        "code": "(+ 1 1)",
        "timeout_ms": 30000,
        "ns": "user",
    }


def test_a_failed_evaluation_reports_the_error_and_where_it_points(tools):
    clj, fake = tools
    fake.answer(
        "repl-eval",
        {
            "status": ["eval-error"],
            "error_message": "ArithmeticException: Divide by zero",
            "context": "1: (/ 1 0)\n   ^--- Divide by zero",
            "err": "Execution error (ArithmeticException)\n",
            "ms": 3,
        },
    )
    result = clj.repl_eval("(/ 1 0)", cwd=fake.cwd)
    assert result.error.startswith("ArithmeticException: Divide by zero")
    assert "^--- Divide by zero" in result.error
    assert result.value == ""
    assert result.output == ""


def test_a_healthy_evaluation_keeps_what_it_wrote_to_stderr(tools):
    clj, fake = tools
    fake.answer(
        "repl-eval", {"value": "5", "err": "to-stderr\n", "status": ["done"], "ms": 4}
    )
    result = clj.repl_eval("(binding [*out* *err*] (println :x) 5)", cwd=fake.cwd)
    assert result.output == "to-stderr\n"
    assert result.error == ""


def test_a_timed_out_evaluation_says_so(tools):
    clj, fake = tools
    fake.answer("repl-eval", {"timed_out": True, "out": "partial\n", "ms": 1000})
    result = clj.repl_eval("(Thread/sleep 5000)", cwd=fake.cwd, timeout_ms=1000)
    assert result.error == "Timed out after 1000 ms."
    assert (result.output, result.code) == ("partial\n", "(Thread/sleep 5000)")


def test_evaluating_without_a_repl_says_which_project_has_none(tools):
    clj, fake = tools
    fake.script(
        {
            "repl-eval": {
                "ok": False,
                "error": {
                    "message": "no REPL running in ~/project",
                    "hint": "then retry the eval",
                },
            }
        }
    )
    with pytest.raises(bridge.ClojureError, match="no REPL running in ~/project"):
        clj.repl_eval("(+ 1 1)", cwd=fake.cwd)


def test_an_evaluation_puts_back_the_closers_its_indentation_shows(tools):
    # An agent that loses count of closing delimiters must not fight the REPL reader.
    clj, fake = tools
    fake.answer("repl-eval", {"value": "#'user/twice", "ms": 2})
    result = clj.repl_eval(
        "(defn twice [x]\n  (let [y (inc x)]\n    (* y 2))\n", cwd=fake.cwd
    )
    repaired = "(defn twice [x]\n  (let [y (inc x)]\n    (* y 2)))\n"
    assert fake.sent("repl-eval")["arg"]["code"] == repaired
    assert result.repairs == ("line 3 added `)` → `(* y 2)))`",)
    assert (result.code, result.value, result.error) == (repaired, "#'user/twice", "")


def test_unbalanced_code_without_a_safe_repair_is_not_evaluated(tools):
    # A REPL evaluates the forms before a surplus closer, then fails on it.
    clj, fake = tools
    code = "(defn add2 [x]\n  (+ x 2)))\n(add2 1)"
    result = clj.repl_eval(code, cwd=fake.cwd)
    assert fake.requests() == []
    lines = result.error.splitlines()
    assert lines[0] == (
        "Line 2, column 12: Unmatched delimiter: ). The code was not evaluated."
    )
    assert "it closes more than it opens, or an opener was lost" in lines[1]
    assert lines[2] == "Balance the delimiters, then evaluate again."
    assert (result.code, result.value, result.output, result.repairs) == (
        code,
        "",
        "",
        (),
    )
    assert (result.id, result.is_running, result.duration_ms) == (fake.cwd, False, 0)


def test_a_refused_evaluation_reports_the_live_repl(tools):
    clj, fake = tools
    live = {"result": "status", "id": "nrepl:~/project", "status": "up"}
    fake.script(
        {
            "repl:start": {"ok": True, "result": dict(live, result="started")},
            "repl:status": {"ok": True, "result": live},
        }
    )
    clj.repl_start(cwd=fake.cwd)
    result = clj.repl_eval('(println "hi)', cwd=fake.cwd)
    assert fake.requests("repl-eval") == []
    assert fake.sent("repl")["op"] == "status"
    assert result.error.splitlines()[0] == (
        "Line 1, column 14: EOF while reading string. The code was not evaluated."
    )
    assert (result.id, result.is_running) == ("nrepl:~/project", True)


def test_other_reader_errors_are_left_to_the_repl(tools):
    # A REPL reads with *read-eval* on, so it decides about `#=` itself.
    clj, fake = tools
    fake.answer("repl-eval", {"value": "3", "ms": 1})
    result = clj.repl_eval("#=(+ 1 2)", cwd=fake.cwd)
    assert fake.sent("repl-eval")["arg"]["code"] == "#=(+ 1 2)"
    assert (result.value, result.repairs) == ("3", ())


def test_draft_switch_targets_live_clojure_project_for_every_tool(
    fake, tmp_path, monkeypatch
):
    # Blockether/vis#280: relative paths follow the active draft, not the process cwd.
    from vis_lang_clojure.tools import ClojureTools

    roots = (fake.directory, tmp_path / "draft")
    roots[1].mkdir()
    (roots[1] / "deps.edn").write_text("{}\n")
    for root in roots:
        (root / "src").mkdir()
        (root / "src" / "a.clj").write_text("(ns a)\n")
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.chdir(install)
    current = {"root": roots[0]}
    language = ClojureTools(workspace_root=lambda: current["root"])
    fake.script(
        {
            "format": {"ok": True, "result": {"files": []}},
            "test": {"ok": True, "result": {"total": 0, "is_pass": True}},
            "repl:start": {"ok": True, "result": {"status": "up"}},
            "repl:status": {"ok": True, "result": {"status": "down"}},
            "repl:stop": {"ok": True, "result": {"status": "down"}},
        }
    )

    for root in roots:
        current["root"] = root
        language.format_code(["src/a.clj"], cwd=".")
        assert fake.sent("format")["root"] == str(root.resolve())
        assert fake.sent("format")["arg"]["paths"] == ["src/a.clj"]
        language.run_tests(["src/a.clj"])
        assert fake.sent("test")["root"] == str(root.resolve())
        for operation in (
            language.repl_start,
            language.repl_status,
            language.repl_stop,
        ):
            assert operation().directory == str(root.resolve())
            assert fake.sent("repl")["root"] == str(root.resolve())
        assert language.repl_status(cwd=".").directory == str(root.resolve())
        assert fake.sent("repl")["root"] == str(root.resolve())

    language.repl_status(cwd=str(roots[0]))
    assert fake.sent("repl")["root"] == str(roots[0].resolve())


def test_entrypoint_binds_the_live_sdk_workspace_root(tmp_path, monkeypatch):
    # Blockether/vis#280: registration must use the shared, live SDK door.
    roots = (tmp_path / "source", tmp_path / "draft")
    for root in roots:
        root.mkdir()
        (root / "deps.edn").write_text("{}\n")
    current = {"root": roots[0]}
    registered = []
    monkeypatch.setattr(vis, "workspace_root", lambda: current["root"])
    monkeypatch.setattr(vis, "register_extension", registered.append)

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    assert len(registered) == 1
    language = registered[0].symbols[0].fn
    for root in roots:
        current["root"] = root
        assert language._root(".") == str(root.resolve())


def _entrypoint_tags(monkeypatch):
    registered = []
    monkeypatch.setattr(vis, "register_extension", registered.append)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    members = registered[0].symbols[0].contract["members"]
    return {member["name"].rsplit(".", 1)[-1]: member["tag"] for member in members}


def _older_host(monkeypatch):
    """Stand in for a Vis host that knows only observation and mutation tags."""
    method = vis.method

    def older(fn=None, *, tag="observation", **options):
        if tag not in ("observation", "mutation"):
            raise ValueError(
                f"vis.method tag must be observation or mutation, got {tag!r}"
            )
        return method(fn, tag=tag, **options)

    monkeypatch.setattr(vis, "method", older)


def _knows_verification():
    try:
        vis.method(tag="verification")
    except ValueError:
        return False
    return True


@pytest.mark.skipif(
    not _knows_verification(), reason="this Vis SDK predates check tags"
)
def test_lint_and_test_runs_report_as_checks(monkeypatch):
    tags = _entrypoint_tags(monkeypatch)
    assert (tags["lint_code"], tags["run_tests"]) == ("verification", "verification")
    assert "check_syntax" not in tags
    assert tags["format_code"] == "mutation"


def test_an_older_host_records_lint_and_test_runs_as_reads(monkeypatch):
    _older_host(monkeypatch)
    tags = _entrypoint_tags(monkeypatch)
    assert (tags["lint_code"], tags["run_tests"]) == ("observation", "observation")
    assert tags["repl_eval"] == "mutation"
    assert "check_syntax" not in tags


def test_syntax_check_is_not_exported_or_advertised(monkeypatch):
    registered = []
    monkeypatch.setattr(vis, "register_extension", registered.append)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    extension = registered[0]
    members = extension.symbols[0].contract["members"]
    assert "clj.check_syntax" not in {member["name"] for member in members}
    assert not hasattr(extension.symbols[0].fn, "check_syntax")
    assert "check_syntax" not in extension.prompt


def test_the_entrypoint_guards_patches_and_python_writes(monkeypatch, tmp_path):
    registered = []
    calls = []
    (tmp_path / "deps.edn").write_text("{}\n")
    path = tmp_path / "core.clj"
    path.write_text("(a)\n")

    monkeypatch.setattr(bridge, "call", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(vis, "workspace_root", lambda: tmp_path)
    monkeypatch.setattr(vis, "state", {})
    monkeypatch.setattr(vis, "register_extension", registered.append)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    extension = registered[0]
    assert [(tuple(hook.ops), hook.phase) for hook in extension.op_hooks] == [
        (("patch",), "before"),
        (("python_execution",), "before"),
        (("patch", "python_execution"), "after"),
    ]
    assert callable(extension.ctx)
    guard = getattr(extension.ctx, "__self__")
    assert guard.key == "clojure_syntax_errors"
    assert all(
        guard.covers(f"src/a{suffix}") for suffix in (".clj", ".cljs", ".cljc", ".edn")
    )
    assert not guard.covers("src/a.py")
    preview = {"path": str(path), "before": "(a)\n", "after": "(a"}
    refusal = extension.op_hooks[0].fn({"op": "patch", "preview": preview})
    assert refusal["marker"] == "block"
    assert path.read_text() == "(a)\n"
    assert (
        extension.op_hooks[0].fn({"preview": {**preview, "path": "example.py"}}) is None
    )
    # The reader runs in Python, so a syntax check never starts the JVM bridge.
    assert calls == []


def test_every_tool_owns_an_activity_and_an_evaluation_shows_its_code(monkeypatch):
    from vis_lang_clojure.tools import ClojureTools

    registered = []
    monkeypatch.setattr(vis, "register_extension", registered.append)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "extension.py"))
    members = registered[0].symbols[0].contract["members"]
    for name in (member["name"].rsplit(".", 1)[-1] for member in members):
        label = getattr(ClojureTools, name).__vis_symbol_activity__.label
        assert label[:1].isupper() and "_" not in label, name

    activity = getattr(ClojureTools.repl_eval, "__vis_symbol_activity__")
    running = activity.render(phase="start", args=(), kwargs={"code": "(+ 1 2)"})
    assert (running.headline, running.summary) == (
        "Evaluate in Clojure REPL",
        "running",
    )
    assert [
        (block.text, getattr(block, "language", None)) for block in running.content
    ] == [
        ("Code", None),
        ("(+ 1 2)", "clojure"),
    ]
    failed = activity.render(
        phase="failure",
        args=(),
        kwargs={"code": "(boom)"},
        error=RuntimeError("no REPL"),
    )
    assert [block.text for block in failed.content] == [
        "Code",
        "(boom)",
        "Error",
        "no REPL",
    ]
