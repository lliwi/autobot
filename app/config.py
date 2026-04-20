import os

from dotenv import load_dotenv

load_dotenv()


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

    # Matrix
    MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "")
    MATRIX_USER_ID = os.environ.get("MATRIX_USER_ID", "")
    MATRIX_PASSWORD = os.environ.get("MATRIX_PASSWORD", "")
    MATRIX_ALLOWED_ROOMS = os.environ.get("MATRIX_ALLOWED_ROOMS", "")  # comma-separated
    MATRIX_ALLOWED_USERS = os.environ.get("MATRIX_ALLOWED_USERS", "")  # comma-separated
    MATRIX_ALLOWED_DM_USERS = os.environ.get("MATRIX_ALLOWED_DM_USERS", "")  # DM-only allowlist; empty = fall back to MATRIX_ALLOWED_USERS
    MATRIX_GROUP_POLICY = os.environ.get("MATRIX_GROUP_POLICY", "mention")  # always, mention, allowlist

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    HEARTBEAT_INTERVAL_MINUTES = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "15"))

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

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
    SESSION_COOKIE_SECURE = True


config = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
