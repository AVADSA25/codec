// G2: the launcher script ships in the bundle and resolves to the BUNDLED
// interpreter when no developer venv exists. Exercised by running it with a
// fake HOME (no ~/codec-qwen38-venv) and a dry `python -c` stand-in via
// CODEC_MODEL_VENV unset, capturing the "python=" line it logs.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const app = join(repo, "dist/Sovereign AI Workstation.app");
const script = join(app, "Contents/Resources/app/scripts/start_model_server.sh");
const fail = [];
if (!existsSync(script)) fail.push("start_model_server.sh is not in the bundle — the qwen3.6 service has nothing to run");
else {
  const fakeHome = mkdtempSync(join(tmpdir(), "nohome-"));
  // Replace the final exec with an echo so we see which interpreter it chose
  // without actually starting a 4 GB model server.
  const probe = `sed 's|^exec "\\$PY" .*|echo "CHOSEN=$PY"|' ${JSON.stringify(script)} > /tmp/sms_probe.sh && bash /tmp/sms_probe.sh`;
  let out = "";
  try { out = execFileSync("/bin/bash", ["-c", probe], { encoding: "utf8", env: { HOME: fakeHome, PATH: "/usr/bin:/bin", CODEC_CONFIG: "/nonexistent" }, stdio: ["ignore", "pipe", "pipe"] }); }
  catch (e) { out = (e.stdout || "") + (e.stderr || ""); }
  const m = out.match(/CHOSEN=(.+)/);
  if (!m) fail.push("could not determine the chosen interpreter: " + out.slice(0, 200));
  else if (!m[1].includes("/Contents/Resources/python/bin/python3")) fail.push(`with no dev venv, chose ${m[1]} instead of the bundled interpreter`);
}
if (fail.length) { console.error("G2 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G2-PASS");
