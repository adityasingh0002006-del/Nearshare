from copy import deepcopy
from datetime import datetime
from decimal import Decimal

import pytest

from config import create_app


class WorkflowCursor:
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
        fail_marker = self.database.get("fail_on")
        if fail_marker and fail_marker in query:
            raise RuntimeError("simulated database failure")

        if "SELECT request_id FROM dbo.Offers WHERE offer_id" in query:
            offer = self._offer(params[0])
            self.result = (offer[1],) if offer else None
        elif "SELECT requester_id, category_id, locality_id, max_budget, status" in query:
            target = self.database["requests"].get(params[0])
            if target:
                self.result = (
                    target["requester_id"], target["category_id"], target["locality_id"],
                    target["max_budget"], target["status"], target["start"], target["end"],
                )
        elif "SELECT request_id, item_id, owner_id, offer_type" in query:
            offer = self._offer(params[0])
            self.result = (offer[1], offer[2], offer[3], offer[4], offer[5], offer[6], offer[8]) if offer else None
        elif "SELECT i.owner_id, i.category_id, i.is_available" in query:
            item = self.database["items"].get(params[0])
            if item:
                self.result = (
                    item["owner_id"], item["category_id"], item["is_available"],
                    item["rental_price"], item["locality_id"], item["is_active"],
                )
        elif "SELECT 1 FROM dbo.Offers WITH" in query:
            request_id, selected_offer_id = params
            self.result = next((
                (1,) for offer in self.database["offers"]
                if offer[1] == request_id and offer[0] != selected_offer_id and offer[8] == "ACCEPTED"
            ), None)
        elif "SELECT DISTINCT owner_id" in query and "FROM dbo.Offers" in query:
            request_id, selected_offer_id = params
            self.results = [
                (owner_id,) for owner_id in sorted({
                    offer[3] for offer in self.database["offers"]
                    if offer[1] == request_id and offer[0] != selected_offer_id
                    and offer[8] == "PENDING"
                })
            ]
        elif "WHERE existing_offer.item_id = ?" in query:
            item_id, end_datetime, start_datetime = params
            offer_ids = {offer[0] for offer in self.database["offers"] if offer[2] == item_id}
            conflict = any(
                booking[1] in offer_ids and booking[7] != "CANCELLED"
                and booking[3] < end_datetime and booking[4] > start_datetime
                for booking in self.database["bookings"]
            )
            self.result = (1,) if conflict else None
        elif "UPDATE dbo.Offers SET status = ?" in query:
            status, offer_id, request_id = params
            offer = self._offer(offer_id)
            if offer and offer[1] == request_id and offer[8] == "PENDING":
                updated = (*offer[:8], status, offer[9])
                self.database["offers"][self.database["offers"].index(offer)] = updated
                self.result = updated
                self.rowcount = 1
        elif "INSERT INTO dbo.Bookings" in query:
            booking_id = self.database["next_booking_id"]
            self.database["next_booking_id"] += 1
            offer_id, borrower_id, start, end, price, deposit = params
            row = (booking_id, offer_id, borrower_id, start, end, price, deposit, "BOOKED", datetime(2026, 10, 10))
            self.database["bookings"].append(row)
            self.result = (booking_id,)
        elif "UPDATE dbo.Offers SET status = N'REJECTED'" in query:
            request_id, selected_offer_id = params
            updated_offers = []
            for offer in self.database["offers"]:
                if offer[1] == request_id and offer[0] != selected_offer_id and offer[8] == "PENDING":
                    offer = (*offer[:8], "REJECTED", offer[9])
                updated_offers.append(offer)
            self.database["offers"] = updated_offers
        elif "UPDATE dbo.Requests SET status = N'BOOKED'" in query:
            request_id, borrower_id = params
            target = self.database["requests"].get(request_id)
            if target and target["requester_id"] == borrower_id and target["status"] in {"OPEN", "MATCHED"}:
                target["status"] = "BOOKED"
                self.rowcount = 1
        elif "UPDATE dbo.Requests SET status = N'COMPLETED'" in query:
            request_id = params[0]
            target = self.database["requests"].get(request_id)
            if target and target["status"] == "BOOKED":
                target["status"] = "COMPLETED"
                self.result = (request_id,)
        elif "INSERT INTO dbo.Notifications" in query:
            self.database["notifications"].append(tuple(params))
        elif "INSERT INTO dbo.AuditLogs" in query:
            self.database["audits"].append(tuple(params))
        elif "SELECT b.booking_id, b.offer_id, b.borrower_id, o.owner_id," in query and "r.request_id, b.status" in query:
            booking = next((row for row in self.database["bookings"] if row[0] == params[0]), None)
            if booking:
                offer = self._offer(booking[1])
                request_row = self.database["requests"].get(offer[1]) if offer else None
                if offer and request_row:
                    self.result = (
                        booking[0], booking[1], booking[2], offer[3], offer[1],
                        booking[7], request_row["status"],
                    )
        elif "UPDATE dbo.Bookings SET status = ?" in query:
            target_status, booking_id, expected_status = params
            booking = next((row for row in self.database["bookings"] if row[0] == booking_id), None)
            if booking and booking[7] == expected_status:
                updated = (*booking[:7], target_status, booking[8])
                self.database["bookings"][self.database["bookings"].index(booking)] = updated
                self.result = (booking_id,)
                self.rowcount = 1
        elif "WHERE b.booking_id = ?" in query:
            booking = next((row for row in self.database["bookings"] if row[0] == params[0]), None)
            self.result = self._booking_join(booking) if booking else None
        elif "WHERE b.borrower_id = ? OR o.owner_id = ?" in query:
            user_id = params[0]
            self.results = []
            for booking in self.database["bookings"]:
                joined = self._booking_join(booking)
                if joined and user_id in {joined[2], joined[3]}:
                    self.results.append(joined)
        return self

    def _offer(self, offer_id):
        return next((offer for offer in self.database["offers"] if offer[0] == offer_id), None)

    def _booking_join(self, booking):
        offer = self._offer(booking[1])
        if not offer:
            return None
        request_row = self.database["requests"].get(offer[1])
        item = self.database["items"].get(offer[2])
        if not request_row or not item:
            return None
        return (
            booking[0], booking[1], booking[2], offer[3], offer[1], offer[2],
            item.get("item_name", "Item"), booking[3], booking[4], booking[5],
            booking[6], booking[7], booking[8],
        )

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.results)


class WorkflowConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = deepcopy(database)
        self.fake_cursor = WorkflowCursor(database)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.database.clear()
        self.database.update(deepcopy(self.snapshot))

    def close(self):
        pass


def make_database():
    created = datetime(2026, 10, 10)
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
                "is_available": True, "is_active": True,
                "rental_price": Decimal("40.00"), "item_name": "Cordless drill",
            },
            21: {
                "owner_id": 201, "category_id": 5, "locality_id": 9,
                "is_available": True, "is_active": True,
                "rental_price": Decimal("45.00"), "item_name": "Impact driver",
            },
        },
        "offers": [
            (1, 1, 20, 200, "RENTAL", Decimal("35.00"), Decimal("10.00"), None, "PENDING", created),
        ],
        "bookings": [],
        "notifications": [],
        "audits": [],
        "next_booking_id": 1,
        "fail_on": None,
    }


def seed_booking(database, status="BOOKED"):
    row = database["offers"][0]
    database["offers"][0] = (*row[:8], "ACCEPTED", row[9])
    database["requests"][1]["status"] = "BOOKED"
    database["bookings"].append((
        1, row[0], 100, datetime(2026, 11, 1), datetime(2026, 11, 3),
        Decimal("35.00"), Decimal("10.00"), status, datetime(2026, 10, 10),
    ))
    database["next_booking_id"] = 2


def signed_in_client(user_id=100):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def install_database(monkeypatch, database):
    connections = []

    def connect():
        connection = WorkflowConnection(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr("routes.offers.get_connection", connect)
    monkeypatch.setattr("routes.bookings.get_connection", connect)
    return connections


def test_accept_offer_creates_booking_and_updates_related_states(monkeypatch):
    database = make_database()
    database["offers"].append((2, 1, 21, 201, "RENTAL", Decimal("45.00"), Decimal("0.00"), None, "PENDING", datetime(2026, 10, 10)))
    connections = install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/accept")

    assert response.status_code == 200, response.json
    assert response.json["offer"]["status"] == "ACCEPTED"
    assert response.json["booking"]["status"] == "BOOKED"
    assert response.json["booking"]["request_id"] == 1
    assert "borrower_id" not in response.json["booking"]
    assert database["requests"][1]["status"] == "BOOKED"
    assert database["offers"][1][8] == "REJECTED"
    assert len(database["bookings"]) == 1
    assert len(database["notifications"]) == 2
    assert len(database["audits"]) == 1
    assert connections[-1].commits == 1


def test_reject_offer_changes_only_target_and_notifies_creator(monkeypatch):
    database = make_database()
    database["offers"].append((2, 1, 21, 201, "RENTAL", Decimal("45.00"), Decimal("0.00"), None, "PENDING", datetime(2026, 10, 10)))
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/reject")

    assert response.status_code == 200
    assert response.json["offer"]["status"] == "REJECTED"
    assert database["offers"][1][8] == "PENDING"
    assert database["requests"][1]["status"] == "OPEN"
    assert database["bookings"] == []
    assert database["notifications"] == [(200, 1, "Your offer 1 on request 1 was rejected.", "OFFER_REJECTED")]
    assert len(database["audits"]) == 1


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_only_request_owner_can_accept_or_reject(monkeypatch, action):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=300).post(f"/api/offers/1/{action}")

    assert response.status_code == 403
    assert database["offers"][0][8] == "PENDING"
    assert database["bookings"] == []


def test_decision_endpoints_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.post("/api/offers/1/accept").status_code == 401
    assert client.post("/api/offers/1/reject").status_code == 401


@pytest.mark.parametrize("offer_status", ["ACCEPTED", "REJECTED", "WITHDRAWN", "EXPIRED"])
def test_repeated_or_invalid_offer_decision_is_rejected(monkeypatch, offer_status):
    database = make_database()
    row = database["offers"][0]
    database["offers"][0] = (*row[:8], offer_status, row[9])
    install_database(monkeypatch, database)

    accepted = signed_in_client().post("/api/offers/1/accept")
    rejected = signed_in_client().post("/api/offers/1/reject")

    assert accepted.status_code == rejected.status_code == 409
    assert database["bookings"] == []


def test_competing_offer_acceptance_rejects_pending_siblings(monkeypatch):
    database = make_database()
    database["offers"].append((2, 1, 21, 201, "RENTAL", Decimal("45.00"), Decimal("0.00"), None, "PENDING", datetime(2026, 10, 10)))
    install_database(monkeypatch, database)

    accepted = signed_in_client().post("/api/offers/1/accept")
    second_accept = signed_in_client().post("/api/offers/2/accept")

    assert accepted.status_code == 200
    assert second_accept.status_code == 409
    assert [offer[8] for offer in database["offers"]] == ["ACCEPTED", "REJECTED"]
    assert len(database["bookings"]) == 1


def test_acceptance_rejects_overlapping_active_booking(monkeypatch):
    database = make_database()
    database["offers"].append((8, 88, 20, 200, "RENTAL", Decimal("35.00"), Decimal("0.00"), None, "ACCEPTED", datetime(2026, 10, 10)))
    database["bookings"].append((1, 8, 888, datetime(2026, 11, 2), datetime(2026, 11, 4), Decimal("35.00"), Decimal("0.00"), "BOOKED", datetime(2026, 10, 10)))
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/accept")

    assert response.status_code == 409
    assert database["offers"][0][8] == "PENDING"
    assert len(database["bookings"]) == 1


def test_adjacent_booking_interval_is_allowed(monkeypatch):
    database = make_database()
    database["offers"].append((8, 88, 20, 200, "RENTAL", Decimal("35.00"), Decimal("0.00"), None, "ACCEPTED", datetime(2026, 10, 10)))
    database["bookings"].append((1, 8, 888, datetime(2026, 10, 30), datetime(2026, 11, 1), Decimal("35.00"), Decimal("0.00"), "COMPLETED", datetime(2026, 10, 10)))
    database["next_booking_id"] = 2
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/accept")

    assert response.status_code == 200, response.json


@pytest.mark.parametrize("invalid", ["dates", "price"])
def test_invalid_booking_dates_or_prices_are_rejected(monkeypatch, invalid):
    database = make_database()
    if invalid == "dates":
        database["requests"][1]["end"] = datetime(2026, 10, 31)
    else:
        row = database["offers"][0]
        database["offers"][0] = (*row[:5], Decimal("60.00"), *row[6:])
    install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/accept")

    assert response.status_code == 409
    assert database["bookings"] == []
    assert database["offers"][0][8] == "PENDING"


def test_database_failure_rolls_back_booking_offer_request_notifications_and_audit(monkeypatch):
    database = make_database()
    database["offers"].append((2, 1, 21, 201, "RENTAL", Decimal("45.00"), Decimal("0.00"), None, "PENDING", datetime(2026, 10, 10)))
    database["fail_on"] = "UPDATE dbo.Requests SET status = N'BOOKED'"
    connections = install_database(monkeypatch, database)

    response = signed_in_client().post("/api/offers/1/accept")

    assert response.status_code == 503
    assert database["offers"][0][8] == "PENDING"
    assert database["offers"][1][8] == "PENDING"
    assert database["requests"][1]["status"] == "OPEN"
    assert database["bookings"] == []
    assert database["notifications"] == []
    assert database["audits"] == []
    assert connections[-1].rollbacks == 1


def test_booking_retrieval_is_limited_to_borrower_and_item_owner(monkeypatch):
    database = make_database()
    database["offers"][0] = (*database["offers"][0][:8], "ACCEPTED", database["offers"][0][9])
    database["requests"][1]["status"] = "BOOKED"
    database["bookings"].append((1, 1, 100, datetime(2026, 11, 1), datetime(2026, 11, 3), Decimal("35.00"), Decimal("10.00"), "BOOKED", datetime(2026, 10, 10)))
    install_database(monkeypatch, database)

    borrower = signed_in_client(user_id=100)
    owner = signed_in_client(user_id=200)
    outsider = signed_in_client(user_id=300)

    assert borrower.get("/api/bookings").json["bookings"][0]["booking_id"] == 1
    assert owner.get("/api/bookings/1").status_code == 200
    assert outsider.get("/api/bookings/1").status_code == 403
    assert outsider.get("/api/bookings").json["bookings"] == []


def test_booking_routes_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/bookings").status_code == 401
    assert client.get("/api/bookings/1").status_code == 401


def test_item_owner_can_confirm_handover(monkeypatch):
    database = make_database()
    seed_booking(database)
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/handover")

    assert response.status_code == 200
    assert response.json["booking"]["status"] == "HANDED_OVER"
    assert database["bookings"][0][7] == "HANDED_OVER"
    assert database["notifications"] == [(
        100, 1, "Booking 1 status changed to HANDED_OVER.", "BOOKING_HANDED_OVER",
    )]
    assert database["audits"][0][0:3] == (200, "BOOKING_HANDED_OVER", "1")


def test_borrower_cannot_confirm_handover_and_nonparticipant_is_denied(monkeypatch):
    database = make_database()
    seed_booking(database)
    install_database(monkeypatch, database)

    borrower = signed_in_client(user_id=100).post("/api/bookings/1/handover")
    outsider = signed_in_client(user_id=300).post("/api/bookings/1/handover")

    assert borrower.status_code == outsider.status_code == 403
    assert database["bookings"][0][7] == "BOOKED"


def test_handover_rejects_invalid_state_transition(monkeypatch):
    database = make_database()
    seed_booking(database, status="RETURNED")
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/handover")

    assert response.status_code == 409
    assert database["bookings"][0][7] == "RETURNED"


def test_borrower_can_return_handed_over_booking(monkeypatch):
    database = make_database()
    seed_booking(database, status="HANDED_OVER")
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=100).post("/api/bookings/1/return")

    assert response.status_code == 200
    assert response.json["booking"]["status"] == "RETURNED"
    assert database["notifications"] == [(
        200, 1, "Booking 1 status changed to RETURNED.", "BOOKING_RETURNED",
    )]


def test_item_owner_cannot_return_booking_for_borrower(monkeypatch):
    database = make_database()
    seed_booking(database, status="HANDED_OVER")
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/return")

    assert response.status_code == 403
    assert database["bookings"][0][7] == "HANDED_OVER"


def test_item_owner_completes_returned_booking_once(monkeypatch):
    database = make_database()
    seed_booking(database, status="RETURNED")
    install_database(monkeypatch, database)
    owner = signed_in_client(user_id=200)

    completed = owner.post("/api/bookings/1/complete")
    repeated = owner.post("/api/bookings/1/complete")

    assert completed.status_code == 200
    assert completed.json["booking"]["status"] == "COMPLETED"
    assert repeated.status_code == 409
    assert database["bookings"][0][7] == "COMPLETED"
    assert database["requests"][1]["status"] == "COMPLETED"
    assert database["items"][20]["is_available"] is True
    assert len(database["notifications"]) == 1
    assert database["notifications"][0][0] == 100


def test_completion_requires_return_and_owner_authorization(monkeypatch):
    database = make_database()
    seed_booking(database, status="HANDED_OVER")
    install_database(monkeypatch, database)

    wrong_actor = signed_in_client(user_id=100).post("/api/bookings/1/complete")
    wrong_state = signed_in_client(user_id=200).post("/api/bookings/1/complete")

    assert wrong_actor.status_code == 403
    assert wrong_state.status_code == 409
    assert database["bookings"][0][7] == "HANDED_OVER"


def test_completion_rejects_inconsistent_linked_request_state(monkeypatch):
    database = make_database()
    seed_booking(database, status="RETURNED")
    database["requests"][1]["status"] = "CANCELLED"
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/complete")

    assert response.status_code == 409
    assert database["bookings"][0][7] == "RETURNED"
    assert database["requests"][1]["status"] == "CANCELLED"


def test_transition_failure_rolls_back_status_notification_and_audit(monkeypatch):
    database = make_database()
    seed_booking(database)
    database["fail_on"] = "INSERT INTO dbo.Notifications"
    connections = install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/handover")

    assert response.status_code == 503
    assert database["bookings"][0][7] == "BOOKED"
    assert database["requests"][1]["status"] == "BOOKED"
    assert database["notifications"] == []
    assert database["audits"] == []
    assert connections[-1].rollbacks == 1


def test_completion_request_update_failure_rolls_back_booking_and_events(monkeypatch):
    database = make_database()
    seed_booking(database, status="RETURNED")
    database["fail_on"] = "UPDATE dbo.Requests SET status = N'COMPLETED'"
    connections = install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/complete")

    assert response.status_code == 503
    assert database["bookings"][0][7] == "RETURNED"
    assert database["requests"][1]["status"] == "BOOKED"
    assert database["notifications"] == []
    assert database["audits"] == []
    assert connections[-1].rollbacks == 1


def test_completion_event_failure_rolls_back_booking_and_request(monkeypatch):
    database = make_database()
    seed_booking(database, status="RETURNED")
    database["fail_on"] = "INSERT INTO dbo.Notifications"
    connections = install_database(monkeypatch, database)

    response = signed_in_client(user_id=200).post("/api/bookings/1/complete")

    assert response.status_code == 503
    assert database["bookings"][0][7] == "RETURNED"
    assert database["requests"][1]["status"] == "BOOKED"
    assert database["notifications"] == []
    assert database["audits"] == []
    assert connections[-1].rollbacks == 1


def test_cancelled_request_cannot_accept_an_offer(monkeypatch):
    database = make_database()
    database["requests"][1]["status"] = "CANCELLED"
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=100).post("/api/offers/1/accept")

    assert response.status_code == 409
    assert database["offers"][0][8] == "PENDING"
    assert database["bookings"] == []
    assert database["requests"][1]["status"] == "CANCELLED"
