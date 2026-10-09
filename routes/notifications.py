"""Authenticated user's notification inbox and read state."""

import re

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import login_required
from services.notification_service import (
    NotificationError,
    list_notifications,
    mark_notification_read,
    unread_count,
)


notifications_bp = Blueprint("notifications", __name__)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_MAX_PER_PAGE = 100
_MAX_PAGE = 2_147_483_647


def _db_error():
    current_app.logger.exception("Notification database operation failed")
    return jsonify(error="Notification service unavailable"), 503


def _query_integer(name, default, maximum=None):
    raw = request.args.get(name)
    if raw is None:
        return default
    if not _INTEGER_RE.fullmatch(raw):
        raise ValueError(f"{name} must be a positive integer")
    value = int(raw)
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be no greater than {maximum}")
    return value


@notifications_bp.get("")
@login_required
def inbox():
    unsupported = set(request.args) - {"page", "per_page"}
    if unsupported:
        return jsonify(error="Unsupported pagination parameter"), 400
    try:
        page = _query_integer("page", 1, _MAX_PAGE)
        per_page = _query_integer("per_page", 20, _MAX_PER_PAGE)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        notifications = list_notifications(
            conn.cursor(), session["user_id"], page, per_page,
        )
        return jsonify(notifications=notifications, page=page, per_page=per_page), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@notifications_bp.get("/unread-count")
@login_required
def get_unread_count():
    conn = None
    try:
        conn = get_connection()
        count = unread_count(conn.cursor(), session["user_id"])
        return jsonify(unread_count=count), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@notifications_bp.patch("/<int:notification_id>/read")
@login_required
def mark_read(notification_id):
    if notification_id <= 0:
        return jsonify(error="notification_id must be a positive integer"), 400
    if request.get_data(cache=False):
        return jsonify(error="This endpoint does not accept a request body"), 400

    conn = None
    try:
        conn = get_connection()
        notification = mark_notification_read(
            conn.cursor(), notification_id, session["user_id"],
        )
        conn.commit()
        return jsonify(notification=notification), 200
    except NotificationError as exc:
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
