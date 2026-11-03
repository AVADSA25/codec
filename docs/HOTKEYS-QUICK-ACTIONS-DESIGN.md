# Hotkeys that follow Settings, a laptop preset, Quick Actions in the app (P-029)

Two independent halves of one buyer problem: a buyer on a laptop has no push-to-talk
key, the Settings page that offers to change it did nothing, and Proofread / Translate /
Read Aloud on selected text existed only on the Studio.

## A. Hotkeys

**What was wrong.** Settings > Hotkeys saved `key_toggle`, `key_voice` and `key_text`,
and `codec_config` resolved them to `KEY_TOGGLE`, `KEY_VOICE`, `KEY_TEXT`, but nothing
used those: `codec.py` compared every key press with `keyboard.Key.f13`, `.f16` and
`.f18`, and its on-screen hints said "F18=voice F16=text".

**Decision (session lead, DECISIONS.md):** wire the settings, do not remove them.
Defaults stay F13 (start / stop), F18 (push to talk), F16 (type a command).

**What changes.**

- `codec_hotkeys.py` (new, stdlib only) is the one place for the three field names,
  the defaults, the presets and the name rule. A hotkey name is `f1` to `f20` (pynput
  on macOS has exactly those). `names(cfg)` gives the three names the listener uses:
  a missing or unusable name falls back to its default, so a typo never leaves CODEC
  with no start key.
- `codec_config.resolve_hotkeys(cfg)` turns those names into pynput keys;
  `KEY_TOGGLE, KEY_VOICE, KEY_TEXT = resolve_hotkeys(cfg)`. The old `_resolve_key`
  (which also accepted single characters, which never matched a key press) goes.
- `codec.py` `on_press` / `on_release` compare with `KEY_TOGGLE`, `KEY_TEXT`,
  `KEY_VOICE`. The toggle overlay, the wake overlay, the recording overlay
  ("release F8 to send"), the console lines and the start banner print the
  configured keys. Keys are read when `open-codec` starts, so a change applies after
  a restart (the Settings help says so).
- **Laptop preset: F6 start / stop, F8 push to talk, F9 type.** Three different keys,
  all within F1 to F12 (a MacBook keyboard stops at F12). Not F5: `codec_dictate.py`
  already toggles live dictation on F5, so the old installer preset (F5/F8/F9) made one
  press do two things. `codec_hotkeys.RESERVED` names F5, and a test reads the F-keys
  `codec_dictate.py` listens for so the two lists cannot drift. On a MacBook the F-row
  sends brightness and media keys unless "Use F1, F2, etc. keys as standard function
  keys" is on (System Settings > Keyboard) or fn is held; both screens say so.
- **Settings**: the three fields become dropdowns of F1 to F20, and the section gets a
  "Keyboard preset" row with one button per preset. `PUT /api/config` refuses a name
  outside F1 to F20, F5, and a key another hotkey already uses (422, the page puts the
  old value back and says why). Values are saved lower case.
- **First-run screen** (the "Connect CODEC to an AI" card) gets a Keyboard row with
  the same two presets. Choosing one writes the three keys at once.
- **API**: `GET /api/hotkeys/presets` returns `{presets: [{id, label, help, keys}],
  current, active}`; `POST /api/hotkeys/preset {"preset": "laptop"}` writes the three
  keys under the config lock (P-054) and answers `{saved, preset, keys}`. Both pages
  draw the buttons from that answer with `textContent`.
- `setup_codec.py` (source installer) takes its two choices from `codec_hotkeys`, so
  its laptop preset becomes F6/F8/F9 too.

Existing configs keep working: a saved F5 (or any valid name) is still used; only new
saves are checked.

## B. Quick Actions

**What was wrong.** `setup_codec.py` said the eight Quick Actions "will be created on
first CODEC launch", but no code wrote a `.workflow`. The Studio's eight were made by
hand and run `python3.13 /Users/<owner>/codec-repo/codec_textassist.py <mode>`.

**What changes.**

- `packaging/macos/quick_actions.py` (new, stdlib only, copied by `build_app.sh` to
  `Contents/Resources/` next to `first_run.py`) writes `CODEC <Name>.workflow` for
  Elevate, Explain, Prompt, Proofread, Read Aloud, Reply, Save and Translate. Each is
  the Studio's shape (one Run Shell Script action, text in, nothing out, Services menu)
  with deterministic UUIDs, so a second run produces the same bytes.
- The shell script inside runs the bundle's own interpreter on the bundle's own
  `Resources/app/codec_textassist.py`. If either is gone (the app was moved or
  deleted), it shows one notification ("CODEC was moved or removed. Open CODEC again
  to repair its Quick Actions."), prints nothing and exits 0, so Automator shows no
  shell error. The script's output goes to `~/Library/Logs/CODEC/quick-actions.log`.
- **Ownership marker.** Each Info.plist carries a `CODECQuickAction` key. A folder of
  that name without the marker (the Studio's hand-made ones, or one the user edited in
  Automator, which drops unknown keys) is never written, renamed or removed. A folder
  with the marker is rewritten only when its bytes differ. Writes go to a hidden temp
  folder and are renamed into place.
- **When.** `first_run.py` writes the missing ones on first launch (after the fleet and
  the models, before the permission report; skipped when `config.json` says
  `assist_enabled: false`, and refused like the LaunchAgents when the app runs from a
  DMG or a translocated copy). On every later launch `codec_app_main.py` runs
  `first_run.py --quick-actions-repair`, which only rewrites marked ones whose paths
  changed (so a moved app heals when opened) and never recreates one the user deleted.
  After a change to the real `~/Library/Services` it runs `pbs -update` so the
  Services menu sees it. Tests always pass a temporary Services folder.
- **Behaviour (unchanged, now pinned by a test that runs `codec_textassist.py` with
  stubs):** Proofread, Elevate, Reply and Prompt replace the selection (copy, then
  paste). Explain and Translate copy the result and show it in a window; they do not
  paste.
- **Uninstaller** lists the eight folders and removes the marked ones; an unmarked
  one is listed as kept.

## Tests

`tests/test_hotkeys.py`: defaults pinned (module, `config.json.example`, the API and
pynput resolution), the laptop preset rule, name fallback, simulated key presses on a
non-default setting, the API refusals and the preset write, both pages listing the
presets. `tests/test_quick_actions.py`: eight folders in a temp Services folder,
their plists, the script run against fake tools (moved app: one notice, no output,
exit 0), never overwriting an unmarked folder, repair mode, dry run, the textassist
behaviour, the first-run and launch wiring. `tests/test_uninstaller.py`: the dry run
lists the eight folders and `--yes` removes only marked ones.

## Rollback

Revert the PR. Saved keys keep their values and the defaults are unchanged; codec.py
goes back to F13/F18/F16 whatever Settings says. Quick Actions already written stay
and keep working while the app stays where it is; the uninstaller of this PR, or
deleting the folders from `~/Library/Services`, removes them.

## Owner actions

Restart `open-codec` to pick up a changed key (and `codec-dashboard` for the new
Settings controls and routes). Try the laptop preset and the Quick Actions on the Mac
Air with a fresh app build: the Services refresh differs by macOS version.
