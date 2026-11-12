# Project mode: a plan gets only the domains it lists (E-02, 2026-11-12)

Owner approval: decision board card E-02, 12 Nov 2026. "Stop the automatic star
grant: a plan gets only the domains it lists; you can still add one from the
Inbox when it asks."

## What
`codec_agent_plan.approve_plan` writes `grants.json` with the plan's own
`network_domains` and nothing more. The block that added `"*"` to every plan that
listed a domain or used a web skill (#227, "single approval grants full web
access") is gone. The planner prompt gains one rule: `network_domains` must list
every site the plan will open or fetch, because the agent can reach only those.

## Why
Since P-098 `permission_gate` reads the host a `web_fetch` or `chrome_open` task
will reach and requires it in the granted domains, declared or not. With `"*"` in
almost every browsing plan's grants that check bound nearly no agent. Without it,
an approved plan reaches the sites the owner saw on the approval card and no
other.

## Behaviour change
- A host the plan did not list: the gate refuses it, the model gets one
  correction nudge, a second miss sets `blocked_on_permission` and posts the
  `agent_blocked` message with a Grant action. The Inbox turns it into a
  `POST /api/agents/{id}/grant` with kind `network_domains` and the refused host;
  the grant adds it to that agent's `grants.json` and the run goes on.
- `"*"` still works when the owner gives it: a global grant
  (`/api/agent_global_grants`) or a `/grant` with value `*`.
- Plans approved before this change keep the `"*"` already in their
  `grants.json`; nothing stored is rewritten.
- Not changed: path grants, skills, the runner's gate code (one comment
  updated), the redirect check (`codec_ssrf`).

## Tests
`tests/test_plan_domain_grants.py`: approval grants exactly the listed domains,
also for a web skill with no domain; an unlisted host is refused, the Inbox's
Grant button for that block posts kind `network_domains` and the host, the route
returns `running`, and the gate then allows the host.
`tests/test_agent_plan.py`: the end-to-end approval test expects the listed
domains only.

## Rollback
`git revert` of the PR. Grants written while it was live keep their listed
domains; an agent that then needs another site asks through the Inbox.
