"""Tests for codec_scheduler"""
import os

# Use a temp path so tests don't pollute real schedules
os.environ.setdefault("HOME", os.path.expanduser("~"))


def test_scheduler_import():
    from codec_scheduler import load_schedules
    assert callable(load_schedules)


def test_add_and_remove_schedule(tmp_path, monkeypatch):
    """add_schedule() now creates schedules with enabled=False by default —
    operator must explicitly toggle_schedule(id, True) to activate. This is
    a deliberate hardening: a misconfigured cron arg can't silently start
    firing a crew until the operator opts in."""
    import codec_scheduler
    test_path = str(tmp_path / "schedules.json")
    monkeypatch.setattr(codec_scheduler, "SCHEDULE_PATH", test_path)

    s = codec_scheduler.add_schedule("daily_briefing", cron_hour=8)
    assert s["crew"] == "daily_briefing"
    assert s["hour"] == 8
    assert s["enabled"] is False
    assert s["id"].startswith("sched_")

    schedules = codec_scheduler.load_schedules()
    assert len(schedules) == 1

    # toggle_schedule activates it; round-trip survives reload.
    assert codec_scheduler.toggle_schedule(s["id"], True) is True
    assert codec_scheduler.load_schedules()[0]["enabled"] is True

    removed = codec_scheduler.remove_schedule(s["id"])
    assert removed is True
    assert len(codec_scheduler.load_schedules()) == 0


def test_parse_schedule_intent():
    from codec_scheduler import _parse_schedule_intent
    intent = _parse_schedule_intent("run daily briefing every morning at 8am")
    assert intent["crew"] == "daily_briefing"
    assert intent["hour"] == 8

    intent2 = _parse_schedule_intent("competitor analysis every monday at 9")
    assert intent2["crew"] == "competitor_analysis"
    assert 0 in intent2["days"]  # Monday


def test_skill_list(tmp_path, monkeypatch):
    import codec_scheduler
    monkeypatch.setattr(codec_scheduler, "SCHEDULE_PATH", str(tmp_path / "s.json"))
    result = codec_scheduler.run("list schedules")
    assert "No schedules" in result or "schedule" in result.lower()


def test_skill_add(tmp_path, monkeypatch):
    import codec_scheduler
    monkeypatch.setattr(codec_scheduler, "SCHEDULE_PATH", str(tmp_path / "s.json"))
    result = codec_scheduler.run("run daily briefing every morning at 8")
    assert "Scheduled" in result or "daily_briefing" in result


def test_heartbeat_has_execute():
    from codec_heartbeat import execute_pending_tasks
    assert callable(execute_pending_tasks)


def test_extract_task():
    from codec_heartbeat import extract_task_from_message
    msg = "I have logged the task to open YouTube and search for AI news."
    task = extract_task_from_message(msg)
    assert task and "youtube" in task.lower()

    msg2 = "I have queued open Safari and check email for CODEC."
    task2 = extract_task_from_message(msg2)
    assert task2  # should extract something

    assert extract_task_from_message("Hello how are you") == ""
