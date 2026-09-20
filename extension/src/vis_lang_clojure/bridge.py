"""The Clojure process this extension talks to.

Everything Clojure happens in `com.blockether/vis-lang-clojure`, the library on
Clojars. This module boots it with the Clojure CLI and speaks its protocol: one
JSON request per line in, one answer per line out.

One process serves one project directory, and it stays alive: an nREPL it
started is its child, so a REPL from one call is still there for the next one.
Closing its stdin is what ends it, which stops every REPL it owns on the way
out.

The library's classpath is resolved away from the project, so a project's own
`deps.edn` — an older Clojure, a pinned older clj-kondo, a dependency only its
repository serves — cannot decide what the tools run on, or stop them booting
at all. The project's dependencies stay where they matter: the nREPL and the
test runs the library starts inside that project.
"""

from __future__ import annotations

import atexit
import collections
import itertools
import json
import os
import queue
import shlex
import subprocess
import threading
import time

from vis_lang_interface import ToolMissing, ToolTimeout, tool_path

LIBRARY_VERSION = "1.0.2"
"""Release of `com.blockether/vis-lang-clojure` this glue speaks to."""

MAIN = "com.blockether.vis.lang.clojure.cli"
"""Namespace the Clojure CLI runs."""

BOOT_TIMEOUT_S = 300.0
"""Seconds the first call waits: a cold Maven cache downloads the library."""

DEFAULT_TIMEOUT_S = 900.0
"""Seconds any later call waits, long enough for a project's own test run."""

STDERR_TAIL_LINES = 40
"""How many of the process's last stderr lines a failure hands back."""

SESSION = "vis-lang-clojure"
"""Owner the library files its REPLs under."""


class ClojureError(RuntimeError):
    """The Clojure side refused a call, with the reason it gave."""


def boot_directory():
    """Where the library's classpath is resolved — never the project.

    Returns:
        A stable cache directory, created when it is missing. Nothing writes a
        `deps.edn` there, so a resolution run in it sees the library coordinate
        and nothing else, and its `.cpcache` makes every later boot a cache read
        instead of a download.
    """
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    directory = os.path.join(base, "vis-lang-clojure", LIBRARY_VERSION)
    os.makedirs(directory, exist_ok=True)
    return directory


def java_command():
    """The JVM that runs the library: the one `JAVA_HOME` names, else PATH's.

    Raises:
        ToolMissing: There is no JDK to run.
    """
    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        java = os.path.join(home, "bin", "java")
        if os.access(java, os.X_OK):
            return java
    return tool_path("java", "Install a JDK, or point JAVA_HOME at one.")


_CLASSPATH = None
_CLASSPATH_LOCK = threading.Lock()


def library_classpath(refresh=False):
    """The released library's classpath, resolved once per run.

    The Clojure CLI reads the `deps.edn` of the directory it runs in and merges
    it into what it resolves. Resolving HERE, in `boot_directory` instead of the
    project, is what keeps a project's own dependencies out of the library's
    classpath.

    Args:
        refresh: Resolve again instead of answering the memoized classpath.

    Returns:
        The classpath the library runs on.

    Raises:
        ClojureError: Resolution failed; the message carries what it printed.
        ToolMissing: The Clojure CLI is not installed.
        ToolTimeout: Resolution outlasted `BOOT_TIMEOUT_S`.
    """
    global _CLASSPATH
    with _CLASSPATH_LOCK:
        if _CLASSPATH and not refresh:
            return _CLASSPATH
        clojure = tool_path(
            "clojure", "Install it from https://clojure.org/guides/install_clojure."
        )
        coordinate = f'{{:deps {{com.blockether/vis-lang-clojure {{:mvn/version "{LIBRARY_VERSION}"}}}}}}'
        try:
            done = subprocess.run(
                (clojure, "-Sdeps", coordinate, "-Spath"),
                cwd=boot_directory(),
                capture_output=True,
                text=True,
                timeout=BOOT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolTimeout(
                f"resolving com.blockether/vis-lang-clojure {LIBRARY_VERSION}"
                f" took longer than {BOOT_TIMEOUT_S:g}s"
            ) from exc
        lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
        if done.returncode != 0 or not lines:
            said = (done.stderr or done.stdout).strip()
            message = (
                f"could not resolve com.blockether/vis-lang-clojure {LIBRARY_VERSION}"
            )
            raise ClojureError(f"{message}\n{said}" if said else message)
        _CLASSPATH = lines[-1]
        return _CLASSPATH


def boot_command():
    """The command that starts the library.

    The library runs on its OWN classpath, resolved away from the project, while
    the process itself still runs IN the project directory. A project's
    `deps.edn` therefore decides nothing about the tools: an older Clojure, a
    pinned older clj-kondo or a dependency only that project's own repository
    serves can no longer break them, and the project keeps its dependencies
    where they belong — in the nREPL and the test runs the library starts for it.

    Returns:
        Program and arguments. `VIS_LANG_CLOJURE_COMMAND` replaces the whole
        command, which is how you run a checkout instead of the release; an
        override brings its own classpath and is run as written.

    Raises:
        ClojureError: The library's classpath could not be resolved.
        ToolMissing: The Clojure CLI or a JDK is not installed.
        ToolTimeout: Resolution outlasted `BOOT_TIMEOUT_S`.
    """
    override = os.environ.get("VIS_LANG_CLOJURE_COMMAND", "").strip()
    if override:
        return tuple(shlex.split(override))
    return (
        java_command(),
        "-cp",
        library_classpath(),
        "-Dclojure.main.report=stderr",
        "clojure.main",
        "-m",
        MAIN,
    )


class Process:
    """One live Clojure process, serving one project directory."""

    def __init__(self, root, command):
        self.root = str(root)
        self.command = tuple(command)
        self.answers = queue.Queue()
        self.errors = collections.deque(maxlen=STDERR_TAIL_LINES)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        try:
            self.process = subprocess.Popen(
                self.command,
                cwd=self.root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise ToolMissing(f"{self.command[0]} is not on PATH") from exc
        threading.Thread(target=self._read_answers, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()

    @property
    def is_running(self):
        """Whether the process is still alive."""
        return self.process.poll() is None

    def tail(self):
        """The last lines the process wrote to stderr."""
        return "\n".join(self.errors)

    def call(self, request, timeout_s=DEFAULT_TIMEOUT_S):
        """Send one request and wait for the answer to that request.

        Args:
            request: Verb and its argument, without the framing keys.
            timeout_s: Seconds to wait for the answer.

        Returns:
            The answer map, whether it reports success or failure.

        Raises:
            ClojureError: The process stopped before answering.
            ToolTimeout: The deadline passed with no answer.
        """
        with self.lock:
            wanted = str(next(self.ids))
            self._write(dict(request, id=wanted, root=self.root, session=SESSION))
            deadline = time.monotonic() + timeout_s
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ToolTimeout(f"{MAIN} did not answer within {timeout_s:g}s")
                try:
                    answer = self.answers.get(timeout=left)
                except queue.Empty:
                    raise ToolTimeout(
                        f"{MAIN} did not answer within {timeout_s:g}s"
                    ) from None
                if answer is None:
                    raise ClojureError(self._stopped())
                # An answer to a call that timed out earlier is no longer wanted.
                if str(answer.get("id")) == wanted:
                    return answer

    def stop(self):
        """End the process, and with it every REPL it owns."""
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _write(self, payload):
        if not self.is_running:
            raise ClojureError(self._stopped())
        try:
            self.process.stdin.write(json.dumps(payload) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise ClojureError(self._stopped()) from exc

    def _stopped(self):
        tail = self.tail()
        said = f"\n{tail}" if tail else ""
        return f"the Clojure process for {self.root} stopped before answering{said}"

    def _read_answers(self):
        for line in self.process.stdout:
            text = line.strip()
            if not text:
                continue
            try:
                self.answers.put(json.loads(text))
            except ValueError:
                # Not an answer: the JVM printed something of its own.
                self.errors.append(text)
        self.answers.put(None)

    def _read_errors(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())


_PROCESSES = {}
_PROCESSES_LOCK = threading.Lock()


def call(verb, arg=None, *, root, op="", timeout_s=DEFAULT_TIMEOUT_S):
    """Run one verb in the process serving `root`.

    Args:
        verb: `format`, `lint`, `test`, `repl-eval`, `repl` or `ping`.
        arg: The verb's own argument.
        root: Project directory the call is about.
        op: REPL lifecycle op, for the `repl` verb.
        timeout_s: Seconds to wait for the answer.

    Returns:
        The result the library reported.

    Raises:
        ClojureError: The library refused the call, or its process stopped.
        ToolMissing: The Clojure CLI is not installed.
        ToolTimeout: The deadline passed with no answer.
    """
    request = {"verb": verb, "arg": {} if arg is None else arg}
    if op:
        request["op"] = op
    answer = process_for(root).call(request, timeout_s)
    if answer.get("ok"):
        return answer.get("result")
    failure = answer.get("error") or {}
    message = failure.get("message") or f"{verb} failed"
    hint = failure.get("hint")
    raise ClojureError(f"{message} — {hint}" if hint else message)


def process_for(root):
    """The live process for `root`, started and greeted when there is none."""
    directory = str(root)
    with _PROCESSES_LOCK:
        live = _PROCESSES.get(directory)
        if live and live.is_running:
            return live
        started = Process(directory, boot_command())
        _PROCESSES[directory] = started
    try:
        started.call({"verb": "ping", "arg": {}}, BOOT_TIMEOUT_S)
    except (ClojureError, ToolTimeout) as exc:
        stop(directory)
        raise ClojureError(
            f"vis-lang-clojure did not start in {directory}: {exc}"
        ) from exc
    return started


def stop(root):
    """Stop the process serving `root`. Safe when there is none."""
    with _PROCESSES_LOCK:
        live = _PROCESSES.pop(str(root), None)
    if live:
        live.stop()


def stop_all():
    """Stop every process this extension started."""
    with _PROCESSES_LOCK:
        live = list(_PROCESSES.values())
        _PROCESSES.clear()
    for process in live:
        process.stop()


atexit.register(stop_all)
