"""audit_report skill — wraps codec_audit_analyzer for MCP/autopilot exposure."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codec_audit_analyzer import run as _run

SKILL_NAME = "audit_report"
SKILL_DESCRIPTION = "Generate CODEC audit report: call volume, errors, p95 latency, high-error tools, timeouts, unused tools. Pass date YYYY-MM-DD or leave empty for yesterday."
SKILL_MCP_EXPOSE = True


def run(task: str = "", context: str = "") -> str:
    return _run(task, context)
