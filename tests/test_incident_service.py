"""Incident autopilot: detect → diagnose → draft → human-approved Issue/PR.

These exercise the DB-side pipeline (``ingest``/``diagnose``/``approve``/
``dismiss``) with the reviewer LLM call and GitHub REST calls stubbed, so no
network or model access is needed.
"""
import pytest

from app.extensions import db
from app.models.agent import Agent
from app.models.incident_report import IncidentReport
from app.services import incident_service


@pytest.fixture()
def reviewer(app, workspaces_dir):
    import os
    ws = os.path.join(workspaces_dir, "reviewer")
    os.makedirs(ws, exist_ok=True)
    a = Agent(name="reviewer", slug="reviewer", status="active",
              workspace_path=ws, model_name="gpt-5.2")
    db.session.add(a)
    db.session.commit()
    return a


def test_ingest_creates_incident(app):
    inc = incident_service.ingest(
        severity="error", source="app.services.foo",
        title="boom", message="ValueError: bad thing at 0x1234",
    )
    assert inc is not None and inc.id is not None
    assert inc.status == "new"
    assert inc.occurrences == 1
    assert inc.signature


def test_ingest_dedups_by_signature(app):
    a = incident_service.ingest(severity="error", source="svc",
                                message="Timeout connecting to host 10.0.0.5:5432")
    b = incident_service.ingest(severity="error", source="svc",
                                message="Timeout connecting to host 10.0.0.9:5432")
    # Normalized signatures collapse the variable IP/port → same incident bumped.
    assert b.id == a.id
    assert b.occurrences == 2
    assert IncidentReport.query.count() == 1


def test_quota_incident_is_classified_and_suppressed(app):
    # A Codex usage-limit error is recorded but auto-resolved: no GitHub draft,
    # no reviewer round. It must NOT sit in the actionable "new" queue.
    inc = incident_service.ingest(
        severity="error", source="run:1005",
        message="Codex usage limit reached (plus plan). (resets at 2026-06-28T18:00:06+00:00; in ~2h 12m)",
    )
    assert inc.status == "dismissed"
    assert inc.proposed_action == "none"
    assert inc.source == "codex:quota"
    assert "quota" in (inc.diagnosis or "").lower()


def test_quota_incidents_dedup_across_different_runs(app):
    # The default source is "run:<id>", which would give every failed run a
    # unique signature. Quota incidents must collapse into ONE regardless of run.
    a = incident_service.ingest(severity="error", source="run:1005",
                                message="Codex usage limit reached (plus plan). (in ~2h 12m)")
    b = incident_service.ingest(severity="error", source="run:1006",
                                message="Codex usage limit reached (plus plan). (in ~1h 58m)")
    assert b.id == a.id
    assert b.occurrences == 2
    assert IncidentReport.query.count() == 1
    # The freshest reset time stays visible.
    assert "1h 58m" in b.message


def test_diagnose_drafts_pr_from_reviewer(app, reviewer, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="KeyError: 'x'")

    reviewer_reply = (
        "Root cause: missing key.\n```json\n"
        '{"diagnosis":"Missing key x in payload.","action":"pr",'
        '"title":"Fix KeyError on x","body":"## Fix\\nguard the key",'
        '"patch":{"target_path":"app/foo.py","new_content":"print(\\"ok\\")\\n"}}'
        "\n```"
    )
    monkeypatch.setattr(
        "app.services.chat_service.run_agent_non_streaming",
        lambda **kw: {"response": reviewer_reply, "run_id": 1, "error": None},
    )

    out = incident_service.diagnose(inc.id)
    assert out.status == "awaiting_approval"
    assert out.proposed_action == "pr"
    assert out.proposed_patch_json["target_path"] == "app/foo.py"
    assert out.diagnosis.startswith("Missing key")


def test_diagnose_pr_without_patch_downgrades_to_issue(app, reviewer, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="weird")
    monkeypatch.setattr(
        "app.services.chat_service.run_agent_non_streaming",
        lambda **kw: {"response": '```json\n{"action":"pr","title":"t","body":"b"}\n```',
                      "run_id": 2, "error": None},
    )
    out = incident_service.diagnose(inc.id)
    assert out.proposed_action == "issue"  # no patch → safe downgrade


def test_diagnose_unparseable_falls_back_to_issue(app, reviewer, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="kaboom")
    monkeypatch.setattr(
        "app.services.chat_service.run_agent_non_streaming",
        lambda **kw: {"response": "I could not produce JSON", "run_id": 3, "error": None},
    )
    out = incident_service.diagnose(inc.id)
    assert out.status == "awaiting_approval"
    assert out.proposed_action == "issue"
    assert out.proposed_body  # fallback body built from raw error


def test_approve_opens_issue(app, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="x")
    inc.status = "awaiting_approval"
    inc.proposed_action = "issue"
    inc.proposed_title = "title"
    inc.proposed_body = "body"
    db.session.commit()

    monkeypatch.setattr("app.services.github_service.is_configured", lambda: True)
    captured = {}

    def fake_issue(title, body, labels=None):
        captured["title"] = title
        captured["labels"] = labels
        return "https://github.com/o/r/issues/7"

    monkeypatch.setattr("app.services.github_service.create_issue", fake_issue)

    out, err = incident_service.approve(inc.id)
    assert err is None
    assert out.status == "approved"
    assert out.github_url.endswith("/issues/7")
    assert "autobot" in captured["labels"]


def test_approve_opens_pr_with_patch(app, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="y")
    inc.status = "awaiting_approval"
    inc.proposed_action = "pr"
    inc.proposed_title = "fix"
    inc.proposed_body = "body"
    inc.proposed_patch_json = {"target_path": "app/foo.py", "new_content": "x = 1\n"}
    db.session.commit()

    monkeypatch.setattr("app.services.github_service.is_configured", lambda: True)
    seen = {}

    def fake_pr(*, target_path, new_content, title, body, **kw):
        seen["target_path"] = target_path
        return "https://github.com/o/r/pull/9"

    monkeypatch.setattr("app.services.github_service.create_pr_with_file_change", fake_pr)

    out, err = incident_service.approve(inc.id)
    assert err is None
    assert out.status == "approved"
    assert out.github_url.endswith("/pull/9")
    assert seen["target_path"] == "app/foo.py"


def test_approve_requires_github_config(app, monkeypatch):
    inc = incident_service.ingest(severity="error", source="svc", message="z")
    inc.status = "awaiting_approval"
    inc.proposed_action = "issue"
    db.session.commit()
    monkeypatch.setattr("app.services.github_service.is_configured", lambda: False)
    out, err = incident_service.approve(inc.id)
    assert err and "GitHub" in err
    assert out.status == "awaiting_approval"  # unchanged, still actionable


def test_dismiss(app):
    inc = incident_service.ingest(severity="error", source="svc", message="q")
    out = incident_service.dismiss(inc.id, note="not real")
    assert out.status == "dismissed"
    assert out.resolution_note == "not real"


def test_pending_excludes_terminal(app):
    a = incident_service.ingest(severity="error", source="s1", message="a")
    b = incident_service.ingest(severity="error", source="s2", message="b")
    incident_service.dismiss(b.id)
    pend = incident_service.pending()
    ids = {i.id for i in pend}
    assert a.id in ids and b.id not in ids
