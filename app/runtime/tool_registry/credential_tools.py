"""Credential read/write tools (agent-scoped + global lookup)."""
from app.runtime.tool_registry.core import ToolDefinition, register


def register_credential_tools():
    register(
        ToolDefinition(
            name="get_credential",
            description=(
                "Fetch a decrypted secret by name. Lookup order: credentials scoped to this agent "
                "first, then global. Response shape depends on credential type: "
                "{type: 'token', name, value} for API keys/tokens; "
                "{type: 'user_password', name, username, password} for username+password pairs. "
                "Treat values as sensitive — never echo them back to the user or log them. "
                "Use the value as-is: do NOT validate its prefix, length, or format based on "
                "your prior knowledge of what that provider's tokens should look like. Provider "
                "token formats change (e.g. Notion moved from 'secret_' to 'ntn_' in 2024). "
                "If the downstream API rejects the credential, report the API's exact error "
                "message verbatim — do not speculate about format."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Credential name, e.g. 'github_token'."}
                },
                "required": ["name"],
            },
            handler=lambda **kwargs: _get_credential(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="list_credentials",
            description=(
                "List credential names available to this agent (names + descriptions only — "
                "values are never returned). Includes agent-scoped and global credentials."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda **kwargs: _list_credentials(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="set_credential",
            description=(
                "Create or update an agent-scoped credential. Encrypted at rest. Use when the "
                "user shares a secret in chat so future runs can reuse it. Two shapes: "
                "credential_type='token' stores a single value (API key, token). "
                "credential_type='user_password' stores a username+password pair — pass the "
                "password in 'value' and the login in 'username'. Agents cannot create global "
                "credentials — that's admin-only from the dashboard."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Unique name, letters/digits/._- only."},
                    "value": {"type": "string", "description": "Secret to store. For user_password this is the password."},
                    "credential_type": {
                        "type": "string",
                        "enum": ["token", "user_password"],
                        "description": "'token' (default) or 'user_password'.",
                    },
                    "username": {
                        "type": "string",
                        "description": "Login/username. Required when credential_type is 'user_password'.",
                    },
                    "description": {"type": "string", "description": "Optional human-readable note."},
                },
                "required": ["name", "value"],
            },
            handler=lambda **kwargs: _set_credential(**kwargs),
        )
    )

    register(
        ToolDefinition(
            name="delete_credential",
            description=(
                "Delete an agent-scoped credential by name. Does not touch global credentials "
                "(those are managed from the dashboard by the admin)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Credential name to delete."}
                },
                "required": ["name"],
            },
            handler=lambda **kwargs: _delete_credential(**kwargs),
        )
    )


def _get_credential(_agent=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not name:
        return {"error": "Missing required argument 'name'"}
    from app.services.credential_service import CredentialError, get_credential_pair

    try:
        pair = get_credential_pair(name, agent_id=_agent.id)
    except CredentialError as e:
        return {"error": str(e)}
    if pair is None:
        return {"error": f"Credential '{name}' not found"}
    return {"name": name, **pair}


def _list_credentials(_agent=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    import os
    from app.services.credential_service import (
        CredentialError,
        list_credentials,
        _ENV_PREFIX,
    )

    try:
        rows = list_credentials(agent_id=_agent.id)
    except CredentialError as e:
        return {"error": str(e)}
    items = [
        {
            "name": r.name,
            "description": r.description,
            "type": r.credential_type,
            "username": r.username if r.credential_type == "user_password" else None,
            "scope": "agent" if r.agent_id == _agent.id else "global",
            "source": "db",
        }
        for r in rows
    ]
    seen_db_names = {r.name for r in rows}
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(_ENV_PREFIX) or not env_val:
            continue
        name = env_key[len(_ENV_PREFIX):].lower()
        if not name or name in seen_db_names:
            continue
        items.append({
            "name": name,
            "description": f"Provided via .env (var {env_key})",
            "type": "token",
            "username": None,
            "scope": "env",
            "source": "env",
        })
    return {"credentials": items}


def _set_credential(_agent=None, name=None, value=None, description=None,
                    credential_type=None, username=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    missing = [k for k, v in (("name", name), ("value", value)) if not v]
    if missing:
        return {"error": f"Missing required argument(s): {', '.join(missing)}"}
    from app.services.credential_service import CredentialError, set_credential

    try:
        row = set_credential(
            name=name,
            value=value,
            description=description,
            credential_type=(credential_type or "token"),
            username=username,
            agent_id=_agent.id,
        )
    except CredentialError as e:
        return {"error": str(e)}
    return {
        "name": row.name,
        "type": row.credential_type,
        "username": row.username if row.credential_type == "user_password" else None,
        "scope": "agent",
        "message": f"Credential '{row.name}' stored securely.",
    }


def _delete_credential(_agent=None, name=None, **kwargs):
    if _agent is None:
        return {"error": "No agent context"}
    if not name:
        return {"error": "Missing required argument 'name'"}
    from app.models.credential import Credential
    from app.services.credential_service import delete_credential

    row = Credential.query.filter_by(agent_id=_agent.id, name=name).first()
    if row is None:
        return {"error": f"Credential '{name}' not found for this agent (global credentials are admin-only)."}
    delete_credential(row.id)
    return {"name": name, "message": "Credential deleted."}
