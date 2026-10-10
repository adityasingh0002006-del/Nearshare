"""Read-only hyperlocal request-to-item matching queries."""


MATCH_ELIGIBILITY_SQL = """r.status IN (N'OPEN', N'MATCHED')
             AND i.owner_id <> r.requester_id
             AND i.is_available = 1
             AND item_owner.is_active = 1
             AND i.category_id = r.category_id
             AND item_locality.locality_id = r.locality_id
             AND request_locality.locality_id = r.locality_id
             AND request_locality.city = item_locality.city
             AND i.rental_price <= r.max_budget
             AND NOT EXISTS
                 (SELECT 1
                  FROM dbo.Bookings AS b
                  INNER JOIN dbo.Offers AS o ON o.offer_id = b.offer_id
                  WHERE o.item_id = i.item_id
                    AND b.status <> N'CANCELLED'
                    AND b.start_datetime < r.end_datetime
                    AND b.end_datetime > r.start_datetime)"""


def find_matches(cursor, request_id, viewer_id=None):
    """Return eligible items for one request in a single parameterized query.

    A match requires the same category and locality, an available item and an
    active owner, an item rental price within the request budget, and no
    non-cancelled booking whose dates overlap the requested interval.
    """
    cursor.execute(
        """SELECT i.item_id, i.category_id, c.category_name, i.item_name,
                  i.description, i.item_condition, i.rental_price,
                  i.security_deposit, i.owner_id
           FROM dbo.Requests AS r
           INNER JOIN dbo.Items AS i ON i.category_id = r.category_id
           INNER JOIN dbo.Categories AS c ON c.category_id = i.category_id
           INNER JOIN dbo.Users AS item_owner ON item_owner.user_id = i.owner_id
           INNER JOIN dbo.Localities AS item_locality
               ON item_locality.locality_id = item_owner.locality_id
              AND item_locality.locality_id = r.locality_id
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           WHERE r.request_id = ?
             AND """ + MATCH_ELIGIBILITY_SQL + """
           ORDER BY i.rental_price, i.item_name, i.item_id""",
        request_id,
    )
    return [
        {
            "item_id": row[0],
            "category_id": row[1],
            "category_name": row[2],
            "item_name": row[3],
            "description": row[4],
            "condition": row[5],
            "rental_price": str(row[6]),
            "security_deposit": str(row[7]),
            "is_item_owner": viewer_id is not None and row[8] == viewer_id,
        }
        for row in cursor.fetchall()
    ]


def find_matching_requests(cursor, item_id):
    """Return safe request details eligible for an item owner to review."""
    cursor.execute(
        """SELECT r.request_id, r.item_description, r.category_id,
                  c.category_name, request_locality.city,
                  request_locality.locality_name, r.start_datetime,
                  r.end_datetime, r.max_budget, r.status
           FROM dbo.Items AS i
           INNER JOIN dbo.Requests AS r ON r.category_id = i.category_id
           INNER JOIN dbo.Categories AS c ON c.category_id = r.category_id
           INNER JOIN dbo.Users AS item_owner ON item_owner.user_id = i.owner_id
           INNER JOIN dbo.Localities AS item_locality
               ON item_locality.locality_id = item_owner.locality_id
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           WHERE i.item_id = ?
             AND """ + MATCH_ELIGIBILITY_SQL + """
           ORDER BY r.start_datetime, r.request_id""",
        item_id,
    )
    return _serialize_matching_requests(cursor.fetchall())


def find_nearby_requests(cursor, owner_id):
    """Return distinct eligible request details for all of one owner's items."""
    cursor.execute(
        """SELECT DISTINCT r.request_id, r.item_description, r.category_id,
                  c.category_name, request_locality.city,
                  request_locality.locality_name, r.start_datetime,
                  r.end_datetime, r.max_budget, r.status
           FROM dbo.Requests AS r
           INNER JOIN dbo.Items AS i ON i.category_id = r.category_id
           INNER JOIN dbo.Categories AS c ON c.category_id = r.category_id
           INNER JOIN dbo.Users AS item_owner ON item_owner.user_id = i.owner_id
           INNER JOIN dbo.Localities AS item_locality
               ON item_locality.locality_id = item_owner.locality_id
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           WHERE item_owner.user_id = ?
             AND """ + MATCH_ELIGIBILITY_SQL + """
           ORDER BY r.start_datetime, r.request_id""",
        owner_id,
    )
    return _serialize_matching_requests(cursor.fetchall())


def find_matching_request_for_borrower_item(cursor, item_id, requester_id):
    """Return the soonest eligible request belonging to this item viewer."""
    cursor.execute(
        """SELECT TOP (1) r.request_id
           FROM dbo.Items AS i
           INNER JOIN dbo.Users AS item_owner ON item_owner.user_id = i.owner_id
           INNER JOIN dbo.Localities AS item_locality
               ON item_locality.locality_id = item_owner.locality_id
           INNER JOIN dbo.Requests AS r ON r.category_id = i.category_id
           INNER JOIN dbo.Localities AS request_locality
               ON request_locality.locality_id = r.locality_id
           WHERE i.item_id = ?
             AND r.requester_id = ?
             AND """ + MATCH_ELIGIBILITY_SQL + """
           ORDER BY r.start_datetime, r.created_at DESC, r.request_id DESC""",
        item_id, requester_id,
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _serialize_matching_requests(rows):
    return [
        {
            "request_id": row[0],
            "title": row[1],
            "category_id": row[2],
            "category_name": row[3],
            "city": row[4],
            "locality": row[5],
            "start_datetime": row[6].isoformat() if row[6] else None,
            "end_datetime": row[7].isoformat() if row[7] else None,
            "max_budget": str(row[8]),
            "status": row[9],
        }
        for row in rows
    ]
