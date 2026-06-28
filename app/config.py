import json
import os

from dotenv import load_dotenv

load_dotenv()

# USD per 1,000 tokens as ``(input, output)``, keyed by a model-name substring;
# the longest matching key wins, so "gpt-5.4-mini" beats "gpt-5.4" beats "gpt-5".
# These mirror the Codex models exposed by ``codex_auth.list_models()``. Codex is
# billed via subscription, so the figures are estimates for metrics/budgets —
# tune them per deployment with the ``MODEL_PRICING_JSON`` env var.
_DEFAULT_MODEL_PRICING = {
    "gpt-5.5": (0.00125, 0.01000),
    "gpt-5.4-mini": (0.00015, 0.00060),
    "gpt-5.4": (0.00125, 0.01000),
    "gpt-5.3-codex": (0.00125, 0.01000),
    "gpt-5.2": (0.00125, 0.01000),
    "gpt-5": (0.00125, 0.01000),  # generic fallback for any other gpt-5.x
    "mini": (0.00015, 0.00060),   # generic fallback for any *-mini variant
    "o4-mini": (0.00015, 0.00060),
    "default": (0.00125, 0.01000),
}


def _load_model_pricing():
    raw = os.environ.get("MODEL_PRICING_JSON")
    if not raw:
        return dict(_DEFAULT_MODEL_PRICING)
    try:
        parsed = json.loads(raw)
        return {k: tuple(v) for k, v in parsed.items()}
    except (ValueError, TypeError):
        return dict(_DEFAULT_MODEL_PRICING)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "postgresql://autobot:autobot@localhost:5432/autobot")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Workspace
    WORKSPACES_BASE_PATH = os.environ.get("WORKSPACES_BASE_PATH", os.path.join(os.path.dirname(__file__), "..", "workspaces"))

    # OpenAI Codex OAuth
    OPENAI_CLIENT_ID = os.environ.get("OPENAI_CLIENT_ID", "")
    OPENAI_CLIENT_SECRET = os.environ.get("OPENAI_CLIENT_SECRET", "")
    OPENAI_REDIRECT_URI = os.environ.get("OPENAI_REDIRECT_URI", "http://localhost:5000/api/oauth/openai/callback")

    # Token encryption
    TOKEN_ENCRYPTION_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "")

    # Agent defaults
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.2")
    MAX_CONTEXT_TOKENS = int(os.environ.get("MAX_CONTEXT_TOKENS", "128000"))
    # Tokens held back from the prompt budget for the model's own reply so the
    # request+completion fits the window. Default 8000 covers a long answer.
    CONTEXT_RESPONSE_RESERVE_TOKENS = int(os.environ.get("CONTEXT_RESPONSE_RESERVE_TOKENS", "8000"))
    # Legacy knob: kept for .env compatibility but no longer a hard cap on
    # history — the token budget above is authoritative. Set to a very high
    # value or ignore entirely. Left in config so older deployments don't
    # blow up on missing key.
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "50"))
    # Hard cap on tool-call rounds per run. Prevents runaway loops when the
    # model keeps calling tools without converging. Individual agents can
    # override this via the ``max_tool_rounds`` column.
    MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "20"))

    # Model pricing in USD per 1,000 tokens as ``(input, output)`` tuples, keyed
    # by a substring of the model name (longest key wins). Used to estimate
    # ``Run.estimated_cost`` for metrics and daily cost budgets. Override the
    # whole table with the ``MODEL_PRICING_JSON`` env var, e.g.
    # '{"gpt-5.2": [0.00125, 0.01], "default": [0.00015, 0.0006]}'.
    MODEL_PRICING = _load_model_pricing()

    # Self-improvement rate limit. Caps how many patches a single agent can
    # produce per rolling hour, counting only states that consume budget
    # (applied + pending_review). Rejected patches don't count — we don't want
    # to punish an agent whose first try was syntactically broken. Set to 0 to
    # disable the limit entirely.
    PATCHES_PER_HOUR_PER_AGENT = int(os.environ.get("PATCHES_PER_HOUR_PER_AGENT", "30"))

    # Kali MCP Server
    KALI_MCP_URL = os.environ.get("KALI_MCP_URL", "http://kali:8000")
    KALI_MCP_TIMEOUT = int(os.environ.get("KALI_MCP_TIMEOUT", "120"))

    # Incident autopilot. When enabled, ERROR/CRITICAL log records (and failed
    # runs) raise an IncidentReport, a reviewer agent diagnoses it and drafts an
    # Issue or PR. The draft waits for human approval in the dashboard before
    # anything is opened on GitHub (see app/services/incident_service.py).
    INCIDENT_AUTOPILOT_ENABLED = os.environ.get("INCIDENT_AUTOPILOT_ENABLED", "true").lower() in ("1", "true", "yes")
    # Minimum severity that triggers an incident: "error" (ERROR+CRITICAL) or
    # "critical" (CRITICAL only). WARNING is intentionally never auto-triggered.
    INCIDENT_MIN_SEVERITY = os.environ.get("INCIDENT_MIN_SEVERITY", "error").lower()
    # Dedup cooldown: a given error signature raises at most one incident per
    # this many hours; further occurrences just bump the counter.
    INCIDENT_DEDUP_COOLDOWN_HOURS = int(os.environ.get("INCIDENT_DEDUP_COOLDOWN_HOURS", "12"))
    # Logger-name prefixes whose records never raise incidents (avoids feedback
    # loops from the incident pipeline itself). Comma-separated.
    INCIDENT_IGNORE_LOGGERS = os.environ.get(
        "INCIDENT_IGNORE_LOGGERS",
        "app.services.incident_service,app.services.github_service,app.services.review_queue_service",
    )

    # Matrix
    MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "")
    MATRIX_USER_ID = os.environ.get("MATRIX_USER_ID", "")
    MATRIX_PASSWORD = os.environ.get("MATRIX_PASSWORD", "")
    MATRIX_ALLOWED_ROOMS = os.environ.get("MATRIX_ALLOWED_ROOMS", "")  # comma-separated
    MATRIX_ALLOWED_USERS = os.environ.get("MATRIX_ALLOWED_USERS", "")  # comma-separated
    MATRIX_ALLOWED_DM_USERS = os.environ.get("MATRIX_ALLOWED_DM_USERS", "")  # DM-only allowlist; empty = fall back to MATRIX_ALLOWED_USERS
    MATRIX_GROUP_POLICY = os.environ.get("MATRIX_GROUP_POLICY", "mention")  # always, mention, allowlist
    # Slug of the agent that handles Matrix messages when no room-specific
    # mapping is found. Leave empty to fall back to the first active agent.
    MATRIX_DEFAULT_AGENT_SLUG = os.environ.get("MATRIX_DEFAULT_AGENT_SLUG", "")

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    HEARTBEAT_INTERVAL_MINUTES = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "15"))

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Cost alert: if today's estimated cost (USD) reaches this threshold an
    # alert banner is shown on the Metrics dashboard. Leave unset to disable.
    COST_ALERT_EUR_DAILY = os.environ.get("COST_ALERT_EUR_DAILY") or None

    # Per-workspace Python environment.
    #   * PACKAGE_ALLOWLIST: comma-separated pypi names that auto-install. Others
    #     need admin approval from the dashboard.
    #   * VENV_BASE_PACKAGES: installed automatically into every new workspace
    #     venv so tools/skills have a sensible starting toolbox.
    #   * PIP_INSTALL_TIMEOUT_SECONDS: hard cap on any `pip install` subprocess.
    #   * WORKSPACE_TOOL_TIMEOUT_SECONDS: hard cap on workspace tool subprocess
    #     calls (builtin tools still run in-process).
    PACKAGE_ALLOWLIST = os.environ.get(
        "PACKAGE_ALLOWLIST",
        "requests,httpx,beautifulsoup4,lxml,feedparser,markdown,pyyaml,python-dateutil,pytz,pydantic",
    )
    VENV_BASE_PACKAGES = os.environ.get(
        "VENV_BASE_PACKAGES",
        "httpx,pydantic",
    )
    PIP_INSTALL_TIMEOUT_SECONDS = int(os.environ.get("PIP_INSTALL_TIMEOUT_SECONDS", "180"))
    WORKSPACE_TOOL_TIMEOUT_SECONDS = int(os.environ.get("WORKSPACE_TOOL_TIMEOUT_SECONDS", "30"))

    # Display timezone for admin UI. Defaults to the container's ``TZ`` when
    # set (so matching .env is enough), otherwise Europe/Madrid for this
    # project. All timestamps in the DB stay in UTC — this only affects
    # rendering via the ``localtz`` Jinja filter.
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE") or os.environ.get("TZ") or "Europe/Madrid"

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Uploads
    AVATAR_UPLOAD_DIR = os.environ.get(
        "AVATAR_UPLOAD_DIR",
        os.path.join(os.path.dirname(__file__), "..", "instance", "avatars"),
    )
    AVATAR_MAX_BYTES = int(os.environ.get("AVATAR_MAX_BYTES", str(2 * 1024 * 1024)))  # 2 MB
    MFA_ISSUER = os.environ.get("MFA_ISSUER", "Autobot")


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///test.db"
    WTF_CSRF_ENABLED = False


class ProductionConfig(Config):
    DEBUG = False
    # Secure cookies are only sent over HTTPS. Default on for real deployments,
    # but overridable: when serving over plain HTTP on a trusted LAN (e.g. a
    # macvlan IP with no TLS) the browser would otherwise drop the session
    # cookie, so login fails with "The CSRF session token is missing" before the
    # MFA step. Set SESSION_COOKIE_SECURE=false in the environment for that case.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
