"""One process per project, and a failure that says what happened."""

import json
import os
import sys

import pytest
from vis_lang_interface import ToolTimeout

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
        "    handle.write(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:], 'env': {name: os.environ.get(name) for name in ('CLJ_CONFIG', 'CLJ_CACHE', 'GITLIBS')}}) + chr(10))\n"
        f"sys.stderr.write({says!r})\n"
        f"sys.stdout.write({classpath!r} + chr(10))\n"
        f"sys.exit({code})\n"
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
    # A confined tool may write to the workspace it was given and to Vis' own
    # directory, and to nothing else. A boot area under `~/.cache`, a Maven
    # repository under `~/.m2` or a CLI cache under `~/.clojure` is outside
    # both, and the resolution that needs one never starts at all.
    record = clojure_shim(tmp_path, monkeypatch)
    assert bridge.library_classpath() == "/jars/library.jar"

    boot = os.path.realpath(bridge.boot_directory())
    assert boot.startswith(os.path.realpath(str(tmp_path / "vis-home")))

    ran = resolutions(record)[-1]
    assert os.path.realpath(ran["cwd"]) == boot
    assert f'"{os.path.join(bridge.boot_directory(), "m2")}"' in " ".join(ran["argv"])
    for name in ("CLJ_CONFIG", "CLJ_CACHE", "GITLIBS"):
        assert os.path.realpath(ran["env"][name]).startswith(boot)


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
