"""Authenticated review creation and participant-scoped retrieval."""

import re

from flask import Blueprint, current_app, jsonify, request, session

from database.connection import get_connection
from middleware.auth import login_required
from services.review_service import ReviewError, create_review, get_review, list_booking_reviews


reviews_bp = Blueprint("reviews", __name__)
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")


def _db_error():
    current_app.logger.exception("Review database operation failed")
    return jsonify(error="Review service unavailable"), 503


def _positive_int(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError(f"{field} must be a positive integer")


def _validate_review(data):
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required")
    if set(data) - {"rating", "comment"}:
        raise ValueError("Request contains unsupported fields")
    if "rating" not in data:
        raise ValueError("rating is required")
    rating = data["rating"]
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValueError("rating must be an integer from 1 to 5")

    comment = data.get("comment")
    if comment is not None:
        if not isinstance(comment, str) or len(comment) > 1000:
            raise ValueError("comment must be a string of at most 1000 characters")
        comment = comment.strip() or None
    return rating, comment


@reviews_bp.post("/bookings/<int:booking_id>/reviews")
@login_required
def submit_review(booking_id):
    try:
        if booking_id <= 0:
            raise ValueError("booking_id must be a positive integer")
        rating, comment = _validate_review(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    conn = None
    try:
        conn = get_connection()
        review = create_review(
            conn.cursor(), booking_id, session["user_id"], rating,
            comment, request.remote_addr,
        )
        conn.commit()
        return jsonify(review=review), 201
    except ReviewError as exc:
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


@reviews_bp.get("/bookings/<int:booking_id>/reviews")
@login_required
def list_reviews(booking_id):
    if booking_id <= 0:
        return jsonify(error="booking_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        rows = list_booking_reviews(conn.cursor(), booking_id, session["user_id"])
        return jsonify(reviews=rows), 200
    except ReviewError as exc:
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@reviews_bp.get("/<int:review_id>")
@login_required
def review_detail(review_id):
    if review_id <= 0:
        return jsonify(error="review_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        review = get_review(conn.cursor(), review_id, session["user_id"])
        return jsonify(review=review), 200
    except ReviewError as exc:
        return jsonify(error=str(exc)), exc.status_code
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()
