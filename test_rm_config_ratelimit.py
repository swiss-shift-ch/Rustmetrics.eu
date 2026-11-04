"""Untergrenzen fuer RM_RL_REQUESTS_PER_MIN und RM_RL_BROWSE_PER_HOUR."""
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


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_requests_per_min_never_below_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_RL_REQUESTS_PER_MIN=raw)
    assert cfg.RL_REQ_PER_MIN == 1


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_browse_per_hour_never_below_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_RL_BROWSE_PER_HOUR=raw)
    assert cfg.RL_BROWSE_PER_H == 1


def test_valid_values_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_RL_REQUESTS_PER_MIN="300", RM_RL_BROWSE_PER_HOUR="30")
    assert (cfg.RL_REQ_PER_MIN, cfg.RL_BROWSE_PER_H) == (300, 30)


def test_defaults_unchanged(monkeypatch):
    for k in ("RM_RL_REQUESTS_PER_MIN", "RM_RL_BROWSE_PER_HOUR"):
        monkeypatch.delenv(k, raising=False)
    cfg = importlib.reload(rm_config)
    assert (cfg.RL_REQ_PER_MIN, cfg.RL_BROWSE_PER_H) == (120, 12)
