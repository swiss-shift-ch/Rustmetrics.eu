"""Untergrenze fuer RM_BROWSE_CACHE_TTL."""
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


@pytest.mark.parametrize("raw", ["0", "-180", "10"])
def test_browse_cache_ttl_never_below_one_minute(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BROWSE_CACHE_TTL=raw)
    assert cfg.BROWSE_CACHE_TTL == 60


def test_valid_value_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_BROWSE_CACHE_TTL="600")
    assert cfg.BROWSE_CACHE_TTL == 600


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv("RM_BROWSE_CACHE_TTL", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.BROWSE_CACHE_TTL == 180
