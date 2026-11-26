"""Tests fuer rm_config: RM_PORT ausserhalb 1..65535.

RM_PORT=0 liess ThreadingHTTPServer still auf einem zufaelligen freien Port
lauschen (Caddy-Proxy auf 8765 → 502, ohne Fehlermeldung); 65536 und hoeher
oder negativ endeten erst beim bind() mit OverflowError. Solche Werte fallen
jetzt auf den Default zurueck.
"""
import importlib

import pytest

import rm_config


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(rm_config)


@pytest.mark.parametrize("value", ["0", "-1", "65536", "99999"])
def test_port_out_of_range_uses_default(monkeypatch, value):
    monkeypatch.setenv("RM_PORT", value)
    assert importlib.reload(rm_config).PORT == 8765


@pytest.mark.parametrize("value", ["1", "8080", "65535"])
def test_port_in_range_is_kept(monkeypatch, value):
    monkeypatch.setenv("RM_PORT", value)
    assert importlib.reload(rm_config).PORT == int(value)


def test_port_invalid_still_uses_default(monkeypatch):
    monkeypatch.setenv("RM_PORT", "abc")
    assert importlib.reload(rm_config).PORT == 8765


def test_env_port_helper_default_when_unset(monkeypatch):
    monkeypatch.delenv("RM_TEST_PORT", raising=False)
    assert rm_config._env_port("RM_TEST_PORT", 5432) == 5432
