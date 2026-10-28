"""Tests fuer rm_config: Aufbewahrungsfristen mit Untergrenze.

Bei RM_SNAPSHOTS_KEEP_DAYS=0/negativ lag der Cutoff in _cleanup_old bei
"jetzt" oder in der Zukunft → alle Snapshots und Sessions (auch offene
presence_sessions) wurden geloescht.
"""
import importlib

import pytest

import rm_config


def _reload(monkeypatch, key, value):
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)
    return importlib.reload(rm_config)


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


def test_snapshot_keep_days_default(monkeypatch):
    assert _reload(monkeypatch, "RM_SNAPSHOTS_KEEP_DAYS", None).SNAPSHOT_TTL_DAY == 14


def test_snapshot_keep_days_custom_value_kept(monkeypatch):
    assert _reload(monkeypatch, "RM_SNAPSHOTS_KEEP_DAYS", "30").SNAPSHOT_TTL_DAY == 30


@pytest.mark.parametrize("value", ["0", "-3"])
def test_snapshot_keep_days_too_small_is_raised_to_minimum(monkeypatch, value):
    cfg = _reload(monkeypatch, "RM_SNAPSHOTS_KEEP_DAYS", value)
    assert cfg.SNAPSHOT_TTL_DAY == cfg.SNAPSHOT_TTL_DAY_MIN >= 1


def test_players_json_keep_hours_default(monkeypatch):
    assert _reload(monkeypatch, "RM_PLAYERS_JSON_KEEP_HOURS", None).PLAYERS_JSON_KEEP_H == 24


@pytest.mark.parametrize("value", ["0", "-1"])
def test_players_json_keep_hours_too_small_is_raised_to_minimum(monkeypatch, value):
    cfg = _reload(monkeypatch, "RM_PLAYERS_JSON_KEEP_HOURS", value)
    assert cfg.PLAYERS_JSON_KEEP_H == cfg.PLAYERS_JSON_KEEP_H_MIN >= 1
