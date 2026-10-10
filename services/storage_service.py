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
    blob_name = _blob_name_for_url(container, blob_url)
    container.delete_blob(blob_name, delete_snapshots="include")


def _blob_name_for_url(container, blob_url):
    """Resolve only canonical URLs in the configured container to blob names."""
    parsed, expected = urlsplit(blob_url), urlsplit(container.url)
    container_name = os.getenv("AZURE_STORAGE_CONTAINER", "nearshare-images")
    prefix = f"/{container_name}/"
    if (parsed.scheme != "https" or parsed.hostname != expected.hostname
            or parsed.port != expected.port or parsed.username or parsed.password
            or parsed.query or parsed.fragment or not parsed.path.startswith(prefix)):
        raise ValueError("Image URL does not belong to the configured storage container")
    blob_name = unquote(parsed.path[len(prefix):])
    if not blob_name or ".." in blob_name.split("/") or "\\" in blob_name:
        raise ValueError("Invalid image blob path")
    return blob_name


def download_image(blob_url):
    """Download a DB-associated image from the configured private container."""
    container = get_blob_container()
    blob_name = _blob_name_for_url(container, blob_url)
    downloader = container.get_blob_client(blob=blob_name).download_blob()
    content_type = downloader.properties.content_settings.content_type
    if not content_type or not content_type.startswith("image/"):
        raise ValueError("Stored item image has an invalid content type")
    return downloader.readall(), content_type
