"""Authenticated offer creation and participant-scoped offer APIs."""

from decimal import Decimal, InvalidOperation
import re

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import login_required
from services.booking_service import (
    BookingWorkflowError,
    accept_offer as accept_offer_transaction,
    reject_offer as reject_offer_transaction,
    serialize_booking,
)
from services.notification_service import create_notification


offers_bp = Blueprint("offers", __name__)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")
_MONEY_MAX = Decimal("99999999.99")
_OFFER_FIELDS = {"item_id", "offer_type", "offered_price", "security_deposit", "message"}
_OFFER_SELECT = """offer_id, request_id, item_id, owner_id, offer_type,
                    offered_price, security_deposit, message, status, created_at"""
_OFFER_SELECT_QUALIFIED = """o.offer_id, o.request_id, o.item_id, o.owner_id,
                              o.offer_type, o.offered_price, o.security_deposit,
                              o.message, o.status, o.created_at"""


def _db_error():
    current_app.logger.exception("Offer database operation failed")
    return jsonify(error="Offer service unavailable"), 503


def _serialize(row):
    return {
        "offer_id": row[0],
        "request_id": row[1],
        "item_id": row[2],
        "offer_type": row[4],
        "offered_price": str(row[5]),
        "security_deposit": str(row[6]),
        "message": row[7],
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


def _money(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    if (not amount.is_finite() or amount < 0 or amount > _MONEY_MAX
            or amount.as_tuple().exponent < -2):
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    return amount.quantize(Decimal("0.01"))


def _validate_offer(data):
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required")
    if set(data) - _OFFER_FIELDS:
        raise ValueError("Request contains unsupported fields")
    required = {"item_id", "offer_type", "offered_price"}
    if required - set(data):
        raise ValueError("item_id, offer_type, and offered_price are required")

    item_id = _positive_int(data["item_id"], "item_id")
    offer_type = data["offer_type"]
    if not isinstance(offer_type, str) or offer_type.strip().upper() not in {"FREE_LENDING", "RENTAL"}:
        raise ValueError("offer_type must be FREE_LENDING or RENTAL")
    offer_type = offer_type.strip().upper()
    offered_price = _money(data["offered_price"], "offered_price")
    security_deposit = _money(data.get("security_deposit", 0), "security_deposit")
    if offer_type == "FREE_LENDING" and offered_price != 0:
        raise ValueError("FREE_LENDING offers must have an offered_price of 0")

    message = data.get("message")
    if message is not None:
        if not isinstance(message, str) or len(message.strip()) > 1000:
            raise ValueError("message must be at most 1000 characters")
        message = message.strip() or None
    return {
        "item_id": item_id,
        "offer_type": offer_type,
        "offered_price": offered_price,
        "security_deposit": security_deposit,
        "message": message,
    }


def _rollback_response(conn, error, status):
    conn.rollback()
    return jsonify(error=error), status


@offers_bp.post("/requests/<int:request_id>/offers")
@login_required
def create_offer(request_id):
    try:
        if request_id <= 0:
            raise ValueError("request_id must be a positive integer")
        values = _validate_offer(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT requester_id, category_id, locality_id, max_budget, status,
                      start_datetime, end_datetime
               FROM dbo.Requests WITH (UPDLOCK, HOLDLOCK)
               WHERE request_id = ?""",
            request_id,
        )
        target_request = cursor.fetchone()
        if not target_request:
            return _rollback_response(conn, "Request not found", 404)
        if target_request[4] not in {"OPEN", "MATCHED"}:
            return _rollback_response(conn, "Request is not eligible for offers", 409)

        cursor.execute(
            """SELECT i.owner_id, i.category_id, i.is_available, i.rental_price,
                      u.locality_id, u.is_active
               FROM dbo.Items AS i WITH (UPDLOCK, HOLDLOCK)
               INNER JOIN dbo.Users AS u WITH (UPDLOCK, HOLDLOCK)
                   ON u.user_id = i.owner_id
               WHERE i.item_id = ?""",
            values["item_id"],
        )
        item = cursor.fetchone()
        if not item:
            return _rollback_response(conn, "Item not found", 404)
        if item[0] != session["user_id"]:
            return _rollback_response(conn, "Only the item owner can make an offer", 403)
        if item[0] == target_request[0]:
            return _rollback_response(conn, "Request and item are not a valid match", 409)
        if not item[5]:
            return _rollback_response(conn, "Item owner is inactive", 409)
        if item[1] != target_request[1] or item[4] != target_request[2]:
            return _rollback_response(conn, "Item category and locality must match the request", 409)
        if not item[2]:
            return _rollback_response(conn, "Item is unavailable", 409)
        if item[3] > target_request[3] or values["offered_price"] > target_request[3]:
            return _rollback_response(conn, "Item and offer price must fit the request budget", 409)

        cursor.execute(
            """SELECT 1
               FROM dbo.Bookings AS b WITH (UPDLOCK, HOLDLOCK)
               INNER JOIN dbo.Offers AS booked_offer WITH (UPDLOCK, HOLDLOCK)
                   ON booked_offer.offer_id = b.offer_id
               WHERE booked_offer.item_id = ?
                 AND b.status <> N'CANCELLED'
                 AND b.start_datetime < ?
                 AND b.end_datetime > ?""",
            values["item_id"], target_request[6], target_request[5],
        )
        if cursor.fetchone():
            return _rollback_response(conn, "Item has a booking that overlaps the request dates", 409)

        # HOLDLOCK protects the request/item key range during the insert. The
        # schema has no unique (request_id, item_id) constraint, so this also
        # serializes concurrent duplicate checks for the same pair.
        cursor.execute(
            """SELECT 1 FROM dbo.Offers WITH (UPDLOCK, HOLDLOCK)
               WHERE request_id = ? AND item_id = ?
                 AND status IN (N'PENDING', N'ACCEPTED')""",
            request_id, values["item_id"],
        )
        if cursor.fetchone():
            return _rollback_response(conn, "An active offer already exists for this request and item", 409)

        cursor.execute(
            """INSERT INTO dbo.Offers
                   (request_id, item_id, owner_id, offer_type, offered_price,
                    security_deposit, message)
               OUTPUT INSERTED.offer_id, INSERTED.request_id, INSERTED.item_id,
                      INSERTED.owner_id, INSERTED.offer_type, INSERTED.offered_price,
                      INSERTED.security_deposit, INSERTED.message, INSERTED.status,
                      INSERTED.created_at
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            request_id, values["item_id"], session["user_id"], values["offer_type"],
            values["offered_price"], values["security_deposit"], values["message"],
        )
        row = cursor.fetchone()
        create_notification(
            cursor, target_request[0], request_id,
            f"A new offer ({row[0]}) was submitted on your request.",
            "OFFER_RECEIVED",
        )
        conn.commit()
        return jsonify(offer=_serialize(row)), 201
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@offers_bp.get("/offers")
@login_required
def list_my_offers():
    """List only offers created by the signed-in user."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT {_OFFER_SELECT} FROM dbo.Offers
                WHERE owner_id = ? ORDER BY created_at DESC, offer_id DESC""",
            session["user_id"],
        )
        return jsonify(offers=[_serialize(row) for row in cursor.fetchall()]), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@offers_bp.get("/requests/<int:request_id>/offers")
@login_required
def list_request_offers(request_id):
    """Allow the requester to see all offers and each creator to see theirs."""
    if request_id <= 0:
        return jsonify(error="request_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT requester_id FROM dbo.Requests WHERE request_id = ?", request_id)
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Request not found"), 404
        if row[0] == session["user_id"]:
            cursor.execute(
                f"""SELECT {_OFFER_SELECT} FROM dbo.Offers
                    WHERE request_id = ? ORDER BY created_at DESC, offer_id DESC""",
                request_id,
            )
        else:
            cursor.execute(
                f"""SELECT {_OFFER_SELECT} FROM dbo.Offers
                    WHERE request_id = ? AND owner_id = ?
                    ORDER BY created_at DESC, offer_id DESC""",
                request_id, session["user_id"],
            )
            own_offers = cursor.fetchall()
            if not own_offers:
                return jsonify(error="You are not a participant in this request's offers"), 403
            return jsonify(offers=[_serialize(offer) for offer in own_offers]), 200
        return jsonify(offers=[_serialize(offer) for offer in cursor.fetchall()]), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@offers_bp.get("/offers/<int:offer_id>")
@login_required
def get_offer(offer_id):
    if offer_id <= 0:
        return jsonify(error="offer_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT {_OFFER_SELECT_QUALIFIED}, r.requester_id
                FROM dbo.Offers AS o
                INNER JOIN dbo.Requests AS r ON r.request_id = o.request_id
                WHERE o.offer_id = ?""",
            offer_id,
        )
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Offer not found"), 404
        if session["user_id"] not in {row[3], row[10]}:
            return jsonify(error="You are not a participant in this offer"), 403
        return jsonify(offer=_serialize(row[:10])), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@offers_bp.post("/offers/<int:offer_id>/withdraw")
@login_required
def withdraw_offer(offer_id):
    if offer_id <= 0:
        return jsonify(error="offer_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT o.owner_id, o.status,
                      CASE WHEN EXISTS
                          (SELECT 1 FROM dbo.Bookings AS b WITH (UPDLOCK, HOLDLOCK)
                           WHERE b.offer_id = o.offer_id) THEN 1 ELSE 0 END
               FROM dbo.Offers AS o WITH (UPDLOCK, HOLDLOCK)
               WHERE o.offer_id = ?""",
            offer_id,
        )
        row = cursor.fetchone()
        if not row:
            return _rollback_response(conn, "Offer not found", 404)
        if row[0] != session["user_id"]:
            return _rollback_response(conn, "You do not own this offer", 403)
        if row[1] != "PENDING" or row[2]:
            return _rollback_response(conn, "Only unbooked pending offers can be withdrawn", 409)
        cursor.execute(
            """UPDATE dbo.Offers SET status = N'WITHDRAWN'
               OUTPUT INSERTED.offer_id, INSERTED.request_id, INSERTED.item_id,
                      INSERTED.owner_id, INSERTED.offer_type, INSERTED.offered_price,
                      INSERTED.security_deposit, INSERTED.message, INSERTED.status,
                      INSERTED.created_at
               WHERE offer_id = ? AND owner_id = ? AND status = N'PENDING'
                 AND NOT EXISTS (SELECT 1 FROM dbo.Bookings WHERE offer_id = ?)""",
            offer_id, session["user_id"], offer_id,
        )
        updated = cursor.fetchone()
        if not updated:
            return _rollback_response(conn, "Offer state changed; reload and try again", 409)
        cursor.execute(
            "SELECT requester_id FROM dbo.Requests WHERE request_id = ?",
            updated[1],
        )
        requester = cursor.fetchone()
        if requester:
            create_notification(
                cursor, requester[0], updated[1],
                f"Offer {updated[0]} on your request was withdrawn.",
                "OFFER_WITHDRAWN",
            )
        conn.commit()
        return jsonify(offer=_serialize(updated)), 200
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


def _decide_offer(offer_id, decision):
    if offer_id <= 0:
        return jsonify(error="offer_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if decision == "accept":
            result = accept_offer_transaction(
                cursor, offer_id, session["user_id"], request.remote_addr,
            )
            conn.commit()
            return jsonify(
                offer=_serialize(result["offer"]),
                booking=serialize_booking(result["booking"]),
            ), 200

        offer = reject_offer_transaction(
            cursor, offer_id, session["user_id"], request.remote_addr,
        )
        conn.commit()
        return jsonify(offer=_serialize(offer)), 200
    except BookingWorkflowError as exc:
        if conn:
            conn.rollback()
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("Offer decision database operation failed")
        return jsonify(error="Offer decision service unavailable"), 503
    finally:
        if conn:
            conn.close()


@offers_bp.post("/offers/<int:offer_id>/accept")
@login_required
def accept_offer(offer_id):
    return _decide_offer(offer_id, "accept")


@offers_bp.post("/offers/<int:offer_id>/reject")
@login_required
def reject_offer(offer_id):
    return _decide_offer(offer_id, "reject")
