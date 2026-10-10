from io import BytesIO
from datetime import datetime
from decimal import Decimal

import pytest
from PIL import Image

from config import create_app


class Cursor:
    def __init__(self, owner_id=2):
        self.owner_id = owner_id
        self.result = None
        self.executed = []

    def execute(self, query, *params):
        self.executed.append((query, params))
        if "SELECT owner_id FROM dbo.Items" in query:
            self.result = (self.owner_id,)
        elif "INSERT INTO dbo.ItemImages" in query:
            self.result = (123,)
        elif "SELECT blob_url FROM dbo.ItemImages WHERE image_id" in query:
            self.result = ("https://storage.example/nearshare-images/items/5/photo.jpg",)
        else:
            self.result = None
        return self

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []


class Connection:
    def __init__(self, owner_id=2):
        self.fake_cursor = Cursor(owner_id)

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def signed_in_client(user_id=1):
    client = create_app({"TESTING": True}).test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["role"] = "USER"
    return client


def test_item_list_and_detail_require_authentication():
    client = create_app({"TESTING": True}).test_client()
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/items/1").status_code == 401


def test_create_rejects_invalid_condition_and_money_before_database(monkeypatch):
    monkeypatch.setattr("routes.items.get_connection", lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")))
    client = signed_in_client()
    response = client.post("/api/items", json={
        "category_id": 1, "item_name": "Drill", "condition": "BROKEN",
        "rental_price": -1, "security_deposit": 0, "is_available": True,
    })
    assert response.status_code == 400
    assert "condition" in response.json["error"]


def test_create_item_accepts_a_new_database_category(monkeypatch):
    now = datetime(2026, 10, 10)

    class CategoryCursor:
        result = None

        def execute(self, query, *params):
            if "SELECT 1 FROM dbo.Categories" in query:
                self.result = (1,) if params == (13,) else None
            elif "INSERT INTO dbo.Items" in query:
                self.result = (91,)
            elif "SELECT i.item_id" in query:
                self.result = (91, 1, 13, "Other", "Tripod", None, "GOOD",
                               Decimal("0.00"), Decimal("0.00"), True, now, now, "")
            return self

        def fetchone(self):
            return self.result

    class CategoryConnection:
        def __init__(self):
            self.fake_cursor = CategoryCursor()

        def cursor(self):
            return self.fake_cursor

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    connection = CategoryConnection()
    monkeypatch.setattr("routes.items.get_connection", lambda: connection)
    response = signed_in_client().post("/api/items", json={
        "category_id": 13, "item_name": "Tripod", "condition": "GOOD",
        "rental_price": 0, "security_deposit": 0, "is_available": True,
    })
    assert response.status_code == 201
    assert response.json["item"]["category_id"] == 13
    assert response.json["item"]["category_name"] == "Other"


def test_update_delete_and_image_upload_reject_non_owner(monkeypatch):
    connection = Connection(owner_id=2)
    monkeypatch.setattr("routes.items.get_connection", lambda: connection)
    client = signed_in_client(user_id=1)
    valid_jpeg = BytesIO()
    Image.new("RGB", (2, 2), color="red").save(valid_jpeg, format="JPEG")
    valid_jpeg.seek(0)

    updated = client.patch("/api/items/8", json={"item_name": "New name"})
    deleted = client.delete("/api/items/8")
    uploaded = client.post("/api/items/8/images", data={
        "image": (valid_jpeg, "photo.jpg", "image/jpeg"),
    }, content_type="multipart/form-data")

    assert updated.status_code == deleted.status_code == uploaded.status_code == 403
    assert all("UPDATE dbo.Items" not in query and "DELETE FROM dbo.Items" not in query
               for query, _ in connection.fake_cursor.executed)


def test_image_upload_rejects_mismatched_content(monkeypatch):
    monkeypatch.setattr("routes.items.get_connection", lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")))
    client = signed_in_client()
    response = client.post("/api/items/8/images", data={
        "image": (BytesIO(b"not an image"), "photo.png", "image/png"),
    }, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "content" in response.json["error"]


@pytest.mark.parametrize(
    ("extension", "format_name", "content_type"),
    [("png", "PNG", "image/png"), ("jpg", "JPEG", "image/jpeg"), ("webp", "WEBP", "image/webp")],
)
def test_image_upload_accepts_supported_image_bytes_with_matching_mime(
    monkeypatch, extension, format_name, content_type
):
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color="red").save(image_bytes, format=format_name)
    image_bytes.seek(0)
    connection = Connection(owner_id=2)
    uploaded = {}

    def fake_upload(blob_name, stream, actual_content_type):
        uploaded.update(blob_name=blob_name, data=stream.read(), content_type=actual_content_type)
        return "https://storage.example/nearshare-images/" + blob_name

    monkeypatch.setattr("routes.items.get_connection", lambda: connection)
    monkeypatch.setattr("routes.items.upload_image", fake_upload)
    client = signed_in_client(user_id=2)
    response = client.post("/api/items/8/images", data={
        "image": (image_bytes, f"photo.{extension}", content_type),
    }, content_type="multipart/form-data")

    assert response.status_code == 201
    assert uploaded["content_type"] == content_type
    assert uploaded["data"]
    assert uploaded["blob_name"].startswith("items/8/")
    assert uploaded["blob_name"].endswith(f".{extension}")
    assert response.json["image"]["blob_url"] == "/api/items/images/123"
    assert any("INSERT INTO dbo.ItemImages" in query
               for query, _ in connection.fake_cursor.executed)


def test_image_upload_rejects_unsupported_or_corrupt_file(monkeypatch):
    monkeypatch.setattr("routes.items.get_connection", lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")))
    client = signed_in_client()
    response = client.post("/api/items/8/images", data={
        "image": (BytesIO(b"\x89PNG\r\n\x1a\nnot really a PNG"), "photo.png", "image/png"),
    }, content_type="multipart/form-data")

    assert response.status_code == 400


def test_image_upload_reports_storage_failure_without_fake_success(monkeypatch, caplog):
    connection = Connection(owner_id=1)
    monkeypatch.setattr("routes.items.get_connection", lambda: connection)

    def fail_storage(*_args, **_kwargs):
        raise ValueError("Connection string is either blank or malformed.")

    monkeypatch.setattr("routes.items.upload_image", fail_storage)
    client = signed_in_client(user_id=1)
    image_bytes = BytesIO()
    Image.new("RGB", (1, 1), color="red").save(image_bytes, format="JPEG")
    image_bytes.seek(0)
    response = client.post("/api/items/5/images", data={
        "image": (image_bytes, "photo.jpg", "image/jpeg"),
    }, content_type="multipart/form-data")

    assert response.status_code == 503
    assert response.json == {"error": "Image storage unavailable"}
    assert "Item image storage operation failed" in caplog.text
    assert not any("INSERT INTO dbo.ItemImages" in query
                   for query, _ in connection.fake_cursor.executed)


def test_image_upload_rejects_mismatched_mime_type(monkeypatch):
    monkeypatch.setattr("routes.items.get_connection", lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")))
    monkeypatch.setattr("routes.items.upload_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Storage should not be called")))
    image_bytes = BytesIO()
    Image.new("RGB", (1, 1), color="red").save(image_bytes, format="PNG")
    image_bytes.seek(0)
    client = signed_in_client()
    response = client.post("/api/items/8/images", data={
        "image": (image_bytes, "photo.png", "application/octet-stream"),
    }, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "matching" in response.json["error"]


def test_item_image_serialization_uses_authenticated_flask_endpoint():
    from routes.items import _serialize

    row = (5, 2, 1, "Tools", "Drill", "", "GOOD", 10, 0, True,
           None, None, "/api/items/images/42|/api/items/images/43")
    assert _serialize(row)["images"] == ["/api/items/images/42", "/api/items/images/43"]


def test_private_item_image_endpoint_requires_authenticated_user():
    client = create_app({"TESTING": True}).test_client()
    response = client.get("/api/items/images/42")
    assert response.status_code == 401


def test_private_item_image_endpoint_returns_bytes_type_and_no_store(monkeypatch):
    connection = Connection()
    monkeypatch.setattr("routes.items.get_connection", lambda: connection)
    monkeypatch.setattr("routes.items.download_image", lambda url: (b"fake-image", "image/webp"))
    client = signed_in_client()

    response = client.get("/api/items/images/42")

    assert response.status_code == 200
    assert response.data == b"fake-image"
    assert response.content_type == "image/webp"
    assert response.headers["Cache-Control"] == "no-store"
    assert any("WHERE image_id = ?" in query and params == (42,)
               for query, params in connection.fake_cursor.executed)


def test_private_item_image_endpoint_returns_404_when_record_missing(monkeypatch):
    class MissingCursor:
        def execute(self, *_args):
            return self
        def fetchone(self):
            return None

    class MissingConnection:
        def cursor(self):
            return MissingCursor()
        def close(self):
            pass

    monkeypatch.setattr("routes.items.get_connection", MissingConnection)
    monkeypatch.setattr("routes.items.download_image", lambda *_args: (_ for _ in ()).throw(AssertionError("No blob lookup for missing record")))
    response = signed_in_client().get("/api/items/images/999")
    assert response.status_code == 404


def test_download_image_rejects_unassociated_blob_paths():
    from services.storage_service import _blob_name_for_url

    class Container:
        url = "https://storage.example/nearshare-images"

    for url in (
        "https://attacker.example/nearshare-images/items/1/x.png",
        "https://storage.example/other/items/1/x.png",
        "https://storage.example/nearshare-images/items/../secret",
        "https://storage.example/nearshare-images/items/%2e%2e/secret",
    ):
        with pytest.raises(ValueError):
            _blob_name_for_url(Container(), url)
