"""CODEC Skill Registry — lazy-loads skill modules on demand.

At startup, only parses skill files with `ast` to extract metadata
(SKILL_NAME, SKILL_DESCRIPTION, SKILL_TRIGGERS, SKILL_MCP_EXPOSE).
The actual module import happens on first invocation and is cached.
"""
import ast
import importlib.util
import logging
import os
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("codec")


def _extract_metadata(filepath: str) -> Optional[Dict[str, Any]]:
    """Parse a skill .py file with ast to extract module-level metadata
    without executing it.  Returns a dict or None if the file is not a
    valid skill (missing SKILL_TRIGGERS or run function)."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            source = f.read()
        tree = ast.parse(source, filename=filepath)
    except (SyntaxError, OSError) as e:
        log.warning("Skill metadata parse error (%s): %s", filepath, e)
        return None

    meta: Dict[str, Any] = {}
    has_run_func = False

    for node in ast.iter_child_nodes(tree):
        # Detect top-level: SKILL_NAME = "..."  etc.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "SKILL_NAME",
                    "SKILL_DESCRIPTION",
                    "SKILL_TRIGGERS",
                    "SKILL_MCP_EXPOSE",
                ):
                    try:
                        meta[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        pass
        # Detect def run(...)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "run":
                has_run_func = True

    if not has_run_func:
        return None

    return meta


class SkillRegistry:
    """Registry that stores skill metadata eagerly and imports modules lazily.

    Usage:
        registry = SkillRegistry(skills_dir)
        registry.scan()                   # fast — AST parse only
        meta = registry.get_metadata()    # list of dicts with name/desc/triggers
        mod  = registry.load("weather")   # first call imports; subsequent calls return cache
    """

    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        # name -> metadata dict  (always populated after scan)
        self._meta: Dict[str, Dict[str, Any]] = {}
        # name -> file path
        self._paths: Dict[str, str] = {}
        # name -> loaded module  (populated on demand)
        self._modules: Dict[str, Any] = {}

    def scan(self) -> int:
        """Scan skills directory and extract metadata via AST.
        Returns the number of skills discovered."""
        self._meta.clear()
        self._paths.clear()
        # Do NOT clear _modules — keep any already-loaded modules cached

        if not os.path.isdir(self.skills_dir):
            return 0

        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            filepath = os.path.join(self.skills_dir, fname)
            meta = _extract_metadata(filepath)
            if meta is None:
                continue
            name = meta.get("SKILL_NAME", fname[:-3])
            self._meta[name] = meta
            self._paths[name] = filepath

        log.info("Skill registry: %d skills discovered (metadata only)", len(self._meta))
        return len(self._meta)

    # ── Metadata access (no import needed) ──────────────────────────────

    def names(self) -> List[str]:
        return list(self._meta.keys())

    def get_meta(self, name: str) -> Optional[Dict[str, Any]]:
        return self._meta.get(name)

    def all_metadata(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._meta)

    def get_triggers(self, name: str) -> List[str]:
        meta = self._meta.get(name, {})
        return meta.get("SKILL_TRIGGERS", [])

    def get_description(self, name: str) -> str:
        meta = self._meta.get(name, {})
        return meta.get("SKILL_DESCRIPTION", name)

    def get_mcp_expose(self, name: str) -> Optional[bool]:
        meta = self._meta.get(name, {})
        return meta.get("SKILL_MCP_EXPOSE", None)

    # ── Lazy module loading ─────────────────────────────────────────────

    def load(self, name: str) -> Optional[Any]:
        """Import and cache the skill module.  Returns the module or None."""
        if name in self._modules:
            return self._modules[name]

        filepath = self._paths.get(name)
        if not filepath:
            log.warning("Skill '%s' not found in registry", name)
            return None

        try:
            mod_name = os.path.basename(filepath)[:-3]
            spec = importlib.util.spec_from_file_location(mod_name, filepath)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._modules[name] = mod
            log.info("Lazy-loaded skill: %s", name)
            return mod
        except Exception as e:
            log.warning("Skill import error (%s): %s", name, e)
            return None

    def is_loaded(self, name: str) -> bool:
        return name in self._modules

    # ── Convenience: run a skill by name ────────────────────────────────

    def run(self, name: str, task: str, *args, **kwargs) -> Optional[str]:
        """Load (if needed) and execute a skill's run() function."""
        mod = self.load(name)
        if mod is None or not hasattr(mod, "run"):
            return None
        return mod.run(task, *args, **kwargs)

    # ── Trigger matching (replaces check_skill) ─────────────────────────

    def match_trigger(self, task: str) -> Optional[str]:
        """Return the name of the first skill whose triggers match, or None."""
        low = task.lower()
        for name, meta in self._meta.items():
            triggers = meta.get("SKILL_TRIGGERS", [])
            if any(t in low for t in triggers):
                return name
        return None
