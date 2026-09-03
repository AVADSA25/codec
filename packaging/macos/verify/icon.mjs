// G2: the bundle must carry the icon its Info.plist names.
import { execFileSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(dirname(pkg));
const app = join(repo, "dist", "Sovereign AI Workstation.app");
const fail = [];

const src = join(pkg, "AppIcon.icns");
if (!existsSync(src)) fail.push("packaging/macos/AppIcon.icns missing — build would ship a blank tile");
else if (statSync(src).size < 10000) fail.push("AppIcon.icns is truncated");

if (!existsSync(app)) fail.push("app not built yet");
else {
  let declared = "";
  try {
    declared = execFileSync("/usr/libexec/PlistBuddy",
      ["-c", "Print :CFBundleIconFile", join(app, "Contents/Info.plist")], { encoding: "utf8" }).trim();
  } catch { fail.push("Info.plist declares no CFBundleIconFile"); }
  if (declared) {
    const f = join(app, "Contents/Resources", declared.endsWith(".icns") ? declared : declared + ".icns");
    if (!existsSync(f)) fail.push(`Info.plist names "${declared}" but it is NOT in the bundle`);
  }
}
if (fail.length) { console.error("G2 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G2-PASS");
