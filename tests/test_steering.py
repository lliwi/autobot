"""Inline steering: talk to an agent mid-task; it folds in the input or queues
a follow-up / objective.

The Redis-backed inbox is exercised against a tiny in-memory fake so the tests
stay hermetic (no Redis service needed, matching CI). The agent-side wiring
(``_drain_steering`` injection and the ``queue_followup`` tool) is tested against
real Agent/Session/Run rows.
"""
from app.models.message import Message
from app.runtime import agent_runner
from app.runtime.tool_registry.steering_tools import _queue_followup
from app.services import session_service, steering_service
from app.services.run_service import create_run


# --------------------------------------------------------------------------- #
# Minimal fake Redis (only the ops steering_service uses)
# --------------------------------------------------------------------------- #

class _FakePipe:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def lrange(self, k, a, b):
        self.ops.append(("lrange", k, a, b))
        return self

    def delete(self, k):
        self.ops.append(("delete", k))
        return self

    def execute(self):
        res = []
        for op in self.ops:
            if op[0] == "lrange":
                _, k, a, b = op
                lst = self.store.get(k, [])
                res.append(lst[a:] if b == -1 else lst[a:b + 1])
            else:
                self.store.pop(op[1], None)
                res.append(1)
        self.ops = []
        return res


class FakeRedis:
    def __init__(self):
        self.store = {}

    def llen(self, k):
        return len(self.store.get(k, []))

    def rpush(self, k, v):
        self.store.setdefault(k, []).append(v)
        return len(self.store[k])

    def expire(self, k, ttl):
        return True

    def pipeline(self):
        return _FakePipe(self.store)


def _use_fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(steering_service, "_redis", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# steering_service
# --------------------------------------------------------------------------- #

def test_interjection_roundtrip(app, monkeypatch):
    _use_fake_redis(monkeypatch)
    assert steering_service.push_interjection(7, "do X") is True
    assert steering_service.push_interjection(7, "and Y") is True
    drained = steering_service.drain_interjections(7)
    assert drained == ["do X", "and Y"]
    # Inbox is emptied after draining.
    assert steering_service.drain_interjections(7) == []


def test_followup_roundtrip(app, monkeypatch):
    _use_fake_redis(monkeypatch)
    assert steering_service.queue_followup(3, "later task") is True
    assert steering_service.pop_followups(3) == ["later task"]
    assert steering_service.pop_followups(3) == []


def test_push_rejects_empty(app, monkeypatch):
    _use_fake_redis(monkeypatch)
    assert steering_service.push_interjection(7, "   ") is False
    assert steering_service.push_interjection(None, "x") is False


def test_redis_unavailable_is_safe(app, monkeypatch):
    monkeypatch.setattr(steering_service, "_redis", lambda: None)
    assert steering_service.push_interjection(7, "x") is False
    assert steering_service.drain_interjections(7) == []
    assert steering_service.queue_followup(7, "x") is False
    assert steering_service.pop_followups(7) == []


def test_has_active_run(app, agent):
    session = session_service.get_or_create_session(agent.id, channel_type="web")
    assert steering_service.has_active_run(session.id) is False
    run = create_run(agent_id=agent.id, session_id=session.id)  # status="running"
    assert steering_service.has_active_run(session.id) is True
    from app.services.run_service import finish_run
    finish_run(run.id, status="completed")
    assert steering_service.has_active_run(session.id) is False


# --------------------------------------------------------------------------- #
# agent_runner injection
# --------------------------------------------------------------------------- #

def test_drain_steering_injects_and_persists(app, agent, monkeypatch):
    session = session_service.get_or_create_session(agent.id, channel_type="web")
    run = create_run(agent_id=agent.id, session_id=session.id)

    monkeypatch.setattr(
        "app.services.steering_service.drain_interjections",
        lambda sid: ["also handle the edge case"],
    )

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    injected = agent_runner._drain_steering(session, messages, run.id)

    assert injected == ["also handle the edge case"]
    # A system note + the user interjection were appended to the live context.
    assert messages[-1] == {"role": "user", "content": "also handle the edge case"}
    assert messages[-2]["role"] == "system"
    # And the interjection was persisted to the session history.
    rows = Message.query.filter_by(session_id=session.id, role="user").all()
    assert any(m.content == "also handle the edge case" for m in rows)


def test_drain_steering_noop_without_session(app):
    messages = []
    assert agent_runner._drain_steering(None, messages, 1) == []
    assert messages == []


# --------------------------------------------------------------------------- #
# queue_followup tool
# --------------------------------------------------------------------------- #

def test_queue_followup_tool(app, agent, monkeypatch):
    session = session_service.get_or_create_session(agent.id, channel_type="web")
    run = create_run(agent_id=agent.id, session_id=session.id)

    captured = {}
    monkeypatch.setattr(
        "app.services.steering_service.queue_followup",
        lambda sid, msg: captured.update(sid=sid, msg=msg) or True,
    )

    out = _queue_followup(_agent=agent, _run_id=run.id, message="deploy after this")
    assert out["queued"] is True
    assert captured == {"sid": session.id, "msg": "deploy after this"}


def test_queue_followup_requires_message(app, agent):
    out = _queue_followup(_agent=agent, _run_id=1, message="  ")
    assert "error" in out
