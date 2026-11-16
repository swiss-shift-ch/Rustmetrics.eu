"""Tests fuer rm_config._env: nicht-endliche Float-Werte.

float() akzeptiert "nan"/"inf"/"-inf". RM_A2S_TIMEOUT_SEC=inf wurde so zu
socket.settimeout(inf) (OverflowError), "nan" zu ValueError — jede A2S-Abfrage
scheiterte. Nicht-endliche Werte fallen jetzt auf den Default zurueck.
"""
import importlib

import pytest

import rm_config


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
def test_non_finite_float_falls_back_to_default(monkeypatch, value):
    monkeypatch.setenv("RM_TEST_FLOAT", value)
    assert rm_config._env("RM_TEST_FLOAT", 2.5, float) == 2.5


def test_finite_float_is_kept(monkeypatch):
    monkeypatch.setenv("RM_TEST_FLOAT", "1.25")
    assert rm_config._env("RM_TEST_FLOAT", 2.5, float) == 1.25


def test_invalid_float_still_falls_back(monkeypatch):
    monkeypatch.setenv("RM_TEST_FLOAT", "abc")
    assert rm_config._env("RM_TEST_FLOAT", 2.5, float) == 2.5


def test_a2s_timeout_inf_uses_default(monkeypatch):
    monkeypatch.setenv("RM_A2S_TIMEOUT_SEC", "inf")
    assert importlib.reload(rm_config).A2S_TIMEOUT == 3.0


def test_int_and_str_unchanged(monkeypatch):
    monkeypatch.setenv("RM_TEST_INT", "42")
    monkeypatch.setenv("RM_TEST_STR", "inf")
    assert rm_config._env("RM_TEST_INT", 1, int) == 42
    assert rm_config._env("RM_TEST_STR", "x") == "inf"
