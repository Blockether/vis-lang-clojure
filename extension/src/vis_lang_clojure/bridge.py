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
import itertools
import os
import shlex
import threading

from vis_lang_interface import RuntimeGone, ToolTimeout, run, runtime, tool_path

from vis_lang_clojure import jail

LIBRARY_VERSION = "1.5.0"
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

    Vis' own state directory holds it. A confined tool may write to the
    workspace it was given and to that directory, and to nothing else: a boot
    area under `~/.cache` is outside both, and a resolution run there never
    starts at all.

    Returns:
        A stable directory, created when it is missing. Nothing writes a
        `deps.edn` there, so a resolution run in it sees the library coordinate
        and nothing else, and its `.cpcache` makes every later boot a cache read
        instead of a download.
    """
    base = os.environ.get("VIS_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".vis"
    )
    directory = os.path.join(base, "lang", "vis-lang-clojure", LIBRARY_VERSION)
    os.makedirs(directory, exist_ok=True)
    return directory


def maven_repository():
    """The Maven repository a confined run shares with the person's own tools.

    `MAVEN_LOCAL_REPO` names it when the machine keeps one elsewhere; otherwise
    it is the ordinary `~/.m2/repository`. Sharing it is what makes a confined
    run cheap: the library, its linter and a project's own dependencies are
    already there, and whatever one run downloads the next one finds.

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

    `CLJ_CONFIG` names it when a machine keeps it elsewhere; otherwise it is
    `$XDG_CONFIG_HOME/clojure` where that variable is set, which is the usual
    Linux arrangement, and `~/.clojure` everywhere else. That is the order the
    `clojure` command itself uses, so a run started here reads the configuration
    the person's own terminal reads.

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

    A confined child may write to the workspace it was given and to Vis' own
    state directory, and to nothing else. The shared caches and the person's
    Clojure configuration are outside both, so the extension grants exactly
    those directories on the call that starts the run; the jail refuses
    everything else, unchanged. A configuration directory that is not there is
    left out: a machine that has none needs no grant, and the run reads the
    project's own `deps.edn` as before.

    Returns:
        The directories to grant, as a tuple.
    """
    configuration = user_configuration()
    if os.path.isdir(configuration):
        return (maven_repository(), git_libraries(), configuration)
    return (maven_repository(), git_libraries())


def boot_environment(boot):
    """Everything the Clojure CLI writes while it resolves.

    Its own configuration and classpath cache live in the boot directory: a
    project's ambient Clojure setup then decides nothing about the tools, and a
    confined run may write there, which `$HOME` it may not. Downloaded artifacts
    are different — a coordinate names exactly one file — so they go to the
    shared caches the run is granted.

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
    always has somewhere to write it, but its CONFIGURATION is the person's own:
    the REPLs and test runs this process starts resolve the aliases the person's
    terminal resolves, including those a cross-project `deps.edn` defines. That
    directory is granted to the run, so a confined one reaches it too.

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
        boot = boot_directory()
        coordinate = (
            f'{{:deps {{com.blockether/vis-lang-clojure {{:mvn/version "{LIBRARY_VERSION}"}}}}'
            f' :mvn/local-repo "{maven_repository()}"}}'
        )
        done = run(
            jail.prepared((clojure, "-Sdeps", coordinate, "-Spath"), boot),
            cwd=boot,
            env=boot_environment(boot),
            timeout_s=BOOT_TIMEOUT_S,
            read_write=granted_paths(),
        )
        lines = [line.strip() for line in done.out.splitlines() if line.strip()]
        if not done.is_ok or not lines:
            said = (done.err or done.out).strip()
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


class Process:
    """One live Clojure process, serving one project directory."""

    def __init__(self, root, command):
        self.root = str(root)
        self.command = tuple(command)
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
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

    def stop(self):
        """End the process, and with it every REPL it owns."""
        self.live.stop()

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
