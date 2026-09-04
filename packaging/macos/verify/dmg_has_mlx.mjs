// G7: the DMG is notarized+stapled and its bundled app can import mlx_vlm.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
const dmg = `${homedir()}/ava-stack/installer-gui/dist/CODEC-Installer.dmg`;
const sh = (c) => execFileSync("/bin/bash", ["-c", c], { encoding: "utf8" });
const fail = []; let mount = "";
try {
  if (!existsSync(dmg)) throw new Error("no DMG");
  const st = spawnSync("xcrun", ["stapler", "validate", dmg], { encoding: "utf8" });
  if (!/The validate action worked/.test((st.stdout || "") + (st.stderr || ""))) fail.push("DMG is not stapled");
  const sp = spawnSync("spctl", ["-a", "-t", "open", "--context", "context:primary-signature", "-vv", dmg], { encoding: "utf8" });
  if (!/accepted/.test((sp.stdout || "") + (sp.stderr || ""))) fail.push("Gatekeeper rejects the DMG");
  mount = (sh(`hdiutil attach ${JSON.stringify(dmg)} -nobrowse -readonly`).match(/\/Volumes\/[^\n]*/) || [""])[0].trim();
  const py = `${mount}/Sovereign AI Workstation.app/Contents/Resources/python/bin/python3`;
  if (!existsSync(py)) fail.push("bundled app has no interpreter");
  else { const r = spawnSync(py, ["-B", "-c", "import mlx_vlm"], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }); if (r.status !== 0) fail.push("app in the DMG cannot import mlx_vlm"); }
  if (!existsSync(`${mount}/Sovereign AI Workstation.app/Contents/Resources/app/scripts/start_model_server.sh`)) fail.push("start_model_server.sh missing from the shipped app");
} catch (e) { fail.push(e.message); }
finally { if (mount) { try { sh(`hdiutil detach ${JSON.stringify(mount)} -quiet`); } catch {} } }
if (fail.length) { console.error("G7 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G7-PASS");
