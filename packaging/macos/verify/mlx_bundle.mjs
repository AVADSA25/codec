// G1: the bundled interpreter can serve MLX, and the dependency is marker-gated.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const fail = [];
const req = readFileSync(join(repo, "requirements.txt"), "utf8");
const line = req.split("\n").find(l => /^mlx-vlm/.test(l.trim()));
if (!line) fail.push("mlx-vlm is not a declared dependency");
else if (!/sys_platform\s*==\s*"darwin"/.test(line) || !/platform_machine\s*==\s*"arm64"/.test(line))
  fail.push("mlx-vlm is not marker-gated to darwin/arm64 — Linux CI would fail to install it");
const py = join(repo, "dist/Sovereign AI Workstation.app/Contents/Resources/python/bin/python3");
if (!existsSync(py)) fail.push("app not built — no bundled interpreter to test");
else for (const m of ["mlx_vlm", "mlx", "mlx_lm", "huggingface_hub"]) {
  try { execFileSync(py, ["-B", "-c", `import ${m}`], { stdio: "pipe", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }); }
  catch { fail.push(`bundled python cannot import ${m}`); }
}
if (fail.length) { console.error("G1 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G1-PASS");
