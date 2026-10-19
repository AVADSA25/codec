"""Shared helpers for the Vibe agent tests: a scripted model, temp project folders, a way to wait for a run.

No real model and no network. The agent talks to its model through codec_vibe_agent._llm_stream,
which the `scripted` fixture replaces with a ScriptedModel.
"""
from __future__ import annotations

import threading
import time

import pytest

import codec_llm
import codec_vibe_agent as va
import codec_vibe_projects as vp


class ScriptedModel:
    """Stands in for codec_llm.stream. Every call takes the next reply from the script.

    A reply is a string, ("cut", text) for a reply that hits the token cap, or codec_llm.STREAM_ERROR.
    When the script is empty the model finishes the run, so a test never hangs on a missing reply."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.kwargs: list[dict] = []
        self.completed = 0
        self.closed_early = 0
        self.pause_after = None         # (chunks, Event): wait for the event after that many chunks
        self.chunk = 7

    def __call__(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        self.kwargs.append(kw)
        item = self.replies.pop(0) if self.replies else "Out of script.\n@@ finish\nOut of script.\n"
        return self._gen(item)

    def _gen(self, item):
        done = False
        try:
            if item is codec_llm.STREAM_ERROR:
                yield codec_llm.STREAM_ERROR
                done = True
                return
            cut = isinstance(item, tuple) and item[0] == "cut"
            text = item[1] if cut else item
            for n, i in enumerate(range(0, len(text), self.chunk), 1):
                if self.pause_after and n == self.pause_after[0]:
                    self.pause_after[1].wait(10)
                yield text[i:i + self.chunk]
            if cut:
                yield codec_llm.FINISH_LENGTH
            yield codec_llm.StreamUsage({"prompt_tokens": 100, "completion_tokens": max(1, len(text) // 4)})
            done = True
        finally:
            if done:
                self.completed += 1
            else:
                self.closed_early += 1


@pytest.fixture
def cfg():
    """The config the agent reads; a test changes it in place."""
    return {}


@pytest.fixture(autouse=True)
def agent_env(tmp_path, monkeypatch, cfg):
    monkeypatch.setenv("CODEC_VIBE_ROOT", str(tmp_path / "vibe"))
    monkeypatch.setenv("CODEC_VIBE_TRASH", str(tmp_path / "trash"))
    monkeypatch.delenv("VIBE_PROJECTS_ENABLED", raising=False)
    monkeypatch.delenv("VIBE_AGENT_ENABLED", raising=False)
    monkeypatch.setattr(va, "_cfg", lambda: cfg)
    monkeypatch.setattr(va, "_sleep", lambda s: None)
    monkeypatch.setattr(va, "_ACTIVE", None)
    va._RUNS.clear()
    vp._AGENT_RUNS.clear()
    audits: list[tuple] = []
    monkeypatch.setattr(va, "_audit", lambda event, message, extra: audits.append((event, dict(extra))))
    monkeypatch.setattr(vp, "_audit", lambda event, message, extra: audits.append((event, dict(extra))))
    monkeypatch.setattr(va, "_llm_stream", ScriptedModel([]))
    yield audits
    for run in list(va._RUNS.values()):
        run.stop()
    deadline = time.time() + 5
    while time.time() < deadline and any(not r.finished for r in va._RUNS.values()):
        time.sleep(0.02)
    va._RUNS.clear()
    vp._AGENT_RUNS.clear()


@pytest.fixture
def audits(agent_env):
    return agent_env


@pytest.fixture
def scripted(monkeypatch):
    """scripted([...replies]) installs a ScriptedModel and returns it."""
    def install(replies):
        model = ScriptedModel(replies)
        monkeypatch.setattr(va, "_llm_stream", model)
        return model
    return install


SITE = {
    "index.html": '<!doctype html>\n<html>\n<head><link rel="stylesheet" href="style.css"></head>\n'
                  '<body>\n<h1>Hello</h1>\n<button id="go">Go</button>\n<script src="app.js"></script>\n</body>\n</html>\n',
    "style.css": "body { color: black; }\nh1 { color: red; }\n",
    "app.js": 'document.getElementById("go").addEventListener("click", function () {\n  alert("hi");\n});\n',
}


def make_project(files=None, title="Agent test") -> str:
    return vp.create_project(title, [{"path": k, "content": v} for k, v in (SITE if files is None else files).items()])["id"]


def wait_finished(pid: str, run_id: str, timeout: float = 15.0) -> dict:
    end = time.time() + timeout
    since, view = 0, {}
    while time.time() < end:
        view = va.get_run(pid, run_id, since, 0.3)
        since = view["next"]
        if view["finished"]:
            return va.get_run(pid, run_id, 0, 0)        # the whole log, as a late reader sees it
    raise AssertionError(f"the run did not finish: status {view.get('status')}")


def wait_for(cond, timeout: float = 10.0):
    end = time.time() + timeout
    while time.time() < end:
        out = cond()
        if out:
            return out
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def run_script(pid: str, replies, scripted, goal="Do the thing", **kw):
    """Start a run on a scripted model and wait for it. Returns (view, model)."""
    model = scripted(replies)
    started = va.start_run(pid, goal, **kw)
    return wait_finished(pid, started["run"]), model


def kinds(view: dict) -> list[str]:
    return [e["type"] for e in view["events"]]


def end_of(view: dict) -> dict:
    return next(e for e in view["events"] if e["type"] == "end")


def blocker():
    return threading.Event()
