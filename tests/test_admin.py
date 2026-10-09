"""Admin authorization, account management, and audit tests."""

from copy import deepcopy
from datetime import datetime

import pytest

from config import create_app


class AdminCursor:
    def __init__(self, database):
        self.database = database
        self.result = None
        self.results = []

    def execute(self, query, *params):
        self.result = None
        self.results = []
        if "SELECT role, is_active FROM dbo.Users" in query:
            user = self.database["users"].get(params[0])
            self.result = (user["role"], user["is_active"]) if user else None
        elif "AS users_total" in query:
            users = list(self.database["users"].values())
            self.result = (
                len(users),
                sum(1 for user in users if user["is_active"]),
                self.database["summary"]["requests"],
                self.database["summary"]["offers"],
                self.database["summary"]["bookings"],
                self.database["summary"]["reviews"],
            )
        elif "SELECT COUNT(*) FROM dbo.Users WITH" in query:
            self.result = (sum(1 for user in self.database["users"].values()
                               if user["role"] == "ADMIN" and user["is_active"]),)
        elif "SELECT COUNT(*) FROM dbo.Users" in query:
            filtered = self._filtered_users(query, params)
            self.result = (len(filtered),)
        elif "FROM dbo.Users" in query and "OFFSET ? ROWS FETCH NEXT ? ROWS ONLY" in query:
            filter_params = params[:-2]
            offset, limit = params[-2:]
            users = self._filtered_users(query, filter_params)
            users.sort(key=lambda u: (u["created_at"], u["user_id"]), reverse=True)
            self.results = [self._row(user) for user in users[offset:offset + limit]]
        elif "UPDATE dbo.Users SET is_active" in query:
            desired, user_id, current = params
            user = self.database["users"].get(user_id)
            if user and user["is_active"] == current:
                user["is_active"] = desired
                user["updated_at"] = datetime(2026, 10, 10)
                self.result = (user_id, desired)
        elif "INSERT INTO dbo.AuditLogs" in query:
            if self.database["fail_audit"]:
                raise RuntimeError("simulated audit failure")
            self.database["audit"].append(tuple(params))
        return self

    def _filtered_users(self, query, params):
        users = list(self.database["users"].values())
        param_index = 0
        if "is_active = ?" in query:
            active = params[param_index]
            param_index += 1
            users = [user for user in users if user["is_active"] == active]
        if "role = ?" in query:
            role = params[param_index]
            users = [user for user in users if user["role"] == role]
        return users

    @staticmethod
    def _row(user):
        return (
            user["user_id"], user["full_name"], user["email"], user["role"],
            user["locality_id"], user["is_active"], user["created_at"],
        )

    def fetchone(self):
        return self.result

    def fetchall(self):
        return list(self.results)


class AdminConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = deepcopy(database)
        self._cursor = AdminCursor(database)
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


def make_database():
    created = datetime(2026, 1, 1)
    return {
        "users": {
            1: {"user_id": 1, "full_name": "Admin One", "email": "admin@example.test",
                "phone": "secret-phone", "password_hash": "secret-hash", "role": "ADMIN",
                "locality_id": 9, "is_active": True, "created_at": created,
                "updated_at": created},
            2: {"user_id": 2, "full_name": "User Two", "email": "user@example.test",
                "phone": "private-phone", "password_hash": "private-hash", "role": "USER",
                "locality_id": 10, "is_active": True, "created_at": created,
                "updated_at": created},
            3: {"user_id": 3, "full_name": "User Three", "email": "three@example.test",
                "phone": "private-phone-3", "password_hash": "private-hash-3", "role": "USER",
                "locality_id": 10, "is_active": False, "created_at": created,
                "updated_at": created},
        },
        "audit": [],
        "fail_audit": False,
        "summary": {"requests": 4, "offers": 5, "bookings": 2, "reviews": 1},
    }


def install_database(monkeypatch, database):
    connections = []

    def connect():
        conn = AdminConnection(database)
        connections.append(conn)
        return conn

    monkeypatch.setattr("routes.admin.get_connection", connect)
    return connections


def signed_in_client(user_id=1, role="ADMIN"):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = role
    return client


def test_admin_access_requires_authentication_and_admin_role():
    anonymous = create_app({"TESTING": True}).test_client()
    assert anonymous.get("/api/admin/users").status_code == 401
    assert anonymous.get("/api/admin/summary").status_code == 401

    ordinary_user = signed_in_client(user_id=2, role="USER")
    assert ordinary_user.get("/api/admin/users").status_code == 403
    assert ordinary_user.patch("/api/admin/users/2/status", json={"is_active": False}).status_code == 403


def test_admin_user_list_filters_paginates_and_omits_private_fields(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().get("/api/admin/users?is_active=false&role=user&page=1&per_page=10")

    assert response.status_code == 200
    assert response.json["total"] == 1
    assert len(response.json["users"]) == 1
    user = response.json["users"][0]
    assert user["user_id"] == 3
    assert user["is_active"] is False
    assert "phone" not in user
    assert "password_hash" not in user


@pytest.mark.parametrize("query", [
    "?is_active=yes", "?role=ROOT", "?per_page=101", "?page=0", "?limit=1",
])
def test_admin_user_list_rejects_invalid_filters(monkeypatch, query):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().get("/api/admin/users" + query)

    assert response.status_code == 400


def test_admin_status_change_is_audited_and_role_cannot_be_changed(monkeypatch):
    database = make_database()
    connections = install_database(monkeypatch, database)
    client = signed_in_client()

    changed = client.patch("/api/admin/users/2/status", json={"is_active": False})
    role_escalation = client.patch("/api/admin/users/2/status", json={"role": "ADMIN"})

    assert changed.status_code == 200
    assert changed.json["user"] == {"user_id": 2, "is_active": False}
    assert role_escalation.status_code == 400
    assert database["users"][2]["is_active"] is False
    assert database["users"][2]["role"] == "USER"
    assert database["audit"] == [(1, "ADMIN_USER_DEACTIVATED", "2", "127.0.0.1")]
    assert connections[0].commits == 1


def test_admin_can_reactivate_account_and_records_audit(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().patch("/api/admin/users/3/status", json={"is_active": True})

    assert response.status_code == 200
    assert database["users"][3]["is_active"] is True
    assert database["audit"] == [(1, "ADMIN_USER_ACTIVATED", "3", "127.0.0.1")]


def test_admin_status_validation_missing_target_and_self_deactivation(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    client = signed_in_client()

    invalid = client.patch("/api/admin/users/2/status", json={"is_active": "false"})
    missing = client.patch("/api/admin/users/99/status", json={"is_active": False})
    invalid_id = client.patch("/api/admin/users/0/status", json={"is_active": False})
    self_deactivate = client.patch("/api/admin/users/1/status", json={"is_active": False})

    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert invalid_id.status_code == 400
    assert self_deactivate.status_code == 409
    assert database["users"][1]["is_active"] is True


def test_only_active_admin_cannot_deactivate_own_account(monkeypatch):
    database = make_database()
    database["users"][2]["is_active"] = False
    database["users"][3]["is_active"] = False
    install_database(monkeypatch, database)

    response = signed_in_client().patch("/api/admin/users/1/status", json={"is_active": False})

    assert response.status_code == 409
    assert database["users"][1]["is_active"] is True


def test_admin_can_disable_another_admin_while_one_admin_remains(monkeypatch):
    database = make_database()
    database["users"][2]["role"] = "ADMIN"
    database["users"][2]["is_active"] = True
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=1).patch("/api/admin/users/2/status", json={"is_active": False})

    assert response.status_code == 200
    assert database["users"][2]["is_active"] is False
    assert database["users"][1]["is_active"] is True


def test_stale_admin_session_is_denied_using_current_database_role(monkeypatch):
    database = make_database()
    database["users"][1]["is_active"] = False
    install_database(monkeypatch, database)

    response = signed_in_client(user_id=1, role="ADMIN").get("/api/admin/users")

    assert response.status_code == 403


def test_platform_summary_uses_supported_tables(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)

    response = signed_in_client().get("/api/admin/summary")

    assert response.status_code == 200
    assert response.json["summary"] == {
        "users_total": 3,
        "users_active": 2,
        "requests_total": 4,
        "offers_total": 5,
        "bookings_total": 2,
        "reviews_total": 1,
    }


def test_audit_failure_rolls_back_status_change(monkeypatch):
    database = make_database()
    database["fail_audit"] = True
    connections = install_database(monkeypatch, database)

    response = signed_in_client().patch("/api/admin/users/2/status", json={"is_active": False})

    assert response.status_code == 503
    assert database["users"][2]["is_active"] is True
    assert database["audit"] == []
    assert connections[-1].rollbacks == 1
