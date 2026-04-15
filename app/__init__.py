import os

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

    # Register blueprints
    from app.api import api_bp
    from app.dashboard import dashboard_bp

    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(dashboard_bp)

    # CLI commands
    register_cli(app)

    return app


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
        """Interactive initial setup: database, admin user, encryption key, OAuth and orchestrator agent."""
        from cryptography.fernet import Fernet

        from app.models.user import User

        click.echo("=" * 50)
        click.echo("  AUTOBOT — Initial Setup")
        click.echo("=" * 50)
        click.echo()

        # 1. Run migrations
        click.echo("[1/5] Applying database migrations...")
        from flask_migrate import upgrade

        upgrade()
        click.echo("  OK — Database up to date.")
        click.echo()

        # 2. Create admin user
        click.echo("[2/5] Admin user setup")
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
        click.echo("[3/5] Token encryption key")
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
        click.echo("[4/5] OpenAI Codex OAuth")
        _run_codex_login(app)
        click.echo()

        # 5. Default agents (orchestrator + reviewer)
        click.echo("[5/5] Default agents (orchestrator + reviewer)")
        _run_default_agents_setup(app)
        click.echo()

        click.echo("=" * 50)
        click.echo("  Setup complete!")
        click.echo()
        click.echo("  Open http://localhost:5000")
        click.echo("=" * 50)

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


def _render_soul(name, role, tone, priorities, limits, mission):
    priorities_block = "\n".join(f"- {p}" for p in priorities) or "- (none)"
    limits_block = "\n".join(f"- {p}" for p in limits) or "- (none)"
    return f"""# Soul — {name}

## Identity
{role}

## Mission
{mission}

## Tone & Style
{tone}

## Principles
{priorities_block}

## Limits
{limits_block}
"""


def _render_agents_md(name, role, type_label="orchestrator", peers=None):
    peers_block = ""
    if peers:
        peers_block = "\n## Peer agents\n" + "\n".join(
            f"- **{peer_name}** (`{peer_slug}`) — {peer_role}" for peer_name, peer_slug, peer_role in peers
        ) + "\n"
    return f"""# Agents

## {name} ({type_label})
- **Role:** {role}
- **Type:** root / {type_label}
- **Sub-agents:** none yet
{peers_block}
Sub-agents will be appended below as they are created.
"""


def _render_memory_md(initial_memory):
    if initial_memory:
        return f"""# Memory

## Initial context
- {initial_memory}
"""
    return """# Memory

No memories recorded yet. Entries will be appended here as the agent learns.
"""
