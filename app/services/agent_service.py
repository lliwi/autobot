import re

from flask import current_app

from app.extensions import db
from app.models.agent import Agent


def _slugify(name):
    slug = re.sub(r"[^\w\s-]", "", name.lower().strip())
    return re.sub(r"[\s_]+", "-", slug)


def create_agent(data):
    name = data["name"]
    slug = data.get("slug") or _slugify(name)
    model_name = data.get("model_name") or current_app.config["OPENAI_MODEL"]

    # Ensure unique slug
    base_slug = slug
    counter = 1
    while Agent.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    from app.workspace.manager import scaffold_workspace

    workspace_path = scaffold_workspace(slug)

    agent = Agent(
        name=name,
        slug=slug,
        model_name=model_name,
        workspace_path=workspace_path,
        oauth_profile_id=data.get("oauth_profile_id"),
    )
    db.session.add(agent)
    db.session.commit()
    return agent
