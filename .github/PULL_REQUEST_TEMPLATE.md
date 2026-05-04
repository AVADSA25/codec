<!--
Thanks for contributing to Sovereign AI Workstation / CODEC.
Fill the relevant sections; delete the rest.
-->

## Summary

<!-- One-paragraph description of what this PR does and why. -->

## Reference

<!-- Link to: relevant docs/PHASE*-*.md design or plan, related issue, prior PR -->

## What changes

| Path | Type | Purpose |
|---|---|---|
| `path/to/file.py` | NEW \| MOD | what this file is responsible for |

<!-- Repeat per file. Skip if changes are obvious from the diff. -->

## Test plan

- [ ] 🧪 New tests added (file: `tests/test_*.py`)
- [ ] 🧪 Full pytest passes — same baseline 20 failed / 73 skipped, only new passing tests added
- [ ] All new audit events emit with correct `correlation_id` per Step 1 §1.4 envelope contract
- [ ] No writes to `~/.codec/*` from tests (verify `temp_codec_dir` fixture covers `codec_audit._AUDIT_LOG`)
- [ ] All kill switches still work (env var disables the feature)

**Manual smoke test after merge:**
- [ ] `git pull && pm2 restart <service>`
- [ ] [describe the user-facing test sequence here]

## Audit emits added

<!-- List any new audit event names + frozenset addition. Skip if none. -->

## Kill switches added or modified

<!-- List any new env vars / config flags. Skip if none. -->

## Out of scope (explicitly deferred)

<!-- What this PR does NOT do, with rationale. Helps reviewer scope expectations. -->

## Self-review checklist

- [ ] Read every line of the diff myself
- [ ] No commented-out code, no `print()` left in
- [ ] No emojis added to code/files unless explicitly requested by the user
- [ ] No `~/.codec/*` paths hand-written that should go through atomic R/W helpers
- [ ] Followed existing patterns; didn't refactor unrelated code
