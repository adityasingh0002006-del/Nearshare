"""Application configuration and Flask app factory."""

import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from werkzeug.exceptions import RequestEntityTooLarge


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config=None):
    """Create and configure the NearShare web application."""
    load_dotenv(BASE_DIR / ".env")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key and not (test_config and test_config.get("TESTING")):
        raise RuntimeError("Set SECRET_KEY to a long, random value in the environment")
    app.config.from_mapping(
        SECRET_KEY=secret_key or secrets.token_urlsafe(32),
        DEBUG=os.getenv("FLASK_ENV", "development") == "development",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV", "development") != "development",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        DB_SERVER=os.getenv("DB_SERVER"),
        DB_NAME=os.getenv("DB_NAME"),
        DB_USER=os.getenv("DB_USER"),
        DB_PASSWORD=os.getenv("DB_PASSWORD"),
        DB_DRIVER=os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
    )
    if test_config:
        app.config.update(test_config)

    from routes.auth import auth_bp
    from routes.admin import admin_bp
    from routes.items import items_bp
    from routes.requests import requests_bp
    from routes.offers import offers_bp
    from routes.bookings import bookings_bp
    from routes.reviews import reviews_bp
    from routes.notifications import notifications_bp
    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(items_bp, url_prefix="/api/items")
    app.register_blueprint(requests_bp, url_prefix="/api/requests")
    app.register_blueprint(offers_bp, url_prefix="/api")
    app.register_blueprint(bookings_bp, url_prefix="/api/bookings")
    app.register_blueprint(reviews_bp, url_prefix="/api/reviews")
    app.register_blueprint(notifications_bp, url_prefix="/api/notifications")
    app.config["MAX_CONTENT_LENGTH"] = (test_config or {}).get(
        "MAX_CONTENT_LENGTH", 5 * 1024 * 1024 + 64 * 1024
    )

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify(error="Request exceeds the 5 MB image upload limit"), 413

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", message="NearShare API is running"), 200

    return app
