"""Manifest loading and validation for workspace tools and skills."""

import json
import re
from pathlib import Path


def load_manifest(manifest_path):
    """Read and parse a manifest.json file. Returns dict or raises ValueError."""
    path = Path(manifest_path)
    if not path.exists():
        raise ValueError(f"Manifest not found: {manifest_path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in {manifest_path}: {e}")


_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_name(name):
    """Validate a tool/skill name: no path separators, no special chars."""
    if not name or not isinstance(name, str):
        return ["'name' is required and must be a non-empty string"]
    if "/" in name or "\\" in name or ".." in name:
        return [f"'name' contains invalid characters: {name}"]
    return []


def validate_tool_manifest(manifest):
    """Validate a tool manifest dict. Returns list of error strings (empty = valid)."""
    errors = []
    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"]

    errors.extend(_validate_name(manifest.get("name")))

    if not manifest.get("description") or not isinstance(manifest.get("description"), str):
        errors.append("'description' is required and must be a string")

    params = manifest.get("parameters")
    if params is not None:
        if not isinstance(params, dict):
            errors.append("'parameters' must be an object")
        elif params.get("type") != "object":
            errors.append("'parameters.type' must be 'object'")

    return errors


def validate_skill_manifest(manifest):
    """Validate a skill manifest dict. Returns list of error strings (empty = valid)."""
    errors = []
    if not isinstance(manifest, dict):
        return ["Manifest must be a JSON object"]

    errors.extend(_validate_name(manifest.get("name")))

    if not manifest.get("description") or not isinstance(manifest.get("description"), str):
        errors.append("'description' is required and must be a string")

    return errors
