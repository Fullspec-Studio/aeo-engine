"""Tests for database connection resilience.

Covers:
- repo.connect() keepalive kwargs
- repo.connect_with_retry() backoff and retry logic
- repo.ensure_alive() ping-and-reconnect logic
- handlers._clients() transparent reconnect on stale cached conn
- ingestion._conn_factory() transparent reconnect on stale cached conn

No real database is used — psycopg is fully mocked.
"""
import importlib
import os
from unittest.mock import MagicMock, call, patch

import psycopg
import pytest

# Prevent boto3 credential-chain errors in test environment
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from aeo.db import repo
from aeo.ingestion import handler as ingestion_handler
from aeo.pipeline import handlers


# ─── repo.connect: keepalive kwargs ────────────────────────────────────────────

def test_connect_passes_keepalive_and_timeout_kwargs():
    """connect() must forward keepalive params and connect_timeout to psycopg."""
    fake_conn = MagicMock()
    with patch("psycopg.connect", return_value=fake_conn) as mock_connect:
        result = repo.connect("postgresql://fake/test")

    assert result is fake_conn
    _, kwargs = mock_connect.call_args
    assert kwargs["connect_timeout"] == 10
    assert kwargs["keepalives"] == 1
    assert kwargs["keepalives_idle"] == 60
    assert kwargs["keepalives_interval"] == 10
    assert kwargs["keepalives_count"] == 3
    assert kwargs["autocommit"] is True


# ─── repo.connect_with_retry ───────────────────────────────────────────────────

def test_connect_with_retry_succeeds_after_two_failures():
    """Two OperationalErrors then success: returns conn; sleeper called with 5 then 10."""
    fake_conn = MagicMock()
    sleeper = MagicMock()
    call_count = [0]

    def flaky_connect(dsn, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise psycopg.OperationalError("timeout")
        return fake_conn

    with patch.object(repo, "connect", side_effect=flaky_connect):
        result = repo.connect_with_retry("postgresql://fake/test", sleeper=sleeper)

    assert result is fake_conn
    assert sleeper.call_args_list == [call(5), call(10)]


def test_connect_with_retry_raises_after_all_attempts_exhausted():
    """Raises OperationalError after all 4 attempts fail; sleeper called 3 times."""
    sleeper = MagicMock()

    with patch.object(repo, "connect", side_effect=psycopg.OperationalError("refused")):
        with pytest.raises(psycopg.OperationalError):
            repo.connect_with_retry("postgresql://fake/test", attempts=4, sleeper=sleeper)

    # Sleep after attempts 1, 2, 3 (not after the final failure)
    assert sleeper.call_count == 3
    assert sleeper.call_args_list == [call(5), call(10), call(15)]


def test_connect_with_retry_default_attempts_is_4():
    """Default attempts=4 is the convention; verify via sleep count on all-fail."""
    sleeper = MagicMock()

    with patch.object(repo, "connect", side_effect=psycopg.OperationalError("x")):
        with pytest.raises(psycopg.OperationalError):
            repo.connect_with_retry("postgresql://fake/test", sleeper=sleeper)

    assert sleeper.call_count == 3


# ─── repo.ensure_alive ─────────────────────────────────────────────────────────

def _make_healthy_conn():
    """Return a MagicMock connection whose cursor context manager succeeds."""
    mock_conn = MagicMock()
    # MagicMock supports context managers; no extra setup needed for success path
    return mock_conn


def _make_dead_conn(exc):
    """Return a MagicMock connection whose cursor.execute raises *exc*."""
    mock_conn = MagicMock()
    # cursor().__enter__().execute() raises; __exit__ must return False so it propagates
    mock_conn.cursor.return_value.__enter__.return_value.execute.side_effect = exc
    mock_conn.cursor.return_value.__exit__.return_value = False
    return mock_conn


def test_ensure_alive_returns_same_conn_when_healthy():
    """Healthy conn: SELECT 1 succeeds → same object returned, no reconnect."""
    mock_conn = _make_healthy_conn()

    with patch.object(repo, "connect_with_retry") as mock_retry:
        result = repo.ensure_alive(mock_conn, "postgresql://fake/test")

    assert result is mock_conn
    mock_retry.assert_not_called()
    # Verify the ping was actually issued
    mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called_once_with("SELECT 1")


def test_ensure_alive_reconnects_on_operational_error():
    """Dead conn (OperationalError on execute) → new connection returned via connect_with_retry."""
    mock_conn = _make_dead_conn(psycopg.OperationalError("SSL SYSCALL error: Connection timed out"))
    new_conn = MagicMock()
    sleeper = MagicMock()

    with patch.object(repo, "connect_with_retry", return_value=new_conn) as mock_retry:
        result = repo.ensure_alive(mock_conn, "postgresql://fake/test", sleeper=sleeper)

    assert result is new_conn
    mock_retry.assert_called_once_with("postgresql://fake/test", sleeper=sleeper)


def test_ensure_alive_reconnects_on_interface_error():
    """Closed connection (InterfaceError) → new connection returned."""
    mock_conn = _make_dead_conn(psycopg.InterfaceError("connection already closed"))
    new_conn = MagicMock()

    with patch.object(repo, "connect_with_retry", return_value=new_conn):
        result = repo.ensure_alive(mock_conn, "postgresql://fake/test")

    assert result is new_conn


def test_ensure_alive_passes_sleeper_through_to_connect_with_retry():
    """ensure_alive forwards its sleeper to connect_with_retry for consistent backoff."""
    mock_conn = _make_dead_conn(psycopg.OperationalError("gone"))
    new_conn = MagicMock()
    custom_sleeper = MagicMock()

    with patch.object(repo, "connect_with_retry", return_value=new_conn) as mock_retry:
        repo.ensure_alive(mock_conn, "postgresql://fake/test", sleeper=custom_sleeper)

    mock_retry.assert_called_once_with("postgresql://fake/test", sleeper=custom_sleeper)


# ─── handlers._clients(): transparent reconnect ────────────────────────────────

def test_clients_reconnects_on_stale_cached_conn(monkeypatch):
    """Second invocation with a dead cached conn: _conn is rebound to the new connection."""
    dead_conn = _make_dead_conn(psycopg.OperationalError("SSL timeout"))
    new_conn = MagicMock()

    monkeypatch.setattr(handlers, "_conn", dead_conn)
    monkeypatch.setattr(handlers, "_bedrock", MagicMock())  # skip boto3 client creation
    monkeypatch.setattr(handlers, "_s3", MagicMock())
    monkeypatch.setattr(handlers, "_comprehend", MagicMock())
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    with patch.object(handlers.repo, "connect_with_retry", return_value=new_conn):
        conn, *_ = handlers._clients()

    assert conn is new_conn
    assert handlers._conn is new_conn


def test_clients_first_call_uses_connect_with_retry(monkeypatch):
    """First call (_conn is None) uses connect_with_retry, not bare connect."""
    new_conn = MagicMock()

    monkeypatch.setattr(handlers, "_conn", None)
    monkeypatch.setattr(handlers, "_bedrock", MagicMock())
    monkeypatch.setattr(handlers, "_s3", MagicMock())
    monkeypatch.setattr(handlers, "_comprehend", MagicMock())
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    with patch.object(handlers.repo, "connect_with_retry", return_value=new_conn) as mock_retry, \
         patch.object(handlers.repo, "connect") as mock_connect:
        conn, *_ = handlers._clients()

    assert conn is new_conn
    mock_retry.assert_called_once()
    mock_connect.assert_not_called()


def test_clients_healthy_conn_not_replaced(monkeypatch):
    """Healthy cached conn: ensure_alive returns same object; _conn unchanged."""
    healthy_conn = _make_healthy_conn()

    monkeypatch.setattr(handlers, "_conn", healthy_conn)
    monkeypatch.setattr(handlers, "_bedrock", MagicMock())
    monkeypatch.setattr(handlers, "_s3", MagicMock())
    monkeypatch.setattr(handlers, "_comprehend", MagicMock())
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    # ensure_alive with healthy conn returns same object (we test the integration here)
    with patch.object(handlers.repo, "connect_with_retry") as mock_retry:
        conn, *_ = handlers._clients()

    assert conn is healthy_conn
    mock_retry.assert_not_called()


# ─── ingestion._conn_factory(): transparent reconnect ─────────────────────────

def test_conn_factory_reconnects_on_stale_cached_conn(monkeypatch):
    """ingestion._conn_factory: dead cached conn is transparently replaced."""
    import aeo.ingestion.handler as mod
    importlib.reload(mod)  # reset _conn = None for a clean slate

    dead_conn = _make_dead_conn(psycopg.OperationalError("server closed the connection unexpectedly"))
    new_conn = MagicMock()

    mod._conn = dead_conn
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    with patch.object(mod.repo, "connect_with_retry", return_value=new_conn):
        result = mod._conn_factory()

    assert result is new_conn
    assert mod._conn is new_conn


def test_conn_factory_first_call_uses_connect_with_retry(monkeypatch):
    """ingestion._conn_factory: first call (_conn=None) uses connect_with_retry."""
    import aeo.ingestion.handler as mod
    importlib.reload(mod)  # _conn = None

    new_conn = MagicMock()
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    with patch.object(mod.repo, "connect_with_retry", return_value=new_conn) as mock_retry, \
         patch.object(mod.repo, "connect") as mock_connect:
        result = mod._conn_factory()

    assert result is new_conn
    mock_retry.assert_called_once()
    mock_connect.assert_not_called()


def test_conn_factory_healthy_conn_not_replaced(monkeypatch):
    """ingestion._conn_factory: healthy cached conn is returned unchanged."""
    import aeo.ingestion.handler as mod
    importlib.reload(mod)

    healthy_conn = _make_healthy_conn()
    mod._conn = healthy_conn
    monkeypatch.setenv("AEO_DSN", "postgresql://fake/test")

    with patch.object(mod.repo, "connect_with_retry") as mock_retry:
        result = mod._conn_factory()

    assert result is healthy_conn
    mock_retry.assert_not_called()
