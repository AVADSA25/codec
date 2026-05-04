"""CODEC Google Auth — single source of truth for OAuth token management.

All Google skills MUST use get_credentials() from this module instead of
loading/refreshing tokens themselves. This prevents scope stripping when
multiple skills refresh and overwrite the token file.
"""
import json
import os
import threading
import logging

log = logging.getLogger("codec")

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")
CREDS_PATH = os.path.expanduser("~/.codec/google_credentials.json")

# All scopes CODEC needs — this is the canonical list
ALL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/tasks",
]

_lock = threading.Lock()
_cached_creds = None


def get_credentials():
    """Return valid Google OAuth credentials with all CODEC scopes.

    Thread-safe. Caches credentials in memory. Refreshes automatically
    when expired. Preserves ALL scopes on refresh (the whole point of
    this module).
    """
    global _cached_creds

    with _lock:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        # Use cached if still valid
        if _cached_creds and not _cached_creds.expired:
            return _cached_creds

        if not os.path.exists(TOKEN_PATH):
            raise FileNotFoundError(
                f"Google token not found at {TOKEN_PATH}. "
                f"Run: python3 reauth_google.py"
            )

        creds = Credentials.from_authorized_user_file(TOKEN_PATH)

        # Check if scopes are complete
        token_scopes = set(creds.scopes or [])
        missing = set(ALL_SCOPES) - token_scopes
        if missing:
            log.warning(
                "[Google Auth] Token missing scopes: %s. "
                "Run: python3 reauth_google.py",
                ", ".join(s.split("/")[-1] for s in missing),
            )

        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds)
                log.info("[Google Auth] Token refreshed successfully")
            except Exception as e:
                log.error("[Google Auth] Token refresh failed: %s", e)
                raise

        _cached_creds = creds
        return creds


def _save_token(creds):
    """Save token preserving ALL scopes from the canonical list.

    The key fix: when Google returns a refreshed token, it may only
    include the scopes that were in the request. We force ALL_SCOPES
    into the saved token so no scope is ever lost.
    """
    try:
        token_data = json.loads(creds.to_json())
        # Force all scopes into the saved token
        token_data["scopes"] = ALL_SCOPES
        with open(TOKEN_PATH, "w") as f:
            json.dump(token_data, f, indent=2)
        os.chmod(TOKEN_PATH, 0o600)
    except Exception as e:
        log.error("[Google Auth] Token save failed: %s", e)


def invalidate_cache():
    """Force next call to reload from disk (e.g. after reauth_google.py)."""
    global _cached_creds
    with _lock:
        _cached_creds = None


def build_service(api, version):
    """Convenience: build a Google API service with valid credentials."""
    from googleapiclient.discovery import build
    return build(api, version, credentials=get_credentials())
