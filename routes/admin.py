"""Administrator-only account management and platform oversight APIs."""

import re

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import admin_required


admin_bp = Blueprint("admin", __name__)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_MAX_ID = 2_147_483_647
_MAX_PAGE_SIZE = 100


class AdminError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _db_error():
    current_app.logger.exception("Admin database operation failed")
    return jsonify(error="Admin service unavailable"), 503


def _require_current_admin(cursor, user_id):
    cursor.execute(
        """SELECT role, is_active FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
           WHERE user_id = ?""",
        user_id,
    )
    row = cursor.fetchone()
    if not row or row[0] != "ADMIN" or not row[1]:
        raise AdminError("Administrator authorization is no longer valid", 403)


def _audit(cursor, actor_id, action, entity_id):
    cursor.execute(
        """INSERT INTO dbo.AuditLogs (user_id, action, entity_type, entity_id, ip_address)
           VALUES (?, ?, N'User', ?, ?)""",
        actor_id, action, str(entity_id), request.remote_addr,
    )


def _query_integer(name, default, maximum):
    raw = request.args.get(name)
    if raw is None:
        return default
    if not _INTEGER_RE.fullmatch(raw):
        raise ValueError(f"{name} must be a positive integer")
    value = int(raw)
    if value > maximum:
        raise ValueError(f"{name} must be no greater than {maximum}")
    return value


def _serialize_user(row):
    return {
        "user_id": row[0],
        "full_name": row[1],
        "email": row[2],
        "role": row[3],
        "locality_id": row[4],
        "is_active": bool(row[5]),
        "created_at": row[6].isoformat() if row[6] else None,
    }


def _user_filter():
    allowed = {"page", "per_page", "is_active", "role"}
    if set(request.args) - allowed:
        raise ValueError("Unsupported user-list filter")

    page = _query_integer("page", 1, _MAX_ID)
    per_page = _query_integer("per_page", 50, _MAX_PAGE_SIZE)
    clauses = []
    params = []
    active = request.args.get("is_active")
    if active is not None:
        if active not in {"true", "false"}:
            raise ValueError("is_active must be true or false")
        clauses.append("is_active = ?")
        params.append(active == "true")
    role = request.args.get("role")
    if role is not None:
        role = role.upper()
        if role not in {"USER", "ADMIN"}:
            raise ValueError("role must be USER or ADMIN")
        clauses.append("role = ?")
        params.append(role)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return page, per_page, where, params


@admin_bp.get("/me")
@admin_required
def admin_me():
    """Small protected endpoint demonstrating server-side role enforcement."""
    return jsonify(user_id=session["user_id"], role=session["role"]), 200


@admin_bp.get("/users")
@admin_required
def list_users():
    try:
        page, per_page, where, filter_params = _user_filter()
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        _require_current_admin(cursor, session["user_id"])
        cursor.execute(
            f"SELECT COUNT(*) FROM dbo.Users{where}", *filter_params,
        )
        total = int(cursor.fetchone()[0])
        cursor.execute(
            f"""SELECT user_id, full_name, email, role, locality_id,
                      is_active, created_at
               FROM dbo.Users{where}
               ORDER BY created_at DESC, user_id DESC
               OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
            *filter_params, (page - 1) * per_page, per_page,
        )
        users = [_serialize_user(row) for row in cursor.fetchall()]
        return jsonify(users=users, page=page, per_page=per_page, total=total), 200
    except AdminError as exc:
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@admin_bp.patch("/users/<int:user_id>/status")
@admin_required
def set_user_status(user_id):
    if user_id <= 0 or user_id > _MAX_ID:
        return jsonify(error="user_id must be a positive integer"), 400
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"is_active"}:
        return jsonify(error="Request must contain only is_active"), 400
    desired_active = data["is_active"]
    if not isinstance(desired_active, bool):
        return jsonify(error="is_active must be a boolean"), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        _require_current_admin(cursor, session["user_id"])
        cursor.execute(
            """SELECT role, is_active FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
               WHERE user_id = ?""",
            user_id,
        )
        target = cursor.fetchone()
        if not target:
            conn.rollback()
            return jsonify(error="User not found"), 404

        target_role, current_active = target[0], bool(target[1])
        if user_id == session["user_id"] and not desired_active:
            conn.rollback()
            return jsonify(error="You cannot deactivate your own administrator account"), 409
        if target_role == "ADMIN" and current_active and not desired_active:
            cursor.execute(
                """SELECT COUNT(*) FROM dbo.Users WITH (UPDLOCK, HOLDLOCK)
                   WHERE role = N'ADMIN' AND is_active = 1""",
            )
            if int(cursor.fetchone()[0]) <= 1:
                conn.rollback()
                return jsonify(error="The last active administrator cannot be deactivated"), 409

        if current_active == desired_active:
            conn.commit()
            return jsonify(user={"user_id": user_id, "is_active": current_active}), 200

        cursor.execute(
            """UPDATE dbo.Users SET is_active = ?, updated_at = SYSUTCDATETIME()
               OUTPUT INSERTED.user_id, INSERTED.is_active
               WHERE user_id = ? AND is_active = ?""",
            desired_active, user_id, current_active,
        )
        updated = cursor.fetchone()
        if not updated:
            conn.rollback()
            return jsonify(error="User status changed; reload and try again"), 409

        action = "ADMIN_USER_ACTIVATED" if desired_active else "ADMIN_USER_DEACTIVATED"
        _audit(cursor, session["user_id"], action, user_id)
        conn.commit()
        return jsonify(user={"user_id": updated[0], "is_active": bool(updated[1])}), 200
    except AdminError as exc:
        if conn:
            conn.rollback()
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@admin_bp.get("/summary")
@admin_required
def platform_summary():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        _require_current_admin(cursor, session["user_id"])
        cursor.execute(
            """SELECT
                 (SELECT COUNT(*) FROM dbo.Users) AS users_total,
                 (SELECT COUNT(*) FROM dbo.Users WHERE is_active = 1) AS users_active,
                 (SELECT COUNT(*) FROM dbo.Requests) AS requests_total,
                 (SELECT COUNT(*) FROM dbo.Offers) AS offers_total,
                 (SELECT COUNT(*) FROM dbo.Bookings) AS bookings_total,
                 (SELECT COUNT(*) FROM dbo.Reviews) AS reviews_total""",
        )
        row = cursor.fetchone()
        return jsonify(summary={
            "users_total": int(row[0]),
            "users_active": int(row[1]),
            "requests_total": int(row[2]),
            "offers_total": int(row[3]),
            "bookings_total": int(row[4]),
            "reviews_total": int(row[5]),
        }), 200
    except AdminError as exc:
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()
