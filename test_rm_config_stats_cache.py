"""Untergrenze fuer den Rust-Stats-Cache."""
import importlib

import pytest

import rm_config


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


@pytest.mark.parametrize("raw", ["0", "-3600", "10"])
def test_stats_cache_ttl_never_below_one_minute(monkeypatch, raw):
    monkeypatch.setenv("RM_RUST_STATS_CACHE_TTL", raw)
    cfg = importlib.reload(rm_config)
    assert cfg.RUST_STATS_CACHE_TTL == 60


def test_valid_value_kept(monkeypatch):
    monkeypatch.setenv("RM_RUST_STATS_CACHE_TTL", "900")
    cfg = importlib.reload(rm_config)
    assert cfg.RUST_STATS_CACHE_TTL == 900


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv("RM_RUST_STATS_CACHE_TTL", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.RUST_STATS_CACHE_TTL == 3600
