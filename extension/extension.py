"""Vis entrypoint. The tools themselves live in vis_lang_clojure."""

import blockether.vis.extension as vis
from vis_lang_interface import presentation, prompt

from vis_lang_clojure.tools import ClojureTools


def _bind(name, label, build, *, tag="observation", show_start=True):
    """Attach one Activity presentation to a method of ClojureTools."""
    setattr(
        ClojureTools,
        name,
        vis.method(
            tag=tag,
            activity=presentation.activity(label, build, show_start=show_start),
        )(getattr(ClojureTools, name)),
    )


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
)
_bind(
    "run_tests",
    "Run Clojure tests",
    lambda result: presentation.test_presentation("Run Clojure tests", result),
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
        "`clj.repl_eval` needs a REPL `clj.repl_start` already started, or `clj.repl_connect`"
        " attached to an nREPL that is running elsewhere.",
        "`clj.run_tests` starts a clean JVM unless this session's REPL is live, in which case it"
        " serves the code that REPL has loaded: reload the namespaces you changed with"
        " `(require ... :reload)` or stop the REPL first.",
        "`clj.lint_code` is clj-kondo and also reports the compiler's reflection warnings,"
        " which this toolchain treats as failures.",
    ),
)


vis.register_extension(
    vis.Extension(
        name="vis-lang-clojure",
        description="Clojure tools: zprint and cljfmt formatting, clj-kondo lint, test runs and an nREPL.",
        version="1.5.2",
        alias="clj",
        symbols=[
            vis.Symbol(ClojureTools(workspace_root=vis.workspace_root), name="clj")
        ],
        prompt=PROMPT,
    )
)
