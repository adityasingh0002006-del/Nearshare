"""Application configuration and Flask app factory."""

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template


BASE_DIR = Path(__file__).resolve().parent


def create_app(test_config=None):
    """Create and configure the NearShare web application."""
    load_dotenv(BASE_DIR / ".env")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-development-only-change-me"),
        DEBUG=os.getenv("FLASK_ENV", "development") == "development",
        DB_SERVER=os.getenv("DB_SERVER"),
        DB_NAME=os.getenv("DB_NAME"),
        DB_USER=os.getenv("DB_USER"),
        DB_PASSWORD=os.getenv("DB_PASSWORD"),
        DB_DRIVER=os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server"),
    )
    if test_config:
        app.config.update(test_config)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", message="NearShare API is running"), 200

    return app
