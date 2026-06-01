"""CODEC Cookbook — local-model lifecycle management (scan → recommend →
download → serve → list → stop) for the M1 Ultra workstation.

This is the NON-skill helper package. All OS / subprocess / PM2 / network work
lives here so the six `skills/cookbook_*.py` skill files stay thin (import only
this package + stdlib-safe modules) and therefore pass the `SkillRegistry`
load-time AST safety gate (`codec_config.is_dangerous_skill_code`, which
forbids `os`/`subprocess`/`socket`/... in skill files).

HARD SAFETY CONTRACT (enforced in serve.py):
  * Cookbook only ever stops a PM2 process it started, in the `cookbook-`
    namespace, after explicit confirm=True.
  * It never binds to or stops the protected ports (8083/8090/8094/9223/5678)
    or any port currently bound by a non-cookbook process (live `pm2 jlist`
    + socket probe at call time).
  * Its own serve range is 8110-8119.
  * It never issues docker stop/rm, never changes an existing service's port,
    never restarts a running service.
"""

__all__ = ["catalog", "probe", "fit", "serve", "download"]
