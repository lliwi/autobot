"""Retry/backoff behavior for the Codex model client.

The model call is the single point every agent run depends on, so a transient
429/5xx or a dropped connection must not sink the whole run. These tests drive
``stream_chat_completion`` with a faked ``httpx.stream`` to verify it retries
recoverable failures, gives up on deterministic ones, and never re-emits output
once streaming has started.
"""
import types

import httpx
import pytest

from app.runtime import model_client as mc

# A minimal SSE script: one text delta, then a completed event with usage.
_SUCCESS_LINES = [
    'data: {"type": "response.output_text.delta", "delta": "hi"}',
    "",
    'data: {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 2}}}',
    "",
]


class _FakeResponse:
    def __init__(self, status_code, headers=None, lines=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._lines = lines or []
        self._body = body

    def read(self):
        return self._body

    def iter_lines(self):
        return iter(self._lines)


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self._response

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def patched_auth(monkeypatch):
    monkeypatch.setattr(mc.codex_auth, "is_logged_in", lambda: True)
    monkeypatch.setattr(mc.codex_auth, "get_access_token", lambda: "tok")
    monkeypatch.setattr(mc.codex_auth, "get_account_id", lambda: "acct")
    monkeypatch.setattr(mc.time, "sleep", lambda _s: None)  # no real waiting


def _agent():
    return types.SimpleNamespace(model_name="gpt-5.2")


def _run(monkeypatch, responses):
    """Drive the client against a queue of fake responses; return (deltas, calls)."""
    queue = list(responses)
    calls = {"n": 0}

    def fake_stream(method, url, **kwargs):
        calls["n"] += 1
        return _FakeStreamCM(queue.pop(0))

    monkeypatch.setattr(mc.httpx, "stream", fake_stream)
    deltas = list(mc.stream_chat_completion(_agent(), [{"role": "user", "content": "hi"}]))
    return deltas, calls["n"]


def test_retries_on_429_then_succeeds(patched_auth, monkeypatch):
    deltas, calls = _run(
        monkeypatch,
        [_FakeResponse(429), _FakeResponse(200, lines=_SUCCESS_LINES)],
    )
    assert calls == 2
    assert ("content", "hi") in deltas


def test_retries_on_500_then_succeeds(patched_auth, monkeypatch):
    deltas, calls = _run(
        monkeypatch,
        [_FakeResponse(503), _FakeResponse(500), _FakeResponse(200, lines=_SUCCESS_LINES)],
    )
    assert calls == 3
    assert ("content", "hi") in deltas


def test_does_not_retry_on_400(patched_auth, monkeypatch):
    with pytest.raises(RuntimeError, match="Codex API 400"):
        _run(monkeypatch, [_FakeResponse(400, body=b"bad request")])


def test_gives_up_after_max_retries(patched_auth, monkeypatch):
    # _MAX_RETRIES retries means MAX+1 total attempts before raising.
    responses = [_FakeResponse(429) for _ in range(mc._MAX_RETRIES + 1)]
    with pytest.raises(RuntimeError, match="Codex API 429"):
        _run(monkeypatch, responses)


def test_retries_transport_error_then_succeeds(patched_auth, monkeypatch):
    calls = {"n": 0}

    def fake_stream(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return _FakeStreamCM(_FakeResponse(200, lines=_SUCCESS_LINES))

    monkeypatch.setattr(mc.httpx, "stream", fake_stream)
    deltas = list(mc.stream_chat_completion(_agent(), [{"role": "user", "content": "hi"}]))
    assert calls["n"] == 2
    assert ("content", "hi") in deltas


_USAGE_LIMIT_BODY = (
    b'{"error":{"type":"usage_limit_reached",'
    b'"message":"The usage limit has been reached",'
    b'"plan_type":"plus","resets_at":1782561606,"resets_in_seconds":7964}}'
)


def test_usage_limit_429_fails_fast_without_retry(patched_auth, monkeypatch):
    # A quota 429 won't clear within the request (reset is hours away), so it
    # must raise immediately on the FIRST attempt — no retry storm.
    calls = {"n": 0}

    def fake_stream(method, url, **kwargs):
        calls["n"] += 1
        return _FakeStreamCM(_FakeResponse(429, body=_USAGE_LIMIT_BODY))

    monkeypatch.setattr(mc.httpx, "stream", fake_stream)
    with pytest.raises(mc.UsageLimitReached) as ei:
        list(mc.stream_chat_completion(_agent(), [{"role": "user", "content": "hi"}]))

    assert calls["n"] == 1  # not retried
    err = ei.value
    assert err.plan_type == "plus"
    assert err.resets_in_seconds == 7964
    assert err.resets_at == 1782561606


def test_usage_limit_message_is_human_friendly(patched_auth):
    err = mc.UsageLimitReached(plan_type="plus", resets_at=1782561606,
                               resets_in_seconds=7964, message="The usage limit has been reached")
    s = str(err)
    assert s.startswith("Codex usage limit reached")
    assert "plus plan" in s
    # Reset info surfaced for operators; no raw JSON dump.
    assert "resets at" in s and "{" not in s


def test_plain_text_usage_limit_is_detected(patched_auth, monkeypatch):
    # Some gateways return a non-JSON 429 body that only contains the marker.
    def fake_stream(method, url, **kwargs):
        return _FakeStreamCM(_FakeResponse(429, body=b"429 usage_limit_reached"))

    monkeypatch.setattr(mc.httpx, "stream", fake_stream)
    with pytest.raises(mc.UsageLimitReached):
        list(mc.stream_chat_completion(_agent(), [{"role": "user", "content": "hi"}]))


def test_honors_retry_after_header(patched_auth, monkeypatch):
    slept = []
    monkeypatch.setattr(mc.time, "sleep", lambda s: slept.append(s))
    _run(
        monkeypatch,
        [_FakeResponse(429, headers={"retry-after": "7"}), _FakeResponse(200, lines=_SUCCESS_LINES)],
    )
    assert slept == [7.0]
