#!/usr/bin/env python3.13
"""Stress-test every MCP-exposed CODEC skill locally.

Usage: /usr/local/bin/python3.13 scripts/stress_test_skills.py [skill_name ...]

If skill names are given, only those are run (re-test after fix).
Otherwise: full sweep, write markdown report to ~/.codec/reports/stress_<UTC-DATE>.md.
"""
from __future__ import annotations
import os, sys, re, time, traceback, importlib, importlib.util, json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone
from pathlib import Path

REPO = Path('/Users/mickaelfarina/codec-repo')
SKILLS_DIR = REPO / 'skills'
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(SKILLS_DIR))

TIMEOUT_S = 15
# Per-skill timeout overrides (skills that legitimately hit slow LLM/vision backends)
TIMEOUT_OVERRIDE = {
    'fact_extract':    90,   # Qwen 35B extraction
    'screenshot_text': 60,   # Qwen2.5-VL on full-screen PNG
}

# Skills that have real side-effects — don't run in automated sweep.
MANUAL_ONLY = {
    'mouse_control':  'moves physical mouse',
    'ax_control':     'controls UI via accessibility APIs',
    'file_ops':       'can delete/modify files',
    'imessage_send':  'would send real iMessage',
    'philips_hue':    'would toggle physical lights',
    'app_switch':     'would switch frontmost app',
    'chrome_close':   'would kill Chrome',
    'tts_say':        'would speak out loud',
    'create_skill':   'generative, heavy LLM call',
    'skill_forge':    'generative, heavy LLM call (not exposed)',
    'ai_news_digest': 'heavy LLM call, long runtime',
    'auto_memorize':  'forced run triggers LLM memory write',
    'lucy':           'heavy LLM agent loop',
    'codec':          'main dispatcher, heavy',
    'self_improve':   'writes to skills, heavy LLM',
    'chrome_automate':'launches & drives browser',
    'chrome_click_cdp':'drives browser via CDP',
    'chrome_fill':    'types into live browser page',
    'chrome_scroll':  'scrolls live browser page',
    'chrome_open':    'opens Chrome tab',
    'chrome_extract': 'requires live browser page',
    'chrome_read':    'requires live browser page',
    'python_exec':    'executes arbitrary python',
    'terminal':       'executes shell commands',
    'process_manager':'can kill processes',
    'pm2_control':    'can kill services',
    'file_search':    'heavy disk scan',
    'chrome_search':  'launches browser search',
    'chrome_tabs':    'requires live browser',
}

# Safe representative inputs
TASKS = {
    'web_fetch':       'fetch https://example.com',
    'web_search':      'python programming',
    'weather':         'weather in Marbella',
    'bitcoin_price':   '',
    'calculator':      '2+2',
    'time_date':       '',
    'system_info':     '',
    'network_info':    '',
    'clipboard':       'read',
    'translate':       'hello to spanish',
    'password_generator': '16',
    'qr_generator':    'https://example.com',
    'json_formatter':  '{"a":1}',
    'screenshot_text': '',
    'active_window':   '',
    'notes':           'list',
    'reminders':       'list',
    'google_calendar': 'list',
    'google_drive':    'list',
    'google_docs':     'list',
    'google_sheets':   'list',
    'google_slides':   'list',
    'google_gmail':    'list',
    'google_keep':     'list',
    'google_tasks':    'list',
    'memory_search':   'test',
    'memory_history':  '',
    'memory_entities': '',
    'memory_save':     'stress test memory save at ' + datetime.now(timezone.utc).isoformat(),
    'audit_report':    '',
    'fact_extract':    'Mickael lives in Marbella.',
    'music':           'status',
    'volume':          'get',
    'brightness':      'get',
    'pomodoro':        'status',
    'timer':           'status',
    'scheduler_skill': 'list',
}

def load_skill(name: str):
    """Import skills.<name> fresh."""
    mod_name = f'skills.{name}'
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SKILLS_DIR / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

def exposed_skills() -> list[str]:
    out = []
    for f in sorted(os.listdir(SKILLS_DIR)):
        if not f.endswith('.py') or f.startswith('_'):
            continue
        txt = (SKILLS_DIR / f).read_text()
        if re.search(r'SKILL_MCP_EXPOSE\s*=\s*True', txt):
            out.append(f[:-3])
    return out

def run_one(name: str) -> dict:
    rec = {'name': name, 'status': None, 'duration_ms': 0, 'output': '', 'error': ''}
    if name in MANUAL_ONLY:
        rec['status'] = 'manual_only'
        rec['output'] = MANUAL_ONLY[name]
        return rec
    task = TASKS.get(name, '')
    t0 = time.time()
    try:
        mod = load_skill(name)
    except Exception as e:
        rec['status'] = 'import_error'
        rec['error'] = f'{type(e).__name__}: {e}\n' + traceback.format_exc()
        rec['duration_ms'] = int((time.time() - t0) * 1000)
        return rec
    run_fn = getattr(mod, 'run', None)
    if not callable(run_fn):
        rec['status'] = 'no_run'
        rec['error'] = 'skill has no run() function'
        return rec
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(run_fn, task)
            budget = TIMEOUT_OVERRIDE.get(name, TIMEOUT_S)
            try:
                result = fut.result(timeout=budget)
                rec['output'] = str(result)[:300]
                rec['status'] = 'pass'
            except FutTimeout:
                rec['status'] = 'timeout'
                rec['error'] = f'exceeded {budget}s'
    except Exception as e:
        rec['status'] = 'fail'
        rec['error'] = f'{type(e).__name__}: {e}\n' + traceback.format_exc()
    rec['duration_ms'] = int((time.time() - t0) * 1000)
    return rec

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else exposed_skills()
    results = []
    for s in targets:
        print(f'[..] {s}', flush=True)
        r = run_one(s)
        print(f'[{r["status"]:>12}] {s} ({r["duration_ms"]}ms)', flush=True)
        results.append(r)

    counts = {'pass': 0, 'fail': 0, 'timeout': 0, 'import_error': 0, 'manual_only': 0, 'no_run': 0}
    for r in results:
        counts[r['status']] = counts.get(r['status'], 0) + 1

    # Report
    reports_dir = Path.home() / '.codec' / 'reports'
    reports_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    report_path = reports_dir / f'stress_{date_str}.md'

    lines = []
    lines.append(f'# CODEC Skill Stress Test — {date_str} UTC')
    lines.append('')
    lines.append(f'Total: {len(results)}')
    for k, v in counts.items():
        if v: lines.append(f'- **{k}**: {v}')
    lines.append('')
    lines.append('| Skill | Status | ms |')
    lines.append('|---|---|---|')
    for r in results:
        lines.append(f'| {r["name"]} | {r["status"]} | {r["duration_ms"]} |')
    lines.append('')

    for r in results:
        lines.append(f'## {r["name"]} — {r["status"]}')
        lines.append(f'duration: {r["duration_ms"]}ms')
        if r['output']:
            lines.append('```')
            lines.append(r['output'])
            lines.append('```')
        if r['error']:
            lines.append('Error:')
            lines.append('```')
            lines.append(r['error'][:1500])
            lines.append('```')
        lines.append('')

    lines.append('## FIX QUEUE')
    for r in results:
        if r['status'] in ('fail', 'timeout', 'import_error'):
            head = (r['error'].splitlines() or [''])[0]
            lines.append(f'- **{r["name"]}** ({r["status"]}): {head}')

    report_path.write_text('\n'.join(lines))
    print(f'\nReport: {report_path}')
    print('Summary:', counts)
    # Also dump JSON for programmatic access
    (reports_dir / f'stress_{date_str}.json').write_text(json.dumps(results, indent=2, default=str))

if __name__ == '__main__':
    main()
