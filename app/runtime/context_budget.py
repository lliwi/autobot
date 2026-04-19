"""Token counting and context budget helpers.

The runtime rebuilds a fat system prompt every turn (SOUL + TOOL_PROTOCOL +
TOOLS/AGENTS/MEMORY.md + live roster + each enabled skill's SKILL.md +
pending items) and appends the chat history. Without a token-aware budget a
long conversation or a couple of heavy tool outputs will silently bust the
model's context window.

Public API:
  - ``count_tokens`` / ``count_message_tokens`` / ``count_messages_tokens``:
    best-effort token counting (tiktoken when available, chars/4 fallback).
  - ``trim_history_to_budget``: drop-oldest trimming that keeps the system
    prompt and the current user turn intact, and returns an observability
    struct the caller logs.
  - ``effective_budget``: prompt budget after reserving room for the reply.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# OpenAI's chat wire format adds framing tokens around each message (role
# tag, separators). A small constant keeps the estimate honest without
# hard-coding a specific model's exact framing.
_PER_MESSAGE_OVERHEAD = 4

# Room left for the model's own response so request+completion fits the
# window. Overridable via config; this is the default when nothing else is
# set and ``effective_budget`` falls back.
_DEFAULT_RESPONSE_RESERVE = 8000

# Singleton tokenizer handle. ``None`` once we've probed and failed — avoids
# re-importing tiktoken on every token count in a hot path.
_TOKENIZER_PROBED = False
_TOKENIZER = None


def _tokenizer():
    """Return a cached tiktoken encoding (cl100k_base) or ``None``.

    cl100k_base is the GPT-3.5/GPT-4 encoding. It's not a perfect match for
    every model the runtime can target but it's close enough to drive trim
    decisions, which is all we use the count for.
    """
    global _TOKENIZER_PROBED, _TOKENIZER
    if _TOKENIZER_PROBED:
        return _TOKENIZER
    _TOKENIZER_PROBED = True
    try:
        import tiktoken
        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        logger.info("tiktoken unavailable, using chars/4 heuristic: %s", e)
        _TOKENIZER = None
    return _TOKENIZER


def count_tokens(text: str) -> int:
    """Best-effort token count for a string.

    Falls back to ``max(1, len(text) // 4)`` when tiktoken isn't importable.
    Slightly pessimistic on code-heavy strings which is fine for budgeting
    (we'd rather trim one turn too many than blow the context window).
    """
    if not text:
        return 0
    enc = _tokenizer()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            # Encoder can theoretically trip on pathological input; heuristic
            # keeps trimming functional rather than crashing the run.
            pass
    return max(1, len(text) // 4)


def count_message_tokens(message: dict) -> int:
    """Token cost of a single chat message: content + tool_calls + overhead."""
    content = message.get("content") or ""
    extra = 0
    # Assistant messages with tool_calls carry a structured payload that the
    # model sees on the wire — account for it so the budget isn't fooled.
    tool_calls = message.get("tool_calls")
    if tool_calls:
        try:
            extra = count_tokens(json.dumps(tool_calls))
        except Exception:
            extra = 0
    return count_tokens(content) + extra + _PER_MESSAGE_OVERHEAD


def count_messages_tokens(messages: list[dict]) -> int:
    return sum(count_message_tokens(m) for m in messages)


@dataclass
class BudgetResult:
    """Outcome of ``trim_history_to_budget`` — messages + observability."""
    messages: list[dict]
    system_tokens: int
    history_tokens: int
    user_tokens: int
    total_tokens: int
    dropped: int
    kept: int
    budget: int
    over_budget: bool


def trim_history_to_budget(
    system_messages: list[dict],
    history: list[dict],
    user_message: dict | None,
    budget: int,
) -> BudgetResult:
    """Drop-oldest history trimming.

    Rules:
      * System messages are behavior-critical — never dropped.
      * The current ``user_message`` is the ask being answered — never dropped.
      * Walk history newest-first, keep turns until tokens exceed budget.
      * Reassemble chronologically: system + kept-history + user.

    When the fixed parts (system + user) alone exceed budget we still return
    them with ``over_budget=True``. Silently dropping the user's question is
    worse than letting the model refuse or truncate — the caller logs and the
    runtime surfaces the failure.
    """
    system_tokens = count_messages_tokens(system_messages)
    user_tokens = count_message_tokens(user_message) if user_message else 0
    fixed_tokens = system_tokens + user_tokens

    remaining = budget - fixed_tokens
    kept_reversed: list[dict] = []
    history_tokens = 0

    if remaining > 0:
        for msg in reversed(history):
            cost = count_message_tokens(msg)
            if history_tokens + cost > remaining:
                break
            kept_reversed.append(msg)
            history_tokens += cost

    kept = list(reversed(kept_reversed))
    dropped = len(history) - len(kept)

    messages: list[dict] = [*system_messages, *kept]
    if user_message is not None:
        messages.append(user_message)

    return BudgetResult(
        messages=messages,
        system_tokens=system_tokens,
        history_tokens=history_tokens,
        user_tokens=user_tokens,
        total_tokens=system_tokens + history_tokens + user_tokens,
        dropped=dropped,
        kept=len(kept),
        budget=budget,
        over_budget=fixed_tokens > budget,
    )


def effective_budget(max_context_tokens: int, response_reserve: int | None = None) -> int:
    """Tokens available for the prompt after reserving room for the reply.

    Floor at 1024 so a misconfigured tiny ``MAX_CONTEXT_TOKENS`` doesn't
    produce a negative or zero budget that silently drops everything.
    """
    reserve = response_reserve if response_reserve is not None else _DEFAULT_RESPONSE_RESERVE
    return max(1024, max_context_tokens - reserve)
