# CODEC Enterprise Stability Plan — Read Before Any Code

## Root Cause 1: Code Duplication
codec.py AND skills/codec.py both have build_session_script(). A fix in one leaves the other broken.
FIX: Extract build_session_script(), all safety patterns, and all dialog code into a single codec_core.py. Both files import from it. One edit, one place, everywhere fixed. This is job 2 after smoke test.

## Root Cause 2: PM2 Worktree Blindspot
PM2 may run from .claude/worktrees/fervent-nightingale/ not ~/codec-repo. Every edit to ~/codec-repo is invisible to the running process until synced.
FIX: Create sync_to_pm2.sh that rsyncs ~/codec-repo to the PM2 exec_cwd then restarts affected process. Run it after every change before asking Mickael to test anything. Never ask Mickael to test without running this first.

## Root Cause 3: No Regression Guard
Mickael is the only regression test. One fix breaks another and nobody catches it until manual testing.
FIX: Build codec_smoke_test.py as job 1, before anything else. 10 checks, under 30 seconds:
1. codec_config imports clean
2. Danger patterns load and match rm -rf /
3. Memory DB accessible and memory table exists
4. Dashboard responds on port 8090
5. Qwen responds on port 8081
6. Whisper responds on port 8084
7. Kokoro responds on port 8083 or 8085
8. UI-TARS responds on configured port
9. At least one skill loads from registry
10. PM2 exec_cwd for open-codec matches ~/codec-repo

## Rule for Every Change
1. Make the edit
2. Run sync_to_pm2.sh
3. Run python3 codec_smoke_test.py
4. All 10 green = ask Mickael to test
5. Any red = fix before touching anything else
