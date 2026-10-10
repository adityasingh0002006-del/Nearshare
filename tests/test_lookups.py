from config import create_app


CATEGORIES = ["Tools", "Electronics", "Outdoor", "Fashion", "Home & Kitchen", "Books & Study", "Gaming", "Sports & Fitness", "Events & Party", "Automotive", "Photography", "Furniture", "Other"]
KANPUR = ["Kakadeo", "Swaroop Nagar", "Civil Lines", "Govind Nagar", "Kidwai Nagar", "Kalyanpur", "Arya Nagar", "Sharda Nagar", "Barra", "Rawatpur"]
LUCKNOW = ["Gomti Nagar", "Indira Nagar", "Aliganj", "Hazratganj", "Alambagh", "Mahanagar", "Vikas Nagar", "Jankipuram", "Chinhat", "Ashiyana"]


class LookupCursor:
    def __init__(self):
        self.query = None
        self.params = ()

    def execute(self, query, *params):
        self.query, self.params = query, params
        return self

    def fetchone(self):
        if "SELECT 1 FROM dbo.Localities" in self.query:
            return (1,) if self.params[0] in {"Kanpur", "Lucknow"} else None
        return None

    def fetchall(self):
        if "dbo.Categories" in self.query:
            return [(i + 1, name) for i, name in enumerate(CATEGORIES)]
        if "DISTINCT city" in self.query:
            return [("Kanpur",), ("Lucknow",)]
        if "dbo.Localities" in self.query:
            city = self.params[-1] if self.params else None
            rows = [(i + 1, name, "Kanpur", "Uttar Pradesh") for i, name in enumerate(KANPUR)]
            rows += [(i + 11, name, "Lucknow", "Uttar Pradesh") for i, name in enumerate(LUCKNOW)]
            return [row for row in rows if city is None or row[2] == city]
        raise AssertionError("Unexpected lookup query")


class LookupConnection:
    def __init__(self):
        self.fake_cursor = LookupCursor()

    def cursor(self):
        return self.fake_cursor

    def close(self):
        pass


def install_lookup(monkeypatch):
    connection = LookupConnection()
    monkeypatch.setattr("routes.lookups.get_connection", lambda: connection)
    return connection


def test_categories_lookup_returns_all_thirteen_database_categories(monkeypatch):
    connection = install_lookup(monkeypatch)
    response = create_app({"TESTING": True}).test_client().get("/api/categories")
    assert response.status_code == 200
    assert [row["category_name"] for row in response.json["categories"]] == CATEGORIES
    assert "ORDER BY category_name" in connection.fake_cursor.query


def test_cities_are_loaded_from_localities(monkeypatch):
    connection = install_lookup(monkeypatch)
    response = create_app({"TESTING": True}).test_client().get("/api/cities")
    assert response.status_code == 200
    assert response.json == {"cities": ["Kanpur", "Lucknow"]}
    assert "SELECT DISTINCT city FROM dbo.Localities" in connection.fake_cursor.query


def test_kanpur_localities_load_correctly(monkeypatch):
    connection = install_lookup(monkeypatch)
    response = create_app({"TESTING": True}).test_client().get("/api/localities?city=Kanpur")
    assert response.status_code == 200
    assert [row["locality_name"] for row in response.json["localities"]] == KANPUR
    assert len(response.json["localities"]) == 10
    assert all(row["city"] == "Kanpur" for row in response.json["localities"])
    assert connection.fake_cursor.params == ("Kanpur",)


def test_lucknow_localities_load_correctly(monkeypatch):
    install_lookup(monkeypatch)
    response = create_app({"TESTING": True}).test_client().get("/api/localities?city=Lucknow")
    assert response.status_code == 200
    assert [row["locality_name"] for row in response.json["localities"]] == LUCKNOW
    assert len(response.json["localities"]) == 10
    assert all(row["city"] == "Lucknow" for row in response.json["localities"])


def test_invalid_and_nonexistent_cities_are_rejected(monkeypatch):
    install_lookup(monkeypatch)
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/localities").status_code == 400
    assert client.get("/api/localities?city=%20%20").status_code == 400
    response = client.get("/api/localities?city=Bengaluru")
    assert response.status_code == 400
    assert response.json == {"error": "Unknown city"}
