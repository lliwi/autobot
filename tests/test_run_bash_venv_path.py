import os
from pathlib import Path
from types import SimpleNamespace

from app.runtime.tool_registry.bash_tools import _run_bash


def _make_fake_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    venv_bin = workspace / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    python = venv_bin / "python"
    python.write_text("#!/usr/bin/env sh\necho venv-python\n", encoding="utf-8")
    python.chmod(0o755)
    return workspace, venv_bin


def test_run_bash_command_resolves_workspace_venv_python_first(tmp_path):
    workspace, venv_bin = _make_fake_workspace(tmp_path)
    agent = SimpleNamespace(workspace_path=str(workspace))

    result = _run_bash(
        _agent=agent,
        command="printf '%s\n' \"$(command -v python)\"; python",
    )

    assert result["ok"] is True
    assert result["venv_active"] is True
    lines = result["stdout"].splitlines()
    assert lines[0] == str(venv_bin / "python")
    assert lines[1] == "venv-python"


def test_run_bash_command_exports_virtual_env(tmp_path):
    workspace, _venv_bin = _make_fake_workspace(tmp_path)
    agent = SimpleNamespace(workspace_path=str(workspace))

    result = _run_bash(_agent=agent, command="printf '%s' \"$VIRTUAL_ENV\"")

    assert result["ok"] is True
    assert result["stdout"] == str(workspace / ".venv")


def test_run_bash_command_uses_non_login_shell(monkeypatch, tmp_path):
    workspace, venv_bin = _make_fake_workspace(tmp_path)
    agent = SimpleNamespace(workspace_path=str(workspace))
    captured = {}

    class Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, cwd, env, capture_output, text, timeout):
        captured["argv"] = argv
        captured["env"] = env
        return Proc()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = _run_bash(_agent=agent, command="echo ok")

    assert result["ok"] is True
    assert captured["argv"] == ["bash", "-c", "echo ok"]
    assert captured["env"]["PATH"].split(os.pathsep)[0] == str(venv_bin)


def test_run_bash_script_receives_workspace_venv_path(tmp_path):
    workspace, venv_bin = _make_fake_workspace(tmp_path)
    agent = SimpleNamespace(workspace_path=str(workspace))

    result = _run_bash(
        _agent=agent,
        script="printf '%s\n' \"$(command -v python)\"\npython\n",
    )

    assert result["ok"] is True
    lines = result["stdout"].splitlines()
    assert lines[0] == str(venv_bin / "python")
    assert lines[1] == "venv-python"
