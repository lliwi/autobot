"""Tests for scheduler_service task mutations (create/enable/disable)."""

import pytest

from app.extensions import db
from app.models.agent import Agent
from app.services import scheduler_service


@pytest.fixture
def agent_id(app):
    with app.app_context():
        a = Agent(name="sch", slug="sch", workspace_path="/tmp/sch")
        db.session.add(a)
        db.session.commit()
        yield a.id


class TestSetTaskEnabled:
    def test_disable_then_enable_recomputes_next_run(self, app, agent_id):
        with app.app_context():
            task = scheduler_service.create_task(
                agent_id=agent_id, task_type="cron",
                schedule_expr="20 9 * * *", timezone_str="Europe/Madrid",
            )
            tid = task.id

            disabled = scheduler_service.set_task_enabled(tid, False)
            assert disabled.enabled is False

            enabled = scheduler_service.set_task_enabled(tid, True)
            assert enabled.enabled is True
            # Re-enabling a cron task must recompute its next fire time.
            assert enabled.next_run_at is not None
            # 09:20 Europe/Madrid is 07:20 or 08:20 UTC depending on DST — minute holds.
            assert enabled.next_run_at.minute == 20

    def test_idempotent(self, app, agent_id):
        with app.app_context():
            task = scheduler_service.create_task(
                agent_id=agent_id, task_type="cron", schedule_expr="0 9 * * *",
            )
            scheduler_service.set_task_enabled(task.id, True)
            again = scheduler_service.set_task_enabled(task.id, True)
            assert again.enabled is True

    def test_missing_task_returns_none(self, app, agent_id):
        with app.app_context():
            assert scheduler_service.set_task_enabled(999999, True) is None


class TestCreateTaskTimezone:
    def test_cron_evaluated_in_given_timezone(self, app, agent_id):
        # "20 9 * * *" in Europe/Madrid → next_run stored as UTC with minute 20
        # and hour 7 (CEST) or 8 (CET), never 9 (which would mean UTC).
        with app.app_context():
            task = scheduler_service.create_task(
                agent_id=agent_id, task_type="cron",
                schedule_expr="20 9 * * *", timezone_str="Europe/Madrid",
            )
            assert task.next_run_at is not None
            assert task.next_run_at.hour in (7, 8)
            assert task.next_run_at.minute == 20
