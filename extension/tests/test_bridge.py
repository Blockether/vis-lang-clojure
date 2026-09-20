"""One process per project, and a failure that says what happened."""

import os
import shutil

import pytest
from vis_lang_interface import ToolTimeout

from vis_lang_clojure import bridge


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


@pytest.mark.skipif(
    not shutil.which("clojure"), reason="the Clojure CLI is not installed"
)
def test_the_default_command_boots_the_released_library(fake, monkeypatch):
    monkeypatch.delenv("VIS_LANG_CLOJURE_COMMAND")
    command = bridge.boot_command()
    assert os.path.basename(command[0]) == "clojure"
    assert f'{{:mvn/version "{bridge.LIBRARY_VERSION}"}}' in command[2]
    assert command[-3:] == ("-M", "-m", bridge.MAIN)
