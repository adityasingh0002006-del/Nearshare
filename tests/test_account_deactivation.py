"""Previously issued sessions stop working after account deactivation."""

from copy import deepcopy

from config import create_app


class AccountCursor:
    def __init__(self, database):
        self.database = database
        self.result = None

    def execute(self, query, *params):
        self.result = None
        if "SELECT is_active FROM dbo.Users" in query:
            user = self.database["users"].get(params[0])
            self.result = (user["is_active"],) if user else None
        elif "SELECT role, is_active FROM dbo.Users" in query:
            user = self.database["users"].get(params[0])
            self.result = (user["role"], user["is_active"]) if user else None
        elif "SELECT COUNT(*) FROM dbo.Users WITH" in query:
            self.result = (sum(
                1 for user in self.database["users"].values()
                if user["role"] == "ADMIN" and user["is_active"]
            ),)
        elif "UPDATE dbo.Users SET is_active" in query:
            desired, user_id, current = params
            user = self.database["users"].get(user_id)
            if user and user["is_active"] == current:
                user["is_active"] = desired
                self.result = (user_id, desired)
        elif "INSERT INTO dbo.AuditLogs" in query:
            self.database["audit"].append(tuple(params))
        return self

    def fetchone(self):
        return self.result


class AccountConnection:
    def __init__(self, database):
        self.database = database
        self.snapshot = deepcopy(database)

    def cursor(self):
        return AccountCursor(self.database)

    def commit(self):
        pass

    def rollback(self):
        self.database.clear()
        self.database.update(deepcopy(self.snapshot))

    def close(self):
        pass


def make_database():
    return {
        "users": {
            1: {"role": "ADMIN", "is_active": True},
            2: {"role": "ADMIN", "is_active": True},
            3: {"role": "USER", "is_active": True},
        },
        "audit": [],
    }


def sign_in(client, user_id, role):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = role


def install_database(monkeypatch, database):
    def connect():
        return AccountConnection(database)

    monkeypatch.setattr("routes.admin.get_connection", connect)
    monkeypatch.setattr("middleware.auth.get_connection", connect)


def test_deactivation_revokes_old_sessions_across_protected_modules(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    app = create_app({"TESTING": True})

    admin = app.test_client()
    sign_in(admin, 1, "ADMIN")
    deactivated_session = app.test_client()
    sign_in(deactivated_session, 2, "ADMIN")

    result = admin.patch("/api/admin/users/2/status", json={"is_active": False})

    assert result.status_code == 200
    assert database["users"][2]["is_active"] is False
    assert database["audit"] == [(1, "ADMIN_USER_DEACTIVATED", "2", "127.0.0.1")]

    protected_urls = [
        ("GET", "/api/items"),
        ("POST", "/api/items"),
        ("PATCH", "/api/items/1"),
        ("DELETE", "/api/items/1"),
        ("GET", "/api/requests"),
        ("POST", "/api/requests"),
        ("PATCH", "/api/requests/1"),
        ("POST", "/api/requests/1/cancel"),
        ("GET", "/api/requests/1/matches"),
        ("POST", "/api/requests/1/offers"),
        ("GET", "/api/offers"),
        ("POST", "/api/offers/1/accept"),
        ("POST", "/api/offers/1/reject"),
        ("POST", "/api/offers/1/withdraw"),
        ("GET", "/api/bookings"),
        ("POST", "/api/bookings/1/handover"),
        ("POST", "/api/bookings/1/return"),
        ("POST", "/api/bookings/1/complete"),
        ("GET", "/api/reviews/bookings/1/reviews"),
        ("POST", "/api/reviews/bookings/1/reviews"),
        ("GET", "/api/notifications"),
        ("PATCH", "/api/notifications/1/read"),
        ("GET", "/api/admin/users"),
        ("PATCH", "/api/admin/users/3/status"),
    ]
    for method, url in protected_urls:
        # Replay the original signed identity to model a stale cookie.
        stale_client = app.test_client()
        sign_in(stale_client, 2, "ADMIN")
        response = stale_client.open(url, method=method)
        assert response.status_code == 403, (method, url, response.json)
        with stale_client.session_transaction() as session:
            assert "user_id" not in session

    assert deactivated_session.get("/api/items").status_code == 403
    assert admin.get("/api/health").status_code == 200


def test_deactivating_one_user_does_not_lock_out_another_active_user(monkeypatch):
    database = make_database()
    install_database(monkeypatch, database)
    app = create_app({"TESTING": True})

    admin = app.test_client()
    sign_in(admin, 1, "ADMIN")
    response = admin.patch("/api/admin/users/2/status", json={"is_active": False})
    assert response.status_code == 200

    unrelated_user = app.test_client()
    sign_in(unrelated_user, 3, "USER")
    assert unrelated_user.get("/api/auth/me").status_code == 200


def test_account_status_lookup_fails_closed(monkeypatch):
    class BrokenConnection:
        def cursor(self):
            raise RuntimeError("database unavailable")

        def close(self):
            pass

    monkeypatch.setattr("middleware.auth.get_connection", BrokenConnection)
    app = create_app({"TESTING": True})
    client = app.test_client()
    sign_in(client, 42, "USER")

    response = client.get("/api/auth/me")

    assert response.status_code == 503
