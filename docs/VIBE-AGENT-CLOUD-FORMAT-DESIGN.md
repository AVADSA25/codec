# Vibe agent on the MiMo cloud entry: the reply format (P-073)

Status: built on branch `fix/vibe-agent-cloud-format`. Owner's word: "go p073" (12 Nov 2026).

## What was wrong

`tools/vibe_agent_eval.py --bar --cloud mimo-v2.6-pro --yes-spend` (#596) scored NO-GO on 12 Nov 2026: file tasks
1 of 8, behaviour tasks 2 of 4, $0.0646. Almost every try ended with status `limit` and "I could not read the model's
replies as tool blocks": four unreadable replies in a row (`FAIL_FORMAT`), from the first step on.

### The transport is not the cause

One try of task 1 was re-run with every request body and every raw SSE line saved (never the headers). In every
reply: `content` holds the whole reply, `reasoning_content` is null, `reasoning_tokens` is 0, `tool_calls` is null,
`finish_reason` is `stop`, and the usage chunk arrives. Nothing is cut, misplaced or dropped by `codec_llm` or
`codec_cloud_models`. The small completion counts (12 to 34 tokens a reply) are simply short replies.

### The model writes its own tool-call tags

MiMo V2.6 Pro wraps the agent's plain-text tool blocks in its own tool-call markup, in a different broken shape
almost every time. Its first reply to the agent's real step-1 prompt, sampled 6 times at the agent's temperature 0.2:

```
<tool_call><@@ list_files .</@@>\n@@ read_file index.html\n@@ read_file style.css\n@@ read_file app.js
I'll look at the existing files first.<tool_call><function=list_files></function></tool_call><tool_call><function=read_file>index.html</function></tool_call>...
<tool_call><@@ list_files></@@ list_files></tool_call><tool_call><@@ read_file>index.html</@@ read_file></tool_call>...
<tool_call><@@ list_files .>\n<@@ read_file index.html>\n<@@ read_file app.js>\n<@@ read_file style.css>
<tool_call><@@ list_files .</@@>\n</@@>
<tool_call><@@ list_files .</@@></function>
```

The two eval tries recorded that day began `<tool_call><@@ list_files **/*</@@></tool_call>` and
`<tool_call><function=list_files></function></tool_call>...`. The parser reads `@@` only at the start of a line,
so none of these is a tool block. The engine's "I found no tool block" message changes nothing: the model copies its
own earlier reply, which is now in the history, and the run stops after four.

What changes the model's behaviour, measured on the same step-1 prompt (each line one probe):

| change to what MiMo sees | plain replies |
|---|---|
| nothing | 0 of 6 |
| one more rule: "never wrap it in tags such as `<tool_call>`" | 0 of 4 |
| thinking switched on (the entry has it off) | 0 of 3 |
| an example reply in the system prompt (at the end, or before RULES) | 0 of 10 |
| one earlier step in the history written in the plain format (`@@ list_files` and its result) | 9 of 9 |

So MiMo can follow the plain format. It does so once it has seen the format used in an assistant turn. A native
tool-call path (sending `tools` and reading `tool_calls`) is not needed and is not built.

Raw evidence (0600, outside the repo): `~/Documents/CODEC-private/vibe-eval/p073-2026-11-12/`:
`raw-task1-try1-main.jsonl` (the eval try on main, every SSE line), `raw-task1-try1-fix.jsonl` (the second try),
`probe-format.jsonl` (every probe above: messages and the full reply).

## The change

1. **A cloud run starts with one step taken by the engine** (`codec_vibe_agent.Run._seed`, used by `_messages` and
   by the helper's `_sub_messages`). The prompt is `system`, the first user message (the goal, unchanged), then an
   assistant turn `proto.SEED_REPLY` ("I will look at the project's files first." and `@@ list_files`), then the
   real `list_files` result of the project as `[RESULT 0 list_files] ... [END RESULT]`, then the run's steps as
   before. The file list is the engine's own (`_t_list_files`, secret-looking names left out, the same as step
   results). It is not a step: no event, no log line, no step count, no budget. Result 0 cannot be asked for
   (`read_output` takes 1 and up). Only for `llm.kind == "cloud"` and a run whose tools hold `list_files`. It costs
   about 60 tokens a call and sits at the start of every call, where the provider's prompt cache keeps it.
2. **A tool block in tool-call tags is read as the plain one** (`codec_vibe_agent_proto.unwrap_tool_calls`, called
   by `scan_reply`). A safety net for the shape MiMo writes most (`<tool_call><@@ read_file a.js</@@></tool_call>`,
   several on a line), not a general tag parser:
   - an opening `<tool_call><@@` or `<tool_call>@@` counts only at the start of a line, or right after the closing
     tags of a wrapped block before it on the same line, once the reply has opened one at a line start. After other
     text on a line it counts for nothing, as `@@` does in the plain format, so text the model quotes from a file
     cannot become a tool block this way;
   - closing tags (`</@@>`, `</tool_call>`) at the end of a line are dropped;
   - lines inside a SEARCH/REPLACE, CONTENT/END or fenced write_file body are never touched, so a file that holds
     these tags is written as the model wrote it;
   - the `<function=NAME>` shape, and any other, stays a format failure: its arguments have no fixed form.
   - While the stream runs, an unfinished `<tool_c...` line after a wrapped read block keeps the stream open, as an
     unfinished `@@` line does (`_may_be_block`, P-072), only in a wrapped reply.
3. **The run keeps the plain form** (`proto.without_todo`, which every stored reply goes through, now also unwraps).
   The model's next steps see their earlier replies in the plain format, which is what keeps MiMo in it.

The result markers are untouched: `wrap_result` and the data rules are as before, and nothing in a result is read
as a tool block.

## What the local model sees: nothing new

- The seed is added only for a cloud run, so the local model's prompt is byte for byte the same.
- `unwrap_tool_calls` returns its argument itself (the same object) when the reply has no opening tag at a line
  start, so `scan_reply` and `without_todo` read every other reply as before. Checked on all 196 local-model replies
  kept in the private run logs (`p049-task14-*-runlog-*.jsonl`): the whole reply (final, lenient and not) and every
  7th-character prefix the stream would show, 5,376 scans, read exactly as origin/main's parser reads them, and
  `without_todo` gives the same text. A wrapped reply is the control: there the two differ, as they must.
- So the local eval baseline rule (a change to what the model sees needs `--bar --baseline`) does not apply.

## Tests

`tests/test_vibe_agent_cloud_format.py`, 21 tests, no network: MiMo's recorded replies read as tool blocks (one block,
three reads on a line, the stream chunks exactly as they came, a wrapped first block with plain lines after it, a
stray closing tag); finish, ask and run_check lose their closing tags; file bodies stay as written (SEARCH/REPLACE,
CONTENT/END, fenced); an opening tag after other text is not a block; the `<function=` shape is still a failure;
replies without an opening tag come back as the same object; a cloud run's every call starts with the seed and the
seed is not a step; a local run and a cloud run without `list_files` have none; MiMo's wrapped replies run and the
next call sees them plain; a cloud run's helper starts with the seed too.

## Rollback

Revert the commit. No stored state, schema, setting or API changed. Run logs keep the model's raw replies as before.

## Not done here

- The page's live line (`static/vibe-agent.js` `describeLive`) looks for `@@` at a line start, so while a wrapped
  reply streams it shows the raw tags for a moment. Display only; logged in docs/known-issues.md.
- No switch in Settings > Vibe: the seed applies to every cloud run.

## Verification

See the PR: one `--bar --cloud mimo-v2.6-pro --yes-spend` run on this branch, results in
`~/Documents/CODEC-private/vibe-eval/cloud-mimo-v2.6-pro-p073-2026-11-12.json`.
