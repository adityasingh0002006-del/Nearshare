"""Schema-backed notification creation and private inbox queries."""

from services.matching_service import MATCH_ELIGIBILITY_SQL


class NotificationError(Exception):
    """Expected notification request failure with an HTTP status."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def create_notification(cursor, user_id, request_id, message, notification_type):
    """Insert a deterministic notification once in the caller's transaction.

    The current schema has no source-event key or uniqueness constraint. A
    locked lookup on the complete event identity makes repeat calls idempotent
    while the workflow transaction remains open.
    """
    cursor.execute(
        """SELECT notification_id
           FROM dbo.Notifications WITH (UPDLOCK, HOLDLOCK)
           WHERE user_id = ? AND request_id = ?
             AND notification_type = ? AND message = ?""",
        user_id, request_id, notification_type, message,
    )
    if cursor.fetchone():
        return False
    cursor.execute(
        """INSERT INTO dbo.Notifications (user_id, request_id, message, notification_type)
           VALUES (?, ?, ?, ?)""",
        user_id, request_id, message, notification_type,
    )
    return True


def notify_matching_item_owners(cursor, request_id):
    """Notify each distinct owner with an eligible item for this request."""
    message = "New request matching your item."
    notification_type = "MATCHING_REQUEST"
    cursor.execute(
        """INSERT INTO dbo.Notifications (user_id, request_id, message, notification_type)
           SELECT DISTINCT i.owner_id, r.request_id, ?, ?
           FROM dbo.Requests AS r
           INNER JOIN dbo.Items AS i ON i.category_id = r.category_id
           INNER JOIN dbo.Categories AS c ON c.category_id = r.category_id
           INNER JOIN dbo.Users AS item_owner ON item_owner.user_id = i.owner_id
           INNER JOIN dbo.Localities AS item_locality
               ON item_locality.locality_id = item_owner.locality_id
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           WHERE r.request_id = ?
             AND """ + MATCH_ELIGIBILITY_SQL + """
             AND NOT EXISTS
                 (SELECT 1 FROM dbo.Notifications AS n WITH (UPDLOCK, HOLDLOCK)
                  WHERE n.user_id = i.owner_id AND n.request_id = r.request_id
                    AND n.notification_type = ?)""",
        message, notification_type, request_id, notification_type,
    )


def notify_request_offer_owners(cursor, request_id, notification_type, message):
    """Notify users with live offers when their request changes state."""
    cursor.execute(
        """SELECT DISTINCT owner_id
           FROM dbo.Offers
           WHERE request_id = ? AND status IN (N'PENDING', N'ACCEPTED')""",
        request_id,
    )
    for row in cursor.fetchall():
        create_notification(cursor, row[0], request_id, message, notification_type)


def notify_pending_offer_creators(cursor, request_id, excluded_offer_id, message, notification_type):
    """Notify each distinct creator whose pending offer is being superseded."""
    cursor.execute(
        """SELECT DISTINCT owner_id
           FROM dbo.Offers WITH (UPDLOCK, HOLDLOCK)
           WHERE request_id = ? AND offer_id <> ? AND status = N'PENDING'""",
        request_id, excluded_offer_id,
    )
    for row in cursor.fetchall():
        create_notification(cursor, row[0], request_id, message, notification_type)


def _notification(row):
    return {
        "notification_id": row[0],
        "request_id": row[1],
        "message": row[2],
        "notification_type": row[3],
        "is_read": bool(row[4]),
        "created_at": row[5].isoformat() if row[5] else None,
    }


def list_notifications(cursor, user_id, page, per_page):
    offset = (page - 1) * per_page
    cursor.execute(
        """SELECT notification_id, request_id, message, notification_type,
                  is_read, created_at
           FROM dbo.Notifications
           WHERE user_id = ?
           ORDER BY created_at DESC, notification_id DESC
           OFFSET ? ROWS FETCH NEXT ? ROWS ONLY""",
        user_id, offset, per_page,
    )
    return [_notification(row) for row in cursor.fetchall()]


def unread_count(cursor, user_id):
    cursor.execute(
        """SELECT COUNT(*) FROM dbo.Notifications
           WHERE user_id = ? AND is_read = 0""",
        user_id,
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def mark_notification_read(cursor, notification_id, user_id):
    cursor.execute(
        """UPDATE dbo.Notifications SET is_read = 1
           OUTPUT INSERTED.notification_id, INSERTED.request_id,
                  INSERTED.message, INSERTED.notification_type,
                  INSERTED.is_read, INSERTED.created_at
           WHERE notification_id = ? AND user_id = ? AND is_read = 0""",
        notification_id, user_id,
    )
    row = cursor.fetchone()
    if row:
        return _notification(row)

    # Treat a repeated read by the owner as success, while not disclosing
    # whether a notification belonging to another user exists.
    cursor.execute(
        """SELECT notification_id, request_id, message, notification_type,
                  is_read, created_at
           FROM dbo.Notifications
           WHERE notification_id = ? AND user_id = ?""",
        notification_id, user_id,
    )
    row = cursor.fetchone()
    if not row:
        raise NotificationError("Notification not found", 404)
    return _notification(row)
