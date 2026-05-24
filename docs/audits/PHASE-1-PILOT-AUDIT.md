# Phase-1 Security Audit — CODEC Pilot (browser-automation subsystem)

**Date:** 2026-05-24
**Scope:** `~/codec/pilot/` — a **separate git repo**, not part of `codec-repo`. ~3,900 LOC,
14 modules. The deferred half of Audit B (`PHASE-1-PROJECTS-PILOT.md`).
**Method:** 5-pass parallel specialist review (security · red-team · architecture ·
correctness · test-coverage), every CRITICAL code-verified by hand against the source +
live system state (`pm2`, `lsof`, `~/.cloudflared/config.yml`).
**Reviewer:** Claude (Sonnet) for AVA Digital LLC.

---

## 🔴 URGENT — a LIVE internet-exposed unauthenticated RCE was found and stopped

At audit time, **`pilot-runner` was online (17h uptime), listening on `*:8094` (all
interfaces), published to the public internet at `pilot.lucyvpa.com`** via Cloudflare tunnel
(`~/.cloudflared/config.yml:21-22`), with **zero authentication** on the FastAPI app
(`pilot_runner.py:88` CORS `["*"]`, `:629` `host="0.0.0.0"`). Any unauthenticated caller
could drive the user's logged-in headless Chrome **and** compile+approve a skill →
arbitrary code execution on the Mac.

**Mitigation taken (with operator approval, 2026-05-24):** `pm2 stop pilot-runner` —
verified nothing now listens on `:8094`. The Cloudflare route still exists.

**Status (2026-05-24):** the full fix wave (PP-1…PP-12) is committed to the Pilot repo's
local `main`, but those commits have **not been pushed/deployed** and the running daemon is
still the pre-fix code. ⚠️ **Before `pm2 start pilot-runner`:** (1) review + deploy the PP-1…
PP-12 commits, (2) remove the `pilot.lucyvpa.com` lines from `~/.cloudflared/config.yml`
(the token gate makes the public tunnel unnecessary — keep Pilot loopback-only). Until both
are done, leave `pilot-runner` stopped.

---

## Findings (P = Pilot)

### P-1 — Unauthenticated control plane on 0.0.0.0 + public Cloudflare tunnel [CRITICAL — LIVE]
**What:** `pilot_runner.py` binds `host="0.0.0.0"` (`:629`), CORS `allow_origins=["*"]`
(`:88`), and has **no auth on any route** (no `Depends`, token, or middleware — only the
CORS middleware exists). It is internet-published at `pilot.lucyvpa.com →
http://localhost:8094` (`~/.cloudflared/config.yml:21-22`) through a bare ingress with **no
Cloudflare Access policy**. **Verified live** (`pm2` online, `lsof` `*:8094 LISTEN`).
**Why it matters:** the parent engine spent PR-2A (loopback default + refuse-unsafe-start),
PR-2D (HMAC internal token), PR-2B (Keychain) hardening its own surface; Pilot re-opens a
fully unauthenticated control plane reachable from the internet. This is the parent's exact
D-7 finding, un-fixed, and worse (publicly tunneled). It is the enabling layer for P-2/P-5.
**Fix:** bind `127.0.0.1`; add the parent's `x-internal-token` (reuse
`codec_keychain.get_internal_token()`) or `dashboard_token` on all routes; restrict CORS to
explicit origins; add `_check_dashboard_start_safety`-style refuse-unsafe-start; gate the
tunnel behind Cloudflare Access (or drop it).

### P-2 — Trace→skill compiler injects `run.task` raw into a docstring → RCE [CRITICAL]
**What:** `compiler.py` builds skill source with an f-string whose docstring contains
`Goal     : {run.task}` **un-escaped** inside a `"""…"""` block (note `SKILL_DESCRIPTION =
{run.task!r}` on the same template IS `repr`-quoted — confirming the docstring slot is the
specific hole). A `run.task` containing `"""` closes the docstring; following lines become
module-level Python that runs at skill **import**. `run.task` comes from the LLM agent or
the unauthenticated `POST /run` body. Same class: `scroll` `amount` is interpolated into a
JS string without `int()` casting (`compiler.py` `_compile_steps`, `replay.py` scroll) →
JS/Python injection.
**Why it matters:** chained with P-1 + P-3, a network attacker authors **and** approves a
skill in two unauthenticated calls → persistent RCE in the CODEC process (secret/SSH-key
exfil, permanent malicious skill).
**Fix:** `repr()`/escape every trace-derived field (task, result, url, text, amount) before
emitting source; `int()`-cast numerics; prefer the data-trace + fixed-loader template (the
`Replayer` path) and drop raw-source emit; `compile()`-validate before writing.

### P-3 — Skill "approval" is a bare file-move with no safety gate [CRITICAL]
**What:** `skill_review.py:155-175` `approve_pending()` = `shutil.move` from
`~/.codec/skills/.pending/` to `~/.codec/skills/`. **No `is_dangerous_skill_code`, no
hash-manifest entry, no audit emit.** The HITL "review" shows a docstring preview that
renders a P-2 payload as harmless comment text. Approve + compile endpoints are
unauthenticated (P-1).
**Why it matters:** diverges from the parent's PR-1A chokepoint (load-time AST gate +
SHA-256 manifest). "Approved" carries none of the parent's guarantees.
**Fix:** run `codec_config.is_dangerous_skill_code` inside `approve_pending` and refuse on
danger; emit a `skill_load_blocked`-style audit; require auth; never auto-approve.

### P-4 — No SSRF / URL-scheme guard on `/navigate` [CRITICAL]
**What:** `pilot_runner.py:215` → `pilot_chrome.navigate()` passes any URL to Playwright
`goto()` with no scheme/host allowlist. `_parse_action` accepts `file://`, `javascript:`,
`http://169.254.169.254` (cloud metadata), internal `http://127.0.0.1:<port>` (the CODEC
dashboard :8090, the local LLM, the real Chrome CDP :9222).
**Why it matters:** with P-1, an unauthenticated caller turns Pilot into an SSRF pivot +
local-file reader (`file:///…` → rendered into `/snapshot`/`/screenshot` → exfil).
**Fix:** allowlist `http`/`https` only; block RFC1918 / loopback / link-local / `file:` /
`chrome:` before `goto`.

### P-5 — Unauthenticated hijack of the persistent logged-in browser profile [HIGH]
**What:** Pilot Chrome uses a persistent, logged-in profile (`pilot_chrome.py`). Via P-1,
`POST /navigate` + `GET /snapshot`/`/screenshot` + `/click`/`/type` (unauth) drive that
authenticated session anywhere and read it back.
**Why it matters:** session/credential theft and on-behalf actions (send, transfer, change
settings) on every site the user is signed into — no compiler needed.
**Fix:** P-1 (auth) closes the remote path; add per-action consent for sensitive sites
(P-7).

### P-6 — Prompt injection: page DOM steers the agent/replay LLM, no trust delimiting [HIGH]
**What:** `pilot_agent.py:264` `render_for_llm(snapshot)` concatenates attacker-controlled
element names/labels/hrefs straight into the "What's your next action?" user turn; same in
`replay.py:331` LLM-rescue. No instruction/data separation, no untrusted delimiter.
**Why it matters:** a malicious page can embed text that redirects the agent (navigate,
type, run a skill) — OWASP-Agentic A1 — and the injected actions feed the trace compiler
(P-2).
**Fix:** wrap page content in explicit untrusted delimiters; instruct the model to treat
element text as data only; constrain `navigate` to a task-scoped origin allowlist;
delimit tool output as an untrusted block (mirror the parent's PR-7G/B-2a guidance).

### P-7 — HITL is advisory, default-open, and unauthenticated [HIGH]
**What:** `hitl.py` `_pause_event.set()` means the agent starts UNpaused (consent is opt-in
pause, not opt-in approval); the base `PilotAgent.execute()` path has no HITL at all; and
`/hitl/{id}/resume|inject|handback|takeover` (`pilot_runner.py:401-438`) are unauthenticated
— the "human in the loop" and an attacker are the same anonymous caller. No literal-verb
strict-consent for destructive browser actions (submit payment, send, delete).
**Fix:** default-deny sensitive actions; require explicit per-action approval; gate HITL
endpoints behind auth; add strict-consent for destructive form submits.

### P-8 — Chrome CDP debug port 9223 is hijackable by any local process [HIGH]
**What:** `pilot_chrome.py:67` launches with `--remote-debugging-port=9223` (fixed,
predictable; no `--remote-debugging-address`). Chrome's CDP socket has no auth; any local
user-mode process can attach (`/json` → WebSocket → `Network.getAllCookies`,
`Runtime.evaluate`) and take over the logged-in profile.
**Why it matters:** matches the parent's local-malware threat model (D-1/D-11). Independent
of the :8094 API.
**Fix:** randomize the port per launch; prefer Playwright pipe transport
(`--remote-debugging-pipe`) and drop the TCP port if external CDP isn't needed. *VERIFY the
socket isn't `0.0.0.0` in this Chromium build (`lsof -iTCP:9223`); if it is → CRITICAL.*

### P-9 — Concurrent runs share one global browser page → corruption; dead lock [HIGH]
**What:** one global `_pilot`/`_page` (`pilot_runner.py:59`); `POST /run/{id}/start` and
`/replay` spawn background tasks with no concurrency guard (only manual recording 409s).
`_lock = asyncio.Lock()` (`:63`) is declared but never acquired; `_runs`/`_hitl`/`_recording`
are read-modify-written unguarded.
**Why it matters:** two runs interleave navigate/click/snapshot on one tab → wrong-element
clicks, wrong-DOM snapshots; correctness + a safety hazard (a click lands on the wrong
control).
**Fix:** single-active-run guard (409) or a `BrowserContext`/page per run; actually use
`_lock` around shared-state mutations.

### P-10 — Replay re-executes irreversible actions across fallback tiers [MEDIUM]
**What:** `replay.py` XPath→CSS→LLM ladder retries the same `click`/`type`; a click that
submitted a form but timed out on the post-wait re-fires on the next tier; LLM-rescue clicks
a *different* element by name.
**Fix:** don't auto-escalate tiers after an attempt that may have landed for
state-mutating actions; gate destructive replays behind consent.

### P-11 — Slug path/glob traversal in skill review [MEDIUM]
**What:** `skill_review.py` `get/approve/reject_pending` interpolate the URL `{slug}` into a
`glob(f"pilot_{slug}*.py")` + `Path` without re-`slugify()`; `*`/`?`/`[`/`..` reachable on
the unauthenticated endpoints. **Fix:** `slugify` + `^[a-z0-9_]+$` reject at the top of all
three.

### P-12 — Pilot is invisible to the parent's audit log [MEDIUM]
**What:** no `~/.codec/audit.log` emits anywhere in Pilot (navigation, typing into
logged-in sites, skill writes). **Fix:** a thin audit adapter at the action chokepoint +
approve/reject, reusing the parent envelope.

### P-13 — Secrets typed into pages persisted cleartext in traces + screencasts [MEDIUM]
**What:** `type` action text (passwords/tokens) stored verbatim in
`~/.codec/pilot_traces/<id>/trace.json` (`pilot_agent.py`→`trace.py`), re-embedded into
compiled scripts; screencast frames capture credential screens. No redaction, no 0600.
**Fix:** redact text typed into password/sensitive fields before persist; chmod trace dirs
0600.

### P-14 — Robustness: junk-LLM aborts run, HITL pause never times out, unbounded `_runs`, MJPEG busy-spin [MEDIUM]
**What:** one malformed Qwen response aborts the whole run with no retry
(`pilot_agent.py:278`); `await _pause_event.wait()` has no timeout (`hitl.py`) → permanent
hang holding the browser; `_runs` grows without eviction; the MJPEG `while True` swallows all
exceptions (`pilot_runner.py:141`) → silent CPU spin on a persistently-failing screenshot.
**Fix:** retry LLM 1-2× feeding the parse error back; pause deadline; `_runs` eviction;
bounded failure counter on the stream.

### P-15 — Lower / informational [LOW]
- `_call_llm` reads `~/.codec/config.json:llm_base_url` (port 8083) directly — config drift
  vs the parent's 8090 + duplicates `codec_llm.acall`; if that field is attacker-influenced,
  the agent's brain is redirected.
- `trace.from_dict` uses `data["task"]`/`["run_id"]` (not `.get`) → 500 on a corrupt trace.
- `getXPath` assumes unique `id`s → wrong-element replay on duplicate ids.

---

## ⚠️ Cross-cutting finding affecting the PARENT repo (codec-repo)

**The PR-1A AST gate is allow-by-omission for network + file + deserialization.**
`codec_config._SKILL_DANGEROUS_MODULES = {os, subprocess, ctypes, shutil, importlib,
signal, pty, socket}` and `_SKILL_DANGEROUS_CALLS` (eval/exec/__import__/getattr/…) do **NOT**
include `urllib`/`http.client`/`httpx`/`requests`/`smtplib` (HTTP exfil), `pickle`/`marshal`
(deserialization RCE), or the `open` builtin (arbitrary file read/write). **Verified**
(`codec_config.py:713-726`). So a skill that passes the gate can still exfiltrate over HTTP
and read/write files. For **hand-written user skills** this is an accepted trade-off (they
legitimately use `urllib`/`open`). But for Pilot's **auto-compiled, attacker-influenceable**
skills it means "approved ≠ safe" — which is why P-3's fix (gate at approve) is necessary
but **not sufficient**; auto-compiled skills need a stricter allowlist than hand-written
ones (e.g. data-trace + fixed loader, no free-form source). Worth a parent-repo follow-up to
add the network/deserialization modules to the dangerous set for the *auto-generated* class.

---

## Test coverage

The 6 `test_phaseN.py` files use a hand-rolled `asyncio.run(main())` runner → **pytest
collects zero Pilot tests** (the parent CI never exercises them). `test_phase1.py` has no
assertions (print-only). No test covers auth, the compile→approve safety gate, SSRF/scheme
rejection, prompt-injection resistance, or any error path. `test_phase6.py` (HITL
pause/resume/inject) is the only substantive one. **Wire the suite into pytest and add the
safety tests alongside each P-fix.**

## How fixes ship (process note)

Pilot is a **separate repo** (`~/codec/pilot/`), not a submodule of `codec-repo` — the
engine reaches it only over the `:8094` HTTP contract via `skills/pilot.py`. So Pilot fixes
**cannot** go through the codec-repo PR workflow; they land in the pilot repo's own git. This
audit doc lives in `codec-repo/docs/audits/` for consistency with the other Phase-1 audits.
The two-repo boundary + port contract should be documented in a `pilot/README.md`.

## Fix wave (Pilot repo `~/codec/`) — status

> **✅ All findings remediated 2026-05-24** — PP-1…PP-12 (+ two follow-ups) committed to the
> Pilot repo's local `main` (no remote/CI there → review/push the commits). **67 pilot
> security tests pass** (`pilot/tests/test_phase7…18`); all native real-chromium suites
> (`test_phase2…6`) stay green → behavior-preserving. Each PP has a design doc under
> `pilot/docs/`.

1. **PP-1 ✅ (CRITICAL)** — P-1: `x-pilot-token` auth on every route (shared via
   `~/.codec/pilot_token`), loopback bind, CORS localhost-only. The parent half (send the
   token) shipped in codec-repo **#132**. *(Cloudflare-tunnel removal is still your manual
   step; until done, keep `pilot-runner` stopped or rely on the token gate.)*
2. **PP-2 ✅ (CRITICAL)** — P-2: `_safe()`/`_int()` escape all trace-derived source +
   `compile()`-validate; P-11: `slugify()` the review slug.
3. **PP-3 ✅ (CRITICAL/HIGH)** — P-4: `validate_navigation_url()` (http/https only; blocks
   file:/internal/loopback/link-local/metadata). *(Follow-up: `about:blank` allowed as an
   exact match — the canonical empty page has no host/network/file, so blocking it was
   over-gating; broad `about:` URLs stay blocked.)*
4. **PP-4 ✅ (HIGH)** — P-6: `wrap_untrusted()` fences page content into the agent/replay LLM
   + system-prompt warning. *(P-7's unauth HITL inject is closed by PP-1's auth.)*
5. **PP-5 ✅ (HIGH)** — P-8: randomized CDP debug port (was fixed 9223).
6. **PP-6 ✅** — P-13: redact secrets typed into password/secret fields from the persisted
   trace + compiled skill (`redact_typed_secret`). 4 tests.
7. **PP-7 ✅** — P-9: single-active-run guard on the shared browser (`_assert_run_slot_free`)
   + P-14 `_runs` eviction (`_evict_old_runs`). 4 tests.
8. **PP-8 ✅** — P-12: forensic audit trail (`pilot/audit.py` → `~/.codec/pilot_audit.log`;
   emits on skill approve/reject, run start, navigate). 3 tests.
9. **PP-9 ✅** — P-15: corrupt/partial trace tolerated on load (`_from_dict` → `.get`). 2 tests.
10. **PP-10 ✅** — P-7 + P-10 (default-deny core): `classify_destructive()` + `guard_action()`
    block irreversible/financial clicks (pay/place-order/delete/transfer/…) in the **agent
    loop** and **replay** unless `PILOT_ALLOW_DESTRUCTIVE=1`. Targeted verb list (not generic
    submit/search) to avoid over-blocking. 5 tests.
11. **PP-11 ✅ (CRITICAL)** — P-3: AST safety gate at skill **approve**. `pilot/safety.py`
    vendors a minimal `is_dangerous_skill_code` (Pilot can't import the parent `codec_config`);
    `approve_pending` refuses + `audit("skill_blocked")` on dangerous imports/calls or
    unparseable code, before the file reaches `~/.codec/skills/`. Defense-in-depth atop PP-2 +
    the parent registry's load-time gate. 6 tests.
12. **PP-12 ✅** — P-14 async robustness: HITL pause gate bounded by `asyncio.wait_for`
    (`HITL_PAUSE_TIMEOUT_S`, default 600s → `paused_timeout`, frees the browser + run slot);
    `/screenshot/stream` MJPEG extracted to `_mjpeg_frames` with a consecutive-failure bound
    (`MJPEG_MAX_CONSECUTIVE_FAILURES`, default 20) so a dead browser closes the stream instead
    of spinning forever. 8 tests.

**67 pilot security tests pass** (`test_phase7…18`); all native real-chromium suites
(`test_phase2…6`) stay green → every fix is behavior-preserving. *(Two follow-ups landed
during verification: `about:blank` allowed in the PP-3 nav guard; the legacy `test_phase3`
endpoint test now sends `x-pilot-token` per PP-1.)*

**Genuinely remaining (low-value / cross-repo — documented, not exploitable):**
- **P-15 `getXPath` duplicate-id** edge — a snapshot-accuracy nicety in injected browser-JS
  (duplicate `id=` attributes yield a non-unique XPath). Low value; the selector-rescue path
  already recovers from stale selectors at replay time.
- **Legacy async suite under pytest** — `test_phase2…6` pass natively (their `__main__`
  harness drives a real-chromium `pilot` fixture) but ERROR under bare `pytest` (no async
  fixture wiring). The new `test_phase7…18` security suite is fully pytest-native; wiring the
  legacy phases is test-infra polish, not a security gap.
- **Parent repo:** cross-cutting AST-gate hardening for *auto-generated* skills (the
  `is_dangerous_skill_code` allow-by-omission for `urllib`/`httpx`/`open`/`pickle`) — a
  codec-repo finding tracked separately, not a Pilot fix.

**Verdict:** Pilot is the highest-risk component in CODEC and it architecturally opted out of
the entire Phase-1 hardening (separate repo, HTTP-only coupling). Internal code quality is
decent, but the trust boundaries (HTTP edge, skill-approval edge, CDP edge) are open. The
emergency stop closed the live exposure; the fix wave above re-aligns Pilot with the engine's
security posture without a rewrite.
