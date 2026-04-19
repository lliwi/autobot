import os
from datetime import datetime, timezone

from flask import Flask

from app.config import config
from app.extensions import bcrypt, csrf, db, login_manager, migrate
from app.logging_config import configure_logging


def create_app(config_name=None):
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "default")

    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    csrf.init_app(app)

    configure_logging(app)
    register_template_filters(app)

    # Register blueprints
    from app.api import api_bp
    from app.dashboard import dashboard_bp

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp)

    # CLI commands
    register_cli(app)

    return app


def register_template_filters(app):
    """Make DB datetimes render in the configured local timezone.

    Models store all timestamps in UTC (many as *naive* datetimes created with
    ``datetime.now(timezone.utc)`` but stored in a column without ``timezone=True``).
    Without conversion, templates rendered UTC regardless of the ``TZ`` env var,
    which is confusing for users living in CEST/CET. This filter:

      1. Treats naive datetimes as UTC (matches how the app writes them).
      2. Converts to the zone given by ``APP_TIMEZONE``/``TZ``, falling back to
         ``Europe/Madrid`` for this project; then to UTC if even that fails.
      3. Formats with ``strftime``. The empty/``None`` case returns ``"-"`` so
         templates stop needing ``{% if foo %}`` guards around every call.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover — Python 3.9+ always has zoneinfo
        ZoneInfo = None

    tz_name = (
        app.config.get("APP_TIMEZONE")
        or os.environ.get("TZ")
        or "UTC"
    )
    try:
        local_tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
    except Exception:
        app.logger.warning("Unknown timezone %r, falling back to UTC", tz_name)
        local_tz = timezone.utc

    def localtz(value, fmt="%Y-%m-%d %H:%M"):
        if value is None:
            return "-"
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(local_tz).strftime(fmt)

    app.jinja_env.filters["localtz"] = localtz


def register_cli(app):
    import click

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_admin(email, password):
        """Create an admin user."""
        from app.models.user import User

        if User.query.filter_by(email=email).first():
            click.echo(f"User {email} already exists.")
            return

        user = User(email=email, role="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin user {email} created.")

    @app.cli.command("onboard")
    def onboard():
        """Interactive initial setup: database, admin user, encryption key, OAuth, default agents, Matrix."""
        from cryptography.fernet import Fernet

        from app.models.user import User

        click.echo("=" * 50)
        click.echo("  AUTOBOT — Initial Setup")
        click.echo("=" * 50)
        click.echo()

        # 1. Run migrations
        click.echo("[1/6] Applying database migrations...")
        from flask_migrate import upgrade

        upgrade()
        click.echo("  OK — Database up to date.")
        click.echo()

        # 2. Create admin user
        click.echo("[2/6] Admin user setup")
        existing = User.query.first()
        if existing:
            click.echo(f"  Admin user already exists: {existing.email}")
            create_user = click.confirm("  Create another admin?", default=False)
        else:
            create_user = True

        if create_user:
            email = click.prompt("  Email", default="admin@autobot.local")
            password = click.prompt("  Password", hide_input=True, confirmation_prompt=True)
            if User.query.filter_by(email=email).first():
                click.echo(f"  User {email} already exists, skipping.")
            else:
                user = User(email=email, role="admin")
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                click.echo(f"  Admin user {email} created.")
        click.echo()

        # 3. Encryption key
        click.echo("[3/6] Token encryption key")
        current_key = app.config.get("TOKEN_ENCRYPTION_KEY", "")
        if current_key:
            click.echo("  TOKEN_ENCRYPTION_KEY is already set in .env")
        else:
            new_key = Fernet.generate_key().decode()
            click.echo(f"  Generated key: {new_key}")
            click.echo("  Add this to your .env file:")
            click.echo(f"  TOKEN_ENCRYPTION_KEY={new_key}")
        click.echo()

        # 4. Codex OAuth
        click.echo("[4/6] OpenAI Codex OAuth")
        _run_codex_login(app)
        click.echo()

        # 5. Default agents (orchestrator + reviewer)
        click.echo("[5/6] Default agents (orchestrator + reviewer)")
        _run_default_agents_setup(app)
        click.echo()

        # 6. Matrix channel (optional)
        click.echo("[6/6] Matrix channel (optional)")
        _run_matrix_setup(app)
        click.echo()

        click.echo("=" * 50)
        click.echo("  Setup complete!")
        click.echo()
        click.echo("  Open http://localhost:5000")
        click.echo("=" * 50)

    @app.cli.command("setup-matrix")
    def setup_matrix():
        """Interactive Matrix channel setup (skips the rest of onboard)."""
        _run_matrix_setup(app)

    @app.cli.command("setup-default-agents")
    def setup_default_agents():
        """Create (or reconfigure) the default agents: orchestrator (optimus) and reviewer."""
        _run_default_agents_setup(app)

    @app.cli.command("codex-login")
    def codex_login():
        """Run the Codex OAuth/PKCE flow. Prints the authorize URL and waits for the browser callback."""
        _run_codex_login(app)

    @app.cli.command("codex-logout")
    def codex_logout():
        """Delete the stored Codex token."""
        from app.services import codex_auth

        if codex_auth.logout():
            click.echo("  Codex token removed.")
        else:
            click.echo("  No Codex token to remove.")

    @app.cli.command("codex-status")
    def codex_status():
        """Show current Codex login status."""
        from app.services import codex_auth

        if codex_auth.is_logged_in():
            click.echo(f"  Logged in (token: {codex_auth.token_path()})")
            click.echo(f"  Account id: {codex_auth.get_account_id() or '-'}")
        else:
            click.echo("  Not logged in. Run `flask codex-login` to connect.")

    @app.cli.command("export-bundle")
    @click.option("--output", "-o", required=True, type=click.Path(dir_okay=False),
                  help="Path to the .tar.gz bundle to write.")
    @click.option("--include-env", is_flag=True, default=False,
                  help="Include the project .env verbatim (contains secrets).")
    @click.option("--include-secrets", is_flag=True, default=False,
                  help="Include decrypted credential values in credentials.json.")
    @click.confirmation_option(
        "--yes", "-y",
        prompt="This will snapshot agents, workspaces, tools, skills, packages"
               " and (optionally) credentials and .env to a tarball. Continue?",
    )
    def export_bundle(output, include_env, include_secrets):
        """Export agents, workspaces, tools, skills, packages, credentials and .env to a portable bundle."""
        from app.services import bundle_service

        if not bundle_service.is_valid_bundle_name(output):
            raise click.UsageError("Output must end in .tar.gz or .tgz")

        if include_env or include_secrets:
            click.echo("  ⚠ The bundle will contain secrets in plaintext. Protect the file accordingly.")

        report = bundle_service.export_bundle(
            output,
            include_env=include_env,
            include_secrets=include_secrets,
        )
        click.echo(f"  ✓ Bundle written to {report.path}")
        click.echo(f"    agents={report.agents} tools={report.tools} skills={report.skills}"
                   f" packages={report.packages} credentials={report.credentials}"
                   f" workspaces={report.workspaces}")
        if report.included_env:
            click.echo("    included .env")
        if report.included_secrets:
            click.echo("    included credential values")

    @app.cli.command("import-bundle")
    @click.option("--input", "-i", "input_path", required=True,
                  type=click.Path(exists=True, dir_okay=False),
                  help="Path to the .tar.gz bundle to load.")
    @click.option("--overwrite", is_flag=True, default=False,
                  help="Replace existing rows / files when the slug matches.")
    @click.confirmation_option(
        "--yes", "-y",
        prompt="This will write into the database and workspaces. Continue?",
    )
    def import_bundle(input_path, overwrite):
        """Import an Autobot bundle produced by `flask export-bundle`."""
        from app.services import bundle_service

        try:
            report = bundle_service.import_bundle(input_path, overwrite=overwrite)
        except ValueError as e:
            raise click.ClickException(str(e)) from e

        click.echo("  ✓ Import complete.")
        click.echo(
            f"    agents +{report.agents_created}/±{report.agents_updated}/skip {report.agents_skipped}"
            f"   tools +{report.tools_created}/±{report.tools_updated}"
            f"   skills +{report.skills_created}/±{report.skills_updated}"
        )
        click.echo(
            f"    packages +{report.packages_created}/±{report.packages_updated}"
            f"   credentials +{report.credentials_created}/±{report.credentials_updated}"
            f"   workspaces={report.workspaces_restored}"
        )
        if report.env_written:
            click.echo("    .env written (restart services to pick up changes)")
        for w in report.warnings:
            click.echo(f"  ⚠ {w}")


def _run_codex_login(app):
    """Interactive Codex OAuth/PKCE flow.

    Uses oauth_cli_kit: launches a local HTTP server on port 1455, prints the
    authorize URL, and blocks until the browser callback arrives.
    """
    import click

    from app.services import codex_auth

    if codex_auth.is_logged_in():
        click.echo(f"  Already logged in as account {codex_auth.get_account_id() or '?'}.")
        if not click.confirm("  Re-authenticate?", default=False):
            return

    click.echo()
    click.echo("  Starting Codex OAuth flow...")
    click.echo("  A local callback server will run on http://localhost:1455/auth/callback.")
    click.echo("  Follow the URL below in your browser — keep this terminal open until login finishes.")
    click.echo()

    try:
        token = codex_auth.login(print_fn=lambda s: click.echo(f"    {s}"), prompt_fn=input)
    except Exception as e:
        click.echo(f"  ✗ Login failed: {e}")
        return

    click.echo()
    click.echo(f"  ✓ Codex connected. account_id={token.account_id}")


def _run_matrix_setup(app):
    """Interactive Matrix channel setup.

    Collects homeserver/user_id/password + optional allowlists, validates the
    credentials against the homeserver, and rewrites the project .env in place.
    Safe to skip — Matrix is an optional channel.
    """
    import click

    from app.services import matrix_setup

    already_configured = bool(app.config.get("MATRIX_HOMESERVER"))
    if already_configured:
        click.echo(f"  Matrix is already configured for {app.config.get('MATRIX_USER_ID', '?')}")
        if not click.confirm("  Reconfigure?", default=False):
            return
    else:
        if not click.confirm("  Configure Matrix now?", default=False):
            click.echo("  Skipped. You can run `flask setup-matrix` later.")
            return

    homeserver = click.prompt("  Homeserver URL", default=app.config.get("MATRIX_HOMESERVER") or "https://matrix.org")
    user_id = click.prompt("  Bot user id (@user:server.tld)", default=app.config.get("MATRIX_USER_ID") or "")
    password = click.prompt("  Bot password", hide_input=True, confirmation_prompt=True)
    allowed_rooms = click.prompt(
        "  Allowed rooms (CSV, blank = all rooms)",
        default=app.config.get("MATRIX_ALLOWED_ROOMS") or "",
        show_default=False,
    )
    allowed_users = click.prompt(
        "  Allowed users (CSV, blank = all users)",
        default=app.config.get("MATRIX_ALLOWED_USERS") or "",
        show_default=False,
    )
    allowed_dm_users = click.prompt(
        "  DM-only allowlist (CSV, blank = fall back to allowed users)",
        default=app.config.get("MATRIX_ALLOWED_DM_USERS") or "",
        show_default=False,
    )
    group_policy = click.prompt(
        "  Group response policy (always/mention/allowlist)",
        default=app.config.get("MATRIX_GROUP_POLICY") or "mention",
    )

    click.echo("  Validating credentials against the homeserver...")
    result = matrix_setup.configure(
        homeserver=homeserver,
        user_id=user_id,
        password=password,
        allowed_rooms=allowed_rooms,
        allowed_users=allowed_users,
        allowed_dm_users=allowed_dm_users,
        group_policy=group_policy,
    )

    if not result["ok"]:
        click.echo(f"  ✗ {result['message']}")
        return

    click.echo(f"  ✓ {result['message']}")
    click.echo(f"  ✓ Written to {result['env_path']}")
    for key, value in (result.get("values") or {}).items():
        click.echo(f"    {key}={value}")
    click.echo()
    click.echo("  Restart the worker so the new env vars take effect:")
    click.echo("    docker compose restart worker")


DEFAULT_AGENT_SPECS = [
    {
        "slug": "optimus",
        "label": "Orchestrator",
        "type_label": "orchestrator",
        "default_name": "optimus",
        "default_role": "Orchestrator agent coordinating sub-agents and tools",
        "default_tone": "concise, direct, professional",
        "default_priorities": "task decomposition, delegation, observability",
        "default_limits": "no destructive ops without confirmation, no access outside workspace",
        "default_mission": "Coordinate Autobot sub-agents to accomplish user goals reliably.",
    },
    {
        "slug": "reviewer",
        "label": "Reviewer",
        "type_label": "reviewer",
        "default_name": "reviewer",
        "default_role": "Reviewer agent auditing the work of other agents and sub-agents",
        "default_tone": "rigorous, constructive, specific",
        "default_priorities": "detect errors, surface improvement opportunities, verify requirements are met",
        "default_limits": "do not execute or modify code directly, only report findings",
        "default_mission": "Supervise the output of other agents to catch mistakes and propose improvements before the user sees the result.",
    },
]


def _run_default_agents_setup(app):
    """Interactive creation/reconfiguration of the default agents.

    Currently provisions two root agents: the orchestrator (``optimus``) and the
    reviewer. Both get their SOUL.md / AGENTS.md / MEMORY.md generated from the
    user's answers. Existing rows with the same slug are reused.
    """
    import click

    created_agents = []
    for spec in DEFAULT_AGENT_SPECS:
        click.echo()
        click.echo(f"  ── {spec['label']} agent ({spec['slug']}) ──")
        agent = _provision_default_agent(app, spec)
        if agent is not None:
            created_agents.append(agent)

    # Cross-reference the agents: record reviewer in optimus' AGENTS.md so the
    # orchestrator knows who to route quality reviews to.
    if len(created_agents) > 1:
        from app.workspace.manager import write_file

        slugs = {a.slug: a for a in created_agents}
        optimus = slugs.get("optimus")
        reviewer = slugs.get("reviewer")
        if optimus and reviewer:
            write_file(
                optimus,
                "AGENTS.md",
                _render_agents_md(
                    optimus.name,
                    "Orchestrator — coordinates sub-agents and delegates tasks",
                    peers=[(reviewer.name, reviewer.slug, "reviews outputs for errors and improvements")],
                ),
            )


def _provision_default_agent(app, spec):
    """Create or reconfigure a single default agent based on ``spec``."""
    import click

    from app.models.agent import Agent
    from app.services import codex_auth
    from app.services.agent_service import create_agent
    from app.workspace.manager import write_file

    existing = Agent.query.filter_by(slug=spec["slug"]).first()
    if existing:
        click.echo(f"  {spec['label']} '{existing.name}' already exists.")
        if not click.confirm("  Reconfigure its workspace files?", default=False):
            return existing
        agent = existing
    else:
        name = click.prompt("  Agent name", default=spec["default_name"])
        agent = create_agent({"name": name, "slug": spec["slug"]})
        click.echo(f"  ✓ Agent '{agent.name}' created (slug={agent.slug}).")

    available = codex_auth.list_models()
    if available:
        click.echo(f"  Available models: {', '.join(available)}")
    current = agent.model_name or (available[0] if available else "gpt-5.2")
    model_name = click.prompt("  Model", default=current)
    if model_name and model_name != agent.model_name:
        agent.model_name = model_name
        db.session.commit()

    click.echo("  Now let's shape the agent's identity. Leave blank to keep defaults.")

    role = click.prompt("  Role (one line)", default=spec["default_role"])
    tone = click.prompt("  Tone / style", default=spec["default_tone"])
    priorities_raw = click.prompt(
        "  Top priorities (comma-separated)", default=spec["default_priorities"]
    )
    limits_raw = click.prompt(
        "  Hard limits (comma-separated)", default=spec["default_limits"]
    )
    mission = click.prompt(
        "  Mission statement (what this agent is here to accomplish)",
        default=spec["default_mission"],
    )
    initial_memory = click.prompt(
        "  Initial memory (one key fact or context, optional)",
        default="",
        show_default=False,
    )

    priorities = [p.strip() for p in priorities_raw.split(",") if p.strip()]
    limits = [p.strip() for p in limits_raw.split(",") if p.strip()]

    soul = _render_soul(agent.name, role, tone, priorities, limits, mission)
    agents_md = _render_agents_md(agent.name, role, type_label=spec["type_label"])
    memory_md = _render_memory_md(initial_memory)

    write_file(agent, "SOUL.md", soul)
    write_file(agent, "AGENTS.md", agents_md)
    write_file(agent, "MEMORY.md", memory_md)

    click.echo(f"  ✓ Workspace files written to {agent.workspace_path}")
    click.echo("    - SOUL.md, AGENTS.md, MEMORY.md")
    return agent


def _read_template(filename):
    """Return the contents of ``workspaces/_template/<filename>`` or ``""`` if missing.

    Onboarding layers user-provided data on top of the shared template so the
    Spanish directives (Identidad de ejecución, Ética operativa, etc.) are
    preserved instead of being clobbered by hand-crafted blocks.
    """
    from app.workspace.manager import _template_path
    path = _template_path() / filename
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _inject_after_first_heading(template_text, block):
    """Insert ``block`` right after the first H1 line of ``template_text``."""
    if not template_text:
        return block
    parts = template_text.split("\n", 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    return head + "\n\n" + block.rstrip() + "\n" + rest


def _render_soul(name, role, tone, priorities, limits, mission):
    priorities_block = "\n".join(f"- {p}" for p in priorities) or "- (none)"
    limits_block = "\n".join(f"- {p}" for p in limits) or "- (none)"
    onboarding = (
        f"## Onboarded identity — {name}\n"
        f"- **Role:** {role}\n"
        f"- **Mission:** {mission}\n"
        f"- **Tone & style:** {tone}\n"
        f"\n"
        f"### Declared priorities\n"
        f"{priorities_block}\n"
        f"\n"
        f"### Declared limits\n"
        f"{limits_block}\n"
    )
    template = _read_template("SOUL.md") or "# Soul\n"
    return _inject_after_first_heading(template, onboarding)


def _render_agents_md(name, role, type_label="orchestrator", peers=None):
    peers_lines = ""
    if peers:
        peers_lines = "\n**Peer agents:**\n" + "\n".join(
            f"- {peer_name} (`{peer_slug}`) — {peer_role}"
            for peer_name, peer_slug, peer_role in peers
        ) + "\n"
    self_block = (
        f"## Root agent: {name} ({type_label})\n"
        f"- **Role:** {role}\n"
        f"- **Type:** root / {type_label}\n"
        f"- **Sub-agents:** none yet\n"
        f"{peers_lines}"
    )
    template = _read_template("AGENTS.md") or "# Agents\n\nNo sub-agents configured yet.\n"
    placeholder = "No sub-agents configured yet."
    if placeholder in template:
        return template.replace(placeholder, self_block.rstrip(), 1)
    return _inject_after_first_heading(template, self_block)


def _render_memory_md(initial_memory):
    template = _read_template("MEMORY.md") or "# Memory\n\nNo memories recorded yet.\n"
    if not initial_memory:
        return template
    block = f"## Initial context\n- {initial_memory}"
    placeholder = "No memories recorded yet."
    if placeholder in template:
        return template.replace(placeholder, block, 1)
    return _inject_after_first_heading(template, block)
