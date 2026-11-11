# One sandbox profile file per run (2026-11-11)

## What
`codec_sandbox.per_call_profile(allow_network)` is a context manager: it creates a
0600 file `~/.codec/sandbox-<random>.sb` (`tempfile.mkstemp`, next to
`SANDBOX_PROFILE_PATH`), writes the profile `_write_sandbox_profile(allow_network)`
always wrote, yields the path and deletes the file when the run ends (also on a
timeout or an error). `codec_sandbox.run_sandboxed` and `skills/python_exec.run`
use it; `python_exec._sandbox_command` now takes the profile path as an argument.

## Why
Every run rewrote the one file `~/.codec/sandbox.sb`. Two runs that start at the
same time with different network rules raced, and the last writer won for both
(known issue 2026-09-28). The crew shell already writes one file per call
(`codec_agents._run_shell_sandboxed`, P-030); this uses the same pattern.

## What does not change
- The profile text, byte for byte (same builder, same arguments).
- `_write_sandbox_profile()` with no keyword arguments still writes
  `~/.codec/sandbox.sb`; nothing at run time calls it that way any more.
- The folder: `~/.codec`, which the profile does not let the sandbox write, so a
  sandboxed program cannot edit its own profile.

## Tests
`tests/test_python_exec.py`: two runs get two different files, both 0600, both
gone afterwards, the shared file is never written; two profiles open at once keep
their own network rule.

## Rollback
Revert the PR. A crash between create and delete can leave one small
`sandbox-*.sb` file in `~/.codec`; it holds no secret and is safe to delete.
