"""Unit tests for app.services.security_policy.

Pure-function module — no DB or Flask app needed.
"""

import pytest

from app.services.security_policy import (
    can_auto_apply,
    classify_target,
    classify_target_type,
    is_prohibited,
)


@pytest.mark.parametrize(
    "path,is_new,expected",
    [
        # Level 1 — always auto
        ("MEMORY.md", False, 1),
        ("skills/foo/manifest.json", False, 1),
        ("skills/foo/SKILL.md", False, 1),
        ("tools/http/manifest.json", False, 1),
        # Level 1 only when creating new — skill/tool Python
        ("skills/foo/skill.py", True, 1),
        ("tools/http/tool.py", True, 1),
        # Level 2 — modifying an existing skill/tool .py
        ("skills/foo/skill.py", False, 2),
        ("tools/http/tool.py", False, 2),
        # Level 2 — catalog files
        ("AGENTS.md", False, 2),
        ("TOOLS.md", False, 2),
        # Sub-agent workspace
        ("agents/sub/SOUL.md", False, 2),
        # Default bucket for anything else inside workspace
        ("notes/random.txt", False, 2),
    ],
)
def test_classify_target(path, is_new, expected):
    assert classify_target(path, is_new_file=is_new) == expected


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "..",
        "SOUL.md",  # identity is protected in MVP
    ],
)
def test_prohibited_paths_are_level_3(path):
    assert classify_target(path) == 3


def test_leading_slash_is_stripped_before_classification():
    # classify_target normalizes leading "/" before matching, so absolute-looking
    # paths collapse to relative and fall through to the default bucket. The
    # real defense against filesystem escape is the workspace manager pinning
    # writes to the workspace root, not this classifier.
    assert classify_target("/etc/passwd") == 2


@pytest.mark.parametrize(
    "path,expected",
    [
        ("MEMORY.md", "memory"),
        ("AGENTS.md", "agents"),
        ("TOOLS.md", "config"),
        ("SOUL.md", "config"),
        ("skills/foo/skill.py", "skill"),
        ("tools/http/tool.py", "tool"),
        ("whatever.txt", "config"),
    ],
)
def test_classify_target_type(path, expected):
    assert classify_target_type(path) == expected


def test_can_auto_apply_matrix():
    assert can_auto_apply(1) is True
    assert can_auto_apply(2) is False
    assert can_auto_apply(3) is False


def test_is_prohibited_matrix():
    assert is_prohibited(1) is False
    assert is_prohibited(2) is False
    assert is_prohibited(3) is True
    # Anything above 3 is also prohibited (defensive).
    assert is_prohibited(4) is True
