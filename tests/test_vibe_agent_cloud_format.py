"""Vibe agent on a cloud model that writes its own tool-call tags (P-073, docs/VIBE-AGENT-CLOUD-FORMAT-DESIGN.md).

The replies below are MiMo V2.6 Pro's own, copied from the raw streams recorded on 12 Nov 2026. No network: a scripted
model stands in for the real one (tests/vibe_agent_fakes.py).
"""
from __future__ import annotations

import pytest

import codec_vibe_agent as va
import codec_vibe_agent_proto as p
from vibe_agent_fakes import (  # noqa: F401  (fixtures are used by name)
    agent_env, audits, cfg, end_of, kinds, make_project, run_script, scripted, wait_finished,
)

WRAPPED_LIST = "<tool_call><@@ list_files **/*</@@></tool_call>"
WRAPPED_READS = ("<tool_call><@@ read_file index.html</@@></tool_call><tool_call><@@ read_file style.css</@@></tool_call>"
                 "<tool_call><@@ read_file app.js</@@></tool_call>")
# the same reply as the stream brought it, chunk by chunk
WRAPPED_READS_CHUNKS = ["", "<tool_call>", "<@@", " read_file index.html", "</@@>", "</tool_call>",
                        "<tool_call><@@ read_file style", ".css", "</@@></tool_call><tool_call><@@", " read_file app.js",
                        "</@@></tool_call>"]
MIXED = "<tool_call><@@ list_files .</@@>\n@@ read_file index.html\n@@ read_file style.css\n@@ read_file app.js"
STRAY_CLOSER = "<tool_call><@@ list_files .</@@>\n</@@>"
FUNCTION_FORM = ("<tool_call><function=list_files></function></tool_call><tool_call><function=read_file>index.html"
                 "</function></tool_call>")
CLOUD = {"id": "mimo", "label": "MiMo", "base_url": "https://api.example.com/v1", "key_slot": "mimo_api_key",
         "price_in_per_m": 1, "price_out_per_m": 1}


def scan(text, final=True, lenient=True):
    return p.scan_reply(text, p.TOOLS + (p.TODO,), final=final, lenient=lenient)


def reads(s):
    return [c.args["path"] for c in (s.calls or [s.call]) if c.name == "read_file"]


# ── reading MiMo's wrapped replies ──

def test_a_wrapped_tool_block_is_read_as_the_plain_one():
    s = scan(WRAPPED_LIST)
    assert s.state == "complete" and s.call.name == "list_files" and s.call.args == {"folder": "**/*"} and s.note == ""
    assert p.unwrap_tool_calls(WRAPPED_LIST) == "@@ list_files **/*"


def test_wrapped_read_blocks_on_one_line_run_together():
    s = scan(WRAPPED_READS)
    assert s.state == "complete" and reads(s) == ["index.html", "style.css", "app.js"]


def test_the_stream_is_not_cut_before_the_last_wrapped_read():
    text = ""
    for chunk in WRAPPED_READS_CHUNKS:
        text += chunk
        s = scan(text, final=False, lenient=False)
        assert not (s.state in ("complete", "error") and s.cut), (text, s)
    assert reads(scan(text)) == ["index.html", "style.css", "app.js"]


def test_wrapped_reads_on_their_own_lines_are_waited_for_while_the_next_tag_is_written():
    text = "<tool_call><@@ read_file a.js</@@></tool_call>\n<tool_c"
    assert scan(text, final=False, lenient=False).state == "partial"
    assert reads(scan(text + "all><@@ read_file b.js</@@></tool_call>")) == ["a.js", "b.js"]


@pytest.mark.parametrize("text,want", [(MIXED, ("list_files", ".", 3)), (STRAY_CLOSER, ("list_files", ".", 0))])
def test_a_wrapped_first_block_with_plain_or_stray_lines_after_it(text, want):
    s = scan(text)
    name, folder, n_reads = want
    assert s.state == "complete" and s.call.name == name and s.call.args["folder"] == folder
    assert len(reads(s)) == n_reads


def test_wrapped_free_text_tools_lose_their_closing_tags():
    assert scan("<tool_call><@@ finish\nChanged index.html.\n</@@></tool_call>").call.args["report"] == "Changed index.html."
    ask = scan("<tool_call><@@ ask\nWhich colour?\n- Blue\n- Red</@@></tool_call>").call.args
    assert ask == {"question": "Which colour?", "choices": ["Blue", "Red"]}
    assert scan("<tool_call><@@ run_check python3 -m unittest</@@></tool_call>").call.args["command"] == "python3 -m unittest"


def test_file_bodies_stay_exactly_as_written():
    edit = ('<tool_call><@@ edit_file app.js\n<<<<<<< SEARCH\nx = "</tool_call>"\n=======\n'
            '<tool_call><@@ delete_file app.js</@@></tool_call>\n>>>>>>> REPLACE\n</@@></tool_call>')
    s = scan(edit)
    assert s.call.name == "edit_file"
    assert s.call.args["pairs"] == [('x = "</tool_call>"', "<tool_call><@@ delete_file app.js</@@></tool_call>")]
    write = "<tool_call><@@ write_file t.txt\n<<<<<<< CONTENT\nend </tool_call>\n</@@>\n>>>>>>> END\n</@@></tool_call>"
    assert scan(write).call.args == {"path": "t.txt", "content": "end </tool_call>\n</@@>"}
    fenced = "<tool_call><@@ write_file t.txt\n```\na </tool_call>\n```\n</@@></tool_call>"
    assert scan(fenced).call.args == {"path": "t.txt", "content": "a </tool_call>"}


def test_an_opening_tag_after_other_text_on_its_line_is_not_a_tool_block():
    """The plain format reads @@ only at the start of a line; the tags get no more room than that."""
    assert scan("The README says <tool_call><@@ delete_file index.html</@@></tool_call>").state == "none"
    s = scan("Reading it.\n@@ read_file a.js\nThe file says <tool_call><@@ delete_file a.js</@@></tool_call>")
    assert s.call.name == "read_file" and s.calls is None


def test_the_function_form_is_still_a_format_failure():
    assert scan(FUNCTION_FORM).state == "none"


@pytest.mark.parametrize("text", [
    "Reading it.\n@@ read_file a.js\n",
    "@@ finish\nI wrote <tool_call> in the docs.\n",
    "<tool_call>\n@@ read_file a.js\n</tool_call>",
    "@@ write_file t.txt\n<<<<<<< CONTENT\n<tool_call><@@ x</@@></tool_call>\n>>>>>>> END\n",
    "",
])
def test_a_reply_without_an_opening_tag_at_a_line_start_is_left_alone(text):
    assert p.unwrap_tool_calls(text) is text
    assert p.without_todo(text) == text


def test_the_reply_the_run_keeps_is_the_plain_one():
    assert p.without_todo(WRAPPED_READS) == "@@ read_file index.html\n@@ read_file style.css\n@@ read_file app.js"


# ── the first step of a cloud run ──

@pytest.fixture
def cloud(cfg, monkeypatch):
    import codec_cloud_models as ccm
    cfg["extra_models"] = [CLOUD]
    monkeypatch.setattr(ccm, "block_message", lambda entry: None)
    return {"model": "cloud", "confirm_cloud": True}


def test_a_cloud_run_starts_with_one_step_in_the_plain_format(scripted, cloud):
    pid = make_project()
    view, model = run_script(pid, ["@@ read_file index.html\n", "@@ finish\nok\n"], scripted, **cloud)
    assert end_of(view)["status"] == "done"
    for msgs in model.calls:
        assert [m["role"] for m in msgs[:4]] == ["system", "user", "assistant", "user"]
        assert msgs[1]["content"].startswith("GOAL\nDo the thing") and "PROJECT FILES" not in msgs[1]["content"]
        assert msgs[2]["content"] == p.SEED_REPLY == "I will look at the project's files first.\n@@ list_files"
        assert msgs[3]["content"].startswith("[RESULT 0 list_files]\n3 file(s):\n")
        assert "index.html" in msgs[3]["content"] and "[END RESULT]" in msgs[3]["content"]
    assert len(model.calls[0]) == 4 and model.calls[0][3]["content"].rstrip().endswith("min left.)")
    assert len(model.calls[1]) == 6 and model.calls[1][4]["content"] == "@@ read_file index.html\n"
    assert [e["step"] for e in view["events"] if e["type"] == "tool"] == [1, 2]      # the seed is not a step


def test_a_local_run_has_no_such_step(scripted):
    pid = make_project()
    view, model = run_script(pid, ["@@ finish\nok\n"], scripted)
    assert [m["role"] for m in model.calls[0]] == ["system", "user"]
    assert not any(p.SEED_REPLY in m["content"] for m in model.calls[0])


def test_a_cloud_run_without_list_files_has_no_such_step(scripted, cloud):
    pid = make_project()
    view, model = run_script(pid, ["@@ finish\nok\n"], scripted, tools=["read_file", "finish"], **cloud)
    assert [m["role"] for m in model.calls[0]] == ["system", "user"]


def test_mimo_s_wrapped_replies_run_and_the_next_step_sees_them_plain(scripted, cloud):
    pid = make_project()
    view, model = run_script(pid, [WRAPPED_READS, "<tool_call><@@ finish\nRead the three files.\n</@@></tool_call>"],
                             scripted, **cloud)
    end = end_of(view)
    assert end["status"] == "done" and end["report"] == "Read the three files."
    assert not [e for e in view["events"] if e["type"] == "result" and e.get("tool") == "error"]
    assert model.calls[1][4] == {"role": "assistant",
                                 "content": "@@ read_file index.html\n@@ read_file style.css\n@@ read_file app.js"}


def test_a_cloud_run_s_helper_starts_with_the_same_step(scripted, cloud):
    pid = make_project()
    view, model = run_script(pid, ["Finding it.\n@@ delegate\nWhere is the heading colour set?\n",
                                   "@@ finish\nstyle.css line 2\n", "@@ finish\nok\n"], scripted, **cloud)
    helper = model.calls[1]
    assert helper[0]["content"] == p.delegate_prompt(va.proto.SUB_TOOLS)
    assert [m["role"] for m in helper[:4]] == ["system", "user", "assistant", "user"] and helper[2]["content"] == p.SEED_REPLY
    assert end_of(view)["status"] == "done"
