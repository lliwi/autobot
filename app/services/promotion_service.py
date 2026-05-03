"""Promotion service — elevate a battle-tested tool/skill to the _template workspace.

Two levels:
  1. generate_promotion_bundle() — always available, produces a tar.gz with the
     item files, a unified diff, PROMOTION.md, and requirements.txt.
  2. create_promotion_pr() — requires GH_TOKEN; creates a git branch, commit and
     GitHub PR automatically via subprocess + gh CLI.

Before generating the bundle or PR the code is scanned for hardcoded secrets,
credentials, and public IP addresses. HIGH findings block the promotion;
MEDIUM/LOW are surfaced as warnings in PROMOTION.md and the PR body.

broadcast_to_all_agents() copies the item to every existing agent after promotion.
"""
import ast
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.extensions import db
from app.models.agent import Agent
from app.models.patch_proposal import PatchProposal
from app.models.skill import Skill
from app.models.tool import Tool
from app.services.patch_validator import validate_patch
from app.services.promotion_secrets_scanner import findings_to_markdown, scan_directory
from app.workspace.manager import get_template_path, get_workspace_path

logger = logging.getLogger(__name__)

_ITEM_DIRS = {"tool": "tools", "skill": "skills"}

_PROMOTIONS_DIR = Path(tempfile.gettempdir()) / "autobot_promotions"


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

def is_promoted_to_template(item_type: str, slug: str) -> bool:
    """Return True if slug already exists in the _template directory."""
    if item_type not in _ITEM_DIRS:
        return False
    return (get_template_path() / _ITEM_DIRS[item_type] / slug).is_dir()


def get_promotion_status(item_type: str, slug: str) -> dict:
    """Return {"in_template": bool, "branch_exists": bool}."""
    in_template = is_promoted_to_template(item_type, slug)
    branch_name = _branch_name(item_type, slug)
    branch_exists = _git_branch_exists(branch_name)
    return {"in_template": in_template, "branch_exists": branch_exists, "branch": branch_name}


# ---------------------------------------------------------------------------
# Level 1 — bundle generation (no external deps)
# ---------------------------------------------------------------------------

def generate_promotion_bundle(agent_id: int | None, item_type: str, slug: str) -> dict:
    """Build a downloadable tar.gz with item files, requirements.txt, diff and PROMOTION.md.

    Returns {"ok": bool, "bundle_path": str, "diff": str, "pr_title": str, "pr_body": str,
             "requirements": [...], "scan": {...}, "error": str|None}.
    """
    check = _resolve_and_validate(agent_id, item_type, slug)
    if not check["ok"]:
        return check

    agent = check["agent"]
    item = check["item"]
    source_dir = check["source_dir"]
    scan = check["scan"]

    requirements = _collect_requirements(agent, source_dir)
    diff = _compute_diff(item_type, slug, source_dir)
    pr_title, pr_body = _pr_texts(agent, item_type, item, diff, requirements, scan)

    # Write requirements.txt to source_dir so it's included in the template copy
    # and picked up by sync_tools/skills_to_db on new agents.
    _write_requirements_file(source_dir, requirements)

    _PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bundle_name = f"{slug}-{ts}.tar.gz"
    bundle_path = _PROMOTIONS_DIR / bundle_name

    with tarfile.open(bundle_path, "w:gz") as tar:
        # Item files under workspaces/_template/<type>/<slug>/
        arcbase = f"workspaces/_template/{_ITEM_DIRS[item_type]}/{slug}"
        for fpath in sorted(source_dir.rglob("*")):
            if fpath.is_file():
                arcname = f"{arcbase}/{fpath.relative_to(source_dir)}"
                tar.add(fpath, arcname=arcname)

        # PROMOTION.md
        _add_bytes_to_tar(tar, pr_body.encode("utf-8"), "PROMOTION.md")

        # promote.patch (unified diff)
        if diff:
            _add_bytes_to_tar(tar, diff.encode("utf-8"), "promote.patch")

    logger.info("Promotion bundle created: %s", bundle_path)
    return {
        "ok": True,
        "bundle_path": str(bundle_path),
        "bundle_name": bundle_name,
        "diff": diff,
        "pr_title": pr_title,
        "pr_body": pr_body,
        "requirements": requirements,
        "scan": scan,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Level 2 — automatic GitHub PR (requires GH_TOKEN)
# ---------------------------------------------------------------------------

def create_promotion_pr(agent_id: int | None, item_type: str, slug: str) -> dict:
    """Create a git branch, commit and GitHub PR for the promotion.

    Returns {"ok": bool, "pr_url": str|None, "branch": str|None, "error": str|None}.
    """
    gh_token = _resolve_gh_token()
    if not gh_token:
        return {"ok": False, "pr_url": None, "branch": None,
                "error": "GH_TOKEN no disponible — configúralo en Variables de Entorno o como credencial 'gh_token' en el almacén de credenciales"}

    check = _resolve_and_validate(agent_id, item_type, slug)
    if not check["ok"]:
        return {**check, "pr_url": None, "branch": None}

    agent = check["agent"]
    item = check["item"]
    source_dir = check["source_dir"]
    scan = check["scan"]

    requirements = _collect_requirements(agent, source_dir)
    _write_requirements_file(source_dir, requirements)

    repo_root = _repo_root()
    if repo_root is None:
        return {"ok": False, "pr_url": None, "branch": None,
                "error": "Could not locate git repository root"}

    template_dir = get_template_path() / _ITEM_DIRS[item_type] / slug
    branch = _branch_name(item_type, slug)

    # Disambiguate if branch already exists
    if _git_branch_exists(branch):
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        branch = f"{branch}-{ts}"

    diff = _compute_diff(item_type, slug, source_dir)
    pr_title, pr_body = _pr_texts(agent, item_type, item, diff, requirements, scan)
    commit_msg = f"{pr_title}\n\n{pr_body}"

    try:
        # Capture original branch to return to
        original = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_root)

        _git("checkout", "-b", branch, cwd=repo_root)

        # Copy item files into template
        if template_dir.exists():
            shutil.rmtree(template_dir)
        shutil.copytree(source_dir, template_dir)

        rel_template = template_dir.relative_to(repo_root)
        _git("add", "-f", str(rel_template), cwd=repo_root)
        git_email = os.environ.get("GIT_AUTHOR_EMAIL", "autobot@autobot.local")
        git_name = os.environ.get("GIT_AUTHOR_NAME", "Autobot")
        _git(
            "-c", f"user.email={git_email}",
            "-c", f"user.name={git_name}",
            "commit", "-m", commit_msg,
            cwd=repo_root,
        )
        # Embed token in the push URL so git doesn't prompt for credentials.
        repo_url = os.environ.get("AUTOBOT_GITHUB_REPO", "").strip()
        if not repo_url:
            raise RuntimeError(
                "AUTOBOT_GITHUB_REPO no está configurado. "
                "Añade AUTOBOT_GITHUB_REPO=https://github.com/owner/repo en .env"
            )
        push_url = _inject_token(repo_url, gh_token)
        _git("push", push_url, branch, cwd=repo_root)

        pr_url = _gh_pr_create(branch, pr_title, pr_body, cwd=repo_root, gh_token=gh_token)

        _git("checkout", original, cwd=repo_root)

        # workspaces/ is gitignored so checkout doesn't remove the template copy.
        # Delete it so is_promoted_to_template() only returns True once the PR
        # is merged and the files land in the codebase.
        if template_dir.exists():
            shutil.rmtree(template_dir)

    except Exception as exc:
        # Try to return to original branch if possible
        try:
            _git("checkout", "-", cwd=repo_root)
        except Exception:
            pass
        # Also clean up local template copy on failure
        if template_dir.exists():
            shutil.rmtree(template_dir)
        err = getattr(exc, "stderr", None) or str(exc)
        return {"ok": False, "pr_url": None, "branch": branch,
                "error": f"Git/gh error: {err}"}

    logger.info("Promotion PR created: %s (branch: %s)", pr_url, branch)
    return {"ok": True, "pr_url": pr_url, "branch": branch, "error": None}


# ---------------------------------------------------------------------------
# Level 3 — broadcast to all existing agents
# ---------------------------------------------------------------------------

def broadcast_to_all_agents(agent_id: int | None, item_type: str, slug: str) -> dict:
    """Assign the item to all existing agents that don't already have it."""
    item = _get_item(item_type, agent_id, slug)
    if item is None:
        return {"ok": False, "error": f"{item_type} '{slug}' not found", "broadcast_copied": 0, "broadcast_errors": []}

    exclude_id = agent_id or 0
    all_agents = Agent.query.filter(Agent.id != exclude_id).all()
    copied = 0
    errors = []

    for target_agent in all_agents:
        try:
            if item_type == "skill":
                from app.services.skill_service import assign_skill_to_agent
                assign_skill_to_agent(item.id, target_agent.id)
            else:
                from app.services.tool_service import copy_tool
                copy_tool(item.id, target_agent.id)
            copied += 1
        except ValueError as e:
            errors.append(f"{target_agent.slug}: {e}")

    return {"ok": True, "error": None, "broadcast_copied": copied, "broadcast_errors": errors}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_and_validate(agent_id: int | None, item_type: str, slug: str) -> dict:
    """Fetch item, validate files and scan for secrets.

    For skills (global), agent_id is ignored — the source is always _global/skills/.
    For tools, agent_id is required to locate the agent's workspace.

    Returns enriched dict. Fails ("ok": False) if HIGH secrets are found.
    """
    if item_type not in _ITEM_DIRS:
        return {"ok": False, "error": f"Unknown item_type '{item_type}'"}

    item = _get_item(item_type, agent_id, slug)
    if item is None:
        return {"ok": False, "error": f"{item_type.capitalize()} '{slug}' not found"}

    if item_type == "skill":
        from app.workspace.manager import get_global_skills_path
        source_dir = get_global_skills_path() / slug
        workspace_root = source_dir.parent.parent  # _global/
        agent = None
    else:
        agent = db.session.get(Agent, agent_id)
        if agent is None:
            return {"ok": False, "error": "Agent not found"}
        if not item.enabled:
            return {"ok": False, "error": f"Tool '{slug}' is disabled — enable it before promoting"}
        workspace_root = get_workspace_path(agent)
        source_dir = workspace_root / _ITEM_DIRS[item_type] / slug

    if not source_dir.exists():
        return {"ok": False, "error": f"Source directory not found: {source_dir}"}

    validation_error = _validate_directory(source_dir, workspace_root)
    if validation_error:
        return {"ok": False, "error": f"Validation failed: {validation_error}"}

    scan = scan_directory(source_dir)
    if not scan["ok"]:
        high = [f for f in scan["findings"] if f["severity"] == "high"]
        details = "; ".join(f"{f['file']}:{f['line']} — {f['pattern_name']}" for f in high[:5])
        return {
            "ok": False,
            "error": f"Se encontró información sensible hardcodeada — corrige antes de promover. {details}",
            "scan": scan,
        }

    return {"ok": True, "agent": agent, "item": item, "source_dir": source_dir,
            "scan": scan, "error": None}


def _get_item(item_type: str, agent_id: int | None, slug: str):
    if item_type == "tool":
        return Tool.query.filter_by(agent_id=agent_id, slug=slug).first()
    # Skills are global — agent_id is irrelevant
    return Skill.query.filter_by(slug=slug).first()


def _validate_directory(source_dir: Path, workspace_root: Path) -> str | None:
    for fpath in sorted(source_dir.rglob("*")):
        if not fpath.is_file() or fpath.suffix.lower() not in (".py", ".json"):
            continue
        rel_path = str(fpath.relative_to(workspace_root))
        result = validate_patch(rel_path, fpath.read_text(encoding="utf-8"), workspace_root=workspace_root)
        if not result["ok"]:
            return f"{fpath.name}: {result['error']}"
    return None


def _compute_diff(item_type: str, slug: str, source_dir: Path) -> str:
    """Generate a unified diff showing what would change in _template/."""
    template_item_dir = get_template_path() / _ITEM_DIRS[item_type] / slug
    lines = []
    for src_file in sorted(source_dir.rglob("*")):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(source_dir)
        tgt_file = template_item_dir / rel
        src_text = src_file.read_text(encoding="utf-8", errors="replace")
        tgt_text = tgt_file.read_text(encoding="utf-8", errors="replace") if tgt_file.exists() else ""

        if src_text == tgt_text:
            continue

        import difflib
        tgt_label = f"workspaces/_template/{_ITEM_DIRS[item_type]}/{slug}/{rel}" if tgt_file.exists() else "/dev/null"
        src_label = f"workspaces/<agent>/{_ITEM_DIRS[item_type]}/{slug}/{rel}"
        diff = difflib.unified_diff(
            tgt_text.splitlines(keepends=True),
            src_text.splitlines(keepends=True),
            fromfile=tgt_label,
            tofile=src_label,
        )
        lines.extend(diff)

    return "".join(lines)


# Common import-name → package-name overrides for packages whose import
# name differs from the pip install name.
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "bs4": "beautifulsoup4",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "attr": "attrs",
    "jwt": "PyJWT",
    "Crypto": "pycryptodome",
    "serial": "pyserial",
    "magic": "python-magic",
    "usb": "pyusb",
    "gi": "PyGObject",
    "wx": "wxPython",
}


def _collect_requirements(agent, source_dir: Path) -> list[str]:
    """Return pip specs needed by the tool/skill.

    Priority:
      1. ``requirements`` list declared in manifest.json (explicit).
      2. Infer from Python import statements vs packages installed in the agent's
         workspace (PackageInstallation where status='installed').
         When agent is None (global skill), only the manifest is used.
    Merges both sources, deduplicates, returns sorted list of specs.
    """
    from app.models.package_installation import PackageInstallation

    # 1. Explicit from manifest
    manifest_file = source_dir / "manifest.json"
    declared: list[str] = []
    if manifest_file.exists():
        try:
            import json
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            declared = [str(r) for r in manifest.get("requirements", []) if r]
        except Exception:
            pass

    # 2. Installed packages for this agent (skip when agent is None for global skills)
    if agent is None:
        installed_by_name: dict[str, str] = {}
    else:
        installed = PackageInstallation.query.filter_by(
            agent_id=agent.id, status="installed"
        ).all()
        installed_by_name = {
            _norm(row.name): row.spec for row in installed
        }

    # 3. Extract imported module names from all .py files
    imported_modules: set[str] = set()
    for fpath in source_dir.rglob("*.py"):
        imported_modules.update(_extract_imports(fpath))

    # 4. Match imported modules to installed packages
    inferred: list[str] = []
    stdlib = _stdlib_names()
    for mod in imported_modules:
        if mod in stdlib:
            continue
        # Check override table first
        pkg_name = _IMPORT_TO_PACKAGE.get(mod, mod)
        norm = _norm(pkg_name)
        if norm in installed_by_name:
            inferred.append(installed_by_name[norm])

    # Merge: declared wins, inferred fills gaps
    declared_norms = {_norm(_spec_name(s)) for s in declared}
    for spec in inferred:
        if _norm(_spec_name(spec)) not in declared_norms:
            declared.append(spec)

    return sorted(set(declared))


def _write_requirements_file(source_dir: Path, requirements: list[str]) -> None:
    """Write (or update) requirements.txt in the tool/skill directory."""
    req_path = source_dir / "requirements.txt"
    if not requirements:
        return
    content = "\n".join(sorted(requirements)) + "\n"
    if req_path.exists() and req_path.read_text(encoding="utf-8") == content:
        return
    req_path.write_text(content, encoding="utf-8")


def _extract_imports(py_file: Path) -> set[str]:
    """Return top-level module names imported by a Python file (AST-safe)."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module.split(".")[0])
    return modules


def _stdlib_names() -> frozenset[str]:
    if hasattr(sys, "stdlib_module_names"):          # Python 3.10+
        return sys.stdlib_module_names               # type: ignore[attr-defined]
    # Fallback: common stdlib top-level names
    return frozenset({
        "abc", "ast", "asyncio", "base64", "builtins", "collections", "contextlib",
        "copy", "csv", "dataclasses", "datetime", "decimal", "difflib", "enum",
        "functools", "glob", "hashlib", "http", "importlib", "inspect", "io",
        "itertools", "json", "logging", "math", "multiprocessing", "operator",
        "os", "pathlib", "pickle", "platform", "queue", "random", "re", "shutil",
        "signal", "socket", "sqlite3", "ssl", "stat", "string", "struct",
        "subprocess", "sys", "tarfile", "tempfile", "threading", "time",
        "traceback", "types", "typing", "unittest", "urllib", "uuid", "warnings",
        "weakref", "xml", "zipfile", "zlib",
    })


def _norm(name: str) -> str:
    """Normalize a package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _spec_name(spec: str) -> str:
    """Extract the package name from a pip spec string (e.g. 'httpx>=0.27' → 'httpx')."""
    return re.split(r"[><=!~\[]", spec)[0].strip()


def _patch_count(agent_id: int, item_type: str, slug: str) -> tuple[int, datetime | None]:
    """Return (count, last_applied_at) of applied patches for this item."""
    prefix = f"{_ITEM_DIRS[item_type]}/{slug}/"
    patches = (
        PatchProposal.query
        .filter(
            PatchProposal.agent_id == agent_id,
            PatchProposal.target_path.like(f"{prefix}%"),
            PatchProposal.status == "applied",
        )
        .order_by(PatchProposal.applied_at.desc())
        .all()
    )
    last = patches[0].applied_at if patches else None
    return len(patches), last


def _branch_name(item_type: str, slug: str) -> str:
    return f"promote/{item_type}/{slug}"


def _pr_texts(agent, item_type: str, item, diff: str,
              requirements: list[str], scan: dict) -> tuple[str, str]:
    if agent is not None:
        count, last_applied = _patch_count(agent.id, item_type, item.slug)
        last_str = last_applied.strftime("%Y-%m-%d") if last_applied else "N/A"
        origin_line = f"**Origen:** agente `{agent.slug}` (`{agent.name}`)"
    else:
        count, last_str = 0, "N/A"
        origin_line = "**Origen:** catálogo global"
    has_diff = bool(diff.strip())

    pr_title = f"promote({item_type}): {item.slug} v{item.version}"

    # Requirements section
    if requirements:
        req_lines = "\n".join(f"- `{r}`" for r in requirements)
        req_section = f"### Dependencias Python\n{req_lines}\n"
    else:
        req_section = "### Dependencias Python\n_Ninguna detectada._\n"

    # Security section
    findings = scan.get("findings", [])
    if findings:
        sec_status = f"⚠️ {scan['summary']}"
        sec_table = findings_to_markdown(findings)
        sec_section = f"### Escaneo de seguridad\n{sec_status}\n\n{sec_table}"
    else:
        sec_section = "### Escaneo de seguridad\n✅ Sin problemas detectados.\n"

    pr_body = f"""\
## Promoción: {item_type}/{item.slug} v{item.version}

{origin_line}
**Patches aplicados:** {count} (último: {last_str})
**Validación de código:** OK

### Descripción
{item.description or "_Sin descripción._"}

{req_section}
{sec_section}
### Cambios en `_template/`
{"El diff está incluido en `promote.patch`." if has_diff else "Sin cambios respecto a la versión actual en `_template/`."}

### Cómo testear
1. Crear un agente nuevo — heredará la {item_type} `{item.slug}` desde `_template/`
2. Sincronizar el workspace del nuevo agente (`Sync from Workspace`)
3. Verificar que los paquetes de `requirements.txt` se registran en `pending_review`
4. Aprobar e instalar los paquetes desde **Dashboard → Packages**
5. Ejecutar la {item_type} con el caso base y verificar el resultado esperado
"""
    return pr_title, pr_body


def _resolve_gh_token() -> str | None:
    """Return the GitHub token from the credential store or environment.

    Lookup order:
      1. Global credential named 'gh_token' in the DB (encrypted at rest).
      2. AUTOBOT_CRED_GH_TOKEN env var (handled by credential_service fallback).
      3. GH_TOKEN env var.
      4. GITHUB_TOKEN env var (alias used by GitHub Actions and some CI setups).
    """
    try:
        from app.services.credential_service import get_credential_value
        value = get_credential_value("gh_token")
        if value:
            return value
    except Exception:
        pass
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def _inject_token(repo_url: str, token: str) -> str:
    """Insert a GitHub token into an HTTPS URL for passwordless git push.

    https://github.com/org/repo  →  https://<token>@github.com/org/repo
    """
    if token and "github.com" in repo_url and repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://{token}@", 1)
    return repo_url


def _git(*args, cwd=None):
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _gh_pr_create(branch: str, title: str, body: str, cwd=None, gh_token: str = "") -> str:
    """Create a GitHub PR via the REST API — no gh CLI required."""
    import json
    import urllib.error
    import urllib.request

    repo_url = os.environ.get("AUTOBOT_GITHUB_REPO", "").strip()
    if not repo_url:
        raise RuntimeError(
            "AUTOBOT_GITHUB_REPO no está configurado. "
            "Añade AUTOBOT_GITHUB_REPO=https://github.com/owner/repo en .env"
        )

    # Extract owner/repo from URL: https://github.com/owner/repo[.git]
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", repo_url)
    if not m:
        raise RuntimeError(f"No se puede parsear el repo de GitHub desde: {repo_url}")
    owner_repo = m.group(1)

    # Determine base branch
    base_branch = os.environ.get("AUTOBOT_GITHUB_BASE_BRANCH", "main")

    api_url = f"https://api.github.com/repos/{owner_repo}/pulls"
    payload = json.dumps({
        "title": title,
        "body": body,
        "head": branch,
        "base": base_branch,
    }).encode("utf-8")

    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "autobot/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            return data["html_url"]
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        # 422 = PR already exists for this branch
        if exc.code == 422:
            existing = _find_existing_pr(owner_repo, branch, gh_token)
            if existing:
                return existing
        raise RuntimeError(f"GitHub API {exc.code}: {err_body}") from exc


def _find_existing_pr(owner_repo: str, branch: str, gh_token: str) -> str | None:
    """Return the HTML URL of an open PR for ``branch``, or None."""
    import json
    import urllib.request

    api_url = f"https://api.github.com/repos/{owner_repo}/pulls?head={branch}&state=open"
    req = urllib.request.Request(
        api_url,
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "autobot/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            prs = json.loads(resp.read())
            if prs:
                return prs[0]["html_url"]
    except Exception:
        pass
    return None


def _git_branch_exists(branch: str) -> bool:
    root = _repo_root()
    try:
        result = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=root,
            capture_output=True, text=True, check=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _repo_root() -> Path | None:
    # Anchor to this file so the subprocess finds the git root even when
    # the process cwd differs (e.g. inside Docker with cwd=/app).
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=here,
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip())
    except Exception:
        return None


def _add_bytes_to_tar(tar: tarfile.TarFile, data: bytes, arcname: str) -> None:
    import io
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))
