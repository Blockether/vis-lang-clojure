"""The Clojure tools this extension exports.

Ordinary Python: every method takes plain arguments and returns a contract
result from `vis_lang_interface`, so the same calls work in a script, in a test
and from Vis. The work itself happens in the Clojure library, one JSON request
away.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from vis_lang_interface import (
    Diagnostic,
    FormatResult,
    LintResult,
    ReplResult,
    ReplSession,
    TestFailure,
    TestResult,
    project_root,
)

from vis_lang_clojure import bridge

LANGUAGE = "clojure"

MARKERS = (
    "deps.edn",
    "project.clj",
    "shadow-cljs.edn",
    "bb.edn",
    "build.boot",
    ".git",
)

LEVELS = ("error", "warning", "info")


def _root(cwd, paths=()):
    """The project directory a call runs in."""
    start = cwd or (paths[0] if paths else Path.cwd())
    return str(project_root(start, MARKERS))


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
    """A REPL lifecycle result as a contract `ReplSession`."""
    detail = [str(result.get("result") or "")]
    if result.get("port"):
        detail.append(f"port {result['port']}")
    if result.get("pid"):
        detail.append(f"pid {result['pid']}")
    return ReplSession(
        LANGUAGE,
        str(result.get("id") or directory),
        str(result.get("cwd") or directory),
        tuple(str(part) for part in result.get("cmd") or ()),
        result.get("status") == "up",
        " · ".join(part for part in detail if part),
    )


class ClojureTools:
    """Format, lint and test Clojure, and evaluate in a project nREPL."""

    def format_code(
        self,
        paths: Annotated[list[str], "Files or directories to format in place."] = (),
        *,
        source: Annotated[str, "Format this text instead of files."] = "",
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
    ) -> FormatResult:
        """Format Clojure with zprint, or cljfmt when the project has no zprint config.

        A delimiter you left out is added back first; one you wrote is never
        deleted. With source, the formatted text comes back and nothing is
        written. With paths, those files are rewritten where they differ, and a
        directory is walked; with neither, the whole project is formatted.
        """
        root = _root(cwd, tuple(paths))
        if source:
            result = bridge.call("format", {"code": source}, root=root)
            return FormatResult(LANGUAGE, (), (), str(result.get("text") or ""), False)
        result = bridge.call(
            "format", {"paths": list(paths)} if paths else {}, root=root
        )
        files = result.get("files") or ()
        return FormatResult(
            LANGUAGE,
            tuple(str(one.get("path")) for one in files if one.get("changed")),
            tuple(str(one.get("path")) for one in files if not one.get("changed")),
            "",
            True,
        )

    def lint_code(
        self,
        paths: Annotated[list[str], "Files or directories to lint."] = (),
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
        root = _root(cwd, tuple(paths))
        arg = {"code": source} if source else ({"paths": list(paths)} if paths else {})
        result = bridge.call("lint", arg, root=root)
        findings = tuple(_diagnostic(one) for one in result.get("findings") or ())
        return LintResult.of(LANGUAGE, findings, result.get("files") or 0)

    def run_tests(
        self,
        paths: Annotated[list[str], "Test files, directories or namespaces."] = (),
        *,
        cwd: Annotated[str, "Project directory; inferred from paths when empty."] = "",
        include: Annotated[
            list[str], "Run only tests carrying these metadata keys."
        ] = (),
        exclude: Annotated[list[str], "Skip tests carrying these metadata keys."] = (),
        namespaces: Annotated[list[str], "Test namespaces to run."] = (),
        vars: Annotated[list[str], "Individual test names to run."] = (),
        aliases: Annotated[list[str], "deps.edn aliases to run under."] = (),
        build: Annotated[str, "shadow-cljs build, for ClojureScript tests."] = "",
        timeout_s: Annotated[int, "Seconds before the run is abandoned."] = 900,
    ) -> TestResult:
        """Run the project's own tests, Lazytest or clojure.test, JVM or ClojureScript.

        The project's test command runs in a clean JVM, unless this session
        already has a REPL for the project, which is reused. Selecting a source
        file runs its test namespace. A selection that spans both runtimes is
        refused rather than silently trimmed.
        """
        root = _root(cwd, tuple(paths))
        arg = {}
        if paths:
            arg["paths"] = list(paths)
        if include:
            arg["include"] = list(include)
        if exclude:
            arg["exclude"] = list(exclude)
        if namespaces:
            arg["namespaces"] = list(namespaces)
        if vars:
            arg["vars"] = list(vars)
        if aliases:
            arg["aliases"] = list(aliases)
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
        return TestResult.of(
            LANGUAGE,
            total=max(total, failed),
            passed=max(max(total, failed) - failed - skipped, 0),
            failed=failed,
            skipped=skipped,
            duration_ms=elapsed,
            failures=failures,
            output=str(result.get("output") or ""),
        )

    def repl_start(
        self,
        cwd: Annotated[str, "Project directory to run the REPL in."] = "",
        *,
        aliases: Annotated[
            list[str], "deps.edn aliases; dev and test by default."
        ] = (),
    ) -> ReplSession:
        """Start a project nREPL, or keep the live one.

        The REPL is a child of the Clojure process this extension keeps for the
        project, so it survives between calls. A live REPL is never replaced,
        because its state is the work; stop it first when you want a new
        classpath.
        """
        root = _root(cwd)
        arg = {"aliases": list(aliases)} if aliases else {}
        return _session(bridge.call("repl", arg, root=root, op="start"), root)

    def repl_status(
        self,
        cwd: Annotated[str, "Project directory the REPL runs in."] = "",
    ) -> ReplSession:
        """Whether this project has a live REPL, and what launched it."""
        root = _root(cwd)
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
        root = _root(cwd)
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
        root = _root(cwd)
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
        serves the code it has loaded.
        """
        root = _root(cwd)
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
        return ReplResult(
            LANGUAGE,
            str(result.get("repl") or root),
            str(result.get("value") or ""),
            printed if broke else printed + stderr,
            (named or stderr) if broke else "",
            int(result.get("ms") or 0),
            not result.get("timed_out"),
        )
