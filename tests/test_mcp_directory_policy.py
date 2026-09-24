"""Claude MCP Directory policy: tools that generate AI audio/video/images or
drive other AI tools must not be exposed over MCP."""
import ast
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "skills"


def _expose(name):
    for node in ast.parse((SKILLS / f"{name}.py").read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SKILL_MCP_EXPOSE":
            return ast.literal_eval(node.value)
    return None


def test_ai_media_and_ai_driver_skills_not_mcp_exposed():
    assert _expose("tts_say") is False
    assert _expose("prompt_feeder") is False
