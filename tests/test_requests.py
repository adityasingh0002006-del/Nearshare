from datetime import datetime

from config import create_app


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.results = []
        self.rowcount = -1
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        self.result = None
        self.results = []
        self.rowcount = -1
        if "SELECT 1 FROM dbo.Categories" in query:
            self.result = (1,) if params[0] in self.database["categories"] else None
        elif "SELECT 1 FROM dbo.Localities" in query:
            self.result = (1,) if params[0] in self.database["localities"] else None
        elif "FROM dbo.Requests WITH (UPDLOCK, HOLDLOCK)" in query and "item_description = ?" in query:
            requester_id, category_id, description, locality_id, start, end, budget = params
            self.result = next((row for row in self.database["requests"].values()
                                if row[1:8] == (requester_id, category_id, description,
                                                locality_id, start, end, budget)
                                and row[8] in {"OPEN", "MATCHED"}), None)
        elif "SELECT owner_id FROM dbo.Items WHERE item_id" in query:
            item = self.database["items"].get(params[0])
            self.result = (item["owner_id"],) if item else None
        elif "SELECT TOP (1) r.request_id" in query:
            item_id, requester_id = params
            item = self.database["items"].get(item_id)
            candidates = []
            for row in self.database["requests"].values():
                if not item or not item.get("is_available") or not item.get("is_active"):
                    continue
                if (row[1] == requester_id and row[1] != item["owner_id"]
                        and row[2] == item["category_id"]
                        and row[4] == item["locality_id"]
                        and row[8] in {"OPEN", "MATCHED"}
                        and item["rental_price"] <= row[7]
                        and not any(b[0] == item_id and b[3] != "CANCELLED"
                                    and b[1] < row[6] and b[2] > row[5]
                                    for b in self.database["bookings"])
                ):
                    candidates.append(row)
            candidates.sort(key=lambda row: (row[5], -row[0]), reverse=False)
            self.result = (candidates[0][0],) if candidates else None
        elif "INSERT INTO dbo.Requests" in query:
            request_id = self.database["next_id"]
            self.database["next_id"] += 1
            row = (request_id, *params[:7], "OPEN", datetime(2026, 10, 10))
            self.database["requests"][request_id] = row
            self.result = (request_id,)
        elif "UPDATE dbo.Requests SET status = N'CANCELLED'" in query:
            request_id, user_id = params
            row = self.database["requests"].get(request_id)
            if row and row[1] == user_id and row[8] in {"OPEN", "MATCHED"}:
                updated = (*row[:8], "CANCELLED", row[9])
                self.database["requests"][request_id] = updated
                self.result = updated
        elif "UPDATE dbo.Requests SET" in query:
            # Values are provided in the same order as the fixed SET field list.
            set_clause = query.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
            fields = [part.split(" = ")[0] for part in set_clause.split(", ")]
            request_id, user_id = params[len(fields)], params[len(fields) + 1]
            row = self.database["requests"].get(request_id)
            if row and row[1] == user_id and row[8] == "OPEN":
                indexes = {
                    "category_id": 2, "item_description": 3, "locality_id": 4,
                    "start_datetime": 5, "end_datetime": 6, "max_budget": 7,
                }
                updated = list(row)
                for field, value in zip(fields, params):
                    updated[indexes[field]] = value
                self.database["requests"][request_id] = tuple(updated)
                self.rowcount = 1
        elif "FROM dbo.Requests AS r" in query and "response.response_status" in query:
            user_id, request_id = params
            row = self.database["requests"].get(request_id)
            if row and row[1] == user_id:
                self.result = (
                    row[0], row[3], row[2], "Tools", "Kanpur", "Kakadeo",
                    row[5], row[6], row[7], row[8], "Aditya", None, 1,
                )
        elif "FROM dbo.Requests" in query:
            if "WHERE requester_id = ?" in query:
                user_id = params[0]
                self.results = [row for row in self.database["requests"].values()
                                if row[1] == user_id]
                self.results.sort(key=lambda row: row[0], reverse=True)
            elif "WHERE status IN" in query:
                user_id = params[0]
                self.results = [row for row in self.database["requests"].values()
                                if row[8] in {"OPEN", "MATCHED"} or row[1] == user_id]
                self.results.sort(key=lambda row: row[0], reverse=True)
            else:
                self.result = self.database["requests"].get(params[0])
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.results)


class FakeConnection:
    def __init__(self, database):
        self.fake_cursor = FakeCursor(database)
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


def make_database():
    return {
        "requests": {}, "categories": {4}, "localities": {9}, "items": {},
        "bookings": [], "next_id": 1,
    }


def signed_in_client(user_id=11):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def valid_payload():
    return {
        "category_id": 4,
        "item_description": "Need a cordless drill for a weekend project",
        "locality_id": 9,
        "start_datetime": "2026-11-10T09:00:00+05:30",
        "end_datetime": "2026-11-12T18:00:00+05:30",
        "max_budget": "125.50",
    }


def install_database(monkeypatch, database):
    connections = []

    def connect():
        connection = FakeConnection(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.requests.get_connection", connect)
    return connections


def test_create_request_persists_session_owner_and_utc_dates(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=11)

    response = client.post("/api/requests", json=valid_payload())

    assert response.status_code == 201
    request_data = response.json["request"]
    assert request_data["requester_id"] == 11
    assert request_data["category_id"] == 4
    assert request_data["locality_id"] == 9
    assert request_data["status"] == "OPEN"
    assert request_data["max_budget"] == "125.50"
    assert request_data["start_datetime"] == "2026-11-10T03:30:00"
    assert database["requests"][1][1] == 11


def test_successful_request_creation_notifies_locality_users(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    calls = []
    monkeypatch.setattr(
        "routes.requests.notify_locality_users_of_request",
        lambda cursor, request_id: calls.append((cursor, request_id)),
    )

    response = signed_in_client(user_id=11).post("/api/requests", json=valid_payload())

    assert response.status_code == 201
    assert len(calls) == 1
    assert calls[0][1] == response.json["request"]["request_id"]


def test_create_request_accepts_a_new_database_category(monkeypatch):
    database = make_database()
    database["categories"].add(13)
    install_database(monkeypatch, database)
    payload = valid_payload()
    payload["category_id"] = 13

    response = signed_in_client(user_id=11).post("/api/requests", json=payload)

    assert response.status_code == 201
    assert response.json["request"]["category_id"] == 13


def test_repeated_identical_request_returns_existing_active_request(monkeypatch):
    database = make_database()
    connections = install_database(monkeypatch, database)
    notifications = []
    monkeypatch.setattr(
        "routes.requests.notify_locality_users_of_request",
        lambda cursor, request_id: notifications.append(request_id),
    )
    client = signed_in_client(user_id=11)

    first = client.post("/api/requests", json=valid_payload())
    repeated = client.post("/api/requests", json=valid_payload())

    assert first.status_code == 201
    assert repeated.status_code == 200
    assert repeated.json["already_exists"] is True
    assert repeated.json["request"]["request_id"] == first.json["request"]["request_id"]
    assert len(database["requests"]) == 1
    assert notifications == [1, 1]
    assert connections[-1].commits == 1
    assert connections[-1].rollbacks == 0


def test_requests_for_different_dates_remain_distinct(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=11)
    first = client.post("/api/requests", json=valid_payload())
    later_dates = valid_payload()
    later_dates["start_datetime"] = "2026-11-20T09:00:00+05:30"
    later_dates["end_datetime"] = "2026-11-22T18:00:00+05:30"

    second = client.post("/api/requests", json=later_dates)

    assert first.status_code == second.status_code == 201
    assert first.json["request"]["request_id"] != second.json["request"]["request_id"]
    assert len(database["requests"]) == 2


def test_matching_item_endpoint_selects_only_an_eligible_owned_request(monkeypatch):
    database = make_database()
    database["items"][20] = {
        "owner_id": 200, "category_id": 4, "locality_id": 9,
        "is_available": True, "is_active": True, "rental_price": 40,
    }
    connections = install_database(monkeypatch, database)
    client = signed_in_client(user_id=11)

    unrelated = valid_payload()
    unrelated["locality_id"] = 9
    client.post("/api/requests", json=unrelated)
    database["requests"][1] = (*database["requests"][1][:2], 5, *database["requests"][1][3:])
    suitable = valid_payload()
    suitable["item_description"] = "A drill for this weekend"
    created = client.post("/api/requests", json=suitable)

    response = client.get("/api/requests/matching-item/20")

    assert response.status_code == 200
    assert response.json == {
        "request_id": created.json["request"]["request_id"], "owns_item": False,
    }
    match_query = next(query for connection in connections
                       for query, _ in connection.fake_cursor.executed
                       if "SELECT TOP (1) r.request_id" in query)
    assert "request_locality.city = item_locality.city" in match_query


def test_matching_item_endpoint_prefers_the_soonest_matching_request(monkeypatch):
    database = make_database()
    database["items"][20] = {
        "owner_id": 200, "category_id": 4, "locality_id": 9,
        "is_available": True, "is_active": True, "rental_price": 40,
    }
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=11)
    farther = valid_payload()
    farther["start_datetime"] = "2026-11-20T09:00:00+05:30"
    farther["end_datetime"] = "2026-11-22T18:00:00+05:30"
    client.post("/api/requests", json=farther)
    sooner = valid_payload()
    sooner["start_datetime"] = "2026-11-05T09:00:00+05:30"
    sooner["end_datetime"] = "2026-11-07T18:00:00+05:30"
    selected = client.post("/api/requests", json=sooner)

    response = client.get("/api/requests/matching-item/20")

    assert response.json["request_id"] == selected.json["request"]["request_id"]


def test_matching_item_endpoint_respects_item_availability_budget_and_booking_dates(monkeypatch):
    database = make_database()
    database["items"][20] = {
        "owner_id": 200, "category_id": 4, "locality_id": 9,
        "is_available": True, "is_active": True, "rental_price": 40,
    }
    install_database(monkeypatch, database)
    client = signed_in_client(user_id=11)
    created = client.post("/api/requests", json=valid_payload())
    request_id = created.json["request"]["request_id"]

    assert client.get("/api/requests/matching-item/20").json["request_id"] == request_id
    database["items"][20]["rental_price"] = 200
    assert client.get("/api/requests/matching-item/20").json["request_id"] is None
    database["items"][20]["rental_price"] = 40
    database["items"][20]["is_available"] = False
    assert client.get("/api/requests/matching-item/20").json["request_id"] is None
    database["items"][20]["is_available"] = True
    database["bookings"].append((20, database["requests"][request_id][5],
                                  database["requests"][request_id][6], "BOOKED"))
    assert client.get("/api/requests/matching-item/20").json["request_id"] is None


def test_matching_item_endpoint_blocks_self_borrow_and_requires_authentication(monkeypatch):
    database = make_database()
    database["items"][20] = {"owner_id": 11}
    install_database(monkeypatch, database)

    owner = signed_in_client(user_id=11).get("/api/requests/matching-item/20")
    anonymous = create_app({"TESTING": True}).test_client().get("/api/requests/matching-item/20")

    assert owner.status_code == 200
    assert owner.json == {"request_id": None, "owns_item": True}
    assert anonymous.status_code == 401


def test_list_and_detail_requests_are_authenticated_and_readable(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client()
    created = client.post("/api/requests", json=valid_payload())
    request_id = created.json["request"]["request_id"]

    listed = client.get("/api/requests")
    detail = client.get(f"/api/requests/{request_id}")
    assert listed.status_code == detail.status_code == 200
    assert listed.json["requests"][0]["request_id"] == request_id
    assert detail.json["request"]["item_description"] == valid_payload()["item_description"]
    assert detail.json["request"]["is_requester"] is True


def test_my_requests_api_only_returns_the_authenticated_users_requests(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    requester_a = signed_in_client(user_id=11)
    requester_b = signed_in_client(user_id=22)
    own_a = requester_a.post("/api/requests", json=valid_payload()).json["request"]["request_id"]
    own_b_payload = valid_payload()
    own_b_payload["item_description"] = "Need a camera"
    own_b = requester_b.post("/api/requests", json=own_b_payload).json["request"]["request_id"]

    requests_a = requester_a.get("/api/requests").json["requests"]
    requests_b = requester_b.get("/api/requests").json["requests"]
    tampered_b = requester_b.get("/api/requests?requester_id=11").json["requests"]

    assert [row["request_id"] for row in requests_a] == [own_a]
    assert [row["request_id"] for row in requests_b] == [own_b]
    assert [row["request_id"] for row in tampered_b] == [own_b]


def test_direct_request_detail_is_not_visible_to_an_unrelated_user(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    request_id = signed_in_client(user_id=11).post(
        "/api/requests", json=valid_payload(),
    ).json["request"]["request_id"]

    response = signed_in_client(user_id=22).get(f"/api/requests/{request_id}")

    assert response.status_code == 404


def test_update_and_cancel_request_by_owner(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client()
    created = client.post("/api/requests", json=valid_payload())
    request_id = created.json["request"]["request_id"]

    updated = client.patch(f"/api/requests/{request_id}", json={"max_budget": "200.00"})
    cancelled = client.post(f"/api/requests/{request_id}/cancel")

    assert updated.status_code == 200
    assert updated.json["request"]["max_budget"] == "200.00"
    assert cancelled.status_code == 200
    assert cancelled.json["request"]["status"] == "CANCELLED"
    assert database["requests"][request_id][8] == "CANCELLED"


def test_request_endpoints_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/requests").status_code == 401
    assert client.get("/api/requests/1").status_code == 401
    assert client.post("/api/requests", json=valid_payload()).status_code == 401


def test_invalid_request_input_is_rejected_before_database(monkeypatch):
    monkeypatch.setattr(
        "routes.requests.get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")),
    )
    client = signed_in_client()
    payload = valid_payload()
    payload["max_budget"] = -1
    assert client.post("/api/requests", json=payload).status_code == 400

    payload = valid_payload()
    payload["end_datetime"] = "2026-11-10T09:00:00+05:30"
    assert client.post("/api/requests", json=payload).status_code == 400

    payload = valid_payload()
    payload["status"] = "BOOKED"
    assert client.post("/api/requests", json=payload).status_code == 400


def test_missing_reference_is_rejected(monkeypatch):
    database = make_database()
    database["categories"].clear()
    install_database(monkeypatch, database)
    response = signed_in_client().post("/api/requests", json=valid_payload())
    assert response.status_code == 400
    assert "category_id" in response.json["error"]


def test_missing_locality_reference_is_rejected(monkeypatch):
    database = make_database()
    database["localities"].clear()
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/requests", json=valid_payload())

    assert response.status_code == 400
    assert "locality_id" in response.json["error"]


def test_non_owner_cannot_update_or_cancel_request(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    owner = signed_in_client(user_id=11)
    request_id = owner.post("/api/requests", json=valid_payload()).json["request"]["request_id"]
    other_user = signed_in_client(user_id=22)

    updated = other_user.patch(f"/api/requests/{request_id}", json={"max_budget": "300"})
    cancelled = other_user.post(f"/api/requests/{request_id}/cancel")
    assert updated.status_code == cancelled.status_code == 403
    assert database["requests"][request_id][1] == 11


def test_only_open_requests_can_be_updated_and_cancelled(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client()
    request_id = client.post("/api/requests", json=valid_payload()).json["request"]["request_id"]
    database["requests"][request_id] = (*database["requests"][request_id][:8], "BOOKED", database["requests"][request_id][9])

    updated = client.patch(f"/api/requests/{request_id}", json={"max_budget": "300"})
    cancelled = client.post(f"/api/requests/{request_id}/cancel")
    assert updated.status_code == cancelled.status_code == 409
