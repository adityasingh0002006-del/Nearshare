from datetime import datetime
from decimal import Decimal

import pytest

from config import create_app


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.results = []
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        self.result = None
        self.results = []

        if "SELECT requester_id, category_id, locality_id, max_budget, status" in query:
            request_row = self.database["requests"].get(params[0])
            if request_row:
                self.result = (
                    request_row["requester_id"], request_row["category_id"],
                    request_row["locality_id"], request_row["max_budget"],
                    request_row["status"], request_row["start"], request_row["end"],
                )
        elif "SELECT i.owner_id, i.category_id, i.is_available" in query:
            item = self.database["items"].get(params[0])
            if item:
                self.result = (
                    item["owner_id"], item["category_id"], item["is_available"],
                    item["rental_price"], item["locality_id"], item["is_active"],
                )
        elif "SELECT response_status FROM dbo.RequestResponses" in query:
            status = self.database["responses"].get((params[0], params[1]))
            self.result = (status,) if status else None
        elif "INSERT INTO dbo.RequestResponses" in query:
            self.database["responses"][(params[0], params[1])] = params[2] if len(params) > 2 else "OFFERED"
        elif "UPDATE dbo.RequestResponses SET response_status" in query:
            self.database["responses"][(params[1], params[2])] = "OFFERED"
        elif "WHERE booked_offer.item_id = ?" in query:
            item_id, request_end, request_start = params
            offer_ids = {offer[0] for offer in self.database["offers"] if offer[2] == item_id}
            booking_exists = any(
                booking[0] in offer_ids and booking[3] != "CANCELLED"
                and booking[1] < request_end and booking[2] > request_start
                for booking in self.database["bookings"]
            )
            self.result = (1,) if booking_exists else None
        elif "SELECT 1 FROM dbo.Offers WITH" in query:
            request_id, item_id = params
            duplicate = any(
                offer[1] == request_id and offer[2] == item_id
                and offer[8] in {"PENDING", "ACCEPTED"}
                for offer in self.database["offers"]
            )
            self.result = (1,) if duplicate else None
        elif "INSERT INTO dbo.Offers" in query:
            offer_id = self.database["next_offer_id"]
            self.database["next_offer_id"] += 1
            row = (offer_id, *params, "PENDING", datetime(2026, 10, 10))
            self.database["offers"].append(row)
            self.result = row
        elif "SELECT requester_id FROM dbo.Requests" in query:
            target = self.database["requests"].get(params[0])
            if target:
                self.result = (target["requester_id"],)
        elif "OR r.requester_id = ?" in query:
            viewer_id = params[0]
            self.results = [
                (*offer, self.database["requests"][offer[1]]["requester_id"])
                for offer in self.database["offers"]
                if offer[3] == viewer_id
                or self.database["requests"][offer[1]]["requester_id"] == viewer_id
            ]
        elif "FROM dbo.Offers AS o" in query and "INNER JOIN dbo.Requests" in query:
            offer = self._offer(params[0])
            if offer:
                self.result = (*offer, self.database["requests"][offer[1]]["requester_id"])
        elif "FROM dbo.Items AS item" in query and "owner.full_name" in query:
            item = self.database["items"].get(params[1])
            if item:
                self.result = (
                    item.get("owner_name", "Surya"), item.get("item_name", "Cordless Drill"),
                    "Tools", self.database["requests"][params[0]]["start"],
                    self.database["requests"][params[0]]["end"], item.get("image_url"),
                )
        elif "SELECT o.owner_id, o.status" in query:
            offer = self._offer(params[0])
            if offer:
                has_booking = any(booking[0] == offer[0] for booking in self.database["bookings"])
                self.result = (offer[3], offer[8], int(has_booking))
        elif "UPDATE dbo.Offers SET status = N'WITHDRAWN'" in query:
            offer_id, user_id, _booking_offer_id = params
            offer = self._offer(offer_id)
            has_booking = any(booking[0] == offer_id for booking in self.database["bookings"])
            if offer and offer[3] == user_id and offer[8] == "PENDING" and not has_booking:
                row = (*offer[:8], "WITHDRAWN", offer[9])
                self.database["offers"][self.database["offers"].index(offer)] = row
                self.result = row
        elif "FROM dbo.Offers" in query:
            if "WHERE owner_id = ?" in query:
                owner_id = params[0]
                self.results = [offer for offer in self.database["offers"] if offer[3] == owner_id]
            elif "WHERE request_id = ? AND owner_id = ?" in query:
                request_id, owner_id = params
                self.results = [offer for offer in self.database["offers"]
                                if offer[1] == request_id and offer[3] == owner_id]
            elif "WHERE request_id = ?" in query:
                request_id = params[0]
                self.results = [offer for offer in self.database["offers"] if offer[1] == request_id]
            else:
                offer = self._offer(params[0])
                self.result = offer
        return self

    def _offer(self, offer_id):
        return next((offer for offer in self.database["offers"] if offer[0] == offer_id), None)

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
        "requests": {
            1: {
                "requester_id": 100, "category_id": 5, "locality_id": 9,
                "max_budget": Decimal("50.00"), "status": "OPEN",
                "start": datetime(2026, 11, 1), "end": datetime(2026, 11, 3),
            },
        },
        "items": {
            20: {
                "owner_id": 200, "category_id": 5, "locality_id": 9,
                "is_active": True, "is_available": True,
                "rental_price": Decimal("40.00"),
            "owner_name": "Surya", "item_name": "Cordless Drill",
            "image_url": "/api/items/images/8",
            },
        },
        "offers": [],
        "bookings": [],
        "responses": {},
        "next_offer_id": 1,
    }


def signed_in_client(user_id=200):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def install_database(monkeypatch, database):
    connections = []

    def connect():
        connection = FakeConnection(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.offers.get_connection", connect)
    return connections


def offer_payload(**changes):
    payload = {"item_id": 20, "offer_type": "RENTAL", "offered_price": "35.00"}
    payload.update(changes)
    return payload


def create_test_offer(client, database, **payload_changes):
    response = client.post("/api/requests/1/offers", json=offer_payload(**payload_changes))
    assert response.status_code == 201
    return response.json["offer"]


def test_create_offer_uses_session_owner_and_schema_supported_fields(monkeypatch):
    database = make_database()
    connections = install_database(monkeypatch, database)

    response = signed_in_client().post("/api/requests/1/offers", json=offer_payload(
        security_deposit="10.00", message="Can lend this weekend", owner_id=999,
    ))

    assert response.status_code == 400  # owner_id is not accepted from the client
    success = signed_in_client().post("/api/requests/1/offers", json=offer_payload(
        security_deposit="10.00", message=" Can lend this weekend ",
    ))
    assert success.status_code == 201
    offer = success.json["offer"]
    assert database["offers"][0][3] == 200
    assert "owner_id" not in offer
    assert offer["status"] == "PENDING"
    assert offer["offered_price"] == "35.00"
    assert offer["security_deposit"] == "10.00"
    assert offer["message"] == "Can lend this weekend"
    assert connections[-1].commits == 1
    assert database["responses"][(1, 200)] == "OFFERED"


def test_user_who_ignored_request_cannot_make_an_offer(monkeypatch):
    database = make_database()
    database["responses"][(1, 200)] = "IGNORED"
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/requests/1/offers", json=offer_payload())

    assert response.status_code == 409
    assert response.json == {"error": "You ignored this request"}
    assert database["offers"] == []


@pytest.mark.parametrize(
    ("request_id", "item_id", "expected_status"),
    [(999, 20, 404), (1, 999, 404)],
)
def test_invalid_request_and_item_ids_return_not_found(monkeypatch, request_id, item_id, expected_status):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().post(
        f"/api/requests/{request_id}/offers", json=offer_payload(item_id=item_id),
    )

    assert response.status_code == expected_status
    assert not database["offers"]


@pytest.mark.parametrize(
    ("change", "expected_error"),
    [
        ({"category_id": 6}, "category and locality"),
        ({"locality_id": 10}, "category and locality"),
        ({"is_available": False}, "unavailable"),
        ({"rental_price": Decimal("60.00")}, "budget"),
        ({"is_active": False}, "inactive"),
    ],
)
def test_ineligible_item_cannot_receive_offer(monkeypatch, change, expected_error):
    database = make_database()
    database["items"][20].update(change)
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/requests/1/offers", json=offer_payload())

    assert response.status_code == 409
    assert expected_error in response.json["error"]
    assert not database["offers"]


def test_offer_price_must_fit_request_budget(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().post(
        "/api/requests/1/offers", json=offer_payload(offered_price="51.00"),
    )

    assert response.status_code == 409
    assert "budget" in response.json["error"]


def test_offer_creation_validates_money_type_and_message(monkeypatch):
    monkeypatch.setattr(
        "routes.offers.get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")),
    )
    client = signed_in_client()
    invalid_payloads = [
        offer_payload(offered_price=-1),
        offer_payload(security_deposit="1.001"),
        offer_payload(offer_type="FREE_LENDING", offered_price="1.00"),
        offer_payload(offer_type="BUY"),
        offer_payload(message="x" * 1001),
    ]
    for payload in invalid_payloads:
        assert client.post("/api/requests/1/offers", json=payload).status_code == 400


def test_request_owner_cannot_offer_on_own_request_and_item_owner_is_enforced(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    self_dealing = signed_in_client(user_id=100).post(
        "/api/requests/1/offers", json=offer_payload(),
    )
    item_not_owned = signed_in_client(user_id=201).post(
        "/api/requests/1/offers", json=offer_payload(),
    )
    assert self_dealing.status_code == 403
    assert self_dealing.json == {"error": "Only the item owner can make an offer"}
    assert item_not_owned.status_code == 403
    assert item_not_owned.json == {"error": "Only the item owner can make an offer"}
    assert not database["offers"]


def test_user_who_owns_neither_request_nor_item_is_forbidden(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=300).post(
        "/api/requests/1/offers", json=offer_payload(),
    )

    assert response.status_code == 403
    assert response.json == {"error": "Only the item owner can make an offer"}
    assert not database["offers"]


def test_owner_cannot_offer_an_item_that_does_not_match_request(monkeypatch):
    database = make_database()
    database["requests"][1]["category_id"] = 6
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post(
        "/api/requests/1/offers", json=offer_payload(),
    )

    assert response.status_code == 409
    assert "match" in response.json["error"] or "category" in response.json["error"]
    assert not database["offers"]


def test_offer_creation_requires_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.post("/api/requests/1/offers", json=offer_payload()).status_code == 401


def test_offer_read_and_withdraw_routes_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/offers").status_code == 401
    assert client.get("/api/requests/1/offers").status_code == 401
    assert client.get("/api/offers/1").status_code == 401
    assert client.post("/api/offers/1/withdraw").status_code == 401


def test_request_owner_and_offer_creator_have_scoped_offer_lists(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    creator = signed_in_client(user_id=200)
    first = create_test_offer(creator, database)
    database["offers"].append((2, 1, 21, 201, "RENTAL", Decimal("30.00"), Decimal("0.00"), None, "PENDING", datetime(2026, 10, 9)))

    owner_list = signed_in_client(user_id=100).get("/api/requests/1/offers")
    creator_list = creator.get("/api/requests/1/offers")
    own_list = creator.get("/api/offers")
    unrelated = signed_in_client(user_id=300).get("/api/requests/1/offers")

    assert len(owner_list.json["offers"]) == 2
    assert owner_list.json["can_decide"] is True
    assert [offer["offer_id"] for offer in creator_list.json["offers"]] == [first["offer_id"]]
    assert creator_list.json["can_decide"] is False
    assert creator_list.json["offers"][0]["can_withdraw"] is True
    assert owner_list.json["offers"][0]["can_decide"] is True
    assert [offer["offer_id"] for offer in own_list.json["offers"]] == [first["offer_id"]]
    assert unrelated.status_code == 403


def test_global_offers_page_lists_received_offers_with_borrower_actions(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    create_test_offer(signed_in_client(user_id=200), database)

    received = signed_in_client(user_id=100).get("/api/offers")
    created = signed_in_client(user_id=200).get("/api/offers")

    assert len(received.json["offers"]) == 1
    assert received.json["offers"][0]["can_decide"] is True
    assert received.json["offers"][0]["can_withdraw"] is False
    assert created.json["offers"][0]["can_decide"] is False
    assert created.json["offers"][0]["can_withdraw"] is True


def test_offer_detail_is_limited_to_request_owner_and_offer_creator(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    offer = create_test_offer(signed_in_client(), database)

    detail = signed_in_client(user_id=100).get(f"/api/offers/{offer['offer_id']}")
    assert detail.status_code == 200
    assert detail.json["offer"]["owner_name"] == "Surya"
    assert detail.json["offer"]["item_name"] == "Cordless Drill"
    assert detail.json["offer"]["category_name"] == "Tools"
    assert detail.json["offer"]["image_url"] == "/api/items/images/8"
    assert detail.json["offer"]["can_decide"] is True
    owner_detail = signed_in_client(user_id=200).get(f"/api/offers/{offer['offer_id']}")
    assert owner_detail.status_code == 200
    assert owner_detail.json["offer"]["can_decide"] is False
    assert signed_in_client(user_id=300).get(f"/api/offers/{offer['offer_id']}").status_code == 403
    assert signed_in_client().get("/api/offers/999").status_code == 404


def test_offer_creator_can_withdraw_pending_offer(monkeypatch):
    database = make_database()
    connections = install_database(monkeypatch, database)
    offer = create_test_offer(signed_in_client(), database)

    response = signed_in_client().post(f"/api/offers/{offer['offer_id']}/withdraw")

    assert response.status_code == 200
    assert response.json["offer"]["status"] == "WITHDRAWN"
    assert connections[-1].commits == 1


@pytest.mark.parametrize("status,has_booking", [("ACCEPTED", False), ("PENDING", True), ("REJECTED", False)])
def test_offer_cannot_be_withdrawn_after_invalid_state_or_booking(monkeypatch, status, has_booking):
    database = make_database()
    install_database(monkeypatch, database)
    offer = create_test_offer(signed_in_client(), database)
    stored = database["offers"][0]
    database["offers"][0] = (*stored[:8], status, stored[9])
    if has_booking:
        database["bookings"].append((offer["offer_id"], datetime(2026, 11, 1), datetime(2026, 11, 3), "BOOKED"))

    response = signed_in_client().post(f"/api/offers/{offer['offer_id']}/withdraw")

    assert response.status_code == 409


def test_non_creator_cannot_withdraw_offer(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    offer = create_test_offer(signed_in_client(), database)

    response = signed_in_client(user_id=201).post(f"/api/offers/{offer['offer_id']}/withdraw")

    assert response.status_code == 403


def test_duplicate_active_offer_is_rejected_but_inactive_offer_can_be_replaced(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client()
    original = create_test_offer(client, database)

    duplicate = client.post("/api/requests/1/offers", json=offer_payload())
    assert duplicate.status_code == 409

    row = database["offers"][0]
    database["offers"][0] = (*row[:8], "WITHDRAWN", row[9])
    replacement = client.post("/api/requests/1/offers", json=offer_payload())
    assert replacement.status_code == 201
    assert replacement.json["offer"]["offer_id"] != original["offer_id"]


def test_offer_is_rejected_when_item_has_overlapping_booking(monkeypatch):
    database = make_database()
    database["offers"].append((1, 77, 20, 200, "RENTAL", Decimal("30.00"), Decimal("0.00"), None, "ACCEPTED", datetime(2026, 10, 9)))
    database["bookings"].append((1, datetime(2026, 11, 2), datetime(2026, 11, 4), "BOOKED"))
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/requests/1/offers", json=offer_payload())

    assert response.status_code == 409
    assert "overlaps" in response.json["error"]
    assert len(database["offers"]) == 1
