import hashlib
import json
import time

from flask import current_app

from app.runtime.action_heuristics import is_task_like, looks_like_promise
from app.runtime.context_builder import build_context
from app.runtime.model_client import stream_chat_completion
from app.runtime.tool_executor import execute as execute_tool

# Default hard cap on tool-call rounds. Overridden by ``Config.MAX_TOOL_ROUNDS``
# (env ``MAX_TOOL_ROUNDS``) at runtime; individual agents can override that via
# the ``max_tool_rounds`` column on the ``agents`` table.
DEFAULT_MAX_TOOL_ROUNDS = 20

# When this many tool-call rounds remain, inject a one-shot system notice so the
# model can wind down and summarize instead of being cut off mid-task. See #25.
_LOW_BUDGET_REMAINING = 2

_LOW_BUDGET_NUDGE = (
    "SYSTEM NOTICE: Your tool-call-round budget is almost exhausted"
    " ({remaining} round(s) left of {limit}). Do not start new lines of"
    " investigation. Call a tool only if it is essential to finish the task;"
    " otherwise stop calling tools and give your best final answer now,"
    " summarizing what you completed, what you found, and any safe next steps."
)

# Injected (with tools disabled) for the single finalization turn once the hard
# round cap is hit, so the user gets a partial synthesis instead of silent loss.
_FINALIZE_PROMPT = (
    "SYSTEM ENFORCEMENT: The maximum tool-call-round budget has been reached."
    " You may not call any more tools. Using the tool results gathered so far,"
    " write a final answer for the user: summarize the actions completed, the"
    " key findings, what remains blocked, and concrete safe next steps. Be"
    " concise and do not apologize."
)

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

# No-progress guardrails. Beyond the exact-repeat detector (same tool + same
# args), we watch for runs that keep failing or keep getting the same result
# even with *different* args — the "chaining tool calls without converging"
# pattern that otherwise burns every round up to the hard cap.
_FAILED_STREAK_NUDGE = 3   # consecutive failed tool calls → nudge once
_FAILED_STREAK_ABORT = 6   # consecutive failed tool calls → abort the run
_RESULT_REPEAT_ABORT = 4   # identical result content seen this many times → abort

_LOOP_BREAK_NUDGE = (
    "SYSTEM: Your recent tool calls keep failing or returning the same result"
    " without progress. Stop retrying the same approach. Either (a) change"
    " strategy with a genuinely different tool or parameters, (b) if a tool or"
    " credential is missing, say so plainly and stop, or (c) summarize what you"
    " already have and report the blocker to the user. Do NOT repeat the failing"
    " call."
)


# Injected ahead of mid-task user interjections (inline steering). Tells the
# model the message arrived WHILE it was working and to decide how to handle it
# rather than blindly dropping its current line of work.
_STEER_NOTE = (
    "SYSTEM: The user sent the following message(s) while you were still working"
    " on the current task. Read them and DECIDE:\n"
    " (a) if they refine or correct the current task, fold them into what you are"
    " doing now;\n"
    " (b) if they are a separate task to do right after this one, call"
    " `queue_followup` with that task and keep going;\n"
    " (c) if they are a long-running background goal, call `create_objective`.\n"
    "Briefly acknowledge what you decided, then continue. Do not abandon"
    " already-completed work."
)


def _drain_steering(session, messages, run_id):
    """Pull pending interjections for this run's session into the live messages.

    Returns the list of injected message strings (empty if none). Best-effort:
    a steering hiccup must never break the run.
    """
    if session is None:
        return []
    try:
        from app.services.steering_service import drain_interjections
        pending = drain_interjections(session.id)
    except Exception:
        return []
    if not pending:
        return []
    messages.append({"role": "system", "content": _STEER_NOTE})
    for msg in pending:
        messages.append({"role": "user", "content": msg})
        try:
            from app.services.session_service import add_message
            add_message(session.id, role="user", content=msg)
        except Exception:
            pass
    return pending


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
    from app.services.agent_budget_service import check_budget
    budget_err = check_budget(agent)
    if budget_err:
        yield json.dumps({"type": "error", "data": budget_err})
        return

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
    result_repeat: dict[str, int] = {}
    error_streak = 0
    loop_break_nudge_used = False
    user_wants_action = is_task_like(user_message)
    action_nudge_used = False

    # Per-agent override beats the global env knob beats the module default.
    max_rounds = (
        getattr(agent, "max_tool_rounds", None)
        or current_app.config.get("MAX_TOOL_ROUNDS")
        or DEFAULT_MAX_TOOL_ROUNDS
    )

    # Termination/observability bookkeeping surfaced on the final chunk (#25).
    tool_executions_count = 0
    last_tool_name = None
    last_tool_status = None
    low_budget_warned = False

    # Per-round timeline for observability (#5). One entry per model round with
    # its latency, token deltas and dispatched tools. Persisted in the finally
    # block so every termination path — normal, abort, or client disconnect —
    # leaves a trace on the Run row.
    rounds_trace: list[dict] = []
    model_ms = 0.0
    round_usage = {"input_tokens": 0, "output_tokens": 0}
    round_tool_calls: list[dict] = []

    def _record_round(note=None):
        rounds_trace.append({
            "round": round_num + 1,
            "model_ms": round(model_ms, 1),
            "input_tokens": round_usage["input_tokens"],
            "output_tokens": round_usage["output_tokens"],
            "content_chars": len(full_response or ""),
            "tool_calls": list(round_tool_calls),
            "note": note,
        })

    try:
        for round_num in range(max_rounds):
            full_response = ""
            tool_calls = None
            model_ms = 0.0
            round_usage = {"input_tokens": 0, "output_tokens": 0}
            round_tool_calls = []

            # Inline steering: fold any mid-task user messages into the loop
            # before this round's model call so the agent reacts to them now.
            injected = _drain_steering(session, messages, run_id)
            for msg in injected:
                yield json.dumps({"type": "steer_applied", "data": msg})

            # Warn the model once when only a couple of rounds remain so it can
            # wind down gracefully instead of being hard-cut mid-task.
            remaining = max_rounds - round_num
            if not low_budget_warned and remaining <= _LOW_BUDGET_REMAINING < max_rounds:
                low_budget_warned = True
                messages.append({
                    "role": "system",
                    "content": _LOW_BUDGET_NUDGE.format(remaining=remaining, limit=max_rounds),
                })
                current_app.logger.info(
                    "agent_runner low-budget warning: agent=%s round=%d remaining=%d",
                    agent.id, round_num, remaining,
                )

            # Trim accumulated agentic pairs that would push the context over
            # budget. Must happen before the API call so the model never sees
            # a payload that exceeds the window.
            _trim_inloop_messages(messages, budget, _fixed_len)

            model_started = time.monotonic()
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
                        round_usage["input_tokens"] += delta_data.get("input_tokens", 0)
                        round_usage["output_tokens"] += delta_data.get("output_tokens", 0)

                    elif delta_type == "rate_limits":
                        try:
                            from app.services.codex_quota_service import save_snapshot
                            save_snapshot(delta_data)
                        except Exception as e:
                            current_app.logger.debug("Skipped codex quota snapshot: %s", e)

            except Exception as e:
                current_app.logger.error(f"Model call error: {e}")
                model_ms = (time.monotonic() - model_started) * 1000
                _record_round(note=f"model_error: {str(e)[:120]}")
                yield json.dumps({"type": "error", "data": str(e)})
                return
            model_ms = (time.monotonic() - model_started) * 1000

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
                    _record_round(note="action_nudge")
                    continue
                # No tool calls — we're done
                _record_round()
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
                    round_tool_calls.append({"tool": tool_name, "status": "aborted_repeat", "ms": 0})
                    _record_round(note="abort_repeat")
                    yield json.dumps({
                        "type": "error",
                        "data": (
                            f"Aborted: tool '{tool_name}' was called 3 times with the same arguments "
                            f"({arguments}) without progress."
                        ),
                    })
                    return

                tc_started = time.monotonic()
                result = execute_tool(run_id, agent, tool_name, arguments)
                tc_ms = (time.monotonic() - tc_started) * 1000
                tool_executions_count += 1
                last_tool_name = tool_name
                last_tool_status = (
                    "error" if isinstance(result, dict) and result.get("error") else "ok"
                )
                round_tool_calls.append(
                    {"tool": tool_name, "status": last_tool_status, "ms": round(tc_ms, 1)}
                )

                yield json.dumps({"type": "tool_result", "data": {"tool": tool_name, "result": result}})

                # Append tool result to messages for next model call. Oversized
                # results are capped so one fat response doesn't blow the budget;
                # the full result stays persisted in ``tool_executions``.
                capped = _cap_tool_result_content(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": capped,
                })

                # No-progress detection: error streaks and repeated identical
                # results catch non-converging loops that vary their arguments
                # (which the exact-repeat guard above misses).
                error_streak = error_streak + 1 if last_tool_status == "error" else 0
                rhash = hashlib.md5(capped.encode("utf-8", "ignore")).hexdigest()
                result_repeat[rhash] = result_repeat.get(rhash, 0) + 1

                if error_streak >= _FAILED_STREAK_ABORT:
                    _record_round(note="abort_error_streak")
                    yield json.dumps({
                        "type": "error",
                        "data": (
                            f"Aborted: {error_streak} consecutive tool calls failed without"
                            " progress — stopping to avoid a runaway loop."
                        ),
                    })
                    return
                if result_repeat[rhash] >= _RESULT_REPEAT_ABORT:
                    _record_round(note="abort_result_repeat")
                    yield json.dumps({
                        "type": "error",
                        "data": (
                            f"Aborted: the same tool result was returned {result_repeat[rhash]}"
                            " times without progress."
                        ),
                    })
                    return

            # End of the round's tool calls: if the agent is on a failing streak
            # but not yet at the abort threshold, nudge it once to change tack.
            if error_streak >= _FAILED_STREAK_NUDGE and not loop_break_nudge_used:
                loop_break_nudge_used = True
                messages.append({"role": "system", "content": _LOOP_BREAK_NUDGE})

            # Completed a full round of tool calls — record it before the next.
            _record_round()

        # Hit the hard round cap. Rather than dropping the task on the floor,
        # give the model one final no-tool turn to synthesize a partial answer
        # from the results gathered so far (#25).
        meta = {
            "termination_reason": "max_tool_rounds",
            "tool_round_limit": max_rounds,
            "tool_rounds_used": max_rounds,
            "tool_executions_count": tool_executions_count,
            "last_tool_name": last_tool_name,
            "last_tool_status": last_tool_status,
            "partial": True,
        }
        current_app.logger.warning(
            "agent_runner max_tool_rounds: agent=%s run=%s limit=%d execs=%d last_tool=%s/%s",
            agent.id, run_id, max_rounds, tool_executions_count,
            last_tool_name, last_tool_status,
        )

        _trim_inloop_messages(messages, budget, _fixed_len)
        messages.append({"role": "system", "content": _FINALIZE_PROMPT})

        final_response = ""
        full_response = ""
        model_ms = 0.0
        round_usage = {"input_tokens": 0, "output_tokens": 0}
        round_tool_calls = []
        model_started = time.monotonic()
        try:
            for delta_type, delta_data in stream_chat_completion(agent, messages, None):
                if delta_type == "content":
                    final_response += delta_data
                    yield json.dumps({"type": "token", "data": delta_data})
                elif delta_type == "usage":
                    usage_total["input_tokens"] += delta_data.get("input_tokens", 0)
                    usage_total["output_tokens"] += delta_data.get("output_tokens", 0)
                    round_usage["input_tokens"] += delta_data.get("input_tokens", 0)
                    round_usage["output_tokens"] += delta_data.get("output_tokens", 0)
        except Exception as e:
            # Even the summarization turn failed — fall back to a clear error so
            # the run is still recorded, now with structured context.
            current_app.logger.error("agent_runner finalization turn failed: %s", e)
            model_ms = (time.monotonic() - model_started) * 1000
            full_response = final_response
            _record_round(note="max_rounds_finalize_failed")
            yield json.dumps({
                "type": "error",
                "data": "Maximum tool call rounds reached",
                "meta": meta,
            })
            return
        model_ms = (time.monotonic() - model_started) * 1000

        if not final_response.strip():
            final_response = (
                "I reached my tool-call-round limit before finishing this task. "
                "Partial work was completed but I could not synthesize a final "
                "summary. Please re-run with a narrower request to continue."
            )

        full_response = final_response
        _record_round(note="max_rounds_finalize")
        meta["rounds"] = len(rounds_trace)
        yield json.dumps({
            "type": "done",
            "data": final_response,
            "usage": usage_total,
            "meta": meta,
        })
    finally:
        # Drop the per-run read cache so long-lived workers don't leak.
        # Runs here if the generator completes, is closed early by the
        # client disconnecting, or raises.
        forget_run_reads(run_id)
        # Persist the per-round timeline for the run-detail view (#5). Best-effort
        # — a trace write must never mask the real run outcome.
        try:
            from app.services.run_service import save_round_trace
            save_round_trace(run_id, rounds_trace)
        except Exception:
            current_app.logger.exception("Failed to persist round trace for run %s", run_id)
        # Steering messages that landed after the final round would otherwise be
        # lost — re-queue them as follow-ups so the next turn still sees them.
        if session is not None:
            try:
                from app.services.steering_service import drain_interjections, queue_followup
                for leftover in drain_interjections(session.id):
                    queue_followup(session.id, leftover)
            except Exception:
                pass
