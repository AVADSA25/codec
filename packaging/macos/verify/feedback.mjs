// G5: launching must never be silent.
// "when I click it nothing happened It just nothing happened." The launcher
// started the fleet and exited: no window, no menu-bar item, no notification.
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(pkg, "launcher/codec_launcher.swift");
const fail = [];
if (!existsSync(src)) { console.error("G5 FAILED: no launcher source"); process.exit(1); }
const s = readFileSync(src, "utf8");

if (!/NSStatusBar\.system\.statusItem/.test(s)) fail.push("no menu-bar item — the app is invisible once started");
if (!/NSAlert\(\)/.test(s)) fail.push("failures are not surfaced to the user");
if (!/setActivationPolicy\(\.accessory\)/.test(s)) fail.push("activation policy not set — presence is undefined");
if (!/Open CODEC Dashboard/.test(s)) fail.push("no route to the dashboard, so a running agent has nowhere to go");
// A failure must name a cause, not just say something went wrong.
if (!/terminationStatus != 0/.test(s)) fail.push("child exit status is never checked");
if (!/Show Logs/.test(s)) fail.push("no way to reach the logs from the failure alert");
if (!/openDashboard\(\)/.test(s.split("guard !outcome.ok else {")[1] || "")) fail.push("a successful start opens nothing");
// LSUIElement in the SHIPPED plist: runtime setActivationPolicy alone left the
// app in the Dock with no status item when launched via `open`.
const plist = join(dirname(pkg), "macos/Info.plist");
if (!/<key>LSUIElement<\/key>\s*<true\/>/.test(readFileSync(plist, "utf8"))) fail.push("Info.plist lacks LSUIElement=true");

if (fail.length) { console.error("G5 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G5-PASS");
