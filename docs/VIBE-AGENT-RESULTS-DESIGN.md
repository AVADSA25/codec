# Vibe agent: what the model sees of results, failed edits, audit tool key, replies in the run log (P-040)

Date 2026-11-03. Owner's go for the 25 Oct audit plan: "go go build i trust ur judgment here" (3 Nov 2026).
Code: `codec_vibe_agent.py` only (plus its tests and three doc lines). `codec_audit.py`, `codec_vibe_agent_cmd.py`
(its collector and `profile()`) and `codec_vibe_agent_proto.py` are not changed.

## Why

1. The model saw only the first 6 KB of any result. A test run prints its failure and its summary at the end, so for
   output over 6 KB the agent never saw why the tests failed, nor the sandbox and timeout notes that `_run_check`
   adds after the output. The command collector already keeps the first 4 KB and the last 12 KB.
2. `read_file` returned up to 24 KB and the model saw 6 KB of it, so a header said "lines 1-300 of 700" while the
   model saw about 130 lines, and the "more lines follow" hint at the end was cut away.
3. The third failed edit of one file ended the whole run (`limit`), before the model could switch to `write_file`.
4. `vibe_agent_step` audit lines lost their tool name: `tool` is a reserved key that `codec_audit.audit()` drops
   from `extra`, and no Vibe line had a `correlation_id`. A run could not be followed in the audit log.
5. The run log kept no model reply, so a run could not be looked at afterwards or replayed.

## What changes

**Results.** A result of up to 6 KB reaches the model whole, as before. A longer one reaches it as its first 1.5 KB
and its last 4 KB with `... (N characters left out) ...` between them. The cut falls on a line end where one is
close (the head is shortened to its last line end when that keeps at least half of it; the tail starts at most 200
characters earlier, at a line start), so the last 4 KB are always there whole. The repeat warning and the
failed-edit advice come after the cut, as the repeat warning did. The page still gets up to 24 KB.

**read_file** is cut on whole lines so that the whole result fits in the 6 KB the model sees: the header's line
range is what the model gets. When lines follow, the header line itself ends with
`(more lines follow: @@ read_file PATH NEXT-)`, so the hint can never be cut. `READ_BYTES` goes away. A file with
short lines still reads 300 lines per call.

**Failed edits.** Edits of one file that do not apply are counted in a row (path normalised, refusals by a safety
rule not counted, a change to the file resets it). From the third, the result adds: stop editing it, read it again
with `read_file`, then rewrite the file with `write_file`. When the file is longer than one reply can hold
(`max_reply_tokens` x 3.5 / 2 characters, 5,250 at the default 3,000 tokens) it says instead to copy one or two of
its lines exactly into SEARCH, because a cut `write_file` is rejected and would end the run. The run goes on; the
step, time and repeat limits still end a run that keeps failing.

**Audit.** The step key is renamed `tool` to `tool_name`. `_audit` passes the run id as `correlation_id` (taken
from the `run` field every agent line already has), so every `vibe_agent_*` line of one run shares it. `log_event`
already takes `correlation_id`; no event is added and the envelope is unchanged.

**Run log.** After each model reply the run writes one `reply` event to `.vibe/runs/<run>.jsonl` only:
`{t, type: "reply", step, chars, cut, text, redacted}`. `text` is the reply with every secret shape replaced by
the audit log's own patterns (`codec_audit._redact_string`, the same pass `_write` runs), then cut to 8,000
characters (what `_Turn` keeps); `chars` is the full length and `redacted` says whether the redaction changed it.
When the patterns cannot be loaded, the event keeps the counts and no text. A reply event never goes into
`Run.events`, so the page's long poll never gets it, and `get_run` drops it when it reads a finished run from the
file. It has no `seq`, so it is not part of the page's sequence either (older code, which filters on `seq`, would
skip it too). A step whose model call failed with no text, and is retried, has no reply event.

## Data and migration

No database, no new file, no new audit event. Old run logs have no reply events and read as before. The run logs
stay trimmed to the newest 20 per project; a run of 30 steps adds at most about 240 KB of reply text.

## Tests

`tests/test_vibe_agent.py` and `tests/test_vibe_agent_cmd_run.py`, scripted model, no network:
- a 20 KB command output, collected by the real `_Collector`, reaches the model (through `_run_call`) with its last
  4 KB whole, its first line and the cut marker; short results are unchanged;
- a read of long lines fits the model's 6 KB on whole lines, and its first line names the next line to read;
- the third failed edit of a small file says to rewrite it with `write_file` and the run finishes `done`; for a long
  file it says to copy lines exactly; a good edit restarts the count;
- with the real `_audit` and a temp audit log (HMAC secret patched, no Keychain), `vibe_agent_step` lines carry
  `tool_name` and `correlation_id` equal to the run id;
- a finished run's log holds one redacted reply event per step, and neither the live view nor the view read back
  from the file has a reply event or the raw reply.

Live: `python3 tools/vibe_agent_eval.py --bar --baseline <P-041 baseline>` on the local model, with no voice call,
image job or other eval running. A task 1 to 12 that passed in the baseline and fails both tries means the format
change is revised before the PR is ready.

## Rollback

Revert the PR. Nothing outside the repo changes except the reply events in per-project `.vibe/runs` logs, which
older code skips and which are trimmed with the logs.
