"""Regression: heartbeat supervisor must not crash comparing naive vs aware
datetimes (issue #30).

``Objective.next_check_at`` can come back from Postgres offset-naive while the
supervisor's ``now`` is offset-aware UTC. Direct comparison raises
``TypeError: can't compare offset-naive and offset-aware datetimes``. These
tests pin the normalization (``_as_aware_utc``) and the due/not-due logic for
naive, aware and ``None`` values.
"""
from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.models.objective import Objective
from app.services import heartbeat_supervisor as hs


def test_as_aware_utc_normalizes():
    assert hs._as_aware_utc(None) is None
    naive = datetime(2026, 1, 1, 12, 0, 0)
    out = hs._as_aware_utc(naive)
    assert out.tzinfo is timezone.utc
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert hs._as_aware_utc(aware) is aware  # already aware → unchanged


def _add_objective(agent, next_check_at, status="active"):
    obj = Objective(
        agent_id=agent.id,
        title="t",
        status=status,
        next_check_at=next_check_at,
        context_json={},
    )
    db.session.add(obj)
    db.session.commit()
    return obj


def test_build_snapshot_handles_naive_next_check_at(app, agent):
    # The crash scenario: a *naive* past timestamp.
    naive_past = datetime.utcnow() - timedelta(hours=1)  # naive, no tzinfo
    _add_objective(agent, naive_past)

    # Must not raise TypeError, and the past objective is due.
    snapshot = hs._build_snapshot(agent)
    objs = snapshot["objectives"]
    assert len(objs) == 1
    assert objs[0]["due"] is True


def test_build_snapshot_due_flags_mixed(app, agent):
    naive_past = datetime.utcnow() - timedelta(hours=2)            # due
    aware_future = datetime.now(timezone.utc) + timedelta(hours=2)  # not due
    _add_objective(agent, naive_past)
    _add_objective(agent, aware_future)
    _add_objective(agent, None)  # never scheduled → due

    snapshot = hs._build_snapshot(agent)
    due_by_check = {(o["due"]) for o in snapshot["objectives"]}
    # Exactly the future one is not-due; the other two are due.
    dues = sorted(o["due"] for o in snapshot["objectives"])
    assert dues == [False, True, True]
    assert True in due_by_check and False in due_by_check


def test_tick_does_not_crash_with_naive_objective(app, agent, monkeypatch):
    """End-to-end tick with a naive next_check_at completes and records an event."""
    _add_objective(agent, datetime.utcnow() - timedelta(hours=1))

    # Don't actually invoke the model: force a skip-like path by stubbing the
    # agent runner so 'act' decisions don't make a network call.
    monkeypatch.setattr(
        "app.services.chat_service.run_agent_non_streaming",
        lambda **kw: {"run_id": None, "response": "", "error": None},
    )
    event = hs.tick(agent.id)
    assert event is not None
    assert event.decision in ("act", "skip", "defer")
