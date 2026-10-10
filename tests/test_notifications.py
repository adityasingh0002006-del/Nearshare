"""Notification inbox and idempotent workflow helper tests."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest

from config import create_app
from services.notification_service import (
    NotificationError,
    create_notification,
    list_notifications,
    mark_notification_read,
    notify_locality_users_of_request,
    unread_count,
)


class NotificationCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.results = []
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        self.result = None
        self.results = []
        if "SELECT notification_id" in query and "WITH (UPDLOCK, HOLDLOCK)" in query:
            user_id, request_id, notification_type = params[:3]
            message = params[3] if len(params) > 3 else None
            row = next((n for n in self.database["notifications"]
                        if n[1] == user_id and n[2] == request_id
                        and n[4] == notification_type
                        and (message is None or n[3] == message)), None)
            self.result = (row[0],) if row else None
        elif "SELECT DISTINCT recipient.user_id, requester.full_name, r.item_description" in query:
            request_id = params[0]
            requests = self.database.get("requests")
            if requests is None:
                request = self.database.get("request")
                requests = {request["request_id"]: request} if request else {}
            request = requests.get(request_id)
            self.results = []
            for recipient in self.database.get("users", []):
                if not request or request["request_id"] != request_id:
                    continue
                if (not recipient["active"] or recipient["user_id"] == request["requester_id"]
                        or recipient["locality_id"] != request["locality_id"]
                        or recipient["city"] != request["city"]
                        or request["status"] not in {"OPEN", "MATCHED"}):
                    continue
                self.results.append((
                    recipient["user_id"], request.get("requester_name", "Aditya"), request["title"], request["category"],
                    request["locality"], request["city"], request["start"],
                    request["end"], request["budget"],
                ))
        elif "INSERT INTO dbo.Notifications" in query:
            user_id, request_id, message, notification_type = params
            self._insert(user_id, request_id, message, notification_type)
        elif "SELECT COUNT(*) FROM dbo.Notifications" in query:
            user_id = params[0]
            self.result = (sum(1 for row in self.database["notifications"]
                               if row[1] == user_id and not row[5]),)
        elif "SELECT notification_id, request_id, message, notification_type" in query:
            if "OFFSET" in query:
                user_id, offset, limit = params
                rows = [row for row in self.database["notifications"] if row[1] == user_id]
                rows.sort(key=lambda row: (row[6], row[0]), reverse=True)
                self.results = [self._output(row) for row in rows[offset:offset + limit]]
            else:
                notification_id, user_id = params
                row = next((row for row in self.database["notifications"]
                            if row[0] == notification_id and row[1] == user_id), None)
                self.result = self._output(row) if row else None
        elif "UPDATE dbo.Notifications SET is_read = 1" in query:
            notification_id, user_id = params
            row = next((row for row in self.database["notifications"]
                        if row[0] == notification_id and row[1] == user_id and not row[5]), None)
            if row:
                updated = (*row[:5], True, row[6])
                self.database["notifications"][self.database["notifications"].index(row)] = updated
                self.result = self._output(updated)
        return self

    def _insert(self, user_id, request_id, message, notification_type):
        row = (self.database["next_id"], user_id, request_id, message,
               notification_type, False, datetime(2026, 10, 10))
        self.database["next_id"] += 1
        self.database["notifications"].append(row)

    @staticmethod
    def _output(row):
        return (row[0], row[2], row[3], row[4], row[5], row[6])

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.results)


class NotificationConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = deepcopy(database)
        self._cursor = NotificationCursor(database)
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


class OfferCreationCursor(NotificationCursor):
    def execute(self, query, *params):
        if "SELECT requester_id, category_id, locality_id, max_budget, status" in query:
            self.result = (100, 5, 9, Decimal("50.00"), "OPEN",
                           datetime(2026, 11, 1), datetime(2026, 11, 3))
            return self
        if "SELECT i.owner_id, i.category_id, i.is_available" in query:
            self.result = (200, 5, True, Decimal("40.00"), 9, True,
                           "Cordless Drill", "Surya", "Kanpur", "Kanpur")
            return self
        if "WHERE booked_offer.item_id = ?" in query or "SELECT 1 FROM dbo.Offers WITH" in query:
            self.result = None
            return self
        if "INSERT INTO dbo.Offers" in query:
            self.result = (
                1, 1, 20, 200, "RENTAL", Decimal("35.00"), Decimal("0.00"),
                None, "PENDING", datetime(2026, 10, 10),
            )
            return self
        return super().execute(query, *params)


class OfferCreationConnection(NotificationConnection):
    def __init__(self, database):
        super().__init__(database)
        self._cursor = OfferCreationCursor(database)


def make_database():
    return {"notifications": [], "next_id": 1, "items": [], "users": []}


def install_database(monkeypatch, database):
    connections = []

    def connect():
        conn = NotificationConnection(database)
        connections.append(conn)
        return conn

    monkeypatch.setattr("routes.notifications.get_connection", connect)
    return connections


def signed_in_client(user_id=10):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def test_notification_creation_is_idempotent_for_same_event():
    database = make_database()
    cursor = NotificationCursor(database)

    assert create_notification(cursor, 10, 20, "Offer received", "OFFER_RECEIVED") is True
    assert create_notification(cursor, 10, 20, "Offer received", "OFFER_RECEIVED") is False
    assert create_notification(cursor, 10, 20, "Another offer received", "OFFER_RECEIVED") is True
    assert len(database["notifications"]) == 2


def test_new_request_notifies_all_active_same_locality_users_without_items_once():
    database = make_database()
    database["request"] = {
        "request_id": 5, "requester_id": 10, "status": "OPEN", "title": "Cordless Drill",
        "category": "Tools", "locality_id": 9, "locality": "Kakadeo", "city": "Kanpur",
        "start": datetime(2026, 10, 10, 17, 35), "end": datetime(2026, 10, 11, 16, 35),
        "budget": Decimal("50.00"),
    }
    database["users"] = [
        {"user_id": 10, "active": True, "locality_id": 9, "city": "Kanpur"},
        {"user_id": 20, "active": True, "locality_id": 9, "city": "Kanpur"},
        {"user_id": 21, "active": True, "locality_id": 9, "city": "Kanpur"},
        {"user_id": 25, "active": True, "locality_id": 9, "city": "Kanpur", "has_matching_item": True},
        {"user_id": 22, "active": True, "locality_id": 8, "city": "Kanpur"},
        {"user_id": 23, "active": True, "locality_id": 9, "city": "Lucknow"},
        {"user_id": 24, "active": False, "locality_id": 9, "city": "Kanpur"},
    ]
    cursor = NotificationCursor(database)

    notify_locality_users_of_request(cursor, 5)
    notify_locality_users_of_request(cursor, 5)

    assert {row[1] for row in database["notifications"]} == {20, 21, 25}
    assert len(database["notifications"]) == 3
    assert all(row[4] == "REQUEST_NEARBY" for row in database["notifications"])
    message = database["notifications"][0][3]
    assert all(value in message for value in (
        "Aditya", "Cordless Drill", "Tools", "Kakadeo", "Kanpur", "2026-10-10", "2026-10-11", "50",
    ))
    assert len(message) <= 1000
    assert all(value not in message for value in ("requester", "email", "phone", "address", "Account A"))
    query = cursor.executed[0][0]
    assert "SELECT DISTINCT recipient.user_id, requester.full_name, r.item_description" in query
    assert "recipient.is_active = 1" in query
    assert "recipient.user_id <> r.requester_id" in query
    assert "recipient_locality.city = request_locality.city" in query
    assert "recipient_locality.locality_id = request_locality.locality_id" in query
    assert "dbo.Items" not in query
    assert "r.status IN (N'OPEN', N'MATCHED')" in query


def test_multiple_locality_requests_are_visible_in_notifications_api_and_unread_count(monkeypatch):
    database = make_database()
    base_request = {
        "requester_id": 10, "status": "OPEN", "category": "Tools",
        "locality_id": 9, "locality": "Kakadeo", "city": "Kanpur",
        "start": datetime(2026, 10, 10, 17, 35),
        "end": datetime(2026, 10, 11, 16, 35), "budget": Decimal("50.00"),
    }
    database["requests"] = {
        5: {**base_request, "request_id": 5, "title": "Cordless Drill"},
        6: {**base_request, "request_id": 6, "title": "Power Sander"},
    }
    database["users"] = [
        {"user_id": 10, "active": True, "locality_id": 9, "city": "Kanpur"},
        {"user_id": 20, "active": True, "locality_id": 9, "city": "Kanpur"},
        {"user_id": 30, "active": True, "locality_id": 8, "city": "Kanpur"},
    ]
    cursor = NotificationCursor(database)

    notify_locality_users_of_request(cursor, 5)
    notify_locality_users_of_request(cursor, 5)
    notify_locality_users_of_request(cursor, 6)

    assert {(row[1], row[2]) for row in database["notifications"]} == {(20, 5), (20, 6)}
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=20)

    inbox = client.get("/api/notifications?page=1&per_page=50")
    unread = client.get("/api/notifications/unread-count")

    assert inbox.status_code == unread.status_code == 200
    assert {row["request_id"] for row in inbox.json["notifications"]} == {5, 6}
    assert all(row["notification_type"] == "REQUEST_NEARBY" for row in inbox.json["notifications"])
    assert unread.json == {"unread_count": 2}
    assert all("Cordless Drill" not in row["message"] or row["request_id"] == 5
               for row in inbox.json["notifications"])


def test_offer_creation_notifies_request_owner_in_same_transaction(monkeypatch):
    database = make_database()
    connections = []

    def connect():
        connection = OfferCreationConnection(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.offers.get_connection", connect)
    response = signed_in_client(user_id=200).post(
        "/api/requests/1/offers",
        json={"item_id": 20, "offer_type": "RENTAL", "offered_price": "35.00"},
    )

    assert response.status_code == 201, response.json
    assert database["notifications"][0][1:5] == (
        100, 1, "Surya offered to rent Cordless Drill for your request.", "OFFER_RECEIVED",
    )
    assert connections[-1].commits == 1


def test_inbox_lists_only_own_notifications_in_paged_newest_first_order(monkeypatch):
    database = make_database()
    database["notifications"] = [
        (1, 10, 4, "older", "TYPE", False, datetime(2026, 10, 8)),
        (2, 10, 4, "newer", "TYPE", False, datetime(2026, 10, 9)),
        (3, 11, 4, "private", "TYPE", False, datetime(2026, 10, 10)),
    ]
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=10).get("/api/notifications?page=1&per_page=1")

    assert response.status_code == 200
    assert response.json["page"] == 1
    assert response.json["per_page"] == 1
    assert [row["notification_id"] for row in response.json["notifications"]] == [2]
    assert "user_id" not in response.json["notifications"][0]


def test_unread_count_is_scoped_to_authenticated_user(monkeypatch):
    database = make_database()
    database["notifications"] = [
        (1, 10, 4, "unread", "TYPE", False, datetime(2026, 10, 9)),
        (2, 10, 4, "read", "TYPE", True, datetime(2026, 10, 8)),
        (3, 11, 4, "other", "TYPE", False, datetime(2026, 10, 10)),
    ]
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=10).get("/api/notifications/unread-count")

    assert response.status_code == 200
    assert response.json == {"unread_count": 1}


def test_owner_can_mark_notification_read_repeatedly(monkeypatch):
    database = make_database()
    database["notifications"].append(
        (1, 10, 4, "hello", "TYPE", False, datetime(2026, 10, 9)),
    )
    connections = install_database(monkeypatch, database)
    client = signed_in_client(user_id=10)

    first = client.patch("/api/notifications/1/read")
    again = client.patch("/api/notifications/1/read")

    assert first.status_code == again.status_code == 200
    assert first.json["notification"]["is_read"] is True
    assert database["notifications"][0][5] is True
    assert connections[-1].commits == 1


def test_notification_endpoints_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/notifications/unread-count").status_code == 401
    assert client.patch("/api/notifications/1/read").status_code == 401


def test_notification_read_is_private_and_invalid_ids_are_rejected(monkeypatch):
    database = make_database()
    database["notifications"].append(
        (1, 20, 4, "private", "TYPE", False, datetime(2026, 10, 9)),
    )
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=10)

    forbidden = client.patch("/api/notifications/1/read")
    missing = client.patch("/api/notifications/99/read")
    invalid = client.patch("/api/notifications/0/read")

    assert forbidden.status_code == missing.status_code == 404
    assert invalid.status_code == 400
    assert database["notifications"][0][5] is False


@pytest.mark.parametrize("query", ["?page=0", "?page=no", "?per_page=101", "?limit=1"])
def test_invalid_inbox_pagination_is_rejected(monkeypatch, query):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().get("/api/notifications" + query)

    assert response.status_code == 400


def test_nonempty_mark_read_body_is_rejected(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().patch("/api/notifications/1/read", json={"is_read": False})

    assert response.status_code == 400


def test_mark_read_service_hides_notifications_owned_by_other_users():
    database = make_database()
    cursor = NotificationCursor(database)
    with pytest.raises(NotificationError) as error:
        mark_notification_read(cursor, 1, 10)
    assert error.value.status_code == 404


def test_service_unread_count_returns_zero_without_rows():
    assert unread_count(NotificationCursor(make_database()), 10) == 0
