"""Fix #8: unit tests for the chat-handler helpers extracted from
chat_completion. The extraction (_build_chat_system_prompt, _chat_vision_response)
made these independently testable in isolation — that testability is the point
of reducing the god-module's hottest handler. Behavior is locked here; the
broader /api/chat behavior stays covered by test_chat_stream / test_step_budget /
test_chat_escalation / test_dashboard_api.
"""
import codec_dashboard as dash

_LOCAL_CFG = {"llm_base_url": "http://localhost:8083/v1"}


def _budget():
    return dash._StepBudget(route="chat", correlation_id="abcdef123456")


def test_vision_response_none_without_images():
    assert dash._chat_vision_response({"messages": []}, []) is None


def test_build_system_prompt_adds_attachment_note():
    out = dash._build_chat_system_prompt(_LOCAL_CFG, _budget(), has_attachment=True,
                                         last_user_text="look at this")
    assert "attached a file or image" in out
    assert "DO NOT emit [SKILL:...]" in out


def test_build_system_prompt_adds_content_rewrite_note():
    out = dash._build_chat_system_prompt(_LOCAL_CFG, _budget(), has_attachment=False,
                                         last_user_text="please rewrite this email")
    assert "rewritten content as plain prose" in out


def test_build_system_prompt_plain_request_has_no_turn_overrides():
    out = dash._build_chat_system_prompt(_LOCAL_CFG, _budget(), has_attachment=False,
                                         last_user_text="what's the weather like?")
    assert "attached a file or image" not in out
    assert "rewritten content as plain prose" not in out


def test_build_system_prompt_consumes_one_llm_call_step():
    b = _budget()
    assert b.count == 0
    dash._build_chat_system_prompt(_LOCAL_CFG, b, has_attachment=False, last_user_text="hi")
    assert b.count == 1, "the llm_call step must be consumed inside the prompt builder"
