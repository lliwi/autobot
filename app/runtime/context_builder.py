from flask import current_app

from app.models.message import Message
from app.runtime.context_budget import (
    count_messages_tokens,
    effective_budget,
    trim_history_to_budget,
)
from app.workspace.loader import (
    load_memory,
    load_packages,
    load_security_baseline,
    load_soul,
)

# DB-side safety net: never pull more than this many rows per turn regardless
# of what the token budget would allow. Protects the runtime from sessions
# that have grown into the thousands of messages. The token budget is the
# real limit; this just keeps the SQL query bounded.
_DB_HISTORY_HARD_CAP = 500

TOOL_PROTOCOL = """## Action-first protocol (MANDATORY)

You are an **agent**, not a chatbot. The user gives you tasks; you execute them with tool calls. Talking is not work.

Core rule — no promises, just action:
- When the user asks you to do something, your **first response MUST include at least one tool call**. Not a description of what you will do — the actual call.
- **FORBIDDEN phrases** in any assistant response: "voy a", "vamos a", "lo haré", "lo ejecutaré", "procedo a", "paso a", "ahora ejecuto", "si te parece procedo", "I will", "I'll", "let me", "going to", "I plan to". If you catch yourself writing one of these, delete it and call the tool instead.
- If you finish a turn without a tool call on a task request, you have failed the turn. The runtime will re-prompt you once; do not rely on that safety net.
- Do NOT ask for permission before acting on a task the user already requested. Act, then report. Only stop to ask when you hit a missing credential, an ambiguous target (e.g. two matching items), or a destructive/irreversible step not in the original ask.
- When a sub-agent exists for the job, delegate via `delegate_task`. When no sub-agent fits, do the work yourself using the available tools. Never say "that agent doesn't exist" without first calling `list_subagents` to confirm.
- Report only **after** you acted. The format is: what you called → what it returned → what's left (if anything). No preamble.

## Tool Usage Protocol

Before any tool call, write **one short line of plan** describing the next step. Then call the tool. If the step is obvious, skip the plan.

Hard rules:
- NEVER call a tool without its `required` arguments filled in. If you don't know a value, obtain it with another tool first — don't ask the user unless no tool can find it.
- Do not repeat the same tool call with the same arguments. The runtime aborts the run after 3 identical repetitions.
- Prefer one precise call over many exploratory ones. Do not call a tool "to see what happens".
- When a tool returns `{"error": ...}`, read the hint, correct the arguments, and try a **different** approach. Do not retry unchanged.
- "Can't do X" is not a valid terminal answer. Before declaring a block: try at least 2 approaches (different tool, smaller scope, delegation, `create_tool`/`fetch_url` fallback), and only then report the specific blocker with the exact cause.

Built-in tool cheatsheet (always call with JSON objects like these):
- `list_workspace_files` — no args. Use once at the start if you need to discover files.
- `read_workspace_file` — `{"filename": "SOUL.md"}`. filename must already exist in the workspace. **Reference docs like `TOOLS.md`, `AGENTS.md`, and per-skill `SKILL.md` files are NOT pre-loaded into your prompt** — they're listed in the "Workspace index" section. Call this tool to read them only when the current task actually needs those details. Second reads of the same file in the same turn return a cached stub; re-use the content you already saw instead of calling again.
- `fetch_url` — `{"url": "https://example.com"}`. Absolute http(s) URL required.
- `propose_change` — `{"target_path": "MEMORY.md", "new_content": "...full content...", "title": "Update memory", "reason": "why"}`. All four fields required. Use for single-file edits.
- `create_skill` — `{"slug": "weather-bcn", "title": "Weather Barcelona", "summary": "...", "instructions": "...markdown...", "code": "def handler(...): ..."}`. Preferred over two propose_change calls when creating a skill.
- `create_tool` — `{"slug": "echo2", "description": "...", "parameters_schema": {"type":"object","properties":{...},"required":[...]}, "code": "def handler(_agent=None, **kwargs): return {...}"}`.
- `delegate_task` — `{"target_name": "reviewer", "message": "review the patch #42"}`.
- `schedule_task` — `{"schedule_expr": "0 18 * * *", "message": "...prompt to run..."}`. Use when the user asks for a recurring/daily/weekly task. 5-field cron, UTC.
- `list_scheduled_tasks` / `cancel_scheduled_task` — manage this agent's scheduled tasks (`cancel_scheduled_task` needs `{"task_id": N}`).
- `get_credential` — `{"name": "github_token"}`. Returns `{type: "token", value, source}` or `{type: "user_password", username, password, source}`. Lookup order: agent-scoped DB → global DB → `AUTOBOT_CRED_<UPPER(name)>` environment variable (token-only). Treat values as sensitive: never echo them to the user or write them to workspace files. **Do not second-guess the format**: use the value as-is even if the prefix/length looks unfamiliar (token formats change — e.g. Notion moved to `ntn_` from the older `secret_`). If the target API rejects it, quote the API's error verbatim instead of speculating about format.
- `list_credentials` — no args. Returns names, types, descriptions, and `source: "db"|"env"` — no secret values. Credentials sourced from `.env` appear with `scope: "env"`.
- `set_credential` — `{"name": "...", "value": "...", "description": "..."}` for a single-value token, or `{"name": "...", "credential_type": "user_password", "username": "...", "value": "<password>"}` for a user+password pair. Always agent-scoped.
- `delete_credential` — `{"name": "..."}`. Removes an agent-scoped credential.
- `get_current_time` — no args. Returns ISO-8601 UTC.
- `list_subagents` / `list_patches` — no required args.
- `install_package` — `{"spec": "feedparser"}` or `{"spec": "pandas>=2,<3"}`. Installs into this agent's isolated workspace venv. Specs on the platform allowlist install immediately; everything else returns `status: "pending_review"` and waits for admin approval. Never pass git URLs, paths, or pip flags.
- `list_packages` — no args. Returns this agent's install history with statuses (`installed`, `pending_review`, `failed`, `rejected`). Check here before requesting an install so you don't duplicate a pending row.

Multi-step task template:
1. State the plan in one line.
2. Gather any information you need (one tool call at a time).
3. Produce the artefact(s) with `propose_change`.
4. Summarise what you did for the user.

Auto-review:
- When a reviewer sub-agent exists, `create_skill`, `create_tool` and `schedule_task` automatically delegate an audit to it. Their response contains a `review` field with the reviewer's feedback. If the review flags something concrete, mention it to the user or fix it in a follow-up step.
"""


def build_context(agent, session, user_message):
    """Build the messages array for the OpenAI API call.

    Token-budgeted: the system prompt is always preserved intact, the current
    user turn is always preserved, and chat history is packed newest-first
    until the token budget is reached. See ``context_budget`` for the rules.
    """
    # System prompt from workspace files. Small, behavior-critical docs are
    # inlined; large reference docs (TOOLS.md, AGENTS.md, per-skill SKILL.md)
    # are replaced by a manifest so the agent reads them via
    # ``read_workspace_file`` only when it actually needs them. Typical saving
    # is ~3-4K tokens/turn.
    security = load_security_baseline()
    soul = load_soul(agent)
    memory = load_memory(agent)
    packages = load_packages(agent)

    # Security baseline goes first so downstream instructions can't override
    # it by sheer prompt-order. TOOL_PROTOCOL follows with the operational
    # rules.
    system_parts = []
    if security:
        system_parts.append(f"# Platform security baseline (non-negotiable)\n{security}")
    system_parts.append(TOOL_PROTOCOL)
    if soul:
        system_parts.append(f"## Identity and Principles\n{soul}")

    live_roster = _render_live_agent_roster(agent)
    if live_roster:
        system_parts.append(live_roster)
    if memory:
        system_parts.append(f"## Memory\n{memory}")
    if packages:
        system_parts.append(f"## Workspace Packages\n{packages}")

    # Lazy-load manifest for heavy reference docs. The agent calls
    # ``read_workspace_file`` against the exact paths listed here when it
    # needs the details.
    manifest = _render_workspace_manifest(agent)
    if manifest:
        system_parts.append(manifest)

    # Pending items (patches + packages) so the agent sees what's already in
    # the review queue and doesn't re-propose an identical change every turn.
    pending_section = _render_pending_items(agent)
    if pending_section:
        system_parts.append(pending_section)

    system_messages: list[dict] = []
    if system_parts:
        system_messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    # Pull the newest N messages, then reverse to chronological order. The
    # previous implementation used ``.order_by(asc).limit(N)`` which returned
    # the OLDEST N rows — after ~50 messages the chat effectively froze its
    # memory in the distant past.
    newest_rows = (
        Message.query.filter_by(session_id=session.id)
        .order_by(Message.created_at.desc())
        .limit(_DB_HISTORY_HARD_CAP)
        .all()
    )
    history_rows = list(reversed(newest_rows))

    # ``chat_service`` persists the user turn *before* calling the runtime so
    # it appears in the session history immediately. That means the row we
    # just loaded includes the current turn — strip it so we don't duplicate
    # the user message (it's added back via ``user_turn`` below).
    if (
        history_rows
        and history_rows[-1].role == "user"
        and history_rows[-1].content == user_message
    ):
        history_rows = history_rows[:-1]

    history_messages = [{"role": m.role, "content": m.content} for m in history_rows]
    user_turn = {"role": "user", "content": user_message}

    budget = effective_budget(
        current_app.config["MAX_CONTEXT_TOKENS"],
        current_app.config.get("CONTEXT_RESPONSE_RESERVE_TOKENS"),
    )
    result = trim_history_to_budget(system_messages, history_messages, user_turn, budget)

    if result.dropped or result.over_budget:
        level = current_app.logger.warning if result.over_budget else current_app.logger.info
        level(
            "context_budget: agent=%s session=%s budget=%d total=%d "
            "system=%d history=%d user=%d kept=%d dropped=%d over_budget=%s",
            agent.id, session.id, result.budget, result.total_tokens,
            result.system_tokens, result.history_tokens, result.user_tokens,
            result.kept, result.dropped, result.over_budget,
        )

    return result.messages


def estimate_context_tokens(agent, session, user_message) -> dict:
    """Introspection helper: how many tokens would this turn cost?

    Runs the full assembly without side effects so dashboards/observability
    can report live context pressure. Returns the same numbers that
    ``build_context`` would log.
    """
    messages = build_context(agent, session, user_message)
    total = count_messages_tokens(messages)
    budget = effective_budget(
        current_app.config["MAX_CONTEXT_TOKENS"],
        current_app.config.get("CONTEXT_RESPONSE_RESERVE_TOKENS"),
    )
    return {
        "total_tokens": total,
        "budget": budget,
        "headroom": budget - total,
        "message_count": len(messages),
    }


def _render_workspace_manifest(agent) -> str:
    """List reference docs the agent can read on demand.

    Replaces inlining of ``TOOLS.md``, ``AGENTS.md`` and every enabled
    skill's ``SKILL.md`` body in the system prompt. Each entry gives the
    exact path plus a one-line description so the model knows what it can
    fetch without having to guess filenames.
    """
    from app.workspace.discovery import get_enabled_skills
    from app.workspace.manager import list_files

    available = set(list_files(agent) or [])

    entries: list[tuple[str, str]] = []

    if "TOOLS.md" in available:
        entries.append(("TOOLS.md", "Workspace tool inventory and usage notes."))
    if "AGENTS.md" in available:
        entries.append(("AGENTS.md",
                        "Sub-agent catalog with full descriptions (live roster above is authoritative)."))

    for skill in get_enabled_skills(agent):
        path = f"{skill.path}/SKILL.md"
        desc = (skill.description or "").strip() or f"Skill: {skill.name}"
        desc = desc.splitlines()[0][:140]
        entries.append((path, desc))

    if not entries:
        return ""

    lines = [
        "## Workspace index (read on demand)",
        "",
        "Reference docs and skill details are NOT pre-loaded to save tokens."
        " Call `read_workspace_file` with the exact path below only when the"
        " current task needs that content:",
        "",
    ]
    for path, desc in entries:
        lines.append(f"- `{path}` — {desc}")
    return "\n".join(lines)


def _render_live_agent_roster(agent) -> str:
    """Live list of sub-agents available for delegation, built from the DB.

    Overrides any stale entry in ``AGENTS.md``: the file is edited by humans
    and by `_register_in_agents_md`, so parent changes made via the admin
    "edit agent" screen never propagated. The runtime needs the real roster.
    """
    from app.models.agent import Agent

    children = (
        Agent.query
        .filter_by(parent_agent_id=agent.id, status="active")
        .order_by(Agent.name)
        .all()
    )
    if not children:
        return ""

    lines = [
        "## Active sub-agents (live from DB — authoritative)",
        "",
        "You may delegate to any of these using"
        " `delegate_task` with `target_name` set to the slug or name.",
        "",
    ]
    for c in children:
        lines.append(f"- **{c.name}** (slug `{c.slug}`, model `{c.model_name or '?'}`)")
    return "\n".join(lines)


def _render_pending_items(agent) -> str:
    """Return a short Markdown list of this agent's pending review items.

    Empty string when there's nothing pending, so the section only shows up
    when the agent actually needs to reason about queued work.
    """
    from app.models.patch_proposal import PatchProposal
    from app.models.package_installation import PackageInstallation

    patches = (
        PatchProposal.query
        .filter_by(agent_id=agent.id, status="pending_review")
        .order_by(PatchProposal.id.desc())
        .limit(10)
        .all()
    )
    packages = (
        PackageInstallation.query
        .filter_by(agent_id=agent.id, status="pending_review")
        .order_by(PackageInstallation.id.desc())
        .limit(10)
        .all()
    )
    if not patches and not packages:
        return ""

    lines = [
        "## Pending review",
        "",
        "These items are already queued. Do **not** re-propose identical"
        " changes — mention them to the user or wait for approval instead.",
        "",
    ]
    if patches:
        lines.append("Patches:")
        for p in patches:
            lines.append(f"- patch #{p.id} · L{p.security_level} · `{p.target_path}` — {p.title}")
        lines.append("")
    if packages:
        lines.append("Package installs:")
        for pk in packages:
            lines.append(f"- package #{pk.id} · `{pk.name}` (spec `{pk.spec}`)")
        lines.append("")
    return "\n".join(lines)
