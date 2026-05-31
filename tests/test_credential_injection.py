"""Regression tests for subprocess credential injection.

Workspace tool wrappers run in the agent's venv (no Flask/DB), so they resolve
credentials from the ``AUTOBOT_CRED_<NAME>`` env vars the tool executor injects.
These tests pin that contract — both for token and user_password credentials.
"""

import pytest

from app.extensions import db
from app.models.agent import Agent
from app.runtime import tool_executor
from app.services import credential_service


@pytest.fixture
def agent(app):
    with app.app_context():
        a = Agent(name="cred", slug="cred", workspace_path="/tmp/cred")
        db.session.add(a)
        db.session.commit()
        yield a


def _inject(agent):
    env = {}
    tool_executor._inject_credentials(agent, env)
    return env


class TestTokenInjection:
    def test_token_exposed_as_upper_env(self, app, agent):
        with app.app_context():
            credential_service.set_credential("portainer", "tok-123", agent_id=agent.id)
            env = _inject(agent)
            assert env["AUTOBOT_CRED_PORTAINER"] == "tok-123"

    def test_global_credential_visible(self, app, agent):
        with app.app_context():
            credential_service.set_credential("notion", "global-tok", agent_id=None)
            env = _inject(agent)
            assert env["AUTOBOT_CRED_NOTION"] == "global-tok"

    def test_agent_scoped_shadows_global(self, app, agent):
        with app.app_context():
            credential_service.set_credential("dup", "global", agent_id=None)
            credential_service.set_credential("dup", "scoped", agent_id=agent.id)
            env = _inject(agent)
            assert env["AUTOBOT_CRED_DUP"] == "scoped"


class TestUserPasswordInjection:
    def test_username_exported_alongside_password(self, app, agent):
        with app.app_context():
            credential_service.set_credential(
                "SMB", "s3cret", agent_id=agent.id,
                credential_type="user_password", username="nasuser",
            )
            env = _inject(agent)
            # value carries the password; username is a sibling env var
            assert env["AUTOBOT_CRED_SMB"] == "s3cret"
            assert env["AUTOBOT_CRED_SMB_USERNAME"] == "nasuser"

    def test_token_has_no_username_var(self, app, agent):
        with app.app_context():
            credential_service.set_credential("portainer", "tok", agent_id=agent.id)
            env = _inject(agent)
            assert "AUTOBOT_CRED_PORTAINER_USERNAME" not in env


class TestUsernamesForSubprocess:
    def test_only_user_password_rows(self, app, agent):
        with app.app_context():
            credential_service.set_credential("tok", "v", agent_id=agent.id)
            credential_service.set_credential(
                "smb", "p", agent_id=agent.id,
                credential_type="user_password", username="u",
            )
            names = credential_service.usernames_for_subprocess(agent.id)
            assert names == {"smb": "u"}
