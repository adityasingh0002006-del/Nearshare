"""Privacy-safe access and per-user responses to nearby requests."""


class RequestResponseError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def get_request_response(cursor, request_id, user_id):
    """Return safe details to the requester or an active same-locality user."""
    cursor.execute(
        """SELECT r.request_id, r.item_description, r.category_id,
                  c.category_name, request_locality.city,
                  request_locality.locality_name, r.start_datetime,
                  r.end_datetime, r.max_budget, r.status, requester.full_name,
                  response.response_status,
                  CASE WHEN r.requester_id = viewer.user_id THEN 1 ELSE 0 END
           FROM dbo.Requests AS r
           INNER JOIN dbo.Users AS requester ON requester.user_id = r.requester_id
           INNER JOIN dbo.Users AS viewer ON viewer.user_id = ?
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           LEFT JOIN dbo.Localities AS viewer_locality
               ON viewer_locality.locality_id = viewer.locality_id
           INNER JOIN dbo.Categories AS c ON c.category_id = r.category_id
           LEFT JOIN dbo.RequestResponses AS response
               ON response.request_id = r.request_id AND response.user_id = viewer.user_id
              AND r.requester_id <> viewer.user_id
           WHERE r.request_id = ?
             AND (r.requester_id = viewer.user_id OR
                 (viewer.is_active = 1 AND r.status IN (N'OPEN', N'MATCHED')
                  AND request_locality.locality_id = viewer_locality.locality_id
                  AND request_locality.city = viewer_locality.city))""",
        user_id, request_id,
    )
    row = cursor.fetchone()
    if not row:
        raise RequestResponseError("Request not found", 404)
    return {
        "request_id": row[0], "title": row[1], "category_id": row[2],
        "category_name": row[3], "city": row[4], "locality": row[5],
        "start_datetime": row[6].isoformat() if row[6] else None,
        "end_datetime": row[7].isoformat() if row[7] else None,
        "max_budget": str(row[8]), "status": row[9],
        "requester_name": row[10], "response_status": row[11],
        "is_requester": bool(row[12]),
    }


def set_request_response(cursor, request_id, user_id, response_status):
    """Persist an idempotent INTERESTED/IGNORED response for an eligible viewer."""
    if not isinstance(response_status, str) or response_status not in {"INTERESTED", "IGNORED"}:
        raise RequestResponseError("response_status must be INTERESTED or IGNORED", 400)
    # Enforce locality and request state in the write predicate, not in client data.
    details = get_request_response(cursor, request_id, user_id)
    if details["is_requester"]:
        raise RequestResponseError("The requester cannot respond to their own request", 403)
    cursor.execute(
        """SELECT response_status FROM dbo.RequestResponses WITH (UPDLOCK, HOLDLOCK)
           WHERE request_id = ? AND user_id = ?""",
        request_id, user_id,
    )
    existing = cursor.fetchone()
    if existing:
        if existing[0] == "IGNORED" and response_status != "IGNORED":
            raise RequestResponseError("This request was ignored", 409)
        if existing[0] == "OFFERED":
            raise RequestResponseError("An offer has already been sent", 409)
        if existing[0] != response_status:
            cursor.execute(
                """UPDATE dbo.RequestResponses SET response_status = ?, updated_at = SYSUTCDATETIME()
                   WHERE request_id = ? AND user_id = ?""",
                response_status, request_id, user_id,
            )
    else:
        cursor.execute(
            """INSERT INTO dbo.RequestResponses (request_id, user_id, response_status)
               VALUES (?, ?, ?)""",
            request_id, user_id, response_status,
        )
    if response_status == "IGNORED":
        cursor.execute(
            """UPDATE dbo.Notifications SET is_read = 1
               WHERE user_id = ? AND request_id = ?
                 AND notification_type IN (N'REQUEST_NEARBY', N'MATCHING_REQUEST')
                 AND is_read = 0""",
            user_id, request_id,
        )
    return response_status
