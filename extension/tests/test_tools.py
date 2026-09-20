"""Every tool answers the contract shape, from what the library reported."""

import pytest
from vis_lang_clojure import bridge


def test_formatting_a_source_string_returns_the_formatted_text(tools):
    clj, fake = tools
    fake.answer(
        "format",
        {"changed": True, "text": "(defn f [x] (* x 2))\n", "formatter": "zprint"},
    )
    result = clj.format_code(source="(defn f [x]\n(* x 2))", cwd=fake.cwd)
    assert result.language == "clojure"
    assert result.source == "(defn f [x] (* x 2))\n"
    assert result.is_written is False
    assert fake.sent("format")["arg"] == {"code": "(defn f [x]\n(* x 2))"}


def test_formatting_files_reports_what_changed(tools):
    clj, fake = tools
    fake.answer(
        "format",
        {
            "files": [
                {"path": "src/a.clj", "changed": True},
                {"path": "src/b.clj", "changed": False},
            ],
            "changed": 1,
        },
    )
    result = clj.format_code(["src"], cwd=fake.cwd)
    assert result.changed == ("src/a.clj",)
    assert result.unchanged == ("src/b.clj",)
    assert result.is_written is True
    assert fake.sent("format")["arg"] == {"paths": ["src"]}


def test_formatting_nothing_formats_the_whole_project(tools):
    clj, fake = tools
    fake.answer("format", {"files": [], "changed": 0})
    assert clj.format_code(cwd=fake.cwd).changed == ()
    assert fake.sent("format")["arg"] == {}


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
    assert (result.total, result.passed, result.failed, result.skipped) == (4, 2, 1, 1)
    assert result.is_passed is False
    failure = result.failures[0]
    assert (failure.test, failure.path, failure.line) == (
        "adds",
        "test/a/core_test.clj",
        12,
    )
    assert failure.message == "expected 2 actual 3"


def test_a_run_that_broke_without_counts_never_reads_green(tools):
    clj, fake = tools
    fake.answer(
        "test", {"is_pass": False, "exit": 1, "output": "Syntax error compiling"}
    )
    result = clj.run_tests(cwd=fake.cwd)
    assert result.failed == 1
    assert result.is_passed is False


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
    assert session.detail == "status"


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
            "code": "(+ 1 1)",
        },
    )
    result = clj.repl_eval("(+ 1 1)", cwd=fake.cwd, ns="user")
    assert (result.value, result.output, result.error) == ("2", "hello\n", "")
    assert (result.duration_ms, result.is_running) == (12, True)
    assert result.id == "nrepl:~/project"
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
