# vis-lang-clojure

Clojure tools for Vis: zprint/cljfmt, clj-kondo, Lazytest and clojure.test, nREPL. It has two parts:
a Clojure library on Clojars and a thin Python extension in `extension/` that runs it.

- Vis has no Clojure contract. Only `extension/extension.py` mentions Vis. Results are the contract types from `vis-lang-interface`; extend the contract there, not with a second result shape.
- Keep the real work in Clojure. The Python side starts the process, sends a request and maps the answer. It must not reimplement formatting, lint, test selection or nREPL.
- `cli.clj` is the protocol boundary: one JSON object per line, `{"id","verb","root","session","op","arg"}` in and `{"id","ok","result"|"error"}` out. A changed verb or result key breaks the installed extension. Change both sides and their tests together.
- Both parts have one version. Change `VERSION`, `build.clj`, `extension/pyproject.toml` and `LIBRARY_VERSION` in `extension/src/vis_lang_clojure/bridge.py` together. Publish the jar on Clojars before the extension release that pins it.
- Write tests with Lazytest, not `clojure.test`, and run them with `clojure -M:test`. The Python tests use a fake `clojure` command and need no JVM: `vis-agent python -m pytest extension/tests -q`.
- ClojureScript tests run only through a shadow-cljs build (`shadow_cljs.clj`), and only where a `shadow-cljs.edn` claims them. `clojure -M:test` cannot load a `*_test.cljs`. In a bare run, the project's own runner handles a test outside every shadow-cljs project.
- shadow-cljs can exit with zero without compiling anything, and its Node autorun drops the test exit status. Judge a run by its printed counts, not only by its exit code. `test_runner.clj` passes a run only on a complete summary with at least one test and no failures or errors.
- Format with zprint and this repository's `.zprint.edn`. Lint with clj-kondo. Reflection warnings are errors here, because the code must run in a native image.
- Do not add tree-sitter or syntax highlighting. Delimiter repair is the conservative add-only pass in `repair.clj`; it never rewrites code that it did not balance.
- Never leave a JVM behind. Own, reap and report every REPL and test subprocess.
