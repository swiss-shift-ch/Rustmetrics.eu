"""Untergrenze fuer RM_SESSION_TTL."""
import importlib

import pytest

import rm_config


def _reload(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(rm_config)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


@pytest.mark.parametrize("raw", ["0", "-86400", "60"])
def test_session_ttl_never_below_one_hour(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_SESSION_TTL=raw)
    assert cfg.SESSION_TTL == 3600


def test_valid_value_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_SESSION_TTL="604800")
    assert cfg.SESSION_TTL == 604800


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv("RM_SESSION_TTL", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.SESSION_TTL == 30 * 86400
