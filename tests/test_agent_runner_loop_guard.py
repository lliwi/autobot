"""Tests for the no-progress loop guards in agent_runner.

Beyond the exact (tool, args) repeat guard, the runner aborts runs that keep
failing (error streak) or keep getting the same result with different args, and
nudges once before aborting.
"""
import json

from app.runtime import agent_runner


def _drain(agent, monkeypatch, *, result_fn, max_rounds=10):
    """Drive the loop; the model calls `noop` once per round with varying args,
    and `result_fn(n)` supplies the tool result for the n-th call.
    """
    agent.max_tool_rounds = max_rounds
    tool_defs = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]
    seen = {"finalize_messages": None}
    counter = {"n": 0}

    def fake_stream(agent_, messages, tools):
        if tools is None:
            seen["finalize_messages"] = [dict(m) for m in messages]
            yield ("content", "PARTIAL")
            return
        counter["n"] += 1
        yield ("tool_calls", [{
            "id": f"c{counter['n']}",
            "function": {"name": "noop", "arguments": json.dumps({"n": counter["n"]})},
        }])

    calls = {"n": 0}

    def fake_exec(*a, **k):
        calls["n"] += 1
        return result_fn(calls["n"])

    monkeypatch.setattr(agent_runner, "build_context", lambda a, s, m: [
        {"role": "system", "content": "sys"}, {"role": "user", "content": m},
    ])
    monkeypatch.setattr(agent_runner, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(agent_runner, "execute_tool", fake_exec)
    monkeypatch.setattr("app.services.agent_budget_service.check_budget", lambda a: None)
    monkeypatch.setattr("app.workspace.discovery.get_agent_tool_definitions", lambda a: tool_defs)
    monkeypatch.setattr("app.runtime.context_budget.model_context_window", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.context_budget.effective_budget", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.tool_registry.forget_run_reads", lambda run_id: None)

    chunks = [json.loads(c) for c in agent_runner.run(agent, None, "go", run_id=1)]
    return chunks, seen, calls


def test_error_streak_aborts(app, agent, monkeypatch):
    # Distinct error each time → result-repeat never triggers, but the error
    # streak does at _FAILED_STREAK_ABORT.
    chunks, _, calls = _drain(agent, monkeypatch, result_fn=lambda n: {"error": f"fail {n}"})
    final = chunks[-1]
    assert final["type"] == "error"
    assert "consecutive tool calls failed" in final["data"]
    assert calls["n"] == agent_runner._FAILED_STREAK_ABORT


def test_repeated_identical_result_aborts(app, agent, monkeypatch):
    # Same result every call (e.g. an "Unknown tool" error) → result-repeat guard.
    chunks, _, calls = _drain(agent, monkeypatch, result_fn=lambda n: {"error": "Unknown tool: x"})
    final = chunks[-1]
    assert final["type"] == "error"
    assert "same tool result was returned" in final["data"]
    assert calls["n"] == agent_runner._RESULT_REPEAT_ABORT


def test_loop_break_nudge_injected_once(app, agent, monkeypatch):
    # 3 distinct errors (streak hits nudge threshold) then successes; run reaches
    # the round cap and the nudge must be present in the finalization context.
    def result_fn(n):
        return {"error": f"e{n}"} if n <= agent_runner._FAILED_STREAK_NUDGE else {"ok": True}

    chunks, seen, _ = _drain(agent, monkeypatch, result_fn=result_fn, max_rounds=5)
    msgs = seen["finalize_messages"]
    assert msgs is not None
    nudges = [m for m in msgs if m["role"] == "system" and "keep failing or returning the same result" in m["content"]]
    assert len(nudges) == 1  # injected exactly once
