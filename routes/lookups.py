"""Public read-only lookups used by signup and marketplace forms."""

from flask import Blueprint, current_app, jsonify

from database.connection import get_connection


lookups_bp = Blueprint("lookups", __name__)


def _lookup_error():
    current_app.logger.exception("Lookup database operation failed")
    return jsonify(error="Lookup service unavailable"), 503


@lookups_bp.get("/categories")
def list_categories():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category_id, category_name FROM dbo.Categories ORDER BY category_name, category_id"
        )
        return jsonify(categories=[
            {"category_id": row[0], "category_name": row[1]}
            for row in cursor.fetchall()
        ]), 200
    except Exception:
        return _lookup_error()
    finally:
        if conn:
            conn.close()


@lookups_bp.get("/localities")
def list_localities():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT locality_id, locality_name, city, state
               FROM dbo.Localities
               ORDER BY state, city, locality_name, locality_id"""
        )
        return jsonify(localities=[
            {"locality_id": row[0], "locality_name": row[1], "city": row[2], "state": row[3]}
            for row in cursor.fetchall()
        ]), 200
    except Exception:
        return _lookup_error()
    finally:
        if conn:
            conn.close()
