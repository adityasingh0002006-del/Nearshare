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
            "viewer_active": True,
            "available_item": True,
            "active_owner": True,
            "within_budget": True,
            "no_conflicting_booking": True,
            "not_ignored": True,
        }
        self.candidate.update(candidate or {})
        self.query = None
        self.params = None
        self.feed_query = None

    def execute(self, query, *params):
        self.query, self.params = query, params
        if "FROM dbo.Users AS viewer" in query:
            self.feed_query = query
        return self

    def fetchall(self):
        if self.feed_query == self.query:
            return [MATCHING_REQUEST] if all(
                self.candidate[key] for key in
                ("active_request", "different_requester", "same_city", "same_locality", "viewer_active", "not_ignored")
            ) else []
        return [(41,)] if all(self.candidate[key] for key in (
            "active_request", "different_requester", "same_city", "same_locality",
            "same_category", "available_item", "active_owner", "within_budget",
            "no_conflicting_booking",
        )) else []


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


def test_local_user_can_retrieve_safe_requests_even_without_matching_item(monkeypatch):
    from services.matching_service import find_nearby_requests

    cursor = NearbyCursor()
    requests = find_nearby_requests(cursor, 20)
    assert requests == [{
        "request_id": 41, "title": "Cordless Drill", "category_id": 1,
        "category_name": "Tools", "city": "Kanpur", "locality": "Kakadeo",
        "start_datetime": "2026-10-10T17:35:00", "end_datetime": "2026-10-11T16:35:00",
        "max_budget": "50.00", "status": "OPEN",
    }]
    assert "dbo.Items" not in cursor.query
    assert "request_locality.locality_id = viewer_locality.locality_id" in cursor.query
    assert "request_locality.city = viewer_locality.city" in cursor.query
    assert "viewer_response.response_status <> N'IGNORED'" in cursor.query


def test_ignored_request_is_hidden_only_for_that_viewer(monkeypatch):
    cursor = NearbyCursor(candidate={"not_ignored": False})
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=20).get("/api/requests/nearby")

    assert response.status_code == 200
    assert response.json == {"requests": []}
    assert "viewer_response.user_id = viewer.user_id" in cursor.feed_query


def test_owner_feed_marks_only_requests_with_a_fully_eligible_item(monkeypatch):
    cursor = NearbyCursor(candidate={"available_item": False})
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=20).get("/api/requests/nearby")

    assert response.status_code == 200
    assert len(response.json["requests"]) == 1
    assert response.json["requests"][0]["has_matching_item"] is False
    assert response.json["requests"][0]["max_budget"] == "50.00"
    assert cursor.params == (20,)
    assert "viewer.user_id = ?" in cursor.feed_query
    select_clause = cursor.feed_query.split("FROM dbo.Users AS viewer", 1)[0]
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
        ("different_requester", "r.requester_id <> viewer.user_id"),
        ("same_city", "request_locality.city = viewer_locality.city"),
        ("same_locality", "request_locality.locality_id = viewer_locality.locality_id"),
        ("viewer_active", "viewer.is_active = 1"),
    ],
)
def test_nearby_feed_excludes_nonlocal_or_inactive_requests(monkeypatch, ineligible, required_sql):
    cursor = NearbyCursor(candidate={ineligible: False})
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/nearby")

    assert response.status_code == 200
    assert response.json == {"requests": []}
    assert required_sql in cursor.feed_query


@pytest.mark.parametrize("ineligible", [
    "same_category", "available_item", "active_owner", "within_budget", "no_conflicting_booking",
])
def test_feed_still_shows_request_but_marks_it_unofferable_without_eligible_item(monkeypatch, ineligible):
    cursor = NearbyCursor(candidate={ineligible: False})
    install_cursor(monkeypatch, cursor)
    response = signed_in_client().get("/api/requests/nearby")
    assert response.status_code == 200
    assert len(response.json["requests"]) == 1
    assert response.json["requests"][0]["has_matching_item"] is False


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
