"""Transactional offer and booking lifecycle with participant-scoped queries."""

from decimal import Decimal, InvalidOperation

from services.notification_service import (
    create_notification,
    notify_pending_offer_creators,
)


class BookingWorkflowError(Exception):
    """Expected business-rule failure with an HTTP response status."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def _get_decision_rows(cursor, offer_id):
    cursor.execute(
        "SELECT request_id FROM dbo.Offers WHERE offer_id = ?",
        offer_id,
    )
    offer_ref = cursor.fetchone()
    if not offer_ref:
        raise BookingWorkflowError("Offer not found", 404)

    request_id = offer_ref[0]
    cursor.execute(
        """SELECT requester_id, category_id, locality_id, max_budget, status,
                  start_datetime, end_datetime
           FROM dbo.Requests WITH (UPDLOCK, HOLDLOCK)
           WHERE request_id = ?""",
        request_id,
    )
    request_row = cursor.fetchone()
    if not request_row:
        raise BookingWorkflowError("Request not found", 404)

    cursor.execute(
        """SELECT request_id, item_id, owner_id, offer_type, offered_price,
                  security_deposit, status
           FROM dbo.Offers WITH (UPDLOCK, HOLDLOCK)
           WHERE offer_id = ?""",
        offer_id,
    )
    offer_row = cursor.fetchone()
    if not offer_row or offer_row[0] != request_id:
        raise BookingWorkflowError("Offer not found", 404)
    return request_id, request_row, offer_row


def _validate_decision_actor(actor_id, request_row, offer_row):
    if request_row[0] != actor_id:
        raise BookingWorkflowError("Only the request owner can decide this offer", 403)
    if request_row[4] not in {"OPEN", "MATCHED"}:
        raise BookingWorkflowError("Request is not eligible for offer decisions", 409)
    if offer_row[6] != "PENDING":
        raise BookingWorkflowError("Only pending offers can be decided", 409)


def _decimal_value(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def _validate_booking_values(request_row, offer_row, item_row):
    start_datetime, end_datetime = request_row[5], request_row[6]
    if not start_datetime or not end_datetime or start_datetime >= end_datetime:
        raise BookingWorkflowError("Request dates are invalid for booking", 409)

    max_budget = _decimal_value(request_row[3])
    item_price = _decimal_value(item_row[3])
    agreed_price = _decimal_value(offer_row[4])
    security_deposit = _decimal_value(offer_row[5])
    if (max_budget is None or item_price is None or agreed_price is None
            or security_deposit is None or min(max_budget, item_price, agreed_price, security_deposit) < 0):
        raise BookingWorkflowError("Stored prices are invalid for booking", 409)
    if item_price > max_budget or agreed_price > max_budget:
        raise BookingWorkflowError("Item and offer price must fit the request budget", 409)
    if offer_row[3] not in {"FREE_LENDING", "RENTAL"}:
        raise BookingWorkflowError("Offer type is invalid for booking", 409)
    if offer_row[3] == "FREE_LENDING" and agreed_price != 0:
        raise BookingWorkflowError("FREE_LENDING offers must have a price of 0", 409)


def _validate_item_for_booking(cursor, request_row, offer_row, actor_id):
    cursor.execute(
        """SELECT i.owner_id, i.category_id, i.is_available, i.rental_price,
                  u.locality_id, u.is_active
           FROM dbo.Items AS i WITH (UPDLOCK, HOLDLOCK)
           INNER JOIN dbo.Users AS u WITH (UPDLOCK, HOLDLOCK)
               ON u.user_id = i.owner_id
           WHERE i.item_id = ?""",
        offer_row[1],
    )
    item_row = cursor.fetchone()
    if not item_row:
        raise BookingWorkflowError("Offered item no longer exists", 409)
    if item_row[0] != offer_row[2] or item_row[0] == actor_id:
        raise BookingWorkflowError("Offered item ownership is no longer valid", 409)
    if not item_row[5]:
        raise BookingWorkflowError("Item owner is inactive", 409)
    if not item_row[2]:
        raise BookingWorkflowError("Offered item is no longer available", 409)
    if item_row[1] != request_row[1] or item_row[4] != request_row[2]:
        raise BookingWorkflowError("Offered item no longer matches request category and locality", 409)
    _validate_booking_values(request_row, offer_row, item_row)
    return item_row


def _ensure_no_booking_conflict(cursor, item_id, start_datetime, end_datetime):
    cursor.execute(
        """SELECT 1
           FROM dbo.Bookings AS b WITH (UPDLOCK, HOLDLOCK)
           INNER JOIN dbo.Offers AS existing_offer WITH (UPDLOCK, HOLDLOCK)
               ON existing_offer.offer_id = b.offer_id
           WHERE existing_offer.item_id = ?
             AND b.status <> N'CANCELLED'
             AND b.start_datetime < ?
             AND b.end_datetime > ?""",
        item_id, end_datetime, start_datetime,
    )
    if cursor.fetchone():
        raise BookingWorkflowError("Item has an overlapping active booking", 409)


def _notify(cursor, user_id, request_id, message, notification_type):
    create_notification(cursor, user_id, request_id, message, notification_type)


def _audit(cursor, user_id, action, entity_id, ip_address):
    cursor.execute(
        """INSERT INTO dbo.AuditLogs (user_id, action, entity_type, entity_id, ip_address)
           VALUES (?, ?, N'Offer', ?, ?)""",
        user_id, action, str(entity_id), ip_address,
    )


def _updated_offer(cursor, offer_id, request_id, status):
    cursor.execute(
        """UPDATE dbo.Offers SET status = ?
            OUTPUT INSERTED.offer_id, INSERTED.request_id, INSERTED.item_id,
                   INSERTED.owner_id, INSERTED.offer_type, INSERTED.offered_price,
                   INSERTED.security_deposit, INSERTED.message, INSERTED.status,
                   INSERTED.created_at
            WHERE offer_id = ? AND request_id = ? AND status = N'PENDING'""",
        status, offer_id, request_id,
    )
    row = cursor.fetchone()
    if not row:
        raise BookingWorkflowError("Offer state changed; reload and try again", 409)
    return row


def _booking_by_id(cursor, booking_id):
    cursor.execute(
        """SELECT b.booking_id, b.offer_id, b.borrower_id, o.owner_id,
                  r.request_id, i.item_id, i.item_name,
                  b.start_datetime, b.end_datetime, b.agreed_price,
                  b.security_deposit, b.status, b.created_at
           FROM dbo.Bookings AS b
           INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
           INNER JOIN dbo.Requests AS r ON r.request_id = o.request_id
           INNER JOIN dbo.Items AS i ON i.item_id = o.item_id
           WHERE b.booking_id = ?""",
        booking_id,
    )
    return cursor.fetchone()


def accept_offer(cursor, offer_id, actor_id, ip_address=None):
    """Accept a pending offer and create its booking in the caller's transaction."""
    request_id, request_row, offer_row = _get_decision_rows(cursor, offer_id)
    _validate_decision_actor(actor_id, request_row, offer_row)
    _validate_item_for_booking(cursor, request_row, offer_row, actor_id)

    cursor.execute(
        """SELECT 1 FROM dbo.Offers WITH (UPDLOCK, HOLDLOCK)
           WHERE request_id = ? AND offer_id <> ? AND status = N'ACCEPTED'""",
        request_id, offer_id,
    )
    if cursor.fetchone():
        raise BookingWorkflowError("Another offer has already been accepted for this request", 409)
    _ensure_no_booking_conflict(cursor, offer_row[1], request_row[5], request_row[6])

    accepted_offer = _updated_offer(cursor, offer_id, request_id, "ACCEPTED")
    cursor.execute(
        """INSERT INTO dbo.Bookings
               (offer_id, borrower_id, start_datetime, end_datetime,
                agreed_price, security_deposit)
           OUTPUT INSERTED.booking_id
           VALUES (?, ?, ?, ?, ?, ?)""",
        offer_id, request_row[0], request_row[5], request_row[6],
        offer_row[4], offer_row[5],
    )
    booking_id_row = cursor.fetchone()
    if not booking_id_row:
        raise BookingWorkflowError("Booking could not be created", 503)

    notify_pending_offer_creators(
        cursor, request_id, offer_id,
        f"Another offer on request {request_id} was accepted.", "OFFER_REJECTED",
    )
    cursor.execute(
        """UPDATE dbo.Offers SET status = N'REJECTED'
           WHERE request_id = ? AND offer_id <> ? AND status = N'PENDING'""",
        request_id, offer_id,
    )
    cursor.execute(
        """UPDATE dbo.Requests SET status = N'BOOKED'
           WHERE request_id = ? AND requester_id = ? AND status IN (N'OPEN', N'MATCHED')""",
        request_id, actor_id,
    )
    if cursor.rowcount == 0:
        raise BookingWorkflowError("Request state changed; reload and try again", 409)

    _notify(
        cursor, accepted_offer[3], request_id,
        f"Your offer {offer_id} on request {request_id} was accepted.", "OFFER_ACCEPTED",
    )
    _audit(cursor, actor_id, "OFFER_ACCEPTED", offer_id, ip_address)

    booking = _booking_by_id(cursor, booking_id_row[0])
    if not booking:
        raise BookingWorkflowError("Booking could not be retrieved", 503)
    return {"offer": accepted_offer, "booking": booking}


def reject_offer(cursor, offer_id, actor_id, ip_address=None):
    """Reject a pending offer without changing sibling offers or bookings."""
    request_id, request_row, _offer_row = _get_decision_rows(cursor, offer_id)
    if request_row[0] != actor_id:
        raise BookingWorkflowError("Only the request owner can decide this offer", 403)
    if request_row[4] not in {"OPEN", "MATCHED"}:
        raise BookingWorkflowError("Request is not eligible for offer decisions", 409)
    offer_row = _offer_row
    if offer_row[6] != "PENDING":
        raise BookingWorkflowError("Only pending offers can be decided", 409)

    rejected_offer = _updated_offer(cursor, offer_id, request_id, "REJECTED")
    _notify(
        cursor, rejected_offer[3], request_id,
        f"Your offer {offer_id} on request {request_id} was rejected.", "OFFER_REJECTED",
    )
    _audit(cursor, actor_id, "OFFER_REJECTED", offer_id, ip_address)
    return rejected_offer


_BOOKING_TRANSITIONS = {
    "handover": ("BOOKED", "HANDED_OVER", "owner", "BOOKING_HANDED_OVER"),
    "return": ("HANDED_OVER", "RETURNED", "borrower", "BOOKING_RETURNED"),
    "complete": ("RETURNED", "COMPLETED", "owner", "BOOKING_COMPLETED"),
}


def transition_booking(cursor, booking_id, actor_id, transition, ip_address=None):
    """Apply one role-authorized status transition in the caller's transaction.

    The owner confirms handover, the borrower confirms return, and the owner
    confirms completion. Completion also marks the linked request COMPLETED;
    all status and event writes remain in the caller's transaction.
    """
    rule = _BOOKING_TRANSITIONS.get(transition)
    if not rule:
        raise ValueError("Unsupported booking transition")
    expected_status, target_status, permitted_party, action = rule

    cursor.execute(
        """SELECT b.booking_id, b.offer_id, b.borrower_id, o.owner_id,
                  r.request_id, b.status, r.status
           FROM dbo.Bookings AS b WITH (UPDLOCK, HOLDLOCK)
           INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
           INNER JOIN dbo.Requests AS r ON r.request_id = o.request_id
           WHERE b.booking_id = ?""",
        booking_id,
    )
    row = cursor.fetchone()
    if not row:
        raise BookingWorkflowError("Booking not found", 404)

    borrower_id, owner_id = row[2], row[3]
    permitted_user = owner_id if permitted_party == "owner" else borrower_id
    if actor_id != permitted_user:
        raise BookingWorkflowError(
            f"Only the booking {permitted_party} can perform this action", 403,
        )
    if row[5] != expected_status:
        raise BookingWorkflowError(
            f"Booking must be {expected_status} before it can become {target_status}", 409,
        )
    if row[6] != "BOOKED":
        raise BookingWorkflowError("The linked request is not in BOOKED state", 409)

    cursor.execute(
        """UPDATE dbo.Bookings SET status = ?
           OUTPUT INSERTED.booking_id
           WHERE booking_id = ? AND status = ?""",
        target_status, booking_id, expected_status,
    )
    updated = cursor.fetchone()
    if not updated:
        raise BookingWorkflowError("Booking state changed; reload and try again", 409)

    if transition == "complete":
        cursor.execute(
            """UPDATE dbo.Requests SET status = N'COMPLETED'
               OUTPUT INSERTED.request_id
               WHERE request_id = ? AND status = N'BOOKED'""",
            row[4],
        )
        completed_request = cursor.fetchone()
        if not completed_request:
            raise BookingWorkflowError(
                "The linked request state changed; reload and try again", 409,
            )

    recipient_id = borrower_id if permitted_party == "owner" else owner_id
    request_id = row[4]
    _notify(
        cursor, recipient_id, request_id,
        f"Booking {booking_id} status changed to {target_status}.", action,
    )
    cursor.execute(
        """INSERT INTO dbo.AuditLogs (user_id, action, entity_type, entity_id, ip_address)
           VALUES (?, ?, N'Booking', ?, ?)""",
        actor_id, action, str(booking_id), ip_address,
    )

    booking = _booking_by_id(cursor, booking_id)
    if not booking:
        raise BookingWorkflowError("Booking could not be retrieved", 503)
    return booking


def list_bookings(cursor, user_id):
    cursor.execute(
        """SELECT b.booking_id, b.offer_id, b.borrower_id, o.owner_id,
                  r.request_id, i.item_id, i.item_name,
                  b.start_datetime, b.end_datetime, b.agreed_price,
                  b.security_deposit, b.status, b.created_at
           FROM dbo.Bookings AS b
           INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
           INNER JOIN dbo.Requests AS r ON r.request_id = o.request_id
           INNER JOIN dbo.Items AS i ON i.item_id = o.item_id
           WHERE b.borrower_id = ? OR o.owner_id = ?
           ORDER BY b.created_at DESC, b.booking_id DESC""",
        user_id, user_id,
    )
    return cursor.fetchall()


def get_booking(cursor, booking_id):
    return _booking_by_id(cursor, booking_id)


def serialize_booking(row):
    return {
        "booking_id": row[0],
        "offer_id": row[1],
        "request_id": row[4],
        "item_id": row[5],
        "item_name": row[6],
        "start_datetime": row[7].isoformat() if row[7] else None,
        "end_datetime": row[8].isoformat() if row[8] else None,
        "agreed_price": str(row[9]),
        "security_deposit": str(row[10]),
        "status": row[11],
        "created_at": row[12].isoformat() if row[12] else None,
    }
