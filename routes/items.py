"""Authenticated item CRUD and image upload endpoints."""

from decimal import Decimal, InvalidOperation
import logging
import re
import uuid

from flask import Blueprint, Response, current_app, jsonify, request, session
from azure.core.exceptions import ResourceNotFoundError
from PIL import Image, UnidentifiedImageError

from database.connection import get_connection
from middleware.auth import login_required
from services.matching_service import find_matching_requests
from services.storage_service import delete_image, download_image, upload_image


items_bp = Blueprint("items", __name__)
CONDITIONS = {"NEW", "LIKE_NEW", "GOOD", "FAIR", "POOR"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "jpg": ("JPEG", "image/jpeg"),
    "jpeg": ("JPEG", "image/jpeg"),
    "png": ("PNG", "image/png"),
    "webp": ("WEBP", "image/webp"),
}
_INTEGER_RE = re.compile(r"^[1-9][0-9]*$")


def _db_error():
    current_app.logger.exception("Item database operation failed")
    return jsonify(error="Item service unavailable"), 503


def _item(row):
    return {
        "item_id": row[0], "owner_id": row[1], "category_id": row[2],
        "category_name": row[3], "item_name": row[4], "description": row[5],
        "condition": row[6], "rental_price": str(row[7]),
        "security_deposit": str(row[8]), "is_available": bool(row[9]),
        "created_at": row[10].isoformat() if row[10] else None,
        "updated_at": row[11].isoformat() if row[11] else None,
        "images": list(row[12] or []),
    }


def _item_select(where="", suffix=""):
    return f"""SELECT i.item_id, i.owner_id, i.category_id, c.category_name,
                      i.item_name, i.description, i.item_condition,
                      i.rental_price, i.security_deposit, i.is_available,
                      i.created_at, i.updated_at,
                      COALESCE((SELECT STRING_AGG(CAST(N'/api/items/images/' + CONVERT(NVARCHAR(20), ii.image_id) AS NVARCHAR(MAX)), N'|')
                                FROM dbo.ItemImages AS ii WHERE ii.item_id = i.item_id), N'')
               FROM dbo.Items AS i
               INNER JOIN dbo.Categories AS c ON c.category_id = i.category_id
               {where} {suffix}"""


def _serialize(row):
    if not row:
        return None
    values = list(row)
    values[12] = values[12].split("|") if values[12] else []
    return _item(values)


def _positive_int(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError(f"{field} must be a positive integer")


def _money(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2 or number > Decimal("99999999.99"):
        raise ValueError(f"{field} must be a non-negative amount with at most two decimal places")
    return number.quantize(Decimal("0.01"))


def _validate_item(data, partial=False):
    if not isinstance(data, dict):
        raise ValueError("A JSON request body is required")
    allowed = {"category_id", "item_name", "description", "condition", "rental_price", "security_deposit", "is_available"}
    if set(data) - allowed:
        raise ValueError("Request contains unsupported fields")
    required = {"category_id", "item_name", "condition", "rental_price", "security_deposit", "is_available"}
    if not partial and required - set(data):
        raise ValueError("category_id, item_name, condition, rental_price, security_deposit, and is_available are required")
    result = {}
    if "category_id" in data:
        result["category_id"] = _positive_int(data["category_id"], "category_id")
    if "item_name" in data:
        name = data["item_name"]
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
            raise ValueError("item_name must be between 1 and 150 characters")
        result["item_name"] = name.strip()
    if "description" in data:
        description = data["description"]
        if description is not None and (not isinstance(description, str) or len(description) > 1000):
            raise ValueError("description must be at most 1000 characters")
        result["description"] = description.strip() if isinstance(description, str) else None
    if "condition" in data:
        condition = data["condition"]
        if not isinstance(condition, str) or condition.strip().upper() not in CONDITIONS:
            raise ValueError("condition must be NEW, LIKE_NEW, GOOD, FAIR, or POOR")
        result["item_condition"] = condition.strip().upper()
    for field in ("rental_price", "security_deposit"):
        if field in data:
            result[field] = _money(data[field], field)
    if "is_available" in data:
        if not isinstance(data["is_available"], bool):
            raise ValueError("is_available must be a boolean")
        result["is_available"] = data["is_available"]
    return result


def _category_exists(cursor, category_id):
    cursor.execute("SELECT 1 FROM dbo.Categories WHERE category_id = ?", category_id)
    return cursor.fetchone() is not None


@items_bp.get("")
@login_required
def list_items():
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(_item_select(suffix="ORDER BY i.created_at DESC, i.item_id DESC"))
        rows = cursor.fetchall()
        return jsonify(items=[_serialize(row) for row in rows]), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.get("/<int:item_id>")
@login_required
def get_item(item_id):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(_item_select("WHERE i.item_id = ?"), item_id)
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Item not found"), 404
        return jsonify(item=_serialize(row)), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.get("/<int:item_id>/matching-requests")
@login_required
def get_item_matching_requests(item_id):
    """List only privacy-safe requests matching an item owned by the viewer."""
    if item_id <= 0:
        return jsonify(error="item_id must be a positive integer"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id FROM dbo.Items WHERE item_id = ?", item_id)
        item = cursor.fetchone()
        if not item:
            return jsonify(error="Item not found"), 404
        if item[0] != session["user_id"]:
            return jsonify(error="You do not own this item"), 403
        matching_requests = find_matching_requests(cursor, item_id)
        return jsonify(item_id=item_id, requests=matching_requests), 200
    except Exception:
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.post("")
@login_required
def create_item():
    try:
        values = _validate_item(request.get_json(silent=True))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if not _category_exists(cursor, values["category_id"]):
            return jsonify(error="category_id does not identify an existing category"), 400
        cursor.execute(
            """INSERT INTO dbo.Items
               (owner_id, category_id, item_name, description, item_condition,
                rental_price, security_deposit, is_available)
               OUTPUT INSERTED.item_id
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            session["user_id"], values["category_id"], values["item_name"],
            values.get("description"), values["item_condition"], values["rental_price"],
            values["security_deposit"], values["is_available"],
        )
        item_id = cursor.fetchone()[0]
        conn.commit()
        cursor.execute(_item_select("WHERE i.item_id = ?"), item_id)
        return jsonify(item=_serialize(cursor.fetchone())), 201
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.route("/<int:item_id>", methods=["PUT", "PATCH"])
@login_required
def update_item(item_id):
    try:
        values = _validate_item(request.get_json(silent=True), partial=True)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if not values:
        return jsonify(error="At least one supported field is required"), 400
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id FROM dbo.Items WHERE item_id = ?", item_id)
        owner = cursor.fetchone()
        if not owner:
            return jsonify(error="Item not found"), 404
        if owner[0] != session["user_id"]:
            return jsonify(error="You do not own this item"), 403
        if "category_id" in values and not _category_exists(cursor, values["category_id"]):
            return jsonify(error="category_id does not identify an existing category"), 400
        assignments, params = [], []
        for key, value in values.items():
            assignments.append(f"{key} = ?")
            params.append(value)
        assignments.append("updated_at = SYSUTCDATETIME()")
        params.append(item_id)
        cursor.execute(f"UPDATE dbo.Items SET {', '.join(assignments)} WHERE item_id = ?", *params)
        conn.commit()
        cursor.execute(_item_select("WHERE i.item_id = ?"), item_id)
        return jsonify(item=_serialize(cursor.fetchone())), 200
    except Exception:
        if conn:
            conn.rollback()
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.delete("/<int:item_id>")
@login_required
def delete_item(item_id):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id FROM dbo.Items WHERE item_id = ?", item_id)
        owner = cursor.fetchone()
        if not owner:
            return jsonify(error="Item not found"), 404
        if owner[0] != session["user_id"]:
            return jsonify(error="You do not own this item"), 403
        cursor.execute("SELECT blob_url FROM dbo.ItemImages WHERE item_id = ?", item_id)
        image_urls = [row[0] for row in cursor.fetchall()]
        cursor.execute("DELETE FROM dbo.ItemImages WHERE item_id = ?", item_id)
        cursor.execute("DELETE FROM dbo.Items WHERE item_id = ?", item_id)
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        return jsonify(error="Item could not be deleted because it is still in use"), 409
    finally:
        if conn:
            conn.close()
    for url in image_urls:
        try:
            delete_image(url)
        except Exception:
            current_app.logger.exception("Could not remove stored item image after item deletion")
    return jsonify(message="Item deleted"), 200


@items_bp.post("/<int:item_id>/images")
@login_required
def add_item_image(item_id):
    image = request.files.get("image")
    if image is None or not image.filename:
        return jsonify(error="An image file is required in the 'image' field"), 400
    extension = image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""
    expected = IMAGE_TYPES.get(extension)
    if not expected or image.mimetype != expected[1]:
        return jsonify(error="Only matching JPEG, PNG, and WebP images are allowed"), 400
    image.stream.seek(0, 2)
    size = image.stream.tell()
    image.stream.seek(0)
    if size <= 0 or size > MAX_IMAGE_BYTES:
        return jsonify(error="Image must be between 1 byte and 5 MB"), 400
    try:
        with Image.open(image.stream) as decoded:
            actual_format = decoded.format
            decoded.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        return jsonify(error="Image content does not match its extension"), 400
    finally:
        image.stream.seek(0)
    if actual_format != expected[0]:
        return jsonify(error="Image content does not match its extension and MIME type"), 400

    conn = None
    blob_url = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT owner_id FROM dbo.Items WHERE item_id = ?", item_id)
        owner = cursor.fetchone()
        if not owner:
            return jsonify(error="Item not found"), 404
        if owner[0] != session["user_id"]:
            return jsonify(error="You do not own this item"), 403
        blob_name = f"items/{item_id}/{uuid.uuid4().hex}.{extension}"
        try:
            blob_url = upload_image(blob_name, image.stream, expected[1])
        except Exception:
            current_app.logger.exception("Item image storage operation failed")
            return jsonify(error="Image storage unavailable"), 503
        cursor.execute("INSERT INTO dbo.ItemImages (item_id, blob_url) OUTPUT INSERTED.image_id VALUES (?, ?)", item_id, blob_url)
        image_id = cursor.fetchone()[0]
        conn.commit()
        return jsonify(image={"blob_url": f"/api/items/images/{image_id}"}), 201
    except Exception:
        if conn:
            conn.rollback()
        if blob_url:
            try:
                delete_image(blob_url)
            except Exception:
                logging.getLogger(__name__).exception("Could not clean up unassociated item image")
        return _db_error()
    finally:
        if conn:
            conn.close()


@items_bp.get("/images/<int:image_id>")
@login_required
def get_item_image(image_id):
    """Serve a database-associated image from the configured private blob container."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT blob_url FROM dbo.ItemImages WHERE image_id = ?", image_id)
        row = cursor.fetchone()
        if not row:
            return jsonify(error="Image not found"), 404
        image_bytes, content_type = download_image(row[0])
        response = Response(image_bytes, mimetype=content_type)
        response.headers["Cache-Control"] = "no-store"
        return response
    except (FileNotFoundError, ResourceNotFoundError):
        return jsonify(error="Image not found"), 404
    except Exception:
        current_app.logger.exception("Item image retrieval failed")
        return jsonify(error="Image service unavailable"), 503
    finally:
        if conn:
            conn.close()
