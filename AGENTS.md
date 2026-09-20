# vis-lang-clojure

Clojure tools for Vis: zprint/cljfmt, clj-kondo, Lazytest and clojure.test, nREPL. Two halves —
a Clojure library on Clojars and a thin Python extension in `extension/` that runs it.

- Vis has no Clojure contract. `extension/extension.py` is the only file that mentions Vis, and
  results are the contract types from `vis-lang-interface`; extend the contract there rather than
  inventing a second result shape.
- Keep the real work in Clojure. The Python side starts the process, sends a request and maps the
  answer; it must not reimplement formatting, lint, test selection or nREPL.
- `cli.clj` is the protocol boundary: one JSON object per line, `{"id","verb","root","session",
  "op","arg"}` in, `{"id","ok","result"|"error"}` out. Changing a verb or a result key breaks the
  installed extension, so change both sides and their tests together.
- One version for both halves: `VERSION`, `build.clj`, `extension/pyproject.toml` and
  `LIBRARY_VERSION` in `extension/src/vis_lang_clojure/bridge.py` all move together, and the jar
  must be on Clojars before the extension release that pins it.
- Tests are Lazytest, not `clojure.test`: `clojure -M:test`. The Python tests run against a fake
  `clojure` command and need no JVM: `vis-agent python -m pytest extension/tests -q`.
- Formatting is zprint with this repository's `.zprint.edn`; lint is clj-kondo, and reflection
  warnings are errors here because the code has to run in a native image elsewhere.
- No tree-sitter and no syntax highlighting. Delimiter repair is the conservative add-only pass in
  `repair.clj`; it never rewrites code it did not balance.
- Never leave a JVM behind: every REPL and test subprocess is owned, reaped and reported.
