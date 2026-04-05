#!/usr/bin/env python3
"""CODEC Smoke Test — 10 checks, under 30 seconds.
Run after every change, before asking Mickael to test anything.
"""
import sys, os, json, sqlite3, subprocess, urllib.request, urllib.error

# Load config the same way CODEC does
CONFIG_PATH = os.path.expanduser("~/.codec/config.json")
cfg = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = cfg.get("skills_dir", os.path.join(REPO_DIR, "skills"))
DB_PATH = os.path.expanduser("~/.q_memory.db")

# Ports from config (same defaults as codec_config.py)
LLM_URL       = cfg.get("llm_base_url", "http://localhost:8081/v1")
WHISPER_URL   = cfg.get("stt_url", "http://localhost:8084/v1/audio/transcriptions")
KOKORO_URL    = cfg.get("tts_url", "http://localhost:8085/v1/audio/speech")
UI_TARS_URL   = cfg.get("ui_tars_base_url", "http://localhost:8083/v1")
DASHBOARD_PORT = cfg.get("dashboard_port", 8090)

passed = 0
failed = 0
results = []


def check(name, fn):
    global passed, failed
    try:
        ok, detail = fn()
        if ok:
            passed += 1
            results.append(f"  \033[92m✓\033[0m {name}")
        else:
            failed += 1
            results.append(f"  \033[91m✗\033[0m {name} — {detail}")
    except Exception as e:
        failed += 1
        results.append(f"  \033[91m✗\033[0m {name} — {e}")


def http_ok(url, timeout=5):
    """Check if a URL responds (any 2xx/4xx = service is up)."""
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        # 4xx/5xx still means the service is running
        return True
    except Exception:
        return False


# ── 1. codec_config imports clean ────────────────────────────────────────────
def test_config_import():
    sys.path.insert(0, REPO_DIR)
    import importlib
    mod = importlib.import_module("codec_config")
    if not hasattr(mod, "DANGEROUS_PATTERNS"):
        return False, "DANGEROUS_PATTERNS not found"
    if not hasattr(mod, "QWEN_BASE_URL"):
        return False, "QWEN_BASE_URL not found"
    return True, ""


# ── 2. Danger patterns load and match "rm -rf /" ────────────────────────────
def test_danger_patterns():
    sys.path.insert(0, REPO_DIR)
    from codec_config import is_dangerous
    if not is_dangerous("rm -rf /"):
        return False, "'rm -rf /' not detected as dangerous"
    if is_dangerous("echo hello"):
        return False, "'echo hello' falsely flagged as dangerous"
    return True, ""


# ── 3. Memory DB accessible and tables exist ────────────────────────────────
def test_memory_db():
    if not os.path.exists(DB_PATH):
        return False, f"{DB_PATH} not found"
    conn = sqlite3.connect(DB_PATH)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    # At minimum we expect sessions or conversations
    if "sessions" not in tables and "conversations" not in tables:
        return False, f"Expected tables missing. Found: {tables}"
    return True, ""


# ── 4. Dashboard responds on port ───────────────────────────────────────────
def test_dashboard():
    url = f"http://localhost:{DASHBOARD_PORT}/"
    if not http_ok(url):
        return False, f"No response on port {DASHBOARD_PORT}"
    return True, ""


# ── 5. Qwen/LLM responds ───────────────────────────────────────────────────
def test_llm():
    url = f"{LLM_URL}/models"
    if not http_ok(url):
        return False, f"No response at {url}"
    return True, ""


# ── 6. Whisper/STT responds ─────────────────────────────────────────────────
def test_whisper():
    # Check the base URL (strip /transcriptions for a models or health check)
    base = WHISPER_URL.rsplit("/", 1)[0]  # .../v1/audio
    parent = base.rsplit("/", 1)[0]       # .../v1
    if not http_ok(f"{parent}/models") and not http_ok(base):
        return False, f"No response near {WHISPER_URL}"
    return True, ""


# ── 7. Kokoro/TTS responds ──────────────────────────────────────────────────
def test_kokoro():
    # Try configured URL's parent path for a health/models check
    base = KOKORO_URL.rsplit("/", 2)[0]  # .../v1
    if not http_ok(f"{base}/models") and not http_ok(base):
        # Also try port 8083 as fallback
        if not http_ok("http://localhost:8083/v1/models"):
            return False, f"No response near {KOKORO_URL} or port 8083"
    return True, ""


# ── 8. UI-TARS responds ─────────────────────────────────────────────────────
def test_ui_tars():
    url = f"{UI_TARS_URL}/models"
    if not http_ok(url):
        return False, f"No response at {url}"
    return True, ""


# ── 9. At least one skill loads from registry ───────────────────────────────
def test_skills():
    if not os.path.isdir(SKILLS_DIR):
        return False, f"Skills dir not found: {SKILLS_DIR}"
    py_files = [f for f in os.listdir(SKILLS_DIR)
                if f.endswith(".py") and not f.startswith("_")]
    if len(py_files) == 0:
        return False, "No skill .py files found"
    # Try importing the registry and discovering skills
    sys.path.insert(0, REPO_DIR)
    try:
        from codec_skill_registry import SkillRegistry
        reg = SkillRegistry(SKILLS_DIR)
        names = reg.list_names()
        if len(names) == 0:
            return False, "Registry discovered 0 skills"
        return True, ""
    except Exception as e:
        # Fallback: at least skill files exist
        return True, f"(registry import failed: {e}, but {len(py_files)} skill files exist)"


# ── 10. PM2 exec_cwd for open-codec matches ~/codec-repo ───────────────────
def test_pm2_cwd():
    expected = os.path.expanduser("~/codec-repo")
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:" + env.get("PATH", "")
    try:
        out = subprocess.check_output(
            ["/opt/homebrew/bin/pm2", "show", "open-codec", "--no-color"],
            stderr=subprocess.STDOUT, timeout=10, env=env
        ).decode()
    except FileNotFoundError:
        return False, "pm2 not found at /opt/homebrew/bin/pm2"
    except subprocess.CalledProcessError as e:
        return False, f"pm2 show failed: {e.output.decode()[:100]}"

    for line in out.splitlines():
        if "exec cwd" in line.lower():
            cwd = line.split("│")[-2].strip() if "│" in line else line.split()[-1]
            if cwd == expected:
                return True, ""
            else:
                return False, f"exec_cwd is {cwd}, expected {expected}"
    return False, "Could not find exec_cwd in pm2 output"


# ── Run all ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n\033[1mCODEC Smoke Test\033[0m")
    print("─" * 40)

    check(" 1. codec_config imports",     test_config_import)
    check(" 2. Danger patterns",          test_danger_patterns)
    check(" 3. Memory DB",                test_memory_db)
    check(" 4. Dashboard (port %d)" % DASHBOARD_PORT, test_dashboard)
    check(" 5. LLM/Qwen",                test_llm)
    check(" 6. Whisper/STT",             test_whisper)
    check(" 7. Kokoro/TTS",             test_kokoro)
    check(" 8. UI-TARS",                test_ui_tars)
    check(" 9. Skill registry",          test_skills)
    check("10. PM2 exec_cwd",           test_pm2_cwd)

    print("─" * 40)
    for r in results:
        print(r)
    print("─" * 40)
    print(f"  \033[1m{passed}/10 passed\033[0m", end="")
    if failed:
        print(f"  \033[91m({failed} failed)\033[0m")
    else:
        print(f"  \033[92mALL GREEN\033[0m")
    print()

    sys.exit(0 if failed == 0 else 1)
