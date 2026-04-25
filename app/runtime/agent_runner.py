import json

from flask import current_app

from app.runtime.action_heuristics import is_task_like, looks_like_promise
from app.runtime.context_builder import build_context
from app.runtime.model_client import stream_chat_completion
from app.runtime.tool_executor import execute as execute_tool

# Default hard cap on tool-call rounds. Overridden by ``Config.MAX_TOOL_ROUNDS``
# (env ``MAX_TOOL_ROUNDS``) at runtime; individual agents can override that via
# the ``max_tool_rounds`` column on the ``agents`` table.
DEFAULT_MAX_TOOL_ROUNDS = 20

# Per-call cap on what goes back into the *model's* message stream. The full
# tool output is still persisted in ``tool_executions`` — this only trims
# what the model sees on the next round so a single fat response (e.g. a
# 200 KB fetch_url body) can't eat the whole context window.
_TOOL_RESULT_MAX_CHARS = 20000


def _cap_tool_result_content(result) -> str:
    """Serialize a tool result for the model, truncating oversized payloads.

    Returns a string ready to drop into a ``{"role": "tool", "content": ...}``
    message. When truncated we append an explicit marker so the model knows
    it didn't see the full output and can decide to fetch again with a
    narrower query rather than hallucinate.
    """
    try:
        raw = json.dumps(result)
    except (TypeError, ValueError):
        raw = json.dumps({"error": "tool result not JSON-serializable"})
    if len(raw) <= _TOOL_RESULT_MAX_CHARS:
        return raw
    return (
        raw[:_TOOL_RESULT_MAX_CHARS]
        + f'\n[truncated-for-context: full length={len(raw)} chars stored in tool_executions]'
    )

_ENFORCE_ACTION_NUDGE = (
    "SYSTEM ENFORCEMENT: Your previous response announced intent without"
    " calling any tool, on a request that requires action. That is"
    " forbidden by the action-first protocol. Re-answer now by executing"
    " the task with the appropriate tool calls. Rules for this retry:"
    " (a) do not apologize, do not re-state the plan, do not ask permission;"
    " (b) do not use the phrases 'voy a', 'lo haré', 'I will', 'let me', etc.;"
    " (c) if you genuinely lack a tool for this task, call `create_tool` or"
    " `fetch_url` — do not give up; (d) if a credential is missing, call"
    " `list_credentials` to verify before telling the user it's missing."
)


def _trim_inloop_messages(messages: list, budget: int, fixed_len: int) -> list:
    """Drop oldest agentic round pairs to keep the running context under budget.

    ``fixed_len`` marks the boundary of the initial context built by
    ``build_context`` (system + trimmed history + user turn). Everything at
    or after that index is agentic pairs appended during the tool-call loop.
    We drop pairs (assistant-with-tool-calls + its following tool-result
    messages) starting from the oldest until the total is under budget.

    Called before each new model invocation, not after, so the model always
    receives a coherent and affordable context even in long multi-round runs.
    """
    from app.runtime.context_budget import count_messages_tokens

    while count_messages_tokens(messages) > budget:
        cut_start = None
        for i in range(fixed_len, len(messages)):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                cut_start = i
                break
        if cut_start is None:
            break  # nothing left to trim in the agentic section
        cut_end = cut_start + 1
        while cut_end < len(messages) and messages[cut_end].get("role") == "tool":
            cut_end += 1
        del messages[cut_start:cut_end]
    return messages


def run(agent, session, user_message, run_id):
    """Main reasoning loop. Generator that yields ChatChunk-like dicts.

    Chunk types:
      - {"type": "token", "data": "..."}
      - {"type": "tool_call", "data": {"name": "...", "arguments": {...}}}
      - {"type": "tool_result", "data": {"tool": "...", "result": {...}}}
      - {"type": "error", "data": "..."}
      - {"type": "done", "data": "...", "usage": {...}}
    """
    messages = build_context(agent, session, user_message)
    # Mark the boundary between the initial context (system + history + user)
    # and the agentic pairs appended during the loop. _trim_inloop_messages uses
    # this to know which messages it may drop.
    _fixed_len = len(messages)

    from app.runtime.context_budget import effective_budget, model_context_window
    from app.runtime.tool_registry import forget_run_reads
    from app.workspace.discovery import get_agent_tool_definitions

    tools = get_agent_tool_definitions(agent)
    # Surface the budget so the client can render a context-usage indicator
    # after each turn without needing a second round-trip.
    budget = effective_budget(
        model_context_window(agent.model_name, current_app.config["MAX_CONTEXT_TOKENS"]),
        current_app.config.get("CONTEXT_RESPONSE_RESERVE_TOKENS"),
    )
    usage_total = {"input_tokens": 0, "output_tokens": 0, "budget": budget}
    repeat_signatures: dict[str, int] = {}
    user_wants_action = is_task_like(user_message)
    action_nudge_used = False

    # Per-agent override beats the global env knob beats the module default.
    max_rounds = (
        getattr(agent, "max_tool_rounds", None)
        or current_app.config.get("MAX_TOOL_ROUNDS")
        or DEFAULT_MAX_TOOL_ROUNDS
    )

    try:
        for round_num in range(max_rounds):
            full_response = ""
            tool_calls = None

            # Trim accumulated agentic pairs that would push the context over
            # budget. Must happen before the API call so the model never sees
            # a payload that exceeds the window.
            _trim_inloop_messages(messages, budget, _fixed_len)

            try:
                for delta_type, delta_data in stream_chat_completion(agent, messages, tools or None):
                    if delta_type == "content":
                        full_response += delta_data
                        yield json.dumps({"type": "token", "data": delta_data})

                    elif delta_type == "tool_calls":
                        tool_calls = delta_data

                    elif delta_type == "usage":
                        usage_total["input_tokens"] += delta_data.get("input_tokens", 0)
                        usage_total["output_tokens"] += delta_data.get("output_tokens", 0)

                    elif delta_type == "rate_limits":
                        try:
                            from app.services.codex_quota_service import save_snapshot
                            save_snapshot(delta_data)
                        except Exception as e:
                            current_app.logger.debug("Skipped codex quota snapshot: %s", e)

            except Exception as e:
                current_app.logger.error(f"Model call error: {e}")
                yield json.dumps({"type": "error", "data": str(e)})
                return

            if not tool_calls:
                if (
                    user_wants_action
                    and not action_nudge_used
                    and looks_like_promise(full_response)
                ):
                    # Stalled on a task: announced intent, never tool-called.
                    # Re-prompt once before giving up.
                    action_nudge_used = True
                    messages.append({
                        "role": "assistant",
                        "content": full_response or "",
                    })
                    messages.append({
                        "role": "system",
                        "content": _ENFORCE_ACTION_NUDGE,
                    })
                    current_app.logger.info(
                        "agent_runner nudge: agent=%s round=%d user_task=1 promise=1",
                        agent.id, round_num,
                    )
                    continue
                # No tool calls — we're done
                yield json.dumps({"type": "done", "data": full_response, "usage": usage_total})
                return

            # Process tool calls
            # Append assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": full_response or None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"],
                        },
                    }
                    for tc in tool_calls
                ],
            })

            abort_after_round = False
            for tc in tool_calls:
                tool_name = tc["function"]["name"]
                try:
                    arguments = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    arguments = {}

                signature = f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"
                repeat_signatures[signature] = repeat_signatures.get(signature, 0) + 1

                yield json.dumps({"type": "tool_call", "data": {"name": tool_name, "arguments": arguments}})

                if repeat_signatures[signature] >= 3:
                    abort_after_round = True
                    yield json.dumps({
                        "type": "error",
                        "data": (
                            f"Aborted: tool '{tool_name}' was called 3 times with the same arguments "
                            f"({arguments}) without progress."
                        ),
                    })
                    return

                result = execute_tool(run_id, agent, tool_name, arguments)

                yield json.dumps({"type": "tool_result", "data": {"tool": tool_name, "result": result}})

                # Append tool result to messages for next model call. Oversized
                # results are capped so one fat response doesn't blow the budget;
                # the full result stays persisted in ``tool_executions``.
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _cap_tool_result_content(result),
                })

            if abort_after_round:
                return

        # Hit max rounds
        yield json.dumps({"type": "error", "data": "Maximum tool call rounds reached"})
    finally:
        # Drop the per-run read cache so long-lived workers don't leak.
        # Runs here if the generator completes, is closed early by the
        # client disconnecting, or raises.
        forget_run_reads(run_id)
