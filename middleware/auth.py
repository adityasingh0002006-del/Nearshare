"""Session authentication and role authorization decorators."""

from functools import wraps

from flask import jsonify, session


def login_required(view):
    """Require a valid user identity stored in the signed Flask session."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify(error="Authentication required"), 401
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
