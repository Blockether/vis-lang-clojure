"""Clojure tools for Vis, run by the Clojure CLI.

Nothing here reimplements Clojure tooling in Python. The library
`com.blockether/vis-lang-clojure` on Clojars does the work. It runs zprint,
cljfmt, clj-kondo, Lazytest, clojure.test, shadow-cljs and nREPL in one JVM.
This package starts that JVM, sends it requests and returns the answers in the
shapes from `vis-lang-interface`.
"""

from vis_lang_clojure.bridge import LIBRARY_VERSION, ClojureError
from vis_lang_clojure.tools import ClojureTools

__all__ = ["ClojureError", "ClojureTools", "LIBRARY_VERSION"]
