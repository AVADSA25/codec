# Voice call inside Chat, and Vibe panes that open by themselves

Status: approved by the owner (2026-11-11, "go build both"). Two separate builds, two PRs.

Owner's words (11 Nov): "add live voice inside chat, we just need a phone icon, voice to voice then start like it is now, then once voice to voice is over a transcript of the voice to voice chat must be inserted into the chat where user is, same if a voice chat is initiated later on the chat after some back and forth text to text chat, the voice chat need context". On Vibe: "I think best we keep it separated". On the Vibe windows: "we should be able to collapse them, or it like proactively showing once u get cooking".

## Part 1: a voice call from inside a chat

### What the owner sees

1. A phone icon in the chat box, next to the microphone.
2. A click opens the Voice page, exactly as it is today: the same call, the same voice 02, the same "stop when I talk", the same modes. The page shows one line at the top: "Linked to: <chat title>".
3. The call already knows the chat. If he typed ten messages before, voice can answer "as we said above".
4. He hangs up. The page goes back to the same chat. The call's lines are now in the chat, after the typed ones, each with a small phone mark and a divider "Voice call, 4 min". Typing afterwards goes on with all of it: the next text reply sees the call too.
5. A call started on a new, empty chat works the same way: the chat is created with the call in it.
6. A temporary chat (P2.11, never saved) gets a plain call with no link: nothing of the call is written to the chat, as before.

### Why the Voice page and not a call window inside the chat page

The Voice page holds the microphone, the speaking worker, the audio player and the reconnect logic (about 1,200 lines that work). Copying that into the chat page, or moving it into a shared script, means touching a working page for no gain. Opening the Voice page with the chat's id keeps it as it is; the only change on that page is reading the id, passing it on, and going back to the chat at the end. It works the same on the phone (PWA).

### How it works

**Opening.** The phone icon goes to `/voice?chat=<id>` with the chat's id (the same id the page already keeps in `codec-chat-session`). The Voice page adds `&chat=<id>` to its socket address. `routes/websocket.py` checks the id against the chat id rule (`^[A-Za-z0-9_-]{1,80}$`) and hands it to `VoicePipeline(chat_id=...)`. An id that fails the rule is ignored and the call is a plain one.

**Context.** The server reads the chat from `qchat.db` itself; the page sends no chat text. It takes the newest 12 typed lines (user and assistant), each cut at 800 characters, 4,000 characters in all, oldest dropped first, and adds them to the voice system prompt as one block "Earlier in this chat", wrapped with `codec_fence.fence` (an older reply can hold web page text). Putting it in the system prompt, not as separate turns, means:
- the voice 20-turn trim never removes it;
- the call's memory save (`save_to_memory`) and the write-back below only see the call's own turns, so nothing typed is saved twice;
- a reconnect keeps it, because the resume store keeps the system prompt.

Cost: about 1,000 more tokens for the model to read on every voice turn. The local model reads about 850 tokens a second with no reuse of an unchanged prompt start, so each answer starts about 1.2 s later on a long chat. That is why the block is capped at 4,000 characters. A short chat costs almost nothing.

**Writing the call into the chat.** When the socket closes, the server appends the call's turns to that chat in `qchat.db`:
- only the user and assistant turns of the call, the same filter as `save_to_memory`; the greeting is not written;
- assistant text cleaned the same way the call shows it (no `<think>` block, no skill tag);
- each row marked `source = 'voice'`;
- a reconnect does not write a line twice: the pipeline counts the turns already written and carries that count in the resume store with the messages;
- a chat that does not exist yet is created, titled with the first thing he said.

The server writes, not the page, so the call is still saved if the phone locks or the tab closes during the call.

**Going back.** On hang-up the Voice page goes to `/chat#chat=<id>` (a new, fixed form of the hash link the chat page already reads; it only opens that chat, it never sends text). The chat page loads the chat the normal way and draws `source = 'voice'` rows with the phone mark and one divider per call. A "Back to chat" link also stays on the Voice page in case he wants to stay.

**Memory.** Unchanged: the call is still saved to `memory.db` under its own `voice_...` session, as today.

### Changes

| File | Change |
|---|---|
| `routes/qchat.py` | New column `qchat_messages.source TEXT` (NULL = typed). Additive, backup of `qchat.db` first, the same pattern as P2.12. `/api/qchat/session/{id}` returns `source`. One helper `append_voice_turns(chat_id, turns)` used by the call. |
| `routes/websocket.py` | Read and check `chat`, pass it to the pipeline. |
| `codec_voice.py` | `chat_id` argument; the context block in the system prompt; the write-back on close; the written count in the resume store. |
| `codec_voice.html` | Read `?chat=`, pass it on, the "Linked to" line, back to the chat on hang-up. |
| `codec_chat.html` | Phone icon (line icon, no emoji; hidden in a temporary chat); the `#chat=<id>` hash; the phone mark and divider on voice rows. |
| `docs/codec-architecture.md` | Two audit lines. |

**Audit:** `voice_chat_linked` (rows and characters read into the call) and `voice_chat_written` (rows written). Counts only, never text or a title.

**Switch:** `config.json:voice.chat_link = false` makes the server ignore `chat` (no context, no write-back); the call is a plain one. Read per call, no restart.

### Not in this

- Vibe. Vibe chat has no memory, persona or skills on purpose (P-042, D-22), and an agent run takes minutes. Voice in Vibe needs its own design, later if ever.
- Wake word and the F18 key: unchanged.
- Calling from a chat in a second tab while typing in the first: the call reads the chat when it starts; lines typed during the call land before the call's lines.

### Tests

- Context block: cut rules (12 lines, 800 and 4,000 characters, oldest dropped first), fenced, absent for a bad id or with the switch off.
- Write-back: only call turns, no greeting, `source = 'voice'`, no double write across a reconnect, a missing chat is created, nothing written without a chat id.
- Migration: the column is added once, the backup is made first, old rows read back with `source` NULL.
- Pages: the icon is hidden in a temporary chat; the hash only opens a chat; voice rows get the mark (string checks in the style of the existing page tests).
- All against temp databases (`tmp_path`), never the live `~/.codec`.

### Rollback

Revert the PR. The `source` column stays and is ignored by the old code. Or set `voice.chat_link` to false.

## Part 2: Vibe editor and preview that open when there is something to show

### Today

On a wide screen the Vibe page shows three panes side by side: chat, code, preview. A new project opens with an empty editor ("// Ask CODEC to build something") and an empty preview. "Hide code" exists (one setting for all projects) and the preview has a close button; a closed preview reopens when an AI change touches an HTML file, even after he closed it, and stays closed after other changes.

### What changes

1. **Each pane has three states: auto, open, closed.** The state is kept per project in the browser (`codec-vibe-panes:<project id>`), and per page for single-file Vibe.
2. **Auto (the default):** the code pane is closed while the project has no file and opens when the first file appears (an agent write, a chat answer that writes files, or opening a file). The preview is closed while there is no page and opens the first time there is one, including after an agent change (today a closed preview ignores that).
3. **His click wins.** Closing a pane sets it to closed: it never opens by itself again in that project until he opens it. Opening it sets it to open.
4. **Collapsed panes leave two small buttons, "Code" and "Preview"**, at the top right of the chat pane, to bring them back. With both closed the chat takes the whole width (text kept at a readable width, centred).
5. "Hide code" keeps working and now sets the code pane of the current project. The old global setting becomes the starting state for projects with no choice yet, so nothing he set before changes.
6. Phone layout (Chat / Preview / Code tabs): unchanged, except that a tab gets its dot when its pane opens by itself.
7. A project with no page (a server or a script) keeps the preview closed on auto; its Run button sits in the preview header, so it takes one click on "Preview" first.

### Changes

Page only: `codec_vibe.html` (state, the two buttons, the auto rules). The open/close rule is one plain function, tested with Node the same way as `tests/test_vibe_agent_page.py`. No server change, no new setting.

### Tests

- The rule: auto opens on the first file and the first page; closed stays closed; open stays open; per project; the old "Hide code" value carries over.
- Page strings: the two buttons, `textContent` only.
- One headless Chrome check of the wide layout with both panes closed and after the first file (the browser pane blocks sub-frames).

### Rollback

Revert the PR. The browser keys are left behind and ignored.

## Order and size

1. Vibe panes first: page only, about 150 lines with tests, low risk.
2. Voice in Chat second: about 350 lines with tests over six files, one additive column.

Neither restarts anything by itself. Both reach the owner after his usual `codec-dashboard` restart (D-30), and the Voice page change needs the open Voice tab reloaded.
