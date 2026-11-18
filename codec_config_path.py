"""Where CODEC's config.json is (docs/TEST-CONFIG-ISOLATION-DESIGN.md).

Every module that reads or writes the config calls `config_path()` when it opens the
file. It answers `codec_config.CONFIG_PATH` when codec_config is loaded (the one name
tests patch) and otherwise the same rule codec_config uses, without importing it: a
process that never loaded codec_config (the watchdog, the observer) does not start
reading the Keychain just to find the file, and modules codec_config itself imports
(codec_models) get no import cycle. Standard library only.

CODEC_CONFIG_PATH is for the test suite only (tests/conftest.py points it at a
missing temp file); nothing in CODEC sets it. Unset, the path is
os.path.expanduser("~/.codec/config.json"), the path every module built before.
"""
import os
import sys

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.codec/config.json")


def from_env() -> str:
    """CODEC_CONFIG_PATH when set (tests only), else ~/.codec/config.json."""
    override = os.environ.get("CODEC_CONFIG_PATH", "")
    return os.path.expanduser(override) if override else DEFAULT_CONFIG_PATH


def config_path() -> str:
    """The config.json to open now: codec_config.CONFIG_PATH when loaded, else from_env()."""
    mod = sys.modules.get("codec_config")
    path = getattr(mod, "CONFIG_PATH", None) if mod is not None else None
    return str(path) if path else from_env()
