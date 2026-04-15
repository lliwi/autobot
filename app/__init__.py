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
        """Interactive initial setup: database, admin user, encryption key, and OAuth."""
        import secrets

        from cryptography.fernet import Fernet

        from app.models.user import User

        click.echo("=" * 50)
        click.echo("  AUTOBOT — Initial Setup")
        click.echo("=" * 50)
        click.echo()

        # 1. Run migrations
        click.echo("[1/4] Applying database migrations...")
        from flask_migrate import upgrade

        upgrade()
        click.echo("  OK — Database up to date.")
        click.echo()

        # 2. Create admin user
        click.echo("[2/4] Admin user setup")
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
        click.echo("[3/4] Token encryption key")
        current_key = app.config.get("TOKEN_ENCRYPTION_KEY", "")
        if current_key:
            click.echo("  TOKEN_ENCRYPTION_KEY is already set in .env")
        else:
            new_key = Fernet.generate_key().decode()
            click.echo(f"  Generated key: {new_key}")
            click.echo("  Add this to your .env file:")
            click.echo(f"  TOKEN_ENCRYPTION_KEY={new_key}")
        click.echo()

        # 4. OAuth config + interactive login
        click.echo("[4/4] OpenAI Codex OAuth")
        _run_codex_login(app)
        click.echo()

        click.echo("=" * 50)
        click.echo("  Setup complete!")
        click.echo()
        click.echo("  Open http://localhost:5000")
        click.echo("=" * 50)

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
