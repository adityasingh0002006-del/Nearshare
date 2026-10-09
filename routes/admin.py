"""Administrator-only API routes."""

from flask import Blueprint, jsonify, session

from middleware.auth import admin_required


admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/me")
@admin_required
def admin_me():
    """Small protected endpoint demonstrating server-side role enforcement."""
    return jsonify(user_id=session["user_id"], role=session["role"]), 200
