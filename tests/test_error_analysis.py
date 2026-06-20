"""Tests for the error-learning loop (error_analysis_service)."""
from app.extensions import db
from app.models.objective import Objective
from app.models.run import Run
from app.models.tool_execution import ToolExecution
from app.services import error_analysis_service as eas


def _fail_exec(agent, run, tool, error):
    db.session.add(ToolExecution(run_id=run.id, agent_id=agent.id, tool_name=tool,
                                 status="error", output_json={"error": error}))


def _run(agent, status="completed", summary=None):
    r = Run(agent_id=agent.id, trigger_type="message", status=status, error_summary=summary)
    db.session.add(r)
    db.session.flush()
    return r


def test_normalize_collapses_variable_parts():
    a = eas.normalize_error("HTTP 500 from https://api/zones/9f3a after 1.2s")
    b = eas.normalize_error("HTTP 502 from https://api/zones/77b1 after 4.8s")
    assert a == b  # ids/numbers/urls stripped → same signature


def test_digest_clusters_recurring_errors(app, agent):
    r = _run(agent)
    for i in range(3):
        _fail_exec(agent, r, "cloudflare", f"HTTP 500 from https://api/z/{i}aa after {i}.1s")
    _fail_exec(agent, r, "jackett", "one off timeout")
    db.session.commit()

    digest = eas.error_digest(agent.id, min_count=1)
    by_tool = {c["tool"]: c for c in digest}
    assert by_tool["cloudflare"]["count"] == 3
    assert by_tool["jackett"]["count"] == 1


def test_digest_excludes_meta_tools(app, agent):
    r = _run(agent)
    _fail_exec(agent, r, "error_digest", "boom")
    _fail_exec(agent, r, "create_objective", "boom")
    db.session.commit()
    assert eas.error_digest(agent.id, min_count=1) == []


def test_scan_creates_fix_objective_above_threshold(app, agent):
    r = _run(agent)
    for i in range(3):
        _fail_exec(agent, r, "cloudflare", f"HTTP 500 from /x/{i} after {i}s")
    db.session.commit()

    created = eas.scan_agent(agent.id, threshold=3)
    assert len(created) == 1
    obj = created[0]
    assert obj.status == "active"
    assert obj.context_json.get("error_signature")
    assert obj.context_json.get("kind") == "error_fix"
    assert len(obj.context_json.get("plan")) == 5


def test_scan_below_threshold_creates_nothing(app, agent):
    r = _run(agent)
    _fail_exec(agent, r, "cloudflare", "HTTP 500 from /x after 1s")
    db.session.commit()
    assert eas.scan_agent(agent.id, threshold=3) == []


def test_scan_dedups_open_fix_objective(app, agent):
    r = _run(agent)
    for i in range(3):
        _fail_exec(agent, r, "cloudflare", f"HTTP 500 from /x/{i} after {i}s")
    db.session.commit()

    assert len(eas.scan_agent(agent.id, threshold=3)) == 1
    # second scan: an open fix-objective already exists → no duplicate
    assert eas.scan_agent(agent.id, threshold=3) == []
    assert Objective.query.filter_by(agent_id=agent.id).count() == 1
