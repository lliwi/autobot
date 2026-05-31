"""Tests for run_log_service: the execution-log query layer."""

import pytest

from app.extensions import db
from app.models.agent import Agent
from app.models.run import Run
from app.models.tool_execution import ToolExecution
from app.services import run_log_service


def _agent(slug):
    a = Agent(name=slug, slug=slug, workspace_path=f"/tmp/{slug}")
    db.session.add(a)
    db.session.commit()
    return a


def _run(agent, trigger_type="message", status="completed", scheduled_task_id=None):
    r = Run(agent_id=agent.id, trigger_type=trigger_type, status=status,
            scheduled_task_id=scheduled_task_id)
    db.session.add(r)
    db.session.commit()
    return r


@pytest.fixture
def agents(app):
    with app.app_context():
        a1 = _agent("alpha")
        a2 = _agent("beta")
        # a1: one completed message, one failed cron
        _run(a1, "message", "completed")
        _run(a1, "cron", "error")
        # a2: one completed heartbeat
        _run(a2, "heartbeat", "completed")
        yield a1.id, a2.id


class TestRecentRuns:
    def test_scope_own_only_returns_agents_runs(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(agent_id=a1, scope="own")
            assert len(runs) == 2
            assert all(r.agent_id == a1 for r in runs)

    def test_scope_all_returns_every_agent(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(agent_id=a1, scope="all")
            assert len(runs) == 3

    def test_filter_by_status(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(agent_id=a1, status="error", scope="own")
            assert len(runs) == 1
            assert runs[0].status == "error"

    def test_filter_by_trigger_type(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(scope="all", trigger_type="cron")
            assert len(runs) == 1
            assert runs[0].trigger_type == "cron"

    def test_newest_first(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(agent_id=a1, scope="own")
            ids = [r.id for r in runs]
            assert ids == sorted(ids, reverse=True)

    def test_summary_timestamps_are_explicit_utc(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            run = run_log_service.recent_runs(agent_id=a1, scope="own")[0]
            summary = run_log_service.summarize_run(run)
            # Must be unambiguous UTC so the agent doesn't read it as local time.
            assert summary["started_at"].endswith("Z")

    def test_limit_capped(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(scope="all", limit=999)
            # only 3 rows exist, but the cap must not raise
            assert len(runs) == 3

    def test_limit_invalid_falls_back(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            runs = run_log_service.recent_runs(scope="all", limit="nope")
            assert len(runs) == 3


class TestRunDetail:
    def test_returns_run_with_tool_executions(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            run = run_log_service.recent_runs(agent_id=a1, scope="own")[0]
            te = ToolExecution(run_id=run.id, agent_id=a1, tool_name="fetch_url",
                               status="completed", output_json={"ok": True})
            db.session.add(te)
            db.session.commit()
            detail = run_log_service.run_detail(run.id, requesting_agent_id=a1, scope="own")
            assert detail["run"]["id"] == run.id
            assert len(detail["tool_executions"]) == 1
            assert detail["tool_executions"][0]["tool_name"] == "fetch_url"

    def test_access_control_own_scope_blocks_other_agent(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            other_run = run_log_service.recent_runs(agent_id=a2, scope="own")[0]
            detail = run_log_service.run_detail(other_run.id, requesting_agent_id=a1, scope="own")
            assert "error" in detail

    def test_scope_all_allows_other_agent(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            other_run = run_log_service.recent_runs(agent_id=a2, scope="own")[0]
            detail = run_log_service.run_detail(other_run.id, requesting_agent_id=a1, scope="all")
            assert "error" not in detail
            assert detail["run"]["id"] == other_run.id

    def test_missing_run(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            detail = run_log_service.run_detail(999999, requesting_agent_id=a1, scope="own")
            assert "error" in detail

    def test_long_output_truncated(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            run = run_log_service.recent_runs(agent_id=a1, scope="own")[0]
            big = "x" * 5000
            te = ToolExecution(run_id=run.id, agent_id=a1, tool_name="big",
                               status="completed", output_json=big)
            db.session.add(te)
            db.session.commit()
            detail = run_log_service.run_detail(run.id, requesting_agent_id=a1, scope="own")
            out = detail["tool_executions"][0]["output"]
            assert "truncated" in out
            assert len(out) < 5000


class TestLinkRunToTask:
    def test_links(self, app, agents):
        a1, a2 = agents
        with app.app_context():
            from app.models.scheduled_task import ScheduledTask

            task = ScheduledTask(agent_id=a1, task_type="cron", schedule_expr="0 9 * * *")
            db.session.add(task)
            db.session.commit()
            run = run_log_service.recent_runs(agent_id=a1, scope="own")[0]
            run_log_service.link_run_to_task(run.id, task.id)
            refreshed = db.session.get(Run, run.id)
            assert refreshed.scheduled_task_id == task.id

    def test_noop_on_missing_run(self, app, agents):
        with app.app_context():
            # Should not raise.
            run_log_service.link_run_to_task(999999, 1)
            run_log_service.link_run_to_task(None, 1)
