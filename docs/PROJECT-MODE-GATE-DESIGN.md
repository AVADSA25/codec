# One skill loader, a Project-mode gate that reads the URL, no blocked path in a plan (P-098)

Approval: Mickael, 3 Nov 2026, about finishing the audit plan: "go go build i trust
ur judgment here". `codec_agent_plan.py` and `codec_agent_runner.py` are AGENTS.md
section 10 zones; the status machine (`_VALID_TRANSITIONS`), `PLAN_SCHEMA_VERSION`
and the plan dataclasses are not changed.

## Why

The docs say every skill run goes through `codec_dispatch` (registry load gate,
plugin hooks, audit, skill switches). Six places still loaded skill files by hand:
the Telegram and iMessage briefings (`__import__("skills.<name>")` for weather,
calendar, tasks and Gmail), the heartbeat's mail alert, the check-in's unread count,
Daybreak's sources and the observer's shift report. Those runs wrote no audit line,
fired no hook and ignored the Skills page switch. Daybreak also put the owner's skill
folder first on `sys.path`, so a user file could stand in for a built-in.

In Project mode, `permission_gate` compared only the domain the model declared for
itself, skipped the check when the model left it empty, and never read the URL in
the task. A plan granted `example.com` could fetch anything. And `approve_plan`
wrote the drafted manifest as it was: the path blocklist was only applied to paths
the owner typed and to `/grant`.

## Part 1: built-in features run skills through `codec_dispatch`

New in `codec_dispatch`:

- `run_feature(name, task, feature, app="")`: runs one named skill for a built-in
  feature through `run_skill`. Returns `None` (the feature skips that source) when
  the skill is switched off or unknown. The first call in a process that never
  scanned the registry (bridges, heartbeat, observer) scans it once, metadata only,
  under a lock.
- `feature_module(name)`: the skill's module through the registry's load gate
  (manifest hash or AST check), `None` when switched off or unknown. Only for a
  feature that reads the skill's own helpers before deciding to run it.
- `run_skill(..., feature=None)`: when `feature` is set, the run's one audit line is
  `tool_result` (source `codec-dispatch`, extra `feature`, `result_len`,
  `correlation_id`) instead of `wake_dispatch`, and nothing is shown on the overlay
  (the owner did not ask for that run). Hooks fire as on every path, transport
  `dispatch`.

Sites:

| File | Before | After |
|---|---|---|
| `codec_telegram.py`, `codec_imessage.py` | `__import__` of four skills | `codec_bridges.run_briefing_skill` -> `run_feature(..., "briefing")` |
| `codec_heartbeat.py` (mail alert) | `spec_from_file_location` of `google_gmail` | `run_feature(..., "heartbeat")`; a switched-off skill skips the alert |
| `codec_daybreak.py` | own `sys.path` + `import_module` (user folder first) | `feature_module` to pre-load, `run_feature(..., "daybreak")` to run |
| `codec_checkin.py` (unread count) | loaded the Gmail skill for its `_get_service` | `codec_google_auth.build_service` directly (no skill load); skipped when `google_gmail` is off. The calendar read passes `feature="checkin"` |
| `codec_observer.py` (shift report) | own `sys.path` + `import_module` | `feature_module` for the day check, `run_feature("shift_report", "shift report [trigger=time\|idle]", "shift_report")` to fire |
| `routes/mcp.py` | loads `skills/mcp_connect.py` by path | unchanged, one documented line: it calls the skill's connection helpers, not `run` |
| `skills/auto_memorize.py` (review fix) | bare `from fact_extract import run`, which worked in the dashboard only after Daybreak's `sys.path` insert | `feature_module("fact_extract")`; manifest regenerated |

Consequences, intended: a skill switched off on the Skills page is skipped by these
features; the user folder can no longer shadow a built-in in Daybreak (built-in name
wins, user files always get the AST check). Side effect: in a bridge process the
registry is now scanned at the first briefing, so the bridge's free-text `try_skill`
(which matched nothing before, because nothing scanned in that process) starts
matching the `BRIDGE_SAFE_SKILLS` list as designed from then on. Reported as a
follow-up, not changed here.

## Part 2: Project-mode gate

`permission_gate` (`codec_agent_runner.py`):

- For `web_fetch` it takes the host of the first `http(s)://` URL in the task, the
  one the skill fetches. For `chrome_open` it takes the host of the URL the skill
  would open: the first word that is a URL or contains a dot, else the first
  shortcut name (`gmail` -> `mail.google.com`), copied from the skill and pinned by a
  parity test that runs the real skill with its side effects stubbed.
- That host, and the declared `network_domain` when there is one, must each be
  granted. Match: exact host, case and a trailing dot ignored. `"*"` still allows
  every host. The check now runs when `network_domain` is empty. A refusal is the
  existing `domain_not_authorized` (one correction nudge, then
  `blocked_on_permission`).
- Residual, documented in the code: where a redirect leads is decided during the
  fetch, after the gate. `web_fetch` re-checks every hop against the fetch guard
  (`codec_ssrf`), not against the plan's domains.

Not changed, owner decision: `approve_plan` grants `"*"` to every plan that browses
(#227, "single approval grants full web access"). While it does, the URL check
stops a fetch only for an agent without `"*"`, for example one that reaches
`web_fetch` through a global skill grant or a later `/grant`.

`codec_agent_plan.py`:

- `plan_from_dict` raises `ValueError` when a `read_paths` or `write_paths` entry is
  on `_PATH_BLOCKLIST_SUBSTRINGS` (segment match, as for typed paths). So a drafted
  plan with `~/.ssh` ends as `plan_failed`, an edited plan is refused (400), and
  `approve_plan` refuses with `PlanValidationError` (the approve route now answers 400
  instead of 500). None of the 34 plans on the owner's Mac has such a path (read-only
  scan, 3 Nov).
- The planner prompt said write paths default to `~/.codec/agents/{agent_id}/artifacts/`,
  which has been on the blocklist since D-14 (and `file_write` refuses all of
  `~/.codec`). It now says the project folder given in the request.

## Part 3: docs

AGENTS.md section 4 says what the loader is: one class (`SkillRegistry`, manifest +
AST gate), the shared instance in `codec_dispatch`, the other instances (MCP, voice
call, crews, autopilot, slash commands) built from the same class, `run_feature` for
built-in features, the two exemptions (`routes/mcp.py`, `codec_sandbox`), and the
two bare-name imports left outside this item (`codec_agent_messaging`'s iMessage
notice, `clipboard_url_fetch`), logged in docs/known-issues.md.
docs/codec-architecture.md gets the feature `tool_result` line.

## Tests

`tests/test_one_loader.py`: a Telegram briefing writes `tool_result` and `hook_fired`
lines with one correlation id per skill and skips a switched-off skill, writes no
overlay event; the same for iMessage, the heartbeat alert, Daybreak and the observer;
the seven files hold no skill load outside `codec_dispatch` but the one documented
line. `tests/test_project_gate.py`: a plan granting only `example.com` refuses
`web_fetch` and `chrome_open` tasks naming another host with `network_domain` empty,
allows `example.com`, `"*"` allows all, the parity test, and `plan_from_dict`,
`approve_plan` (and its route) refusing a plan that drafts `~/.ssh`. Existing tests
whose drafted plans used the old `~/.codec/agents` default now use a project folder.

## Rollback

`git revert`. Nothing stored changes. Takes effect after the owner restarts
`codec-agent-runner`, `codec-dashboard`, `codec-telegram`, `codec-imessage` (and
`codec-heartbeat`, `codec-observer` for the mail alert and shift report).
