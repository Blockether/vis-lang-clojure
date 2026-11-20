# vis-lang-clojure

Clojure tools for [Vis](https://github.com/Blockether/vis): zprint and cljfmt formatting,
clj-kondo lint, reader syntax checks, test runs and an nREPL.

## Install

```bash
vis-agent extension install blockether/vis-lang-clojure --subdirectory extension --global --trust
```

Install the [Clojure CLI](https://clojure.org/guides/install_clojure) and put it and a JDK on
`PATH`. The first tool call fetches the library from Clojars. Syntax checks and repairs do not
need the CLI or the JDK. Use Vis 0.2.53 or later for toolchain grants.

## Use

| Tool | Purpose |
| --- | --- |
| `clj.format_code` | Format files or a snippet. Set `is_written=True` to write the changes. |
| `clj.lint_code` | Run clj-kondo and report reflection warnings as failures. |
| `clj.run_tests` | Run project tests. Set `coverage=True` to get coverage for Lazytest. |
| `clj.repl_start` | Start a project nREPL for this session. |
| `clj.repl_status` | Check if the project's REPL is active. |
| `clj.repl_connect` | Attach to an nREPL that is already active. |
| `clj.repl_eval` | Evaluate Clojure in the active REPL. |
| `clj.repl_stop` | Stop a managed nREPL or detach from an external nREPL. |

```python
clj.run_tests(["test/app/core_test.clj::adds"])
```

Edit hooks check changed files and repair structural errors when safe. See the
[Clojure toolchain reference](docs/toolchain.md) for supported test runners, syntax checks and
classpath details. Results follow the [language contract](https://github.com/Blockether/vis-lang-interface).

## Development

From the repository root, run the Clojure tests. Then run the Python tests from `extension/`:

```bash
clojure -M:test
cd extension
uv run --with pytest --with pyright==1.1.414 pytest tests -q
```
