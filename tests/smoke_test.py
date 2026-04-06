#!/usr/bin/env python3
"""CODEC Smoke Test — hit every critical endpoint, verify responses.

Run: python3 tests/smoke_test.py
Uses no auth (tests public endpoints + internal endpoints via localhost).
For authenticated endpoints, set CODEC_PIN env var.

Exit code 0 = all pass, 1 = failures detected.
"""
import requests
import time
import sys
import json
import os
import hashlib

BASE = os.environ.get("CODEC_URL", "http://localhost:8090")
PIN = os.environ.get("CODEC_PIN", "")
TIMEOUT = 30
RESULTS = []
SESSION = requests.Session()


def log(status, name, detail=""):
    icon = "✅" if status == "PASS" else "❌" if status == "FAIL" else "⚠️"
    line = f"{icon} {status:4s} | {name:40s} | {detail[:80]}"
    print(line)
    RESULTS.append({"status": status, "name": name, "detail": detail})


def authenticate():
    """Try to authenticate with PIN if provided."""
    if not PIN:
        log("SKIP", "Authentication", "No CODEC_PIN set — skipping auth tests")
        return False
    try:
        r = SESSION.post(f"{BASE}/api/auth/pin", json={"pin": PIN}, timeout=TIMEOUT)
        if r.status_code == 200 and r.json().get("authenticated"):
            log("PASS", "Authentication (PIN)", f"Session established")
            return True
        else:
            log("FAIL", "Authentication (PIN)", f"{r.status_code}: {r.text[:100]}")
            return False
    except Exception as e:
        log("FAIL", "Authentication (PIN)", str(e))
        return False


# ═══════════════════════════════════════════════
# 1. Infrastructure Health
# ═══════════════════════════════════════════════
def test_dashboard_up():
    try:
        r = requests.get(f"{BASE}/", timeout=TIMEOUT)
        if r.status_code == 200 and "CODEC" in r.text:
            log("PASS", "Dashboard (GET /)", f"HTTP {r.status_code} in {r.elapsed.total_seconds():.2f}s")
        else:
            log("FAIL", "Dashboard (GET /)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Dashboard (GET /)", str(e))


def test_health():
    try:
        r = requests.get(f"{BASE}/api/health", timeout=TIMEOUT)
        log("PASS" if r.status_code == 200 else "FAIL", "Health (GET /api/health)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Health (GET /api/health)", str(e))


def test_llm_direct():
    """Test Qwen LLM directly on port 8081."""
    try:
        r = requests.get("http://localhost:8081/v1/models", timeout=5)
        models = [m["id"] for m in r.json()["data"]]
        has_qwen = any("Qwen" in m for m in models)
        log("PASS" if has_qwen else "WARN", "LLM Models (8081)", f"{len(models)} models, Qwen={'yes' if has_qwen else 'no'}")
    except Exception as e:
        log("FAIL", "LLM Models (8081)", str(e))


def test_vision_direct():
    """Test Vision model on port 8082."""
    try:
        r = requests.get("http://localhost:8082/v1/models", timeout=5)
        models = [m["id"] for m in r.json()["data"]]
        has_vl = any("VL" in m for m in models)
        log("PASS" if has_vl else "WARN", "Vision Models (8082)", f"{len(models)} models, VL={'yes' if has_vl else 'no'}")
    except Exception as e:
        log("FAIL", "Vision Models (8082)", str(e))


def test_whisper():
    """Test Whisper STT on port 8084."""
    try:
        r = requests.get("http://localhost:8084/", timeout=5)
        # 404 is OK — Whisper only has POST endpoints
        log("PASS" if r.status_code in (200, 404, 405) else "FAIL",
            "Whisper STT (8084)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Whisper STT (8084)", str(e))


def test_tts():
    """Test Kokoro TTS on port 8085."""
    try:
        r = requests.get("http://localhost:8085/", timeout=5)
        log("PASS" if r.status_code in (200, 404, 405) else "FAIL",
            "Kokoro TTS (8085)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Kokoro TTS (8085)", str(e))


# ═══════════════════════════════════════════════
# 2. API Endpoints (require auth)
# ═══════════════════════════════════════════════
def test_status():
    try:
        r = SESSION.get(f"{BASE}/api/status", timeout=TIMEOUT)
        if r.status_code == 200:
            log("PASS", "Status (GET /api/status)", f"alive={r.json().get('alive')}")
        elif r.status_code == 401:
            log("SKIP", "Status (GET /api/status)", "Auth required")
        else:
            log("FAIL", "Status (GET /api/status)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Status (GET /api/status)", str(e))


def test_conversations():
    try:
        r = SESSION.get(f"{BASE}/api/conversations?limit=5", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            log("PASS", "Conversations (GET /api/conversations)", f"{len(data)} items")
        elif r.status_code == 401:
            log("SKIP", "Conversations (GET /api/conversations)", "Auth required")
        else:
            log("FAIL", "Conversations (GET /api/conversations)", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Conversations (GET /api/conversations)", str(e))


def test_chat_sessions():
    try:
        r = SESSION.get(f"{BASE}/api/qchat/sessions", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            log("PASS", "Chat Sessions (GET /api/qchat/sessions)", f"{len(data)} sessions")
        elif r.status_code == 401:
            log("SKIP", "Chat Sessions", "Auth required")
        else:
            log("FAIL", "Chat Sessions", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Chat Sessions", str(e))


def test_chat_search():
    try:
        r = SESSION.get(f"{BASE}/api/qchat/search?q=hello", timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            log("PASS", "Chat Search (GET /api/qchat/search)", f"{len(data)} results for 'hello'")
        elif r.status_code == 401:
            log("SKIP", "Chat Search", "Auth required")
        else:
            log("FAIL", "Chat Search", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Chat Search", str(e))


def test_flash_command():
    """Test Flash (Quick Chat) /api/command."""
    try:
        r = SESSION.post(f"{BASE}/api/command",
                         json={"task": "what is 2+2?", "source": "smoke_test"},
                         timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            log("PASS", "Flash Command (POST /api/command)", f"status={data.get('status')}")
        elif r.status_code == 401:
            log("SKIP", "Flash Command", "Auth required")
        else:
            log("FAIL", "Flash Command", f"HTTP {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log("FAIL", "Flash Command", str(e))


def test_chat_llm():
    """Test Chat LLM call (non-streaming)."""
    try:
        r = SESSION.post(f"{BASE}/api/chat",
                         json={"messages": [{"role": "user", "content": "Say hi in one word"}],
                               "stream": False, "thinking": False},
                         timeout=60)
        if r.status_code == 200:
            data = r.json()
            resp = data.get("response", "")[:80]
            log("PASS", "Chat LLM (POST /api/chat)", f"Response: {resp}")
        elif r.status_code == 401:
            log("SKIP", "Chat LLM", "Auth required")
        else:
            log("FAIL", "Chat LLM", f"HTTP {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log("FAIL", "Chat LLM", str(e))


def test_chat_skill_calculator():
    """Test Chat skill routing — calculator."""
    try:
        r = SESSION.post(f"{BASE}/api/chat",
                         json={"messages": [{"role": "user", "content": "calculate 15 * 37"}],
                               "stream": False},
                         timeout=30)
        if r.status_code == 200:
            data = r.json()
            has_skill = data.get("skill") == "calculator"
            resp = data.get("response", "")[:80]
            if has_skill and "555" in resp:
                log("PASS", "Chat Skill: calculator", resp)
            elif "555" in resp:
                log("PASS", "Chat Skill: calculator (via LLM)", resp)
            else:
                log("WARN", "Chat Skill: calculator", f"skill={data.get('skill')}, resp={resp}")
        elif r.status_code == 401:
            log("SKIP", "Chat Skill: calculator", "Auth required")
        else:
            log("FAIL", "Chat Skill: calculator", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Chat Skill: calculator", str(e))


def test_chat_skill_weather():
    """Test Chat skill routing — weather."""
    try:
        r = SESSION.post(f"{BASE}/api/chat",
                         json={"messages": [{"role": "user", "content": "weather in Marbella"}],
                               "stream": False},
                         timeout=30)
        if r.status_code == 200:
            data = r.json()
            has_skill = data.get("skill") == "weather"
            resp = data.get("response", "")[:80]
            if has_skill:
                log("PASS", "Chat Skill: weather", resp)
            else:
                log("WARN", "Chat Skill: weather (LLM fallback)", resp)
        elif r.status_code == 401:
            log("SKIP", "Chat Skill: weather", "Auth required")
        else:
            log("FAIL", "Chat Skill: weather", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Chat Skill: weather", str(e))


def test_notifications():
    try:
        r = SESSION.get(f"{BASE}/api/notifications/count", timeout=TIMEOUT)
        if r.status_code == 200:
            log("PASS", "Notifications count", json.dumps(r.json()))
        elif r.status_code == 401:
            log("SKIP", "Notifications count", "Auth required")
        else:
            log("FAIL", "Notifications count", f"HTTP {r.status_code}")
    except Exception as e:
        log("FAIL", "Notifications count", str(e))


# ═══════════════════════════════════════════════
# 3. Pages Load
# ═══════════════════════════════════════════════
def test_pages():
    pages = [("/chat", "Chat"), ("/voice", "Voice"), ("/vibe", "Vibe"), ("/tasks", "Tasks")]
    for path, name in pages:
        try:
            r = SESSION.get(f"{BASE}{path}", timeout=TIMEOUT)
            if r.status_code == 200:
                log("PASS", f"Page: {name} ({path})", f"HTTP {r.status_code}")
            elif r.status_code in (302, 401):
                log("SKIP", f"Page: {name} ({path})", "Auth redirect")
            else:
                log("FAIL", f"Page: {name} ({path})", f"HTTP {r.status_code}")
        except Exception as e:
            log("FAIL", f"Page: {name} ({path})", str(e))


# ═══════════════════════════════════════════════
# Run All Tests
# ═══════════════════════════════════════════════
def main():
    print("=" * 80)
    print("  CODEC SMOKE TEST")
    print(f"  Target: {BASE}")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()

    # Infrastructure (no auth needed)
    print("── Infrastructure ──")
    test_dashboard_up()
    test_health()
    test_llm_direct()
    test_vision_direct()
    test_whisper()
    test_tts()
    print()

    # Auth
    print("── Authentication ──")
    authed = authenticate()
    print()

    # API Endpoints
    print("── API Endpoints ──")
    test_status()
    test_conversations()
    test_chat_sessions()
    test_chat_search()
    test_flash_command()
    test_notifications()
    print()

    # LLM + Skills
    print("── LLM & Skills ──")
    test_chat_llm()
    test_chat_skill_calculator()
    test_chat_skill_weather()
    print()

    # Pages
    print("── Pages ──")
    test_pages()
    print()

    # Summary
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] in ("SKIP", "WARN"))
    total = len(RESULTS)

    print("=" * 80)
    print(f"  RESULTS: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed == 0:
        print("  🟢 ALL CRITICAL TESTS PASSED")
    else:
        print("  🔴 FAILURES DETECTED")
    print("=" * 80)

    # Write results to file for CI
    results_path = os.path.join(os.path.dirname(__file__), "smoke_results.json")
    with open(results_path, "w") as f:
        json.dump({"timestamp": time.strftime('%Y-%m-%dT%H:%M:%S'),
                    "passed": passed, "failed": failed, "skipped": skipped,
                    "results": RESULTS}, f, indent=2)
    print(f"\n  Results saved to: {results_path}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
