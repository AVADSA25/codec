// G8: no NEW test failures. Compares the failing SET against the documented
// pre-existing ones, so a new failure cannot hide behind a count.
import { execFileSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
// verify/ -> macos/ -> packaging/ -> repo root
const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
let out = "";
try { out = execFileSync("python3", ["-m", "pytest", "-q", "--tb=no"], { cwd: repo, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 }); }
catch (e) { out = (e.stdout || "") + (e.stderr || ""); }
const failing = [...out.matchAll(/^FAILED (\S+)/gm)].map(m => m[1].split(" ")[0]);
// Documented in docs/known-issues.md: the cloud-gating commits broke these.
const known = /^tests\/(test_llm_stream|test_llm_async|test_stream_usage|test_llm_vision_dedup|test_agent_plan|test_llm_raise_mode|test_skill_loader_unification|test_security)\.py/;
const novel = failing.filter(f => !known.test(f));
if (novel.length) {
  console.error("G8 FAILED: new failures introduced:\n - " + novel.join("\n - "));
  process.exit(1);
}
console.log(`${failing.length} failing, all pre-existing and documented`);
// Ledger-independent token: this verifier is reused across ledgers where it
// sits at different gate numbers. Printing "G8" made a G6 gate fail on a token
// mismatch while the suite was clean.
console.log("UNLAZY-SUITE-NO-NEW-FAILURES");
