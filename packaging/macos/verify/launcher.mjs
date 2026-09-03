// G3: CFBundleExecutable must be Mach-O, and the SIGNED app must carry the
// microphone entitlement.
//
// The defect: a /bin/sh main executable. codesign --entitlements against a
// script-headed bundle is accepted and silently ignored, leaving hardened
// runtime enforced with zero entitlements — a voice product that can never be
// granted a mic, with no error anywhere. Asserts the binary format AND the
// signed result, because either alone is satisfiable while still broken.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(dirname(pkg));
const app = join(repo, "dist", "Sovereign AI Workstation.app");
const AUDIO = "com.apple.security.device.audio-input";
const fail = [];

// Source of truth: the entitlement must be declared, and the launcher must exist.
if (!existsSync(join(pkg, "launcher/codec_launcher.swift")))
  fail.push("no Swift launcher source — CFBundleExecutable would be a script again");
const ents = existsSync(join(pkg, "codec.entitlements"))
  ? readFileSync(join(pkg, "codec.entitlements"), "utf8") : "";
if (!ents.includes(AUDIO)) fail.push(`codec.entitlements does not declare ${AUDIO}`);

if (!existsSync(app)) {
  fail.push("app not built yet");
} else {
  const exe = join(app, "Contents/MacOS/codec");
  const kind = execFileSync("file", [exe], { encoding: "utf8" });
  if (!/Mach-O/.test(kind))
    fail.push(`main executable is NOT Mach-O (${kind.split(":")[1]?.trim()}) — entitlements are ignored`);

  // What actually shipped. spawnSync: codesign writes to stderr on success.
  const r = spawnSync("codesign", ["-d", "--entitlements", "-", "--xml", app], { encoding: "utf8" });
  const dumped = (r.stdout || "") + (r.stderr || "");
  if (!dumped.includes(AUDIO))
    fail.push(`the SIGNED app does not carry ${AUDIO} — its microphone will fail silently`);
}
if (fail.length) { console.error("G3 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G3-PASS");
