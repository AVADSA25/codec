# FIX8 — `routes/chat.py` extraction (first slice of the dashboard god-module)

> Follow-on design note required by `docs/SECURITY-REMEDIATION-DESIGN.md` Fix #8
> and CLAUDE.md §11 (>1-module structural change). **No code is written until
> this note is approved.** Closes the start of audit finding C9.

**Author:** audit follow-up · **Status:** AWAITING APPROVAL

---

## 1. What & why

`codec_dashboard.py` is **3,861 LOC** — the audit's C9 "god-module". The single
most complex unit in it is the chat handler `chat_completion`
(`codec_dashboard.py:2650-3005`, ~356 lines, cyclomatic complexity ~48 per the
audit). High complexity in the request path that runs every chat turn is a
maintenance + correctness risk (the streaming/skill-tag/step-budget interplay
is hard to reason about and easy to regress).

**Goal:** a *behavior-preserving* extraction of the chat-handler cluster into a
new `routes/chat.py` `APIRouter`, mounted via `app.include_router(...)` exactly
like the existing `routes/agents.py`, `routes/skills.py`, `routes/auth.py`.
Pure move + seam. **Zero behavior change.** Complexity of the handler itself is
reduced by splitting its phases into named helpers as part of the move.

This is explicitly the *first* slice — it does NOT attempt to dismantle the
whole god-module. One coherent, low-risk extraction with the existing chat/
stream tests as the safety net.

## 2. The cluster to move (verified against current HEAD)

| Symbol | Lines | Role |
|---|---|---|
| `CHAT_SKILL_ALLOWLIST` | 2207 | set gating pre-LLM hijack + post-LLM tag |
| `_StepBudget` | 2369 | per-turn step cap (Phase 1 Step 3) |
| `_try_skill` / `_try_skill_by_name` | 2442 / 2459 | pre-LLM skill hijack |
| `_classify_chat_message` | 2570 | Qwen project-escalation classifier (Step 10) |
| `_should_escalate_to_project` | 2616 | 2-signal escalation gate (Step 10) |
| `chat_completion` | 2650-3005 | the `@app.post("/api/chat")` handler |
| `_stream_gen` (nested) | 2889 | SSE streaming generator inside the handler |

Contiguous span ≈ **lines 2207-3005 (~800 LOC)**.

## 3. Collaborators (the seam — what crosses the new module boundary)

`chat_completion` references, and the extraction must thread (import or pass):

- **Cross-module (already importable, no change):** `codec_chat_stream`
  (`SkillTagBuffer`, `SKILL_TAG_RE` — already imported at dashboard:39),
  `codec_llm` (`stream(keepalive=True)`, `call(raise_on_error=True)`),
  `codec_observer` (system-prompt injection), `codec_memory` (context),
  `codec_dispatch.run_skill`, `codec_slash_commands.parse_slash`,
  `codec_identity` (system prompt), `codec_audit`.
- **Dashboard-local state that must be SHARED, not duplicated:**
  `_autoescalate_silence_set` + `_AUTOESCALATE_SILENCE_LOCK` (Step 10 in-memory
  per-session silence — CLAUDE.md "don't-touch from outside"),
  `AGENT_AUTO_ESCALATE_ENABLED`, `ESCALATE_CHECKPOINTS_THRESHOLD`, the memory
  singleton, any LLM config getters.

**Decision point (see §7, Decision A):** how to share that module-level state
between `codec_dashboard.py` and the new `routes/chat.py` without creating a
circular import.

## 4. Proposed approach

1. **Create `routes/chat.py`** with `router = APIRouter()` (mirrors the 4
   existing route modules).
2. **Move the cluster** (§2) verbatim into it. Convert `@app.post("/api/chat")`
   → `@router.post("/api/chat")`.
3. **Resolve shared state via a small `routes/_chat_state.py` (or extend
   `routes/_shared.py`)** that owns `_autoescalate_silence_set`,
   `_AUTOESCALATE_SILENCE_LOCK`, and the escalation flags/consts. Both
   `codec_dashboard` (if it still references them) and `routes/chat` import from
   there. This breaks the would-be circular import (`dashboard → chat → dashboard`).
4. **Reduce `chat_completion` CC** by extracting its phases into named helpers
   *during* the move (each is a straight cut of an existing block, no logic
   change): `_resolve_slash_or_skill(...)`, `_build_chat_messages(...)`
   (memory + observer injection), `_stream_chat_response(...)`
   (the `_stream_gen` body), `_finalize_nonstream(...)`. Target handler CC ≈ 15.
5. **Mount** in `codec_dashboard.py`: `from routes.chat import router as chat_router; app.include_router(chat_router)` — same pattern already used for agents/skills/auth.
6. Delete the now-moved definitions from `codec_dashboard.py`.

## 5. Migration / compat

- **No API change.** Path stays `/api/chat`; request/response/SSE shape
  identical. The PWA is untouched.
- **No new dependency.** Pure restructure.
- Auth middleware, step budget, observer injection, skill hijack, post-LLM tag
  resolution, auto-escalation all behave identically — they're moved, not
  altered.
- Import-order: `app.include_router` must run after `app` is created; place the
  mount next to the existing `include_router` calls.

## 6. Test plan

- The existing chat + streaming tests are the behavior-preserving net:
  `tests/test_chat_stream*.py`, `tests/test_skill_tag*.py`,
  `tests/test_dashboard*.py`, `tests/test_step_budget*.py`,
  `tests/test_*escalat*` (exact set enumerated at implementation start). **All
  must pass unchanged** — no test edits beyond import paths.
- Add `tests/test_routes_chat_smoke.py`: assert `routes.chat.router` exposes
  `/api/chat` and that `chat_completion` is importable from the new module.
- Full suite green before/after (diff = same pass count).

## 7. Open decisions (need sign-off)

**Decision A — shared module-level state:**
- **A1 (Recommended):** move `_autoescalate_silence_set` + lock + escalation
  consts into `routes/_shared.py` (or a new `routes/_chat_state.py`); both
  modules import them. Cleanest break of the circular import.
- A2: keep them in `codec_dashboard.py` and have `routes/chat.py` import the
  dashboard module lazily inside the handler. Smaller diff, but re-introduces a
  dashboard↔chat coupling the extraction was meant to reduce.

**Decision B — scope of this slice:**
- **B1 (Recommended):** chat cluster only (§2). Leave the other ~3,000 LOC for
  later slices. Smallest reviewable unit.
- B2: also pull the schedules/notifications routes in the same PR. Bigger blast
  radius; rejected for a "first slice".

## 8. Rollback

Single revert of the extraction commit restores the inlined handler. No
persisted state, no schema, no API change — rollback is clean.

## 9. Risk

Medium. The handler is hot (every chat turn) and the streaming path is subtle.
Mitigation: behavior-preserving move only, existing stream/tag/budget tests as
the gate, helper extraction is mechanical block-cutting. Recommend implementing
on the existing `security-remediation` branch as its own commit so it can be
reverted independently of the landed fixes.
