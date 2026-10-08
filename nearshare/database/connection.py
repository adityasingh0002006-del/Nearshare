import os


def get_connection_string():
    """Build an Azure SQL ODBC string from environment settings; never hard-code secrets."""
    required = ("DATABASE_SERVER", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing database settings: " + ", ".join(missing))
    driver = os.getenv("DATABASE_DRIVER", "{ODBC Driver 18 for SQL Server}")
    return (
        f"DRIVER={driver};SERVER=tcp:{os.environ['DATABASE_SERVER']},1433;"
        f"DATABASE={os.environ['DATABASE_NAME']};UID={os.environ['DATABASE_USER']};"
        f"PWD={os.environ['DATABASE_PASSWORD']};Encrypt=yes;TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )


def get_connection():
    """Open a connection only when Azure SQL credentials and ODBC driver are configured."""
    import pyodbc
    return pyodbc.connect(get_connection_string())
