"""Untergrenzen fuer RM_BROWSE_BM_MAX_PAGES und RM_BROWSE_RESULT_CAP."""
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


@pytest.mark.parametrize("raw", ["0", "-2"])
def test_bm_max_pages_never_below_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BROWSE_BM_MAX_PAGES=raw)
    assert cfg.BROWSE_BM_MAX_PAGES == 1


@pytest.mark.parametrize("raw", ["0", "-50"])
def test_result_cap_never_below_one(monkeypatch, raw):
    cfg = _reload(monkeypatch, RM_BROWSE_RESULT_CAP=raw)
    assert cfg.BROWSE_RESULT_CAP == 1


def test_valid_values_kept(monkeypatch):
    cfg = _reload(monkeypatch, RM_BROWSE_BM_MAX_PAGES="12", RM_BROWSE_RESULT_CAP="500")
    assert (cfg.BROWSE_BM_MAX_PAGES, cfg.BROWSE_RESULT_CAP) == (12, 500)


def test_defaults_unchanged(monkeypatch):
    for k in ("RM_BROWSE_BM_MAX_PAGES", "RM_BROWSE_RESULT_CAP"):
        monkeypatch.delenv(k, raising=False)
    cfg = importlib.reload(rm_config)
    assert (cfg.BROWSE_BM_MAX_PAGES, cfg.BROWSE_RESULT_CAP) == (40, 2000)
