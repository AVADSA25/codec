# Gates: a buyer can connect a brain to CODEC without help

OWNS: codec_setup.py, routes/setup.py, codec_dashboard.html, packaging/macos/**,
      tests/test_setup_connect.py, GATES.md

(The previous ledger — "the packaged CODEC app must actually run on a stranger's
Mac", 6/6 met — is in git history at PR #335.)

Scope: today a fresh install cannot talk to any model at all. `fetch_models.py`
downloads `Qwen2.5-7B-Instruct-4bit`; the chat handler defaults to
`Qwen3.6-35B-A3B-4bit`, which was never downloaded; the installer writes neither
`llm_model` nor `llm_base_url`; and `codec_ava_client.py` is not wired into the
chat path. Every first message fails, with no UI anywhere to fix it. This is the
single thing between "installed" and "usable" for a paying stranger.

Decision (operator, 2026-09-04): the setup lives in BOTH the installer and the
app, and bring-your-own is a generic OpenAI-compatible base URL + key. To stop
two surfaces diverging, the APP is the source of truth: the installer only
pre-seeds a choice, and the app re-tests it before clearing the first-run screen.

## Connectivity is real, not assumed

- [x] G1: A fresh config resolves to a model that actually exists — the downloaded model and the configured default are the same one.
  CHECK: python3 -m pytest tests/test_setup_connect.py -q -s -k defaults_match_reality
  EXPECT: UNLAZY-G1-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=678eec6a73422a25a5aa59d3e454dc0ece5e24dc9364b40de30093925c38471d; output-bytes=591

- [x] G2: /api/setup/status reports not-connected on a fresh config, and connected only after a provider is configured AND verified.
  CHECK: python3 -m pytest tests/test_setup_connect.py -q -s -k status_reflects
  EXPECT: UNLAZY-G2-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=19e51a3b0cdb1a5337e3f1a781ac43c65ee6a25f3433ce59dd36463b4061e674; output-bytes=591

- [x] G3: All three provider modes round-trip: local, AVA cloud, and a custom OpenAI-compatible base URL.
  CHECK: python3 -m pytest tests/test_setup_connect.py -q -s -k provider_roundtrip
  EXPECT: UNLAZY-G3-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=62911349c51de7d47b70436137b08b17df91438a590e4feb8e720af8a7ee2c19; output-bytes=591

- [x] G4: The connection test exercises the REAL chat path and fails honestly — a wrong URL, a bad key and a missing model each report a usable reason, never a false green.
  CHECK: python3 -m pytest tests/test_setup_connect.py -q -s -k test_call_is_honest
  EXPECT: UNLAZY-G4-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=ede76cb89a897f70309c22c65c67772bab1c7aace49cc5121e2a19234503151a; output-bytes=591

## Secrets

- [x] G5: A user-supplied API key is stored in the Keychain and NEVER written to config.json.
  CHECK: python3 -m pytest tests/test_setup_connect.py -q -s -k key_never_on_disk
  EXPECT: UNLAZY-G5-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=6b02264f017da8f47a3951f24f08d48801d1f3c7ce59f360170101ac129e792a; output-bytes=591

## The screen

- [x] G6: The dashboard shows the connect screen while no brain is connected, dismisses it once one is, and offers the same screen from Settings afterwards.
  CHECK: node packaging/macos/verify/setup_screen.mjs
  EXPECT: UNLAZY-G6-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=30c91a661ce0bcca63f516d474e01e7e31615c1e33fd39670d74deb48da044a8; output-bytes=15

## Both surfaces agree

- [x] G7: The installer's provider choice is written in the exact shape the app reads, and the app re-verifies rather than trusting it.
  CHECK: node packaging/macos/verify/installer_contract.mjs
  EXPECT: UNLAZY-G7-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=a0d8eb69aa26d9bffb4b9db35037257a83ac7c7073ad3cb4d656fa6fd3af47ac; output-bytes=15

## No regression

- [x] G8: The existing suite gains no NEW failures beyond the 40 pre-existing llm/stream ones in docs/known-issues.md.
  CHECK: node packaging/macos/verify/suite_no_new_failures.mjs
  EXPECT: UNLAZY-G8-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-setup; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=da0edbf8a7d0be62cd58b5b322681f6177d9daceee987ded6330193c62bd1ca9; output-bytes=59

<!--
Negative controls: G1..G5 run against the pre-fix tree and must fail there.
G4 is exercised against a KNOWN-BAD endpoint as a positive control — a test that
only ever sees a working server proves nothing about honesty.
G8 compares the failure SET, not a count, so a new failure cannot hide behind a
pre-existing one.
Toolchain: macOS. Shell: /bin/bash.
-->
