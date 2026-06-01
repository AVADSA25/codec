"""CODEC Skill: Cookbook hardware scan (read-only)."""
from codec_cookbook import probe

SKILL_NAME = "cookbook_scan"
SKILL_DESCRIPTION = (
    "Scan this Mac's hardware + live PM2 stack and report unified-memory "
    "headroom available for serving additional local models. Read-only."
)
SKILL_TAGS = ["cookbook", "models", "scan", "hardware", "local-llm"]
SKILL_TRIGGERS = [
    "cookbook scan", "scan hardware", "cookbook hardware", "model memory headroom",
    "how much memory for models", "cookbook status",
]
SKILL_MCP_EXPOSE = True  # read-only, safe to expose


def run(task, app="", ctx=""):
    s = probe.snapshot()
    lines = [
        f"🖥  {s['chip'] or 'Apple Silicon'} — {s['unified_total_gb']} GB unified",
        f"   resident (PM2): {s['resident_gb_total']} GB  ·  free (vm_stat): {s['vm_free_gb']} GB",
        f"   available for a new model: ~{s['available_gb']} GB "
        f"(total − {s['os_reserve_gb']} GB OS reserve − resident)",
        f"   PM2 processes: {s['pm2_process_count']}",
    ]
    if not s["mlx_version_ok"]:
        lines.append(f"   ⚠ mlx-lm {s['mlx_version'] or 'missing'} — Qwen3 MoE needs ≥ 0.25.2")
    else:
        lines.append(f"   mlx-lm {s['mlx_version']} ✓")
    busy = s["serve_ports_bound"]
    rng = s["serve_range"]
    lines.append(
        f"   Cookbook serve range {rng[0]}-{rng[1]}: "
        + (f"{len(busy)} busy ({', '.join(map(str, busy))})" if busy else "all free")
    )
    lines.append(f"   protected ports (never touched): {', '.join(map(str, s['protected_ports']))}")
    return "\n".join(lines)
