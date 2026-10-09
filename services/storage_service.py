"""Azure Blob operations for item images."""

import os
from urllib.parse import unquote, urlsplit

from azure.storage.blob import ContentSettings

from nearshare.storage import get_blob_container


def upload_image(blob_name, stream, content_type):
    """Upload one validated image and return its canonical blob URL."""
    container = get_blob_container()
    blob = container.get_blob_client(blob=blob_name)
    blob.upload_blob(stream, overwrite=False, content_settings=ContentSettings(content_type=content_type))
    return blob.url


def delete_image(blob_url):
    """Delete an image only when its URL points into the configured container."""
    container = get_blob_container()
    parsed, expected = urlsplit(blob_url), urlsplit(container.url)
    container_name = os.getenv("AZURE_STORAGE_CONTAINER", "nearshare-images")
    prefix = f"/{container_name}/"
    if parsed.scheme != "https" or parsed.hostname != expected.hostname or not parsed.path.startswith(prefix):
        raise ValueError("Image URL does not belong to the configured storage container")
    blob_name = unquote(parsed.path[len(prefix):])
    if not blob_name or ".." in blob_name.split("/"):
        raise ValueError("Invalid image blob path")
    container.delete_blob(blob_name, delete_snapshots="include")
