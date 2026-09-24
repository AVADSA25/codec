"""Persistent OAuth 2.1 provider for CODEC MCP HTTP.

Subclasses FastMCP's InMemoryOAuthProvider and persists all four state dicts
(clients, auth_codes, access_tokens, refresh_tokens) to a JSON file on disk
so tokens survive service restarts. claude.ai stays connected across
`pm2 restart codec-mcp-http` without needing re-authorization.

Tokens are opaque (32-byte hex, stored server-side) rather than JWT — the user
requested "signing key on disk" for durability; opaque-with-disk achieves the
same durability property (survive restart) without the JWT machinery, and keeps
revocation trivially synchronous.

Storage:   ~/.codec/oauth_state.json   (0600)
TTLs:      access token  365d   (bumped 2026-05-28 from 30d to remove
                                 monthly re-auth prompts in claude.ai)
           refresh token 365d   (bumped 2026-05-28 from 90d for the same
                                 reason — annual re-auth at most)
           auth code     5m     (in-memory only — short enough that
                                 restart loss is fine)

Owner gate (2026-09-24): `authorize()` never issues a code by itself. The
SDK base class auto-approves every request, and Dynamic Client Registration
is open (claude.ai needs it), so before this gate anyone who reached the
public URL could register a client and walk away with a 1-year token. Now
`authorize()` parks the request and redirects to `/oauth/consent`, where the
owner must enter the CODEC PIN (`codec_mcp_consent.py`). Only
`approve_pending()` mints a code.
"""
from __future__ import annotations

import json
import os
import time
import secrets
import threading
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizationParams, AuthorizeError,
    RefreshToken, TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

from codec_jsonstore import atomic_write_json

try:
    from codec_audit import log_event as _oauth_log_event
except ImportError:  # pragma: no cover — audit unavailable shouldn't break OAuth
    def _oauth_log_event(*a, **kw):  # type: ignore[no-redef]
        pass


def _token_id(token_value: str) -> str:
    """Last 8 chars of an opaque token — safe to log as identifier."""
    return (token_value or "")[-8:]

# 2026-04-25: bumped access-token TTL from 24h → 30d so claude.ai connections
# don't go stale mid-week if the refresh flow doesn't fire.
# 2026-05-28: bumped again to 365d / 365d. The previous 30d access kept
# triggering monthly re-auth prompts in claude.ai. Threat model: anyone
# with read access to ~/.codec/oauth_state.json already has the user's
# machine, so TTL length is not the primary security control here —
# revocation is (clear the file, restart codec-mcp-http). The opaque
# server-side token can be invalidated at any moment.
ACCESS_TOKEN_TTL = 365 * 24 * 60 * 60    # 1 year (was 30d, originally 24h)
REFRESH_TOKEN_TTL = 365 * 24 * 60 * 60   # 1 year (was 90d, originally 30d)

# Owner gate: a parked /authorize request lives this long, and at most this
# many wait at once (oldest evicted) so unauthenticated callers can't grow
# the dict without bound.
PENDING_AUTH_TTL = 10 * 60
PENDING_AUTH_MAX = 50

_STATE_PATH = Path(os.path.expanduser("~/.codec/oauth_state.json"))
_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


class PersistentOAuthProvider(InMemoryOAuthProvider):
    """OAuth provider that mirrors its state dicts to disk on every mutation."""

    def __init__(self, *args, state_path: Path = _STATE_PATH, **kwargs):
        super().__init__(*args, **kwargs)
        self._state_path = state_path
        self._lock = threading.Lock()
        # rid -> (client, params, expires_at). RAM only: a restart drops
        # in-flight consents, and the user just clicks Connect again.
        self._pending: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams, float]] = {}
        self._pending_lock = threading.Lock()
        self._load()

    # ---------- persistence ----------

    def _serialize(self) -> dict[str, Any]:
        return {
            "clients": {k: v.model_dump(mode="json") for k, v in self.clients.items()},
            "access_tokens": {k: v.model_dump(mode="json") for k, v in self.access_tokens.items()},
            "refresh_tokens": {k: v.model_dump(mode="json") for k, v in self.refresh_tokens.items()},
            "access_to_refresh": dict(self._access_to_refresh_map),
            "refresh_to_access": dict(self._refresh_to_access_map),
        }

    def _load(self):
        # PR-2B (D-8 closure): prefer encrypted state from Keychain. Fall back
        # to the legacy `~/.codec/oauth_state.json` plaintext file ONLY for
        # one-shot migration on first post-PR-2B startup. After migration
        # the plaintext file is deleted (see _save).
        data = None
        try:
            from codec_keychain import get_oauth_state
            kc_blob = get_oauth_state()
            if kc_blob:
                data = json.loads(kc_blob)
        except Exception:
            data = None

        if data is None:
            # Legacy path: read plaintext file (will be migrated on first save).
            if not self._state_path.exists():
                return
            try:
                data = json.loads(self._state_path.read_text())
            except Exception:
                return
        try:
            self.clients = {
                k: OAuthClientInformationFull.model_validate(v)
                for k, v in data.get("clients", {}).items()
            }
            now = time.time()
            self.access_tokens = {
                k: AccessToken.model_validate(v)
                for k, v in data.get("access_tokens", {}).items()
                if v.get("expires_at") is None or v["expires_at"] > now
            }
            self.refresh_tokens = {
                k: RefreshToken.model_validate(v)
                for k, v in data.get("refresh_tokens", {}).items()
                if v.get("expires_at") is None or v["expires_at"] > now
            }
            self._access_to_refresh_map = {
                k: v for k, v in data.get("access_to_refresh", {}).items()
                if k in self.access_tokens and v in self.refresh_tokens
            }
            self._refresh_to_access_map = {
                k: v for k, v in data.get("refresh_to_access", {}).items()
                if k in self.refresh_tokens and v in self.access_tokens
            }
        except Exception:
            # Corrupt state — start fresh rather than crash.
            self.clients = {}
            self.access_tokens = {}
            self.refresh_tokens = {}
            self._access_to_refresh_map = {}
            self._refresh_to_access_map = {}

    def _save(self):
        # PR-2B (D-8 closure): write serialized state to Keychain. If the
        # legacy plaintext file exists from a pre-migration install, delete
        # it after the Keychain write succeeds. If Keychain is unavailable
        # (locked / not on macOS / fallback failed), fall back to the
        # legacy plaintext path so OAuth keeps working — operational
        # continuity > strict secret isolation.
        with self._lock:
            state = self._serialize()
            blob = json.dumps(state)
            kc_ok = False
            try:
                from codec_keychain import set_oauth_state
                kc_ok = set_oauth_state(blob)
            except Exception:
                kc_ok = False

            if kc_ok:
                # Successful Keychain write — remove legacy plaintext on disk.
                try:
                    if self._state_path.exists():
                        self._state_path.unlink()
                except Exception:
                    pass
                return

            # Fallback: legacy plaintext file. Crash-durable write via the
            # canonical jsonstore helper — unique tmp + flush + os.fsync +
            # atomic os.replace + chmod 0600. C6 (Fix #1b): the previous
            # `tmp.write_text(blob); os.replace(...)` skipped fsync, so a
            # crash between write() and the page-cache flush could land a
            # truncated/empty oauth_state.json and lose every token
            # (claude.ai forced re-auth on next restart). atomic_write_json
            # closes that durability window.
            atomic_write_json(self._state_path, state)

    # ---------- overrides: persist after every mutation ----------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        self._save()

    # ---------- owner gate ----------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the PIN consent page.

        Never returns a redirect carrying a code — that only happens in
        `approve_pending()` after the owner enters the PIN."""
        if client.client_id is None or client.client_id not in self.clients:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description="Client not registered.",
            )
        rid = secrets.token_urlsafe(32)
        now = time.time()
        with self._pending_lock:
            for k in [k for k, v in self._pending.items() if v[2] < now]:
                del self._pending[k]
            while len(self._pending) >= PENDING_AUTH_MAX:
                oldest = min(self._pending, key=lambda k: self._pending[k][2])
                del self._pending[oldest]
            self._pending[rid] = (client, params, now + PENDING_AUTH_TTL)
        self._emit_consent("oauth_consent_requested", client, "info")
        base = str(self.base_url).rstrip("/")
        return f"{base}/oauth/consent?rid={rid}"

    def pending_request(self, rid: str) -> dict | None:
        """Display info for a live parked request, or None."""
        with self._pending_lock:
            entry = self._pending.get(rid or "")
            if not entry or entry[2] < time.time():
                return None
            client, params, _ = entry
        return {
            "client_id": client.client_id,
            "client_name": client.client_name or "Unnamed client",
            "redirect_uri": str(params.redirect_uri),
        }

    def _pop_pending(self, rid: str):
        with self._pending_lock:
            entry = self._pending.pop(rid or "", None)
        if not entry or entry[2] < time.time():
            return None
        return entry

    async def approve_pending(self, rid: str) -> str | None:
        """Owner approved: mint the code and return the client redirect."""
        entry = self._pop_pending(rid)
        if entry is None:
            return None
        client, params, _ = entry
        url = await super().authorize(client, params)
        self._emit_consent("oauth_consent_granted", client, "info")
        return url

    def deny_pending(self, rid: str, reason: str = "denied") -> str | None:
        """Drop the request; return the client redirect with access_denied."""
        entry = self._pop_pending(rid)
        if entry is None:
            return None
        client, params, _ = entry
        self._emit_consent("oauth_consent_denied", client, "warning", reason=reason)
        return construct_redirect_uri(
            str(params.redirect_uri), error="access_denied", state=params.state
        )

    def _emit_consent(self, event: str, client, level: str, **extra) -> None:
        try:
            _oauth_log_event(
                event, "codec-oauth-provider",
                f"{event} for client {client.client_id}",
                client_id=client.client_id,
                outcome="ok" if level == "info" else "denied",
                level=level,
                extra={"client_name": client.client_name, **extra},
            )
        except Exception:
            pass

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Reimplement to get longer TTLs than the parent's 1h default.
        if authorization_code.code not in self.auth_codes:
            raise TokenError("invalid_grant", "Authorization code not found or already used.")
        del self.auth_codes[authorization_code.code]

        access_value = f"codec_at_{secrets.token_hex(32)}"
        refresh_value = f"codec_rt_{secrets.token_hex(32)}"
        now = time.time()

        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")

        self.access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + ACCESS_TOKEN_TTL),
        )
        self.refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(now + REFRESH_TOKEN_TTL),
        )
        self._access_to_refresh_map[access_value] = refresh_value
        self._refresh_to_access_map[refresh_value] = access_value
        self._save()

        # token_issued audit (one cid for the issue→refresh chain — covers
        # this issuance and any subsequent refreshes against the same chain).
        cid = secrets.token_hex(6)
        try:
            _oauth_log_event(
                "token_issued", "codec-oauth-provider",
                f"Access token issued for client {client.client_id}",
                client_id=client.client_id,
                extra={
                    "access_token_id": _token_id(access_value),
                    "refresh_token_id": _token_id(refresh_value),
                    "expires_in_sec": ACCESS_TOKEN_TTL,
                    "scope": " ".join(authorization_code.scopes),
                },
                correlation_id=cid,
            )
        except Exception:
            pass

        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh_value,
            scope=" ".join(authorization_code.scopes),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        original_scopes = set(refresh_token.scopes)
        if not set(scopes).issubset(original_scopes):
            raise TokenError(
                "invalid_scope",
                "Requested scopes exceed those authorized by the refresh token.",
            )

        # Capture the previous-access-id (looked up before we revoke) so
        # token_refreshed can pair the old/new ids in the audit log.
        previous_access_id = _token_id(
            self._refresh_to_access_map.get(refresh_token.token, "")
        )

        self._revoke_internal(refresh_token_str=refresh_token.token)

        access_value = f"codec_at_{secrets.token_hex(32)}"
        refresh_value = f"codec_rt_{secrets.token_hex(32)}"
        now = time.time()

        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")

        self.access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(now + ACCESS_TOKEN_TTL),
        )
        self.refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client.client_id,
            scopes=scopes,
            expires_at=int(now + REFRESH_TOKEN_TTL),
        )
        self._access_to_refresh_map[access_value] = refresh_value
        self._refresh_to_access_map[refresh_value] = access_value
        self._save()

        # token_refreshed audit. New cid per refresh; design §1.4 leaves
        # cross-refresh chaining for a follow-up (would need to persist the
        # original-issuance cid alongside the refresh_token to reuse it).
        cid = secrets.token_hex(6)
        try:
            _oauth_log_event(
                "token_refreshed", "codec-oauth-provider",
                f"Access token refreshed for client {client.client_id}",
                client_id=client.client_id,
                extra={
                    "access_token_id": _token_id(access_value),
                    "previous_id": previous_access_id,
                    "expires_in_sec": ACCESS_TOKEN_TTL,
                    "scope": " ".join(scopes),
                },
                correlation_id=cid,
            )
        except Exception:
            pass

        return OAuthToken(
            access_token=access_value,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh_value,
            scope=" ".join(scopes),
        )

    async def revoke_token(self, token) -> None:
        await super().revoke_token(token)
        self._save()

    # ---------- audit-only helpers — invoked by ops paths ----------

    def emit_token_expired(self, access_token_id: str, client_id: str | None,
                           age_seconds: float | int | None = None) -> None:
        """Emit token_expired when a token's TTL check fails on validate.
        Caller passes the last-8 of the access token, the client_id if known,
        and the token's age in seconds at expiry."""
        try:
            _oauth_log_event(
                "token_expired", "codec-oauth-provider",
                f"Access token expired for client {client_id or 'unknown'}",
                client_id=client_id,
                outcome="denied", level="warning",
                extra={
                    "access_token_id": access_token_id,
                    "age_seconds": age_seconds,
                },
            )
        except Exception:
            pass

    def emit_state_invalidated(self, reason: str, tokens_cleared: int = 0) -> None:
        """Emit oauth_state_invalidated for admin clear / corruption /
        manual delete events. `reason` should be one of:
            'admin_clear' | 'corruption' | 'manual_delete'
        """
        try:
            _oauth_log_event(
                "oauth_state_invalidated", "codec-oauth-provider",
                f"OAuth state invalidated: {reason}",
                outcome="warning", level="warning",
                extra={"reason": reason, "tokens_cleared": tokens_cleared},
            )
        except Exception:
            pass
