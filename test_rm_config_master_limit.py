"""Untergrenze fuer das Server-Limit vom Master (Browse)."""
import importlib

import pytest

import rm_config


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


@pytest.mark.parametrize("raw", ["0", "-50"])
def test_master_limit_never_below_one(monkeypatch, raw):
    monkeypatch.setenv("RM_BROWSE_MASTER_LIMIT", raw)
    cfg = importlib.reload(rm_config)
    assert cfg.BROWSE_MASTER_LIMIT == 1


def test_valid_value_kept(monkeypatch):
    monkeypatch.setenv("RM_BROWSE_MASTER_LIMIT", "5000")
    cfg = importlib.reload(rm_config)
    assert cfg.BROWSE_MASTER_LIMIT == 5000


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv("RM_BROWSE_MASTER_LIMIT", raising=False)
    cfg = importlib.reload(rm_config)
    assert cfg.BROWSE_MASTER_LIMIT == 20000
