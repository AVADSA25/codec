#!/usr/bin/env python3
"""Write the CI job summary from pytest's junit.xml and coverage.xml (D-33).

    python tools/ci_report_summary.py --label "ubuntu" junit.xml coverage.xml

Prints a short Markdown report (test counts, overall and routes/ line
coverage, skipped tests by reason) and appends it to $GITHUB_STEP_SUMMARY
when that is set. A missing or unreadable file is reported, not fatal, so the
summary still shows up after a failed test run. CI-only; nothing at runtime
reads it.
"""
import argparse
import collections
import os
import sys
import xml.etree.ElementTree as ET


def junit_lines(path: str) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        return [f"- Tests: no readable {path} ({type(e).__name__})"]
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    total = failures = errors = skipped = 0
    reasons: collections.Counter = collections.Counter()
    for s in suites:
        total += int(s.get("tests", 0))
        failures += int(s.get("failures", 0))
        errors += int(s.get("errors", 0))
        skipped += int(s.get("skipped", 0))
        for case in s.iter("testcase"):
            sk = case.find("skipped")
            if sk is not None:
                reason = (sk.get("message") or "no reason given").strip()
                reasons[reason[:160]] += 1
    passed = total - failures - errors - skipped
    out = [f"- Tests: {passed:,} passed, {failures:,} failed, {errors:,} errors, "
           f"{skipped:,} skipped ({total:,} collected)"]
    if reasons:
        out += ["", "| Skipped | Reason |", "|---:|---|"]
        for reason, n in reasons.most_common(15):
            out.append(f"| {n} | {reason.replace('|', '/')} |")
        if len(reasons) > 15:
            rest = sum(n for _, n in reasons.most_common()[15:])
            out.append(f"| {rest} | ({len(reasons) - 15} other reasons) |")
    return out


def coverage_lines(path: str) -> list[str]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        return [f"- Coverage: no readable {path} ({type(e).__name__})"]
    overall = float(root.get("line-rate", 0)) * 100
    valid = covered = 0
    for cls in root.iter("class"):
        if cls.get("filename", "").replace("\\", "/").startswith("routes/"):
            for line in cls.iter("line"):
                valid += 1
                covered += int(line.get("hits", 0)) > 0
    routes = f"{covered / valid * 100:.1f}%" if valid else "n/a"
    return [f"- Coverage: {overall:.1f}% of lines overall, routes/: {routes} "
            f"({covered:,} of {valid:,} lines)"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True, help="job name for the heading")
    ap.add_argument("junit")
    ap.add_argument("coverage")
    args = ap.parse_args(argv)
    text = "\n".join([f"### Tests: {args.label}", "",
                      *coverage_lines(args.coverage), *junit_lines(args.junit), ""])
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
