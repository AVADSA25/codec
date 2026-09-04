// G6: the connect screen exists, is gated on real connectivity, and is
// reachable again from Settings.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
// verify/ -> macos/ -> packaging/ -> repo root
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const s = readFileSync(join(repo, "codec_dashboard.html"), "utf8");
const fail = [];
if (!/id="setupOverlay"/.test(s)) fail.push("no connect overlay");
if (!/\/api\/setup\/status/.test(s)) fail.push("never asks whether a brain is connected");
if (!/\/api\/setup\/verify/.test(s)) fail.push("never verifies — it could report success without asking a model");
// Choosing must not equal connecting.
if (!/overlay\.hidden\s*=\s*!!d\.connected/.test(s)) fail.push("overlay visibility is not driven by verified connectivity");
for (const m of ["local", "ava", "custom"])
  if (!new RegExp(`data-mode="${m}"`).test(s)) fail.push(`no "${m}" option`);
if (!/local_available===false/.test(s)) fail.push("local option is offered even when no local server can answer");
if (!/openConnectSetup/.test(s)) fail.push("not reachable from Settings after first run");
if (!/Keychain, never on disk/.test(s)) fail.push("does not tell the user where their key goes");
if (fail.length) { console.error("G6 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G6-PASS");
