"""The Clojure tools this extension exports.

Ordinary Python: every method takes plain arguments and returns a contract
result from `vis_lang_interface`. The same calls work in a script, in a test and
from Vis. The Clojure library does the work itself, one JSON request away. The
syntax check is the exception: `reader` reads source in Python, without a JVM.
The same reader checks the delimiters of code before the REPL evaluates it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

from vis_lang_interface import (
    Diagnostic,
    FormatResult,
    LintResult,
    ReplResult,
    ReplSession,
    SyntaxResult,
    TestFailure,
    TestResult,
    line_changes,
    project_root,
    string_list,
)

from vis_lang_clojure import bridge, reader, repair

LANGUAGE = "clojure"

MARKERS = (
    "deps.edn",
    "project.clj",
    "shadow-cljs.edn",
    "bb.edn",
    "build.boot",
    ".git",
)

# The reader names unbalanced delimiters with these messages. The REPL decides
# about other problems itself: it reads `#=` and record literals.
UNBALANCED = ("EOF while reading", "Unmatched delimiter")

LEVELS = ("error", "warning", "info")

# The files the reader checks: Clojure, ClojureScript, shared, Babashka and EDN.
SYNTAX_SUFFIXES = (".clj", ".cljs", ".cljc", ".cljx", ".bb", ".edn")


def _root(cwd, paths=(), workspace_root=Path.cwd):
    """Find the nearest Clojure project in the current session working copy."""
    start = Path(cwd or (paths[0] if paths else ".")).expanduser()
    named = start if start.is_absolute() else Path(workspace_root()) / start
    return str(project_root(named, MARKERS))


def _diagnostic(finding):
    """One clj-kondo or compiler finding as a contract `Diagnostic`."""
    level = str(finding.get("level") or "warning")
    return Diagnostic(
        str(finding.get("file") or ""),
        int(finding.get("row") or 0),
        int(finding.get("col") or 0),
        level if level in LEVELS else "warning",
        str(finding.get("message") or ""),
        str(finding.get("type") or finding.get("provider") or ""),
    )


def _check_syntax(sources, root):
    """Read each text in `sources` with the Python port of Clojure's reader.

    This is the check a `SyntaxGuard` runs. `sources` maps a path, spelled the
    way the result names it, to the text to read. Nothing is read from disk and no
    JVM starts, so `root` is not used. Each text that does not read gives one error:
    where the reader stopped, as `reader.problem` reports it.
    """
    texts = {str(path): str(text) for path, text in dict(sources).items()}
    problems = []
    for path in sorted(texts):
        found = reader.problem(texts[path])
        if found is not None:
            problems.append(
                Diagnostic(path, found.line, found.column, "error", found.message)
            )
    return SyntaxResult.of(LANGUAGE, tuple(problems), len(texts))


def _failure(fault):
    """One failing test as a contract `TestFailure`."""
    name = str(fault.get("test") or fault.get("ns") or "")
    return TestFailure(
        name,
        str(fault.get("file") or ""),
        int(fault.get("line") or 0),
        str(fault.get("message") or ""),
    )


def _session(result, directory):
    """A REPL lifecycle result as a contract `ReplSession`, its detail in plain words."""
    is_running = result.get("status") == "up"
    happened = str(result.get("result") or "")
    if happened == "status":
        happened = "running" if is_running else "not running"
    detail = [happened.replace("-", " ")]
    if result.get("port"):
        detail.append(f"port {result['port']}")
    if result.get("pid"):
        detail.append(f"pid {result['pid']}")
    return ReplSession(
        LANGUAGE,
        str(result.get("id") or directory),
        str(result.get("cwd") or directory),
        tuple(str(part) for part in result.get("cmd") or ()),
        is_running,
        " · ".join(part for part in detail if part),
    )


def _refused(code, problem, why, root):
    """The answer for code the REPL did not get: where it stops reading, and why."""
    status = {}
    if bridge.serves(root):
        status = bridge.call("repl", {}, root=root, op="status")
    session = _session(status, root)
    return ReplResult(
        LANGUAGE,
        session.id,
        "",
        "",
        f"Line {problem.line}, column {problem.column}: {problem.message}. "
        "The code was not evaluated.\n"
        f"No safe repair exists: {why}.\n"
        "Balance the delimiters, then evaluate again.",
        0,
        session.is_running,
        code,
    )


class ClojureTools:
    """Format, lint, syntax-check and test Clojure, and evaluate in a project nREPL."""

    def __init__(self, *, workspace_root=Path.cwd):
        """Keep a live root provider. Hosted tools receive the SDK function."""
        self._workspace_root = workspace_root

    def _root(self, cwd, paths=()):
        return _root(cwd, paths, self._workspace_root)

    def format_code(
        self,
        paths: Annotated[Sequence[str], "Files or directories to format."] = (),
        *,
        source: Annotated[str, "Format this text instead of files."] = "",
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
        is_written: Annotated[bool, "Rewrite the files that differ."] = False,
    ) -> FormatResult:
        """Format Clojure with zprint, or cljfmt when the project has no zprint config.

        This changes layout only, not syntax. With source, the formatted text
        comes back and nothing is written. With paths, the result lists the
        files that differ, and is_written rewrites them. Directories are walked.
        With neither, the whole project is checked, or formatted with is_written.
        """
        paths = string_list(paths)
        root = self._root(cwd, paths)
        if source:
            result = bridge.call("format", {"code": source}, root=root)
            text = str(result.get("text") or "")
            added, removed = line_changes(source, text)
            return FormatResult(LANGUAGE, (), (), text, False, added, removed)
        arg = {"paths": list(paths)} if paths else {}
        result = bridge.call("format", {**arg, "write": is_written}, root=root)
        files = result.get("files") or ()
        changed = [one for one in files if one.get("changed")]
        counts = [line_changes(one["before"], one["after"]) for one in changed]
        return FormatResult(
            LANGUAGE,
            tuple(str(one.get("path")) for one in changed),
            tuple(str(one.get("path")) for one in files if not one.get("changed")),
            "",
            any(one.get("wrote") for one in changed),
            sum(added for added, _ in counts),
            sum(removed for _, removed in counts),
        )

    def lint_code(
        self,
        paths: Annotated[Sequence[str], "Files or directories to lint."] = (),
        *,
        source: Annotated[str, "Lint this text instead of files."] = "",
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
    ) -> LintResult:
        """Lint Clojure with clj-kondo, plus the compiler's reflection warnings.

        Findings keep clj-kondo's own rule names. Reflection and boxed-math
        warnings only exist while code is compiled, so the code being linted is
        compiled in a namespace that is thrown away afterwards. With no paths
        and no source, the project's own source roots are linted.
        """
        paths = string_list(paths)
        root = self._root(cwd, paths)
        arg = {"code": source} if source else ({"paths": list(paths)} if paths else {})
        result = bridge.call("lint", arg, root=root)
        findings = tuple(_diagnostic(one) for one in result.get("findings") or ())
        return LintResult.of(LANGUAGE, findings, result.get("files") or 0)

    def run_tests(
        self,
        paths: Annotated[Sequence[str], "Test files, directories or namespaces."] = (),
        *,
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
        include: Annotated[
            Sequence[str], "Run only tests carrying these metadata keys."
        ] = (),
        exclude: Annotated[
            Sequence[str], "Skip tests carrying these metadata keys."
        ] = (),
        namespaces: Annotated[Sequence[str], "Test namespaces to run."] = (),
        vars: Annotated[Sequence[str], "Individual test names to run."] = (),
        aliases: Annotated[Sequence[str], "deps.edn aliases to run under."] = (),
        build: Annotated[str, "shadow-cljs build, for ClojureScript tests."] = "",
        timeout_s: Annotated[int, "Seconds before the run is abandoned."] = 900,
    ) -> TestResult:
        """Run the project's own tests, Lazytest or clojure.test, JVM or ClojureScript.

        The project's test command runs in a clean JVM, unless this session
        already has a REPL for the project, which is reused. The command can
        use the Cognitect test-runner, Kaocha or Lazytest. Selecting a source
        file runs its test namespace. A selection that spans both runtimes is
        refused rather than silently trimmed. Counts are tests, not assertions.
        """
        paths = string_list(paths)
        root = self._root(cwd, paths)
        arg = {}
        if paths:
            arg["paths"] = list(paths)
        listed = {
            "include": include,
            "exclude": exclude,
            "namespaces": namespaces,
            "vars": vars,
            "aliases": aliases,
        }
        for key, value in listed.items():
            if value:
                arg[key] = list(string_list(value))
        if build:
            arg["build"] = build
        started = time.monotonic()
        result = bridge.call("test", arg, root=root, timeout_s=timeout_s)
        elapsed = int((time.monotonic() - started) * 1000)
        failures = tuple(_failure(one) for one in result.get("failures") or ())
        total = int(result.get("total") or result.get("selected") or 0)
        skipped = int(result.get("skipped") or 0)
        failed = int(result.get("fail") or 0)
        # A runner can fail without leaving countable failures — a compile error,
        # a non-zero exit. Its own verdict decides, so a red run never reads green.
        if not result.get("is_pass") and not failed:
            failed = max(len(failures), 1)
        # A run refused before any test started answers only why; that reason
        # leads the output, so the red result explains itself.
        output = str(result.get("output") or "")
        error = str(result.get("error") or "")
        if error and error not in output:
            output = f"{error}\n\n{output}" if output else error
        return TestResult.of(
            LANGUAGE,
            total=max(total, failed),
            # Skipped tests never ran, so `total` already leaves them out.
            passed=max(total, failed) - failed,
            failed=failed,
            skipped=skipped,
            duration_ms=elapsed,
            failures=failures,
            output=output,
        )

    def repl_start(
        self,
        cwd: Annotated[str, "Project directory to run the REPL in."] = "",
        *,
        aliases: Annotated[
            Sequence[str], "deps.edn aliases; dev and test by default."
        ] = (),
    ) -> ReplSession:
        """Start a project nREPL, or keep the live one.

        The REPL is a child of the Clojure process that this extension keeps for
        the project, so it survives between calls. A live REPL is never
        replaced, because its state is the work. Stop it first when you want a
        new classpath.
        """
        root = self._root(cwd)
        arg = {"aliases": list(string_list(aliases))} if aliases else {}
        return _session(bridge.call("repl", arg, root=root, op="start"), root)

    def repl_status(
        self,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
    ) -> ReplSession:
        """Whether this project has a live REPL, and what launched it."""
        root = self._root(cwd)
        return _session(bridge.call("repl", {}, root=root, op="status"), root)

    def repl_stop(
        self,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
        *,
        build: Annotated[str, "Detach this shadow-cljs build instead."] = "",
    ) -> ReplSession:
        """Stop this project's REPL, or detach an nREPL it only attached to.

        Safe when none is running. An external REPL is let go, never killed.
        """
        root = self._root(cwd)
        arg = {"build": build} if build else {}
        return _session(bridge.call("repl", arg, root=root, op="stop"), root)

    def repl_connect(
        self,
        cwd: Annotated[str, "Project directory the external REPL belongs to."] = "",
        *,
        port: Annotated[int, "Port the external nREPL listens on."] = 0,
        host: Annotated[str, "Host it listens on; localhost by default."] = "",
        build: Annotated[str, "shadow-cljs build to evaluate ClojureScript in."] = "",
    ) -> ReplSession:
        """Attach to an nREPL you started yourself, without owning it.

        Give the port, or a shadow-cljs build whose watch publishes its own port.
        An attachment lives beside the managed REPL for the same project, so
        attaching to a ClojureScript build never costs you the JVM REPL.
        """
        root = self._root(cwd)
        arg = {}
        if port:
            arg["port"] = int(port)
        if host:
            arg["host"] = host
        if build:
            arg["build"] = build
        return _session(bridge.call("repl", arg, root=root, op="connect"), root)

    def repl_eval(
        self,
        code: Annotated[str, "Clojure to evaluate."],
        *,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
        ns: Annotated[str, "Namespace to evaluate in."] = "",
        timeout_ms: Annotated[int, "Milliseconds to wait for the answer."] = 30000,
    ) -> ReplResult:
        """Evaluate code in the project's live REPL, keeping its state between calls.

        Start the REPL first: evaluating without one is an error naming the
        project that has none. Reload a changed namespace yourself — a REPL
        serves the code it has loaded. Code with unbalanced delimiters is
        repaired first when its indentation shows the missing closers. When no
        repair is safe, the code is not evaluated and the error says why.
        """
        root = self._root(cwd)
        repairs = ()
        problem = reader.problem(code)
        if problem and problem.message.startswith(UNBALANCED):
            repaired, why = repair.repair_code(code, parses_clean=reader.parses_clean)
            if repaired is None:
                return _refused(code, problem, why, root)
            code, repairs = repaired.source, repaired.notes
        arg = {"code": code, "timeout_ms": int(timeout_ms)}
        if ns:
            arg["ns"] = ns
        result = bridge.call(
            "repl-eval", arg, root=root, timeout_s=max(timeout_ms / 1000 + 30, 60)
        )
        printed = str(result.get("out") or "")
        stderr = str(result.get("err") or "")
        # A REPL prints an exception to stderr and names it separately. A failed
        # evaluation reports that as the error, with the line it points at; a
        # healthy one that merely wrote to stderr keeps it as output.
        broke = bool(result.get("error_message")) or "eval-error" in (
            result.get("status") or ()
        )
        named = " ".join(
            part
            for part in (
                str(result.get("error_message") or ""),
                str(result.get("context") or ""),
            )
            if part.strip()
        )
        timed_out = bool(result.get("timed_out"))
        error = (named or stderr) if broke else ""
        if timed_out and not error:
            error = f"Timed out after {int(timeout_ms)} ms."
        return ReplResult(
            LANGUAGE,
            str(result.get("repl") or root),
            str(result.get("value") or ""),
            printed if broke else printed + stderr,
            error,
            int(result.get("ms") or 0),
            not timed_out,
            str(result.get("code") or code),
            repairs,
        )
