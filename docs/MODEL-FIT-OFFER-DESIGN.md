# Offer the best local model that fits this Mac (P-039)

Status: built 2026-11-04 (parts 1, 3, 4). Part 2 (a launchd-aware model-server
restart) is designed below and waits for the owner's word: it is an AGENTS.md
section 10 change.

Owner's word, 2026-11-03: "go go build i trust ur judgment here" (approval for the
design-first gate on the audit plan items). It does not cover section 10 files, so
this change leaves `codec_models.restart_server`, `scripts/start_model_server.sh`
and the switch / probe / revert sequence exactly as they are.

## What and why

A buyer who saw the Studio demo gets the bundled 7B model. Settings should show
the largest catalog model that fits this Mac, its size, a download with a
progress bar, and a button that switches CODEC to it. The pieces exist in
`codec_cookbook` (fit maths, download jobs) but did not work for a buyer:

1. `fit.py` used the Studio's figures for every Mac: 24 GB kept for macOS and
   8 GB of margin. A 16 GB Mac had negative free memory, so nothing fit.
2. `catalog.json` had no entry for the 7B the app ships
   (`packaging/macos/models.json`, `qwen-7b-4bit`).
3. Entries carried no licence, and the 4B and 3B entries had no size, so ranking
   them meant asking the Hugging Face Hub on every call (a Settings page load
   must not reach the internet).

## Changes

**Fit that scales with RAM** (`codec_cookbook/fit.py`). `os_reserve_for(total)`
and `margin_for(total)` return 24 and 8 at 64 GB and above (exactly today's
figures, so the Studio and bigger Macs rank exactly as before). Below 64 GB both
shrink in proportion to the Mac's memory, with floors of 4 GB and 1 GB. 16 GB:
6 GB reserve, 2 GB margin. `probe.available_gb()` uses the scaled reserve when
no reserve is passed; `cookbook_recommend` passes the scaled margin.
`quick_need_gb` uses the KV geometry stored in the catalog entry (copied
verbatim from each model's `config.json`) instead of fetching it, so ranking is
offline. The numbers equal today's online numbers (a test computes both).

**Catalog** (`codec_cookbook/catalog.json`). Every entry gets `label`,
`size_gb` (weight files on the Hub), `licence` (the model card's `license`
field, read 2026-11-04) and `kv`. The 4B and 3B get `anchor_gb` equal to their
Hub weight size. New entry `qwen25-7b`: repo and revision equal to
`models.json` `qwen-7b-4bit`, roles `chat`, 4.3 GB, Apache-2.0.

**Licences** (`codec_cookbook/catalog.py`). `COMMERCIAL_LICENCES` is the
allowlist: `apache-2.0` and `llama3.2`. Only entries whose licence is on it are
offered. `llama3.2` carries the notice its licence requires (the attribution
line, "Built with Llama", the Acceptable Use Policy link), shown in Settings
whenever the Llama entry is offered. The Studio's own fine-tune is not in the
catalog, so it is never offered.

**Offer** (`codec_cookbook/offer.py`, new). `build()` ranks the general-purpose
entries (roles chat, reason, max, fast, tiny; the coder model is left out of a
chat-model offer) with the scaled fit, keeps those that fit and are licensed
for commercial use, and marks each as in use, downloaded, or not. `best` is the
largest; `upgrade` says whether it is bigger than the model in use.
`start_download(id)` refuses an entry that is not offered or already on disk,
and refuses when the disk has less than its size plus 2 GB free; it then starts
the existing detached Cookbook download, pinned to the entry's revision, and
writes one `model_download_started` audit line. `download_progress(id)` adds
bytes on disk (Hub cache blobs, including partial files) and a percent.
`use(id)` refuses an entry that does not fit this Mac (the memory guard), one
that is not downloaded, and any switch while an image job holds
`~/.codec/image_job.lock`; otherwise it calls `codec_models.set_active` (the
existing switch, probe and revert). When Create image is installed and the
model would leave less free memory than its `min_free_gb` (36 GB), the offer
says Create image waits while that model is loaded. The 36 GB guard itself is
not changed.

**Routes** (`routes/models.py`): `GET /api/models/fit`,
`POST /api/models/fit/download` `{id}`, `GET /api/models/fit/download?id=`,
`POST /api/models/fit/use` `{id}`. Same auth as `/api/model`.

**Page** (`codec_dashboard.html`, Settings > Language model). A "Best model
for this Mac" row: memory line, the best model with size, licence and notice,
a Download button and a `<progress>` bar while it downloads, a Use button
(with a confirm) once it is on disk, and the other models that fit under a
disclosure. Server text goes in with `textContent` only.

Downloads land in the Hugging Face cache (`~/.cache/huggingface/hub`), where
`codec_models.discover_local` finds them, not in `~/.codec/models`.

## Audit

`model_download_started` (source `codec-cookbook`, info): `model` (catalog id),
`repo`, `size_gb`. The switch reuses `model_switched`.

## Test plan

`tests/test_model_fit.py`: the 64 GB ranking pinned literally and equal to the
old constants with Hub-fetched numbers; 16 GB offers the 7B first and nothing
needing more than the free memory; 7B entry equals `models.json`; every entry
carries an allowed licence; a fitting entry with another licence, or none, is
never offered; the Llama notice; refusals for a model that does not fit, one
not downloaded, an image job running, short disk; the download revision pin,
byte count and percent; routes; the page code. RAM, disk and the lock are
fixtures; nothing downloads, nothing calls the real `set_active`.

## Rollback

Revert the PR. A downloaded model stays in the Hugging Face cache (delete
`~/.cache/huggingface/hub/models--<org>--<name>` by hand). A failed switch
already restores `llm_model` and `llm_local_restore` (existing revert).

## Not in this change: launchd-aware restart (done-when 2, section 10)

On a buyer's Mac there is no PM2, so `restart_server` returns "pm2 not on PATH,
switching in place": the probe makes the running server load the new model in
place (it resolves the model per request), but the old model's memory is not
given back and a later restart of the job is the only reclaim. Proposed, for the
owner's word:

- In `codec_models.restart_server`, when `pm2` is absent and
  `codec_supervisor.kind() == "launchd"`, use the LaunchAgent
  `ai.avadigital.codec.<model_pm2_process>`: `launchctl kill SIGTERM
  gui/<uid>/<label>`, wait until `codec_supervisor.launchd_jlist()` no longer
  reports it online and the port is free (same 30 s limit), then
  `launchctl kickstart gui/<uid>/<label>` and wait for status online plus the
  port (same 300 s limit). Stop-then-start, so one model is resident at a time.
- Without that LaunchAgent the current in-place message stays.
- `tests/test_model_switching.py` stays green; new tests fake `launchctl` and
  `launchd_jlist`, never the real one.
