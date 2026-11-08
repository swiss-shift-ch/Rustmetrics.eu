"""Untergrenzen fuer den BattleMetrics-Schutzschalter."""
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


@pytest.mark.parametrize("raw", ["0", "-21600", "30"])
def test_breaker_cooldown_never_below_one_minute(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BM_BREAKER_COOLDOWN=raw)
    assert cfg.BM_BREAKER_COOLDOWN == 60


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_breaker_fails_at_least_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BM_BREAKER_FAILS=raw)
    assert cfg.BM_BREAKER_FAILS == 1


def test_valid_values_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_BM_BREAKER_COOLDOWN="3600", RM_BM_BREAKER_FAILS="5")
    assert cfg.BM_BREAKER_COOLDOWN == 3600
    assert cfg.BM_BREAKER_FAILS == 5


def test_defaults_unchanged(monkeypatch):
    monkeypatch.delenv("RM_BM_BREAKER_COOLDOWN", raising=False)
    monkeypatch.delenv("RM_BM_BREAKER_FAILS", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.BM_BREAKER_COOLDOWN == 21600
    assert cfg.BM_BREAKER_FAILS == 3
