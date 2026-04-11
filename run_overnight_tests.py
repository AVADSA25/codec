#!/usr/bin/env python3
"""Overnight agent crew test runner — fires all 7 untested crews sequentially."""
import requests, json, time, os
from datetime import datetime

DASHBOARD = "http://127.0.0.1:8090"
HEADERS = {"Content-Type": "application/json", "x-internal": "codec"}
RESULTS_PATH = os.path.expanduser("~/.codec/overnight_test_results.json")

TESTS = [
    {
        "id": "AG-1",
        "crew": "trip_planner",
        "payload": {
            "crew": "trip_planner",
            "topic": "Plan a 3-day weekend trip to Lisbon for one person, mid-budget, focused on food and architecture.",
            "destination": "Lisbon",
            "dates": "3-day weekend",
        },
    },
    {
        "id": "AG-2",
        "crew": "email_handler",
        "payload": {
            "crew": "email_handler",
            "topic": "Triage my inbox and draft replies to the 3 most recent unread emails.",
        },
    },
    {
        "id": "AG-3",
        "crew": "social_media",
        "payload": {
            "crew": "social_media",
            "topic": "CODEC just launched open source — write a Twitter, LinkedIn, and Instagram post announcing it.",
        },
    },
    {
        "id": "AG-4",
        "crew": "code_review",
        "payload": {
            "crew": "code_review",
            "topic": "Review this Python function for bugs: def divide(a,b): return a/b",
            "code": "def divide(a,b): return a/b",
        },
    },
    {
        "id": "AG-5",
        "crew": "meeting_summarizer",
        "payload": {
            "crew": "meeting_summarizer",
            "topic": "Summarize this transcript: 'John said we need to launch by Friday. Sarah disagreed, said testing needs 2 more days. We agreed on Monday launch with Sarah leading QA.'",
            "meeting_input": "John said we need to launch by Friday. Sarah disagreed, said testing needs 2 more days. We agreed on Monday launch with Sarah leading QA.",
        },
    },
    {
        "id": "AG-6",
        "crew": "invoice_generator",
        "payload": {
            "crew": "invoice_generator",
            "topic": "Generate an invoice for AVA Digital LLC, client Karl Schmidt, Germany. Service: AI setup consultation, 10 hours at 150 EUR/hour. Due in 30 days.",
            "invoice_details": "Seller: AVA Digital LLC. Client: Karl Schmidt, Germany. Service: AI setup consultation, 10 hours at 150 EUR/hour. Total: 1500 EUR. Payment due in 30 days.",
        },
    },
    {
        "id": "AG-7",
        "crew": "competitor_analysis",
        "payload": {
            "crew": "competitor_analysis",
            "topic": "Analyze CODEC's top 3 competitors in the open-source local AI assistant space.",
        },
    },
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def run_test(test):
    test_id = test["id"]
    crew = test["crew"]
    log(f"{'='*60}")
    log(f"STARTING {test_id}: {crew}")
    log(f"{'='*60}")

    result = {
        "id": test_id,
        "crew": crew,
        "started": datetime.now().isoformat(),
        "status": "unknown",
        "result": "",
        "doc_url": None,
        "error": None,
        "elapsed_sec": 0,
    }

    start = time.time()
    try:
        # Start the crew
        r = requests.post(
            f"{DASHBOARD}/api/agents/run",
            json=test["payload"],
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code != 200:
            result["status"] = "error"
            result["error"] = f"HTTP {r.status_code}: {r.text[:200]}"
            log(f"  FAILED to start: {result['error']}")
            return result

        data = r.json()
        job_id = data.get("job_id")
        if not job_id:
            result["status"] = "error"
            result["error"] = "No job_id returned"
            log(f"  FAILED: no job_id")
            return result

        log(f"  Job started: {job_id}")

        # Poll for up to 15 minutes
        for i in range(180):
            time.sleep(5)
            try:
                sr = requests.get(
                    f"{DASHBOARD}/api/agents/status/{job_id}",
                    headers=HEADERS,
                    timeout=10,
                )
                if sr.status_code == 200:
                    job_data = sr.json()
                    st = job_data.get("status")
                    if st not in ("running", "pending"):
                        elapsed = int(time.time() - start)
                        result_text = job_data.get("result", "")
                        if isinstance(result_text, dict):
                            result_text = result_text.get("result", str(result_text))

                        result["status"] = st
                        result["result"] = str(result_text)[:3000]
                        result["elapsed_sec"] = elapsed

                        # Check for Google Doc URL
                        import re
                        doc_match = re.search(r'(https://docs\.google\.com/document/d/[^\s]+)', str(result_text))
                        if doc_match:
                            result["doc_url"] = doc_match.group(1)

                        status_icon = "✅" if st == "complete" else "❌"
                        log(f"  {status_icon} Finished: {st} ({elapsed}s)")
                        if result["doc_url"]:
                            log(f"  📄 Google Doc: {result['doc_url']}")
                        log(f"  Preview: {str(result_text)[:200]}")
                        return result

                if i % 12 == 0 and i > 0:
                    log(f"  Still running... ({i*5}s)")

            except Exception as e:
                log(f"  Poll error: {e}")

        # Timeout
        result["status"] = "timeout"
        result["error"] = "Exceeded 15 minute timeout"
        result["elapsed_sec"] = int(time.time() - start)
        log(f"  ⏰ TIMEOUT after 15 minutes")

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["elapsed_sec"] = int(time.time() - start)
        log(f"  ❌ ERROR: {e}")

    return result


def main():
    log("=" * 60)
    log("CODEC OVERNIGHT AGENT CREW TESTS")
    log(f"Running {len(TESTS)} tests sequentially")
    log("=" * 60)

    all_results = []

    for test in TESTS:
        result = run_test(test)
        result["finished"] = datetime.now().isoformat()
        all_results.append(result)

        # Save after each test (in case of crash)
        with open(RESULTS_PATH, "w") as f:
            json.dump(all_results, f, indent=2)

        # Brief pause between tests
        log(f"  Waiting 10s before next test...")
        time.sleep(10)

    # Summary
    log("")
    log("=" * 60)
    log("OVERNIGHT TEST SUMMARY")
    log("=" * 60)
    passed = sum(1 for r in all_results if r["status"] == "complete")
    failed = sum(1 for r in all_results if r["status"] != "complete")
    log(f"  PASSED: {passed}/{len(all_results)}")
    log(f"  FAILED: {failed}/{len(all_results)}")
    for r in all_results:
        icon = "✅" if r["status"] == "complete" else "❌"
        doc = f" 📄" if r.get("doc_url") else ""
        log(f"  {icon} {r['id']} ({r['crew']}): {r['status']} — {r['elapsed_sec']}s{doc}")

    # Save final
    with open(RESULTS_PATH, "w") as f:
        json.dump(all_results, f, indent=2)
    log(f"\nFull results saved to: {RESULTS_PATH}")
    log("Done. Goodnight! 🌙")


if __name__ == "__main__":
    main()
