# CODEC Full Product Audit — Test Instructions

## Your Mission

You are auditing CODEC, a voice-controlled AI computer agent. Your job is to **test every single feature across all 7 products**, one by one, confirm each works, and if anything fails — figure out why, fix it, test again, and report what happened.

## Setup

All services should already be running via PM2. Verify first:

```bash
pm2 list
```

If not running:
```bash
cd ~/codec-repo && pm2 start ecosystem.config.js
```

Dashboard should be at: http://localhost:8090

## Step 1: Run the automated test suite

```bash
cd ~/codec-repo
pytest tests/test_full_product_audit.py -v --tb=short 2>&1 | tee audit_results.txt
```

If `pytest` not found, use the full path:
```bash
/Users/mickaelfarina/Library/Python/3.13/bin/pytest tests/test_full_product_audit.py -v --tb=short 2>&1 | tee audit_results.txt
```

Review every PASS/FAIL. For each FAIL:
1. Read the error message
2. Investigate the root cause (read the relevant source file)
3. Fix the issue
4. Re-run that specific test: `pytest tests/test_full_product_audit.py::TestClassName::test_name -v`
5. Document what you found and fixed

## Step 2: Manual verification — Product by Product

After automated tests, verify these manually:

### Product 1: Dashboard (http://localhost:8090/)
- [ ] Page loads with CODEC branding
- [ ] Status indicator shows green (services healthy)
- [ ] Command input sends tasks
- [ ] History tab shows past commands
- [ ] Chat tab shows conversations
- [ ] Audit tab shows action log
- [ ] Settings tab loads config
- [ ] Settings can be saved
- [ ] Theme toggle works (dark/light)
- [ ] Webcam snapshot works (if camera available)
- [ ] Screenshot capture works
- [ ] File upload extracts text
- [ ] Clipboard read/write works
- [ ] TTS plays audio response
- [ ] Notification badge shows count
- [ ] CSP header has NO unsafe-eval (check response headers)
- [ ] UI element toggles work (Settings > UI Toggles)

### Product 2: Chat (http://localhost:8090/chat)
- [ ] Page loads with message area
- [ ] Send message gets LLM response
- [ ] Messages stream in real-time (SSE)
- [ ] New session button works
- [ ] Session saves to sidebar
- [ ] Load previous session works
- [ ] Web search toggle works
- [ ] File/image upload works
- [ ] Agent mode: select crew, see tools
- [ ] Agent execution returns results
- [ ] Custom agent save/load works
- [ ] Voice reply toggle works
- [ ] Copy message button works
- [ ] 250K context claim: send long conversation, verify no truncation errors

### Product 3: Voice (http://localhost:8090/voice)
- [ ] Page loads with call interface
- [ ] Start call establishes WebSocket
- [ ] Microphone captures audio
- [ ] Speech-to-text transcribes correctly
- [ ] LLM generates response
- [ ] Text-to-speech plays response
- [ ] Transcript shows conversation
- [ ] "Your Turn" button works
- [ ] Call timer increments
- [ ] End call disconnects cleanly
- [ ] Auto-call with ?auto=1 parameter

### Product 4: Vibe Code (http://localhost:8090/vibe)
- [ ] Monaco editor loads
- [ ] Syntax highlighting works
- [ ] Run Python code: `print(2+2)` shows `4`
- [ ] Run Bash code: `echo hello` shows `hello`
- [ ] Run JavaScript: `console.log(42)` shows `42`
- [ ] Save file works
- [ ] Save as skill works
- [ ] Forge skill creation works
- [ ] Session save/load works
- [ ] Session delete works
- [ ] HTML preview works
- [ ] Language auto-detection works
- [ ] Dangerous code blocked (try `rm -rf /` in bash)

### Product 5: Skills System
- [ ] `GET /api/skills` returns 40+ skills
- [ ] Each skill has name, triggers, description
- [ ] Weather skill fires on "what's the weather"
- [ ] Web search skill fires on "search for"
- [ ] Terminal skill fires on "run command"
- [ ] Time skill fires on "what time is it"
- [ ] Music skill fires on "play music"
- [ ] Calculator skill fires on "calculate"
- [ ] All 52 skill files compile without errors
- [ ] All skills have run() function
- [ ] All skills have SKILL_TRIGGERS
- [ ] create_skill.py routes through review gate (not direct file write)
- [ ] terminal.py uses centralized is_dangerous()
- [ ] Trigger matching uses word boundaries (play != display)

### Product 6: Agent/Crew Framework
- [ ] 13+ crews available via API
- [ ] deep_research crew exists
- [ ] daily_briefing crew exists
- [ ] email_handler crew exists
- [ ] content_writer crew exists
- [ ] competitor_analysis crew exists
- [ ] invoice_generator crew exists
- [ ] project_manager crew exists
- [ ] meeting_summarizer crew exists
- [ ] All crews have valid agents with roles
- [ ] Tool scoping works (allowed_tools enforced)
- [ ] Shell execute blocks dangerous commands
- [ ] Agent loop respects MAX_AGENT_STEPS (8)
- [ ] Audit logging works for crew execution

### Product 7: Tasks (http://localhost:8090/tasks)
- [ ] Schedules tab loads
- [ ] Create schedule works
- [ ] Toggle schedule enabled/disabled
- [ ] Delete schedule works
- [ ] Run Now triggers immediate execution
- [ ] Heartbeat tab loads config
- [ ] Alerts tab loads alert config
- [ ] History tab shows execution log
- [ ] Reports/notifications tab shows alerts
- [ ] Mark notification as read works
- [ ] Heartbeat checks system health (5 services)
- [ ] Heartbeat checks memory stats
- [ ] Heartbeat creates daily backup
- [ ] Heartbeat size monitoring (>100MB warning)

### Cross-cutting: Authentication
- [ ] Auth page loads
- [ ] Touch ID option shown (if available)
- [ ] PIN entry works
- [ ] TOTP 2FA setup flow works
- [ ] Wrong PIN rejected
- [ ] Session persists after auth
- [ ] Logout clears session

### Cross-cutting: Memory System
- [ ] FTS5 search works
- [ ] Recent messages query works
- [ ] Sessions list works
- [ ] Rebuild FTS works
- [ ] Query sanitization blocks SQL injection
- [ ] Memory save + search roundtrip works

### Cross-cutting: Security Verification
- [ ] DANGEROUS_PATTERNS has 30+ entries in codec_config.py
- [ ] is_dangerous() catches: rm -rf, sudo, chmod 777, curl|bash, fork bomb
- [ ] is_dangerous() allows: ls, echo, git, python
- [ ] /api/command rejects dangerous commands (403)
- [ ] CSP has no unsafe-eval (both main and preview frame)
- [ ] create_skill.py cannot write to disk without review
- [ ] terminal.py imports is_dangerous from codec_config
- [ ] heartbeat checks is_dangerous before auto-execution
- [ ] No /tmp/ paths in codec files (should use ~/.codec/)
- [ ] ecosystem.config.js exists and is valid JS

## Step 3: Report

Create a final report with:

1. **Test Results Summary**: X passed, Y failed, Z skipped
2. **Fixes Applied**: For each failure, what was wrong and how you fixed it
3. **Remaining Issues**: Anything you couldn't fix and why
4. **Grade per Product**: A/B/C/D/F for each of the 7 products
5. **Overall Grade**: Combined assessment

Save report to: `~/codec-repo/AUDIT_REPORT.md`

## Important Notes

- Dashboard is at http://localhost:8090
- If a service is down, try: `pm2 restart <service-name>`
- The LLM runs locally on MLX (Mac Studio M1 Ultra)
- Whisper STT at localhost:8084, Kokoro TTS at localhost:8085
- Skills are in ~/.codec/skills/
- Memory DB is at ~/.q_memory.db
- Config is at ~/.codec/config.json
- When fixing issues: always run py_compile after editing Python files
- After fixes: commit with descriptive message
- Do NOT skip any test — test everything, report everything
