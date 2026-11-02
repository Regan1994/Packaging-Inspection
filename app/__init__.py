from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

from flask import Flask

from config import Config
from .automatic_backup import run_daily_auto_backup
from .db import close_db, init_db
from .services import format_date, format_datetime, format_duration, format_seconds, format_time, format_work_duration, status_label


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["BACKUP_DIRECTORY"]).mkdir(parents=True, exist_ok=True)
    log_path = Path(app.config["DATABASE"]).parent / "app.log"
    if not any(isinstance(handler, RotatingFileHandler) for handler in app.logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.jinja_env.globals.update(format_date=format_date, format_datetime=format_datetime, format_duration=format_duration, format_seconds=format_seconds, format_time=format_time, format_work_duration=format_work_duration, status_label=status_label, app_version=app.config["APP_VERSION"])
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        if app.config["AUTO_BACKUP_ENABLED"]:
            try:
                backup_path, created = run_daily_auto_backup(
                    app.config["DATABASE"],
                    app.config["BACKUP_DIRECTORY"],
                    retention=app.config["AUTO_BACKUP_RETENTION"],
                )
                if created:
                    app.logger.info("automatic database backup created: %s", backup_path)
            except Exception:
                app.logger.exception("automatic database backup failed; application startup will continue")

    from .routes import bp
    app.register_blueprint(bp)
    return app

