# SECURITY-REMEDIATION-DESIGN

> Remediation plan for the top-10 findings from the 2026-05-28 full code audit.
> This is the CLAUDE.md §11 design-first gate: **no working code has been edited.**
> Implementation begins only after the operator approves the order below
> (and explicitly signs off on the items flagged DECISION-REQUIRED).

**Author:** audit follow-up · **Date:** 2026-05-28 · **Status:** AWAITING APPROVAL
**Source audit:** 9 specialist reviewers · 1,910 tests baseline (1,818 pass / 9 fail / 83 skip / 1 collection error)

---

## 0. Verification baseline (ground-truthed before writing this doc)

Every line reference below was re-read against current `HEAD` on 2026-05-28, not trusted from the audit summary. Corrections found during verification:

| Claim in audit | Verified reality | Effect on plan |
|---|---|---|
| OAuth TTL 365d is a finding | `codec_oauth_provider.py:48-57` — **deliberately set to 365d TODAY** with documented threat-model rationale | **Dropped.** Don't-touch zone + maintainer decision. Not touched. |
| C6 `_save()` reads state pre-lock | `_serialize()` is called **inside** `self._lock` (line 144-145) | Narrowed: real gap is **no fsync** on fallback path (164-167) |
| C5 SQLite has no concurrency guard | True (`check_same_thread=False`, no lock, line 44) — but WAL + `busy_timeout=5000` are set (45-46) | Real, but lower blast radius than stated |
| C1 TOTP confirm trusts client secret | Confirmed verbatim (`routes/auth.py:194-218`) — `secret` taken from request body, verified against itself, written to config | Unchanged — genuine takeover |
| C3 python_exec in chat allowlist | Confirmed (`codec_dashboard.py:2219`) + sandbox `(allow file-read*)` (`codec_sandbox.py:28`) | Unchanged |
| C2 bridge fail-open | Confirmed (`codec_telegram.py:70` default `[]`, `:509` `if allowed and …`) | Unchanged |

---

## 1. Scope

**In scope:** the 9 Critical + selected High findings, sequenced as the audit's top-10 fix order.

**Explicitly OUT of scope (don't-touch zones — require separate, explicit operator sign-off, NOT bundled here):**
- `ACCESS_TOKEN_TTL` / `REFRESH_TOKEN_TTL` (`codec_oauth_provider.py:56-57`) — maintainer-set 365d, documented today.
- `codec_audit.py` audit envelope schema (schema:1).
- `_HTTP_BLOCKED` membership in `codec_config.py` (we ADD a CHAT-path restriction in Fix #3; we do not alter `_HTTP_BLOCKED` itself).
- PM2 process names (Fix #6 changes the *interpreter path*, never a process `name:`).
- `codec_identity.py` operating principles.

**Standing constraints honored throughout:** no synthetic code; no data deletion; verify wiring before and after each edit; one concern per commit; tests green after each file change; never auto-deploy skill proposals.

---

## 2. Phased remediation plan

### PHASE A — Critical security blockers (the release gate)

These three are exploitable today and gate the next release.

---

#### Fix #1 — Authenticated-TOTP enrollment + OAuth durability + fastmcp install

**Closes:** C1 (TOTP takeover), C6 (OAuth fsync), C7 (fastmcp missing/CVE — *test-env + install only; TTL untouched*).

**1a. C1 — `routes/auth.py:194-218` (`/api/auth/totp/confirm`)**

*Problem:* the endpoint accepts `secret` from the request body, calls `pyotp.TOTP(secret).verify(code)` (which the attacker satisfies because they generated both), then writes that attacker-chosen secret to `config.json:totp_secret`. If the endpoint is reachable without an established auth session (it is — `/api/auth/*` must be pre-auth by design), an unauthenticated attacker enrolls their own 2FA secret → account takeover.

*Change (two independent layers):*
1. **Require an established session to enroll.** `confirm` must reject unless the caller presents a valid `_auth_sessions` token (i.e. they already passed Touch ID / PIN). Enrolling 2FA is a logged-in action, not a pre-auth one.
2. **Server-owns-the-secret.** `/api/auth/totp/setup` already generates the secret server-side (line 184). Stash it in a short-TTL, session-keyed pending map (`_pending_totp_secrets[token] = {secret, created}`); `confirm` verifies the code against the **pending server-stored** secret, never a body-supplied one. Remove `secret` from the confirm request contract.

*Migration/compat:* PWA setup flow currently posts `{code, secret}`; it will post `{code}` + auth token. One-line frontend change in `codec_dashboard.html` TOTP panel (tracked as a sub-task). Existing enrolled users unaffected (their `totp_secret` already in config; `/verify` path at 221-239 unchanged).

*Test:* new `tests/test_auth_totp.py` — (a) unauthenticated confirm → 401/403; (b) authenticated confirm with body-supplied foreign secret → ignored, server secret used; (c) happy-path enroll → verify round-trip.

*Rollback:* revert `routes/auth.py` + the 1-line HTML change. No persisted state migration, so rollback is clean.

*Risk/flags:* touches the auth path — **DECISION-REQUIRED** on layer choice (see §3, Decision A).

**1b. C6 — `codec_oauth_provider.py:164-167` (`_save` fallback)**

*Problem:* the Keychain-unavailable fallback writes plaintext via `tmp.write_text(blob); os.replace(...)` with **no fsync** → crash-window data loss/corruption of `oauth_state.json`.

*Change:* replace the hand-rolled tmp+replace with `codec_jsonstore.atomic_write_json(self._state_path, json.loads(blob))` (already does tmp **+ fsync +** replace + 0600). Keep the `with self._lock:` and the Keychain-first branch exactly as-is.

*Migration/compat:* none — same file, same format, same 0600 perms. `oauth_state.json` is a don't-touch zone *for clearing/TTL*, but improving the write durability of the fallback path is non-breaking (no schema change, no TTL change). Will surface to operator before commit per don't-touch protocol.

*Test:* `tests/test_oauth_provider.py` (currently collection-erroring — unblocked by 1c) — add a fallback-path durability test (monkeypatch Keychain unavailable, assert atomic write + 0600).

*Rollback:* one-function revert.

**1c. C7 — fastmcp install in test/CI env (NOT a TTL or runtime-behavior change)**

*Problem:* `fastmcp` not importable in the python3.11 test env → `tests/test_oauth_provider.py` collection error + 6 downstream test files can't run + 9 of 9 suite failures trace here.

*Change:* (1) **Verify** the exact advisory + safe version via `pip-audit` / registry before pinning — do NOT assert a CVE id/version on faith. (2) Add the verified safe `fastmcp` pin to `requirements.txt` + the CI workflow's install step. (3) Re-run the suite; expect the 9 failures + collection error to clear.

*Migration/compat:* dependency addition to an already-declared import (`codec_oauth_provider.py:35` already imports it). No runtime behavior change.

*Test:* `pytest --collect-only` clean (no collection error); full suite failures → 0 attributable to fastmcp.

*Rollback:* unpin / remove from requirements.

---

#### Fix #2 — Fail-closed message bridges + bridge skill allowlist

**Closes:** C2.

*Problem:* `codec_telegram.py:70` defaults `allowed_chat_ids` to `[]`, and `:509` is `if allowed and chat_id not in allowed:` → empty list = **allow all**. Anyone who learns the bot token can drive CODEC. `codec_bridges.py:27` `_SKIP_SKILLS` doesn't exclude `terminal`/`python_exec`/`file_write`/`pilot`, so an inbound message can reach high-power skills. Same shape in `codec_imessage.py`.

*Change:*
1. **Fail-closed default.** When `allowed_chat_ids`/allowed-handles is empty, **deny all** inbound, log a one-time `bridge_no_allowlist` warning telling the operator how to allow their own chat id. (`codec_telegram.py` get-config + the `:509` guard; mirror in `codec_imessage.py`.)
2. **`BRIDGE_SAFE_SKILLS` allowlist.** Inbound bridge messages may only dispatch skills on an explicit safe list (read/info skills); `terminal`, `python_exec`, `file_write`, `pilot`, `process_manager`, `pm2_control`, `ax_control` are never reachable from a bridge. Enforced in `codec_bridges.try_skill`.

*Migration/compat:* **behavior change** — a user currently relying on empty-list-allows-all will stop receiving bridge responses until they add their chat id. This is the correct secure default; document in release notes + the warning log line. **DECISION-REQUIRED** (§3, Decision B) on whether to ship fail-closed immediately or behind a one-release deprecation warning.

*Test:* `tests/test_bridges.py` — empty allowlist denies; populated allowlist permits only listed ids; `terminal`/`python_exec` rejected from bridge path even when chat id is allowed.

*Rollback:* revert the guard default + remove `BRIDGE_SAFE_SKILLS` gate. Inbound stays PWA-only regardless (no new inbound channel added — consistent with CLAUDE.md).

---

#### Fix #3 — Remove `python_exec` from chat allowlist + tighten sandbox file-read

**Closes:** C3.

*Problem:* `codec_dashboard.py:2219` includes `python_exec` in `CHAT_SKILL_ALLOWLIST` → a prompt-injection payload in chat can fire it. The sandbox profile `codec_sandbox.py:28` `(allow file-read*)` lets sandboxed code read `~/.ssh`, `~/.codec/secrets*`, `oauth_state.json`; the skill's return value flows back into the chat/LLM transcript — a read-then-return exfil path even though network egress is denied.

*Change:*
1. **Remove `python_exec` from `CHAT_SKILL_ALLOWLIST`** (line 2219). It remains available as a skill but is no longer auto-firable from the pre-LLM chat hijack / post-LLM tag path. (`SKILL_MCP_EXPOSE=False` already keeps it off MCP.)
2. **Tighten the sandbox read scope.** Replace blanket `(allow file-read*)` with an allow-list of the paths Python genuinely needs (interpreter prefix, stdlib, site-packages, `/private/tmp`, skill_output) and an explicit `(deny file-read* (subpath "<home>/.ssh"))` / `~/.codec/secrets*` / `oauth_state.json` / Keychain dirs. Default-deny is safer than default-allow here.

*Migration/compat:* removing `python_exec` from the chat allowlist is the intended security posture; legitimate local Python execution still works via explicit invocation. Sandbox tightening must be validated against the existing `python_exec` smoke tests so we don't break legitimate imports. **DECISION-REQUIRED** (§3, Decision C) — full removal vs. keep-behind-strict-consent.

*Test:* `tests/test_python_exec.py` — injection-style chat message no longer triggers `python_exec`; sandboxed read of `~/.ssh/id_rsa` is denied; a benign `import json; print(...)` still runs.

*Rollback:* re-add the set member + revert the profile.

---

### PHASE B — Robustness & correctness

---

#### Fix #4 — Extract `codec_concurrency.run_with_timeout`; kill the hanging ThreadPoolExecutor pattern

**Closes:** C4.

*Problem:* `codec_mcp.py:222-224` and `codec_observer.py:276-278` use `with ThreadPoolExecutor() as ex: fut.result(timeout=…)`. On timeout, `__exit__` calls `shutdown(wait=True)` and **blocks on the runaway task** — the timeout is defeated; MCP/observer can hang.

*Change:* extract the proven pattern from `codec_hooks._run_hook_with_timeout` into a new, additive `codec_concurrency.run_with_timeout(fn, timeout, ...)` (daemon thread + `Event`, no blocking `__exit__`). Migrate the two call sites. New module = zero risk; the two migrations are the only working-code touch.

*Test:* `tests/test_concurrency.py` — a function exceeding the timeout returns control promptly and does NOT block on shutdown; result/exception propagation correct.

*Rollback:* revert the two call sites; leave the (unused) helper or delete it.

---

#### Fix #5 — SQLite + JSON state locking pass

**Closes:** C5, plus High/Med H14 + M6.

*Problem:* shared `sqlite3` connection across threads with no lock (`codec_memory.py:44`); `routes/agents.py` `grant_permission` and `codec_ask_user.py` `_write_question_notification` do read-modify-write on JSON state without `file_lock`.

*Change:*
1. Wrap `CodecMemory` connection use in a `threading.RLock` (cursor ops serialized; WAL + busy_timeout stay as the cross-process layer).
2. Route the two JSON read-modify-write sites through `codec_jsonstore.file_lock(...)` + `atomic_write_json(...)`.

*Migration/compat:* none — internal locking only. Verify no deadlock vs. existing `_auth_lock`/`self._lock` usages.

*Test:* concurrency stress test on `CodecMemory.save` from N threads (no corruption); concurrent `grant_permission` writes don't clobber.

*Rollback:* per-site revert.

---

#### Fix #6 — Dependency upgrade pass + PM2-to-venv pin

**Closes:** C7 (transitive) + dependency-auditor Highs.

*Problem:* runtime Python 3.13 drifted below `requirements.txt` minimums; several packages have advisories; PM2 may launch system python instead of the venv.

*Change:* (1) `pip-audit` → upgrade the flagged set (verify each advisory first; **no version asserted on faith**). (2) Pin PM2 `interpreter:` in `ecosystem.config.js` to the venv python — **process `name:` fields untouched** (don't-touch zone). (3) Re-run full suite after upgrades.

*Migration/compat:* dependency bumps can ripple — do this on a branch, full suite must stay green. **DECISION-REQUIRED** (§3, Decision D) — aggressive (latest) vs. minimal (only-advisory-affected) upgrade.

*Rollback:* `requirements.txt` revert + `pip install -r`.

---

### PHASE C — Structural & coverage

---

#### Fix #7 — SSRF + skill-overwrite hardening

**Closes:** H1, H2, H6.

*Change:* add an SSRF guard (block private/link-local/metadata IPs, enforce http(s), cap redirects/size) shared by `web_fetch` + `clipboard_url_fetch`; ensure `skill_approve`/skill-write can't overwrite a hash-pinned built-in. Additive guard module + call-site wiring.

*Test:* SSRF guard rejects `169.254.169.254`, `127.0.0.1`, `file://`, `10.x`; allows public hosts. Skill-overwrite of a manifest-pinned name refused.

*Rollback:* per-site revert.

---

#### Fix #8 — `routes/chat.py` extraction (first slice of the god-module)

**Closes:** start of C9 (`codec_dashboard.py` 3,859 LOC; `chat_completion` CC 48).

*Change:* extract the chat handler into `routes/chat.py`, dropping `chat_completion` complexity toward ~15. Pure move + seam, no behavior change. This is a >1-module structural change → its own follow-on design note before implementation.

*Test:* existing chat/stream tests must pass unchanged (behavior-preserving refactor).

*Rollback:* revert the extraction commit.

---

#### Fix #9 — Promote `codec_jsonstore` to mandatory state registry

**Closes:** C8 (~25 ad-hoc `~/.codec/*.json` writers).

*Change:* inventory every `~/.codec/*.json` writer; migrate ad-hoc writers to `atomic_write_json` + `file_lock`; add `docs/STATE-FILES.md` registry. Large surface → its own design note + phased migration (don't-touch state files migrated last, with operator sign-off each).

*Test:* per-migrated-file round-trip + concurrent-write test.

*Rollback:* per-file revert.

---

#### Fix #10 — Test coverage + flake fixes

**Closes:** test-coverage-reviewer gaps.

*Change:* TOTP HTTP tests (from Fix #1), OAuth scope-escalation tests, `permission_gate` mutation tests, replace `time.sleep` with `threading.Event` in flaky tests, add a CI grep-guard that fails if a new inline `chat/completions` POST is added outside `codec_llm` (protects the A-12 invariant).

*Rollback:* tests are additive; safe to drop.

---

## 3. Open decisions requiring explicit operator sign-off

I will NOT start these until you pick. Each is framed A/B/C with my recommendation first.

**Decision A — TOTP confirm fix (Fix #1a):**
- **A1 (Recommended):** both layers — require auth session AND server-owns-secret. Closes the takeover completely; ~1 frontend line.
- A2: require-auth-session only (smaller change, but still trusts body secret for an already-authed user — acceptable but weaker).
- A3: server-owns-secret only (defends the secret, but leaves enroll callable pre-auth — weaker).

**Decision B — Bridge fail-closed rollout (Fix #2):**
- **B1 (Recommended):** ship fail-closed now + loud warning log + release note. Correct secure default.
- B2: one-release deprecation — warn on empty allowlist but still allow, flip to deny next release.

**Decision C — python_exec chat exposure (Fix #3):**
- **C1 (Recommended):** remove from `CHAT_SKILL_ALLOWLIST` entirely + tighten sandbox.
- C2: keep in allowlist but route every fire through Step-3 strict-consent + tighten sandbox.

**Decision D — Dependency upgrade aggressiveness (Fix #6):**
- **D1 (Recommended):** minimal — only advisory-affected packages, verified one-by-one.
- D2: aggressive — bring everything to latest compatible, full-suite gated.

**Decision E — Execution granularity:**
- **E1 (Recommended):** I implement Phase A (Fixes 1-3) now as separate commits, each with tests green, surfacing the don't-touch-adjacent `oauth_state.json` write-durability change before its commit. Pause for review after Phase A.
- E2: I implement one specific fix you name, then stop.
- E3: this doc is enough for now — you'll schedule implementation later.

---

## 4. Zero-risk first action (no working-code edit)

Independent of the decisions above, the one fully-reversible, behavior-neutral action that unblocks the most downstream work is **Fix #1c**: verify the fastmcp advisory + safe version and install it in the test env so `pytest --collect-only` is clean and the 9 failing tests can actually run. That gives a true green baseline to measure every subsequent fix against. I'll do this first unless you say otherwise.

---

## 5. What this plan deliberately does NOT do

- Does not change OAuth TTLs (maintainer-set today).
- Does not alter the audit envelope schema, `_HTTP_BLOCKED` membership, PM2 process names, or `codec_identity.py`.
- Does not add any inbound channel (bridges stay outbound/response-only).
- Does not auto-deploy anything from `~/.codec/skill_proposals/`.
- Does not touch `memory.db` schema (Fix #5 is a runtime lock, not a migration).
