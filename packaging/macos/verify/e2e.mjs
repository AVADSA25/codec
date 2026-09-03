// G6: the BUNDLE must actually serve the dashboard.
//
// Every previous "success" in this saga was a log line. This gate starts the
// dashboard from the bundled interpreter and bundled sources, on a port the
// operator's PM2 fleet does not own, and requires a real HTTP response. It
// cannot be satisfied by the existing :8090 dashboard — the port is asserted
// free first, so a pass means THIS bundle served the request.
import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createConnection } from "node:net";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));
const repo = dirname(dirname(pkg));
const app = join(repo, "dist", "Sovereign AI Workstation.app");
const py = join(app, "Contents/Resources/python/bin/python3");
const appDir = join(app, "Contents/Resources/app");
const PORT = 8099;   // deliberately not 8090 (PM2 dashboard) or 8083 (model)

const listening = (port) => new Promise((res) => {
  const s = createConnection({ host: "127.0.0.1", port }, () => { s.end(); res(true); });
  s.on("error", () => res(false));
  setTimeout(() => { s.destroy(); res(false); }, 1500);
});

const fail = [];
let child;
try {
  if (!existsSync(py)) throw new Error(`bundled interpreter missing: ${py}`);
  if (!existsSync(appDir)) throw new Error(`bundled sources missing: ${appDir}`);

  // Guard against a false pass from something already on the port.
  if (await listening(PORT)) throw new Error(`port ${PORT} already in use — cannot prove the bundle served it`);

  child = spawn(py, ["-B", "-m", "uvicorn", "codec_dashboard:app", "--host", "127.0.0.1", "--port", String(PORT)],
                { cwd: appDir, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" }, stdio: ["ignore", "pipe", "pipe"] });
  let stderr = "";
  child.stderr.on("data", (d) => { stderr += d.toString(); });
  child.stdout.on("data", (d) => { stderr += d.toString(); });

  const deadline = Date.now() + 90000;
  let up = false;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) break;
    if (await listening(PORT)) { up = true; break; }
    await new Promise(r => setTimeout(r, 1000));
  }
  if (!up) {
    const why = stderr.split("\n").filter(l => /Error|error|Traceback/.test(l)).slice(-3).join(" | ")
                || `no listener after 90s (exit ${child.exitCode})`;
    throw new Error(`the bundled dashboard never served: ${why}`);
  }

  const code = execFileSync("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}",
                                     "-m", "20", `http://127.0.0.1:${PORT}/`], { encoding: "utf8" }).trim();
  if (!/^(200|30[12]|401|403)$/.test(code))
    fail.push(`bundled dashboard answered HTTP ${code} — not a working response`);
  else
    console.log(`  bundled stack served HTTP ${code} on :${PORT}`);
} catch (e) {
  fail.push(e.message);
} finally {
  if (child && child.exitCode === null) { try { child.kill("SIGKILL"); } catch {} }
}

if (fail.length) { console.error("G6 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G6-PASS");
