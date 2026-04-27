from flask import Blueprint

api_bp = Blueprint("api", __name__)

# Exempt API from CSRF (uses JSON, not forms)
from app.extensions import csrf

csrf.exempt(api_bp)

from app.api import auth, agents, chat, runs, scheduler, metrics, skills, tools, subagents, patches, errors, promote  # noqa: E402, F401
