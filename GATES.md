# Gates: "Use the local model" works on a buyer's Mac

OWNS: requirements.txt, packaging/macos/**, codec_setup.py, routes/setup.py,
      codec_dashboard.html, scripts/start_model_server.sh, tests/test_local_model_bundle.py, GATES.md

Scope: the connect screen offered a local model that could not exist on a buyer's
Mac. Three independent reasons, each fatal: the bundled Python has no mlx_vlm;
scripts/start_model_server.sh is not in the bundle and hardcodes the developer's
venv; and first_run.py invokes fetch_models WITHOUT --yes, so the "bundled" LLM is
a dry run — never downloaded. Operator decision 2026-09-04: accept the bundle
growing from ~273 MB to ~1.2 GB for the best first-run experience.

- [x] G1: The bundled interpreter can serve MLX models — `import mlx_vlm` succeeds inside the bundle, and the dependency is declared with a darwin/arm64 marker so Linux CI does not try to install it.
  CHECK: node packaging/macos/verify/mlx_bundle.mjs
  EXPECT: UNLAZY-G1-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=dc6dc367ec60e5dddea47d34ea91736c4149d3449a1ccbf44002528e86284577; output-bytes=15

- [x] G2: The model-server launcher ships in the bundle and, with no developer venv present, resolves to the BUNDLED interpreter.
  CHECK: node packaging/macos/verify/model_server_script.mjs
  EXPECT: UNLAZY-G2-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=f84ca6efa6efa2657b3702f9a7391fa2b6fb2142e9c61a59e4a0476490dc4c7f; output-bytes=15

- [x] G3: The bundled stack actually serves a model — started from the bundle on a port PM2 does not own, it answers /v1/models and a chat completion.
  CHECK: node packaging/macos/verify/model_server_e2e.mjs
  EXPECT: UNLAZY-G3-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=968f24fe1b4fb309d47cf4c11adef3578145e7c3941522c9ba56ee36ad1e0012; output-bytes=48

- [x] G4: The connect screen can download the bundled model with visible progress, refuses a duplicate download, and reports failure honestly.
  CHECK: python3 -m pytest tests/test_local_model_bundle.py -q -s -k download
  EXPECT: UNLAZY-G4-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=c15f3b7274f3af1a3f426dc088d8e35134d309c770826da388614f0137056ac6; output-bytes=584

- [x] G5: A downloaded model is discovered by the connect screen and becomes selectable as `local` without a restart of anything the user can see.
  CHECK: python3 -m pytest tests/test_local_model_bundle.py -q -s -k discovered_after_download
  EXPECT: UNLAZY-G5-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=ff599f6537dd42d0f730885870f023376a18819616d28631bf34dd5129de7abc; output-bytes=596

- [x] G6: No NEW test failures beyond the documented pre-existing set.
  CHECK: node packaging/macos/verify/suite_no_new_failures.mjs
  EXPECT: UNLAZY-SUITE-NO-NEW-FAILURES
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=3e1b5f88d380ef9bf07c41200775484354b06e55e748e9ceba20142e9fc4ebdb; output-bytes=73

- [x] G7: The rebuilt DMG is notarized, stapled, and bundles the mlx-capable app.
  CHECK: node packaging/macos/verify/dmg_has_mlx.mjs
  EXPECT: UNLAZY-G7-PASS
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/Users/mickaelfarina/codec-wt-local; path=2cbcf16e1311/44 entries; EXPECT=matched; output-sha256=a0d8eb69aa26d9bffb4b9db35037257a83ac7c7073ad3cb4d656fa6fd3af47ac; output-bytes=15

<!--
G3 must use a model already on the build machine (a small one) and a port the
PM2 fleet does not own; the pass must come from the BUNDLED python, asserted by
the process's executable path, not by any server that happens to be up.
G1 negative control: run against the pre-fix requirements — must fail.
Toolchain: macOS arm64 only, by nature. Shell: /bin/bash.
-->
