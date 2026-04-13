import os
import shutil
from pathlib import Path

from flask import current_app


def _base_path():
    return Path(current_app.config["WORKSPACES_BASE_PATH"]).resolve()


def _template_path():
    return Path(__file__).resolve().parent.parent.parent / "workspaces" / "_template"


def scaffold_workspace(slug):
    """Create a new workspace directory from the template."""
    workspace = _base_path() / slug
    workspace.mkdir(parents=True, exist_ok=True)

    template = _template_path()
    if template.exists():
        for src_file in template.iterdir():
            if src_file.is_file():
                dest = workspace / src_file.name
                if not dest.exists():
                    shutil.copy2(src_file, dest)

    # Create standard subdirectories
    for subdir in ("skills", "tools", "agents", "runs", "patches", "tests"):
        (workspace / subdir).mkdir(exist_ok=True)

    return str(workspace)


def get_workspace_path(agent):
    return Path(agent.workspace_path).resolve()


def read_file(agent, filename):
    filepath = get_workspace_path(agent) / filename
    if not filepath.exists():
        return ""
    return filepath.read_text(encoding="utf-8")


def write_file(agent, filename, content):
    filepath = get_workspace_path(agent) / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


def list_files(agent):
    workspace = get_workspace_path(agent)
    if not workspace.exists():
        return []
    return [str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()]
