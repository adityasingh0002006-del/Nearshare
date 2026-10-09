from io import BytesIO

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
    assert response.json["image"]["blob_url"].endswith(uploaded["blob_name"])
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
