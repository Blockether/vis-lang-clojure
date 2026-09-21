"""One process per project, and a failure that says what happened."""

import json
import os
import sys

import pytest
from vis_lang_interface import ToolRun, ToolTimeout

from vis_lang_clojure import bridge, jail


def test_a_call_names_the_project_and_its_owner(fake):
    bridge.call("ping", root=fake.cwd)
    request = fake.sent("ping")
    assert request["root"] == fake.cwd
    assert request["session"] == bridge.SESSION


def test_one_process_serves_a_project(fake):
    first = bridge.process_for(fake.cwd)
    bridge.call("ping", root=fake.cwd)
    assert bridge.process_for(fake.cwd) is first


def test_a_stopped_process_is_started_again(fake):
    first = bridge.process_for(fake.cwd)
    bridge.stop(fake.cwd)
    assert first.is_running is False
    assert bridge.process_for(fake.cwd) is not first


def test_a_refusal_carries_the_message_and_the_hint(fake):
    fake.script(
        {
            "lint": {
                "ok": False,
                "error": {"message": "no such path", "hint": "name one that exists"},
            }
        }
    )
    with pytest.raises(
        bridge.ClojureError, match="no such path — name one that exists"
    ):
        bridge.call("lint", {"paths": ["nowhere"]}, root=fake.cwd)


def test_a_process_that_dies_is_reported(fake):
    fake.script({"format": "exit"})
    with pytest.raises(bridge.ClojureError, match="stopped before answering"):
        bridge.call("format", {}, root=fake.cwd)


def test_a_silent_process_times_out(fake):
    fake.script({"format": "silence"})
    with pytest.raises(ToolTimeout, match="did not answer"):
        bridge.call("format", {}, root=fake.cwd, timeout_s=0.5)


def test_a_late_answer_is_not_mistaken_for_the_next_one(fake):
    fake.script(
        {
            "format": "silence",
            "lint": {"ok": True, "result": {"files": 1, "findings": []}},
        }
    )
    with pytest.raises(ToolTimeout):
        bridge.call("format", {}, root=fake.cwd, timeout_s=0.5)
    assert bridge.call("lint", {}, root=fake.cwd) == {"files": 1, "findings": []}


def clojure_shim(
    tmp_path, monkeypatch, *, classpath="/jars/library.jar", code=0, says=""
):
    """Put a `clojure` on PATH that records where it ran, and answer its record.

    The shim prints `classpath` the way `-Spath` does, writes `says` to stderr
    and exits with `code`, so a test can drive both halves of a resolution
    without a JVM.
    """
    record = tmp_path / "resolutions.jsonl"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    shim = binaries / "clojure"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(record)!r}, 'a') as handle:\n"
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:], 'env': {name: os.environ.get(name) for name in ('CLJ_CONFIG', 'CLJ_CACHE', 'GITLIBS', 'VIS_LANG_CLOJURE_MAVEN_REPO')}}) + chr(10))\n"
        f"sys.stderr.write({says!r})\n"
        f"sys.stdout.write({classpath!r} + chr(10))\n"
        f"sys.exit({code})\n",
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", str(binaries), prepend=os.pathsep)
    monkeypatch.setenv("VIS_HOME", str(tmp_path / "vis-home"))
    monkeypatch.delenv("VIS_LANG_CLOJURE_COMMAND", raising=False)
    monkeypatch.setattr(bridge, "_CLASSPATH", None)
    return record


def resolutions(record):
    """Every resolution the shim recorded, newest last."""
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines()]


def test_the_default_command_runs_the_library_on_its_own_classpath(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VIS_HOME", str(tmp_path / "vis-home"))
    monkeypatch.delenv("VIS_LANG_CLOJURE_COMMAND", raising=False)
    monkeypatch.setattr(bridge, "java_command", lambda: "/jdk/bin/java")
    monkeypatch.setattr(bridge, "library_classpath", lambda: "/jars/library.jar")
    command = bridge.boot_command()
    # Confined or not, the JVM starts behind the preamble: only the sandbox
    # knows whether its proxy needs preparing, and only from inside it.
    assert command[1] == jail.preamble(bridge.boot_directory())
    assert command[2:] == (
        "/jdk/bin/java",
        "-cp",
        "/jars/library.jar",
        "-Dclojure.main.report=stderr",
        "clojure.main",
        "-m",
        bridge.MAIN,
    )


def test_the_classpath_is_resolved_outside_the_project(tmp_path, monkeypatch):
    # A project's deps.edn must not reach the library's classpath: the Clojure
    # CLI merges the deps.edn of the directory it runs in, so a project on an
    # older Clojure — or with a dependency only its own repository serves —
    # used to break every Clojure tool before the first call.
    project = tmp_path / "project"
    project.mkdir()
    (project / "deps.edn").write_text(
        '{:deps {org.clojure/clojure {:mvn/version "1.10.3"}}}'
    )
    record = clojure_shim(tmp_path, monkeypatch)
    monkeypatch.chdir(project)

    assert bridge.library_classpath() == "/jars/library.jar"

    ran = resolutions(record)[-1]
    assert os.path.realpath(ran["cwd"]) == os.path.realpath(bridge.boot_directory())
    assert os.path.realpath(ran["cwd"]) != os.path.realpath(project)
    assert "-Spath" in ran["argv"]
    assert f'{{:mvn/version "{bridge.LIBRARY_VERSION}"}}' in " ".join(ran["argv"])
    assert not (project / ".cpcache").exists()


def test_the_classpath_is_resolved_once(tmp_path, monkeypatch):
    record = clojure_shim(tmp_path, monkeypatch)
    assert bridge.library_classpath() == bridge.library_classpath()
    assert len(resolutions(record)) == 1
    assert bridge.library_classpath(refresh=True) == "/jars/library.jar"
    assert len(resolutions(record)) == 2


def test_the_resolution_writes_only_where_a_confined_tool_may(tmp_path, monkeypatch):
    # A confined tool may write to the workspace it was given, to Vis' own
    # directory, and to the paths the call that starts it grants. The CLI's own
    # configuration and classpath cache stay in the boot area; downloaded
    # artifacts go to the shared caches the run is granted, and nothing else of
    # `$HOME` is reachable.
    monkeypatch.setenv("HOME", str(tmp_path / "person"))
    monkeypatch.delenv("MAVEN_LOCAL_REPO", raising=False)
    monkeypatch.delenv("GITLIBS", raising=False)
    record = clojure_shim(tmp_path, monkeypatch)
    assert bridge.library_classpath() == "/jars/library.jar"

    boot = os.path.realpath(bridge.boot_directory())
    assert boot.startswith(os.path.realpath(str(tmp_path / "vis-home")))

    ran = resolutions(record)[-1]
    assert os.path.realpath(ran["cwd"]) == boot
    assert f'"{bridge.maven_repository()}"' in " ".join(ran["argv"])
    for name in ("CLJ_CONFIG", "CLJ_CACHE"):
        assert os.path.realpath(ran["env"][name]).startswith(boot)
    assert ran["env"]["GITLIBS"] == bridge.git_libraries()
    assert ran["env"][jail.REPOSITORY_VARIABLE] == bridge.maven_repository()


def test_the_shared_caches_are_the_ones_a_person_already_has(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "person"))
    monkeypatch.delenv("MAVEN_LOCAL_REPO", raising=False)
    monkeypatch.delenv("GITLIBS", raising=False)
    assert bridge.maven_repository() == str(tmp_path / "person" / ".m2" / "repository")
    assert bridge.git_libraries() == str(tmp_path / "person" / ".gitlibs")
    monkeypatch.setenv("MAVEN_LOCAL_REPO", "/srv/artifacts")
    monkeypatch.setenv("GITLIBS", "/srv/gitlibs")
    # A configuration directory the host happens to keep must not leak into the
    # grants this test is about, so name one that is not there.
    monkeypatch.setenv("CLJ_CONFIG", str(tmp_path / "person" / ".clojure"))
    assert bridge.granted_paths() == ("/srv/artifacts", "/srv/gitlibs")


def test_the_configuration_is_the_one_the_clojure_command_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "person"))
    monkeypatch.delenv("CLJ_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert bridge.user_configuration() == str(tmp_path / "person" / ".clojure")
    # Where XDG_CONFIG_HOME is set, the usual Linux arrangement, the CLI reads
    # its configuration from there instead.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "person" / ".config"))
    assert bridge.user_configuration() == str(
        tmp_path / "person" / ".config" / "clojure"
    )
    monkeypatch.setenv("CLJ_CONFIG", "/srv/clojure")
    assert bridge.user_configuration() == "/srv/clojure"


def test_a_configuration_is_granted_only_when_the_machine_has_one(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MAVEN_LOCAL_REPO", "/srv/artifacts")
    monkeypatch.setenv("GITLIBS", "/srv/gitlibs")
    configuration = tmp_path / "person" / ".clojure"
    monkeypatch.setenv("CLJ_CONFIG", str(configuration))
    assert bridge.granted_paths() == ("/srv/artifacts", "/srv/gitlibs")
    configuration.mkdir(parents=True)
    assert bridge.granted_paths() == (
        "/srv/artifacts",
        "/srv/gitlibs",
        str(configuration),
    )


def test_a_resolution_asks_for_the_caches_it_uses(tmp_path, monkeypatch):
    # The shared caches sit outside the session's roots: a confined run reaches
    # them only because this call hands them over, and it hands over nothing else.
    asked = {}

    def recorded(command, **options):
        asked.update(options)
        return ToolRun(
            command=tuple(command),
            exit_code=0,
            out="/jars/library.jar",
            err="",
            duration_ms=1,
        )

    monkeypatch.setenv("VIS_HOME", str(tmp_path / "vis-home"))
    monkeypatch.setattr(bridge, "tool_path", lambda *arguments: "/bin/clojure")
    monkeypatch.setattr(bridge, "run", recorded)
    monkeypatch.setattr(bridge, "_CLASSPATH", None)
    assert bridge.library_classpath(refresh=True) == "/jars/library.jar"
    assert asked["read_write"] == bridge.granted_paths()


def test_a_project_process_is_handed_the_caches_and_the_configuration(
    tmp_path, monkeypatch
):
    # The boot resolution keeps the tools clear of a project's ambient setup.
    # The process serving the project must not: a REPL asked for an alias a
    # cross-project `deps.edn` defines has to find it, the way a terminal does.
    started = {}

    class Live:
        is_running = True

    def recorded(command, **options):
        started.update(options)
        return Live()

    monkeypatch.setenv("VIS_HOME", str(tmp_path / "vis-home"))
    monkeypatch.setenv("CLJ_CONFIG", "/srv/clojure")
    monkeypatch.setattr(bridge.runtime, "start", recorded)
    bridge.Process(tmp_path / "project", ("java", "-cp", "library.jar"))
    assert started["read_write"] == bridge.granted_paths()
    assert started["env"]["CLJ_CONFIG"] == "/srv/clojure"
    assert started["env"]["GITLIBS"] == bridge.git_libraries()
    assert started["env"][jail.REPOSITORY_VARIABLE] == bridge.maven_repository()
    boot = os.path.realpath(bridge.boot_directory())
    assert os.path.realpath(started["env"]["CLJ_CACHE"]).startswith(boot)


def test_a_failed_resolution_says_what_it_printed(tmp_path, monkeypatch):
    clojure_shim(
        tmp_path,
        monkeypatch,
        classpath="",
        code=1,
        says="Error building classpath. Could not find artifact com.example:nope",
    )
    with pytest.raises(bridge.ClojureError, match="Could not find artifact"):
        bridge.library_classpath()


def test_the_jdk_comes_from_java_home_when_it_has_one(tmp_path, monkeypatch):
    home = tmp_path / "jdk"
    (home / "bin").mkdir(parents=True)
    java = home / "bin" / "java"
    java.write_text("#!/bin/sh\nexit 0\n")
    java.chmod(0o755)
    monkeypatch.setenv("JAVA_HOME", str(home))
    assert bridge.java_command() == str(java)


def test_an_override_is_run_as_written(tmp_path, monkeypatch):
    monkeypatch.setenv("VIS_HOME", str(tmp_path / "vis-home"))
    monkeypatch.setenv("VIS_LANG_CLOJURE_COMMAND", "clojure -M:dev -m other.main")
    command = bridge.boot_command()
    assert command[1] == jail.preamble(bridge.boot_directory())
    assert command[2:] == ("clojure", "-M:dev", "-m", "other.main")


class Stopping:
    """A process that dies before it answers the first thing it is asked."""

    def __init__(self, result):
        self.result = result
        self.asked = []

    def call(self, request, timeout_s):
        self.asked.append(request)
        if len(self.asked) == 1:
            raise bridge.ClojureStopped("the Clojure process stopped before answering")
        return {"ok": True, "result": self.result}


def test_a_process_that_died_before_answering_is_asked_again(monkeypatch):
    # A sandbox reclaiming the process tree of the call that spawned the JVM,
    # or a crowded machine picking it, must not lose a lint nobody ran.
    live = Stopping({"files": 1})
    monkeypatch.setattr(bridge, "process_for", lambda root: live)
    assert bridge.call("lint", root="/app") == {"files": 1}
    assert len(live.asked) == 2


def test_a_verb_that_runs_the_project_is_never_repeated(monkeypatch):
    live = Stopping({"tests": 1})
    monkeypatch.setattr(bridge, "process_for", lambda root: live)
    with pytest.raises(bridge.ClojureStopped):
        bridge.call("test", root="/app")
    assert len(live.asked) == 1
