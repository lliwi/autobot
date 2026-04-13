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

        # 4. OAuth config
        click.echo("[4/4] OpenAI Codex OAuth configuration")
        client_id = app.config.get("OPENAI_CLIENT_ID", "")
        if client_id:
            click.echo(f"  OPENAI_CLIENT_ID is set: {client_id[:8]}...")
        else:
            click.echo("  OPENAI_CLIENT_ID is not configured.")
            click.echo("  To enable chat with OpenAI Codex, add these to your .env:")
            click.echo("    OPENAI_CLIENT_ID=<your-client-id>")
            click.echo("    OPENAI_CLIENT_SECRET=<your-client-secret>")
            click.echo("    OPENAI_REDIRECT_URI=http://localhost:5000/api/oauth/openai/callback")
        click.echo()

        click.echo("=" * 50)
        click.echo("  Setup complete!")
        click.echo()
        click.echo("  Next steps:")
        click.echo("    1. Review/update your .env file")
        click.echo("    2. Restart: docker compose restart web")
        click.echo("    3. Open http://localhost:5000")
        click.echo("=" * 50)
