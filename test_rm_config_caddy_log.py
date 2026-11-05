"""Untergrenze fuer RM_CADDY_LOG_MAX_BYTES."""
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


@pytest.mark.parametrize("raw", ["0", "-1", "100"])
def test_caddy_log_max_bytes_has_floor(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_CADDY_LOG_MAX_BYTES=raw)
    assert cfg.CADDY_LOG_MAX_BYTES == 64 * 1024


def test_caddy_log_valid_value_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_CADDY_LOG_MAX_BYTES=str(1024 * 1024))
    assert cfg.CADDY_LOG_MAX_BYTES == 1024 * 1024


def test_caddy_log_default_unchanged(monkeypatch):
    monkeypatch.delenv("RM_CADDY_LOG_MAX_BYTES", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.CADDY_LOG_MAX_BYTES == 5 * 1024 * 1024
