"""Vis entrypoint. The tools themselves live in vis_lang_clojure."""

from typing import Literal

import blockether.vis.extension as vis
from vis_lang_interface import presentation, prompt
from vis_lang_interface.syntax import SyntaxGuard

from vis_lang_clojure.repair import repair_source
from vis_lang_clojure.tools import (
    LANGUAGE,
    SYNTAX_SUFFIXES,
    ClojureTools,
    _check_syntax,
)

# The tags that this extension binds. Older SDKs do not export `vis.SymbolTag`,
# so this module keeps its own tag type.
_Tag = Literal["observation", "mutation", "verification"]


def _bind(
    name, label, build, *, tag: _Tag = "observation", show_start=True, describe=None
):
    """Attach one Activity presentation to a method of ClojureTools."""
    setattr(
        ClojureTools,
        name,
        vis.method(
            tag=tag,
            activity=presentation.activity(
                label, build, show_start=show_start, describe=describe
            ),
        )(getattr(ClojureTools, name)),
    )


def _knows(tag: _Tag) -> bool:
    """Whether this Vis host accepts `tag`. Older hosts refuse `verification`."""
    try:
        vis.method(tag=tag)
    except ValueError:
        return False
    return True


# Lint and test runs check work; a host that lacks the tag records them as reads.
_CHECK: _Tag = "verification" if _knows("verification") else "observation"


_bind(
    "format_code",
    "Format Clojure code",
    lambda result: presentation.format_presentation("Format Clojure code", result),
    tag="mutation",
    show_start=False,
)
_bind(
    "lint_code",
    "Lint Clojure code",
    lambda result: presentation.lint_presentation("Lint Clojure code", result),
    tag=_CHECK,
)
_bind(
    "run_tests",
    "Run Clojure tests",
    lambda result: presentation.test_presentation("Run Clojure tests", result),
    tag=_CHECK,
)
_bind(
    "repl_start",
    "Start Clojure REPL",
    lambda result: presentation.session_presentation("Start Clojure REPL", result),
    tag="mutation",
)
_bind(
    "repl_status",
    "Check Clojure REPL",
    lambda result: presentation.session_presentation("Check Clojure REPL", result),
    show_start=False,
)
_bind(
    "repl_stop",
    "Stop Clojure REPL",
    lambda result: presentation.session_presentation("Stop Clojure REPL", result),
    tag="mutation",
    show_start=False,
)
_bind(
    "repl_connect",
    "Attach to Clojure REPL",
    lambda result: presentation.session_presentation("Attach to Clojure REPL", result),
    tag="mutation",
)
_bind(
    "repl_eval",
    "Evaluate in Clojure REPL",
    lambda result: presentation.repl_presentation("Evaluate in Clojure REPL", result),
    tag="mutation",
    describe=presentation.code_argument("clojure"),
)

PROMPT = prompt.routing(
    "Clojure",
    "clj",
    (
        "format_code",
        "lint_code",
        "run_tests",
        "repl_start",
        "repl_status",
        "repl_connect",
        "repl_eval",
        "repl_stop",
    ),
    notes=(
        "`clj.repl_eval` needs a REPL from `clj.repl_start`, or an nREPL elsewhere that"
        " `clj.repl_connect` attached.",
        "`clj.repl_eval` puts back missing closers that the indentation shows and lists them in"
        " `repairs`. With no safe repair, it evaluates nothing and reports where the code stops"
        " reading.",
        "If this session's REPL is live, `clj.run_tests` uses the code that REPL loaded; else it"
        " starts a clean JVM. With a live REPL, first reload changed namespaces with"
        " `(require ... :reload)` or stop the REPL.",
        "`clj.lint_code` runs clj-kondo and also reports compiler reflection warnings as failures.",
        "`clj.format_code` changes layout only. Structural repair runs through operation hooks.",
        "`patch` validates repairs before one atomic write and reports each correction.",
        "Syntax checks and repairs run in local Python, without a JVM.",
        "Python file writes are checked after the block, without a transaction or rollback.",
        "Repairs appear in `clojure_syntax_repairs`; unresolved files stay in `clojure_syntax_errors`.",
    ),
)

# Keeps the files the Clojure reader reads parseable, across patches and Python writes.
GUARD = SyntaxGuard(LANGUAGE, SYNTAX_SUFFIXES, _check_syntax, repair=repair_source)


vis.register_extension(
    vis.Extension(
        name="vis-lang-clojure",
        description="Clojure tools: zprint and cljfmt formatting, clj-kondo lint, reader syntax checks, test runs and an nREPL.",
        version="1.12.1",
        alias="clj",
        symbols=[
            vis.Symbol(ClojureTools(workspace_root=vis.workspace_root), name="clj")
        ],
        prompt=PROMPT,
        op_hooks=GUARD.op_hooks(),
        ctx=GUARD.ctx,
    )
)
