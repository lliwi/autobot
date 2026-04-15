"""Security policy engine for self-improvement.

Classifies workspace changes into security levels:
  Level 1 (auto-allowed): MEMORY.md, new skills/tools, workspace manifests
  Level 2 (requires approval): existing skill .py files, AGENTS.md, TOOLS.md, sub-agents
  Level 3 (prohibited in MVP): core app, OAuth, DB/migrations, security policies
"""

import re

# Level 1 — auto-allowed
_LEVEL_1_PATTERNS = [
    r"^MEMORY\.md$",
    r"^skills/[^/]+/manifest\.json$",
    r"^skills/[^/]+/SKILL\.md$",
    r"^tools/[^/]+/manifest\.json$",
]

# Level 1 only for NEW files (creation, not modification)
_LEVEL_1_NEW_ONLY_PATTERNS = [
    r"^skills/[^/]+/skill\.py$",
    r"^skills/[^/]+/",
    r"^tools/[^/]+/tool\.py$",
    r"^tools/[^/]+/",
]

# Level 2 — requires approval
_LEVEL_2_PATTERNS = [
    r"^AGENTS\.md$",
    r"^TOOLS\.md$",
    r"^skills/[^/]+/skill\.py$",   # modification of existing
    r"^tools/[^/]+/tool\.py$",     # modification of existing
    r"^agents/",                    # sub-agent workspace files
]

# Level 3 — prohibited paths (anything outside workspace is implicitly level 3)
_PROHIBITED_PATTERNS = [
    r"^\.\.\/",          # path traversal
    r"^\.\.",            # path traversal
    r"^/",              # absolute paths
    r"^SOUL\.md$",      # identity is protected in MVP
]


def classify_target(target_path, is_new_file=False):
    """Classify a workspace-relative path into a security level.

    Args:
        target_path: Relative path within the agent workspace.
        is_new_file: True if this is a new file creation, False if modification.

    Returns:
        Security level (1, 2, or 3).
    """
    path = target_path.strip("/")

    # Level 3 — prohibited
    for pattern in _PROHIBITED_PATTERNS:
        if re.match(pattern, path):
            return 3

    # Level 1 — always auto-allowed
    for pattern in _LEVEL_1_PATTERNS:
        if re.match(pattern, path):
            return 1

    # Level 1 — new files in skills/tools directories
    if is_new_file:
        for pattern in _LEVEL_1_NEW_ONLY_PATTERNS:
            if re.match(pattern, path):
                return 1

    # Level 2 — requires approval
    for pattern in _LEVEL_2_PATTERNS:
        if re.match(pattern, path):
            return 2

    # Default: anything else in workspace is level 2
    return 2


def classify_target_type(target_path):
    """Determine the target_type from a workspace path.

    Returns one of: memory, skill, tool, agents, config.
    """
    path = target_path.strip("/")

    if path == "MEMORY.md":
        return "memory"
    if path == "AGENTS.md":
        return "agents"
    if path in ("TOOLS.md", "SOUL.md"):
        return "config"
    if path.startswith("skills/"):
        return "skill"
    if path.startswith("tools/"):
        return "tool"
    return "config"


def can_auto_apply(security_level):
    """Whether a patch at this security level can be auto-applied."""
    return security_level == 1


def is_prohibited(security_level):
    """Whether a patch at this security level is prohibited."""
    return security_level >= 3
