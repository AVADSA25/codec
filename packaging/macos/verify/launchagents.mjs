// G4: no LaunchAgent may reference a removable volume, and first_run must
// refuse to write one from an ephemeral location.
//
// All 14 plists were written pointing at /Volumes/CODEC Installer 1/... because
// the app ran from the mounted DMG. Every one exits 78 after eject. Exercises
// the real guard against three fixtures rather than grepping for it.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const fail = [];

// 1. The guard must refuse ephemeral locations and allow real ones.
const probe = (path) => {
  const code = `
import sys; sys.path.insert(0, ${JSON.stringify(pkg)})
import first_run
try:
    first_run.refuse_if_ephemeral(${JSON.stringify(path)}); print("ALLOWED")
except first_run.EphemeralLocationError: print("REFUSED")
`;
  return execFileSync("python3", ["-c", code], { encoding: "utf8" }).trim();
};
const cases = [
  ["/Volumes/CODEC Installer 1/Sovereign AI Workstation.app/Contents", "REFUSED", "mounted volume"],
  ["/private/var/folders/x/AppTranslocation/A/d/CODEC.app/Contents", "REFUSED", "translocated"],
  ["/Applications/Sovereign AI Workstation.app/Contents", "ALLOWED", "/Applications"],
];
for (const [path, want, label] of cases) {
  let got;
  try { got = probe(path); } catch (e) { got = "ERROR: " + (e.stderr || e.message).slice(0, 80); }
  if (got !== want) fail.push(`guard on ${label}: expected ${want}, got ${got}`);
}

// 2. Nothing already installed may point at a removable volume.
const agents = join(homedir(), "Library/LaunchAgents");
if (existsSync(agents)) {
  for (const f of readdirSync(agents).filter(f => /avadigital\.codec/i.test(f))) {
    const body = readFileSync(join(agents, f), "utf8");
    if (body.includes("/Volumes/")) fail.push(`${f} points at a removable volume — dies on eject`);
  }
}
if (fail.length) { console.error("G4 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G4-PASS");
