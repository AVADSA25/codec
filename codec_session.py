"""CODEC Session Runner — executes agent tasks in isolated subprocess.

Replaces the L.append string-building pattern with a real importable module.
All session functionality (agent loop, TTS, screenshot, corrections, queue,
streaming, command preview) is preserved.
"""
import os
import sys
import json
import time
import re
import sqlite3
import tempfile
import subprocess
import base64
import resource
import atexit
import select
import logging
from datetime import datetime

log = logging.getLogger("codec_session")


# ── Named Constants ─────────────────────────────────────────────────────────
MAX_AGENT_STEPS = 8           # Maximum steps per agent loop
COMPACTION_THRESHOLD = 22     # History length that triggers compaction
MAX_RECENT_CONTEXT = 5        # Recent messages kept raw during compaction
SELECT_TIMEOUT_SEC = 0.3      # stdin select() polling interval
MEMORY_LIMIT_MB = 512         # RLIMIT_AS cap (Linux only)
CPU_LIMIT_SEC = 120           # RLIMIT_CPU hard cap


# ── Resource Limits ──────────────────────────────────────────────────────────

def _apply_resource_limits():
    try:
        # RLIMIT_AS not available on macOS — only set CPU limit
        if hasattr(resource, "RLIMIT_AS"):
            _mem = MEMORY_LIMIT_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (_mem, _mem))
        resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_SEC, CPU_LIMIT_SEC))
    except Exception as e:
        log.warning(f"Resource limit setup failed: {e}")


# ── Screen Keywords ──────────────────────────────────────────────────────────

SCREEN_KW = [
    "look at my screen", "look at the screen", "what's on my screen",
    "whats on my screen", "read my screen", "see my screen", "screen",
    "what am i looking at", "what do you see", "look at this",
]

CORRECTION_WORDS = [
    "no i meant", "not that", "wrong", "i meant", "actually i want",
    "thats not right", "no no", "no open", "i said", "please use",
]


def needs_screen(t):
    return any(k in t.lower() for k in SCREEN_KW)


# ── Helpers ──────────────────────────────────────────────────────────────────

def strip_think(t):
    return re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL).strip()


def extract_content(rj):
    msg = rj["choices"][0]["message"]
    c = msg.get("content", "").strip()
    if c:
        return strip_think(c)
    r = msg.get("reasoning", "").strip()
    if r:
        return strip_think(r)
    return ""


def clean_resp(text):
    t = text.strip()
    for p in ["Done.", "Done:", "Done,", "Done "]:
        if t.startswith(p):
            t = t[len(p) :].strip()
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1].strip()
    return t or text


# ── Session Class ────────────────────────────────────────────────────────────

class Session:
    """A single interactive CODEC agent session."""

    def __init__(
        self,
        sys_msg: str,
        session_id: str,
        qwen_base_url: str,
        qwen_model: str,
        qwen_vision_url: str,
        qwen_vision_model: str,
        tts_voice: str,
        llm_api_key: str,
        llm_kwargs: dict,
        llm_provider: str,
        tts_engine: str,
        kokoro_url: str,
        kokoro_model: str,
        db_path: str,
        task_queue: str,
        session_alive: str,
        streaming: bool,
        agent_name: str,
        key_voice: str = "f18",
        key_text: str = "f16",
    ):
        self.sys_msg = sys_msg
        self.session_id = session_id
        self.qwen_base_url = qwen_base_url
        self.qwen_model = qwen_model
        self.qwen_vision_url = qwen_vision_url
        self.qwen_vision_model = qwen_vision_model
        self.tts_voice = tts_voice
        self.llm_api_key = llm_api_key
        self.llm_kwargs = llm_kwargs
        self.llm_provider = llm_provider
        self.tts_engine = tts_engine
        self.kokoro_url = kokoro_url
        self.kokoro_model = kokoro_model
        self.db_path = db_path
        self.task_queue = task_queue
        self.session_alive = session_alive
        self.streaming = streaming
        self.agent_name = agent_name
        self.key_voice = key_voice
        self.key_text = key_text
        self.h = []  # conversation history

        self.AGENT_SYS = f"""You are {agent_name}, an AI agent with FULL access to a Mac Studio M1 Ultra.
You can execute bash commands and AppleScript to accomplish any task.
You can also SEE the screen (via screencapture + vision) and CONTROL the mouse cursor (via pyautogui).
To click something on screen, run bash: python3.13 -c "import sys; sys.path.insert(0,'{os.path.dirname(os.path.abspath(__file__))}/skills'); from mouse_control import run; print(run('click the <element>'))"
RESPOND IN THIS EXACT JSON FORMAT:
{{ "thought": "brief plan", "action": "bash" or "applescript" or "done", "code": "command to execute", "summary": "what you did (only when action is done)" }}
RULES:
1. For URLs: bash open command. For apps: applescript.
2. Max 8 steps. Execute each fully. Dont say done until ALL complete.
3. For screen/mouse requests: use the mouse_control skill via python3.13 as shown above.
ALWAYS respond with valid JSON only.

SAFETY RULES:
- Dangerous commands (rm, sudo, etc.) will trigger a confirmation dialog on screen.
  The user must click Allow/Deny. Just execute the command — the safety system handles confirmation.
- If a command returns "Command blocked by user" or "BLOCKED", tell the user it was blocked.
- NEVER hallucinate success. If a command fails or is blocked, report the EXACT error honestly.
- NEVER claim you performed an action you did not actually execute.
- NEVER say "done" for a task unless you actually ran the command AND got a successful result.
- When the user confirms a previous request (e.g. "yes delete it"), recall the context and execute."""

        # Dangerous command patterns — single source of truth in codec_config
        _repo_dir = os.path.dirname(os.path.abspath(__file__))
        if _repo_dir not in sys.path:
            sys.path.insert(0, _repo_dir)
        from codec_config import DANGEROUS_PATTERNS, is_dangerous
        self.DANGEROUS = [p.lower() for p in DANGEROUS_PATTERNS]
        self._is_dangerous = is_dangerous

        self.SAFE_CMDS = [
            "sqlite3", "echo ", "cat ", "ls ", "pwd", "date", "uptime",
            "whoami", "sw_vers", "which ", "head ", "tail ", "wc ",
            "grep ", "screencapture", "defaults read", "open -a",
            "open http", "osascript -e 'set volume", "osascript -e 'get volume",
            "afplay ", "python3 -c \"import", "pmset", "brightness",
            "osascript -e 'tell application",
        ]

        self.ACTION_WORDS = [
            "create", "open", "delete", "move", "copy", "search", "find",
            "run", "install", "download", "check", "list", "show", "make",
            "build", "fix", "update", "write", "read", "send", "get",
            "set", "start", "stop",
        ]

    # ── Cleanup ──────────────────────────────────────────────────────────

    def cleanup(self):
        try:
            os.unlink(self.session_alive)
        except Exception as e:
            log.warning(f"Session alive file cleanup failed: {e}")
        try:
            c = sqlite3.connect(self.db_path)
            for msg in self.h:
                if msg["role"] != "system":
                    c.execute(
                        "INSERT INTO conversations (session_id, timestamp, role, content) VALUES (?,?,?,?)",
                        (self.session_id, datetime.now().isoformat(), msg["role"], msg["content"][:500]),
                    )
            c.commit()
            c.close()
            print("[C] Conversation saved to memory.")
        except Exception as e:
            log.warning(f"Conversation save to database failed: {e}")

    # ── Screenshot ───────────────────────────────────────────────────────

    def screenshot_ctx(self):
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            subprocess.run(["screencapture", "-x", tmp.name], timeout=5)
            if not os.path.exists(tmp.name) or os.path.getsize(tmp.name) < 1000:
                return ""
            with open(tmp.name, "rb") as f:
                ib = base64.b64encode(f.read()).decode()
            os.unlink(tmp.name)
            print("[C] Reading screen...")
            import requests
            r = requests.post(
                self.qwen_vision_url + "/chat/completions",
                json={
                    "model": self.qwen_vision_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + ib}},
                                {"type": "text", "text": "Read all visible text. Include app name and content. Raw text only."},
                            ],
                        }
                    ],
                    "max_tokens": 800,
                },
                timeout=60,
            )
            if r.status_code == 200:
                return r.json()["choices"][0]["message"].get("content", "")[:2000]
        except Exception as e:
            log.warning(f"Screenshot capture or vision analysis failed: {e}")
        return ""

    # ── TTS ──────────────────────────────────────────────────────────────

    def speak(self, text):
        print("[TTS] Speaking: " + text[:60])
        try:
            clean = re.sub(r"[*#`]", "", text[:300]).replace('"', "").replace("'", "").strip()
            if not clean:
                return
            if self.tts_engine == "disabled":
                return
            if self.tts_engine == "macos_say":
                subprocess.Popen(["say", "-v", self.tts_voice, clean])
                return
            import requests
            r = requests.post(
                self.kokoro_url,
                json={"model": self.kokoro_model, "input": clean, "voice": self.tts_voice},
                stream=True,
                timeout=20,
            )
            if r.status_code == 200:
                tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
                for chunk in r.iter_content(4096):
                    tmp.write(chunk)
                tmp.close()
                subprocess.Popen(["afplay", tmp.name])
        except Exception as e:
            log.warning(f"TTS playback failed: {e}")

    # ── LLM Calls ────────────────────────────────────────────────────────

    def qwen_call(self, messages):
        import requests
        headers = {"Content-Type": "application/json"}
        if self.llm_api_key:
            headers["Authorization"] = "Bearer " + self.llm_api_key
        payload = {"model": self.qwen_model, "messages": messages, "max_tokens": 500, "temperature": 0.5}
        payload.update(self.llm_kwargs)
        for attempt in range(3):
            try:
                r = requests.post(
                    self.qwen_base_url + "/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=90,
                )
                if r.status_code == 200:
                    resp = extract_content(r.json())
                    if resp:
                        return resp
            except Exception as e:
                log.warning(f"LLM API call attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return ""

    def qwen_stream(self, messages):
        import requests
        try:
            headers = {"Content-Type": "application/json"}
            if self.llm_api_key:
                headers["Authorization"] = "Bearer " + self.llm_api_key
            payload = {
                "model": self.qwen_model,
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.5,
                "stream": True,
            }
            payload.update(self.llm_kwargs)
            r = requests.post(
                self.qwen_base_url + "/chat/completions",
                json=payload,
                headers=headers,
                timeout=90,
                stream=True,
            )
            if r.status_code != 200:
                return self.qwen_call(messages)
            full = ""
            for line in r.iter_lines():
                if not line:
                    continue
                line = line.decode("utf-8")
                if line.startswith("data: "):
                    d = line[6:]
                    if d.strip() == "[DONE]":
                        break
                    try:
                        delta = json.loads(d).get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if delta:
                            sys.stdout.write(delta)
                            sys.stdout.flush()
                            full += delta
                    except Exception as e:
                        log.warning(f"Stream chunk parse failed: {e}")
            print()
            return strip_think(full).strip()
        except Exception as e:
            log.warning(f"Streaming LLM call failed, falling back to non-streaming: {e}")
            return self.qwen_call(messages)

    # ── Command Execution ────────────────────────────────────────────────

    def _cmd_preview(self, action, code):
        import tkinter as tk
        result = {"allow": False}
        root = tk.Tk()
        root.title("CODEC")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#0a0a0a")
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w, h = 480, 200
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        # Top section: header + command text on canvas
        cv = tk.Canvas(root, bg="#0a0a0a", highlightthickness=0, width=w, height=130)
        cv.pack(side="top", fill="x")
        cv.create_rectangle(1, 1, w - 1, 129, outline="#E8711A", width=1)
        cv.create_text(w // 2, 20, text="C O D E C  —  Command Preview", fill="#E8711A", font=("Helvetica", 13, "bold"))
        cv.create_line(10, 38, w - 10, 38, fill="#333")
        lbl = action.upper() + ": " + code[:120]
        cv.create_text(w // 2, 80, text=lbl, fill="#e0e0e0", font=("SF Mono", 11), width=w - 40)

        def allow():
            result["allow"] = True
            root.withdraw()
            root.quit()
            root.destroy()

        def deny():
            result["allow"] = False
            root.withdraw()
            root.quit()
            root.destroy()

        # Bottom section: buttons in a frame (not behind the canvas)
        btn_frame = tk.Frame(root, bg="#0a0a0a")
        btn_frame.pack(side="top", pady=15)
        abtn = tk.Button(btn_frame, text="\u2713 Allow", bg="#00cc55", fg="#000", font=("Helvetica", 13, "bold"), border=0, padx=20, pady=6, command=allow)
        abtn.pack(side="left", padx=10)
        dbtn = tk.Button(btn_frame, text="\u2717 Deny", bg="#888", fg="#000", font=("Helvetica", 13, "bold"), border=0, padx=20, pady=6, command=deny)
        dbtn.pack(side="left", padx=10)
        root.after(120000, deny)
        try:
            root.mainloop()
        except Exception as e:
            log.debug("Security dialog mainloop exited: %s", e)
        return result["allow"]

    def _danger_preview(self, action, code):
        """Show a RED warning preview for dangerous commands. Returns True if user approves."""
        import tkinter as tk
        result = {"allow": False}
        root = tk.Tk()
        root.title("CODEC — DANGER")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#0a0a0a")
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w, h = 520, 230
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

        cv = tk.Canvas(root, bg="#0a0a0a", highlightthickness=0, width=w, height=150)
        cv.pack(side="top", fill="x")
        cv.create_rectangle(1, 1, w - 1, 149, outline="#ff3333", width=2)
        cv.create_text(w // 2, 22, text="\u26a0  DANGEROUS COMMAND", fill="#ff3333", font=("Helvetica", 14, "bold"))
        cv.create_line(10, 42, w - 10, 42, fill="#553333")
        lbl = action.upper() + ": " + code[:140]
        cv.create_text(w // 2, 75, text=lbl, fill="#e0e0e0", font=("SF Mono", 11), width=w - 40)
        cv.create_text(w // 2, 125, text="This command can delete data. Are you sure?", fill="#ff9999", font=("Helvetica", 11))

        def allow():
            result["allow"] = True
            root.withdraw()
            root.quit()
            root.destroy()

        def deny():
            result["allow"] = False
            root.withdraw()
            root.quit()
            root.destroy()

        btn_frame = tk.Frame(root, bg="#0a0a0a")
        btn_frame.pack(side="top", pady=15)
        abtn = tk.Button(btn_frame, text="\u2713 Allow", bg="#ff3333", fg="#fff",
                         font=("Helvetica", 13, "bold"), border=0, padx=20, pady=6, command=allow)
        abtn.pack(side="left", padx=10)
        dbtn = tk.Button(btn_frame, text="\u2717 Deny", bg="#444", fg="#fff",
                         font=("Helvetica", 13, "bold"), border=0, padx=20, pady=6, command=deny)
        dbtn.pack(side="left", padx=10)
        root.after(30000, deny)  # Auto-deny after 30s
        try:
            root.mainloop()
        except Exception as e:
            log.debug("Danger preview dialog mainloop exited: %s", e)
        return result["allow"]

    def run_code(self, action, code):
        try:
            # ── Dangerous command check (uses word-boundary regex from codec_config) ──
            if self._is_dangerous(code):
                log.warning("Dangerous command flagged: %s", code.lower()[:100])
                print(f"\n[SAFETY] \u26a0\ufe0f  FLAGGED: {code[:80]}")
                with open(os.path.expanduser("~/.codec/audit.log"), "a") as _af:
                    _af.write(f'[{time.strftime("%Y-%m-%dT%H:%M:%S")}] shell_flagged: {code[:200]}\n')

                # Show danger preview dialog (works in PM2 — uses tkinter, not stdin)
                if self._danger_preview(action, code):
                    print("[SAFETY] User APPROVED dangerous command via dialog.")
                    with open(os.path.expanduser("~/.codec/audit.log"), "a") as _af:
                        _af.write(f'[{time.strftime("%Y-%m-%dT%H:%M:%S")}] APPROVED: {code[:200]}\n')
                    # Fall through to execute below
                else:
                    print("[SAFETY] User DENIED dangerous command via dialog.")
                    with open(os.path.expanduser("~/.codec/audit.log"), "a") as _af:
                        _af.write(f'[{time.strftime("%Y-%m-%dT%H:%M:%S")}] DENIED: {code[:200]}\n')
                    return "Command blocked by user. Dangerous command was denied."

            # Safe commands skip preview
            is_safe = any(code.strip().lower().startswith(s) for s in self.SAFE_CMDS)
            if not is_safe and not self._cmd_preview(action, code):
                print("[PREVIEW] Command denied by user.")
                with open(os.path.expanduser("~/.codec/audit.log"), "a") as _af:
                    _af.write(f'[{time.strftime("%Y-%m-%dT%H:%M:%S")}] PREVIEW_DENIED: {code[:200]}\n')
                return "Command denied by user via preview."

            if action == "applescript":
                r = subprocess.run(["osascript", "-e", code], capture_output=True, text=True, timeout=30)
            else:
                r = subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=30)
            out = r.stdout.strip()
            err = r.stderr.strip()
            return (out or err or "OK (no output)")[:500]
        except subprocess.TimeoutExpired:
            return "ERROR: Timeout"
        except Exception as e:
            return "ERROR: " + str(e)

    # ── Agent Loop ───────────────────────────────────────────────────────

    def run_agent(self, task):
        print("\n[CODEC-Agent] Task: " + task[:100])
        am = [
            {"role": "system", "content": self.AGENT_SYS},
            {"role": "user", "content": "Task: " + task},
        ]
        for step in range(MAX_AGENT_STEPS):
            resp = self.qwen_call(am)
            if not resp:
                return "Qwen did not respond."
            try:
                c = resp
                if "```json" in c:
                    c = c.split("```json")[1].split("```")[0]
                elif "```" in c:
                    c = c.split("```")[1].split("```")[0]
                data = json.loads(c.strip())
            except Exception as e:
                log.warning(f"Agent JSON parse failed: {e}")
                print("CODEC: " + resp)
                self.h.append({"role": "user", "content": task})
                self.h.append({"role": "assistant", "content": resp})
                return resp

            act = data.get("action", "done")
            thought = data.get("thought", "")
            code = data.get("code", "")
            summary = data.get("summary", "")

            if thought:
                print("  [Think] " + thought)
            if act == "done":
                result = summary or "Task completed."
                print("  [Done] " + result)
                self.h.append({"role": "user", "content": task})
                self.h.append({"role": "assistant", "content": result})
                return result
            if code:
                print("  [" + act + "] " + code[:80])
                output = self.run_code(act, code)
                print("  [Result] " + output[:200])
                am.append({"role": "assistant", "content": resp})
                am.append({"role": "user", "content": "Output: " + output + "\nContinue or done?"})
            else:
                am.append({"role": "assistant", "content": resp})
                am.append({"role": "user", "content": "No code. Try again or done."})
        return "Task completed (max steps)."

    # ── Corrections ──────────────────────────────────────────────────────

    def detect_correction(self, u):
        low = u.lower()
        if any(c in low for c in CORRECTION_WORDS) and len(self.h) >= 2:
            lu = la = ""
            for msg in reversed(self.h):
                if msg["role"] == "assistant" and not la:
                    la = msg["content"]
                elif msg["role"] == "user" and not lu:
                    lu = msg["content"]
                if lu and la:
                    break
            if lu:
                try:
                    c = sqlite3.connect(self.db_path)
                    c.execute(
                        "CREATE TABLE IF NOT EXISTS corrections "
                        "(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT)"
                    )
                    c.execute(
                        "INSERT INTO corrections (timestamp,original,corrected,context) VALUES (?,?,?,?)",
                        (datetime.now().isoformat(), lu[:200], u[:200], la[:200]),
                    )
                    c.commit()
                    c.close()
                    print("[C] Correction saved.")
                except Exception as e:
                    log.warning(f"Correction save to database failed: {e}")

    def get_corrections(self):
        try:
            c = sqlite3.connect(self.db_path)
            c.execute(
                "CREATE TABLE IF NOT EXISTS corrections "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, original TEXT, corrected TEXT, context TEXT)"
            )
            rows = c.execute("SELECT original,corrected FROM corrections ORDER BY id DESC LIMIT 5").fetchall()
            c.close()
            if rows:
                return "\n".join(
                    ["USER CORRECTIONS:"] + [f"M said: {o[:60]} -> corrected: {co[:60]}" for o, co in rows]
                )
        except Exception as e:
            log.warning(f"Corrections retrieval from database failed: {e}")
        return ""

    # ── Ask / Process ────────────────────────────────────────────────────

    def ask_q(self, u):
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if needs_screen(u):
            print("[C] Taking screenshot...")
            ctx = self.screenshot_ctx()
            if ctx:
                u = u + "\n\nSCREEN CONTENT:\n" + ctx
        self.h.append({"role": "user", "content": f"[{now}] {u}"})
        if self.streaming:
            sys.stdout.write("\nCODEC: ")
            sys.stdout.flush()
            resp = self.qwen_stream(self.h)
        else:
            resp = self.qwen_call(self.h)
        if resp:
            self.h.append({"role": "assistant", "content": resp})
            if len(self.h) > COMPACTION_THRESHOLD:
                try:
                    _repo_dir2 = os.path.dirname(os.path.abspath(__file__))
                    if _repo_dir2 not in sys.path:
                        sys.path.insert(0, _repo_dir2)
                    from codec_compaction import compact_context
                    compacted = compact_context(self.h[1:], max_recent=MAX_RECENT_CONTEXT)
                    self.h[:] = [self.h[0], {"role": "system", "content": compacted}] + self.h[-10:]
                except Exception as e:
                    log.warning(f"Context compaction failed, trimming history: {e}")
                    self.h[:] = self.h[:1] + self.h[-20:]
            return resp
        return "Qwen busy."

    def process_input(self, u):
        print("\nM: " + u)
        self.detect_correction(u)
        corr = self.get_corrections()
        if corr and self.h and self.h[0]["role"] == "system" and "CORRECTIONS" not in self.h[0]["content"]:
            self.h[0]["content"] = self.h[0]["content"] + "\n\n" + corr

        # ── Skill routing (before agent/LLM) ──
        if len(u) < 500:
            try:
                from codec_dispatch import check_skill, run_skill
                skill = check_skill(u)
                if skill:
                    result = run_skill(skill, u, "")
                    if result is not None:
                        print(f"\nCODEC: {result}")
                        self.speak(str(result))
                        self.h.append({"role": "user", "content": u})
                        self.h.append({"role": "assistant", "content": str(result)})
                        return
            except Exception as e:
                log.warning(f"Skill check failed: {e}")

        if any(w in u.lower().split() for w in self.ACTION_WORDS):
            done = clean_resp(self.run_agent(u))
            print("\nCODEC: " + done)
            self.speak(done)
        else:
            resp = clean_resp(self.ask_q(u))
            if not self.streaming:
                print("\nCODEC: " + resp)
            self.speak(resp)

    # ── Queue Check ──────────────────────────────────────────────────────

    def check_queue(self):
        if os.path.exists(self.task_queue):
            try:
                with open(self.task_queue) as f:
                    data = json.load(f)
                os.unlink(self.task_queue)
                return data
            except Exception as e:
                log.warning(f"Task queue read failed: {e}")
        return None

    # ── Main Loop ────────────────────────────────────────────────────────

    def run(self):
        _apply_resource_limits()

        # Write PID file
        with open(self.session_alive, "w") as pf:
            pf.write(str(os.getpid()))

        atexit.register(self.cleanup)

        # Load persistent memory
        try:
            c = sqlite3.connect(self.db_path)
            c.execute(
                "CREATE TABLE IF NOT EXISTS conversations "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, timestamp TEXT, role TEXT, content TEXT)"
            )
            rows = c.execute("SELECT role,content FROM conversations ORDER BY id DESC LIMIT 10").fetchall()
            c.close()
            if rows:
                rows.reverse()
                prev = [{"role": r, "content": ct} for r, ct in rows]
                print(f"[C] Loaded {len(prev)} messages from previous sessions.")
            else:
                prev = []
        except Exception as e:
            log.warning(f"Persistent memory load from database failed: {e}")
            prev = []

        self.h = [{"role": "system", "content": self.sys_msg}] + prev

        # Banner
        ss = "ON" if self.streaming else "OFF"
        O = "\033[38;2;232;113;26m"
        D = "\033[38;2;80;80;80m"
        W = "\033[38;2;200;200;200m"
        R = "\033[0m"
        bar = '═' * 43
        print(
            f"{O}    ╔{bar}╗\n"
            f"{O}    ║                                           ║\n"
            f"{O}    ║  ██████  ██████  ██████  ███████  ██████  ║\n"
            f"{O}    ║ ██      ██    ██ ██   ██ ██      ██       ║\n"
            f"{O}    ║ ██      ██    ██ ██   ██ █████   ██       ║\n"
            f"{O}    ║ ██      ██    ██ ██   ██ ██      ██       ║\n"
            f"{O}    ║  ██████  ██████  ██████  ███████  ██████  ║\n"
            f"{O}    ║                                   v1.5.0  ║\n"
            f"{O}    ╠{bar}╣\n"
            f"{O}    ║{W}  {self.key_voice.upper()} voice  {self.key_text.upper()} text  ** screen  ++ doc   {O}║\n"
            f"{O}    ║{W}  Hey C = wake word  type exit to close    {O}║\n"
            f"{O}    ╠{bar}╣\n"
            f"{O}    ║{D}  Stream={ss}  Memory=ON  Skills=ON          {O}║\n"
            f"{O}    ╚{bar}╝{R}"
        )

        # Process any queued task
        queued = self.check_queue()
        if queued:
            self.process_input(queued["task"])

        # Main interactive loop
        while True:
            queued = self.check_queue()
            if queued:
                self.process_input(queued["task"])
                continue
            sys.stdout.write("\nM: ")
            sys.stdout.flush()
            while True:
                queued = self.check_queue()
                if queued:
                    sys.stdout.write("\r" + " " * 60 + "\r")
                    self.process_input(queued["task"])
                    break
                try:
                    ready, _, _ = select.select([sys.stdin], [], [], SELECT_TIMEOUT_SEC)
                    if ready:
                        u = sys.stdin.readline().strip()
                        u = re.sub(r"\x1b\[[0-9;]*[a-zA-Z~]", "", u).strip()
                        if not u:
                            break
                        if u.lower() in ["exit", "quit", "bye"]:
                            self.cleanup()
                            print("\n[CODEC Session ended]")
                            sys.exit(0)
                        self.process_input(u)
                        break
                except (KeyboardInterrupt, EOFError):
                    self.cleanup()
                    print("\n[CODEC Session ended]")
                    sys.exit(0)
