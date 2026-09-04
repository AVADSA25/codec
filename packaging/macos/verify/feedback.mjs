// G5: launching must never be silent.
// "when I click it nothing happened It just nothing happened." The launcher
// started the fleet and exited: no window, no menu-bar item, no notification.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const src = join(pkg, "launcher/codec_launcher.swift");
const fail = [];
if (!existsSync(src)) { console.error("G5 FAILED: no launcher source"); process.exit(1); }
// The launcher is multi-file now: declarations in codec_launcher.swift, the
// bootstrap (and activation policy) in main.swift, the window in
// DashboardWindow.swift. Read them all, or a check on one file goes stale the
// moment code moves — which is exactly what happened here.
const s = readdirSync(join(pkg, "launcher")).filter(f => f.endsWith(".swift"))
  .map(f => readFileSync(join(pkg, "launcher", f), "utf8")).join("\n");

if (!/NSStatusBar\.system\.statusItem/.test(s)) fail.push("no menu-bar item — the app is invisible once started");
if (!/NSAlert\(\)/.test(s)) fail.push("failures are not surfaced to the user");
if (!/setActivationPolicy\(\.regular\)/.test(s)) fail.push("activation policy is not .regular — a windowed app belongs in the Dock and ⌘-Tab");
if (!/Open CODEC Dashboard/.test(s)) fail.push("no route to the dashboard, so a running agent has nowhere to go");
// A failure must name a cause, not just say something went wrong.
if (!/terminationStatus != 0/.test(s)) fail.push("child exit status is never checked");
if (!/Show Logs/.test(s)) fail.push("no way to reach the logs from the failure alert");
if (!/openDashboard\(\)/.test(s.split("guard !outcome.ok else {")[1] || "")) fail.push("a successful start opens nothing");
// The app owns a real window now, so it must NOT be LSUIElement (that hides it
// from the Dock and app switcher) and must declare the localhost ATS exemption,
// without which the WKWebView renders a blank window over plain-http loopback.
const plist = readFileSync(join(dirname(pkg), "macos/Info.plist"), "utf8");
if (/<key>LSUIElement<\/key>\s*<true\/>/.test(plist))
  fail.push("LSUIElement=true hides a windowed app from the Dock");
if (!/NSAllowsLocalNetworking/.test(plist))
  fail.push("no ATS localhost exemption — the dashboard window would be blank");
// The dashboard must render IN the app, not be handed to a browser.
const win = join(pkg, "launcher/DashboardWindow.swift");
if (!existsSync(win)) fail.push("no DashboardWindow.swift — the UI would still be a browser tab");
else {
  const w = readFileSync(win, "utf8");
  if (!/WKWebView/.test(w)) fail.push("dashboard window does not embed a web view");
  if (!/decidePolicyFor/.test(w)) fail.push("no navigation policy — the window would act as an open browser");
}
if (/NSWorkspace\.shared\.open\(URL\(string: "http:\/\/127\.0\.0\.1:8090/.test(s))
  fail.push("still opens the dashboard in an external browser");

if (fail.length) { console.error("G5 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G5-PASS");
