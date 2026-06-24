"""Tests for the max-tool-round termination behavior (issue #25).

When a run exhausts its tool-call-round budget the runner must no longer drop
the task on the floor with a bare ``error``. Instead it should:

  * warn the model once when the budget is nearly exhausted, and
  * perform one final no-tool turn that synthesizes a partial answer, emitting
    a ``done`` chunk carrying structured termination metadata.
"""

import json


from app.runtime import agent_runner
from app.services.chat_service import _resolve_outcome


def _drain(agent, monkeypatch, *, max_rounds=3, final_content="PARTIAL SUMMARY"):
    """Run the agent loop with a model stub that always calls a tool.

    Returns ``(chunks, seen)`` where ``chunks`` is the list of decoded chunk
    dicts and ``seen`` captures the messages handed to the finalization turn.
    """
    agent.max_tool_rounds = max_rounds

    # A non-empty tool list so the loop's ``tools or None`` stays truthy and the
    # finalization turn (tools=None) is distinguishable from a normal round.
    tool_defs = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]

    seen = {"finalize_messages": None}
    counter = {"n": 0}

    def fake_stream(agent_, messages, tools):
        if tools is None:
            # Finalization turn: capture context, emit a summary.
            seen["finalize_messages"] = [dict(m) for m in messages]
            yield ("content", final_content)
            yield ("usage", {"input_tokens": 5, "output_tokens": 3})
            return
        # Normal round: always request a tool call with varying args so the
        # repeat-3-times guard never fires before the round cap is reached.
        counter["n"] += 1
        yield ("tool_calls", [{
            "id": f"call-{counter['n']}",
            "function": {"name": "noop", "arguments": json.dumps({"n": counter["n"]})},
        }])

    monkeypatch.setattr(agent_runner, "build_context", lambda a, s, m: [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": m},
    ])
    monkeypatch.setattr(agent_runner, "stream_chat_completion", fake_stream)
    monkeypatch.setattr(agent_runner, "execute_tool", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("app.services.agent_budget_service.check_budget", lambda a: None)
    monkeypatch.setattr("app.workspace.discovery.get_agent_tool_definitions", lambda a: tool_defs)
    monkeypatch.setattr("app.runtime.context_budget.model_context_window", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.context_budget.effective_budget", lambda *a, **k: 1_000_000)
    monkeypatch.setattr("app.runtime.tool_registry.forget_run_reads", lambda run_id: None)

    chunks = [json.loads(c) for c in agent_runner.run(agent, session=None, user_message="audit everything", run_id=1)]
    return chunks, seen


class TestMaxRoundFinalization:
    def test_emits_partial_done_with_metadata(self, app, agent, monkeypatch):
        chunks, _ = _drain(agent, monkeypatch, max_rounds=3)

        final = chunks[-1]
        assert final["type"] == "done", f"expected graceful done, got {final}"
        assert final["data"] == "PARTIAL SUMMARY"

        meta = final["meta"]
        assert meta["termination_reason"] == "max_tool_rounds"
        assert meta["tool_round_limit"] == 3
        assert meta["tool_rounds_used"] == 3
        assert meta["tool_executions_count"] == 3
        assert meta["last_tool_name"] == "noop"
        assert meta["last_tool_status"] == "ok"
        assert meta["partial"] is True

        # The old hard-abort error must no longer be emitted.
        assert not any(
            c["type"] == "error" and c.get("data") == "Maximum tool call rounds reached"
            for c in chunks
        )

    def test_low_budget_warning_injected_before_finalization(self, app, agent, monkeypatch):
        _, seen = _drain(agent, monkeypatch, max_rounds=3)
        finalize_msgs = seen["finalize_messages"]
        assert finalize_msgs is not None
        assert any(
            m["role"] == "system" and "budget is almost exhausted" in m["content"]
            for m in finalize_msgs
        ), "expected a low-budget warning to have been injected"
        # And the finalization prompt itself is present on the last turn.
        assert any(
            m["role"] == "system" and "maximum tool-call-round budget has been reached" in m["content"].lower()
            for m in finalize_msgs
        )

    def test_failed_finalization_falls_back_to_error_with_meta(self, app, agent, monkeypatch):
        agent.max_tool_rounds = 2
        counter = {"n": 0}

        def fake_stream(agent_, messages, tools):
            if tools is None:
                raise RuntimeError("model down")
            counter["n"] += 1
            yield ("tool_calls", [{
                "id": f"c{counter['n']}",
                "function": {"name": "noop", "arguments": json.dumps({"n": counter["n"]})},
            }])

        monkeypatch.setattr(agent_runner, "build_context", lambda a, s, m: [{"role": "user", "content": m}])
        monkeypatch.setattr(agent_runner, "stream_chat_completion", fake_stream)
        monkeypatch.setattr(agent_runner, "execute_tool", lambda *a, **k: {"ok": True})
        monkeypatch.setattr("app.services.agent_budget_service.check_budget", lambda a: None)
        monkeypatch.setattr("app.workspace.discovery.get_agent_tool_definitions",
                            lambda a: [{"type": "function", "function": {"name": "noop", "parameters": {}}}])
        monkeypatch.setattr("app.runtime.context_budget.model_context_window", lambda *a, **k: 1_000_000)
        monkeypatch.setattr("app.runtime.context_budget.effective_budget", lambda *a, **k: 1_000_000)
        monkeypatch.setattr("app.runtime.tool_registry.forget_run_reads", lambda run_id: None)

        chunks = [json.loads(c) for c in agent_runner.run(agent, None, "go", run_id=2)]
        final = chunks[-1]
        assert final["type"] == "error"
        assert final["data"] == "Maximum tool call rounds reached"
        assert final["meta"]["termination_reason"] == "max_tool_rounds"


class TestResolveOutcome:
    def test_error_takes_precedence(self):
        assert _resolve_outcome("boom", {"termination_reason": "max_tool_rounds"}) == ("error", "boom")

    def test_partial_from_meta(self):
        status, note = _resolve_outcome(None, {
            "termination_reason": "max_tool_rounds",
            "tool_round_limit": 20,
            "tool_executions_count": 7,
            "last_tool_name": "get_credential",
            "last_tool_status": "ok",
        })
        assert status == "partial"
        assert "20" in note and "7" in note

    def test_plain_completion(self):
        assert _resolve_outcome(None, None) == ("completed", None)
