"""GitHub skill helper.

Provides small helpers for creating GitHub issues and pull requests using either
GitHub REST API credentials available to the agent or the gh CLI if present.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

API_VERSION = "2022-11-28"
DEFAULT_REPO = "lliwi/autobot"
DEFAULT_REPO_URL = "https://github.com/lliwi/autobot"


def _agent_get_credential(_agent: Any, name: str) -> Optional[str]:
    if _agent is None:
        return None
    try:
        if hasattr(_agent, "get_credential"):
            cred = _agent.get_credential(name)
            if isinstance(cred, dict):
                return cred.get("value") or cred.get("password") or cred.get("token")
            return cred
    except Exception:
        pass
    if isinstance(_agent, dict):
        for bucket in ("credentials", "creds", "secrets", "credential_values"):
            val = _agent.get(bucket)
            if isinstance(val, dict) and name in val:
                item = val[name]
                if isinstance(item, dict):
                    return item.get("value") or item.get("password") or item.get("token")
                return item
    return None


def _github_token(_agent: Any) -> Optional[str]:
    return (
        _agent_get_credential(_agent, "github_token")
        or _agent_get_credential(_agent, "github")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
    )


def _normalize_repo(repo: Optional[str]) -> str:
    if not repo:
        return DEFAULT_REPO
    repo = repo.strip()
    if repo.startswith("https://github.com/"):
        repo = repo.removeprefix("https://github.com/").strip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    return repo or DEFAULT_REPO


def _split_repo(repo: Optional[str]) -> Tuple[str, str]:
    repo = _normalize_repo(repo)
    if not repo or "/" not in repo:
        raise ValueError("repo must be in owner/repo format")
    owner, name = repo.strip().split("/", 1)
    if not owner or not name or "/" in name:
        raise ValueError("repo must be in owner/repo format")
    _SLUG_RE = re.compile(r'^[a-zA-Z0-9._-]+$')
    if not _SLUG_RE.match(owner) or not _SLUG_RE.match(name):
        raise ValueError("owner/repo must only contain alphanumeric, '.', '_', '-'")
    return owner, name


def _api_request(token: str, method: str, path: str, payload: Optional[dict] = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com" + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "autobot-github-skill",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {e.code}: {detail}") from e


def create_issue(_agent: Any, repo: Optional[str], title: str, body: str, labels: Optional[List[str]] = None) -> dict:
    repo = _normalize_repo(repo)
    owner, name = _split_repo(repo)
    labels = labels or []

    if shutil.which("gh"):
        cmd = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
        for label in labels:
            cmd.extend(["--label", label])
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "gh CLI timed out after 60s"}
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return {"ok": True, "method": "gh", "repo": repo, "url": proc.stdout.strip()}

    token = _github_token(_agent)
    if not token:
        raise RuntimeError("Missing GitHub credential: configure github_token or github, or install/authenticate gh CLI")
    payload = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    result = _api_request(token, "POST", f"/repos/{owner}/{name}/issues", payload)
    return {
        "ok": True,
        "method": "api",
        "repo": repo,
        "number": result.get("number"),
        "url": result.get("html_url"),
        "title": result.get("title"),
    }


def create_pr(_agent: Any, repo: Optional[str], title: str, head: str, base: str, body: str = "", draft: bool = False) -> dict:
    repo = _normalize_repo(repo)
    owner, name = _split_repo(repo)

    if shutil.which("gh"):
        cmd = ["gh", "pr", "create", "--repo", repo, "--title", title, "--head", head, "--base", base, "--body", body]
        if draft:
            cmd.append("--draft")
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "gh CLI timed out after 60s"}
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return {"ok": True, "method": "gh", "repo": repo, "url": proc.stdout.strip()}

    token = _github_token(_agent)
    if not token:
        raise RuntimeError("Missing GitHub credential: configure github_token or github, or install/authenticate gh CLI")
    payload = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
    result = _api_request(token, "POST", f"/repos/{owner}/{name}/pulls", payload)
    return {
        "ok": True,
        "method": "api",
        "repo": repo,
        "number": result.get("number"),
        "url": result.get("html_url"),
        "title": result.get("title"),
    }


def handler(_agent: Any = None, action: str = "help", **kwargs) -> dict:
    """Dispatch helper for issue/pr creation.

    action values:
    - create_issue: title, body, repo=DEFAULT_REPO, labels=[]
    - create_pr: title, head, base, repo=DEFAULT_REPO, body='', draft=False
    - status/help
    """
    if action == "create_issue":
        return create_issue(
            _agent,
            repo=kwargs.get("repo") or DEFAULT_REPO,
            title=kwargs["title"],
            body=kwargs.get("body", ""),
            labels=kwargs.get("labels") or [],
        )
    if action == "create_pr":
        return create_pr(
            _agent,
            repo=kwargs.get("repo") or DEFAULT_REPO,
            title=kwargs["title"],
            head=kwargs["head"],
            base=kwargs["base"],
            body=kwargs.get("body", ""),
            draft=bool(kwargs.get("draft", False)),
        )
    return {
        "ok": True,
        "default_repo": DEFAULT_REPO,
        "default_repo_url": DEFAULT_REPO_URL,
        "gh_available": bool(shutil.which("gh")),
        "has_token_hint": bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")),
        "actions": ["create_issue", "create_pr"],
        "credential_names": ["github_token", "github"],
    }
