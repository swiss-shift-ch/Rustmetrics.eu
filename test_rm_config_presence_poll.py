"""Tests fuer rm_config: Untergrenze fuer RM_PRESENCE_POLL_SEC.

Bei 0/negativ lief der Presence-Poller ohne Pause (Dauer-Abfrage der Steam-API
bis zum Rate-Limit), und PRESENCE_STALE_SEC = STALE_POLLS * 0 schloss jede
offene Presence-Session sofort als «veraltet».
"""
import importlib

import pytest

import rm_config


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RM_PRESENCE_POLL_SEC", raising=False)
    else:
        monkeypatch.setenv("RM_PRESENCE_POLL_SEC", value)
    return importlib.reload(rm_config)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


def test_presence_poll_default(monkeypatch):
    assert _reload(monkeypatch, None).PRESENCE_POLL_SEC == 60


def test_presence_poll_custom_value_kept(monkeypatch):
    assert _reload(monkeypatch, "120").PRESENCE_POLL_SEC == 120


@pytest.mark.parametrize("value", ["0", "-5"])
def test_presence_poll_too_small_is_raised_to_minimum(monkeypatch, value):
    cfg = _reload(monkeypatch, value)
    assert cfg.PRESENCE_POLL_SEC == cfg.PRESENCE_POLL_SEC_MIN >= 10
