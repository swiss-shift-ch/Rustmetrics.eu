"""PG_DSN: libpq-Werte muessen gequotet sein (leeres Passwort, Leerzeichen, Quotes)."""
import importlib

import psycopg2.extensions as pgext
import pytest

import rm_config


def _dsn_with(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(rm_config).PG_DSN


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    importlib.reload(rm_config)


def test_empty_password_does_not_swallow_dbname(monkeypatch):
    monkeypatch.delenv("RM_PGPASSWORD", raising=False)
    parsed = pgext.parse_dsn(_dsn_with(monkeypatch, RM_PGDATABASE="rm_live"))
    assert parsed["dbname"] == "rm_live"
    assert parsed["password"] == ""


def test_password_with_space_and_quotes_survives(monkeypatch):
    secret = "a b'c\\d"
    parsed = pgext.parse_dsn(_dsn_with(monkeypatch, RM_PGPASSWORD=secret))
    assert parsed["password"] == secret
    assert parsed["dbname"] == "rustmetrics"


def test_defaults_unchanged(monkeypatch):
    for key in ("RM_PGHOST", "RM_PGPORT", "RM_PGUSER", "RM_PGPASSWORD", "RM_PGDATABASE"):
        monkeypatch.delenv(key, raising=False)
    parsed = pgext.parse_dsn(importlib.reload(rm_config).PG_DSN)
    assert parsed == {"host": "127.0.0.1", "port": "5432", "user": "rustmetrics",
                      "password": "", "dbname": "rustmetrics"}
