# Vibe page and loop defects (P-064)

Plan item P-064 (audit 25 Oct 2026, reader reports on the Vibe page and the agent loop). Eight defects, each fixed at
its own site. No new feature, no schema change, no new dependency, no audit event, no sandbox change.

## What is wrong and what changes

| # | Defect | Fix |
|---|---|---|
| 1 | A saved session replays the file context. P-042 already saves a user turn as the typed text (`VibeChat.stored`). Still wrong: an assistant turn in a project is saved with all its file blocks; a reloaded older session shows the `[PROJECT FILES]` / `[CURRENT CODE]` block in the bubble and sends it back to the model; if `vibe-chat.js` did not load, the save falls back to the raw history. | `stored` saves `plain` for both roles (an assistant turn in a project keeps its explanation plus `[Files written: ...]`). New `VibeChat.loaded(rows)` cuts the block off a user row when a session is opened, so the bubble, `hist` and `_histForSend` never see it. The no-script fallback saves `plain` too. |
| 2 | Run shows `Exit:undefined (undefineds)` when the server refuses (403 at the tunnel or a blocked pattern, 400 for a language it does not run). | `_runReport(ok, status, d)`: a refusal shows `Error` and the server's own text in the output pane. |
| 3 | Save posts the open file to `/api/save_file` in `~/codec-workspace`, outside the project, and says `Saved: <name>` after a 403. | In a project the button reads **Save version** and calls `VibeProj.saveVersion()`: pending edits are written, then `POST .../checkpoint` (the Versions drawer already labels it "Saved"). No request to `/api/save_file`. Single-file mode keeps `/api/save_file`; `_saveReport` shows `Not saved: <reason>` on a refusal. |
| 4 | Inspect Apply writes all four properties: a transparent background (`rgba(0, 0, 0, 0)`) becomes black, an existing `style` attribute is replaced whole, and the search runs in the open tab even when that is `style.css`. | The panel writes only the controls the owner touched. `_tagWithStyle(openTag, props)` sets those properties in the tag's style attribute and keeps every other declaration. The edit goes to the file the preview shows (`VibeProj.previewPath()`), which is opened in the editor; the click-to-source jump uses the same file. The preview frame sets only what was sent. |
| 5 | Enter while an answer streams starts a second request. | `_vibeBusy` is true from the send until the answer is applied or stopped; `sendVibe` and `doSend` return at once while it is set, and the typed text stays in the box. |
| 6 | If the editor script does not load, `require.config` throws and the rest of the page script never runs (send, resize, code-pane state, folder chip). Monaco is now a copy in the repo (D-24), so the failure is the vendored loader not loading (offline cache, a broken deploy). | `require` is called only when the loader is there, with an error callback; otherwise `#eb` shows "The editor could not load" and the rest of the script runs. The three sites a send reaches without an editor (`setDetectedLang`, the final sync, the preview fallback) check `ed` first. |
| 7 | The time limit counts the minutes the owner takes to answer a question or an approval, so an answer after minute 15 ends the run `limit` the moment it arrives. | `_ask` and `_approval` move the deadline on by the time they waited. `max_seconds` stays the work budget; a run can take longer than that in wall-clock time. |
| 8 | A project with more than 200 files (dropped in by hand or written by a command) makes the file scan raise `VibeError(413)` in the start checkpoint or the per-step file map; the run ends "internal error". | `_main` ends such a run `failed` with one line: "The project has more than 200 files, ...". The 200-file cap and the scan stay as they are (the real fix is P-050). |

Not in this item: file chips and the Revert link on a reloaded turn (need a checkpoint id per message row, P-077);
vendoring Monaco (done on main, D-24).

## API and data

- Page only for 1 to 6: `codec_vibe.html`, `static/vibe-chat.js` (`stored` changed, `loaded` added),
  `static/vibe-projects.js` (`saveVersion`, `previewPath`, `fileText`, `setFileText`; the Save label follows the project).
  Stamps updated with `tools/stamp_static.py`.
- `vibe.db`: no schema change. From now on a project assistant row holds the short text. A session opened and saved
  again is rewritten without the old blocks (the save is a full sync); the files themselves stay in the project folder
  and its checkpoints, and a single-file session keeps its code column.
- Loop: `codec_vibe_agent.py` only (`_ask`, `_approval`, `_main`).

## Tests

- Node (no browser): `stored` and `loaded` in `tests/test_vibe_chat_page.py`; `_histForSend` on a loaded session,
  `_runReport`, `_saveReport`, `_styleDecls`, `_tagWithStyle` taken from the page in `tests/test_vibe_page_js.py`.
- `tests/test_vibe_agent.py` with the scripted model: an answer after the 15-minute mark continues the run; 201 files
  before the start and files that pass 200 during a run end `failed` with the reason (both failed first with
  "internal error"). `tests/test_vibe_agent_cmd_run.py`: an approval given after the mark lets the run go on.
- One headless Chrome check on a temp profile against a stub server that serves the page with the dashboard's CSP
  (scratch harness, not committed): each of the six page defects, then the page with the vendored `loader.js` blocked
  and with `editor.main.js` blocked, each ending with a console read (no script error, no CSP report; only the HTTP
  refusals the check provokes and the blocked script).

## Rollback

Revert the PR. No data, schema or config key to undo. Deploy and rollback both need a `codec-dashboard` restart
(owner's word).
