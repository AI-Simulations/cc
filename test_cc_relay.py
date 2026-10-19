"""Offline checks for the relay's live feed and orphaned-job delivery; no workers run."""
import importlib.util
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

spec = importlib.util.spec_from_file_location("cc", Path(__file__).with_name("cc-switch.py"))
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


# Feed: one streamed message spans several entries; each block shows once, in Claude Code's style.
t = time.time()
entries = [{"type": "user", "timestamp": stamp(t), "message": {"content": "task"}},
           {"type": "assistant", "timestamp": stamp(t + 3), "message": {"id": "m1", "content": [{"type": "thinking", "thinking": "plan it"}]}},
           {"type": "assistant", "timestamp": stamp(t + 3), "message": {"id": "m1", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo hi"}}]}},
           {"type": "user", "timestamp": stamp(t + 4), "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "hi\nthere"}]}}]
state = {}
lines = cc.feed_lines(entries, state)
assert lines == ["✻ Thought for 3s\n  ∴ plan it", "● Bash(echo hi)", "  ⎿  hi … +1 lines"], lines
assert cc.feed_lines(entries[1:3], state) == [], "blocks already shown stay hidden"

# Orphans: only this session's jobs whose relay stopped waiting; finished ones are delivered once.
tmp = Path(tempfile.mkdtemp())
cc.JOBS_DIR = str(tmp)
parent = str(uuid.uuid4())


def job(task, age, done, wait_age=None, out="RESULT TEXT"):
    name = str(uuid.uuid4())
    context = {"token": str(uuid.uuid4()), "source": f"C:/x/{parent}.jsonl", "permission_mode": "auto"}
    (tmp / f"{name}.in").write_text(cc.CONTEXT_PREFIX + json.dumps(context) + "\n" + task)
    then = time.time() - age
    os.utime(tmp / f"{name}.in", (then, then))
    cc.job_meta(name)
    (tmp / f"{name}.out").write_text(out)
    if done:
        (tmp / f"{name}.rc").write_text("0")
        (tmp / f"{name}.in").unlink()
    if wait_age is not None:
        (tmp / f"{name}.wait").touch()
        then = time.time() - wait_age
        os.utime(tmp / f"{name}.wait", (then, then))
    return name


job("fresh task", 60, False)
job("waited task", 3600, False, wait_age=30)
running = job("orphan running", 3600, False)
finished = job("orphan finished", 3600, True, wait_age=900)
big = job("orphan big", 3600, True, wait_age=900, out="x" * (cc.DELIVER_CAP + 50))
assert {item["job"] for item in cc.orphan_jobs(parent)} == {running, finished, big}
assert cc.orphan_jobs(str(uuid.uuid4())) == [], "another session's jobs are not ours"
text = cc.deliver_orphans(parent)
assert "orphan running" in text and "still running" in text and "RESULT TEXT" in text and "exit code 0" in text
assert "whole result is in" in text and (tmp / f"{big}.result.txt").exists()
assert not (tmp / f"{finished}.out").exists() and (tmp / f"{running}.out").exists()
assert "RESULT TEXT" not in cc.deliver_orphans(parent), "a result is delivered once"
print("PASS: relay feed and orphan delivery.")
