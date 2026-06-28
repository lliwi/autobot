"""Model client for the Codex backend (chatgpt.com/backend-api).

Translates Chat-Completions-style messages and tools into the OpenAI Responses
API schema, POSTs to the Codex endpoint with the user's ChatGPT subscription
token, and yields deltas back in the legacy format expected by agent_runner.
"""
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone

import httpx

from app.services import codex_auth

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "gpt-5.2"

# Transient-failure handling for the model call. A tool-call loop that loses a
# single round to a 429 or a dropped connection aborts the whole run, so we
# retry the *connection* a few times with exponential backoff + jitter. Once the
# server has answered 200 and we start streaming we are committed: a mid-stream
# failure propagates (we can't un-yield already-emitted deltas).
_MAX_RETRIES = 4
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 30.0
_RETRY_AFTER_CAP_SECONDS = 60.0


class _RetryableStatus(Exception):
    """Internal signal: server returned a retryable status before any output."""

    def __init__(self, status_code: int, delay: float):
        super().__init__(f"retryable status {status_code}")
        self.status_code = status_code
        self.delay = delay


class UsageLimitReached(RuntimeError):
    """Codex subscription quota exhausted (HTTP 429 ``usage_limit_reached``).

    Distinct from a transient 429: the reset is typically minutes-to-hours away,
    so retrying within the request is pointless. Carries the reset metadata and a
    human-friendly str() ("Codex usage limit reached (plus plan). (resets at …)")
    so callers can surface it instead of dumping the raw API JSON. Incident-level
    dedup is handled separately by a stable signature in ``incident_service``.
    """

    def __init__(self, *, plan_type=None, resets_at=None, resets_in_seconds=None,
                 message=None):
        self.plan_type = plan_type
        self.resets_at = resets_at
        self.resets_in_seconds = resets_in_seconds
        self.api_message = message
        super().__init__(self._format())

    def _format(self) -> str:
        plan = f" ({self.plan_type} plan)" if self.plan_type else ""
        parts = []
        try:
            if self.resets_at:
                ts = datetime.fromtimestamp(int(self.resets_at), tz=timezone.utc)
                parts.append(f"resets at {ts.isoformat()}")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        try:
            if self.resets_in_seconds:
                secs = int(self.resets_in_seconds)
                h, m = secs // 3600, (secs % 3600) // 60
                parts.append(f"in ~{h}h {m}m" if h else f"in ~{m}m")
        except (TypeError, ValueError):
            pass
        when = f" ({'; '.join(parts)})" if parts else ""
        return f"Codex usage limit reached{plan}.{when}"


def _parse_usage_limit(status_code: int, body_text: str):
    """Return reset metadata when ``body_text`` is a Codex usage-limit 429.

    Handles both the JSON error envelope and a plain-text body that merely
    contains the ``usage_limit_reached`` marker. Returns ``None`` otherwise.
    """
    if status_code != 429 or "usage_limit_reached" not in (body_text or ""):
        return None
    err = {}
    try:
        data = json.loads(body_text)
        err = data.get("error") if isinstance(data, dict) else {}
        err = err if isinstance(err, dict) else {}
    except (ValueError, TypeError):
        err = {}
    return {
        "plan_type": err.get("plan_type"),
        "resets_at": err.get("resets_at"),
        "resets_in_seconds": err.get("resets_in_seconds"),
        "message": err.get("message"),
    }


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with full jitter: random in [0, base*2**attempt]."""
    ceiling = min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** attempt))
    return random.uniform(0.0, ceiling)


def _parse_retry_after(headers) -> float | None:
    """Honor a numeric ``Retry-After`` header (seconds), capped for safety."""
    raw = headers.get("retry-after") if headers else None
    if not raw:
        return None
    try:
        return min(float(raw), _RETRY_AFTER_CAP_SECONDS)
    except (TypeError, ValueError):
        return None


def stream_chat_completion(agent, messages, tools=None):
    """Stream a Codex response in Chat-Completions-style deltas.

    Yields tuples:
      ("content", str)        — text delta
      ("tool_calls", list)    — accumulated tool calls when stream ends
      ("usage", dict)         — input/output token counts
    """
    if not codex_auth.is_logged_in():
        raise RuntimeError("Codex no autenticado. Ejecuta `flask codex-login`.")

    token = codex_auth.get_access_token()
    account_id = codex_auth.get_account_id() or ""
    system, input_items = _convert_messages(messages)

    body = {
        "model": (agent.model_name or DEFAULT_MODEL).strip(),
        "store": False,
        "stream": True,
        "instructions": system,
        "input": input_items,
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": _cache_key(messages),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    if tools:
        body["tools"] = _convert_tools(tools)

    headers = {
        "Authorization": f"Bearer {token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta": "responses=experimental",
        "originator": codex_auth.ORIGINATOR,
        "accept": "text/event-stream",
        "content-type": "application/json",
    }

    attempt = 0
    while True:
        # ``committed`` flips once we've received a 200 and begun streaming, so
        # the network-error handler knows it can no longer safely retry.
        committed = False
        try:
            with httpx.stream(
                "POST", CODEX_RESPONSES_URL, json=body, headers=headers, timeout=180.0
            ) as response:
                if response.status_code != 200:
                    error_body = response.read().decode("utf-8", errors="replace")[:2000]
                    # Usage-limit (quota) 429s won't clear within the request —
                    # the reset is minutes-to-hours away. Fail fast with a clear,
                    # operator-friendly message instead of retry-storming a quota
                    # that cannot recover in time.
                    quota = _parse_usage_limit(response.status_code, error_body)
                    if quota is not None:
                        info = UsageLimitReached(**quota)
                        logger.warning(
                            "Codex usage limit reached (plan=%s resets_in=%ss) — not retrying",
                            quota.get("plan_type"), quota.get("resets_in_seconds"),
                        )
                        raise info
                    if response.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                        delay = _parse_retry_after(response.headers) or _backoff_delay(attempt)
                        raise _RetryableStatus(response.status_code, delay)
                    raise RuntimeError(f"Codex API {response.status_code}: {error_body[:500]}")
                # Past this point we are streaming real output and cannot retry.
                committed = True
                # Forward the subscription rate-limit headers so the runner can
                # persist a snapshot for the metrics dashboard.
                quota_headers = {
                    k: v for k, v in response.headers.items()
                    if isinstance(k, str) and k.lower().startswith("x-codex-")
                }
                if quota_headers:
                    yield ("rate_limits", quota_headers)
                yield from _consume_sse(response)
            return
        except _RetryableStatus as r:
            attempt += 1
            logger.warning(
                "Codex API %s — retry %d/%d in %.1fs",
                r.status_code, attempt, _MAX_RETRIES, r.delay,
            )
            time.sleep(r.delay)
            continue
        except httpx.TransportError as e:
            # Connection/read errors. Only safe to retry if nothing was emitted.
            if committed or attempt >= _MAX_RETRIES:
                raise RuntimeError(f"Codex request failed: {e}") from e
            attempt += 1
            delay = _backoff_delay(attempt - 1)
            logger.warning(
                "Codex transport error (%s) — retry %d/%d in %.1fs",
                type(e).__name__, attempt, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
            continue


def _consume_sse(response):
    tool_call_buffers: dict[str, dict] = {}
    usage = None

    for event in _iter_sse(response):
        et = event.get("type")
        if et == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if call_id:
                    tool_call_buffers[call_id] = {
                        "id": item.get("id") or "fc_0",
                        "name": item.get("name"),
                        "arguments": item.get("arguments") or "",
                    }
        elif et == "response.output_item.done":
            # Safety net: if the .done event carries fully-materialized args and
            # we didn't receive deltas, use them.
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                final_args = item.get("arguments")
                if call_id and call_id in tool_call_buffers and final_args:
                    tool_call_buffers[call_id]["arguments"] = final_args
        elif et == "response.output_text.delta":
            delta = event.get("delta") or ""
            if delta:
                yield ("content", delta)
        elif et == "response.function_call_arguments.delta":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] += event.get("delta") or ""
        elif et == "response.function_call_arguments.done":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] = event.get("arguments") or ""
        elif et == "response.completed":
            resp = event.get("response") or {}
            usage_raw = resp.get("usage") or {}
            usage = {
                "input_tokens": int(usage_raw.get("input_tokens") or 0),
                "output_tokens": int(usage_raw.get("output_tokens") or 0),
            }
        elif et in ("error", "response.failed"):
            detail = event.get("error") or event.get("message") or event
            raise RuntimeError(f"Codex stream failed: {str(detail)[:500]}")

    if tool_call_buffers:
        tool_calls = []
        for call_id, buf in tool_call_buffers.items():
            tool_calls.append({
                "id": f"{call_id}|{buf['id']}",
                "function": {
                    "name": buf["name"] or "",
                    "arguments": buf["arguments"] or "{}",
                },
            })
        yield ("tool_calls", tool_calls)
    if usage:
        yield ("usage", usage)


def _iter_sse(response):
    buffer: list[str] = []

    def _flush():
        data_lines = [line[5:].strip() for line in buffer if line.startswith("data:")]
        buffer.clear()
        if not data_lines:
            return None
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except Exception:
            logger.warning("Failed to parse SSE event JSON: %s", data[:200])
            return None

    for line in response.iter_lines():
        if line == "":
            if buffer:
                event = _flush()
                if event is not None:
                    yield event
        else:
            buffer.append(line)
    if buffer:
        event = _flush()
        if event is not None:
            yield event


def _cache_key(messages):
    h = hashlib.sha256()
    h.update(json.dumps(messages, sort_keys=True, default=str).encode())
    return h.hexdigest()


def _convert_messages(messages):
    """Chat Completions messages -> (system_prompt, Responses API input items)."""
    system_prompt = ""
    input_items: list[dict] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            if isinstance(content, str):
                system_prompt = (system_prompt + "\n\n" + content).strip() if system_prompt else content
            continue

        if role == "user":
            input_items.append(_convert_user_content(content))
            continue

        if role == "assistant":
            if isinstance(content, str) and content:
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content}],
                    "status": "completed",
                    "id": f"msg_{idx}",
                })
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                call_id, item_id = _split_tool_call_id(tc.get("id"))
                input_items.append({
                    "type": "function_call",
                    "id": item_id or f"fc_{idx}",
                    "call_id": call_id or f"call_{idx}",
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                })
            continue

        if role == "tool":
            call_id, _ = _split_tool_call_id(msg.get("tool_call_id"))
            output = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            input_items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": output,
            })

    return system_prompt, input_items


def _convert_user_content(content):
    if isinstance(content, str):
        return {"role": "user", "content": [{"type": "input_text", "text": content}]}
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append({"type": "input_text", "text": item.get("text", "")})
        if parts:
            return {"role": "user", "content": parts}
    return {"role": "user", "content": [{"type": "input_text", "text": ""}]}


def _convert_tools(tools):
    converted = []
    for t in tools or []:
        fn = (t.get("function") or {}) if t.get("type") == "function" else t
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description") or "",
            "parameters": params if isinstance(params, dict) else {},
        })
    return converted


def _split_tool_call_id(value):
    if isinstance(value, str) and value and "|" in value:
        call_id, item_id = value.split("|", 1)
        return call_id, (item_id or None)
    return (value or "call_0"), None
