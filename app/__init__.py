from flask import Flask
from werkzeug.security import generate_password_hash
from .config import Config, DEFAULT_SETTINGS
from .services import default_services
from .state import JobState


def create_app(config=None):
    """Create an isolated BookProcessor application instance."""
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.extensions["bookprocessor_services"] = app.config.get("SERVICES") or default_services(app.config)
    app.extensions["job_state"] = JobState(app.config["WEB_JOB_WORKERS"])

    from .auth import bp as auth_bp
    from .admin import bp as admin_bp
    from .books import bp as books_bp
    from .health import bp as health_bp
    from .jobs import bp as jobs_bp
    for blueprint in (auth_bp, admin_bp, books_bp, health_bp, jobs_bp):
        app.register_blueprint(blueprint)

    if app.config.get("BOOTSTRAP_ADMIN"):
        services = app.extensions["bookprocessor_services"]
        services.database.ensure_admin(
            app.config["DATABASE_URL"], app.config["ADMIN_USERNAME"],
            generate_password_hash(app.config["ADMIN_PASSWORD"]),
        )
    if app.config.get("START_FOLDER_WATCHER"):
        from .folder_watcher import start_folder_watcher
        app.extensions["folder_watcher"] = start_folder_watcher(app)
    return app


__all__ = ["create_app", "DEFAULT_SETTINGS"]
