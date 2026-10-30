// The built .app must not carry GPL helper programs or libraries (audit P-033, P-011):
// SoX (rec, play, soxi, libsox), poppler (pdftotext, pdftoppm, pdfinfo, pdftocairo) and the
// GPL codecs PyAV bundles (libx264, libx265). The app records with sounddevice and reads
// PDFs with PDFKit instead. Usage: node no_gpl_helpers.mjs [path/to/App.app]
import { existsSync, readdirSync, lstatSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repo = dirname(dirname(dirname(dirname(fileURLToPath(import.meta.url)))));
const app = process.argv[2] || join(repo, "dist", "Sovereign AI Workstation.app");
// Programs match by exact file name (a folder such as numpy/rec is not one); libsoxr, the
// LGPL resampler, is not libsox.
const PROGRAM = /^(sox|soxi|rec|play|pdftotext|pdftoppm|pdfinfo|pdftocairo|ffmpeg|ffprobe)$/;
const LIBRARY = /^lib(sox|x264|x265|poppler)[.\d-]*(\.dylib|\.so|\.a)?$/;

if (!existsSync(app)) { console.error(`NO-GPL-HELPERS FAILED: ${app} does not exist (build the app first)`); process.exit(1); }
const found = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = lstatSync(p);
    if ((st.isFile() && PROGRAM.test(name)) || LIBRARY.test(name)) found.push(p);
    if (st.isDirectory()) walk(p);
  }
})(app);
if (found.length) { console.error("NO-GPL-HELPERS FAILED:\n - " + found.join("\n - ")); process.exit(1); }
console.log("NO-GPL-HELPERS-PASS");
