# NearShare

**Why Buy When You Can Borrow?** NearShare is a hyperlocal platform concept for borrowing and lending useful items in your community.

## Technology stack

- Frontend: HTML, CSS, and vanilla JavaScript
- Backend: Python Flask REST API
- Database: Azure SQL Database / SQL Server with T-SQL
- File storage: Azure Blob Storage
- Hosting: Azure App Service
- Analytics: Power BI
- Version control: Git and GitHub

The application serves a welcome page and API health check, plus session-based authentication and role checks. The Azure SQL foundation includes the approved schema, available-items view and procedure, and development seed data. Booking workflows and Azure Blob integration remain future work.

## Run locally

1. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install the Python requirements:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Install **Microsoft ODBC Driver 18 for SQL Server** on the computer running the app. The `pyodbc` Python package requires this separate system driver.

4. Copy `.env.example` to `.env` and fill in the Azure SQL settings below. Keep `.env` private and never commit it. The application can still serve its welcome and health routes without Azure SQL credentials.

5. Start Flask:

   ```powershell
   py app.py
   ```

   Open `http://127.0.0.1:5000/` to see the welcome page.

## Azure SQL prerequisites and environment

Create an Azure SQL logical server and database, configure a firewall rule for the development machine's public IP, and prepare a SQL login with permission to create and use the schema. Install ODBC Driver 18 on any machine that will connect through `pyodbc`.

Set these values in the local `.env` file:

| Variable | Purpose |
| --- | --- |
| `DB_SERVER` | Azure SQL server host name, such as `your-server.database.windows.net` |
| `DB_NAME` | Database name |
| `DB_USER` | SQL login name |
| `DB_PASSWORD` | SQL login password |
| `DB_DRIVER` | ODBC driver name; defaults to `ODBC Driver 18 for SQL Server` |

`FLASK_ENV` and `SECRET_KEY` configure Flask. Set `SECRET_KEY` to a long random value before starting the app; the application refuses to start without one outside tests. Azure Blob placeholders are retained for a later storage phase and are not used by this database foundation.

## Authentication API

- `POST /api/auth/register` accepts `full_name`, `email`, `phone`, `password` (at least 12 characters), and `locality_id`. Registration always creates a `USER`; role assignment is never accepted from the request.
- `POST /api/auth/login` accepts `email` and `password` and starts a signed, HTTP-only Flask session.
- `POST /api/auth/logout` ends the session.
- `GET /api/auth/me` returns the signed-in user's public profile and requires authentication.
- `GET /api/admin/me` demonstrates admin-only access. `login_required`, `roles_required`, and `admin_required` are available in `middleware.auth` for protected API handlers.

Passwords are stored using Werkzeug's salted scrypt password hash. Login success/failure and logout are recorded in the existing `dbo.AuditLogs` table. Configure HTTPS in production; session cookies are marked secure outside development mode.

## Create the database objects

Connect to the target Azure SQL database using SQL Server Management Studio (SSMS) or Azure Data Studio. Run the scripts in this order, in the same database:

1. `sql/schema.sql` creates the 11 approved tables, constraints, and indexes. Run it once on a new database.
2. `sql/views.sql` creates `dbo.AvailableItemsView`.
3. `sql/procedures.sql` creates `dbo.GetAvailableItemsByCategory`.
4. `sql/seed.sql` inserts repeatable development localities, categories, users, items, and requests.

`sql/triggers.sql` documents why no trigger is needed in this phase; it does not create a database object. The seed users contain non-authenticatable development placeholders in `password_hash`, not working passwords.

The procedure can be called in SSMS with a category ID, for example:

```sql
EXEC dbo.GetAvailableItemsByCategory @CategoryId = 1;
```

The Flask connection helper reads the same `DB_*` variables from the environment. It uses encryption and certificate validation with ODBC Driver 18 and never embeds credentials in source code.

## API health check

`GET http://127.0.0.1:5000/api/health` returns JSON while the API is running, for example:

```json
{"message":"NearShare API is running","status":"ok"}
```

Never commit `.env` or real credentials. `.env.example` contains placeholders only.
