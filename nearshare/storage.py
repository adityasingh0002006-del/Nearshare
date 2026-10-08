import os
from azure.storage.blob import BlobServiceClient


def get_blob_container():
    """Return the configured private image container; create it when first used."""
    connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not connection_string:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING is not configured")
    client = BlobServiceClient.from_connection_string(connection_string)
    container = client.get_container_client(os.getenv("AZURE_STORAGE_CONTAINER", "nearshare-images"))
    try:
        container.create_container()
    except Exception as exc:
        # Azure returns a conflict when the container already exists.
        if getattr(exc, "status_code", None) != 409:
            raise
    return container
