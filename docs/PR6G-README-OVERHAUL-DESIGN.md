# PR-6G — README investor overhaul (design note)

> Wave 6 (Audit F / Investor Readiness). Docs-only. Closes **F-3, F-9, F-10,
> F-14, F-17, F-18**. Reference: `docs/audits/PHASE-1-INVESTOR-READINESS.md`.

## What

Rework the public README so a stranger from Hacker News understands what CODEC
does in 30 seconds, the moat lands in the first screen, and every metric claim
is truthful and internally consistent. Three files change: `README.md`,
`CONTRIBUTING.md`, `AGENTS.md` (CLAUDE.md is a symlink → AGENTS.md, so one edit
covers both).

## Why (findings closed)

| Finding | Sev | Fix in this PR |
|---|---|---|
| **F-3** | CRITICAL | Stop overstating. Replace static `tests-940+` with a **live GitHub Actions CI badge** + a conservative round-down count badge. Reconcile counts. |
| **F-9** | HIGH | Lead with a one-sentence value prop above the fold (before "What This Is"). |
| **F-10** | HIGH | Add a **"Why CODEC, not the alternatives"** section with the 3 moat bullets (vs Open Interpreter/Aider, vs Cursor/Claude desktop, vs CrewAI/LangChain). |
| **F-14** | MEDIUM | Add a top-level **"## Architecture"** section: condensed topology + link to `docs/ARCHITECTURE.md`. |
| **F-17** | LOW | One test number everywhere (README badge + body + CONTRIBUTING + AGENTS). |
| **F-18** | LOW | State the **bidirectional MCP** story in the MCP section (CODEC is client AND server; two CODECs peer = agent-to-agent). |

## Count reconciliation (the F-3 / F-17 core)

Measured in this repo on 2026-05-24:

| Metric | Old claim(s) | Measured | New claim |
|---|---|---|---|
| Tests | `940+` (badge + body), `600+` (AGENTS), `168+` (CONTRIBUTING) | `def test_` = **1,386** funcs / 99 files; `pytest --collect-only` = **1,685** collected | **`1,300+`** (rounds *down* from the function-count floor → unimpeachable by either measure) + live CI badge |
| Skills | `75` | `76` modules with `SKILL_NAME` (excl. `_template`) | **`76`** |
| Lines | `58K+` | **67,404** Python LOC | **`67K+`** |

**Why 1,300+ and not 1,600+:** F-3 is CRITICAL specifically about *overstating*.
The conservative round-down of the lowest defensible interpretation (`def test_`
= 1,386 → 1,300+) cannot be called an overstatement by anyone running either
`grep -rc 'def test_' tests/` (1,386) **or** `pytest --collect-only` (1,685).
The exact numbers go in the body for full transparency.

## Out of scope (flagged to HANDOFF-MICKAEL.md, need Mickael)

- **F-8** demo GIF in first viewport — needs a screen recording.
- **F-11** pricing/paid-tier shape — only a soft "waitlist" line added; tier + price band need Mickael's decision.
- **F-12** Discord / GitHub Discussions — needs Mickael to create the surfaces.
- **F-18 (Lucy)** — the README states the generic bidirectional-MCP/agent-to-agent story; whether "Lucy" is a separate brand to name is a Mickael call.

## Test plan

`tests/test_readme_investor.py` (regression guard):
- No stale `940+` in README; no `168+` in CONTRIBUTING; no `600+ tests` in AGENTS.
- One reconciled test number (`1,300+`) reachable in README + CONTRIBUTING + AGENTS.
- Skills claim is `76`, not `75 built-in skills` / `skills-75`.
- Value-prop sentence near the top; `## Why CODEC` section; `## Architecture` section linking `docs/ARCHITECTURE.md`; MCP section names both client AND server.

## Rollback

Pure docs. `git revert` the single commit; no runtime, schema, or daemon impact.
