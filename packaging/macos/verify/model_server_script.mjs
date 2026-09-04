// G2: the launcher script ships in the bundle and resolves to the BUNDLED
// interpreter when no developer venv exists. Exercised by running it with a
// fake HOME (no ~/codec-qwen38-venv) and a dry `python -c` stand-in via
// CODEC_MODEL_VENV unset, capturing the "python=" line it logs.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const app = join(repo, "dist/Sovereign AI Workstation.app");
const script = join(app, "Contents/Resources/app/scripts/start_model_server.sh");
const fail = [];
if (!existsSync(script)) fail.push("start_model_server.sh is not in the bundle — the qwen3.6 service has nothing to run");
else {
  const fakeHome = mkdtempSync(join(tmpdir(), "nohome-"));
  // Replace the final exec with an echo so we see which interpreter it chose
  // without starting a 4 GB model server. The probe copy must live in the SAME
  // directory as the script: it resolves the bundled interpreter relative to
  // its own location, so a copy in /tmp would look for /tmp/../../python and
  // fall through to the system python — which is what the first version of
  // this gate did, and it blamed the script for the gate's own mistake.
  const probePath = join(dirname(script), ".sms_probe.sh");
  const probe = `sed 's|^exec "\\$PY" .*|echo "CHOSEN=$PY"|' ${JSON.stringify(script)} > ${JSON.stringify(probePath)} && bash ${JSON.stringify(probePath)}; rm -f ${JSON.stringify(probePath)}`;
  let out = "";
  try { out = execFileSync("/bin/bash", ["-c", probe], { encoding: "utf8", env: { HOME: fakeHome, PATH: "/usr/bin:/bin", CODEC_CONFIG: "/nonexistent" }, stdio: ["ignore", "pipe", "pipe"] }); }
  catch (e) { out = (e.stdout || "") + (e.stderr || ""); }
  const m = out.match(/CHOSEN=(.+)/);
  if (!m) fail.push("could not determine the chosen interpreter: " + out.slice(0, 200));
  // Resolve before comparing: the script reaches the interpreter via
  // scripts/../../python, which is correct and does not contain the literal
  // "Resources/python" until normalised. The first version of this gate failed
  // the right answer on a string-match technicality.
  else if (!resolve(m[1].trim()).endsWith("/Contents/Resources/python/bin/python3")) fail.push(`with no dev venv, chose ${m[1]} instead of the bundled interpreter`);
}
if (fail.length) { console.error("G2 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G2-PASS");
