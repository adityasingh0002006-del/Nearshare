from datetime import datetime
from decimal import Decimal

from config import create_app


class ResponseCursor:
    def __init__(self, *, locality_id=9, city="Kanpur", active=True, requester_id=11):
        self.locality_id = locality_id
        self.city = city
        self.active = active
        self.requester_id = requester_id
        self.response = None
        self.notifications = []
        self.result = None
        self.results = []
        self.queries = []

    def execute(self, query, *params):
        self.queries.append((query, params))
        self.result = None
        self.results = []
        if "FROM dbo.Requests AS r" in query and "response.response_status" in query:
            user_id, request_id = params
            eligible = (request_id == 41 and user_id != self.requester_id and self.active
                        and self.locality_id == 9 and self.city == "Kanpur")
            if eligible:
                self.result = (
                    41, "Cordless Drill", 3, "Tools", "Kanpur", "Kakadeo",
                    datetime(2026, 10, 10, 17, 35), datetime(2026, 10, 11, 16, 35),
                    Decimal("50.00"), "OPEN", "Aditya", self.response,
                )
        elif "SELECT response_status FROM dbo.RequestResponses" in query:
            self.result = (self.response,) if self.response else None
        elif "INSERT INTO dbo.RequestResponses" in query:
            self.response = params[2]
        elif "UPDATE dbo.RequestResponses SET response_status" in query:
            self.response = params[0]
        elif "UPDATE dbo.Notifications SET is_read" in query:
            self.notifications.append(params)
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.results


class ResponseConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def signed_in_client(user_id=22):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def install(monkeypatch, cursor):
    connections = []

    def connect():
        connection = ResponseConnection(cursor)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.requests.get_connection", connect)
    return connections


def test_locality_user_gets_private_safe_request_details(monkeypatch):
    cursor = ResponseCursor()
    install(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/41/response")

    assert response.status_code == 200
    request_data = response.json["request"]
    assert request_data["requester_name"] == "Aditya"
    assert request_data["title"] == "Cordless Drill"
    for private_field in ("email", "phone", "address", "requester_email", "requester_phone"):
        assert private_field not in request_data
    query = cursor.queries[0][0]
    assert "request_locality.locality_id = viewer_locality.locality_id" in query
    assert "request_locality.city = viewer_locality.city" in query
    assert "viewer.is_active = 1" in query
    assert "email" not in query.lower()
    assert "phone" not in query.lower()
    assert "address" not in query.lower()


def test_requester_wrong_locality_and_inactive_viewers_cannot_open_response(monkeypatch):
    for cursor, user_id in (
        (ResponseCursor(), 11),
        (ResponseCursor(locality_id=10), 22),
        (ResponseCursor(city="Lucknow"), 22),
        (ResponseCursor(active=False), 22),
    ):
        install(monkeypatch, cursor)
        response = signed_in_client(user_id).get("/api/requests/41/response")
        assert response.status_code == 404


def test_request_response_ignore_is_persisted_idempotently_and_marks_broadcast_read(monkeypatch):
    cursor = ResponseCursor()
    connections = install(monkeypatch, cursor)
    client = signed_in_client()

    first = client.post("/api/requests/41/response", json={"response_status": "IGNORED"})
    repeated = client.post("/api/requests/41/response", json={"response_status": "IGNORED"})

    assert first.status_code == repeated.status_code == 200
    assert first.json["response_status"] == "IGNORED"
    assert cursor.response == "IGNORED"
    assert len(cursor.notifications) == 2
    assert all("RequestResponses" in query for query, _ in cursor.queries if "INSERT INTO" in query)
    assert connections[-1].commits == 1


def test_ignored_request_cannot_be_reopened_or_offered_on(monkeypatch):
    cursor = ResponseCursor()
    cursor.response = "IGNORED"
    install(monkeypatch, cursor)

    response = signed_in_client().post(
        "/api/requests/41/response", json={"response_status": "INTERESTED"},
    )

    assert response.status_code == 409
    assert cursor.response == "IGNORED"


def test_response_endpoint_requires_authentication_and_valid_status():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/requests/41/response").status_code == 401
    assert client.post("/api/requests/41/response", json={"response_status": "OFFERED"}).status_code == 401
