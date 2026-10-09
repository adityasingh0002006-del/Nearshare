"""Small Azure SQL connection helper using environment configuration only."""

import os
from dotenv import load_dotenv


load_dotenv()


def _odbc_value(value):
    """Quote an ODBC value so special characters remain part of the value."""
    return "{" + value.replace("}", "}}") + "}"


def get_connection_string():
    """Build a secure ODBC connection string from DB_* environment variables."""
    required = ("DB_SERVER", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing Azure SQL settings: " + ", ".join(missing))

    driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server").strip()
    driver = driver.strip("{}")
    server = os.environ["DB_SERVER"].strip()

    return ";".join(
        (
            f"DRIVER={_odbc_value(driver)}",
            f"SERVER={_odbc_value('tcp:' + server + ',1433')}",
            f"DATABASE={_odbc_value(os.environ['DB_NAME'])}",
            f"UID={_odbc_value(os.environ['DB_USER'])}",
            f"PWD={_odbc_value(os.environ['DB_PASSWORD'])}",
            "Encrypt=yes",
            "TrustServerCertificate=no",
            "Connection Timeout=60",
        )
    )


def get_connection():
    """Open an Azure SQL connection when credentials and ODBC are configured."""
    import pyodbc

    return pyodbc.connect(get_connection_string())
