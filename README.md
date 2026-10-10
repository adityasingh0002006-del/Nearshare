# NearShare

**Why Buy When You Can Borrow?** NearShare is a hyperlocal borrowing and lending platform. Neighbours can share useful items, post requests, find locality-based matches, make offers, coordinate bookings and review completed borrows.

## Architecture

- **Frontend:** Flask/Jinja template, responsive CSS and vanilla JavaScript (`static/`). The browser calls the existing REST endpoints through a small shared Fetch helper.
- **Backend:** Flask application factory in `config.py`; JSON APIs are registered from `routes/`. Authentication uses Flask's signed, HTTP-only session cookie. API authorization remains on the server.
- **Database:** Azure SQL / SQL Server through `pyodbc`, using the existing objects in `sql/` and connection settings from environment variables.
- **Images:** Azure Blob Storage through the existing image upload API. Blob URLs are kept in the `ItemImages` table; image bytes are not stored in SQL.
- **Analytics:** Power BI is an intended integration. No embed URL/API is configured in this repository.

## Current features

- Public NearShare landing page and responsive navigation.
- Registration, login, logout and current-session lookup.
- Browse/search items, view details, create/edit/delete owned items and upload JPEG, PNG or WebP images.
- Create requests, see open requests, view hyperlocal matches and make offers on matching items.
- View offers, accept/reject request offers, withdraw own pending offers and create bookings through acceptance.
- View bookings, confirm handover, return and completion, and submit/list participant reviews.
- Notification inbox, unread count and mark-as-read action.
- Admin summary, paged user list and account activation/deactivation.
- Loading, empty, error and success feedback; responsive layouts and keyboard-accessible form controls.

The lookup API reads categories and cities from the database and returns localities filtered by city, so forms do not hardcode category or locality IDs. Profile update/deactivation endpoints (there is no registered `/api/users` blueprint), item-owner names/contact details, admin item moderation, an admin booking list, an audit-log list, or a Power BI embed endpoint are not exposed. `/api/auth/me` returns `user_id`, `full_name`, `email`, and `role` only; it does not return phone, locality, account status or member-since data. The booking list/detail serializer also omits `borrower_id` and `owner_id`, so the frontend cannot identify the participant permitted to take a booking transition. The admin summary does not include item totals or completed-booking totals. Item API image arrays contain the canonical Blob URLs, but there is no signed-URL or authenticated image-proxy endpoint for viewing images from a private container. No item-list query filters are implemented by the API.

## Run locally

1. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies and the Microsoft ODBC Driver 18 for SQL Server:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and configure the required values. Keep `.env` private:

   ```powershell
   Copy-Item .env.example .env
   ```

   Set `SECRET_KEY` to a long random value. Database-backed pages require `DB_SERVER`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD`. `DB_DRIVER` defaults to `ODBC Driver 18 for SQL Server`. Image uploads require `AZURE_STORAGE_CONNECTION_STRING`; `AZURE_STORAGE_CONTAINER` is optional and defaults to `nearshare-images`.

4. Create database objects in Azure SQL, in this order, if setting up a new database: `sql/schema.sql`, `sql/views.sql`, `sql/procedures.sql`, and (for development data) `sql/seed.sql`. `sql/triggers.sql` is documentation and creates no trigger.

5. Start the Flask app:

   ```powershell
   py app.py
   ```

   Visit `http://127.0.0.1:5000/`. The API health check is `http://127.0.0.1:5000/api/health`.

## Existing API surface

Routes are defined in the Flask blueprints; all endpoints are under `/api` unless noted.

| Area | Endpoints |
| --- | --- |
| Health | `GET /api/health` |
| Auth | `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Admin | `GET /api/admin/me`, `GET /api/admin/users`, `PATCH /api/admin/users/<user_id>/status`, `GET /api/admin/summary` |
| Items | `GET, POST /api/items`, `GET, PUT, PATCH, DELETE /api/items/<item_id>`, `POST /api/items/<item_id>/images` |
| Lookups | `GET /api/categories`, `GET /api/cities`, `GET /api/localities?city=<city>` |
| Requests | `GET, POST /api/requests`, `GET /api/requests/<request_id>`, `PATCH /api/requests/<request_id>`, `GET /api/requests/<request_id>/matches`, `POST /api/requests/<request_id>/cancel` |
| Offers | `POST /api/requests/<request_id>/offers`, `GET /api/offers`, `GET /api/requests/<request_id>/offers`, `GET /api/offers/<offer_id>`, `POST /api/offers/<offer_id>/withdraw`, `POST /api/offers/<offer_id>/accept`, `POST /api/offers/<offer_id>/reject` |
| Bookings | `GET /api/bookings`, `GET /api/bookings/<booking_id>`, `POST /api/bookings/<booking_id>/handover`, `/return`, `/complete` |
| Reviews | `POST, GET /api/reviews/bookings/<booking_id>/reviews`, `GET /api/reviews/<review_id>` |
| Notifications | `GET /api/notifications`, `GET /api/notifications/unread-count`, `PATCH /api/notifications/<notification_id>/read` |

The browser sends same-origin requests with the session cookie. Item uploads send multipart form data with the `image` field. Request/offer/booking state changes, ownership and roles are validated by the backend and database workflow.

## Tests

Run the existing backend suite with:

```powershell
py -m pytest
```

The tests use the fixtures and mocks in `tests/`; a fully working local end-to-end flow also requires the configured Azure SQL database, ODBC driver and Blob Storage settings.

## Deployment and security

Deploy the Flask application to the configured hosting platform (the project targets Azure App Service), supply production secrets through environment configuration, use HTTPS, and configure Azure SQL firewall/network access and Blob Storage. Outside development mode the Flask session cookie is marked secure. Never commit `.env`, database credentials, storage connection strings or production secrets. Keep authorization and validation in the backend; the UI only reflects the available workflow and is not an access-control boundary.
