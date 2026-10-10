"""Authenticated request marketplace endpoints."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import login_required
from services.matching_service import find_matches
from services.notification_service import (
    notify_matching_item_owners,
    notify_request_offer_owners,
)


requests_bp = Blueprint("requests", __name__)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_MONEY_MAX = Decimal("99999999.99")
_EDITABLE_FIELDS = {
    "category_id", "item_description", "locality_id", "start_datetime",
    "end_datetime", "max_budget",
}
_REQUEST_COLUMNS = """request_id, requester_id, category_id, item_description,
                       locality_id, start_datetime, end_datetime, max_budget,
                       status, created_at"""


def _db_error():
    current_app.logger.exception("Request database operation failed")
    return jsonify(error="Request service unavailable"), 503


def _serialize(row):
    return {
        "request_id": row[0],
        "requester_id": row[1],
        "category_id": row[2],
        "item_description": row[3],
        "locality_id": row[4],
        "start_datetime": row[5].isoformat() if row[5] else None,
        "end_datetime": row[6].isoformat() if row[6] else None,
        "max_budget": str(row[7]),
        "status": row[8],
        "created_at": row[9].isoformat() if row[9] else None,
    }


def _positive_int(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError(f"{field} must be a positive integer")


def _money(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("max_budget must be a non-negative amount with at most two decimal places")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("max_budget must be a non-negative amount with at most two decimal places")
    if (not amount.is_finite() or amount < 0 or amount > _MONEY_MAX
            or amount.as_tuple().exponent < -2):
        raise ValueError("max_budget must be a non-negative amount with at most two decimal places")
    return amount.quantize(Decimal("0.01"))


def _datetime(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO 8601 datetime with a timezone")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError(f"{field} must be an ISO 8601 datetime with a timezone")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    # DATETIME2 stores no offset; the schema documents all timestamps as UTC.
    return parsed.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _validate(data, partial=False):
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required")
    unsupported = set(data) - _EDITABLE_FIELDS
    if unsupported:
        raise ValueError("Request contains unsupported fields")
    if partial and not data:
        raise ValueError("At least one supported field is required")
    required = _EDITABLE_FIELDS if not partial else set()
    if required - set(data):
        raise ValueError("category_id, item_description, locality_id, start_datetime, end_datetime, and max_budget are required")

    result = {}
    if "category_id" in data:
        result["category_id"] = _positive_int(data["category_id"], "category_id")
    if "locality_id" in data:
        result["locality_id"] = _positive_int(data["locality_id"], "locality_id")
    if "item_description" in data:
        description = data["item_description"]
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1000:
            raise ValueError("item_description must be between 1 and 1000 characters")
        result["item_description"] = description.strip()
    for field in ("start_datetime", "end_datetime"):
        if field in data:
            result[field] = _datetime(data[field], field)
    if "max_budget" in data:
        result["max_budget"] = _money(data["max_budget"])
    return result


def _exists(cursor, table, key, value):
    # table/key are fixed internal identifiers; values remain parameterized.
    cursor.execute(f"SELECT 1 FROM dbo.{table} WHERE {key} = ?", value)
    return cursor.fetchone() is not None


def _validate_references(cursor, values):
    if "category_id" in values and not _exists(cursor, "Categories", "category_id", values["category_id"]):
        return "category_id does not identify an existing category"
    if "locality_id" in values and not _exists(cursor, "Localities", "locality_id", values["locality_id"]):
        return "locality_id does not identify an existing locality"
    return None


def _ordered_dates(values):
    return values["start_datetime"] < values["end_datetime"]


@requests_bp.post("")
@login_required
def create_request():
    try:
        values = _validate(request.get_json(silent=True))
        if not _ordered_dates(values):
            raise ValueError("start_datetime must be earlier than end_datetime")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        reference_error = _validate_references(cursor, values)
        if reference_error:
            return jsonify(error=reference_error), 400
        # Treat an exact repeat of an active borrowing need as the same
        # request. HOLDLOCK keeps simultaneous double submissions from
        # creating two rows for the same user and request details.
        cursor.execute(
            f"""SELECT TOP (1) {_REQUEST_COLUMNS}
                FROM dbo.Requests WITH (UPDLOCK, HOLDLOCK)
                WHERE requester_id = ? AND category_id = ?
                  AND item_description = ? AND locality_id = ?
                  AND start_datetime = ? AND end_datetime = ? AND max_budget = ?
                  AND status IN (N'OPEN', N'MATCHED')
                ORDER BY created_at DESC, request_id DESC""",
            session["user_id"], values["category_id"], values["item_description"],
            values["locality_id"], values["start_datetime"], values["end_datetime"],
            values["max_budget"],
        )
        existing = cursor.fetchone()
        if existing:
            conn.rollback()
            return jsonify(request=_serialize(existing), already_exists=True), 200
        cursor.execute(
            """INSERT INTO dbo.Requests
               (requester_id, category_id, item_description, locality_id,
                start_datetime, end_datetime, max_budget)
               OUTPUT INSERTED.request_id
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            session["user_id"], values["category_id"], values["item_description"],
            values["locality_id"], values["start_datetime"], values["end_datetime"],
            values["max_budget"],
        )
        request_id = cursor.fetchone()[0]
        notify_matching_item_owners(cursor, request_id)
        cursor.execute(f"SELECT {_REQUEST_COLUMNS} FROM dbo.Requests WHERE request_id = ?", request_id)
        row = cursor.fetchone()
        conn.commit()
        return jsonify(request=_serialize(row)), 201
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@requests_bp.get("/matching-item/<int:item_id>")
@login_required
def find_matching_request_for_item(item_id):
    """Return the best active request for this user that matches an item.

    Eligible requests use the normal matching rules: different requester and
    item owner, same category and locality, active request, available item,
    rental price within budget, and no overlapping active booking. When more
    than one request qualifies, the soonest upcoming start date wins; ties use
    newest creation time and then the highest request id.
    """
    if item_id <= 0:
        return jsonify(error="item_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT owner_id FROM dbo.Items WHERE item_id = ?",
            item_id,
        )
        item = cursor.fetchone()
        if not item:
            return jsonify(error="Item not found"), 404
        if item[0] == session["user_id"]:
            return jsonify(request_id=None, owns_item=True), 200

        cursor.execute(
            """SELECT TOP (1) r.request_id
               FROM dbo.Items AS i
               INNER JOIN dbo.Users AS item_owner
                   ON item_owner.user_id = i.owner_id
               INNER JOIN dbo.Requests AS r
                   ON r.category_id = i.category_id
                  AND r.locality_id = item_owner.locality_id
               WHERE i.item_id = ?
                 AND r.requester_id = ?
                 AND r.requester_id <> i.owner_id
                 AND r.status IN (N'OPEN', N'MATCHED')
                 AND i.is_available = 1
                 AND item_owner.is_active = 1
                 AND i.rental_price <= r.max_budget
                 AND NOT EXISTS
                     (SELECT 1
                      FROM dbo.Bookings AS b
                      INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
                      WHERE o.item_id = i.item_id
                        AND b.status <> N'CANCELLED'
                        AND b.start_datetime < r.end_datetime
                        AND b.end_datetime > r.start_datetime)
               ORDER BY r.start_datetime, r.created_at DESC, r.request_id DESC""",
            item_id, session["user_id"],
        )
        match = cursor.fetchone()
        return jsonify(request_id=match[0] if match else None, owns_item=False), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@requests_bp.get("")
@login_required
def list_requests():
    """Show open marketplace requests plus the signed-in user's own history."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT {_REQUEST_COLUMNS} FROM dbo.Requests
                WHERE status IN (N'OPEN', N'MATCHED') OR requester_id = ?
                ORDER BY created_at DESC, request_id DESC""",
            session["user_id"],
        )
        return jsonify(requests=[_serialize(row) for row in cursor.fetchall()]), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@requests_bp.get("/<int:request_id>")
@login_required
def get_request(request_id):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_REQUEST_COLUMNS} FROM dbo.Requests WHERE request_id = ?", request_id)
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Request not found"), 404
        if row[1] != session["user_id"] and row[8] not in {"OPEN", "MATCHED"}:
            return jsonify(error="Request not found"), 404
        return jsonify(request=_serialize(row)), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@requests_bp.get("/<int:request_id>/matches")
@login_required
def get_request_matches(request_id):
    """Find eligible items for a request owned by the signed-in user."""
    if request.args:
        return jsonify(error="Matching filters are determined by the saved request"), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT requester_id, status FROM dbo.Requests WHERE request_id = ?",
            request_id,
        )
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Request not found"), 404
        if row[1] not in {"OPEN", "MATCHED"}:
            return jsonify(error="Only open or matched requests can be matched"), 409
        matches = find_matches(cursor, request_id, session["user_id"])
        if row[0] != session["user_id"] and not any(m["is_item_owner"] for m in matches):
            return jsonify(error="You do not own this request"), 403
        if row[0] != session["user_id"]:
            matches = [match for match in matches if match["is_item_owner"]]
        return jsonify(
            request_id=request_id,
            is_requester=row[0] == session["user_id"],
            matches=matches,
        ), 200
    except Exception:
        current_app.logger.exception("Request matching operation failed")
        return jsonify(error="Matching service unavailable"), 503
    finally:
        if conn:
            conn.close()


@requests_bp.patch("/<int:request_id>")
@login_required
def update_request(request_id):
    try:
        values = _validate(request.get_json(silent=True), partial=True)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT {_REQUEST_COLUMNS} FROM dbo.Requests WHERE request_id = ?", request_id)
        existing = cursor.fetchone()
        if not existing:
            return jsonify(error="Request not found"), 404
        if existing[1] != session["user_id"]:
            return jsonify(error="You do not own this request"), 403
        if existing[8] != "OPEN":
            return jsonify(error="Only open requests can be updated"), 409

        merged = {
            "category_id": existing[2],
            "item_description": existing[3],
            "locality_id": existing[4],
            "start_datetime": existing[5],
            "end_datetime": existing[6],
            "max_budget": existing[7],
        }
        merged.update(values)
        if not _ordered_dates(merged):
            return jsonify(error="start_datetime must be earlier than end_datetime"), 400
        reference_error = _validate_references(cursor, values)
        if reference_error:
            return jsonify(error=reference_error), 400

        assignments = [f"{field} = ?" for field in values]
        params = [values[field] for field in values]
        params.append(request_id)
        cursor.execute(
            f"UPDATE dbo.Requests SET {', '.join(assignments)} WHERE request_id = ? AND requester_id = ? AND status = N'OPEN'",
            *params, session["user_id"],
        )
        if getattr(cursor, "rowcount", 1) == 0:
            conn.rollback()
            return jsonify(error="Request state changed; reload and try again"), 409
        cursor.execute(f"SELECT {_REQUEST_COLUMNS} FROM dbo.Requests WHERE request_id = ?", request_id)
        row = cursor.fetchone()
        conn.commit()
        return jsonify(request=_serialize(row)), 200
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@requests_bp.post("/<int:request_id>/cancel")
@login_required
def cancel_request(request_id):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE dbo.Requests SET status = N'CANCELLED'
               OUTPUT INSERTED.request_id, INSERTED.requester_id, INSERTED.category_id,
                      INSERTED.item_description, INSERTED.locality_id,
                      INSERTED.start_datetime, INSERTED.end_datetime, INSERTED.max_budget,
                      INSERTED.status, INSERTED.created_at
               WHERE request_id = ? AND requester_id = ? AND status IN (N'OPEN', N'MATCHED')""",
            request_id, session["user_id"],
        )
        row = cursor.fetchone()
        if not row:
            cursor.execute(f"SELECT {_REQUEST_COLUMNS} FROM dbo.Requests WHERE request_id = ?", request_id)
            existing = cursor.fetchone()
            if not existing:
                return jsonify(error="Request not found"), 404
            if existing[1] != session["user_id"]:
                return jsonify(error="You do not own this request"), 403
            return jsonify(error="Only open or matched requests can be cancelled"), 409
        notify_request_offer_owners(
            cursor, request_id, "REQUEST_CANCELLED",
            f"Request {request_id} was cancelled by its requester.",
        )
        conn.commit()
        return jsonify(request=_serialize(row)), 200
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()
