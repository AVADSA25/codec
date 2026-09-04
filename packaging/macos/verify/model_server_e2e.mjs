// G3: the bundled stack serves a real model. Uses a small model already on the
// build machine and a port PM2 does not own; asserts the serving process is the
// BUNDLED python by path, so the developer's server cannot answer for it.
import { spawn, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createConnection } from "node:net";
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const py = join(repo, "dist/Sovereign AI Workstation.app/Contents/Resources/python/bin/python3");
const MODEL = "mlx-community/Qwen3-4B-4bit";
const PORT = 8098;
const listening = (port) => new Promise((res) => { const s = createConnection({ host: "127.0.0.1", port }, () => { s.end(); res(true); }); s.on("error", () => res(false)); setTimeout(() => { s.destroy(); res(false); }, 1500); });
const fail = []; let child;
try {
  if (!existsSync(py)) throw new Error("app not built");
  if (!existsSync(join(homedir(), ".cache/huggingface/hub/models--mlx-community--Qwen3-4B-4bit"))) throw new Error(`${MODEL} not on this machine — cannot run the e2e`);
  if (await listening(PORT)) throw new Error(`port ${PORT} busy — cannot prove the bundle served it`);
  child = spawn(py, ["-B", "-m", "mlx_vlm.server", "--model", MODEL, "--port", String(PORT), "--host", "127.0.0.1"],
                { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", HF_HUB_OFFLINE: "1" }, stdio: ["ignore", "pipe", "pipe"] });
  let log = ""; child.stdout.on("data", d => log += d); child.stderr.on("data", d => log += d);
  const deadline = Date.now() + 240000; let up = false;
  while (Date.now() < deadline) { if (child.exitCode !== null) break; if (await listening(PORT)) { up = true; break; } await new Promise(r => setTimeout(r, 2000)); }
  if (!up) throw new Error("bundled model server never listened: " + log.split("\n").filter(l => /rror|Traceback/.test(l)).slice(-3).join(" | "));
  const who = execFileSync("ps", ["-o", "command=", "-p", String(child.pid)], { encoding: "utf8" });
  if (!who.includes("/Contents/Resources/python/bin/python3")) throw new Error("serving process is not the bundled interpreter: " + who.trim().slice(0, 120));
  const body = JSON.stringify({ model: MODEL, messages: [{ role: "user", content: "Reply with the single word READY" }], max_tokens: 8 });
  const reply = execFileSync("curl", ["-s", "-m", "120", "-X", "POST", `http://127.0.0.1:${PORT}/v1/chat/completions`, "-H", "Content-Type: application/json", "-d", body], { encoding: "utf8" });
  const j = JSON.parse(reply); const text = j?.choices?.[0]?.message?.content ?? "";
  if (!text) throw new Error("no completion from the bundled server: " + reply.slice(0, 200));
  console.log(`  bundled server answered: ${text.trim().slice(0, 40)}`);
} catch (e) { fail.push(e.message); }
finally { if (child && child.exitCode === null) { try { child.kill("SIGKILL"); } catch {} } }
if (fail.length) { console.error("G3 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G3-PASS");
