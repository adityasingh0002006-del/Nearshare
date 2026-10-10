from datetime import datetime
from decimal import Decimal

import pytest

from config import create_app


MATCHING_REQUEST = (
    41, "Cordless Drill", 1, "Tools", "Kanpur", "Kakadeo",
    datetime(2026, 10, 10, 17, 35), datetime(2026, 10, 11, 16, 35),
    Decimal("50.00"), "OPEN",
)


class NearbyCursor:
    def __init__(self, candidate=None):
        self.candidate = {
            "active_request": True,
            "different_requester": True,
            "same_city": True,
            "same_locality": True,
            "same_category": True,
            "available_item": True,
            "active_owner": True,
            "within_budget": True,
            "no_conflicting_booking": True,
        }
        self.candidate.update(candidate or {})
        self.query = None
        self.params = None

    def execute(self, query, *params):
        self.query, self.params = query, params
        return self

    def fetchall(self):
        return [MATCHING_REQUEST] if all(self.candidate.values()) else []


class NearbyConnection:
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
        "routes.requests.get_connection",
        lambda: NearbyConnection(cursor),
    )


def test_owner_can_retrieve_safe_eligible_requests_for_their_items(monkeypatch):
    cursor = NearbyCursor()
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=20).get("/api/requests/nearby")

    assert response.status_code == 200
    assert response.json == {"requests": [{
        "request_id": 41,
        "title": "Cordless Drill",
        "category_id": 1,
        "category_name": "Tools",
        "city": "Kanpur",
        "locality": "Kakadeo",
        "start_datetime": "2026-10-10T17:35:00",
        "end_datetime": "2026-10-11T16:35:00",
        "max_budget": "50.00",
        "status": "OPEN",
    }]}
    assert cursor.params == (20,)
    assert "item_owner.user_id = ?" in cursor.query
    select_clause = cursor.query.split("FROM dbo.Requests", 1)[0]
    assert "requester_id" not in select_clause
    assert "email" not in select_clause
    assert "phone" not in select_clause
    assert "address" not in select_clause


def test_nearby_request_feed_requires_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/requests/nearby").status_code == 401


@pytest.mark.parametrize(
    ("ineligible", "required_sql"),
    [
        ("active_request", "r.status IN (N'OPEN', N'MATCHED')"),
        ("different_requester", "i.owner_id <> r.requester_id"),
        ("same_city", "request_locality.city = item_locality.city"),
        ("same_locality", "item_locality.locality_id = r.locality_id"),
        ("same_category", "i.category_id = r.category_id"),
        ("available_item", "i.is_available = 1"),
        ("active_owner", "item_owner.is_active = 1"),
        ("within_budget", "i.rental_price <= r.max_budget"),
        ("no_conflicting_booking", "b.start_datetime < r.end_datetime"),
    ],
)
def test_nearby_feed_excludes_ineligible_requests(monkeypatch, ineligible, required_sql):
    cursor = NearbyCursor(candidate={ineligible: False})
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/nearby")

    assert response.status_code == 200
    assert response.json == {"requests": []}
    assert required_sql in cursor.query


def test_nearby_feed_deduplicates_request_matching_multiple_owned_items():
    from services.matching_service import find_nearby_requests

    class QueryCursor:
        def execute(self, query, *params):
            self.query, self.params = query, params

        def fetchall(self):
            return [MATCHING_REQUEST]

    cursor = QueryCursor()
    requests = find_nearby_requests(cursor, 20)

    assert len(requests) == 1
    assert "SELECT DISTINCT r.request_id" in cursor.query
    assert cursor.params == (20,)
