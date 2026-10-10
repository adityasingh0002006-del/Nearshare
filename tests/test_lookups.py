from config import create_app


class LookupCursor:
    def __init__(self):
        self.query = None

    def execute(self, query, *params):
        self.query = query
        assert not params
        return self

    def fetchall(self):
        if "dbo.Categories" in self.query:
            return [(4, "Tools"), (5, "Electronics")]
        if "dbo.Localities" in self.query:
            return [(1, "Indiranagar", "Bengaluru", "Karnataka")]
        raise AssertionError("Unexpected lookup query")


class LookupConnection:
    def __init__(self):
        self.fake_cursor = LookupCursor()

    def cursor(self):
        return self.fake_cursor

    def close(self):
        pass


def test_categories_lookup_returns_ids_and_names_without_authentication(monkeypatch):
    connection = LookupConnection()
    monkeypatch.setattr("routes.lookups.get_connection", lambda: connection)

    response = create_app({"TESTING": True}).test_client().get("/api/categories")

    assert response.status_code == 200
    assert response.json == {"categories": [
        {"category_id": 4, "category_name": "Tools"},
        {"category_id": 5, "category_name": "Electronics"},
    ]}
    assert "ORDER BY category_name" in connection.fake_cursor.query


def test_localities_lookup_returns_ids_and_human_readable_location_without_authentication(monkeypatch):
    connection = LookupConnection()
    monkeypatch.setattr("routes.lookups.get_connection", lambda: connection)

    response = create_app({"TESTING": True}).test_client().get("/api/localities")

    assert response.status_code == 200
    assert response.json == {"localities": [{
        "locality_id": 1,
        "locality_name": "Indiranagar",
        "city": "Bengaluru",
        "state": "Karnataka",
    }]}
    assert "ORDER BY state, city, locality_name" in connection.fake_cursor.query
