"""Helper for configuring the Matrix channel.

Given the handful of credentials that the admin already has (homeserver URL,
bot user id, password, optional allowlists), this module can:

  * validate the credentials by attempting a real login against the homeserver,
  * persist them by rewriting the project ``.env`` file in place (preserving
    comments and unrelated keys),
  * expose the resulting configuration as a plain dict so CLI and
    dashboard callers can report it back to the operator.

Reusable from the ``flask onboard`` CLI, from a dashboard form, or from a
tool handler.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MATRIX_ENV_KEYS = (
    "MATRIX_HOMESERVER",
    "MATRIX_USER_ID",
    "MATRIX_PASSWORD",
    "MATRIX_ALLOWED_ROOMS",
    "MATRIX_ALLOWED_USERS",
    "MATRIX_ALLOWED_DM_USERS",
    "MATRIX_GROUP_POLICY",
)

VALID_POLICIES = ("always", "mention", "allowlist")


def test_connection(homeserver: str, user_id: str, password: str,
                    timeout: float = 10.0) -> tuple[bool, str]:
    """Try to log in against the homeserver. Returns (ok, message).

    Uses a one-shot matrix-nio client; logs out afterwards to avoid leaving
    orphaned sessions on the homeserver.
    """
    if not (homeserver and user_id and password):
        return False, "homeserver, user_id and password are required"
    if not (homeserver.startswith("http://") or homeserver.startswith("https://")):
        return False, "homeserver must start with http:// or https://"
    if not user_id.startswith("@") or ":" not in user_id:
        return False, "user_id must look like @user:homeserver.tld"

    async def _probe() -> tuple[bool, str]:
        from nio import AsyncClient, LoginError, LoginResponse

        client = AsyncClient(homeserver, user_id)
        try:
            resp = await asyncio.wait_for(client.login(password), timeout=timeout)
        except asyncio.TimeoutError:
            return False, f"login timed out after {timeout:.0f}s"
        except Exception as e:
            return False, f"login failed: {e}"
        finally:
            try:
                await client.close()
            except Exception:
                pass

        if isinstance(resp, LoginResponse):
            try:
                await client.logout()
            except Exception:
                pass
            return True, f"login ok (device_id={resp.device_id})"
        if isinstance(resp, LoginError):
            return False, f"login rejected: {resp.message}"
        return False, f"unexpected response: {resp}"

    try:
        return asyncio.run(_probe())
    except RuntimeError as e:
        return False, f"could not run probe: {e}"


def update_env_file(env_path: Path | str, updates: dict[str, str]) -> Path:
    """Write ``updates`` into ``env_path``, preserving unrelated lines.

    Existing keys are replaced in place (same line number). Missing keys are
    appended at the end under a ``# Matrix (updated)`` banner. Comments and
    blank lines are preserved verbatim. If ``env_path`` does not exist, it is
    created with just the provided keys.
    """
    path = Path(env_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    out_lines: list[str] = []
    for line in existing_lines:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out_lines.append(f"{key}={remaining.pop(key)}")
                continue
        out_lines.append(line)

    if remaining:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        out_lines.append("# Matrix (updated by matrix_setup)")
        for key, value in remaining.items():
            out_lines.append(f"{key}={value}")

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return path


def configure(
    homeserver: str,
    user_id: str,
    password: str,
    allowed_rooms: str | list[str] | None = None,
    allowed_users: str | list[str] | None = None,
    allowed_dm_users: str | list[str] | None = None,
    group_policy: str = "mention",
    env_path: Path | str | None = None,
    validate: bool = True,
) -> dict:
    """Validate + persist Matrix credentials.

    Args:
        homeserver: e.g. ``https://matrix.org``.
        user_id: full MXID, e.g. ``@autobot:matrix.org``.
        password: bot account password.
        allowed_rooms / allowed_users: optional allowlists. Accepts either a
            list or a comma-separated string.
        allowed_dm_users: optional allowlist scoped to DMs (rooms with ≤2
            members). If empty, DMs fall back to ``allowed_users``.
        group_policy: one of ``always``, ``mention``, ``allowlist``.
        env_path: path to the ``.env`` file (defaults to ``<repo>/.env``).
        validate: if True, attempt a real login before writing. Skip only when
            you already know the credentials work (e.g. from a prior run).

    Returns a dict ``{ok, message, env_path, values}`` ready to be rendered in
    the CLI or dashboard. Never raises — all errors are returned in the dict.
    """
    if group_policy not in VALID_POLICIES:
        return {
            "ok": False,
            "message": f"group_policy must be one of {VALID_POLICIES}",
            "env_path": None,
            "values": None,
        }

    def _as_csv(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return ",".join(s.strip() for s in value.split(",") if s.strip())
        return ",".join(str(s).strip() for s in value if str(s).strip())

    allowed_rooms_str = _as_csv(allowed_rooms)
    allowed_users_str = _as_csv(allowed_users)
    allowed_dm_users_str = _as_csv(allowed_dm_users)

    if validate:
        ok, msg = test_connection(homeserver, user_id, password)
        if not ok:
            return {"ok": False, "message": msg, "env_path": None, "values": None}
    else:
        msg = "validation skipped"

    if env_path is None:
        env_path = Path(__file__).resolve().parents[2] / ".env"
    else:
        env_path = Path(env_path)

    values = {
        "MATRIX_HOMESERVER": homeserver,
        "MATRIX_USER_ID": user_id,
        "MATRIX_PASSWORD": password,
        "MATRIX_ALLOWED_ROOMS": allowed_rooms_str,
        "MATRIX_ALLOWED_USERS": allowed_users_str,
        "MATRIX_ALLOWED_DM_USERS": allowed_dm_users_str,
        "MATRIX_GROUP_POLICY": group_policy,
    }

    try:
        written_path = update_env_file(env_path, values)
    except OSError as e:
        return {"ok": False, "message": f"cannot write env file: {e}", "env_path": str(env_path), "values": None}

    redacted = dict(values)
    redacted["MATRIX_PASSWORD"] = "***" if password else ""
    logger.info("Matrix credentials written to %s", written_path)
    return {
        "ok": True,
        "message": msg,
        "env_path": str(written_path),
        "values": redacted,
    }
