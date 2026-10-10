from datetime import datetime
from decimal import Decimal

import pytest

from config import create_app


MATCHING_REQUEST = (
    41, "Cordless drill", 1, "Tools", "Kanpur", "Kakadeo",
    datetime(2026, 10, 10, 17, 35), datetime(2026, 10, 11, 16, 35),
    Decimal("50.00"), "OPEN",
)


class MatchingRequestsCursor:
    def __init__(self, owner_id=20, candidate=None, missing_item=False):
        self.owner_id = owner_id
        self.candidate = {
            "active_request": True,
            "available_item": True,
            "active_owner": True,
            "same_city": True,
            "same_locality": True,
            "same_category": True,
            "within_budget": True,
            "no_conflicting_booking": True,
            "different_requester": True,
        }
        self.candidate.update(candidate or {})
        self.missing_item = missing_item
        self.result = None
        self.rows = []
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        if "SELECT owner_id FROM dbo.Items" in query:
            self.result = None if self.missing_item else (self.owner_id,)
        elif "FROM dbo.Items AS i" in query:
            self.rows = [MATCHING_REQUEST] if all(self.candidate.values()) else []
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.rows)


class MatchingRequestsConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self):
        return self.fake_cursor

    def close(self):
        pass


def signed_in_client(user_id=20):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def install_cursor(monkeypatch, cursor):
    monkeypatch.setattr(
        "routes.items.get_connection",
        lambda: MatchingRequestsConnection(cursor),
    )


def test_item_owner_can_retrieve_matching_requests_without_requester_data(monkeypatch):
    cursor = MatchingRequestsCursor()
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/items/9/matching-requests")

    assert response.status_code == 200
    assert response.json == {
        "item_id": 9,
        "requests": [{
            "request_id": 41,
            "title": "Cordless drill",
            "category_id": 1,
            "category_name": "Tools",
            "city": "Kanpur",
            "locality": "Kakadeo",
            "start_datetime": "2026-10-10T17:35:00",
            "end_datetime": "2026-10-11T16:35:00",
            "max_budget": "50.00",
            "status": "OPEN",
        }],
    }
    sql = cursor.executed[1][0]
    select_clause = sql.split("FROM dbo.Items", 1)[0]
    assert "requester_id" not in select_clause
    assert "email" not in select_clause
    assert "phone" not in select_clause
    assert "address" not in select_clause
    assert cursor.executed[0][1] == (9,)
    assert cursor.executed[1][1] == (9,)


def test_non_owner_cannot_retrieve_matching_requests_for_another_users_item(monkeypatch):
    cursor = MatchingRequestsCursor(owner_id=20)
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=21).get("/api/items/9/matching-requests")

    assert response.status_code == 403
    assert len(cursor.executed) == 1


def test_missing_item_returns_not_found(monkeypatch):
    cursor = MatchingRequestsCursor(missing_item=True)
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/items/999/matching-requests")

    assert response.status_code == 404
    assert len(cursor.executed) == 1


def test_matching_request_endpoint_requires_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/items/9/matching-requests").status_code == 401


@pytest.mark.parametrize(
    ("excluded_condition", "required_sql"),
    [
        ("active_request", "r.status IN (N'OPEN', N'MATCHED')"),
        ("available_item", "i.is_available = 1"),
        ("active_owner", "item_owner.is_active = 1"),
        ("same_city", "request_locality.city = item_locality.city"),
        ("same_locality", "item_locality.locality_id = r.locality_id"),
        ("same_category", "i.category_id = r.category_id"),
        ("within_budget", "i.rental_price <= r.max_budget"),
        ("different_requester", "i.owner_id <> r.requester_id"),
        ("no_conflicting_booking", "b.start_datetime < r.end_datetime"),
    ],
)
def test_matching_requests_excludes_ineligible_candidates(
    monkeypatch, excluded_condition, required_sql
):
    cursor = MatchingRequestsCursor(candidate={excluded_condition: False})
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/items/9/matching-requests")

    assert response.status_code == 200
    assert response.json["requests"] == []
    assert required_sql in cursor.executed[1][0]


def test_matching_request_sql_excludes_all_overlapping_non_cancelled_bookings():
    from services.matching_service import find_matching_requests

    class QueryCursor:
        def __init__(self):
            self.query = None
            self.params = None

        def execute(self, query, *params):
            self.query, self.params = query, params

        def fetchall(self):
            return []

    cursor = QueryCursor()
    assert find_matching_requests(cursor, 9) == []
    assert "b.status <> N'CANCELLED'" in cursor.query
    assert "b.start_datetime < r.end_datetime" in cursor.query
    assert "b.end_datetime > r.start_datetime" in cursor.query
    assert "request_locality.city = item_locality.city" in cursor.query
    assert cursor.params == (9,)
