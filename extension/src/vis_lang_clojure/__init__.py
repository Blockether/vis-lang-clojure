"""Clojure tools for Vis, run by the Clojure CLI.

Nothing here reimplements Clojure tooling in Python. The work is done by
`com.blockether/vis-lang-clojure` on Clojars — zprint, cljfmt, clj-kondo,
Lazytest, clojure.test, shadow-cljs and nREPL, all in one JVM — and this package
is the glue that starts it, asks it for something and returns the answer in the
shapes from `vis-lang-interface`.
"""

from vis_lang_clojure.bridge import LIBRARY_VERSION, ClojureError
from vis_lang_clojure.tools import ClojureTools

__all__ = ["ClojureError", "ClojureTools", "LIBRARY_VERSION"]
