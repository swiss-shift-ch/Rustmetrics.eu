"""Tests fuer rm_config: Env-Werte mit Untergrenze."""
import importlib

import pytest

import rm_config


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RM_POLL_INTERVAL_SEC", raising=False)
    else:
        monkeypatch.setenv("RM_POLL_INTERVAL_SEC", value)
    return importlib.reload(rm_config)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


def test_poll_interval_default(monkeypatch):
    assert _reload(monkeypatch, None).POLL_INTERVAL == 20


def test_poll_interval_custom_value_kept(monkeypatch):
    assert _reload(monkeypatch, "60").POLL_INTERVAL == 60


@pytest.mark.parametrize("value", ["0", "-5", "1"])
def test_poll_interval_too_small_is_raised_to_minimum(monkeypatch, value):
    cfg = _reload(monkeypatch, value)
    assert cfg.POLL_INTERVAL == cfg.POLL_INTERVAL_MIN


def test_poll_interval_invalid_falls_back_to_default(monkeypatch):
    assert _reload(monkeypatch, "abc").POLL_INTERVAL == 20
