"""Vibe coding agent: the loop, the file tools and the run log (docs/VIBE-AGENT-DESIGN.md, step 3a).

A run is one background thread. The owner gives a goal; the agent lists, reads, searches, edits,
writes and deletes files of ONE project, asks a question when it must, and ends with a report.
Every step that changes files is saved as a checkpoint tagged with the run and the step, so the
page can undo the whole run or go back to before any step. The project is held by the run while
it works (codec_vibe_projects.agent_claim): every other writer gets a 409.

What the model sees and writes is plain text (codec_vibe_agent_proto). Nothing here runs a
program: commands come in step 3c, behind the owner's approval and a sandbox.

The text of the goal, of files and of replies is never written to the audit log; only counts,
tool names and statuses are.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import codec_vibe_agent_cmd as cmdmod
import codec_vibe_agent_proto as proto
import codec_vibe_projects as vp
from codec_vibe_projects import VibeError

log = logging.getLogger("codec_vibe_agent")

DEFAULTS = {"enabled": True, "max_steps": 30, "max_seconds": 900, "max_context_tokens": 10000,
            "max_reply_tokens": 3000, "wait_seconds": 600}
BOUNDS = {"max_steps": (1, 100), "max_seconds": (30, 3600), "max_context_tokens": (2000, 60000),
          "max_reply_tokens": (300, 8000), "wait_seconds": (5, 3600)}
MAX_GOAL_CHARS = 4000
MAX_HISTORY_TURNS = 4
MAX_HISTORY_CHARS = 1500
KEEP_RUNS = 20
RUNS_DIR = "runs"
FULL_RESULTS = 6            # newest results the model sees in full
DISPLAY_CHARS = 24 * 1024   # what the page keeps of one result
MODEL_RESULT_CHARS = 6 * 1024
READ_LINES = 300
READ_BYTES = 24 * 1024
LIST_ENTRIES = 200
MAP_LINES = 60
SEARCH_MATCHES = 40
MAX_QUESTIONS = 3
FAIL_EDITS = 3              # failed edits of one file in a row
FAIL_FORMAT = 4             # unreadable replies in a row
REPEAT_WARN = 3
REPEAT_STOP = 5
MODEL_RETRIES = 2
CHARS_PER_TOKEN = 3.5
TEXT_FLUSH_SECONDS = 0.25
MAX_WAIT_SECONDS = 25.0
WAIT_STEP = 1.0              # how often a waiting run looks at the clock and the stop flag
TEMPERATURE = 0.2

RUN_ID_RE = re.compile(r"^r[0-9a-f]{8}$")

_GLOBAL = threading.Lock()
_ACTIVE: "Run | None" = None
_RUNS: dict[str, "Run"] = {}


# ── settings ────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        import codec_config
        with open(codec_config.CONFIG_PATH) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def settings() -> dict:
    out = dict(DEFAULTS)
    raw = _cfg().get("vibe_agent")
    if isinstance(raw, dict):
        for key, (lo, hi) in BOUNDS.items():
            v = raw.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[key] = max(lo, min(hi, int(v)))
        if isinstance(raw.get("enabled"), bool):
            out["enabled"] = raw["enabled"]
    return out


def enabled() -> bool:
    if os.environ.get("VIBE_AGENT_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(settings()["enabled"])


def _audit(event: str, message: str, extra: dict) -> None:
    """Counts, tool names and statuses only. Never a goal, a path, a file or a model reply."""
    try:
        from codec_audit import log_event
        log_event(event, "codec-vibe", message, extra=extra)
    except Exception:
        pass


def _mono() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# ── the model ───────────────────────────────────────────────────────────────

@dataclass
class _Llm:
    kind: str           # local | cloud
    base_url: str
    model: str
    label: str
    host: str = ""


def _run_command(*args, **kw):
    """The one place a command is started (tests replace it)."""
    return cmdmod.run(*args, **kw)


def _llm_stream(messages, **kw):
    """The one place the agent talks to a model (tests replace it)."""
    import codec_llm
    return codec_llm.stream(messages, **kw)


def _resolve_llm(kind: str, confirm_cloud: bool = False) -> _Llm:
    import urllib.parse
    import codec_cloud_models as ccm
    cfg = _cfg()
    if kind == "cloud":
        if not confirm_cloud:
            raise VibeError(400, "A cloud run needs the owner's confirmation")
        ents = ccm.entries(cfg)
        if not ents:
            raise VibeError(400, "No cloud model is set up")
        e = ccm.active_entry(cfg) or ents[0]
        why = ccm.block_message(e)
        if why:
            raise VibeError(409, why)
        return _Llm("cloud", ccm._norm(e["base_url"]), e["id"], str(e.get("label") or e["id"]),
                    urllib.parse.urlparse(e["base_url"]).hostname or "")
    if kind != "local":
        raise VibeError(400, "The model must be local or cloud")
    base = cfg.get("llm_base_url") or ccm.DEFAULT_LOCAL_BASE_URL
    model = cfg.get("llm_model") or ""
    base, model = ccm.local_only(base, model, cfg)      # a switch of chat to the cloud does not move the agent
    if not model:
        try:
            import codec_models
            model = codec_models.DEFAULT_MODEL
        except Exception:
            model = ""
    return _Llm("local", base, model, "Local")


def models_info() -> dict:
    """What the page needs to offer the model choice: the local model, and the cloud model if one is ready."""
    import urllib.parse
    import codec_cloud_models as ccm
    cfg = _cfg()
    out = {"local": {"label": "Local", "model": _resolve_llm("local").model}, "cloud": None,
           "limits": {k: settings()[k] for k in ("max_steps", "max_seconds")}}
    ents = ccm.entries(cfg)
    if ents:
        e = ccm.active_entry(cfg) or ents[0]
        why = ccm.block_message(e)
        out["cloud"] = {"label": str(e.get("label") or e["id"]), "host": urllib.parse.urlparse(e["base_url"]).hostname or "",
                        "ready": why is None, "reason": why or ""}
    return out


# ── results of one tool ─────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    text: str                       # what the model sees
    summary: str                    # one line for the timeline and for old steps
    changed: tuple = ()             # paths whose content changed
    blocked: str = ""               # path | secret: a safety rule refused it


def _span(seconds: int) -> str:
    return f"{seconds} seconds" if seconds < 120 else f"{seconds // 60} minutes"


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


def _fail(msg: str, blocked: str = "") -> Result:
    return Result(False, msg, msg.split("\n", 1)[0][:160], blocked=blocked)


def _path_failure(e: VibeError, rel: str = "") -> Result:
    msg = e.message
    if "looks like a secret" in msg:
        return _fail("That file name looks like a secret, so the agent may not read or write it.", "secret")
    if e.status == 404:
        return _fail("There is no such file or folder.")
    if e.status == 400:
        return _fail(msg, "path")
    return _fail(msg)


def _read_text(pid: str, rel: str):
    """(content, None) or (None, Result) for a file the agent may read."""
    try:
        info = vp.read_file(pid, rel)
    except VibeError as e:
        if e.status == 404:
            return None, _fail("No such file. Use list_files to see the files, or write_file to create it.")
        return None, _path_failure(e, rel)
    if info.get("too_large"):
        return None, _fail("That file is larger than 1 MB.")
    if info.get("binary"):
        return None, _fail("That file is not text.")
    return info["content"], None


def _t_list_files(run: "Run", folder: str) -> Result:
    files = [f for f in vp.list_files(run.pid) if not vp.looks_secret(f["path"])]
    folder = (folder or "").strip().strip("/")
    if folder:
        try:
            vp.safe_rel(folder)
        except VibeError as e:
            return _path_failure(e)
        files = [f for f in files if f["path"].startswith(folder + "/")]
    if not files:
        return Result(True, "No files." if not folder else f"No files in {folder}.", "No files")
    shown = files[:LIST_ENTRIES]
    lines = [f"{f['path']}  ({_size(f['size'])})" for f in shown]
    more = f"\n... and {len(files) - len(shown)} more" if len(files) > len(shown) else ""
    head = f"{len(files)} file(s)" + (f" in {folder}" if folder else "")
    return Result(True, head + ":\n" + "\n".join(lines) + more, head)


def _t_read_file(run: "Run", path: str, first: int, last) -> Result:
    content, bad = _read_text(run.pid, path)
    if bad:
        return bad
    lines = content.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    if total == 0:
        return Result(True, f"{path} is empty.", f"Read {path} (empty)")
    if first > total:
        return _fail(f"The file has only {total} lines.")
    end = min(last or total, total, first + READ_LINES - 1)
    chunk = lines[first - 1:end]
    body = proto.numbered(chunk, first, width_to=total)
    while len(body.encode("utf-8", "replace")) > READ_BYTES and len(chunk) > 1:
        chunk = chunk[:max(1, len(chunk) * 3 // 4)]
        end = first + len(chunk) - 1
        body = proto.numbered(chunk, first, width_to=total)
    more = f"\n(more lines follow: @@ read_file {path} {end + 1}-)" if end < total else ""
    head = f"{path}, lines {first}-{end} of {total}"
    return Result(True, head + "\n" + body + more, f"Read {head}")


def _t_search(run: "Run", pattern: str, path: str) -> Result:
    pat = (pattern or "").strip()
    if not pat or len(pat) > 200:
        return _fail("Give a search text of 1 to 200 characters.")
    low = pat.lower()
    where = (path or "").strip().strip("/")
    if where:
        try:
            vp.safe_rel(where)
        except VibeError as e:
            return _path_failure(e)
    hits: list[str] = []
    more = False
    for f in vp.list_files(run.pid):
        rel = f["path"]
        if vp.looks_secret(rel):
            continue
        if where and rel != where and not rel.startswith(where + "/"):
            continue
        try:
            info = vp.read_file(run.pid, rel)
        except VibeError:
            continue
        text = info.get("content")
        if not isinstance(text, str):
            continue
        for n, line in enumerate(text.split("\n"), 1):
            if low in line.lower():
                if len(hits) >= SEARCH_MATCHES:
                    more = True
                    break
                hits.append(f"{rel}:{n}: {line.strip()[:200]}")
        if more:
            break
    if not hits:
        return Result(True, f'No matches for "{pat}".', f'No matches for "{pat[:40]}"')
    tail = "\n(more matches exist: narrow the search)" if more else ""
    return Result(True, "\n".join(hits) + tail, f'{len(hits)}{"+" if more else ""} match(es) for "{pat[:40]}"')


def _changed_region(old: str, new: str) -> str:
    a, b = old.split("\n"), new.split("\n")
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    start = max(0, i - 2)
    return proto.numbered(b[start:start + 12], start + 1)


def _t_edit_file(run: "Run", path: str, pairs) -> Result:
    content, bad = _read_text(run.pid, path)
    if bad:
        return bad
    new, err = proto.apply_pairs(content, pairs)
    if err:
        return _fail(err)
    if new == content:
        return _fail("The edit changes nothing: SEARCH and REPLACE are the same.")
    try:
        done = vp.agent_write(run.pid, path, new)
    except VibeError as e:
        return _path_failure(e)
    added, removed = proto.line_stats(content, new)
    head = f"Edited {done['path']} (+{added} -{removed})"
    return Result(True, head + ". The changed part now reads:\n" + _changed_region(content, new), head, changed=(done["path"],))


def _t_write_file(run: "Run", path: str, content: str) -> Result:
    text = content if content == "" or content.endswith("\n") else content + "\n"
    try:
        done = vp.agent_write(run.pid, path, text)
    except VibeError as e:
        return _path_failure(e)
    n = text.count("\n")
    head = f"Wrote {done['path']} ({'new' if done['created'] else 'replaced'}, {n} lines, {_size(done['size'])})"
    return Result(True, head + ".", head, changed=(done["path"],))


def _t_delete_file(run: "Run", path: str) -> Result:
    try:
        done = vp.agent_delete(run.pid, path)
    except VibeError as e:
        return _path_failure(e)
    return Result(True, f"Deleted {done['path']}.", f"Deleted {done['path']}", changed=(done["path"],))


# ── one run ─────────────────────────────────────────────────────────────────

@dataclass
class _Turn:
    n: int
    name: str
    arg: str
    assistant: str
    assistant_short: str
    result: str                 # wrapped, in full
    summary: str
    ok: bool
    read_path: str = ""
    stale: bool = False


@dataclass
class _Reply:
    text: str = ""
    usage: dict = field(default_factory=dict)
    cut: bool = False           # the model hit the reply token cap
    normal_end: bool = False    # the stream ended by itself
    error: bool = False
    stopped: bool = False
    timed_out: bool = False


class _ContextTooBig(Exception):
    pass


def _tool_arg(call: proto.Call) -> str:
    a = call.args
    if call.name == "list_files":
        return a.get("folder") or "."
    if call.name == "read_file":
        rng = f" {a['first']}-{a['last'] or ''}" if (a["first"] != 1 or a["last"]) else ""
        return a["path"] + rng
    if call.name == "search":
        return f'"{a["pattern"][:60]}"' + (f" in {a['path']}" if a["path"] else "")
    if call.name == "edit_file":
        return f"{a['path']} ({len(a['pairs'])} edit{'s' if len(a['pairs']) != 1 else ''})"
    if call.name in ("write_file", "delete_file"):
        return a["path"]
    if call.name == "run_check":
        return a["command"][:120]
    return ""


def _signature(call: proto.Call) -> str:
    return call.name + "\x00" + json.dumps(call.args, sort_keys=True, default=str)


class Run:
    def __init__(self, pid: str, goal: str, history: list, llm: _Llm, limits: dict, can_run: bool):
        self.id = "r" + secrets.token_hex(4)
        self.pid, self.goal, self.history, self.llm, self.limits, self.can_run = pid, goal, history, llm, limits, can_run
        self.allowed = tuple(t for t in proto.TOOLS if t != "run_check" or (can_run and cmdmod.available()))
        self.status = "running"
        self.finished = False
        self.step = 0
        self.turns: list[_Turn] = []
        self.events: list[dict] = []
        self.start_cp: str | None = None
        self.tokens = {"prompt": 0, "completion": 0}
        self.stop_event = threading.Event()
        self._cond = threading.Condition()
        self._seq = 0
        self._t0 = _mono()
        self.deadline = self._t0 + limits["max_seconds"]
        self._sigs: list[str] = []
        self._format_fails = 0
        self._cuts = 0
        self._model_fails = 0
        self._edit_fails: dict[str, int] = {}
        self._questions = 0
        self._answer: dict | None = None
        self._qid = ""
        self._decision: dict | None = None
        self._aid = ""
        self._thread: threading.Thread | None = None

    # events -------------------------------------------------------------

    def _log_path(self) -> Path:
        return vp.project_dir(self.pid) / vp.META_DIR / RUNS_DIR / f"{self.id}.jsonl"

    def _emit(self, kind: str, **fields) -> dict:
        with self._cond:
            self._seq += 1
            ev = {"seq": self._seq, "t": round(time.time(), 3), "type": kind, **fields}
            self.events.append(ev)
            if kind != "text":
                try:
                    path = self._log_path()
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    with os.fdopen(fd, "a", encoding="utf-8") as f:
                        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                except (OSError, VibeError):
                    log.warning("vibe agent: could not write the run log", exc_info=True)
            self._cond.notify_all()
            return ev

    def _drop_text(self, step: int) -> None:
        with self._cond:
            self.events = [e for e in self.events if not (e["type"] == "text" and e.get("step") == step)]

    def view(self, since: int = 0, wait: float = 0.0) -> dict:
        wait = max(0.0, min(float(wait or 0), MAX_WAIT_SECONDS))
        end = _mono() + wait
        with self._cond:
            while wait > 0 and not self.finished and not any(e["seq"] > since for e in self.events):
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(left)
            evs = [e for e in self.events if e["seq"] > since]
            return {"run": self.id, "project": self.pid, "status": self.status, "finished": self.finished,
                    "events": evs, "next": evs[-1]["seq"] if evs else since, "step": self.step}

    # control ------------------------------------------------------------

    def stop(self) -> None:
        self.stop_event.set()
        with self._cond:
            self._cond.notify_all()

    def answer(self, qid: str, text: str) -> None:
        with self._cond:
            if self.status != "waiting" or qid != self._qid:
                raise VibeError(409, "Nothing is waiting for that answer")
            self._answer = {"text": text}
            self._cond.notify_all()

    def approve(self, aid, decision, network) -> None:
        if decision not in ("once", "always", "decline"):
            raise VibeError(400, "Choose once, always or decline")
        with self._cond:
            if self.status != "waiting" or not isinstance(aid, str) or not aid or aid != self._aid:
                raise VibeError(409, "Nothing is waiting for that decision")
            self._decision = {"decision": decision, "network": bool(network) and decision != "decline"}
            self._cond.notify_all()

    def _end(self, status: str, report: str) -> None:
        """Finish the run. Everything else is done before the end event, so whoever sees it can undo at once."""
        global _ACTIVE
        with self._cond:
            if self.finished:
                return
            self.status = status
        vp.agent_release(self.pid, self.id)
        with _GLOBAL:
            if _ACTIVE is self:
                _ACTIVE = None
        self._trim_logs()
        seconds = round(_mono() - self._t0, 1)
        _audit("vibe_agent_end", "Vibe agent run ended", {
            "project": self.pid, "run": self.id, "status": status, "steps": self.step, "seconds": seconds,
            "prompt_tokens": self.tokens["prompt"], "completion_tokens": self.tokens["completion"], "model": self.llm.kind})
        with self._cond:
            self._emit("end", status=status, report=str(report)[:proto.MAX_REPORT_CHARS], steps=self.step, seconds=seconds,
                       prompt_tokens=self.tokens["prompt"], completion_tokens=self.tokens["completion"],
                       start_checkpoint=self.start_cp)
            self.finished = True
            self._cond.notify_all()

    def _trim_logs(self) -> None:
        try:
            d = self._log_path().parent
            files = sorted((p for p in d.glob("r*.jsonl") if RUN_ID_RE.match(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
            live = {r.id for r in list(_RUNS.values()) if not r.finished}
            for p in files[KEEP_RUNS:]:
                if p.stem not in live:
                    p.unlink()
        except (OSError, VibeError):
            pass

    # the loop -----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._main, name=f"vibe-agent-{self.id}", daemon=True)
        self._thread.start()

    def _main(self) -> None:
        try:
            self._begin()
            self._loop()
        except Exception:
            log.exception("vibe agent run failed")
            self._end("failed", "The agent stopped because of an internal error.")
        finally:
            if not self.finished:
                self._end("failed", "The agent stopped unexpectedly.")

    def _begin(self) -> None:
        cp = vp.agent_checkpoint(self.pid, "Before agent: " + self.goal.replace("\n", " ")[:60], "before", self.id, 0, force=True)
        self.start_cp = cp["id"] if cp else None
        self._emit("start", goal=self.goal, model={"kind": self.llm.kind, "label": self.llm.label}, can_run=self.can_run,
                   limits={k: self.limits[k] for k in ("max_steps", "max_seconds")}, start_checkpoint=self.start_cp)
        _audit("vibe_agent_start", "Vibe agent run started", {
            "project": self.pid, "run": self.id, "model": self.llm.kind, "goal_chars": len(self.goal)})

    def _loop(self) -> None:
        lim = self.limits
        while True:
            if self.stop_event.is_set():
                return self._end("stopped", "Stopped.")
            if self.step >= lim["max_steps"]:
                return self._end("limit", f"Stopped at the limit of {lim['max_steps']} steps. The files keep the work done so far.")
            if _mono() > self.deadline:
                return self._end("limit", f"Stopped at the time limit of {_span(lim['max_seconds'])}. The files keep the work done so far.")
            if self.llm.kind == "cloud":
                import codec_cloud_models as ccm
                e = ccm.entry_for_id(self.llm.model, _cfg())
                why = ccm.block_message(e) if e else "The cloud model is no longer set up."
                if why:
                    return self._end("failed", why)
            self.step += 1
            n = self.step
            try:
                messages = self._messages()
            except _ContextTooBig:
                return self._end("limit", "The project is too big for the context limit. Raise max_context_tokens or use fewer files.")
            reply = self._stream(n, messages)
            self._drop_text(n)
            if reply.stopped:
                return self._end("stopped", "Stopped.")
            if reply.timed_out:
                return self._end("limit", f"Stopped at the time limit of {_span(lim['max_seconds'])}. The files keep the work done so far.")
            if reply.error and not reply.text.strip():
                self._model_fails += 1
                self.step -= 1
                if self._model_fails >= MODEL_RETRIES:
                    return self._end("failed", "The model did not answer. Check that it is running, then try again.")
                _sleep(2.0)
                continue
            self._model_fails = 0
            scan = proto.scan_reply(reply.text, self.allowed, final=True, lenient=reply.normal_end)
            if scan.note:
                self._emit("note", step=n, text=scan.note[:600])
            if reply.cut and scan.state != "complete":
                self._cuts += 1
                if self._cuts >= 2:
                    return self._end("limit", "The model's reply was cut off twice in a row. Try a smaller goal.")
                self._bad_reply(n, reply.text, "Your reply was cut off at the length limit. Use smaller edits: one change per edit_file, or write a long file in parts.")
                continue
            self._cuts = 0
            if scan.state != "complete":
                self._format_fails += 1
                if self._format_fails >= FAIL_FORMAT:
                    return self._end("limit", "I could not read the model's replies as tool blocks.")
                msg = scan.error or "I found no tool block in your reply. Reply with one short sentence and one tool block that starts with @@ at the start of a line.\n" + proto.FORMAT_HELP
                self._bad_reply(n, reply.text, msg)
                continue
            self._format_fails = 0
            done = self._run_call(n, reply.text, scan.call)
            if done:
                return

    def _bad_reply(self, n: int, text: str, message: str) -> None:
        wrapped = proto.wrap_result(n, "error", "", message, ok=False)
        self._emit("result", step=n, ok=False, summary=message.split("\n", 1)[0][:160], output=message[:DISPLAY_CHARS], tool="error")
        said = text.strip() or "(no reply)"
        self.turns.append(_Turn(n, "error", "", said[:2000], said[:300], wrapped, message.split("\n", 1)[0][:160], False))
        _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool": "error", "ok": False, "ms": 0})

    def _run_call(self, n: int, text: str, call: proto.Call) -> bool:
        """Run one tool. True when the run is over."""
        arg = _tool_arg(call)
        self._emit("tool", step=n, name=call.name, arg=arg)
        sig = _signature(call)
        self._sigs.append(sig)
        streak = 0
        for s in reversed(self._sigs):
            if s != sig:
                break
            streak += 1
        if streak >= REPEAT_STOP:
            self._end("limit", "The agent kept repeating the same step, so I stopped it.")
            return True
        t0 = _mono()
        if call.name == "finish":
            report = call.args["report"]
            if report == "Done." and call.note:             # the summary went into the sentence before the block
                report = call.note
            self._emit("result", step=n, ok=True, summary="Finished", output=report, tool="finish")
            _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool": "finish", "ok": True, "ms": 0})
            self._end("done", report)
            return True
        if call.name == "ask":
            result = self._ask(n, call)
            if result is None:
                return True
        elif call.name == "run_check":
            result = self._run_check(n, call)
            if result is None:
                return True
        else:
            result = self._exec(call)
        ms = int((_mono() - t0) * 1000)
        warn = ""
        if streak >= REPEAT_WARN:
            warn = f"\nYou have made this same call {streak} times in a row. Do something different, or use finish."
        if result.blocked:
            _audit("vibe_agent_blocked", "Vibe agent step refused", {"project": self.pid, "run": self.id, "n": n, "reason": result.blocked})
        if not result.ok and call.name == "edit_file":
            key = call.args["path"]
            self._edit_fails[key] = self._edit_fails.get(key, 0) + 1
        elif result.ok and result.changed:
            for p in result.changed:
                self._edit_fails.pop(p, None)
        model_text = result.text if len(result.text) <= MODEL_RESULT_CHARS else (
            result.text[:MODEL_RESULT_CHARS] + f"\n... (cut: {len(result.text) - MODEL_RESULT_CHARS} more characters)")
        wrapped = proto.wrap_result(n, call.name, arg, model_text + warn, result.ok)
        self._emit("result", step=n, ok=result.ok, summary=result.summary, output=result.text[:DISPLAY_CHARS], tool=call.name)
        _audit("vibe_agent_step", "Vibe agent step", {"project": self.pid, "run": self.id, "n": n, "tool": call.name, "ok": result.ok, "ms": ms})
        short = text if len(text) <= 600 else (call.note[:300] + "\n" + f"@@ {call.name} {arg}\n(content left out)")
        self.turns.append(_Turn(n, call.name, arg, text[:8000], short, wrapped, result.summary, result.ok,
                                read_path=call.args.get("path", "") if call.name == "read_file" else ""))
        if result.ok and result.changed:
            self._checkpoint(n, f"Agent: {call.name} {result.changed[0]}", result.changed)
        elif call.name == "run_check":
            self._checkpoint(n, "Agent: ran " + call.args["command"][:60], ())
        if call.name == "edit_file" and self._edit_fails.get(call.args["path"], 0) >= FAIL_EDITS:
            self._end("limit", f"I could not apply an edit to {call.args['path']} after {FAIL_EDITS} tries. "
                               "The files keep the work done so far.")
            return True
        return False

    def _checkpoint(self, n: int, label: str, paths) -> None:
        """Save the files as a step of this run, when they changed, and tell the page."""
        try:
            cp = vp.agent_checkpoint(self.pid, label[:120], "ai", self.id, n)
        except VibeError:
            cp = None
        changed = set(paths) | set((cp or {}).get("changed", []))
        for t in self.turns:
            if t.read_path and t.read_path in changed:
                t.stale = True
        if cp:
            self._emit("checkpoint", step=n, id=cp["id"], parent=cp.get("parent"), label=cp["label"],
                       lines_added=cp["stat"]["lines_added"], lines_removed=cp["stat"]["lines_removed"], changed=cp.get("changed", []))

    def _exec(self, call: proto.Call) -> Result:
        a = call.args
        try:
            if call.name == "list_files":
                return _t_list_files(self, a["folder"])
            if call.name == "read_file":
                return _t_read_file(self, a["path"], a["first"], a["last"])
            if call.name == "search":
                return _t_search(self, a["pattern"], a["path"])
            if call.name == "edit_file":
                return _t_edit_file(self, a["path"], a["pairs"])
            if call.name == "write_file":
                return _t_write_file(self, a["path"], a["content"])
            if call.name == "delete_file":
                return _t_delete_file(self, a["path"])
        except VibeError as e:
            return _fail(e.message)
        except OSError as e:
            log.warning("vibe agent tool error: %s", e)
            return _fail("The file could not be read or written.")
        return _fail(f"The tool {call.name} is not available.")

    # commands ----------------------------------------------------------

    def _command_audit(self, n: int, decision: str, command: str, network: bool, exit_code, ms: int) -> None:
        word = (command.split() or [""])[0].rsplit("/", 1)[-1]
        program = word if re.fullmatch(r"[A-Za-z0-9._+-]{1,30}", word) else ""
        _audit("vibe_agent_command", "Vibe agent command", {"project": self.pid, "run": self.id, "n": n, "decision": decision,
                                                           "program": program, "network": bool(network), "exit_code": exit_code, "ms": ms})

    def _approval(self, n: int, command: str):
        """Ask the owner. (decision, network), or None when the run ended while waiting."""
        self._aid = "a" + secrets.token_hex(3)
        with self._cond:
            self._decision = None
            self.status = "waiting"
        self._emit("approval", step=n, id=self._aid, command=command)
        end = _mono() + self.limits["wait_seconds"]
        with self._cond:
            while self._decision is None and not self.stop_event.is_set():
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(min(left, WAIT_STEP))
            got = self._decision
            self.status = "running" if (got is not None and not self.stop_event.is_set()) else self.status
        if self.stop_event.is_set():
            self._end("stopped", "Stopped.")
            return None
        if got is None:
            self._end("stopped", f"No answer came within {_span(self.limits['wait_seconds'])}, so the agent stopped.")
            return None
        self._emit("decision", step=n, id=self._aid, decision=got["decision"], network=got["network"])
        return got["decision"], got["network"]

    def _run_check(self, n: int, call: proto.Call) -> "Result | None":
        """Run one command the owner approved, in the sandbox. None when the run ended while waiting."""
        command = call.args["command"]
        try:
            command = cmdmod.check_command(command)
        except cmdmod.Refused as e:
            self._command_audit(n, "refused", command, False, None, 0)
            return Result(False, f"That command was refused: {e}. Use a plainer one.", "Command refused", blocked="dangerous")
        stored = next((a for a in vp.allowed_commands(self.pid) if a["cmd"] == command), None)
        if stored:
            decision, network = "stored", bool(stored["network"])
        else:
            got = self._approval(n, command)
            if got is None:
                return None
            decision, network = got
            if decision == "decline":
                self._command_audit(n, "declined", command, False, None, 0)
                return Result(False, "The owner did not allow that command, so it was not run. Do not ask for it again. "
                                     "Check your work another way, or finish and say it was not checked.", "Declined")
            if decision == "always":
                try:
                    vp.allow_command(self.pid, command, network)
                except VibeError:
                    pass
        t0 = _mono()
        try:
            res = _run_command(vp.project_dir(self.pid), vp.vibe_root(), command, network=network,
                               timeout=max(5.0, min(float(cmdmod.DEFAULT_TIMEOUT), self.deadline - _mono())), stop_event=self.stop_event)
        except cmdmod.Refused as e:
            return Result(False, str(e), "Command not run")
        self._command_audit(n, decision, command, network, res.exit_code, int((_mono() - t0) * 1000))
        if res.stopped:
            head = "Stopped before the command finished."
        elif res.timed_out:
            head = f"Timed out after {int(res.seconds)} s and was stopped."
        else:
            head = f"Exit code {res.exit_code} after {res.seconds} s."
        extra = ""
        if res.timed_out:
            extra += f"\nA command is stopped after {cmdmod.DEFAULT_TIMEOUT} seconds. Do not start servers or watchers with run_check."
        if res.denied:
            extra += ("\nThe sandbox stopped something: the command can read this project and the usual toolchains, write only inside the "
                      f"project, and has {'web access only' if network else 'no network'}.")
        ok = res.exit_code == 0
        state = "stopped" if res.stopped else "timed out" if res.timed_out else f"exit {res.exit_code}"
        return Result(ok, head + "\n" + (res.output.strip("\n") or "(no output)") + extra, f"Ran {command[:50]}: {state} ({res.seconds} s)")

    def _ask(self, n: int, call: proto.Call) -> "Result | None":
        """Wait for the owner. None when the run ended while waiting."""
        if self._questions >= MAX_QUESTIONS:
            return _fail(f"You already asked {MAX_QUESTIONS} questions. Decide yourself, or finish and say what is unclear.")
        self._questions += 1
        self._qid = "q" + secrets.token_hex(3)
        with self._cond:
            self._answer = None
            self.status = "waiting"
        self._emit("question", step=n, id=self._qid, text=call.args["question"], choices=call.args["choices"])
        end = _mono() + self.limits["wait_seconds"]
        with self._cond:
            while self._answer is None and not self.stop_event.is_set():
                left = end - _mono()
                if left <= 0:
                    break
                self._cond.wait(min(left, WAIT_STEP))
            answer = self._answer
            self.status = "running" if (answer is not None and not self.stop_event.is_set()) else self.status
        if self.stop_event.is_set():
            self._end("stopped", "Stopped.")
            return None
        if answer is None:
            self._end("stopped", f"No answer came within {_span(self.limits['wait_seconds'])}, so the agent stopped.")
            return None
        self._emit("answer", step=n, id=self._qid)
        text = str(answer["text"])[:1000]
        return Result(True, "The owner answered: " + text, "Answered")

    # the model ----------------------------------------------------------

    def _first_user(self) -> str:
        parts = ["GOAL\n" + self.goal]
        if self.history:
            lines = [f"{m['role']}: {m['content']}" for m in self.history]
            parts.append("EARLIER IN THIS CHAT (context only)\n" + "\n".join(lines))
        parts.append("Work in the project folder. Start with the smallest step that moves toward the goal.")
        return "\n\n".join(parts)

    def _map(self) -> str:
        files = [f for f in vp.list_files(self.pid) if not vp.looks_secret(f["path"])]
        if not files:
            return "PROJECT FILES: none yet"
        shown = files[:MAP_LINES]
        more = f"\n... and {len(files) - len(shown)} more (list_files shows them)" if len(files) > len(shown) else ""
        return f"PROJECT FILES ({len(files)}):\n" + "\n".join(f"{f['path']} ({_size(f['size'])})" for f in shown) + more

    @staticmethod
    def _result_text(t: _Turn, mode: str) -> str:
        if t.stale:
            return f"[RESULT {t.n} {t.name} {t.arg}] The file has changed since. Read it again if you need it."
        if mode == "full":
            return t.result
        return f"[RESULT {t.n} {t.name} {t.arg}] {t.summary} (shortened to save space)" + ("" if t.ok else " (this step failed)")

    @staticmethod
    def _estimate(msgs: list) -> int:
        return int(sum(len(m["content"]) for m in msgs) / CHARS_PER_TOKEN) + 4 * len(msgs)

    def _messages(self) -> list:
        cap = self.limits["max_context_tokens"]
        system = {"role": "system", "content": proto.system_prompt(self.allowed)}
        modes = ["full" if i >= len(self.turns) - FULL_RESULTS else "short" for i in range(len(self.turns))]
        site = self._map()
        first = self._first_user()
        shrink = None
        while True:
            msgs = [system]
            users = [first]
            for i, t in enumerate(self.turns):
                msgs.append({"role": "user", "content": users[-1]})
                msgs.append({"role": "assistant", "content": t.assistant if modes[i] == "full" else t.assistant_short})
                body = self._result_text(t, modes[i])
                if shrink and i == len(self.turns) - 1 and len(body) > shrink:
                    half = shrink // 2
                    body = body[:half] + "\n... (cut to fit the context) ...\n" + body[-half:]
                users.append(body)
            msgs.append({"role": "user", "content": users[-1] + "\n\n" + site})
            if self._estimate(msgs) <= cap:
                return msgs
            idx = next((i for i, m in enumerate(modes[:-1]) if m == "full"), None)
            if idx is not None:
                modes[idx] = "short"
                continue
            if self.turns and (shrink is None or shrink > 1500):
                shrink = max(1500, int(cap * CHARS_PER_TOKEN * 0.3)) if shrink is None else shrink // 2
                continue
            raise _ContextTooBig()

    def _stream(self, n: int, messages: list) -> _Reply:
        import codec_llm
        reply = _Reply()
        pending = ""
        last = _mono()
        kw = dict(base_url=self.llm.base_url, model=self.llm.model, api_key="", max_tokens=self.limits["max_reply_tokens"],
                  temperature=TEMPERATURE, timeout=180.0, enable_thinking=False, usage_sentinel=True, error_sentinel=True)
        gen = _llm_stream(messages, **kw)
        try:
            for item in gen:
                if self.stop_event.is_set():
                    reply.stopped = True
                    break
                if _mono() > self.deadline:
                    reply.timed_out = True
                    break
                if isinstance(item, codec_llm.StreamUsage):
                    reply.usage = dict(item)
                    continue
                if item is codec_llm.STREAM_ERROR:
                    reply.error = True
                    break
                if item is codec_llm.FINISH_LENGTH:
                    reply.cut = True
                    continue
                if not isinstance(item, str):
                    continue
                reply.text += item
                pending += item
                if _mono() - last >= TEXT_FLUSH_SECONDS:
                    self._emit("text", step=n, chunk=pending)
                    pending, last = "", _mono()
                sc = proto.scan_reply(reply.text, self.allowed, final=False)
                if sc.state in ("complete", "error") and sc.cut:
                    break
            else:
                reply.normal_end = not (reply.cut or reply.error)
        finally:
            close = getattr(gen, "close", None)
            if close:
                close()
        if pending:
            self._emit("text", step=n, chunk=pending)
        u = reply.usage
        self.tokens["prompt"] += int(u.get("prompt_tokens") or self._estimate(messages))
        self.tokens["completion"] += int(u.get("completion_tokens") or len(reply.text) / CHARS_PER_TOKEN)
        return reply


# ── starting, finding and stopping runs ─────────────────────────────────────

def _clean_history(history) -> list:
    out = []
    if isinstance(history, list):
        for m in history[-MAX_HISTORY_TURNS:]:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
                c = m["content"].strip()
                if c:
                    out.append({"role": m["role"], "content": c[:MAX_HISTORY_CHARS]})
    return out


def start_run(pid: str, goal, *, model: str = "local", confirm_cloud: bool = False, history=None, can_run: bool = False) -> dict:
    global _ACTIVE
    if not vp.enabled() or not enabled():
        raise VibeError(404, "The agent is switched off")
    if not isinstance(goal, str) or not goal.strip():
        raise VibeError(400, "Write the goal first")
    goal = goal.strip()
    if len(goal) > MAX_GOAL_CHARS:
        raise VibeError(400, f"The goal is longer than {MAX_GOAL_CHARS} characters")
    vp.project_dir(pid)
    llm = _resolve_llm(model if isinstance(model, str) else "local", bool(confirm_cloud))
    run = Run(pid, goal, _clean_history(history), llm, settings(), bool(can_run))
    with _GLOBAL:
        if _ACTIVE is not None and not _ACTIVE.finished:
            raise VibeError(409, "The agent is already working on a project. Stop it first.")
        vp.agent_claim(pid, run.id)
        _ACTIVE = run
        _RUNS[run.id] = run
        for rid in [r for r, x in _RUNS.items() if x.finished][:-KEEP_RUNS]:
            _RUNS.pop(rid, None)
    try:
        run.start()
    except Exception:
        run._end("failed", "The agent could not start.")
        raise
    return {"run": run.id, "status": "running", "model": {"kind": llm.kind, "label": llm.label},
            "limits": {k: run.limits[k] for k in ("max_steps", "max_seconds")}}


def _file_events(pid: str, run_id: str) -> list:
    if not RUN_ID_RE.match(str(run_id)):
        raise VibeError(404, "No such run")
    path = vp.project_dir(pid) / vp.META_DIR / RUNS_DIR / f"{run_id}.jsonl"
    if not path.is_file():
        raise VibeError(404, "No such run")
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
    return out


def _status_of(events: list) -> str:
    end = next((e for e in reversed(events) if e.get("type") == "end"), None)
    return end["status"] if end else "interrupted"


def get_run(pid: str, run_id: str, since: int = 0, wait: float = 0.0) -> dict:
    vp.project_dir(pid)
    run = _RUNS.get(run_id)
    if run is not None and run.pid == pid:
        return run.view(since, wait)
    events = _file_events(pid, run_id)
    evs = [e for e in events if int(e.get("seq", 0)) > int(since or 0)]
    return {"run": run_id, "project": pid, "status": _status_of(events), "finished": True, "events": evs,
            "next": evs[-1]["seq"] if evs else int(since or 0), "step": sum(1 for e in events if e.get("type") == "tool")}


def list_runs(pid: str) -> list:
    d = vp.project_dir(pid) / vp.META_DIR / RUNS_DIR
    if not d.is_dir():
        return []
    out = []
    files = sorted((p for p in d.glob("r*.jsonl") if RUN_ID_RE.match(p.stem)), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:KEEP_RUNS]:
        try:
            events = _file_events(pid, p.stem)
        except VibeError:
            continue
        start = next((e for e in events if e.get("type") == "start"), {})
        live = _RUNS.get(p.stem)
        status = live.status if (live is not None and not live.finished) else _status_of(events)
        end = next((e for e in reversed(events) if e.get("type") == "end"), {})
        out.append({"run": p.stem, "goal": str(start.get("goal", ""))[:200], "status": status,
                    "started": start.get("t"), "steps": sum(1 for e in events if e.get("type") == "tool"),
                    "model": (start.get("model") or {}).get("label", ""), "start_checkpoint": start.get("start_checkpoint"),
                    "seconds": end.get("seconds")})
    return out


def _live(pid: str, run_id: str) -> Run:
    vp.project_dir(pid)
    run = _RUNS.get(run_id)
    if run is None or run.pid != pid:
        raise VibeError(404, "No such run")
    return run


def approve_run(pid: str, run_id: str, aid, decision, network=False) -> dict:
    run = _live(pid, run_id)
    if not run.can_run:
        raise VibeError(403, "Commands can only be approved for a run started at this Mac")
    run.approve(aid, decision, bool(network))
    return {"run": run_id, "ok": True}


def commands(pid: str) -> list:
    return vp.allowed_commands(pid)


def forget(pid: str, command) -> list:
    if not isinstance(command, str):
        raise VibeError(400, "Send the command text")
    return vp.forget_command(pid, command)


def stop_run(pid: str, run_id: str) -> dict:
    run = _live(pid, run_id)
    run.stop()
    return {"run": run_id, "stopping": not run.finished}


def answer_run(pid: str, run_id: str, qid, text) -> dict:
    run = _live(pid, run_id)
    if not isinstance(qid, str) or not isinstance(text, str) or not text.strip():
        raise VibeError(400, "Send the question id and the answer")
    run.answer(qid, text.strip())
    return {"run": run_id, "ok": True}
