from config import create_app
from werkzeug.security import check_password_hash


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.result = None

    def execute(self, query, *params):
        if "INSERT INTO dbo.Users" in query:
            user_id = len(self.database["users"]) + 1
            row = (user_id, params[0], params[1], params[3], "USER", params[4], True)
            self.database["users"].append(row)
            self.result = (user_id, params[0], params[1], "USER", params[4])
        elif "FROM dbo.Users WHERE email" in query:
            self.result = next((row for row in self.database["users"] if row[2] == params[0]), None)
        elif "INSERT INTO dbo.AuditLogs" in query:
            self.database["audit"].append(params[1])
        return self

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, database):
        self.database = database

    def cursor(self):
        return FakeCursor(self.database)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_auth_me_requires_login():
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_admin_route_rejects_user_role():
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "USER"
    assert client.get("/api/admin/me").status_code == 403


def test_admin_route_accepts_admin_role():
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = 17
        session["role"] = "ADMIN"
    response = client.get("/api/admin/me")
    assert response.status_code == 200
    assert response.json == {"user_id": 17, "role": "ADMIN"}


def test_registration_hashes_password_and_login_audits(monkeypatch):
    database = {"users": [], "audit": []}
    monkeypatch.setattr("routes.auth.get_connection", lambda: FakeConnection(database))
    client = create_app({"TESTING": True}).test_client()

    registered = client.post("/api/auth/register", json={
        "full_name": "Test User",
        "email": "TEST@example.com",
        "phone": "5550100",
        "password": "a-long-test-password",
        "locality_id": 1,
        "role": "ADMIN",
    })
    assert registered.status_code == 201
    assert registered.json["user"]["role"] == "USER"
    assert "password_hash" not in registered.json["user"]
    stored_hash = database["users"][0][3]
    assert stored_hash != "a-long-test-password"
    assert check_password_hash(stored_hash, "a-long-test-password")

    logged_in = client.post("/api/auth/login", json={
        "email": "test@example.com", "password": "a-long-test-password"
    })
    assert logged_in.status_code == 200
    assert client.get("/api/auth/me").json["user"]["email"] == "test@example.com"
    assert database["audit"] == ["LOGIN_SUCCESS"]

    client.post("/api/auth/logout")
    assert database["audit"] == ["LOGIN_SUCCESS", "LOGOUT"]
    assert client.get("/api/auth/me").status_code == 401
