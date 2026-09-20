# vis-lang-clojure

The Clojure language pack for [Vis](https://github.com/Blockether/vis). It registers with
[vis-lang-interface](https://github.com/Blockether/vis-lang-interface) and serves Clojure,
ClojureScript and EDN:

- delimiter and literal verdicts, plus add-only delimiter repair, for the write gate,
- `format_code` through zprint and cljfmt,
- `lint_code` through clj-kondo, including the reflection and boxed-math pass,
- `run_tests` for Lazytest and clojure.test, with shadow-cljs builds,
- `repl_eval` over nREPL, with a managed REPL and its shadow-cljs sibling.

## Status

Early: extraction from the Vis engine is in progress. Until the first release, pin by commit.
