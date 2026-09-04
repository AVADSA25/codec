# Gates: "Use the local model" works on a buyer's Mac

OWNS: requirements.txt, packaging/macos/**, codec_setup.py, routes/setup.py,
      codec_dashboard.html, scripts/start_model_server.sh, tests/test_local_model_bundle.py, GATES.md

Scope: the connect screen offered a local model that could not exist on a buyer's
Mac. Three independent reasons, each fatal: the bundled Python has no mlx_vlm;
scripts/start_model_server.sh is not in the bundle and hardcodes the developer's
venv; and first_run.py invokes fetch_models WITHOUT --yes, so the "bundled" LLM is
a dry run — never downloaded. Operator decision 2026-09-04: accept the bundle
growing from ~273 MB to ~1.2 GB for the best first-run experience.

- [ ] G1: The bundled interpreter can serve MLX models — `import mlx_vlm` succeeds inside the bundle, and the dependency is declared with a darwin/arm64 marker so Linux CI does not try to install it.
  CHECK: node packaging/macos/verify/mlx_bundle.mjs
  EXPECT: UNLAZY-G1-PASS
  EVIDENCE: pending

- [ ] G2: The model-server launcher ships in the bundle and, with no developer venv present, resolves to the BUNDLED interpreter.
  CHECK: node packaging/macos/verify/model_server_script.mjs
  EXPECT: UNLAZY-G2-PASS
  EVIDENCE: pending

- [ ] G3: The bundled stack actually serves a model — started from the bundle on a port PM2 does not own, it answers /v1/models and a chat completion.
  CHECK: node packaging/macos/verify/model_server_e2e.mjs
  EXPECT: UNLAZY-G3-PASS
  EVIDENCE: pending

- [ ] G4: The connect screen can download the bundled model with visible progress, refuses a duplicate download, and reports failure honestly.
  CHECK: python3 -m pytest tests/test_local_model_bundle.py -q -s -k download
  EXPECT: UNLAZY-G4-PASS
  EVIDENCE: pending

- [ ] G5: A downloaded model is discovered by the connect screen and becomes selectable as `local` without a restart of anything the user can see.
  CHECK: python3 -m pytest tests/test_local_model_bundle.py -q -s -k discovered_after_download
  EXPECT: UNLAZY-G5-PASS
  EVIDENCE: pending

- [ ] G6: No NEW test failures beyond the documented pre-existing set.
  CHECK: node packaging/macos/verify/suite_no_new_failures.mjs
  EXPECT: UNLAZY-G6-PASS
  EVIDENCE: pending

- [ ] G7: The rebuilt DMG is notarized, stapled, and bundles the mlx-capable app.
  CHECK: node packaging/macos/verify/dmg_has_mlx.mjs
  EXPECT: UNLAZY-G7-PASS
  EVIDENCE: pending

<!--
G3 must use a model already on the build machine (a small one) and a port the
PM2 fleet does not own; the pass must come from the BUNDLED python, asserted by
the process's executable path, not by any server that happens to be up.
G1 negative control: run against the pre-fix requirements — must fail.
Toolchain: macOS arm64 only, by nature. Shell: /bin/bash.
-->
