import os
from flask import Flask, jsonify
from dotenv import load_dotenv


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-development-only-change-me"),
        DEBUG=os.getenv("FLASK_ENV") == "development",
    )
    if test_config:
        app.config.update(test_config)

    from nearshare.routes.health import health_bp
    app.register_blueprint(health_bp, url_prefix="/api")

    @app.get("/")
    def index():
        return jsonify(name="NearShare", tagline="Why Buy When You Can Borrow?", status="running")

    return app
