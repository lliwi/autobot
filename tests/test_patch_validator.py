"""Unit tests for app.services.patch_validator.validate_patch."""

import os
import tempfile
from pathlib import Path

import pytest

from app.services.patch_validator import validate_patch


def _check_named(result, name):
    for c in result["checks"]:
        if c["name"] == name:
            return c
    return None


# -- JSON ---------------------------------------------------------------

def test_valid_json_passes():
    result = validate_patch("skills/foo/manifest.json", '{"name": "foo", "description": "x"}')
    assert result["ok"] is True
    assert _check_named(result, "json_parse")["ok"] is True


def test_invalid_json_fails():
    result = validate_patch("skills/foo/manifest.json", "{not valid json")
    assert result["ok"] is False
    assert _check_named(result, "json_parse")["ok"] is False
    assert "invalid JSON" in result["error"]


def test_manifest_missing_fields_fails():
    result = validate_patch("skills/foo/manifest.json", '{"description": "x"}')
    assert result["ok"] is False
    shape = _check_named(result, "manifest_shape")
    assert shape is not None and shape["ok"] is False
    assert "name" in shape["detail"]


def test_tool_manifest_bad_parameters_type_fails():
    content = '{"name": "foo", "description": "x", "parameters": "not-an-object"}'
    result = validate_patch("tools/foo/manifest.json", content)
    assert result["ok"] is False
    assert _check_named(result, "manifest_shape")["ok"] is False


def test_manifest_shape_only_runs_when_json_parses():
    # Broken JSON should short-circuit the shape check.
    result = validate_patch("skills/foo/manifest.json", "{")
    assert _check_named(result, "manifest_shape") is None


# -- Python syntax -----------------------------------------------------

def test_python_syntax_ok():
    result = validate_patch("skills/foo/skill.py", "def run():\n    return 1\n")
    assert _check_named(result, "python_syntax")["ok"] is True


def test_python_syntax_error_fails():
    result = validate_patch("skills/foo/skill.py", "def run(:\n")
    assert result["ok"] is False
    assert _check_named(result, "python_syntax")["ok"] is False
    assert "SyntaxError" in result["error"]


# -- Tool handler ------------------------------------------------------

def test_tool_requires_handler_function():
    result = validate_patch("tools/http/tool.py", "def not_handler():\n    pass\n")
    assert result["ok"] is False
    assert _check_named(result, "tool_handler_present")["ok"] is False


def test_tool_with_handler_passes_handler_check():
    code = "def handler(arg):\n    return arg\n"
    result = validate_patch("tools/http/tool.py", code)
    assert _check_named(result, "tool_handler_present")["ok"] is True


def test_skill_does_not_require_handler():
    # skill.py is not a tool handler — handler check should not appear.
    result = validate_patch("skills/foo/skill.py", "def anything():\n    return 1\n")
    assert _check_named(result, "tool_handler_present") is None


# -- Smoke import -----------------------------------------------------

def test_smoke_import_passes_for_clean_module():
    with tempfile.TemporaryDirectory() as ws:
        result = validate_patch(
            "tools/ok/tool.py",
            "def handler(x):\n    return x\n",
            workspace_root=Path(ws),
        )
        assert result["ok"] is True
        smoke = _check_named(result, "smoke_import")
        assert smoke is not None and smoke["ok"] is True


def test_smoke_import_fails_for_broken_top_level():
    with tempfile.TemporaryDirectory() as ws:
        result = validate_patch(
            "tools/broken/tool.py",
            "def handler(x):\n    return x\n\nraise RuntimeError('boom')\n",
            workspace_root=Path(ws),
        )
        assert result["ok"] is False
        assert _check_named(result, "smoke_import")["ok"] is False
        assert "import failed" in result["error"].lower() or "boom" in result["error"]


def test_smoke_import_skipped_without_workspace_root():
    result = validate_patch("tools/ok/tool.py", "def handler(x):\n    return x\n")
    assert _check_named(result, "smoke_import") is None


# -- Non-code files ---------------------------------------------------

def test_markdown_has_no_blocking_checks():
    result = validate_patch("MEMORY.md", "# anything\n")
    assert result["ok"] is True
    assert _check_named(result, "no_static_checks_for_type") is not None
