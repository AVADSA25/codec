# Vibe eval: one scored run on a cloud entry, and its cost estimate (P-073, card E-13)

Status: approved by the owner on the decision board, card E-13, 12 Nov 2026: "Yes: one run on the configured
non-Anthropic cloud entry; I tell you the cost estimate first." This change only makes that run possible and
estimable. The paid run itself happens later, after the owner has seen the estimate.

## Why

`tools/vibe_agent_eval.py` scores the Vibe coding agent on tasks 1 to 12, on the local model only. To decide whether
native tool calls are worth building, the owner wants the same score once on the registered cloud entry (MiMo).
That run spends API money and sends the eval projects' text (made-up test files only, no personal data) to the cloud
host, so it needs its own guards and a cost figure before anything is sent.

## What

Three flags on the eval script. No change to `codec_llm.py`, `codec_cloud_models.py` or the agent engine.

- `--cloud ID` names an `extra_models` cloud entry. Before anything else the script prints the entry (id, label,
  host), its two prices, its monthly cap and this month's spend on it from `~/.codec/cloud_spend.json` (read only),
  then refuses (exit 2) unless `--yes-spend` is also given. An id that is not a registered cloud entry, or an entry
  without prices, is refused the same way.
- `--yes-spend` lets the run go ahead. Each try starts its run exactly the way the Vibe page does for a cloud run:
  `start_run(..., model="cloud", confirm_cloud=True, cloud_id=ID)`. So `_resolve_llm` checks the entry with
  `block_message` (prices, cap, key), the run checks it again before every call, and `codec_llm` checks the cap and
  records the spend for every reply, as for any cloud run. A cloud run takes tasks 1 to 12 only (the approved
  scope): no `--only` id above 12. A try is not started once the month's spend has reached the entry's cap.
- `--estimate` (with `--cloud`) makes no model call, starts no run and reads no key. It reads a stored local result
  file (`--baseline FILE`, else the newest `baseline-*.json` in `~/Documents/CODEC-private/vibe-eval/`) and the
  entry's prices, and prints:
  - LOW: the tokens the baseline actually used on tasks 1 to 12 (each try as recorded), priced at the entry's
    prices. A try without token counts counts as its steps at the ceiling below.
  - HIGH: the ceiling the agent's own limits allow: every task fails twice and every try uses every step
    (`max_steps`, 6 for task 12) at a full prompt (`max_context_tokens` x 1.5, the top of the measured
    estimate-to-server range) and a full reply (`max_reply_tokens`); one more call per step when compaction is on.
  - the cap headroom (cap minus this month's spend) and whether each figure fits in it.

## Result file

A cloud result carries `kind: "cloud"`, `cloud_id`, the entry's label, host, prices and cap, the month, and
`spent_usd` (what this run added to the ledger; each try also has its own `spent_usd`). A local result file is
unchanged (no `kind` field means local).

A cloud result is never a baseline for the local model: `compare()` and `--baseline` refuse a pair whose kinds
differ (as well as a pair whose model ids differ, as before). `--bar`, `--baseline` and `--compare` for local runs
behave as before.

## Guards

- Two flags for money: `--cloud ID` alone only shows what would be spent; `--yes-spend` is the confirm step.
- The monthly cap stays the hard stop: the agent refuses a call once spend reaches it, and the script stops starting
  tries.
- Nothing in the estimate path calls a model, reads the Keychain or writes a file.
- The key is never printed: the script prints the entry's id, label, host and prices only.

## Tests

`tests/test_vibe_agent_eval.py`, with a fake cloud entry, a fake ledger in a temp folder and the scripted model (no
network): `--cloud` without `--yes-spend` refuses and starts nothing; the estimate makes no model call, reads no
key, leaves the ledger as it was and gives the hand-computed low and high figures; an unknown entry, a task above 12
and `--estimate` without `--cloud` are refused; a cloud try sends the run to the entry with `confirm_cloud`; the
result file records `kind`, `cloud_id` and `spent_usd`; a cloud result is refused as a baseline for a local run.
The existing tests keep the local path as it was.

## Rollback

Revert the commit. The flags are new and nothing else reads them; local result files never had the new fields.
