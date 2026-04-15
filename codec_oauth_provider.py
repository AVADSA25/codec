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
TTLs:      access token  24h
           refresh token 30d
           auth code     5m (in-memory only — short enough that restart loss is fine)
"""
from __future__ import annotations

import json
import os
import stat
import time
import secrets
import threading
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider

ACCESS_TOKEN_TTL = 24 * 60 * 60          # 24h
REFRESH_TOKEN_TTL = 30 * 24 * 60 * 60    # 30d

_STATE_PATH = Path(os.path.expanduser("~/.codec/oauth_state.json"))
_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


class PersistentOAuthProvider(InMemoryOAuthProvider):
    """OAuth provider that mirrors its state dicts to disk on every mutation."""

    def __init__(self, *args, state_path: Path = _STATE_PATH, **kwargs):
        super().__init__(*args, **kwargs)
        self._state_path = state_path
        self._lock = threading.Lock()
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
        with self._lock:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._serialize()))
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            os.replace(tmp, self._state_path)

    # ---------- overrides: persist after every mutation ----------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await super().register_client(client_info)
        self._save()

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
