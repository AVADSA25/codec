# Four small correctness fixes (P-097)

Owner's go, 3 Nov 2026: "go go build i trust ur judgment here" (the 25 Oct audit plan, item P-097,
own findings GP13-6 and GP13-8). Status: built in the PR that carries this file; live after the owner
restarts `codec-dashboard` (and `open-codec` for the wake-word path of the `system` skill).

## 1. Claim check: a skill that did not run does not back a claim
**Why.** Both chat paths (`stream:true` in `_stream_gen`, `stream:false` in `chat_completion`) added the
skill name to the "this turn did it" set as soon as `_try_skill_by_name` returned, whatever it
returned. A declined consent ("Warning: 'file_ops' is a destructive operation and wasn't confirmed —
skipped."), a temporary-chat refusal or a skill that returned nothing therefore backed "I've saved it
to your Desktop", and the correction note never came. `email_triage` (it only reads the inbox) backed
"I've sent the email". The request-side persistence rule flagged ordinary messages such as
"Summarise this: our rule is that for every session the trainer must log attendance" and
"Explain what 'from now on, always use tabs' means".

**Change.**
- Both call sites now record `(skill name, result)` pairs. `_post_reply_checks` (the one place both
  paths share since P-099) turns them into the set of skills that ran with
  `codec_claim_check.backed_actions(pairs)`.
- `codec_claim_check.result_backs_claim(result)` is False for: no result, an empty result, the chat
  consent refusal, the temporary-chat refusal, a switched-off skill, a plugin veto, the licence lock
  and the wake-word "Cancelled" refusal. Each is matched by its fixed opening words; a test builds
  every one from the code that produces it, so a reworded refusal fails the test instead of silently
  backing claims again. A timeout still counts: the skill may still be acting (design bias of the
  module: a false negative over a false positive).
- `email_triage` is removed from the skills that back "sent a message".
- The persistence request is checked on the user's own words only: quoted text ('...', "...", curly
  quotes, backticks) is left out, and a message that opens by asking to work on a text (summarise,
  translate, explain, rewrite, rephrase, paraphrase, proofread, shorten, simplify, analyse, critique)
  is not a persistence request. The reply-side patterns still catch "I'll remember this for future
  sessions" in those turns.

## 2. Web search: an outage is not cached as the answer
**Why.** `codec_search.search` cached whatever Serper returned for 5 minutes: the "Search error" row
when the request raised, or `[]` when Serper answered 429 (out of credit). DuckDuckGo was never tried
when a Serper key was set.

**Change.** `_serper_results` raises on any error or a status other than 200; `search_serper` keeps its
old shape (error row) for any outside caller. `search` falls back to DuckDuckGo when Serper raises or
answers non-200, and caches only a non-empty list without an error row. One warning line names the
HTTP status or the error type, never the query.

## 3. `system` skill: the real disk and a real memory line
**Why.** It ran `df -h /`, the sealed system snapshot (about 2% used on every Mac), and printed the
words "Memory stats available" instead of a number.

**Change.** `df -P -k` on `/System/Volumes/Data` (the volume that holds the user's files) when it
exists, else `/`, parsed with one regex; sizes are shown in base 10 like Finder. Memory is
`sysctl -n hw.memsize` plus `vm_stat`: used = app memory (anonymous minus purgeable pages, or active
pages on an older macOS) + wired + compressed, the sum Activity Monitor shows, in GB of 2^30 bytes.
If either output cannot be read, there is no memory line. The skill is hash-pinned:
`skills/.manifest.json` is regenerated.

## 4. Hue re-discovery keeps settings saved meanwhile
**Why.** `rediscover_and_update_config` read `config.json`, ran a discovery that can take several
seconds (mDNS, cloud, a /24 scan), then wrote the old copy back with the new IP: a setting saved during
the scan was lost. It also used its own fixed `config.json.tmp`.

**Change.** The first read only supplies the stored bridge id. The write goes through
`codec_config.update_config` (P-054): it takes the `config.json.lock` file lock that the other config
writers take, reads the file again under it, sets `hue_bridge_ip` (and backfills `hue_bridge_id`), and
writes atomically. If the file cannot be read at write time, nothing is written and the found IP is
still returned, so the skill can retry the command. `_atomic_write` is removed.

## Data, API, migration
No schema change, no new config key, no new audit event. `_post_reply_checks` takes pairs instead of
names (two callers, both in `routes/chat.py`). New helpers: `codec_claim_check.result_backs_claim`,
`codec_claim_check.backed_actions`, `codec_search._serper_results`.

## Test plan
All fakes or `tmp_path`; no network, no Hue bridge, no real disk or memory read in new tests.
- `tests/test_claim_check.py`: each refusal string, built by its producer, does not back; a real result
  does; `email_triage` does not back "sent"; the two ordinary messages get no note; existing
  persistence-request cases still flag.
- `tests/test_chat_reply_parity.py`: a declined `file_ops` tag on both paths leaves the Desktop claim
  flagged; a real result clears it.
- `tests/test_search_fallback.py`: Serper raises / answers 429 → DuckDuckGo results; neither the error
  row nor `[]` is cached; a good Serper answer is cached as before.
- `tests/test_system_info.py`: fixture `df` / `vm_stat` / `sysctl` outputs give the Data-volume line and
  the memory line; unreadable memory output gives no memory line.
- `tests/test_hue_discovery.py`: a key written during discovery survives, the lock is taken on that
  path and the file is read under it; an unreadable file at write time is left byte for byte.

## Rollback
Revert the PR and restart `codec-dashboard` (and `open-codec`). Nothing on disk to undo.
