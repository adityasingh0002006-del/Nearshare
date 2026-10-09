import os
import secrets
from datetime import timedelta
from flask import Flask, jsonify
from werkzeug.exceptions import RequestEntityTooLarge
from dotenv import load_dotenv


def create_app(test_config=None):
    load_dotenv()
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key and not (test_config and test_config.get("TESTING")):
        raise RuntimeError("Set SECRET_KEY to a long, random value in the environment")
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=secret_key or secrets.token_urlsafe(32),
        DEBUG=os.getenv("FLASK_ENV") == "development",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV", "development") != "development",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    )
    if test_config:
        app.config.update(test_config)

    from routes.auth import auth_bp
    from routes.admin import admin_bp
    from routes.items import items_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(items_bp, url_prefix="/api/items")
    app.config["MAX_CONTENT_LENGTH"] = (test_config or {}).get(
        "MAX_CONTENT_LENGTH", 5 * 1024 * 1024 + 64 * 1024
    )

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify(error="Request exceeds the 5 MB image upload limit"), 413

    from nearshare.routes.health import health_bp
    app.register_blueprint(health_bp, url_prefix="/api")

    @app.get("/")
    def index():
        return jsonify(name="NearShare", tagline="Why Buy When You Can Borrow?", status="running")

    return app
