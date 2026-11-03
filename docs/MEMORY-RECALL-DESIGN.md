# Memory recall on normal sentences (P-062)

## What

`CodecMemory.search()` (`codec_memory.py`) finds rows for questions typed or spoken the normal
way. Today "what's the weather", "weather?" and "invoice, hotel" return nothing (the apostrophe,
the question mark and the comma are FTS5 syntax errors, swallowed into `[]`), and "did the hotel
get paid yesterday" returns nothing because FTS5 joins bare words with AND, so every word, "did"
and "the" included, must be in one row. The first demo question, "What did we discuss about the
hotel", fails the same way.

## How (query side only)

`_sanitize_fts_query` is not changed (a test pins its output and `routes/memory.py` calls it
before `search()`). After it, `search()` builds its own MATCH expressions:

1. **Words.** `_query_words()` takes the words of the sanitized text (letters, digits and
   underscores, joined by an inner `'`, `-` or `.` as in "what's", "follow-up", "3.7.0"), each
   once, in order.
2. **Content words.** The same words without common ones (`_COMMON_WORDS`: articles, pronouns,
   helper verbs, question words, "discuss", "said", "remember", "yesterday" and the like: words
   that say how a question is asked, not what it is about). A trailing possessive is dropped
   ("hotel's" looks for "hotel").
3. **Quoting.** Every word goes into the query as an FTS5 string (`"word"`), so no character in a
   query can act as FTS5 syntax: punctuation, `AND`/`OR`/`NEAR` that slip past the sanitizer, `-`
   and `col:` all become plain text. The operators come only from the builder.
4. **Three passes, most exact first,** each ranked by BM25, a row never repeated, stopping when
   `limit` rows are found:
   - every word present, in any column: exactly what `search()` returned before, so for a
     plain-word query the old result list is the start of the new one (exact phrases never rank
     worse);
   - every content word present in the message text (only when common words were dropped);
   - any content word present in the message text, `OR`-joined (only with two or more).
   The new passes look only at the `content` column, so a word such as "user" or "2026" does not
   pull in rows by their role or timestamp. A query made only of common words runs the first
   pass alone, as before.
5. **Fallback.** If the FTS index fails (`sqlite3.OperationalError`, for example a missing or
   damaged `conversations_fts`), `search()` logs the error class (never the query) and searches
   `conversations.content` with `LIKE` instead of returning `[]`: rows holding any content word
   (rows holding more of them first, then newest), or all words when there are no content words;
   at most 8 words; `score` is minus the number of words found, so lower is still better. If that
   fails too, `[]` as before.

Callers do not change: `get_context()` (chat, voice, wake word), `GET /api/memory/search`, the MCP
`search_memory` tool and the `memory_search` skill all call `search()`.

## API and data changes

None. Same signature, same row dicts (`id, session_id, timestamp, role, content, score`). No
schema change, no FTS rebuild, no new audit event, no new dependency. Two small behaviour
changes: a `limit` below 1 now returns `[]` (a negative limit used to mean "no limit"), and FTS5
column syntax typed into a query ("role:user") is read as plain words.

Effect to know about: voice turns and wake-word replies call `get_context()` with the whole
sentence, so they will now often get up to 3 or 5 past snippets where they got none (a few hundred
more prompt tokens per turn). That is the behaviour those call sites were written for.

## Migration

None. The change is live when the processes that import `codec_memory` restart: `codec-dashboard`
(chat, memory page, `/api/memory/search`), `open-codec` (wake word) and `codec-mcp-http` (MCP).

## Test plan

`tests/test_memory_recall.py`, fixture databases of made-up rows in `tmp_path` only:
- the four sentences and the demo question return their row, and plain FTS of the same text finds
  nothing (the old behaviour);
- "what's the weather" never returns a row without "weather" (with a positive control showing that
  row is searchable);
- for phrase queries over the old test texts plus look-alike rows, the old result list is the start
  of the new one;
- punctuation and operator words never reach FTS5 as syntax (the fallback is not used);
- a dropped FTS table falls back to LIKE and still finds the row; a second failure returns `[]`;
- `user_id` is respected in every pass and in the fallback;
- `_sanitize_fts_query` output is pinned.
`tests/test_memory.py` and the other tests that import `codec_memory` stay green.

## Rollback

Revert the commit and restart the three processes. Nothing is stored differently, so there is
nothing to undo in `memory.db`.
