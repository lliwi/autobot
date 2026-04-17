"""Per-workspace Python virtual environments.

Each agent gets an isolated ``<workspace>/.venv`` so it can install packages
without contaminating the shared Flask environment. Tools and skills are run
as subprocesses against this venv.

Responsibilities:

  * ``ensure_venv(agent)`` — create the venv and pre-install base packages on
    first use. Idempotent and safe to call on every run.
  * ``venv_python(agent)`` — absolute path to the venv's ``python`` interpreter.
    Returns ``None`` if the venv is missing and couldn't be created.
  * ``pip_install(agent, spec)`` — run ``pip install <spec>`` inside the venv.
    Returns ``(ok, installed_version, stderr_tail)``.
  * ``pip_uninstall(agent, name)`` — remove a package.
  * ``list_installed(agent)`` — parse ``pip list --format=json``.

All subprocess calls go through this module so timeouts, environment scrubbing,
and error capture live in one place.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import venv
from pathlib import Path

from flask import current_app

logger = logging.getLogger(__name__)

_VENV_DIRNAME = ".venv"


def _workspace_path(agent) -> Path:
    return Path(agent.workspace_path).resolve()


def venv_dir(agent) -> Path:
    return _workspace_path(agent) / _VENV_DIRNAME


def venv_python(agent) -> Path | None:
    py = venv_dir(agent) / "bin" / "python"
    return py if py.exists() else None


def _base_packages() -> list[str]:
    raw = current_app.config.get("VENV_BASE_PACKAGES", "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def _pip_timeout() -> int:
    return int(current_app.config.get("PIP_INSTALL_TIMEOUT_SECONDS", 180))


def _clean_env() -> dict:
    """Environment for pip/tool subprocesses.

    Strips Flask/DB/Codex creds so workspace code can't accidentally read them.
    Preserves ``PATH``, ``HOME``, locale, and proxy settings that pip needs.
    """
    keep = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
            "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy",
            "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    env.setdefault("PIP_NO_INPUT", "1")
    return env


def ensure_venv(agent) -> Path:
    """Create the workspace venv if missing and seed it with base packages.

    Returns the path to the venv's python interpreter. Safe to call on every
    run — it's a no-op once the venv exists.
    """
    vdir = venv_dir(agent)
    py = vdir / "bin" / "python"
    if py.exists():
        return py

    logger.info("Creating venv for agent %s at %s", agent.slug, vdir)
    vdir.parent.mkdir(parents=True, exist_ok=True)
    builder = venv.EnvBuilder(
        system_site_packages=False,
        clear=False,
        symlinks=True,
        with_pip=True,
        upgrade_deps=False,
    )
    builder.create(str(vdir))

    if not py.exists():
        raise RuntimeError(f"venv created but python missing at {py}")

    # Seed with base packages (best-effort — a slow/offline machine shouldn't
    # prevent the agent from starting; just log and continue).
    for spec in _base_packages():
        try:
            _run_pip(py, ["install", spec])
        except Exception as e:
            logger.warning("Base package %s failed to install: %s", spec, e)

    return py


def _run_pip(py: Path, args: list[str]) -> subprocess.CompletedProcess:
    cmd = [str(py), "-m", "pip", "--disable-pip-version-check", "--no-input", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=_pip_timeout(),
        env=_clean_env(),
        check=False,
    )


def pip_install(agent, spec: str) -> tuple[bool, str | None, str | None]:
    """Install ``spec`` into the agent's venv.

    Returns ``(ok, installed_version, stderr_tail)``. ``installed_version`` is
    populated via a follow-up ``pip show`` when the install succeeds.
    """
    py = ensure_venv(agent)
    try:
        proc = _run_pip(py, ["install", spec])
    except subprocess.TimeoutExpired as e:
        return False, None, f"timeout after {e.timeout}s"

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        return False, None, tail

    # Resolve the installed version. We can't parse the spec reliably (extras,
    # version specifiers, environment markers), so use pip show on the base
    # package name.
    base_name = _spec_base_name(spec)
    version = None
    if base_name:
        show = _run_pip(py, ["show", base_name])
        if show.returncode == 0:
            for line in show.stdout.splitlines():
                if line.lower().startswith("version:"):
                    version = line.split(":", 1)[1].strip()
                    break
    return True, version, None


def pip_uninstall(agent, name: str) -> tuple[bool, str | None]:
    py = venv_python(agent)
    if py is None:
        return True, None  # no venv => nothing to uninstall
    try:
        proc = _run_pip(py, ["uninstall", "-y", name])
    except subprocess.TimeoutExpired as e:
        return False, f"timeout after {e.timeout}s"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "")[-2000:]
    return True, None


def list_installed(agent) -> list[dict]:
    """Return ``[{'name': ..., 'version': ...}, ...]`` for the venv, or ``[]``."""
    py = venv_python(agent)
    if py is None:
        return []
    try:
        proc = _run_pip(py, ["list", "--format=json"])
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    return [{"name": d.get("name"), "version": d.get("version")} for d in data if d.get("name")]


# -- helpers ----------------------------------------------------------------


def _spec_base_name(spec: str) -> str | None:
    """Extract the PyPI distribution name from a spec like 'foo[bar]>=1.0'.

    Returns ``None`` if it can't confidently parse one. Not a full PEP 508
    parser — just enough to call ``pip show``.
    """
    s = spec.strip()
    for sep in ("==", ">=", "<=", "!=", "~=", ">", "<", ";", "[", " "):
        idx = s.find(sep)
        if idx > 0:
            s = s[:idx]
            break
    s = s.strip()
    return s or None


def default_tool_runner() -> str:
    """Absolute path to the subprocess tool runner shipped with the app."""
    return str(Path(sys.modules["app"].__file__).resolve().parent / "runtime" / "tool_subprocess_runner.py")
