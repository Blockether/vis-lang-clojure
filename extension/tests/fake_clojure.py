"""A stand-in for the Clojure CLI, so the tests need no JVM.

It speaks the same protocol: one JSON request per line in, one answer per line
out. `VIS_LANG_CLOJURE_FAKE` names a JSON file mapping a verb — or `verb:op` for
the REPL lifecycle — to the answer to send back; the answer `"exit"` ends the
process instead, and `"silence"` never replies. Every request it read is
appended to `VIS_LANG_CLOJURE_FAKE_LOG`, one JSON object per line, so a test can
check what was actually asked.
"""

import json
import os
import sys


def main():
    with open(os.environ["VIS_LANG_CLOJURE_FAKE"]) as handle:
        script = json.load(handle)
    log = os.environ.get("VIS_LANG_CLOJURE_FAKE_LOG", "")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        if log:
            with open(log, "a") as handle:
                handle.write(json.dumps(request) + "\n")
        verb = str(request.get("verb"))
        op = str(request.get("op") or "")
        answer = script.get(
            f"{verb}:{op}", script.get(verb, {"ok": True, "result": {"pong": True}})
        )
        if answer == "exit":
            return
        if answer == "silence":
            continue
        sys.stdout.write(json.dumps(dict(answer, id=request.get("id"))) + "\n")
        sys.stdout.flush()


main()
