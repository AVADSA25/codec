"""This Mac and its owner, read at runtime for the chat, voice and wake prompts (P-026).

`gather()` is the one function the prompts read their situation from:

  hardware  this Mac's model, chip and memory, read from the machine (ioreg and sysctl; no
            serial numbers), once per process; "" when it cannot be read (Linux, a failed probe)
  skills    how many skills the registry lists that are switched on; never a typed number
  about_me  the owner's own text from Settings > Memory > About me (codec_memory_upgrade)
  facts     active saved facts, newest first, at most FACTS_LIMIT, only those whose origin is
            owner, derived or system (codec_fence.PROMPT_ORIGINS, P-037); `exclude_sources` also
            leaves out facts by their source tag (codec_memory_upgrade.query_valid_facts)

Deep Chat renders all four with `chat_block()` and leaves out CHAT_EXCLUDED_SOURCES. Voice
(codec_voice._build_system_prompt) and the wake word (codec._build_voice_system_prompt) render
About me and the facts in their own blocks. Nothing here writes anything.
Design: docs/BUYER-IDENTITY-DESIGN.md, docs/MEMORY-PROVENANCE-DESIGN.md.
"""
from __future__ import annotations

import logging
import subprocess
import sys

import codec_fence

log = logging.getLogger("codec_runtime_context")

# As many facts as voice and the wake word have always injected.
FACTS_LIMIT = 20
# The origins every prompt's facts come from (P-037): agent and untrusted facts, and any source
# CODEC does not know, stay out until the owner promotes them in Settings > Memory.
PROMPT_ORIGINS = codec_fence.PROMPT_ORIGINS
# Facts Deep Chat also leaves out by source tag: daybreak facts are the open threads, which Deep Chat
# already gets in their own block (codec_daybreak.get_working_context). mcp and untrusted are kept
# out by the origin filter as well.
CHAT_EXCLUDED_SOURCES = ("mcp", "untrusted", "daybreak")
# Deep Chat's limits: About me as long as Settings > Memory lets it be (routes.memory_page.ABOUT_MAX),
# one fact line and the whole facts block (about 400 tokens).
CHAT_ABOUT_MAX_CHARS = 1500
CHAT_FACT_MAX_CHARS = 300
CHAT_FACTS_MAX_CHARS = 1600

_HARDWARE: str | None = None  # read once per process; the hardware does not change while it runs


def _read(cmd: list, timeout: float = 3.0) -> bytes:
    """stdout of a read-only command, b"" on any failure. Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else b""
    except (OSError, subprocess.SubprocessError):
        return b""


def _sysctl(name: str) -> str:
    return _read(["/usr/sbin/sysctl", "-n", name]).decode("utf-8", "replace").strip()


def _product_name() -> str:
    """The Mac's model name as the machine reports it ("Mac Studio", "MacBook Air"); "" when absent."""
    raw = _read(["/usr/sbin/ioreg", "-arc", "IOPlatformDevice", "-k", "product-name"])
    if not raw:
        return ""
    try:
        import plistlib
        for entry in plistlib.loads(raw):
            value = entry.get("product-name") if isinstance(entry, dict) else None
            if isinstance(value, (bytes, bytearray)):
                value = bytes(value).split(b"\0", 1)[0].decode("utf-8", "replace")
            if isinstance(value, str) and value.strip():
                return value.strip()
    except Exception as e:  # a malformed answer means no name, never a failed prompt
        log.debug("ioreg product-name unreadable: %s", e)
    return ""


def _probe_hardware() -> str:
    if sys.platform != "darwin":
        return ""
    name = _product_name() or _sysctl("hw.model")
    chip = _sysctl("machdep.cpu.brand_string")
    mem = _sysctl("hw.memsize")
    memory = f"{round(int(mem) / 2 ** 30)} GB memory" if mem.isdigit() and int(mem) > 0 else ""
    return ", ".join(p for p in (name, chip, memory) if p)


def hardware() -> str:
    """This Mac in a few words, e.g. "Mac Studio, Apple M1 Ultra, 64 GB memory"; "" when unknown."""
    global _HARDWARE
    if _HARDWARE is None:
        _HARDWARE = _probe_hardware()
    return _HARDWARE


def skill_count() -> int:
    """How many skills the registry lists that are switched on (P3.6); 0 when unknown."""
    try:
        from codec_dispatch import registry
        if not registry.names():
            registry.scan()  # metadata only (AST); no skill code runs
        names = registry.names()
    except Exception as e:
        log.debug("skill registry unavailable: %s", e)
        return 0
    try:
        import codec_skill_switches
        off = codec_skill_switches.off_set()
    except Exception:
        off = frozenset()
    return sum(1 for n in names if n not in off)


def gather(*, machine: bool = True, facts: bool = True, exclude_sources=(),
           origins=PROMPT_ORIGINS) -> dict:
    """{"hardware", "skills", "about_me", "facts"} for a prompt. `machine=False` skips the hardware
    and the skill count (voice and the wake word do not use them); `facts=False` skips the facts
    (a temporary chat); `exclude_sources` leaves out facts by source; `origins` keeps only facts of
    these origins (default PROMPT_ORIGINS; None reads every fact). Never raises."""
    ctx = {"hardware": "", "skills": 0, "about_me": "", "facts": []}
    if machine:
        ctx["hardware"] = hardware()
        ctx["skills"] = skill_count()
    try:
        import codec_memory_upgrade as cmu
    except Exception as e:
        log.warning("About me and facts unavailable: %s", e)
        return ctx
    try:
        ctx["about_me"] = (cmu.load_identity() or "").strip()
    except Exception as e:
        log.warning("About me unreadable: %s", e)
    if facts:
        ex = tuple(s for s in (exclude_sources or ()) if s)
        kw = {}
        if ex:
            kw["exclude_sources"] = ex
        if origins is not None:
            kw["origins"] = tuple(origins)
        try:
            rows = cmu.query_valid_facts(limit=FACTS_LIMIT, **kw)
            ctx["facts"] = list(rows or [])
        except Exception as e:
            log.warning("facts unreadable: %s", e)
    return ctx


def chat_block(ctx: dict) -> tuple[str, int]:
    """Deep Chat's block for `ctx` (from gather): this Mac, the skill count, About me and the saved
    facts. Returns (text, number of facts in it); the text is "" when there is nothing to say."""
    parts = []
    head = []
    hw = str(ctx.get("hardware") or "").strip()
    try:
        skills = int(ctx.get("skills") or 0)
    except (TypeError, ValueError):
        skills = 0
    if hw:
        head.append(f"Hardware: {hw} (read from this Mac).")
    if skills > 0:
        head.append(f"Skills: {skills} installed and switched on.")
    if head:
        parts.append("## This Mac\n" + "\n".join(head))
    about = str(ctx.get("about_me") or "").strip()
    if about:
        if len(about) > CHAT_ABOUT_MAX_CHARS:
            about = about[:CHAT_ABOUT_MAX_CHARS].rstrip() + " …"
        parts.append("[ABOUT THE USER — their own words, from Settings > Memory > About me]\n"
                     + about + "\n[END ABOUT THE USER]")
    lines: list[str] = []
    used = 0
    for fact in ctx.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        key = " ".join(str(fact.get("key") or "").split())
        value = " ".join(str(fact.get("value") or "").split())  # one line each: a value cannot fake a block
        if not value:
            continue
        if len(value) > CHAT_FACT_MAX_CHARS:
            value = value[:CHAT_FACT_MAX_CHARS].rstrip() + " …"
        # A learned fact's key is a hash of its text (skills/fact_extract.py): the text says it all.
        line = f"- {value}" if (not key or key.startswith("learned:")) else f"- {key}: {value}"
        if used + len(line) > CHAT_FACTS_MAX_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    if lines:
        parts.append("[ACTIVE FACTS — saved about the user, newest first]\n" + "\n".join(lines) + "\n[END FACTS]")
    return "\n\n".join(parts), len(lines)
