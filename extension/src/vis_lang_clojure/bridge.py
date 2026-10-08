"""The Clojure process this extension talks to.

Everything Clojure happens in `com.blockether/vis-lang-clojure`, the library on
Clojars. This module boots it with the Clojure CLI and speaks its protocol: one
JSON request per line in, one answer per line out.

One process serves one project directory, and it stays alive. Each nREPL it
starts is its child, so a REPL from one call is still there for the next call.
The process holds its own stdin open, so it also outlives a sandbox restart: the
next Python process attaches to it under the same shell id. Only `stop` and the
end of the session end it, and every REPL it owns stops with it.

The process gives each verb a lane. Calls in one lane wait for each other, and
calls in different lanes run at the same time: a lint or a format does not wait
for a test run.

The library's classpath is resolved outside the project. A project's own
`deps.edn` cannot choose what the tools run on or stop them from starting. This
holds for an older Clojure, a pinned older clj-kondo, or a dependency that only
the project's repository serves. The project's dependencies stay where they
matter: in the nREPL and the test runs that the library starts inside that
project.
"""

from __future__ import annotations

import atexit
import hashlib
import itertools
import os
import re
import secrets
import shlex
import threading

import blockether.vis.extension as vis
from vis_lang_interface import RuntimeGone, ToolTimeout, run, runtime, tool_path
from vis_lang_interface.process import shell_call

from vis_lang_clojure import jail

LIBRARY_VERSION = "1.15.0"
"""Release of `com.blockether/vis-lang-clojure` this glue speaks to."""

MAIN = "com.blockether.vis.lang.clojure.cli"
"""Namespace the Clojure CLI runs."""

BOOT_TIMEOUT_S = 300
"""Seconds the first call waits: a cold Maven cache downloads the library."""

DEFAULT_TIMEOUT_S = 900.0
"""Seconds any later call waits, long enough for a project's own test run."""

STDERR_TAIL_LINES = 40
"""How many of the process's last stderr lines a failure hands back."""

SESSION = "vis-lang-clojure"
"""Owner the library files its REPLs under."""

RESTARTABLE = frozenset({"ping", "format", "lint"})
"""Verbs a fresh process may be asked again when one died before answering.

Each of them reads or rewrites files and runs none of the project's own code,
so a repeat costs a boot and nothing else. `test` and the REPL verbs run that
code, and asking twice could run it twice."""


class ClojureError(RuntimeError):
    """The Clojure side refused a call, with the reason it gave."""


class ClojureStopped(ClojureError):
    """The process died before it answered, so there is no answer to report."""


def boot_directory():
    """Where the library's classpath is resolved — never the project.

    Vis' own state directory holds it. A confined tool can write only to its
    workspace and to that directory. A boot area under `~/.cache` is outside
    both, so a resolution run there never starts.

    Returns:
        A stable directory, created when it is missing. Nothing writes a
        `deps.edn` there, so a resolution run in it sees only the library
        coordinate. Its `.cpcache` makes every later boot a cache read instead
        of a download.
    """
    base = os.environ.get("VIS_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".vis"
    )
    directory = os.path.join(base, "lang", "vis-lang-clojure", LIBRARY_VERSION)
    os.makedirs(directory, exist_ok=True)
    return directory


def maven_repository():
    """The Maven repository a confined run shares with the person's own tools.

    `MAVEN_LOCAL_REPO` names it when the machine keeps one elsewhere. Otherwise
    it is the usual `~/.m2/repository`. Sharing it makes a confined run cheap.
    The library, its linter and a project's own dependencies are already there.
    Whatever one run downloads, the next run finds.

    Returns:
        The repository directory.
    """
    override = os.environ.get("MAVEN_LOCAL_REPO", "").strip()
    return override or os.path.join(os.path.expanduser("~"), ".m2", "repository")


def git_libraries():
    """Where dependencies fetched from git are cached, shared for that reason.

    Returns:
        The cache directory `GITLIBS` names, else the ordinary `~/.gitlibs`.
    """
    override = os.environ.get("GITLIBS", "").strip()
    return override or os.path.join(os.path.expanduser("~"), ".gitlibs")


def user_configuration():
    """The person's own tools.deps configuration, found the way the CLI finds it.

    `CLJ_CONFIG` names it when a machine keeps it elsewhere. Otherwise it is
    `$XDG_CONFIG_HOME/clojure` when that variable is set, as is usual on Linux.
    In all other cases it is `~/.clojure`. The `clojure` command uses the same
    order, so a run started here reads the same configuration as the person's
    own terminal.

    Returns:
        The configuration directory, whether or not it exists.
    """
    override = os.environ.get("CLJ_CONFIG", "").strip()
    if override:
        return override
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return os.path.join(xdg, "clojure")
    return os.path.join(os.path.expanduser("~"), ".clojure")


def granted_paths():
    """Paths outside the session that every Clojure run is handed.

    A confined child can write only to its workspace and to Vis' own state
    directory. The shared caches and the person's Clojure configuration are
    outside both. The extension grants exactly those directories on the call
    that starts the run. The jail still refuses everything else. A configuration
    directory that does not exist is left out. A machine without one needs no
    grant, and the run reads the project's own `deps.edn` as before.

    Returns:
        The directories to grant, as a tuple.
    """
    configuration = user_configuration()
    if os.path.isdir(configuration):
        return (maven_repository(), git_libraries(), configuration)
    return (maven_repository(), git_libraries())


def boot_environment(boot):
    """Everything the Clojure CLI writes while it resolves.

    Its own configuration and classpath cache live in the boot directory. As a
    result, the Clojure setup around a project decides nothing about the tools.
    A confined run can also write there, but not in `$HOME`. Downloaded
    artifacts are different, because a coordinate names exactly one file. They
    go to the shared caches that the run is granted.

    Args:
        boot: The boot directory.

    Returns:
        The environment delta for the resolution run.
    """
    return {
        "CLJ_CONFIG": os.path.join(boot, "config"),
        "CLJ_CACHE": os.path.join(boot, "cpcache"),
        "GITLIBS": git_libraries(),
        jail.REPOSITORY_VARIABLE: maven_repository(),
    }


def project_environment(boot):
    """The environment of the process that serves ONE project.

    Its classpath cache stays where the boot run keeps one, so a confined run
    always has a place to write it. Its CONFIGURATION, however, is the person's
    own. The REPLs and test runs that this process starts resolve the same
    aliases as the person's terminal. This includes aliases that a cross-project
    `deps.edn` defines. That directory is granted to the run, so a confined run
    can reach it too.

    Args:
        boot: The boot directory.

    Returns:
        The environment delta for the project's process.
    """
    return {**boot_environment(boot), "CLJ_CONFIG": user_configuration()}


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
        ClojureError: Resolution failed. The message includes its output.
        ToolMissing: The Clojure CLI is not installed.
        ToolTimeout: Resolution took longer than `BOOT_TIMEOUT_S`.
    """
    global _CLASSPATH
    with _CLASSPATH_LOCK:
        if _CLASSPATH and not refresh:
            return _CLASSPATH
        clojure = tool_path(
            "clojure", "Install it from https://clojure.org/guides/install_clojure."
        )
        boot = boot_directory()
        coordinate = (
            f'{{:deps {{com.blockether/vis-lang-clojure {{:mvn/version "{LIBRARY_VERSION}"}}}}'
            f' :mvn/local-repo "{maven_repository()}"}}'
        )
        vis.log(
            "info",
            f"vis-lang-clojure: library classpath resolution started version={LIBRARY_VERSION}",
        )
        try:
            done = run(
                jail.prepared((clojure, "-Sdeps", coordinate, "-Spath"), boot),
                cwd=boot,
                env=boot_environment(boot),
                timeout_s=BOOT_TIMEOUT_S,
                read_write=granted_paths(),
            )
        except ToolTimeout:
            vis.log(
                "warn",
                f"vis-lang-clojure: library classpath resolution status=timeout timeout_s={BOOT_TIMEOUT_S:g}",
            )
            raise
        lines = [line.strip() for line in done.out.splitlines() if line.strip()]
        if not done.is_ok or not lines:
            said = (done.err or done.out).strip()
            mentions_403 = "true" if re.search(r"(?<!\d)403(?!\d)", said) else "false"
            vis.log(
                "warn",
                "vis-lang-clojure: library classpath resolution "
                f"status=failed exit_code={done.exit_code} duration_ms={done.duration_ms} output_mentions_403={mentions_403}",
            )
            message = (
                f"could not resolve com.blockether/vis-lang-clojure {LIBRARY_VERSION}"
            )
            raise ClojureError(f"{message}\n{said}" if said else message)
        _CLASSPATH = lines[-1]
        vis.log(
            "info",
            f"vis-lang-clojure: library classpath resolution status=ok duration_ms={done.duration_ms}",
        )
        return _CLASSPATH


def boot_command():
    """The command that starts the library.

    The library runs on its OWN classpath, resolved outside the project. The
    process itself still runs IN the project directory. So a project's
    `deps.edn` decides nothing about the tools. An older Clojure, a pinned older
    clj-kondo or a dependency that only the project's own repository serves
    cannot break them. The project keeps its dependencies where they belong: in
    the nREPL and the test runs that the library starts for it.

    Returns:
        Program and arguments. `VIS_LANG_CLOJURE_COMMAND` replaces the whole
        command, so you can run a checkout instead of the release. An override
        brings its own classpath and runs as written.

    Raises:
        ClojureError: The library's classpath could not be resolved.
        ToolMissing: The Clojure CLI or a JDK is not installed.
        ToolTimeout: Resolution outlasted `BOOT_TIMEOUT_S`.
    """
    boot = boot_directory()
    override = os.environ.get("VIS_LANG_CLOJURE_COMMAND", "").strip()
    if override:
        return jail.prepared(tuple(shlex.split(override)), boot)
    return jail.prepared(
        (
            java_command(),
            "-cp",
            library_classpath(),
            "-Dclojure.main.report=stderr",
            "clojure.main",
            "-m",
            MAIN,
        ),
        boot,
    )


def shell_id(root):
    """The shell id of the process that serves `root`.

    The id keeps the process across a sandbox restart: the next process that
    loads this extension attaches to it, with every REPL it owns.
    """
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"vis-lang-clojure-{digest}"


class Process:
    """One live Clojure process, serving one project directory."""

    def __init__(self, root, command):
        self.root = str(root)
        self.command = tuple(command)
        self.ids = itertools.count(1)
        # A kept process can still hold answers to an earlier Python process's
        # calls, so every id carries a prefix of this object's own.
        self.prefix = secrets.token_hex(4)
        # Calls in one lane wait for each other. Until the greeting names the
        # lanes, and for a library that names none, every verb shares one lock.
        self.lock = threading.Lock()
        self.lanes = {}
        # The process serves ONE project, and it resolves that project's own
        # dependencies for the test runs and REPLs it starts: it needs the same
        # shared caches the boot resolution used, the person's own Clojure
        # configuration, and the grants that reach both.
        self.live = runtime.start(
            self.command,
            cwd=self.root,
            env=project_environment(boot_directory()),
            read_write=granted_paths(),
            name="clojure",
            shell_id=shell_id(self.root),
        )

    @property
    def is_running(self):
        """Whether the process is still alive."""
        return self.live.is_running

    def tail(self):
        """The last lines the process wrote for itself."""
        return "\n".join(self.live.log_tail(STDERR_TAIL_LINES))

    def call(self, request, timeout_s=DEFAULT_TIMEOUT_S):
        """Send one request and wait for the answer to that request.

        Calls in one lane run one at a time, and calls in different lanes run at
        the same time, so a lint or a format does not wait for a test run. A call
        waits at most `timeout_s` for the call before it in its lane to finish,
        and then at most `timeout_s` for its own answer, so a short call never
        waits out a long test run.

        Args:
            request: Verb and its argument, without the framing keys.
            timeout_s: Seconds to wait for the process, and again for the answer.

        Returns:
            The answer map, whether it reports success or failure.

        Raises:
            ClojureError: The process stopped before answering.
            ToolTimeout: The lane stayed busy, or no answer came in time.
        """
        lock = self.lanes.get(str(request.get("verb")), self.lock)
        if not lock.acquire(timeout=timeout_s):
            raise ToolTimeout(
                f"{MAIN} stayed busy with another call for {timeout_s:g}s"
            )
        try:
            wanted = f"{self.prefix}-{next(self.ids)}"
            payload = dict(request, id=wanted, root=self.root, session=SESSION)
            try:
                # An answer to a call that timed out earlier is no longer
                # wanted, so this waits for the id it just sent.
                return self.live.request(payload, timeout_s, wants=wanted)
            except RuntimeGone as exc:
                raise ClojureStopped(self._stopped()) from exc
            except TimeoutError as exc:
                raise ToolTimeout(
                    f"{MAIN} did not answer within {timeout_s:g}s"
                ) from exc
        finally:
            lock.release()

    def greet(self):
        """Ping the process, and learn the lane of each verb.

        Each lane gets its own lock. A library that names no lanes keeps one
        shared lock, so it answers one call at a time.

        Raises:
            ClojureError: The process stopped before answering.
            ToolTimeout: No answer came within `BOOT_TIMEOUT_S`.
        """
        answer = self.call({"verb": "ping", "arg": {}}, BOOT_TIMEOUT_S)
        result = answer.get("result")
        named = result.get("lanes") if isinstance(result, dict) else None
        if not isinstance(named, dict):
            return
        locks = {}
        self.lanes = {
            str(verb): locks.setdefault(str(lane), threading.Lock())
            for verb, lane in named.items()
        }

    def stop(self):
        """End the process, and with it every REPL it owns."""
        self.live.stop()

    def detach(self):
        """Leave the process and its REPLs running for the next Python process."""
        self.live.detach()

    def _stopped(self):
        tail = self.tail()
        said = f"\n{tail}" if tail else ""
        return f"the Clojure process for {self.root} stopped before answering{said}"


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
    try:
        answer = process_for(root).call(request, timeout_s)
    except ClojureStopped:
        if verb not in RESTARTABLE:
            raise
        # A process can be killed while it works: a sandbox reclaiming the
        # process tree of the call that spawned it, or a machine short of
        # memory picking the JVM. For these verbs a fresh process is asked the
        # same thing once more instead of handing back a failure nobody caused.
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
        started.greet()
    except (ClojureError, ToolTimeout) as exc:
        stop(directory)
        raise ClojureError(
            f"vis-lang-clojure did not start in {directory}: {exc}"
        ) from exc
    return started


def serves(root):
    """True when a live process serves `root`. It never starts one.

    A process kept from before a sandbox restart counts. The next call attaches
    to it.
    """
    with _PROCESSES_LOCK:
        live = _PROCESSES.get(str(root))
    if live is not None:
        return live.is_running
    try:
        kept = shell_call()({"op": "logs", "id": shell_id(root), "offset": -1})
    except Exception:
        return False
    return str(kept.get("status")) == "running"


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


def detach_all():
    """Leave every process running, kept for the next Python process.

    A sandbox restart ends this Python process. The REPLs live on, and the next
    call attaches to them. `stop` and the end of the session stop them.
    """
    with _PROCESSES_LOCK:
        live = list(_PROCESSES.values())
        _PROCESSES.clear()
    for process in live:
        process.detach()


atexit.register(detach_all)
