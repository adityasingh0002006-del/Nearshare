import os
from pathlib import Path

from azure.storage.blob import BlobServiceClient
from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent.parent


def get_blob_container():
    """Return the configured private image container; create it when first used."""
    # Storage may be called through more than one app factory or working directory.
    # Load the project-local settings here as well; never override process settings.
    load_dotenv(PROJECT_DIR / ".env", override=False)
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
