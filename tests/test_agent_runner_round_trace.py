"""Per-round tracing of the agent loop (#5).

Every model round should leave a structured entry on ``Run.rounds_trace`` —
latency, token deltas and the tools it dispatched — so a run that burns its
budget can be diagnosed round by round from the dashboard.
"""
import json

from app.extensions import db
from app.models.run import Run
from app.runtime import agent_runner
from app.services.run_service import create_run


def _patch_common(monkeypatch, tool_defs):
    monkeypatch.setattr(agent_runner, "build_context", lambda a, s, m: [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": m},
    ])
    monkeypatch.setattr(agent_runner, "execute_tool", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.services.agent_budget_service.check_budget", lambda a: None)
    monkeypatch.setattr("app.workspace.discovery.get_agent_tool_definitions", lambda a: tool_defs)
    monkeypatch.setattr("app.runtime.context_budget.model_context_window", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.context_budget.effective_budget", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.tool_registry.forget_run_reads", lambda run_id: None)


def test_round_trace_persisted(app, agent, monkeypatch):
    agent.max_tool_rounds = 5
    tool_defs = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

    calls = {"n": 0}

    def fake_stream(agent_, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            # Round 1: one tool call.
            yield ("usage", {"input_tokens": 10, "output_tokens": 4})
            yield ("tool_calls", [{
                "id": "call-1",
                "function": {"name": "noop", "arguments": json.dumps({"x": 1})},
            }])
            return
        # Round 2: plain answer, no tools -> done.
        yield ("content", "all done")
        yield ("usage", {"input_tokens": 6, "output_tokens": 2})

    _patch_common(monkeypatch, tool_defs)
    monkeypatch.setattr(agent_runner, "stream_chat_completion", fake_stream)

    run = create_run(agent_id=agent.id, session_id=None, trigger_type="message")
    chunks = [json.loads(c) for c in agent_runner.run(agent, None, "do the thing", run.id)]

    assert chunks[-1]["type"] == "done"

    refreshed = db.session.get(Run, run.id)
    trace = refreshed.rounds_trace
    assert isinstance(trace, list) and len(trace) == 2

    first, second = trace
    assert first["round"] == 1
    assert first["input_tokens"] == 10 and first["output_tokens"] == 4
    assert [tc["tool"] for tc in first["tool_calls"]] == ["noop"]
    assert first["tool_calls"][0]["status"] == "ok"

    assert second["round"] == 2
    assert second["tool_calls"] == []
    assert second["content_chars"] == len("all done")


def test_round_trace_records_tool_error_status(app, agent, monkeypatch):
    agent.max_tool_rounds = 5
    tool_defs = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

    calls = {"n": 0}

    def fake_stream(agent_, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            yield ("tool_calls", [{
                "id": "call-1",
                "function": {"name": "noop", "arguments": "{}"},
            }])
            return
        yield ("content", "done")

    _patch_common(monkeypatch, tool_defs)
    monkeypatch.setattr(agent_runner, "stream_chat_completion", fake_stream)
    # Tool returns an error dict -> status should be recorded as "error".
    monkeypatch.setattr(agent_runner, "execute_tool", lambda *a, **k: {"error": "boom"})

    run = create_run(agent_id=agent.id, session_id=None, trigger_type="message")
    list(agent_runner.run(agent, None, "do the thing", run.id))

    refreshed = db.session.get(Run, run.id)
    assert refreshed.rounds_trace[0]["tool_calls"][0]["status"] == "error"
