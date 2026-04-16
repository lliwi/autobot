import json

from flask import current_app

from app.runtime.context_builder import build_context
from app.runtime.model_client import stream_chat_completion
from app.runtime.tool_executor import execute as execute_tool
MAX_TOOL_ROUNDS = 10


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

    from app.workspace.discovery import get_agent_tool_definitions

    tools = get_agent_tool_definitions(agent)
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    repeat_signatures: dict[str, int] = {}

    for round_num in range(MAX_TOOL_ROUNDS):
        full_response = ""
        tool_calls = None

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

        except Exception as e:
            current_app.logger.error(f"Model call error: {e}")
            yield json.dumps({"type": "error", "data": str(e)})
            return

        if not tool_calls:
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

            # Append tool result to messages for next model call
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result),
            })

        if abort_after_round:
            return

    # Hit max rounds
    yield json.dumps({"type": "error", "data": "Maximum tool call rounds reached"})
