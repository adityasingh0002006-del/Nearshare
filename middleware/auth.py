"""Session authentication and role authorization decorators."""

from functools import wraps

from flask import current_app, jsonify, session

from database.connection import get_connection


def login_required(view):
    """Require a valid user identity stored in the signed Flask session."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify(error="Authentication required"), 401
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT is_active FROM dbo.Users WHERE user_id = ?",
                user_id,
            )
            row = cursor.fetchone()
        except Exception:
            current_app.logger.exception("Authenticated account status check failed")
            return jsonify(error="Authentication service unavailable"), 503
        finally:
            if conn:
                conn.close()

        if not row or not row[0]:
            session.clear()
            return jsonify(error="Account is inactive"), 403
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    """Require an authenticated user with one of the supplied roles."""
    allowed = {role.upper() for role in roles}

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session.get("role") not in allowed:
                return jsonify(error="Forbidden"), 403
            return view(*args, **kwargs)
        return wrapped
    return decorator


def admin_required(view):
    """Require an authenticated administrator."""
    return roles_required("ADMIN")(view)
