"""Untergrenzen fuer Browse-/A2S-Konfiguration (RM_BROWSE_PARALLEL, Timeouts)."""
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


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_browse_parallel_never_below_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BROWSE_PARALLEL=raw)
    assert cfg.BROWSE_PARALLEL == 1


def test_browse_parallel_keeps_valid_value(monkeypatch):
    cfg = _reload(monkeypatch, RM_BROWSE_PARALLEL="40")
    assert cfg.BROWSE_PARALLEL == 40


@pytest.mark.parametrize("name", ["RM_A2S_TIMEOUT_SEC", "RM_BROWSE_A2S_TIMEOUT"])
@pytest.mark.parametrize("raw", ["0", "-1", "0.01"])
def test_a2s_timeouts_have_minimum(monkeypatch, name, raw):
    cfg = _reload(monkeypatch, **{name: raw})
    assert cfg.A2S_TIMEOUT >= cfg.A2S_TIMEOUT_MIN
    assert cfg.BROWSE_TIMEOUT >= cfg.A2S_TIMEOUT_MIN


def test_a2s_timeouts_keep_valid_values(monkeypatch):
    cfg = _reload(monkeypatch, RM_A2S_TIMEOUT_SEC="4.5", RM_BROWSE_A2S_TIMEOUT="1.5")
    assert cfg.A2S_TIMEOUT == 4.5
    assert cfg.BROWSE_TIMEOUT == 1.5


def test_defaults_unchanged(monkeypatch):
    for k in ("RM_BROWSE_PARALLEL", "RM_A2S_TIMEOUT_SEC", "RM_BROWSE_A2S_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    cfg = importlib.reload(rm_config)
    assert (cfg.BROWSE_PARALLEL, cfg.A2S_TIMEOUT, cfg.BROWSE_TIMEOUT) == (80, 3.0, 2.5)
