"""P-098: Project mode reads the host from the task, and no plan asks for a
blocked path (docs/PROJECT-MODE-GATE-DESIGN.md).

permission_gate used to check only the network_domain the model declared, so a
web_fetch or chrome_open task naming another host passed when the declaration
was empty or false. It now takes the host the skill will reach from the task
(pinned against the real skills by the parity tests) and checks it against the
plan's domains. plan_from_dict and approve_plan refuse a drafted path on the
blocklist. Nothing here opens Chrome, fetches a page or touches ~/.codec.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import codec_agent_runner as car
from codec_agent_runner import Action, PermissionViolation, permission_gate

REPO = Path(__file__).resolve().parent.parent

ONLY_EXAMPLE = {"skills": ["web_fetch", "chrome_open"], "network_domains": ["example.com"],
                "read_paths": [], "write_paths": []}
NO_GLOBAL = {"skills": [], "network_domains": [], "read_paths": [], "write_paths": []}


def _gate(skill, task, domain="", grants=ONLY_EXAMPLE, global_grants=NO_GLOBAL):
    permission_gate(Action(skill=skill, task=task, network_domain=domain), grants, global_grants)


# ── the gate ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("skill,task,host", [
    ("web_fetch", "fetch https://evil.test/page and summarise it", "evil.test"),
    ("web_fetch", "read (https://www.example.com/a).", "www.example.com"),
    ("chrome_open", "open evil.test/login", "evil.test"),
    ("chrome_open", "go to https://evil.test then example.com", "evil.test"),
    ("chrome_open", "open gmail", "mail.google.com"),
])
def test_gate_refuses_an_ungranted_host_with_no_declared_domain(skill, task, host):
    with pytest.raises(PermissionViolation) as e:
        _gate(skill, task)
    assert (e.value.reason, e.value.needed) == ("domain_not_authorized", host)


def test_gate_refuses_a_task_host_that_contradicts_the_declared_domain():
    with pytest.raises(PermissionViolation) as e:
        _gate("web_fetch", "fetch https://evil.test/x", domain="example.com")
    assert e.value.needed == "evil.test"


@pytest.mark.parametrize("skill,task", [
    ("web_fetch", "fetch https://example.com/page"),
    ("web_fetch", "fetch https://EXAMPLE.com./page"),
    ("chrome_open", "open example.com/docs"),
    ("chrome_open", "open a new tab"),          # no host named: nothing to check
])
def test_gate_allows_the_granted_host(skill, task):
    _gate(skill, task)


def test_gate_star_and_global_grants_still_allow():
    _gate("web_fetch", "fetch https://evil.test/x",
          grants=dict(ONLY_EXAMPLE, network_domains=["example.com", "*"]))
    _gate("chrome_open", "open evil.test",
          global_grants=dict(NO_GLOBAL, network_domains=["evil.test"]))


# ── parity with the real skills ─────────────────────────────────────────────

def _load_skill(name):
    spec = importlib.util.spec_from_file_location(f"p098_parity_{name}", REPO / "skills" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


WEB_FETCH_TASKS = [
    "fetch https://example.com/page and summarise",
    "read (https://news.example.org/a?b=1).",
    "compare https://a.example.com/x with https://b.example.net/y",
    "https://EXAMPLE.com./path",
    'get "https://quoted.example.io/x"',
    "fetch http://user:pw@inner.example.com:8080/p",
    "no address in this one",
]


@pytest.mark.parametrize("task", WEB_FETCH_TASKS)
def test_parity_web_fetch_gate_reads_the_url_the_skill_fetches(task):
    mod = _load_skill("web_fetch")
    fetched = []

    def capture(url, *a, **k):
        fetched.append(url)
        raise RuntimeError("stop before the network")
    mod._get_validating_redirects = capture
    mod.run(task, "agent:p098")
    expected = [car._url_host(u) for u in fetched]
    assert car._task_hosts(Action(skill="web_fetch", task=task)) == expected


def _chrome_open_tasks():
    mod = _load_skill("chrome_open")
    tasks = [f"open {name}" for name in mod.SHORTCUTS]
    tasks += ["open github.com/foo", "go to https://Example.org/x then https://other.net",
              'open "docs.example.com"', "please open the page at site.io",
              "open notes for project v1.2", "browse to user@evil.test:pw@good.example",
              "open my calendar and gmail", "open file:///etc/hosts"]
    return tasks


@pytest.mark.parametrize("task", _chrome_open_tasks())
def test_parity_chrome_open_gate_reads_the_url_the_skill_opens(task, monkeypatch):
    import codec_browser_policy
    mod = _load_skill("chrome_open")
    opened = []

    def check(url, skill):
        opened.append(url)
        return False, "stopped by the test"
    monkeypatch.setattr(codec_browser_policy, "check_url", check)
    mod.subprocess = SimpleNamespace(run=lambda *a, **k: pytest.fail("chrome_open ran a program"))
    mod.run(task)
    expected = [car._url_host(u) for u in opened]
    assert car._task_hosts(Action(skill="chrome_open", task=task)) == expected


def test_parity_shortcut_table_matches_chrome_open():
    mod = _load_skill("chrome_open")
    assert [(n, car._url_host(u)) for n, u in mod.SHORTCUTS.items()] == list(car._CHROME_OPEN_SHORTCUT_HOSTS)


# ── no blocked path in a plan ───────────────────────────────────────────────

@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    import codec_agent_plan as cap
    import codec_audit
    monkeypatch.setattr(cap, "_CODEC_DIR", tmp_path)
    monkeypatch.setattr(cap, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(cap, "_GLOBAL_GRANTS_PATH", tmp_path / "agent_global_grants.json")
    monkeypatch.setattr(codec_audit, "_AUDIT_LOG", tmp_path / "audit.log")
    return tmp_path / "agents"


def _plan_dict(read_paths=(), write_paths=("~/codec-projects/p098/**",)):
    return {
        "schema": 1, "agent_id": "p098", "goals": ["Collect notes"],
        "checkpoints": [{"id": "c1", "title": "Collect", "description": "...",
                         "skills_needed": ["weather"], "expected_output": "notes",
                         "step_budget": 60}],
        "permission_manifest": {"read_paths": list(read_paths), "write_paths": list(write_paths),
                                "network_domains": [], "skills": ["weather"],
                                "destructive_ops": []},
        "estimated_duration_minutes": 5, "assumptions": [],
    }


@pytest.mark.parametrize("read_paths,write_paths", [
    (["~/.ssh/**"], ["~/codec-projects/p098/**"]),
    ([], ["~/.ssh/authorized_keys"]),
    ([], ["~/.codec/agents/p098/artifacts/**"]),
])
def test_blocked_path_refused_by_plan_from_dict(read_paths, write_paths):
    import codec_agent_plan as cap
    with pytest.raises(ValueError, match="blocked path"):
        cap.plan_from_dict(_plan_dict(read_paths, write_paths))


def test_blocked_path_check_is_by_segment():
    import codec_agent_plan as cap
    plan = cap.plan_from_dict(_plan_dict(["~/Documents/notes_ssh/**"]))
    assert plan.permission_manifest.read_paths == ["~/Documents/notes_ssh/**"]


def _waiting_plan_on_disk(agents_dir, read_paths):
    import codec_agent_plan as cap
    d = agents_dir / "p098"
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(_plan_dict(read_paths)))
    cap.save_manifest("p098", {"agent_id": "p098", "status": "awaiting_approval", "title": "x"})
    return d


def test_blocked_path_refused_by_approve_plan_and_route(agents_dir):
    import codec_agent_plan as cap
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.agents import router
    d = _waiting_plan_on_disk(agents_dir, ["~/.ssh/**"])

    with pytest.raises(cap.PlanValidationError, match="blocked path"):
        cap.approve_plan("p098")
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post("/api/agents/p098/approve")
    assert r.status_code == 400 and "blocked path" in r.json()["detail"]
    assert not (d / "grants.json").exists()
    assert cap.load_manifest("p098")["status"] == "awaiting_approval"
