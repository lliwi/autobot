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
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "o4-mini")
    MAX_CONTEXT_TOKENS = int(os.environ.get("MAX_CONTEXT_TOKENS", "128000"))
    MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "50"))

    # Matrix
    MATRIX_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "")
    MATRIX_USER_ID = os.environ.get("MATRIX_USER_ID", "")
    MATRIX_PASSWORD = os.environ.get("MATRIX_PASSWORD", "")
    MATRIX_ALLOWED_ROOMS = os.environ.get("MATRIX_ALLOWED_ROOMS", "")  # comma-separated
    MATRIX_ALLOWED_USERS = os.environ.get("MATRIX_ALLOWED_USERS", "")  # comma-separated
    MATRIX_GROUP_POLICY = os.environ.get("MATRIX_GROUP_POLICY", "mention")  # always, mention, allowlist

    # Scheduler
    SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "true").lower() == "true"
    HEARTBEAT_INTERVAL_MINUTES = int(os.environ.get("HEARTBEAT_INTERVAL_MINUTES", "15"))

    # Rate limiting
    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Session
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"


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
