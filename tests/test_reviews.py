"""Review route and transaction behavior tests."""

from copy import deepcopy
from datetime import datetime

import pytest

from config import create_app


class ReviewCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.results = []

    def execute(self, query, *params):
        self.result = None
        self.results = []
        if "SELECT b.borrower_id, o.owner_id, b.status, r.request_id" in query:
            booking = self.database["booking"]
            if booking and booking[0] == params[0]:
                self.result = (booking[1], booking[2], booking[3], booking[4])
        elif "SELECT review_id FROM dbo.Reviews" in query:
            self.result = next((
                (row[0],) for row in self.database["reviews"]
                if row[1] == params[0] and row[2] == params[1]
            ), None)
        elif "INSERT INTO dbo.Reviews" in query:
            if self.database["fail_on"] == "INSERT INTO dbo.Reviews":
                raise RuntimeError("simulated review insert failure")
            booking_id, reviewer_id, reviewee_id, rating, comment = params
            review_id = self.database["next_review_id"]
            self.database["next_review_id"] += 1
            row = (review_id, booking_id, reviewer_id, reviewee_id, rating, comment, datetime(2026, 10, 10))
            self.database["reviews"].append(row)
            self.result = (row[0], row[1], row[4], row[5], row[6])
        elif "INSERT INTO dbo.Notifications" in query:
            if self.database["fail_on"] == "INSERT INTO dbo.Notifications":
                raise RuntimeError("simulated notification failure")
            self.database["notifications"].append(tuple(params))
        elif "INSERT INTO dbo.AuditLogs" in query:
            self.database["audits"].append(tuple(params))
        elif "SELECT review_id, booking_id, rating, comment, created_at" in query:
            booking_id = params[0]
            self.results = [
                (r[0], r[1], r[4], r[5], r[6])
                for r in self.database["reviews"] if r[1] == booking_id
            ]
        elif "FROM dbo.Reviews AS rv" in query:
            review = next((r for r in self.database["reviews"] if r[0] == params[0]), None)
            booking = self.database["booking"]
            if review and booking and review[1] == booking[0]:
                self.result = (review[0], review[1], review[4], review[5], review[6], booking[1], booking[2])
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.results)


class ReviewConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = deepcopy(database)
        self._cursor = ReviewCursor(database)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.database.clear()
        self.database.update(deepcopy(self.snapshot))

    def close(self):
        pass


def make_database(status="COMPLETED", borrower_id=100, owner_id=200):
    return {
        "booking": (1, borrower_id, owner_id, status, 77),
        "reviews": [],
        "next_review_id": 1,
        "notifications": [],
        "audits": [],
        "fail_on": None,
    }


def install_database(monkeypatch, database):
    connections = []

    def connect():
        connection = ReviewConnection(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.reviews.get_connection", connect)
    return connections


def signed_in_client(user_id=100):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def test_completed_booking_participant_can_create_review_and_notify(monkeypatch):
    database = make_database()
    connections = install_database(monkeypatch, database)

    response = signed_in_client(user_id=100).post(
        "/api/reviews/bookings/1/reviews",
        json={"rating": 5, "comment": "  Great lender  "},
    )

    assert response.status_code == 201, response.json
    assert response.json["review"] == {
        "review_id": 1,
        "booking_id": 1,
        "rating": 5,
        "comment": "Great lender",
        "created_at": "2026-10-10T00:00:00",
    }
    assert "reviewer_id" not in response.json["review"]
    assert database["reviews"][0][2:5] == (100, 200, 5)
    assert database["notifications"] == [(
        200, 77, "You received review 1 for a completed booking.", "REVIEW_RECEIVED",
    )]
    assert database["audits"][0] == (100, "1", "127.0.0.1")
    assert connections[-1].commits == 1


@pytest.mark.parametrize("payload", [
    {"rating": 0}, {"rating": 6}, {"rating": 3.5}, {"rating": True},
    {"rating": "5"}, {"rating": 4, "comment": "x" * 1001},
    {"rating": 4, "comment": 10}, {"rating": 4, "reviewer_id": 200},
])
def test_invalid_review_rating_or_comment_is_rejected(monkeypatch, payload):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/reviews/bookings/1/reviews", json=payload)

    assert response.status_code == 400
    assert database["reviews"] == []


def test_review_creation_requires_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.post("/api/reviews/bookings/1/reviews", json={"rating": 5}).status_code == 401
    assert client.get("/api/reviews/bookings/1/reviews").status_code == 401
    assert client.get("/api/reviews/1").status_code == 401


@pytest.mark.parametrize("status", ["BOOKED", "HANDED_OVER", "RETURNED", "CANCELLED"])
def test_only_completed_bookings_can_be_reviewed(monkeypatch, status):
    database = make_database(status=status)
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/reviews/bookings/1/reviews", json={"rating": 4})

    assert response.status_code == 409
    assert database["reviews"] == []


def test_booking_participant_cannot_review_self(monkeypatch):
    database = make_database(borrower_id=100, owner_id=100)
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=100).post("/api/reviews/bookings/1/reviews", json={"rating": 4})

    assert response.status_code == 409
    assert database["reviews"] == []


def test_unrelated_user_cannot_create_review(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=300).post(
        "/api/reviews/bookings/1/reviews", json={"rating": 4},
    )

    assert response.status_code == 403
    assert database["reviews"] == []


def test_duplicate_review_is_rejected(monkeypatch):
    database = make_database()
    database["reviews"].append((1, 1, 100, 200, 4, None, datetime(2026, 10, 9)))
    database["next_review_id"] = 2
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=100).post("/api/reviews/bookings/1/reviews", json={"rating": 2})

    assert response.status_code == 409
    assert len(database["reviews"]) == 1


def test_booking_participants_can_list_reviews_and_outsiders_are_denied(monkeypatch):
    database = make_database()
    database["reviews"].append((1, 1, 100, 200, 5, "Good", datetime(2026, 10, 9)))
    install_database(monkeypatch, database)

    borrower = signed_in_client(user_id=100).get("/api/reviews/bookings/1/reviews")
    owner = signed_in_client(user_id=200).get("/api/reviews/bookings/1/reviews")
    outsider = signed_in_client(user_id=300).get("/api/reviews/bookings/1/reviews")

    assert borrower.status_code == owner.status_code == 200
    assert borrower.json["reviews"][0]["rating"] == 5
    assert outsider.status_code == 403


def test_review_detail_is_visible_only_to_booking_participants(monkeypatch):
    database = make_database()
    database["reviews"].append((1, 1, 100, 200, 5, "Good", datetime(2026, 10, 9)))
    install_database(monkeypatch, database)

    allowed = signed_in_client(user_id=200).get("/api/reviews/1")
    denied = signed_in_client(user_id=300).get("/api/reviews/1")
    missing = signed_in_client(user_id=200).get("/api/reviews/99")

    assert allowed.status_code == 200
    assert "reviewee_id" not in allowed.json["review"]
    assert denied.status_code == 403
    assert missing.status_code == 404


def test_notification_failure_rolls_back_review(monkeypatch):
    database = make_database()
    database["fail_on"] = "INSERT INTO dbo.Notifications"
    connections = install_database(monkeypatch, database)

    response = signed_in_client().post("/api/reviews/bookings/1/reviews", json={"rating": 5})

    assert response.status_code == 503
    assert database["reviews"] == []
    assert database["notifications"] == []
    assert database["audits"] == []
    assert connections[-1].rollbacks == 1


def test_review_route_rejects_invalid_booking_id(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/reviews/bookings/0/reviews", json={"rating": 5})

    assert response.status_code == 400
