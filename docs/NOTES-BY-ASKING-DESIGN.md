# Save a note by asking — design

Status: approved 23 Oct 2026 ("proceed on both"). Built the same day.
Follows `docs/NOTES-DESIGN.md` (the Notes tab, #467), which left "CODEC cannot write notes" as its default.

## Goal

The owner asked (23 Oct 2026): "make sure that codec is able to save note for me for when im asking."

When he says "save a note: buy lemons for Friday" in chat, by voice or through a bridge, CODEC saves it on the Notes shelf (Tasks > Notes) and answers "Saved to Notes: buy lemons for Friday."

## What exists today (checked 23 Oct 2026)

- Those phrases already match the built-in `notes` skill (`skills/notes.py`): "take a note", "save a note", "note this", "note that", "jot down", "new note", "add a note", "make a note", "write a note", "remember that", "my notes", "show notes". Checked with `check_skill`: "save a note: buy lemons", "take a note ...", "note this: ...", "save that to my notes", "add to my notes: ..." and "show my notes" all resolve to `notes`.
- That skill writes into the macOS **Apple Notes** app with `osascript` (a wait of up to 10 seconds, and the chat request waits for it), and "show my notes" opens the Apple Notes app. The new Notes tab never receives them.
- `notes` is on the chat allowlist (`CHAT_SKILL_ALLOWLIST`), is not in the voice skip list, and is exposed over MCP (`SKILL_MCP_EXPOSE = True`).
- Trigger conflicts are settled by the longest matching trigger. A second skill with the same phrases would be a coin toss, so this design does not add one.
- No test mentions the Apple Notes skill.

## Design (one plan)

1. **The `notes` skill saves to CODEC Notes by default.** The same phrases, no new skill. Apple Notes is used only when the message says "apple notes" ("save a note in Apple Notes: ..."), with the old code path unchanged.
2. **The text** is what follows the phrase, found by the skill's existing parsing ("save a note: X", "note this, X", "jot down X"). It goes through `codec_notes.create(text, source="chat")`, so the Notes limits, the id rules, the 0600 files and the audit events (`note_saved`: id, source, size, never the text) are exactly those of the tab. A title is made from the first line.
3. **No text** ("save that note", "save that to my notes"): CODEC answers "Tell me what to save, for example: save a note, buy lemons. Or press the bookmark under a reply." Version one does not read the previous reply.
4. **"Show my notes"** answers "Your notes are in Tasks > Notes (12 notes)." It says how many, never what is in them. With "apple notes" in the message it still opens the Apple Notes app.
5. **The AI cannot write notes on its own.** The skill refuses when the call comes from a model-written skill tag (`app` contains "LLM-routed"), so a web page or a file CODEC read cannot make it write notes. Only the owner's own message (typed, spoken or sent through a bridge) saves a note.
6. **MCP stays as it is**: over claude.ai the skill keeps writing to Apple Notes. Moving that to CODEC Notes is a separate decision.
7. **Plain sentences for refusals**: a full shelf ("Notes are full (2,000). Delete one first."), a note over 100,000 characters, a switched-off shelf ("Notes are switched off").

## Files

Changed: `skills/notes.py` (and `skills/.manifest.json`, regenerated with `tools/generate_skill_manifest.py --write`, as the contributor rule requires), `AGENTS.md` (one line in the Notes section).
New: `tests/test_notes_skill.py`, this doc.

## Tests

- The phrases above save to the Notes shelf and answer "Saved to Notes: ...", with the right title and `source` "chat"; the Apple Notes path is not called (`subprocess` is replaced by a recorder).
- "In Apple Notes" in the message still calls `osascript` and answers "Saved to Apple Notes".
- A call from a model-written tag (`app="CODEC Chat (LLM-routed)"`) saves nothing and says why.
- No text answers with the hint and saves nothing; a full shelf and an over-long note answer with their sentence.
- "Show my notes" gives the pointer and the count, never any text; with "apple notes" it opens the app.
- The audit log gets no title and no text; the voice skip list and the chat allowlist are unchanged.
- Page-level tests of the Notes tab keep passing; `ruff` and the whole suite.

## Rollout

`skills/notes.py` is read at startup by `codec-dashboard` (chat) and `open-codec` (voice), so after the merge the owner restarts both once: `pm2 restart codec-dashboard open-codec`. I check afterwards with the internal token that "save a note: ..." reaches `~/.codec/notes/` and then trash the test note.

## Rollback

Revert the commit and restart the two processes. Notes already saved stay in `~/.codec/notes/`.

## Decisions (defaults chosen; say if you want another)

1. **Where "save a note" goes:** CODEC Notes by default, Apple Notes only when you say "Apple Notes". Other option: keep Apple Notes as the default and say "in CODEC notes" for the new shelf.
2. **claude.ai (MCP):** unchanged for now.
3. **"Save that" (the previous reply):** not in version one; the bookmark under the reply does that.
4. **Reading notes back by voice or chat:** not now; "show my notes" only points to the tab. Reading text aloud needs its own design (privacy).

## Changes made while building

- "add to notes", "save to notes" and "note to self" are new trigger phrases; "add to my notes" and "save this to my notes" were already caught by the existing "my notes" trigger but were read as a request to open the notes, so they are now write phrases.
- The note text is found by a new reader that keeps the case and punctuation ("Chef Giorgio's number is 0612") and does not stop at a colon inside the sentence ("the fish arrives at 10:30"). The old Apple Notes path keeps its own reader, unchanged except that "in Apple Notes" is not saved as part of the note.
- "Show my notes" over claude.ai (MCP) still lists the Apple Notes inline; locally it now answers with the pointer and the count.
