# Chat stream:false handles [SKILL:] tags like the stream (2026-11-11)

## What was different (known issue 2026-11-03, left by P-099)
`routes/chat.py:chat_completion`, stream:false, resolved only the first
`[SKILL:name:query]` tag; a disallowed tag used no step of the turn's budget; a tag
whose skill returned nothing or raised stayed visible in the reply. The stream
(`_resolve_skill_tag` through `codec_chat_stream.SkillTagBuffer`) resolves every
tag, charges each tag one step, and drops any tag it cannot resolve.

## Change
The stream:false block walks `SKILL_TAG_RE.finditer(answer)` in order and, for
each tag, does what `_resolve_skill_tag` does: consume one `post_llm_skill_tag`
step (allowed or not); past the budget or outside `CHAT_SKILL_ALLOWLIST`, drop the
tag; otherwise run `_try_skill_by_name` in a worker thread, record the action for
the claim check, and put a non-empty result in bold, else drop the tag. A failure
still writes the `post_llm_skill_tag_failed` audit line (A-22), and the tag is
dropped. The reply is rebuilt from the text between tags, so a result that itself
looks like a tag is never resolved again.

## Not changed
The stream path, the post-reply checks (`_post_reply_checks`), the audit lines,
the JSON reply's fields. No page sends stream:false today.

## Tests
`tests/test_chat_reply_parity.py`: two tags, a disallowed tag, and a skill that
gives nothing or fails, each compared on both paths (all four fail on the old code).

## Rollback
Revert the PR.
