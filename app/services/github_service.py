"""Thin GitHub REST helpers for opening Issues and PRs from incident reports.

Self-contained (stdlib ``urllib`` only) and decoupled from the local git tree:
a PR is built entirely through the API (create a branch ref from the base, commit
a single-file change via the contents API, then open the PR). This means the web
process can open a PR without a checkout.

Configuration (env):
  - ``GH_TOKEN`` / ``GITHUB_TOKEN`` — a token with ``repo`` scope.
  - ``AUTOBOT_GITHUB_REPO`` — ``https://github.com/owner/repo``.
  - ``AUTOBOT_GITHUB_BASE_BRANCH`` — base branch for PRs (default ``main``).
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

_API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(_token() and os.environ.get("AUTOBOT_GITHUB_REPO", "").strip())


def _token() -> str:
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _owner_repo() -> str:
    repo_url = os.environ.get("AUTOBOT_GITHUB_REPO", "").strip()
    if not repo_url:
        raise GitHubError("AUTOBOT_GITHUB_REPO no está configurado.")
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", repo_url)
    if not m:
        raise GitHubError(f"No se puede parsear owner/repo desde: {repo_url}")
    return m.group(1)


def _base_branch() -> str:
    return os.environ.get("AUTOBOT_GITHUB_BASE_BRANCH", "main").strip() or "main"


def _request(method: str, path: str, payload: dict | None = None):
    token = _token()
    if not token:
        raise GitHubError("No hay GH_TOKEN/GITHUB_TOKEN configurado.")
    url = path if path.startswith("http") else f"{_API}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "autobot/incident-autopilot",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GitHubError(f"GitHub API {exc.code} {method} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"GitHub unreachable: {exc}") from exc


# --------------------------------------------------------------------------- #
# Issues
# --------------------------------------------------------------------------- #

def create_issue(title: str, body: str, labels: list[str] | None = None) -> str:
    """Open an issue; return its html_url."""
    owner_repo = _owner_repo()
    payload = {"title": title[:250], "body": body or ""}
    if labels:
        payload["labels"] = labels
    data = _request("POST", f"/repos/{owner_repo}/issues", payload)
    return data.get("html_url", "")


# --------------------------------------------------------------------------- #
# Pull requests (single-file change, fully via API)
# --------------------------------------------------------------------------- #

def create_pr_with_file_change(
    *, target_path: str, new_content: str, title: str, body: str,
    branch_prefix: str = "autobot/incident",
) -> str:
    """Create a branch off base, commit ``target_path`` = ``new_content``, open a PR.

    Returns the PR html_url. Raises GitHubError on failure. If a PR already
    exists for the generated branch, its URL is returned instead.
    """
    owner_repo = _owner_repo()
    base = _base_branch()

    # 1. Resolve the base branch head SHA.
    ref = _request("GET", f"/repos/{owner_repo}/git/ref/heads/{base}")
    base_sha = ref.get("object", {}).get("sha")
    if not base_sha:
        raise GitHubError(f"No se pudo resolver el SHA de la rama base '{base}'.")

    # 2. Create a new branch ref (unique-ish to avoid collisions).
    branch = f"{branch_prefix}-{int(time.time())}"
    try:
        _request(
            "POST", f"/repos/{owner_repo}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
    except GitHubError as exc:
        if "422" not in str(exc):  # 422 = ref exists; anything else is fatal
            raise

    # 3. Read current file SHA on the branch (required to update an existing file).
    file_sha = None
    try:
        existing = _request(
            "GET", f"/repos/{owner_repo}/contents/{target_path}?ref={branch}"
        )
        file_sha = existing.get("sha")
    except GitHubError:
        file_sha = None  # new file

    import base64
    put_payload = {
        "message": title[:200],
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if file_sha:
        put_payload["sha"] = file_sha
    _request("PUT", f"/repos/{owner_repo}/contents/{target_path}", put_payload)

    # 4. Open the PR.
    try:
        pr = _request(
            "POST", f"/repos/{owner_repo}/pulls",
            {"title": title[:250], "body": body or "", "head": branch, "base": base},
        )
        return pr.get("html_url", "")
    except GitHubError as exc:
        if "422" in str(exc):
            existing = _request(
                "GET", f"/repos/{owner_repo}/pulls?head={owner_repo.split('/')[0]}:{branch}&state=open"
            )
            if existing:
                return existing[0].get("html_url", "")
        raise
