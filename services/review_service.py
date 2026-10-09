"""Review eligibility, persistence, and participant-scoped queries."""

from services.notification_service import create_notification


class ReviewError(Exception):
    """Expected review business-rule failure with an HTTP status."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _serialize(row):
    return {
        "review_id": row[0],
        "booking_id": row[1],
        "rating": row[2],
        "comment": row[3],
        "created_at": row[4].isoformat() if row[4] else None,
    }


def _booking_participants(cursor, booking_id, lock=False):
    lock_clause = " WITH (UPDLOCK, HOLDLOCK)" if lock else ""
    cursor.execute(
        f"""SELECT b.borrower_id, o.owner_id, b.status, r.request_id
            FROM dbo.Bookings AS b{lock_clause}
            INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
            INNER JOIN dbo.Requests AS r ON r.request_id = o.request_id
            WHERE b.booking_id = ?""",
        booking_id,
    )
    row = cursor.fetchone()
    if not row:
        raise ReviewError("Booking not found", 404)
    return row


def _ensure_participant(actor_id, borrower_id, owner_id):
    if actor_id not in {borrower_id, owner_id}:
        raise ReviewError("You are not a participant in this booking", 403)


def create_review(cursor, booking_id, reviewer_id, rating, comment, ip_address=None):
    """Create one direction of a review for a completed booking atomically."""
    borrower_id, owner_id, status, request_id = _booking_participants(
        cursor, booking_id, lock=True,
    )
    _ensure_participant(reviewer_id, borrower_id, owner_id)
    if status != "COMPLETED":
        raise ReviewError("Only completed bookings can be reviewed", 409)
    if borrower_id == owner_id:
        raise ReviewError("A booking cannot be reviewed by its own participant", 409)

    reviewee_id = owner_id if reviewer_id == borrower_id else borrower_id
    if reviewer_id == reviewee_id:
        raise ReviewError("You cannot review yourself", 400)

    # The unique schema key is (booking_id, reviewer_id). Lock this key range
    # so concurrent duplicate submissions fail as a business conflict.
    cursor.execute(
        """SELECT review_id FROM dbo.Reviews WITH (UPDLOCK, HOLDLOCK)
           WHERE booking_id = ? AND reviewer_id = ?""",
        booking_id, reviewer_id,
    )
    if cursor.fetchone():
        raise ReviewError("You have already reviewed this booking", 409)

    cursor.execute(
        """INSERT INTO dbo.Reviews (booking_id, reviewer_id, reviewee_id, rating, comment)
           OUTPUT INSERTED.review_id, INSERTED.booking_id, INSERTED.rating,
                  INSERTED.comment, INSERTED.created_at
           VALUES (?, ?, ?, ?, ?)""",
        booking_id, reviewer_id, reviewee_id, rating, comment,
    )
    row = cursor.fetchone()
    if not row:
        raise ReviewError("Review could not be created", 503)

    create_notification(
        cursor, reviewee_id, request_id,
        f"You received review {row[0]} for a completed booking.", "REVIEW_RECEIVED",
    )
    cursor.execute(
        """INSERT INTO dbo.AuditLogs (user_id, action, entity_type, entity_id, ip_address)
           VALUES (?, N'REVIEW_CREATED', N'Review', ?, ?)""",
        reviewer_id, str(row[0]), ip_address,
    )
    return _serialize(row)


def list_booking_reviews(cursor, booking_id, actor_id):
    borrower_id, owner_id, _status, _request_id = _booking_participants(cursor, booking_id)
    _ensure_participant(actor_id, borrower_id, owner_id)
    cursor.execute(
        """SELECT review_id, booking_id, rating, comment, created_at
           FROM dbo.Reviews
           WHERE booking_id = ?
           ORDER BY created_at, review_id""",
        booking_id,
    )
    return [_serialize(row) for row in cursor.fetchall()]


def get_review(cursor, review_id, actor_id):
    cursor.execute(
        """SELECT rv.review_id, rv.booking_id, rv.rating, rv.comment, rv.created_at,
                  b.borrower_id, o.owner_id
           FROM dbo.Reviews AS rv
           INNER JOIN dbo.Bookings AS b ON b.booking_id = rv.booking_id
           INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
           WHERE rv.review_id = ?""",
        review_id,
    )
    row = cursor.fetchone()
    if not row:
        raise ReviewError("Review not found", 404)
    _ensure_participant(actor_id, row[5], row[6])
    return _serialize(row[:5])
