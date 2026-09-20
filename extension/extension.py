"""Vis entrypoint. The tools themselves live in vis_lang_clojure."""

import blockether.vis.extension as vis
from vis_lang_interface import presentation

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

vis.register_extension(
    vis.Extension(
        name="vis-lang-clojure",
        description="Clojure tools: zprint and cljfmt formatting, clj-kondo lint, test runs and an nREPL.",
        version="1.2.1",
        alias="clj",
        symbols=[vis.Symbol(ClojureTools(), name="clj")],
    )
)
