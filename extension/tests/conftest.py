"""The scripted stand-in these tests run against, instead of a JVM."""

import json
import shlex
import sys
from pathlib import Path

import pytest
from vis_lang_clojure import bridge

FAKE = Path(__file__).parent / "fake_clojure.py"


class Fake:
    """A fake Clojure process: scripted answers in, recorded requests out."""

    def __init__(self, tmp_path, monkeypatch):
        self.script_path = tmp_path / "script.json"
        self.log_path = tmp_path / "requests.jsonl"
        self.directory = tmp_path / "project"
        self.directory.mkdir()
        (self.directory / "deps.edn").write_text("{}\n")
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
        monkeypatch.setenv("VIS_LANG_CLOJURE_COMMAND", command)
        monkeypatch.setenv("VIS_LANG_CLOJURE_FAKE", str(self.script_path))
        monkeypatch.setenv("VIS_LANG_CLOJURE_FAKE_LOG", str(self.log_path))
        self.script({})

    @property
    def cwd(self):
        """The project directory, spelled the way the tools resolve it."""
        return str(self.directory.resolve())

    def script(self, answers):
        """Set the answer for each verb, keyed by verb or `verb:op`."""
        self.script_path.write_text(json.dumps(answers))

    def answer(self, verb, result):
        """Script one verb to succeed with `result`."""
        self.script({verb: {"ok": True, "result": result}})

    def requests(self, verb=""):
        """Every request the fake read, newest last."""
        if not self.log_path.exists():
            return []
        rows = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        return [row for row in rows if not verb or row.get("verb") == verb]

    def sent(self, verb):
        """The last request for `verb`."""
        return self.requests(verb)[-1]


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A fake Clojure process, stopped again when the test ends."""
    bridge.stop_all()
    yield Fake(tmp_path, monkeypatch)
    bridge.stop_all()


@pytest.fixture
def tools(fake):
    """The tools, talking to the fake."""
    from vis_lang_clojure.tools import ClojureTools

    return ClojureTools(), fake
