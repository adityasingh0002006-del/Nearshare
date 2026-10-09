"""Participant-scoped booking retrieval endpoints."""

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import login_required
from services.booking_service import (
    BookingWorkflowError,
    get_booking,
    list_bookings,
    serialize_booking,
    transition_booking,
)


bookings_bp = Blueprint("bookings", __name__)


def _db_error():
    current_app.logger.exception("Booking database operation failed")
    return jsonify(error="Booking service unavailable"), 503


@bookings_bp.get("")
@login_required
def list_my_bookings():
    conn = None
    try:
        conn = get_connection()
        rows = list_bookings(conn.cursor(), session["user_id"])
        return jsonify(bookings=[serialize_booking(row) for row in rows]), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@bookings_bp.get("/<int:booking_id>")
@login_required
def get_booking_detail(booking_id):
    if booking_id <= 0:
        return jsonify(error="booking_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        row = get_booking(conn.cursor(), booking_id)
        if not row:
            return jsonify(error="Booking not found"), 404
        if session["user_id"] not in {row[2], row[3]}:
            return jsonify(error="You are not a participant in this booking"), 403
        return jsonify(booking=serialize_booking(row)), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


def _transition(booking_id, transition_name):
    if booking_id <= 0:
        return jsonify(error="booking_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        booking = transition_booking(
            conn.cursor(), booking_id, session["user_id"],
            transition_name, request.remote_addr,
        )
        conn.commit()
        return jsonify(booking=serialize_booking(booking)), 200
    except BookingWorkflowError as exc:
        if conn:
            conn.rollback()
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("Booking state transition failed")
        return jsonify(error="Booking service unavailable"), 503
    finally:
        if conn:
            conn.close()


@bookings_bp.post("/<int:booking_id>/handover")
@login_required
def handover_booking(booking_id):
    return _transition(booking_id, "handover")


@bookings_bp.post("/<int:booking_id>/return")
@login_required
def return_booking(booking_id):
    return _transition(booking_id, "return")


@bookings_bp.post("/<int:booking_id>/complete")
@login_required
def complete_booking(booking_id):
    return _transition(booking_id, "complete")
