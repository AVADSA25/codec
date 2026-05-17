"""CODEC Skill: Audit Log Integrity Verification (PR-2E, closes D-12).

Walks `~/.codec/audit.log` and verifies the HMAC-SHA256 signature on
every line. Reports total / signed / unsigned / broken counts and the
line number of the first integrity violation. Operator-local — NOT
MCP-exposed (forensic operations don't go through the remote MCP
boundary).

Pre-PR-2E lines have no `hmac` field and are classified as `unsigned`,
not broken. Post-PR-2E lines with `hmac_status="unsigned_keychain_unavailable"`
also count as unsigned (Keychain was locked at write time).
"""
SKILL_NAME = "audit_verify"
SKILL_DESCRIPTION = (
    "Verify the integrity of the audit log via HMAC chain. "
    "Reports total / signed / unsigned / broken lines + first violation."
)
SKILL_TRIGGERS = [
    "verify audit log", "check audit integrity",
    "audit log integrity", "verify audit",
]
SKILL_MCP_EXPOSE = False  # operator-only; forensic surface stays local


def run(task: str = "", app: str = "", ctx: str = "") -> str:
    """Run the verification utility and return a human-readable summary."""
    try:
        from codec_audit import verify_audit_log
    except Exception as e:
        return f"Audit verify unavailable: codec_audit import failed: {e}"

    result = verify_audit_log()
    if result.get("error"):
        return f"Audit verify failed: {result['error']}"

    total = result["total_lines"]
    signed = result["signed_lines"]
    unsigned = result["unsigned_lines"]
    broken = result["broken_lines"]

    if result["integrity_ok"]:
        return (
            f"Audit log integrity OK. "
            f"Total: {total}, signed: {signed}, "
            f"pre-PR-2E or unsigned: {unsigned}, broken: 0."
        )
    first = result.get("first_broken_line_no", "?")
    return (
        f"⚠️ INTEGRITY VIOLATION DETECTED. "
        f"Broken lines: {broken} (first at line {first}). "
        f"Total: {total}, signed: {signed}, unsigned: {unsigned}. "
        f"Possible tampering — investigate immediately."
    )
