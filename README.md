# vis-lang-clojure

Clojure tools for [Vis](https://github.com/Blockether/vis): zprint/cljfmt formatting, clj-kondo
lint with the reflection and boxed-math pass, Lazytest and clojure.test runs, and nREPL evaluation
with a managed REPL.

Vis knows nothing about Clojure. This extension does. The work happens in a Clojure library
published to Clojars, so it runs on the JVM with your project's own classpath, deps.edn aliases,
`.zprint.edn` and `.clj-kondo` configuration. A small Python package starts that library through
the `clojure` CLI and speaks to it over stdio.

## Install

```bash
vis-agent extension install Blockether/vis-lang-clojure --global --trust
```

You need the [Clojure CLI](https://clojure.org/guides/install_clojure) and a JDK on `PATH`. The
library itself is fetched from Clojars the first time a tool runs.

## Tools

```python
clj.format_code(["src"])                         # zprint, or cljfmt when that is the project's config
clj.format_code(source="(defn f [x](* x 2))")    # format a snippet, nothing written
clj.lint_code(["src"])                           # clj-kondo + reflection warnings
clj.run_tests(["test"])                          # Lazytest and clojure.test
clj.run_tests(["test/app/core_test.clj::adds"])  # one test
clj.repl_start(cwd="~/app", aliases=["dev"])     # a project nREPL for this session
clj.repl_eval("(+ 1 1)", cwd="~/app")            # evaluate in that REPL
clj.repl_connect(cwd="~/app", port=7888)         # or attach to an nREPL you already run
clj.repl_stop(cwd="~/app")
```

Tests reuse a running REPL when there is one, and otherwise run in a clean JVM.

## How it works

`extension/` is the Python side. It resolves the library's classpath once, in a cache directory
of its own —

```
clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:mvn/version "1.0.2"}}}' -Spath
```

— and then runs

```
java -cp "$classpath" clojure.main -m com.blockether.vis.lang.clojure.cli
```

once per project root, inside that project. Resolving away from the project is deliberate: your
`deps.edn` decides what your REPL and your test runs see, never what these tools themselves run
on, so a project pinning an older Clojure — or one whose dependencies come from a repository the
tools cannot reach — still formats, lints and tests. The two sides exchange one JSON object per
line with each other. The Clojure side
(`src/com/blockether/vis/lang/clojure/`) does the real work and returns plain data, which the
Python side turns into the result types from
[vis-lang-interface](https://github.com/Blockether/vis-lang-interface).

## Development

```bash
clojure -M:test                                  # the Clojure library
vis-agent python -m pytest extension/tests -q    # the Python glue
```

To run the extension against this checkout instead of the released jar:

```bash
export VIS_LANG_CLOJURE_COMMAND="clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:local/root \"$PWD\"}}}' -M -m com.blockether.vis.lang.clojure.cli"
```

That override is run exactly as written, in the project directory, so — unlike the default
command — it takes its classpath from that project's `deps.edn` too.
