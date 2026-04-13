from app.workspace.manager import read_file

WORKSPACE_FILES = ("SOUL.md", "AGENTS.md", "MEMORY.md", "TOOLS.md")


def load_soul(agent):
    return read_file(agent, "SOUL.md")


def load_agents(agent):
    return read_file(agent, "AGENTS.md")


def load_memory(agent):
    return read_file(agent, "MEMORY.md")


def load_tools(agent):
    return read_file(agent, "TOOLS.md")


def load_full_context(agent):
    """Load all workspace context files as a dict."""
    return {name: read_file(agent, name) for name in WORKSPACE_FILES}
