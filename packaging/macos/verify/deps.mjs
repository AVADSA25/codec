// G1: every startup import must be a declared dep AND present in the BUNDLE.
// Imports are resolved inside the bundled interpreter, not the dev one — the
// whole defect was that the build machine had fastapi and the bundle did not.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const pkg = dirname(dirname(fileURLToPath(import.meta.url)));   // packaging/macos
const repo = dirname(dirname(pkg));
const app = join(repo, "dist", "Sovereign AI Workstation.app");
const py = join(app, "Contents/Resources/python/bin/python3");
const fail = [];

// Declared, not merely mentioned inside a comment — that is exactly how fastapi
// hid for months.
const reqs = readFileSync(join(repo, "requirements.txt"), "utf8")
  .split("\n").filter(l => l.trim() && !l.trim().startsWith("#"))
  .map(l => l.split(/[<>=\[;]/)[0].trim().toLowerCase());
for (const need of ["fastapi", "uvicorn"])
  if (!reqs.includes(need)) fail.push(`${need} is not a DECLARED dependency (a comment does not count)`);

if (!existsSync(py)) {
  fail.push(`bundled interpreter missing at ${py} — build the app first`);
} else {
  // Import what the app imports at startup, in the bundle's own interpreter.
  const modules = ["fastapi", "uvicorn", "requests", "pydantic"];
  for (const m of modules) {
    // -B and PYTHONDONTWRITEBYTECODE: importing inside the bundle would write
    // __pycache__/*.pyc into it and BREAK THE CODE SIGNATURE ("a sealed
    // resource is missing or invalid"). A check that damages the artifact it
    // measures is worse than no check — this one did exactly that once.
    try {
      execFileSync(py, ["-B", "-c", `import ${m}`],
                   { stdio: "pipe", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
    }
    catch { fail.push(`bundled python cannot import ${m} — the app will die at startup`); }
  }
}

if (fail.length) { console.error("G1 FAILED:\n - " + fail.join("\n - ")); process.exit(1); }
console.log("UNLAZY-G1-PASS");
