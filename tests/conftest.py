"""Shared test-only active-account lookup for signed-session route tests."""

import pytest


class _ActiveAccountCursor:
    def execute(self, _query, *_params):
        return self

    def fetchone(self):
        return (True,)


class _ActiveAccountConnection:
    def cursor(self):
        return _ActiveAccountCursor()

    def close(self):
        pass


@pytest.fixture(autouse=True)
def default_active_account_lookup(monkeypatch):
    """Keep unit tests independent of Azure SQL unless they install a test DB."""
    monkeypatch.setattr(
        "middleware.auth.get_connection", _ActiveAccountConnection,
    )
