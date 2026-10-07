# vis-lang-clojure

## When to use

Use this extension to [format, lint, test and evaluate Clojure](#tools) from Vis.
To catch or repair broken source after edits, see [Keep files parseable](#keep-files-parseable).
For the shared language result types, see [vis-lang-interface](https://github.com/Blockether/vis-lang-interface).

Formatting, lint, tests and evaluation use a Clojure library published to Clojars.
Syntax checks and structural repair run locally in Python, without a JVM.

## Install

```bash
vis-agent extension install Blockether/vis-lang-clojure --subdirectory extension --global --trust
```

You need the [Clojure CLI](https://clojure.org/guides/install_clojure) and a JDK on `PATH`. The
library itself is fetched from Clojars the first time a tool runs. Syntax checks and repairs do
not need them.

## Tools

```python
clj.format_code(["src"])                         # check: list the files that need formatting
clj.format_code(["src"], is_written=True)        # zprint, or cljfmt when that is the project's config
clj.format_code(source="(defn f [x](* x 2))")    # format a snippet, nothing written
clj.lint_code(["src"])                           # clj-kondo + reflection warnings
clj.run_tests(["test"])                          # Lazytest, clojure.test, Cognitect test-runner, Kaocha
clj.run_tests(["test/app/core_test.clj::adds"])  # one test
clj.repl_start(cwd="~/app", aliases=["dev"])     # a project nREPL for this session
clj.repl_eval("(+ 1 1)", cwd="~/app")            # evaluate in that REPL
clj.repl_connect(cwd="~/app", port=7888)         # or attach to an nREPL you already run
clj.repl_stop(cwd="~/app")
```

Tests reuse a running REPL when there is one, and otherwise run in a clean JVM.

## Keep files parseable

Syntax checks run automatically through edit hooks, not through a public tool.
The extension checks changed `.clj`, `.cljs`, `.cljc`, `.cljx`, `.bb` and `.edn` files with a
Python port of the Clojure reader. Tests compare its verdicts with Clojure's own reader.
The reader only reads: `#=` and record literals are reported, not evaluated.
Other reader tags stay data, and an alias needs no loaded namespace.
A few extreme inputs, such as very deep nesting, get no verdict and count as readable.

- `format_code` changes layout only. It does not repair broken structure.
- Before a `patch` writes, the guard tries a conservative repair of the complete proposed file.
  It keeps the edited lines, original text and line endings as evidence.
  The reader must accept the candidate before one atomic write. The patch reports each correction.
  If no safe repair exists, a patch that breaks a parseable file is refused.
- After a Python block, the guard checks changed files and can repair them.
  These checks happen after writes. They do not make the block transactional or roll back its effects.
- Before `repl_eval` sends code to the REPL, the reader checks its delimiters.
  If they do not balance, the same conservative repair can change any line of that code.
  The result lists each correction in `repairs`.
  If no safe repair exists, the REPL does not get the code. The error gives the line, the column and the reason.
  Other reader problems go to the REPL unchanged, because the REPL reads `#=` and record literals itself.
- Repair notes and diffs appear in `session["clojure_syntax_repairs"]`.
  Unresolved files stay in `session["clojure_syntax_errors"]` until they parse again.

Most repairs only add missing delimiters. The original text can also prove a narrow closer or quote correction.
A generic deletion, a retyped delimiter or a change to unrelated code is refused.
This workflow needs a Vis host that supports repair decisions from operation hooks.

The guard is `SyntaxGuard` from
[vis-lang-interface](https://github.com/Blockether/vis-lang-interface#keep-source-files-parseable),
which describes the shared rules.

## How it works

`extension/` is the Python side. It resolves the library's classpath once, in a cache directory
of its own —

```
clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:mvn/version "1.14.1"}}}' -Spath
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
(`src/com/blockether/vis/lang/clojure/`) runs the language tools.
The Python side maps results to
[vis-lang-interface](https://github.com/Blockether/vis-lang-interface) types and owns syntax checks and structural repair.

## Inside a Vis sandbox

When Vis confines a session, a tool may write to the workspace and to Vis' own state directory,
and everything it sends leaves through a local proxy that asks for a credential and terminates
TLS with its own certificate authority. Programs that read `https_proxy` need nothing more, but a
JVM takes its proxy from system properties and Maven takes its from `settings.xml`, so a confined
Clojure run would fail to reach Clojars or Maven Central at all.

This extension prepares both before the JVM starts. You do not configure anything: it points the
JVM at the proxy, builds a trust store from the sandbox's certificate bundle, and writes a
`settings.xml` with the proxy and its credential. That generated `settings.xml` lives under
`~/.vis/lang/vis-lang-clojure/<version>/jail`, never in your own `~/.m2`: a file of Vis' making does
not belong in a directory you keep yours in.

The caches are shared on purpose. Together with the workspace and Vis' own state directory, a
confined run is granted your Maven repository (`~/.m2/repository`, or `MAVEN_LOCAL_REPO` when you
keep it elsewhere), your git-dependency cache (`~/.gitlibs`, or `GITLIBS`) and, when it exists, your
tools.deps configuration directory (`CLJ_CONFIG`, else `$XDG_CONFIG_HOME/clojure`, else
`~/.clojure`); everything else is refused. So a confined build downloads only what you do not have
yet, and a REPL or test run started for your project resolves the aliases your own terminal
resolves, including those a global `deps.edn` defines. The tools' own resolution stays out of that:
their configuration and classpath cache live in the jail directory, so nothing a project or your
global setup declares decides what the formatter, the linter or the test runner run on.

Outside a sandbox there is no proxy in the environment, nothing is prepared, and your caches and
configuration are used exactly as before.

## Development

```bash
clojure -M:test                                  # the Clojure library
vis-agent python -m pytest extension/tests -q    # the Python glue
```

To run the extension against this checkout instead of the released jar:

```bash
export VIS_LANG_CLOJURE_COMMAND="clojure -Sdeps '{:deps {com.blockether/vis-lang-clojure {:local/root \"$PWD\"}}}' -M -m com.blockether.vis.lang.clojure.cli"
```

That override brings its own classpath and is run as written, in the project directory, so —
unlike the default command — it takes its classpath from that project's `deps.edn` too.
