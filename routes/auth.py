"""Registration, login, logout, and current-user API endpoints."""

import re

from flask import Blueprint, current_app, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from database.connection import get_connection
from middleware.auth import login_required


auth_bp = Blueprint("auth", __name__)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _audit(cursor, user_id, action):
    cursor.execute(
        """INSERT INTO dbo.AuditLogs (user_id, action, entity_type, entity_id, ip_address)
           VALUES (?, ?, N'Auth', ?, ?)""",
        user_id,
        action,
        str(user_id) if user_id is not None else None,
        request.remote_addr,
    )


def _public_user(row):
    return {
        "user_id": row[0],
        "full_name": row[1],
        "email": row[2],
        "role": row[3],
        "locality_id": row[4],
    }


def _db_error(message="Authentication service unavailable"):
    current_app.logger.exception("Authentication database operation failed")
    return jsonify(error=message), 503


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="A JSON request body is required"), 400

    full_name = data.get("full_name")
    email = data.get("email")
    phone = data.get("phone")
    password = data.get("password")
    locality_id = data.get("locality_id")
    if not all(isinstance(value, str) and value.strip() for value in (full_name, email, phone, password)):
        return jsonify(error="full_name, email, phone, and password are required"), 400
    full_name, email, phone = full_name.strip(), email.strip().lower(), phone.strip()
    if len(full_name) > 150 or len(email) > 254 or len(phone) > 25 or not _EMAIL_RE.fullmatch(email):
        return jsonify(error="One or more fields are invalid"), 400
    if len(password) < 12 or len(password) > 1024:
        return jsonify(error="Password must be between 12 and 1024 characters"), 400
    if isinstance(locality_id, bool) or not isinstance(locality_id, int) or locality_id <= 0:
        return jsonify(error="locality_id must be a positive integer"), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO dbo.Users (full_name, email, phone, password_hash, role, locality_id)
               OUTPUT INSERTED.user_id, INSERTED.full_name, INSERTED.email,
                      INSERTED.role, INSERTED.locality_id
               VALUES (?, ?, ?, ?, N'USER', ?)""",
            full_name,
            email,
            phone,
            generate_password_hash(password),
            locality_id,
        )
        user = _public_user(cursor.fetchone())
        conn.commit()
        return jsonify(user=user), 201
    except Exception:
        if conn:
            conn.rollback()
        return _db_error("Could not register user; email or phone may already be in use")
    finally:
        if conn:
            conn.close()


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="A JSON request body is required"), 400
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email.strip() or not password:
        return jsonify(error="email and password are required"), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT user_id, full_name, email, password_hash, role, locality_id, is_active
               FROM dbo.Users WHERE email = ?""",
            email.strip().lower(),
        )
        row = cursor.fetchone()
        if not row or not row[6] or not check_password_hash(row[3], password):
            _audit(cursor, row[0] if row else None, "LOGIN_FAILED")
            conn.commit()
            return jsonify(error="Invalid email or password"), 401
        user = _public_user((row[0], row[1], row[2], row[4], row[5]))
        _audit(cursor, row[0], "LOGIN_SUCCESS")
        conn.commit()
        session.clear()
        session["user_id"] = user["user_id"]
        session["role"] = user["role"]
        session["full_name"] = user["full_name"]
        session["email"] = user["email"]
        session.permanent = True
        return jsonify(user=user), 200
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@auth_bp.post("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id is not None:
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            _audit(cursor, user_id, "LOGOUT")
            conn.commit()
        except Exception:
            if conn:
                conn.rollback()
            session.clear()
            return _db_error("Could not complete logout")
        finally:
            if conn:
                conn.close()
    session.clear()
    return jsonify(message="Logged out"), 200


@auth_bp.get("/me")
@login_required
def me():
    return jsonify(user={
        "user_id": session["user_id"],
        "full_name": session.get("full_name"),
        "email": session.get("email"),
        "role": session.get("role"),
    }), 200
