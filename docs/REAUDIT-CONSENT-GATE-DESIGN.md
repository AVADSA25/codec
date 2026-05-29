# RE-AUDIT — strict-consent gate on the chat / MCP skill paths

> Design note for the re-audit's cross-cutting MEDIUM (red-team CHAIN-001/002/006).
> CLAUDE.md §11 gate: **no code written until this is approved** — it's a
> >1-module change that alters chat/MCP UX. Design-only.

**Author:** re-audit follow-up · **Status:** AWAITING APPROVAL

---

## 1. Problem

The Step-3 strict-consent gate (literal-verb confirmation for destructive ops,
`docs/PHASE1-STEP3-DESIGN.md §1.7`) is wired **only into `codec_agent_runner`**.
The three *other* execution paths that can reach the same high-power skills go
through chokepoints with **no consent gate**:

| Path | Chokepoint | Current guard |
|---|---|---|
| Chat pre-LLM hijack | `codec_dashboard._try_skill` → `codec_dispatch.run_skill` (codec_dashboard.py:2452-2455) | allowlist + `is_dangerous` (terminal only) |
| Chat post-LLM `[SKILL:]` tag | `_try_skill_by_name` → `codec_dispatch.run_skill` (2473-2475) | allowlist + `is_dangerous` |
| MCP tool call | `codec_mcp.tool_fn` invoke closure → `registry.load().run()` (codec_mcp.py:218-221) | `_HTTP_BLOCKED` only |

So a **prompt-injected** chat message / MCP tool call (from web_fetch'd content,
an email summary, a malicious doc claude.ai reads) can fire `terminal`,
`file_ops`, `pilot`, `imessage_send`, etc. with only `is_dangerous` (which
CLAUDE.md explicitly calls "a heuristic / typo-catcher, NOT a complete security
boundary") or a path blocklist in the way. (`skill_forge`/`python_exec` are
already off the chat allowlist; this is about the rest.)

## 2. Chokepoints to gate

- **`codec_dispatch.run_skill(skill, task, app)`** — covers chat (both paths),
  voice, wake-word, session, triggers. ONE function.
- **`codec_mcp.tool_fn`** — covers MCP stdio + HTTP. Separate.

A shared `codec_consent.gate(tool_name, task, *, transport) -> ConsentDecision`
called at the top of both, so the policy lives in one module.

## 3. The hard part — consent UX differs by transport

`codec_ask_user.ask(destructive=True, …)` **blocks the worker thread** on a
`threading.Event` until the user answers via PWA/voice. That's fine for the
agent-runner (background) and voice (announce-and-listen), but:

- **Chat** is a *synchronous HTTP request*. Blocking it for consent hangs the
  chat turn for up to the timeout — poor UX, and the user may not be looking at
  the PWA consent panel.
- **MCP** — claude.ai (the caller) can't perform operator-grade (Touch ID/PIN)
  consent, and blocking the tool call stalls the connector.

So the gate must be **per-transport**, not one-size.

## 4. Proposed policy (per transport)

- **MCP (`tool_fn`)** → **hard-refuse** destructive skills (return a clear
  "not permitted over MCP" string), NOT a prompt. Most are already in
  `_HTTP_BLOCKED`; this extends the *principle* to the full destructive set
  (file_ops-write, file_write, imessage_send, pilot, ax_control). claude.ai
  brings its own context and cannot consent at the operator tier.
- **Chat** → consent required before a destructive skill fires. Two UX options
  (Decision A).
- **Voice** → reuse `ask_user` announce-and-listen (already works on the agent
  path); the voice WS is now authenticated (re-audit N1).

## 5. Defining "destructive"

Today `codec_ask_user._is_destructive_tool` = membership in `_HTTP_BLOCKED`
(`python_exec, terminal, process_manager, pm2_control, ax_control`). That misses
`file_ops`(write/delete), `file_write`, `imessage_send`, `pilot`, `skill_forge`.
→ **Decision C**: a per-skill `SKILL_DESTRUCTIVE = True` module flag
(AST-extracted by `codec_skill_registry`, like `SKILL_MCP_EXPOSE`) — extensible,
self-documenting — vs a central `DESTRUCTIVE_SKILLS` set in `codec_config`.

## 6. Open decisions (need sign-off)

**Decision A — chat consent UX:**
- **A1 (Recommended):** *return a `consent_required` response* (don't block) —
  the chat UI renders a confirm affordance; on confirm the client re-dispatches
  with a short-lived consent token. Clean for synchronous chat; needs a small
  `codec_dashboard.html` + chat-handler change.
- A2: *block-and-prompt* — call `ask_user.ask` from the chat handler (PWA
  AskUserQuestion panel already polls every 8s), worker thread waits with a
  short timeout. Smaller code change; worse UX (hangs the turn).

**Decision B — MCP destructive policy:**
- **B1 (Recommended):** hard-refuse the full destructive set over MCP (extend
  the `_HTTP_BLOCKED` principle to a `DESTRUCTIVE`-aware refusal in `tool_fn`).
- B2: out-of-band PWA consent (the MCP call returns "pending", the operator
  approves in the PWA, claude.ai retries) — much more machinery; likely overkill.

**Decision C — destructive classification:** per-skill `SKILL_DESTRUCTIVE` flag
(Recommended) vs central set.

**Decision D — `is_dangerous` interplay:** keep `is_dangerous` as the
command-content heuristic for `terminal` AND layer the skill-level consent gate
on top (defense in depth) — Recommended. Don't remove `is_dangerous`.

**Decision E — scope:** gate `run_skill` (chat) + `tool_fn` (MCP) now; leave
voice on the existing `ask_user` path. Or include a voice allowlist too.

## 7. Test plan

- destructive skill via chat post-LLM tag → consent required (A1: returns
  `consent_required`, skill NOT run until token; A2: `ask_user` invoked).
- destructive skill via MCP `tool_fn` → refused (B1), audit `mcp_destructive_blocked`.
- non-destructive skill (weather, calculator) → unaffected on both paths.
- consent token round-trip re-dispatches and runs once (A1).
- kill switch env var disables the gate (parity with other Step-3 switches).
- `is_dangerous` still fires for terminal (Decision D layering).

## 8. Rollback / risk

Per-chokepoint revert + a `CONSENT_GATE_ENABLED` kill switch. Risk: UX
regression (over-prompting) — mitigated by the per-skill `SKILL_DESTRUCTIVE`
flag (only flagged skills prompt) and the per-transport policy (MCP refuses,
chat prompts, voice announces). Behavior for non-destructive skills is unchanged.

## 9. What this does NOT do

- Does not change the agent-runner consent (already correct).
- Does not alter `_HTTP_BLOCKED` membership (the gate is additive).
- Does not weaken `is_dangerous` or the skill load-time AST gate.
