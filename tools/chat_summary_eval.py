#!/usr/bin/env python3
"""Deep Chat notes on a long chat (P-038, docs/CHAT-SUMMARY-DESIGN.md): recall and prefill on the live LOCAL model.

Not part of CI: it needs the running local model and takes about half an hour, and it holds the model server while
it runs, so it refuses to start during a voice call or an image job. It never calls a cloud model: it refuses to
start when the active model URL is not on this Mac. The chat is tools/chat_summary_fixture.json, 60 made-up turns
with 20 questions; CODEC's replies are the fixture's, not the model's.

1. Replay: turns 1 to 60 go through the real code, routes.chat._fit_prompt_budget and
   codec_chat_summary.fit_with_notes, one after the other. A notes job planned at a turn runs before the next turn
   (in the dashboard it runs in the background after the reply). From turn 30 on, each turn's prompt is sent with
   max_tokens 1 and its prefill time is read from the model-server log (counts and timings only).
2. Recall: each question is asked after turn 60, once with notes and once with the notes switched off (P-034 alone,
   for comparison), and the answer is checked against the fixture's expected words.

Bars: recall with notes at least 18 of 20; prefill of every turn after 30 within 1.2x of turn 30, with no other
request on the model server during a measured prefill (else the run is DISTURBED; run it again when it is idle).

    python3 tools/chat_summary_eval.py --out r.json          run both parts, write the numbers, exit 0 when both bars are met
    python3 tools/chat_summary_eval.py --check r.json        recompute both bars from a result file; no model needed
    python3 tools/chat_summary_eval.py --check r.json --bar recall      exit status from one bar only
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

FIXTURE = REPO / "tools" / "chat_summary_fixture.json"
CONFIG = os.path.expanduser("~/.codec/config.json")
VOICE_MARKER = os.path.expanduser("~/.codec/voice_session.json")
IMAGE_LOCK = os.path.expanduser("~/.codec/image_job.lock")
SERVER_LOG = os.path.expanduser("~/.pm2/logs/qwen3.6-error.log")
BUDGET = 12000
MEASURE_FROM = 30
RECALL_BAR = 18
PREFILL_RATIO = 1.2
SCHEMA = 1
# A stand-in for the chat's system prompt (about 1,000 estimated tokens), so the run does not import the dashboard.
SYSTEM = (
    "You are CODEC, a private assistant that runs on the user's own Mac. You help with planning, writing, research "
    "and everyday questions. Answer directly and briefly unless the user asks for detail. Use plain language and "
    "short paragraphs; use a list only when the content is a list. When the user gives you facts about their plans, "
    "keep them consistent across the conversation, and when a later message changes an earlier fact, use the newer "
    "one. Do not invent names, numbers, dates or places: when you do not know something, say so and say what would "
    "help you find out. Never claim to have done something you have not done, such as sending a message, saving a "
    "file or booking anything. When you draft a message for the user, write it so they can copy it as it is. If a "
    "request is unclear, make the most reasonable reading, say it in one line, and go ahead. Keep the user's privacy: "
    "nothing they tell you leaves this Mac unless they ask for it. Respect the user's time: no filler, no repeated "
    "summaries of what they just said, no apologies for things that are not your fault. For dates and times, write "
    "them the way the user does. For money, keep the currency the user uses. For people, use the names the user "
    "gives. For tasks with several steps, give the steps in order, each with what done looks like. When you give "
    "advice, give the one recommendation you would follow yourself and the main reason, then stop. When the user "
    "asks what was said earlier in the conversation, answer from the conversation itself, and if that part is no "
    "longer available to you, say so rather than guess. When the user is stressed or overloaded, be kind and "
    "practical: help them pick what matters most now. When the user asks for a timetable, a checklist or a draft, "
    "make it complete enough to use without another question. Keep track of open questions and remind the user of "
    "them when it helps. Do not use emoji. Do not use headings in short answers.")
ANSWER_HINT = " Answer in one short sentence."


class NotLocal(RuntimeError):
    """The active model URL is not on this Mac."""


class Busy(RuntimeError):
    """A voice call or an image job is on, or the model server does not answer."""


# ── The fixture ──────────────────────────────────────────────────────────────

def load_fixture(path=FIXTURE) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reply(data: dict, i: int) -> str:
    """CODEC's reply to turn i (0-based): its own text, then four paragraphs of the topic's general advice."""
    turn = data["turns"][i]
    pool = data["paragraphs"][turn["topic"]]
    picks = [pool[(4 * i + k) % len(pool)] for k in range(4)]
    return "\n\n".join([turn["codec"]] + picks)


def messages_at(data: dict, turn: int, question: str | None = None) -> list:
    """What the page sends at `turn` (1-based): the system message, turns 1 to turn-1 with CODEC's replies, then
    turn `turn`'s message, or `question` after the whole chat when `question` is given."""
    upto = len(data["turns"]) if question is not None else turn - 1
    out = [{"role": "system", "content": SYSTEM}]
    for i in range(upto):
        out.append({"role": "user", "content": data["turns"][i]["user"]})
        out.append({"role": "assistant", "content": reply(data, i)})
    out.append({"role": "user", "content": question + ANSWER_HINT if question is not None
                else data["turns"][turn - 1]["user"]})
    return out


def answered(answer: str, alternatives: list) -> bool:
    """The answer holds every word of at least one expected alternative (case and spacing ignored)."""
    text = re.sub(r"\s+", " ", (answer or "").lower().replace("’", "'"))
    return any(all(word.lower() in text for word in alt) for alt in alternatives)


# ── Verdicts (also used by --check on a saved result file) ──────────────────

def recall_verdict(questions: list) -> dict:
    passed = sum(1 for q in questions if q.get("pass"))
    return {"passed": passed, "total": len(questions), "met": passed >= RECALL_BAR and len(questions) == 20,
            "from_notes_only": sum(1 for q in questions if q.get("notes_only")),
            "passed_from_notes_only": sum(1 for q in questions if q.get("notes_only") and q.get("pass"))}


def prefill_verdict(turns: list) -> dict:
    rows = {r["turn"]: r for r in turns if r.get("prefill_s") is not None}
    base = rows.get(MEASURE_FROM)
    later = [r for t, r in sorted(rows.items()) if t > MEASURE_FROM]
    disturbed = [r["turn"] for r in rows.values() if r.get("disturbed")]
    if base is None or not later or base["prefill_s"] <= 0:
        return {"met": False, "reason": "turn 30 or the later turns were not measured", "disturbed": disturbed}
    worst = max(later, key=lambda r: r["prefill_s"])
    ratio = worst["prefill_s"] / base["prefill_s"]
    expected = max(r["turn"] for r in turns) - MEASURE_FROM
    return {"met": ratio <= PREFILL_RATIO and not disturbed and len(later) == expected,
            "turn30_s": base["prefill_s"], "worst_turn": worst["turn"], "worst_s": worst["prefill_s"],
            "ratio": round(ratio, 3), "measured_after_30": len(later), "expected_after_30": expected,
            "disturbed": disturbed}


# ── The live model ───────────────────────────────────────────────────────────

def require_local(base_url: str) -> str:
    import codec_cloud_models as ccm
    if not ccm.is_local_url(base_url):
        raise NotLocal(f"The active model URL ({base_url}) is not on this Mac. This eval runs on the local model "
                       "only and never calls a cloud URL.")
    return base_url


def _pid_alive(path: str) -> bool:
    try:
        pid = int(Path(path).read_text().split()[0])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, IndexError):
        return False


def require_idle() -> None:
    if os.path.exists(VOICE_MARKER):
        raise Busy("A voice session is open (~/.codec/voice_session.json). Run the eval when no call is on.")
    if os.path.exists(IMAGE_LOCK) and _pid_alive(IMAGE_LOCK):
        raise Busy("An image job is running (~/.codec/image_job.lock). Run the eval when it has finished.")


class ServerLog:
    """Reads the model server's own lines after an offset: prefill timings and in-flight counts, never prompt text."""

    def __init__(self, path=SERVER_LOG):
        self.path = path
        self.offset = self._size()

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def mark(self) -> None:
        self.offset = self._size()

    def since_mark(self) -> list:
        size = self._size()
        start = self.offset if size >= self.offset else 0     # the log was rotated
        try:
            with open(self.path, "rb") as f:
                f.seek(start)
                data = f.read(size - start)
        except OSError:
            return []
        return data.decode("utf-8", "replace").splitlines()


_PREFILL = re.compile(r"Prefill completed: request=(\S+) prompt_tokens=(\d+) cached_tokens=(\d+) elapsed=([\d.]+)s")
_STARTED = re.compile(r"Request started: .* in_flight=(\d+)")


def read_prefill(lines: list, prompt_tokens: int | None) -> dict:
    """The prefill line for a request of `prompt_tokens`, and whether another request shared the server."""
    found = [(m.group(1), int(m.group(2)), int(m.group(3)), float(m.group(4)))
             for m in map(_PREFILL.search, lines) if m]
    started = [int(m.group(1)) for m in map(_STARTED.search, lines) if m]
    mine = [f for f in found if prompt_tokens is None or f[1] == prompt_tokens]
    others = {f[0] for f in found if f not in mine}
    if not mine:
        return {"prefill_s": None, "disturbed": True, "server_prompt_tokens": None}
    _, tokens, cached, elapsed = mine[-1]
    return {"prefill_s": elapsed, "server_prompt_tokens": tokens, "cached_tokens": cached,
            "disturbed": bool(others) or len(started) != 1 or any(n > 1 for n in started)}


def measure(sent: list, llm: dict, log: ServerLog, tries: int = 3) -> dict:
    """Prefill of `sent` (max_tokens 1), from the server log. A measurement that shared the server with another
    request is taken again, up to `tries` times; the last one is kept and says whether it was disturbed."""
    import codec_llm
    for attempt in range(1, tries + 1):
        require_idle()
        log.mark()
        usage: dict = {}
        t0 = time.monotonic()
        codec_llm.call(sent, base_url=llm["base_url"], model=llm["model"], api_key="", max_tokens=1,
                       temperature=0.0, timeout=600, enable_thinking=False, usage_out=usage)
        wall = time.monotonic() - t0
        time.sleep(0.5)                       # let the server finish its log lines
        out = read_prefill(log.since_mark(), usage.get("prompt_tokens"))
        out.update(wall_s=round(wall, 2), attempts=attempt)
        if not out["disturbed"]:
            break
    return out


def ask(sent: list, llm: dict) -> str:
    import codec_llm
    require_idle()
    return codec_llm.call(sent, base_url=llm["base_url"], model=llm["model"], api_key="", max_tokens=160,
                          temperature=0.0, timeout=600, enable_thinking=False)


def run(data: dict, llm: dict, log: ServerLog, verbose: bool = True) -> dict:
    import codec_chat_summary as notes
    import routes.chat as chat
    notes.clear()
    on, off = {}, {"chat_summary": False}
    turns = []
    for t in range(1, len(data["turns"]) + 1):
        sent, trim, job = notes.fit_with_notes(messages_at(data, t), BUDGET, on, fit=chat._fit_prompt_budget, llm=llm)
        row = {"turn": t, "estimated_tokens": chat.estimate_prompt_tokens(sent),
               "summarised": (trim or {}).get("summarised", 0), "dropped": (trim or {}).get("dropped", 0)}
        if t >= MEASURE_FROM:
            row.update(measure(sent, llm, log))
        if job is not None:
            require_idle()
            t0 = time.monotonic()
            done = job.run()
            row["job"] = {"from": job.start_at, "to": job.end, "chunks": len(job.chunks()),
                          "seconds": round(time.monotonic() - t0, 1), "stopped": job.stopped,
                          "notes_words": len(done.text.split()) if done else 0}
        turns.append(row)
        if verbose:
            print(f"turn {t:2d}: {row['estimated_tokens']:6d} est. tokens, {row['summarised']:3d} summarised, "
                  f"{row['dropped']:3d} left out" + (f", prefill {row['prefill_s']}s" if row.get("prefill_s") else "")
                  + (f", notes job {row['job']['seconds']}s" if row.get("job") else ""), flush=True)
    questions = []
    for n, q in enumerate(data["questions"], 1):
        msgs = messages_at(data, 0, q["q"])
        source = data["turns"][q["turn"] - 1]["user"]
        sent, trim, _ = notes.fit_with_notes(msgs, BUDGET, on, fit=chat._fit_prompt_budget, llm=llm)
        plain, _, _ = notes.fit_with_notes(msgs, BUDGET, off, fit=chat._fit_prompt_budget, llm=llm)
        a_notes, a_plain = ask(sent, llm), ask(plain, llm)
        row = {"id": n, "turn": q["turn"], "question": q["q"],
               "notes_only": not any(m.get("content") == source for m in sent),
               "pass": answered(a_notes, q["answers"]), "answer": a_notes[:300],
               "pass_without_notes": answered(a_plain, q["answers"]), "answer_without_notes": a_plain[:300]}
        questions.append(row)
        if verbose:
            print(f"Q{n:2d} (turn {q['turn']:2d}{', notes only' if row['notes_only'] else ''}): "
                  f"{'PASS' if row['pass'] else 'FAIL'} with notes, "
                  f"{'pass' if row['pass_without_notes'] else 'fail'} without", flush=True)
    return {"turns": turns, "questions": questions}


def _git_sha() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO,
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return sha + ("+uncommitted changes" if dirty and sha else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def report(result: dict, bar: str = "both") -> int:
    """Print both verdicts; exit status 0 when the `bar` asked for (recall, prefill or both) is met."""
    rec, pre = recall_verdict(result["questions"]), prefill_verdict(result["turns"])
    without = sum(1 for q in result["questions"] if q.get("pass_without_notes"))
    print(f"RECALL {'BAR MET' if rec['met'] else 'BAR NOT MET'}: {rec['passed']}/{rec['total']} with notes "
          f"({rec['passed_from_notes_only']}/{rec['from_notes_only']} from notes only), {without}/{rec['total']} without")
    if pre.get("ratio") is not None:
        print(f"PREFILL {'BAR MET' if pre['met'] else ('DISTURBED' if pre['disturbed'] else 'BAR NOT MET')}: "
              f"turn 30 {pre['turn30_s']}s, worst after it turn {pre['worst_turn']} {pre['worst_s']}s, "
              f"ratio {pre['ratio']} (bar {PREFILL_RATIO}), measured {pre['measured_after_30']}/{pre['expected_after_30']}"
              + (f", other requests during turns {pre['disturbed']}" if pre["disturbed"] else ""))
    else:
        print(f"PREFILL BAR NOT MET: {pre['reason']}")
    met = {"recall": rec["met"], "prefill": pre["met"], "both": rec["met"] and pre["met"]}[bar]
    return 0 if met else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="write the result file here")
    ap.add_argument("--check", help="recompute the bars from a result file (no model needed)")
    ap.add_argument("--bar", choices=("both", "recall", "prefill"), default="both",
                    help="with --check: which bar decides the exit status")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        return report(json.loads(Path(args.check).read_text(encoding="utf-8")), args.bar)
    try:
        cfg = json.loads(Path(CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    base_url = cfg.get("llm_base_url") or "http://localhost:8083/v1"
    model = cfg.get("llm_model") or "mlx-community/Qwen3.6-35B-A3B-4bit"
    try:
        require_local(base_url)
        require_idle()
    except (NotLocal, Busy) as e:
        print(f"REFUSED: {e}")
        return 2
    if not os.path.exists(SERVER_LOG):
        print(f"REFUSED: the model-server log {SERVER_LOG} is missing, so prefill cannot be read.")
        return 2
    llm = {"base_url": base_url, "model": model, "api_key": ""}
    started = datetime.datetime.now().isoformat(timespec="seconds")
    sha = _git_sha()        # the code this run loads, taken before it starts
    try:
        result = run(load_fixture(), llm, ServerLog(), verbose=not args.quiet)
    except Busy as e:
        print(f"STOPPED: {e}")
        return 2
    result.update({"schema": SCHEMA, "model": model, "base_url": base_url, "git_sha": sha,
                   "started": started, "finished": datetime.datetime.now().isoformat(timespec="seconds"),
                   "budget": BUDGET, "recall": recall_verdict(result["questions"]),
                   "prefill": prefill_verdict(result["turns"])})
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return report(result)


if __name__ == "__main__":
    sys.exit(main())
