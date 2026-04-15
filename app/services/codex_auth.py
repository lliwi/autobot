"""Codex (ChatGPT subscription) authentication via oauth-cli-kit.

Wraps the library so the rest of the app has a single import point. Tokens are
stored on disk by oauth-cli-kit at ~/.local/share/oauth-cli-kit/auth/codex.json,
which is mounted on the `codex_auth` Docker volume for persistence.
"""
import logging
import threading
from contextlib import contextmanager

import httpx
from oauth_cli_kit import (
    OPENAI_CODEX_PROVIDER,
    OAuthToken,
    get_token,
    login_oauth_interactive,
)
from oauth_cli_kit import server as _ock_server
from oauth_cli_kit.storage import FileTokenStorage

logger = logging.getLogger(__name__)

ORIGINATOR = "autobot"
CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
CODEX_CLIENT_VERSION = "0.50.0"

# Models verified to work against chatgpt.com/backend-api/codex/responses with a
# ChatGPT subscription. OpenAI's /codex/models endpoint currently only advertises
# "gpt-5.2", so we merge this curated list with whatever the API returns so that
# the dashboard reflects the user's actual plan.
KNOWN_CODEX_MODELS = ["gpt-5.4", "gpt-5.4-mini", "gpt-5.3-codex", "gpt-5.2"]
FALLBACK_MODELS = KNOWN_CODEX_MODELS


def _storage() -> FileTokenStorage:
    return FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename)


def token_path() -> str:
    return str(_storage().get_token_path())


def is_logged_in() -> bool:
    try:
        token = _storage().load()
    except Exception:
        return False
    return token is not None and bool(token.access)


def load_token() -> OAuthToken | None:
    try:
        return _storage().load()
    except Exception as e:
        logger.warning("Failed to load codex token: %s", e)
        return None


def get_access_token() -> str:
    """Return a valid access token, refreshing it on disk if needed.

    Raises if not logged in.
    """
    token = get_token(provider=OPENAI_CODEX_PROVIDER, storage=_storage())
    return token.access


def get_account_id() -> str | None:
    token = load_token()
    return token.account_id if token else None


@contextmanager
def _bind_callback_server_to_all_interfaces():
    """Monkey-patch oauth_cli_kit to bind its callback server to 0.0.0.0 instead of 'localhost'.

    Inside Docker the library's default ``localhost`` bind is only reachable from
    inside the container, breaking the host browser's redirect even with a
    ``-p 1455:1455`` port mapping. We patch only for the duration of the login.
    """
    original = _ock_server._start_local_server

    def patched(state, on_code=None):
        try:
            server = _ock_server._OAuthServer(("0.0.0.0", 1455), state, on_code=on_code)
        except OSError as exc:
            return None, f"Local callback server failed to start: {exc}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, None

    _ock_server._start_local_server = patched
    try:
        yield
    finally:
        _ock_server._start_local_server = original


def login(print_fn=print, prompt_fn=input) -> OAuthToken:
    """Run the interactive OAuth/PKCE flow. Blocks until the browser callback arrives."""
    with _bind_callback_server_to_all_interfaces():
        return login_oauth_interactive(
            print_fn=print_fn,
            prompt_fn=prompt_fn,
            provider=OPENAI_CODEX_PROVIDER,
            originator=ORIGINATOR,
            storage=_storage(),
        )


_MODELS_CACHE: dict = {"ids": None}


def list_models(force_refresh: bool = False) -> list[str]:
    """Return the list of model IDs available on the user's Codex account.

    Queries the Codex backend's /codex/models endpoint. Cached in-process until
    refreshed. If the call fails or the user is not logged in, returns a
    conservative fallback so the UI stays usable.
    """
    if not force_refresh and _MODELS_CACHE["ids"] is not None:
        return _MODELS_CACHE["ids"]

    if not is_logged_in():
        return list(FALLBACK_MODELS)

    try:
        token = get_access_token()
        account_id = get_account_id() or ""
        response = httpx.get(
            CODEX_MODELS_URL,
            params={"client_version": CODEX_CLIENT_VERSION},
            headers={
                "Authorization": f"Bearer {token}",
                "chatgpt-account-id": account_id,
                "originator": ORIGINATOR,
                "OpenAI-Beta": "responses=experimental",
            },
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.warning("Failed to fetch Codex models: %s", e)
        return list(FALLBACK_MODELS)

    api_ids: list[str] = []
    raw_models = payload.get("models") if isinstance(payload, dict) else None
    if isinstance(raw_models, list):
        for m in raw_models:
            if isinstance(m, str):
                api_ids.append(m)
            elif isinstance(m, dict):
                mid = m.get("slug") or m.get("id") or m.get("name")
                if mid:
                    api_ids.append(mid)

    # Merge curated known-working models (not advertised by /codex/models) with
    # whatever extras the API returns, preserving insertion order.
    ids: list[str] = []
    seen: set[str] = set()
    for mid in [*KNOWN_CODEX_MODELS, *api_ids]:
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)

    _MODELS_CACHE["ids"] = ids
    return ids


def logout() -> bool:
    """Delete the token file. Returns True if a file was removed."""
    import os

    path = _storage().get_token_path()
    if path.exists():
        os.remove(path)
        _MODELS_CACHE["ids"] = None
        return True
    return False
