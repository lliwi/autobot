import json

from openai import OpenAI

from app.services.oauth_service import get_access_token


def get_client(agent):
    """Create an OpenAI client using the agent's OAuth token."""
    if not agent.oauth_profile_id:
        raise RuntimeError(f"Agent '{agent.name}' has no OAuth profile configured")

    access_token = get_access_token(agent.oauth_profile_id)
    return OpenAI(api_key=access_token)


def stream_chat_completion(agent, messages, tools=None):
    """Stream a chat completion from the OpenAI API.

    Yields (delta_type, delta_data) tuples:
      - ("content", str) for text content deltas
      - ("tool_calls", list) for accumulated tool calls when the stream ends
      - ("usage", dict) for token usage stats
    """
    client = get_client(agent)

    kwargs = {
        "model": agent.model_name,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = tools

    stream = client.chat.completions.create(**kwargs)

    accumulated_tool_calls = {}
    usage = None

    for chunk in stream:
        if chunk.usage:
            usage = {
                "input_tokens": chunk.usage.prompt_tokens,
                "output_tokens": chunk.usage.completion_tokens,
            }

        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        # Text content
        if delta.content:
            yield ("content", delta.content)

        # Tool calls (accumulated across chunks)
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in accumulated_tool_calls:
                    accumulated_tool_calls[idx] = {
                        "id": tc.id or "",
                        "function": {"name": "", "arguments": ""},
                    }
                if tc.id:
                    accumulated_tool_calls[idx]["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        accumulated_tool_calls[idx]["function"]["name"] = tc.function.name
                    if tc.function.arguments:
                        accumulated_tool_calls[idx]["function"]["arguments"] += tc.function.arguments

        # End of stream
        if choice.finish_reason == "tool_calls" and accumulated_tool_calls:
            yield ("tool_calls", list(accumulated_tool_calls.values()))
            accumulated_tool_calls = {}

    if usage:
        yield ("usage", usage)
