// G7: the installer pre-seeds a provider in the EXACT shape codec_setup reads,
// and the app re-verifies rather than trusting it.
//
// Two surfaces writing one setting is the operator's accepted trade-off
// (2026-09-04). This gate is what stops them drifting: the key names and the
// allowed values are asserted on both sides.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
// verify/ -> macos/ -> packaging/ -> repo root
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const setup = readFileSync(join(repo, "codec_setup.py"), "utf8");
const fail = [];

// The app's contract.
for (const k of ["llm_provider_mode", "llm_base_url", "llm_model", "llm_verified_at"])
  if (!setup.includes(k)) fail.push(`codec_setup no longer uses ${k}`);
for (const m of ["local", "ava", "custom"])
  if (!new RegExp(`"${m}"`).test(setup)) fail.push(`provider "${m}" missing from codec_setup`);

// The app must not treat the installer's word as proof.
if (!/cfg\["llm_verified_at"\] = ""/.test(setup))
  fail.push("a provider choice does not reset verification — a pre-seeded choice would read as connected");

// The installer side, when present.
const sv = join(homedir(), "ava-stack/installer-gui/CODECInstaller/Sources/CODECInstaller/SetupView.swift");
if (existsSync(sv)) {
  // Strip // comments first: the previous version flagged the comment that
  // EXPLAINS why these keys must not be written. A checker that fails on its
  // own documentation is worse than no checker.
  const v = readFileSync(sv, "utf8").split("\n").filter(l => !l.trim().startsWith("//")).join("\n");
  if (!/llm_provider_mode/.test(v))
    fail.push("installer does not write llm_provider_mode — the app cannot read its choice");
  if (/llm_api_key/.test(v))
    fail.push("installer writes an API key into config.json — keys belong in the Keychain");
  if (/llm_verified_at/.test(v))
    fail.push("installer stamps llm_verified_at — only a real reply may do that");
}
if (fail.length) { console.error("G7 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G7-PASS");
