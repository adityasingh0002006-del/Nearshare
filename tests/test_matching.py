from decimal import Decimal

from config import create_app
from services.matching_service import find_matches


class MatchingCursor:
    def __init__(self, request_owner=7, request_status="OPEN", matches=None):
        self.request_owner = request_owner
        self.request_status = request_status
        self.matches = matches or []
        self.result = None
        self.rows = []
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        if "SELECT requester_id, status FROM dbo.Requests" in query:
            self.result = (self.request_owner, self.request_status)
        elif "FROM dbo.Requests AS r" in query:
            self.rows = list(self.matches)
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.rows)


class MatchingConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self):
        return self.fake_cursor

    def close(self):
        pass


def signed_in_client(user_id=7):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def sample_match():
    return (31, 4, "Tools", "Cordless drill", "18V drill", "GOOD", Decimal("25.00"), Decimal("50.00"), 200)


def install_cursor(monkeypatch, cursor):
    monkeypatch.setattr("routes.requests.get_connection", lambda: MatchingConnection(cursor))


def test_request_owner_receives_matching_item_without_private_owner_data(monkeypatch):
    cursor = MatchingCursor(matches=[sample_match()])
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/12/matches")

    assert response.status_code == 200
    assert response.json == {
        "request_id": 12,
        "is_requester": True,
        "matches": [{
            "item_id": 31,
            "category_id": 4,
            "category_name": "Tools",
            "item_name": "Cordless drill",
            "description": "18V drill",
            "condition": "GOOD",
            "rental_price": "25.00",
            "security_deposit": "50.00",
            "is_item_owner": False,
        }],
    }
    assert cursor.executed[1][1] == (12,)
    assert "email" not in response.json["matches"][0]
    assert "phone" not in response.json["matches"][0]
    assert "owner_id" not in response.json["matches"][0]


def test_matching_returns_empty_list_when_no_candidates(monkeypatch):
    cursor = MatchingCursor(matches=[])
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/12/matches")

    assert response.status_code == 200
    assert response.json == {"request_id": 12, "is_requester": True, "matches": []}


def test_matching_sql_applies_schema_supported_eligibility_filters():
    class QueryCursor:
        def __init__(self):
            self.query = None
            self.params = None

        def execute(self, query, *params):
            self.query = query
            self.params = params

        def fetchall(self):
            return []

    cursor = QueryCursor()
    assert find_matches(cursor, 12) == []
    query = cursor.query
    select_clause = query.split("FROM dbo.Requests", 1)[0]
    assert "i.category_id = r.category_id" in query
    assert "item_locality.locality_id = r.locality_id" in query
    assert "request_locality.city = item_locality.city" in query
    assert "i.is_available = 1" in query
    assert "item_owner.is_active = 1" in query
    assert "i.owner_id <> r.requester_id" in query
    assert "i.rental_price <= r.max_budget" in query
    assert "b.start_datetime < r.end_datetime" in query
    assert "b.end_datetime > r.start_datetime" in query
    assert "b.status <> N'CANCELLED'" in query
    assert "owner_name" not in select_clause
    assert "email" not in select_clause
    assert "phone" not in select_clause
    assert cursor.params == (12,)


def test_kanpur_request_cannot_match_lucknow_item_even_with_same_category():
    class QueryCursor:
        def execute(self, query, *params):
            self.query, self.params = query, params

        def fetchall(self):
            # The query joins both locality rows and requires the cities to match;
            # equal categories alone cannot make two different city rows qualify.
            return []

    cursor = QueryCursor()
    assert find_matches(cursor, 41) == []
    assert "item_locality.locality_id = r.locality_id" in cursor.query
    assert "request_locality.city = item_locality.city" in cursor.query


def test_matching_requires_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/requests/12/matches").status_code == 401


def test_matching_is_restricted_to_request_owner(monkeypatch):
    cursor = MatchingCursor(request_owner=7)
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=8).get("/api/requests/12/matches")

    assert response.status_code == 403
    assert len(cursor.executed) == 2


def test_matched_item_owner_can_view_their_match_and_make_offer(monkeypatch):
    cursor = MatchingCursor(request_owner=7, matches=[sample_match()])
    install_cursor(monkeypatch, cursor)

    response = signed_in_client(user_id=200).get("/api/requests/12/matches")

    assert response.status_code == 200
    assert response.json["is_requester"] is False
    assert len(response.json["matches"]) == 1
    assert response.json["matches"][0]["is_item_owner"] is True


def test_matching_rejects_unsupported_filters(monkeypatch):
    cursor = MatchingCursor()
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/12/matches?radius_km=5")

    assert response.status_code == 400
    assert cursor.executed == []


def test_matching_only_accepts_open_or_matched_requests(monkeypatch):
    cursor = MatchingCursor(request_status="CANCELLED")
    install_cursor(monkeypatch, cursor)

    response = signed_in_client().get("/api/requests/12/matches")

    assert response.status_code == 409
    assert len(cursor.executed) == 1
