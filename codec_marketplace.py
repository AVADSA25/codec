#!/usr/bin/env python3
"""
CODEC Skill Marketplace — Install, search, publish, and manage community skills.

Usage:
    python3 codec_marketplace.py install <skill-name>
    python3 codec_marketplace.py search <query>
    python3 codec_marketplace.py list
    python3 codec_marketplace.py update
    python3 codec_marketplace.py remove <skill-name>
    python3 codec_marketplace.py info <skill-name>
    python3 codec_marketplace.py publish <file.py>

Or via CODEC voice: "Hey CODEC, install bitcoin price skill"
"""
import json
import os
import sys
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
REGISTRY_URL   = "https://raw.githubusercontent.com/AVADSA25/codec-skills/main/registry.json"
SKILLS_DIR     = os.path.expanduser("~/.codec/skills")
MARKETPLACE_META = os.path.join(SKILLS_DIR, ".marketplace.json")
CACHE_DIR      = os.path.expanduser("~/.codec/marketplace_cache")

os.makedirs(SKILLS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR,  exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_marketplace_meta() -> dict:
    """Load local tracking of installed marketplace skills."""
    if os.path.exists(MARKETPLACE_META):
        try:
            with open(MARKETPLACE_META) as f:
                return json.load(f)
        except Exception:
            pass
    return {"installed": {}, "last_update": ""}


def _save_marketplace_meta(meta: dict) -> None:
    with open(MARKETPLACE_META, "w") as f:
        json.dump(meta, f, indent=2)


def _load_cached_registry() -> dict:
    cache_path = os.path.join(CACHE_DIR, "registry.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception:
            pass
    # Minimal built-in fallback so the tool is still usable offline
    return {"skills": [], "categories": []}


def _fetch_registry(silent: bool = False) -> dict:
    """Fetch the skill registry from GitHub, fall back to cache on error."""
    try:
        import requests
        r = requests.get(REGISTRY_URL, timeout=15)
        if r.status_code == 200:
            data = r.json()
            with open(os.path.join(CACHE_DIR, "registry.json"), "w") as f:
                json.dump(data, f, indent=2)
            return data
        if not silent:
            print(f"Registry fetch failed: HTTP {r.status_code} — using cached registry")
        return _load_cached_registry()
    except Exception as e:
        if not silent:
            print(f"Network error: {e} — using cached registry")
        return _load_cached_registry()


def _download_skill(skill_entry: dict, registry: dict) -> str | None:
    """Download a skill .py file from GitHub raw."""
    try:
        import requests
        base_url = registry.get("base_url", "")
        url      = f"{base_url}/{skill_entry['file']}"
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            return r.text
        print(f"Download failed: HTTP {r.status_code} from {url}")
        return None
    except Exception as e:
        print(f"Download error: {e}")
        return None


def _install_deps(deps: list) -> None:
    for dep in deps:
        try:
            __import__(dep.replace("-", "_"))
        except ImportError:
            print(f"  Installing dependency: {dep}")
            os.system(f"pip3.13 install {dep} --break-system-packages --quiet 2>/dev/null || pip3 install {dep} --quiet")


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_install(name: str, interactive: bool = True) -> bool:
    """Install a skill from the marketplace."""
    registry = _fetch_registry()
    skills   = registry.get("skills", [])

    # Find skill by name or display_name
    match = next(
        (s for s in skills
         if s["name"] == name or s.get("display_name", "").lower() == name.lower()),
        None
    )
    if not match:
        # Try fuzzy: partial name match
        match = next(
            (s for s in skills if name.lower() in s["name"].lower()),
            None
        )

    if not match:
        print(f"❌ Skill '{name}' not found in marketplace.")
        print(f"   Try: codec search {name.split('-')[0]}")
        return False

    # Already installed at same version?
    meta = _load_marketplace_meta()
    existing = meta["installed"].get(match["name"])
    if existing and existing.get("version") == match["version"]:
        print(f"✅ '{match['display_name']}' v{match['version']} is already installed.")
        return True
    if existing:
        print(f"⬆️  Updating '{match['display_name']}' from v{existing.get('version','?')} → v{match['version']}…")

    # Install dependencies
    deps = match.get("dependencies", [])
    if deps:
        print(f"📦 Dependencies: {', '.join(deps)}")
        _install_deps(deps)

    print(f"\n  Name:        {match['display_name']}")
    print(f"  Author:      {match['author']}" + ("  ✓ verified" if match.get("verified") else ""))
    print(f"  Description: {match['description']}")
    print(f"  Triggers:    {', '.join(match.get('triggers', [])[:4])}")
    print(f"  Category:    {match.get('category', 'general')}")

    if not match.get("verified"):
        print("\n  ⚠️  Community skill — not verified by AVA Digital. Review before production use.")

    if interactive:
        try:
            confirm = input("\nInstall? [Y/n] ").strip().lower()
            if confirm == "n":
                print("Cancelled.")
                return False
        except (EOFError, KeyboardInterrupt):
            pass  # Non-interactive (voice/tests) — proceed

    print(f"\nDownloading '{match['display_name']}' v{match['version']}…")
    code = _download_skill(match, registry)
    if not code:
        return False

    py_name = match["name"].replace("-", "_") + ".py"
    dest    = os.path.join(SKILLS_DIR, py_name)
    with open(dest, "w") as f:
        f.write(code)

    meta["installed"][match["name"]] = {
        "version":      match["version"],
        "file":         py_name,
        "installed_at": datetime.now().isoformat(),
        "author":       match["author"],
        "verified":     match.get("verified", False),
    }
    _save_marketplace_meta(meta)

    print(f"\n✅ '{match['display_name']}' installed → {dest}")
    print(f"   Restart CODEC to activate: pm2 restart ava-autopilot")
    return True


def cmd_search(query: str) -> None:
    """Search for skills in the marketplace."""
    registry = _fetch_registry()
    q        = query.lower()
    results  = [
        s for s in registry.get("skills", [])
        if q in f"{s['name']} {s.get('display_name','')} {s.get('description','')} {' '.join(s.get('triggers',[]))} {s.get('category','')}".lower()
    ]

    if not results:
        print(f"No skills found for '{query}'. Try a broader term.")
        return

    meta = _load_marketplace_meta()
    print(f"\n🔍  {len(results)} skill(s) matching '{query}':\n")
    for s in results:
        installed = " ✅ installed" if s["name"] in meta.get("installed", {}) else ""
        verified  = " ✓" if s.get("verified") else ""
        print(f"  {s['name']:<26} {s.get('display_name','')}{verified}{installed}")
        print(f"  {'':<26} {s.get('description','')}")
        print(f"  {'':<26} triggers: {', '.join(s.get('triggers',[])[:3])}")
        print()
    print(f"  Install: codec install <skill-name>")


def cmd_list() -> None:
    """List all installed skills (built-in + marketplace)."""
    meta             = _load_marketplace_meta()
    marketplace_files = {v.get("file") for v in meta.get("installed", {}).values()}

    rows = []
    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        source = "marketplace" if fname in marketplace_files else "built-in"
        rows.append((fname[:-3], source))

    print(f"\n📦  CODEC Skills ({len(rows)} installed):\n")
    for name, source in rows:
        icon = "🌐" if source == "marketplace" else "📦"
        print(f"  {icon}  {name:<32} ({source})")

    built_in    = sum(1 for _, s in rows if s == "built-in")
    marketplace = sum(1 for _, s in rows if s == "marketplace")
    print(f"\n  📦 Built-in: {built_in}   🌐 Marketplace: {marketplace}")
    print(f"  Browse: codec search <query>  |  Install: codec install <name>")


def cmd_update() -> None:
    """Update all marketplace skills to latest versions."""
    registry  = _fetch_registry()
    meta      = _load_marketplace_meta()
    installed = meta.get("installed", {})

    if not installed:
        print("No marketplace skills installed.")
        return

    updated = 0
    for name, info in installed.items():
        remote = next((s for s in registry.get("skills", []) if s["name"] == name), None)
        if not remote:
            print(f"  {name}: not in registry (may have been removed)")
            continue
        if remote["version"] == info.get("version"):
            print(f"  {name}: up to date (v{info.get('version')})")
            continue

        print(f"  ⬆️  {name}: v{info.get('version','?')} → v{remote['version']}")
        code = _download_skill(remote, registry)
        if code:
            with open(os.path.join(SKILLS_DIR, info["file"]), "w") as f:
                f.write(code)
            meta["installed"][name]["version"]    = remote["version"]
            meta["installed"][name]["updated_at"] = datetime.now().isoformat()
            updated += 1

    if updated:
        meta["last_update"] = datetime.now().isoformat()
        _save_marketplace_meta(meta)
        print(f"\n✅ Updated {updated} skill(s). Restart CODEC: pm2 restart ava-autopilot")
    else:
        print("\nAll marketplace skills are up to date.")


def cmd_remove(name: str) -> None:
    """Remove a marketplace skill."""
    meta = _load_marketplace_meta()
    if name not in meta.get("installed", {}):
        print(f"'{name}' is not a marketplace skill. Use 'codec list' to see installed skills.")
        return

    info     = meta["installed"][name]
    filepath = os.path.join(SKILLS_DIR, info["file"])

    try:
        confirm = input(f"Remove '{name}' ({info['file']})? [Y/n] ").strip().lower()
        if confirm == "n":
            print("Cancelled.")
            return
    except (EOFError, KeyboardInterrupt):
        pass

    if os.path.exists(filepath):
        os.remove(filepath)

    del meta["installed"][name]
    _save_marketplace_meta(meta)
    print(f"✅ Removed '{name}'. Restart CODEC: pm2 restart ava-autopilot")


def cmd_info(name: str) -> None:
    """Show detailed info about a marketplace skill."""
    registry = _fetch_registry()
    skill    = next((s for s in registry.get("skills", []) if s["name"] == name), None)

    if not skill:
        print(f"Skill '{name}' not found in marketplace.")
        return

    meta      = _load_marketplace_meta()
    installed = name in meta.get("installed", {})

    print(f"\n📦  {skill.get('display_name', name)}")
    print(f"    Name:         {skill['name']}")
    print(f"    Version:      {skill['version']}")
    print(f"    Author:       {skill['author']}" + ("  ✓ verified" if skill.get("verified") else ""))
    print(f"    Category:     {skill.get('category', 'general')}")
    print(f"    Description:  {skill['description']}")
    print(f"    Triggers:     {', '.join(skill.get('triggers', []))}")
    print(f"    Dependencies: {', '.join(skill.get('dependencies', [])) or 'none'}")
    print(f"    Status:       {'✅ installed' if installed else 'not installed'}")
    if installed:
        info = meta["installed"][name]
        print(f"    Installed:    {info.get('installed_at', '')[:10]}")
    print()


def cmd_publish(filepath: str) -> None:
    """Guide user through publishing a skill to the marketplace."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    import re
    with open(filepath) as f:
        code = f.read()

    issues = []
    if "SKILL_TRIGGERS" not in code:   issues.append("Missing SKILL_TRIGGERS = [...]")
    if "SKILL_DESCRIPTION" not in code: issues.append("Missing SKILL_DESCRIPTION = '...'")
    if "def run(" not in code:          issues.append("Missing def run(task, context=None)")

    triggers_match = re.search(r"SKILL_TRIGGERS\s*=\s*\[([^\]]+)\]", code)
    trigger_count  = len(triggers_match.group(1).split(",")) if triggers_match else 0

    print(f"\n🚀  CODEC Skill Publish Guide")
    print("=" * 42)

    if issues:
        print("\n⚠️  Issues found in your skill file:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print(f"\n✅ Skill format looks good! ({trigger_count} triggers found)")

    print("""
To publish to the CODEC marketplace:

1. Fork  https://github.com/AVADSA25/codec-skills
2. Create skills/your-skill-name/your_skill.py
3. Create skills/your-skill-name/skill.json with metadata
4. Add your entry to registry.json
5. Open a Pull Request

skill.json template:""")
    basename = os.path.basename(filepath).replace(".py", "")
    print(json.dumps({
        "name": basename.replace("_", "-"),
        "display_name": basename.replace("_", " ").title(),
        "description": "What this skill does",
        "version": "1.0.0",
        "author": "your-github-username",
        "author_github": "your-github-username",
        "triggers": ["trigger one", "trigger two", "trigger three"],
        "category": "utility",
        "dependencies": [],
        "file": f"{basename.replace('_','-')}/{basename}.py",
        "verified": False
    }, indent=2))
    print(f"\n  Guidelines: https://github.com/AVADSA25/codec-skills/blob/main/CONTRIBUTING.md")


# ── CODEC Skill (voice-accessible) ──────────────────────────────────────────

SKILL_NAME        = "marketplace"
SKILL_TRIGGERS    = [
    "install skill", "marketplace", "skill marketplace", "search skills",
    "browse skills", "available skills", "codec install", "skill store",
    "download skill", "find skill"
]
SKILL_DESCRIPTION = "Browse and install skills from the CODEC Skill Marketplace"


def run(task: str, context: str = "") -> str:
    """Voice-accessible marketplace entry point."""
    lower = task.lower()

    if any(w in lower for w in ["install ", "download "]):
        for prefix in ["install skill ", "install ", "download skill ", "download "]:
            if prefix in lower:
                name = lower.split(prefix, 1)[1].strip().rstrip(".").replace(" ", "-")
                registry = _fetch_registry(silent=True)
                match = next(
                    (s for s in registry.get("skills", []) if s["name"] == name or name in s["name"]),
                    None
                )
                if not match:
                    return f"Skill '{name}' not found in marketplace. Say 'search skills {name}' to browse."
                code = _download_skill(match, registry)
                if not code:
                    return f"Failed to download '{name}' — check your internet connection."
                py_name = match["name"].replace("-", "_") + ".py"
                dest    = os.path.join(SKILLS_DIR, py_name)
                with open(dest, "w") as f:
                    f.write(code)
                meta = _load_marketplace_meta()
                meta["installed"][match["name"]] = {
                    "version":      match["version"],
                    "file":         py_name,
                    "installed_at": datetime.now().isoformat(),
                    "author":       match["author"],
                    "verified":     match.get("verified", False),
                }
                _save_marketplace_meta(meta)
                return f"Installed {match['display_name']} v{match['version']}. Restart CODEC to activate."

    if any(w in lower for w in ["search ", "find ", "browse "]):
        for prefix in ["search skills ", "search skill ", "search ", "find skill ", "find ", "browse skills ", "browse "]:
            if prefix in lower:
                query    = lower.split(prefix, 1)[1].strip().rstrip(".")
                registry = _fetch_registry(silent=True)
                results  = [
                    s for s in registry.get("skills", [])
                    if query in f"{s['name']} {s.get('description','')} {' '.join(s.get('triggers',[]))}".lower()
                ]
                if results:
                    lines = [f"Found {len(results)} skill(s) for '{query}':"]
                    for s in results[:5]:
                        v = " (verified)" if s.get("verified") else ""
                        lines.append(f"  {s['name']} — {s['description']}{v}")
                    lines.append("Say 'install [name]' to install one.")
                    return "\n".join(lines)
                return f"No skills found for '{query}'. Try a broader search term."

    # Default: marketplace summary
    registry = _fetch_registry(silent=True)
    total    = len(registry.get("skills", []))
    meta     = _load_marketplace_meta()
    inst     = len(meta.get("installed", {}))
    cats     = registry.get("categories", [])
    return (
        f"CODEC Skill Marketplace: {total} skills available, {inst} installed.\n"
        f"Categories: {', '.join(cats[:6])}.\n"
        f"Say 'search skills [topic]' or 'install skill [name]'."
    )


# ── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    HELP = """\
CODEC Skill Marketplace
═══════════════════════════════════════
  codec install <name>    Install a skill
  codec search  <query>   Search available skills
  codec list              List all installed skills
  codec update            Update all marketplace skills
  codec remove  <name>    Uninstall a marketplace skill
  codec info    <name>    Show skill details
  codec publish <file.py> Publishing guide
"""

    if len(sys.argv) < 2:
        print(HELP)
        sys.exit(0)

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    commands = {
        "install": lambda: cmd_install(arg),
        "search":  lambda: cmd_search(arg),
        "list":    lambda: cmd_list(),
        "update":  lambda: cmd_update(),
        "remove":  lambda: cmd_remove(arg),
        "info":    lambda: cmd_info(arg),
        "publish": lambda: cmd_publish(arg),
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"Unknown command: {cmd}\n")
        print(HELP)
        sys.exit(1)
