"""Shared fixtures for the unit-test suite.

Builds a Flask app in testing mode with SQLite in-memory + a throwaway
workspaces directory so tests can create agents and write workspace files
without touching the real dev database or the developer's workspaces.
"""

import os
import shutil
import tempfile

import pytest

os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import create_app  # noqa: E402
from app import models as _models  # noqa: E402,F401 — registers ORM classes
from app.extensions import db  # noqa: E402
from app.models.agent import Agent  # noqa: E402


@pytest.fixture()
def workspaces_dir():
    tmp = tempfile.mkdtemp(prefix="autobot-tests-ws-")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def app(workspaces_dir):
    flask_app = create_app("testing")
    flask_app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        WORKSPACES_BASE_PATH=workspaces_dir,
        PATCHES_PER_HOUR_PER_AGENT=30,
        TESTING=True,
    )
    with flask_app.app_context():
        db.create_all()
        try:
            yield flask_app
        finally:
            db.session.remove()
            db.drop_all()


@pytest.fixture()
def agent(app, workspaces_dir):
    ws = os.path.join(workspaces_dir, "tester")
    os.makedirs(ws, exist_ok=True)
    a = Agent(
        name="tester",
        slug="tester",
        status="active",
        workspace_path=ws,
        model_name="gpt-5.2",
    )
    db.session.add(a)
    db.session.commit()
    return a
