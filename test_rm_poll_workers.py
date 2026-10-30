"""Tests fuer RM_POLL_WORKERS: Untergrenze fuer den Poll-Worker-Pool.

Bei 0/negativ warf ThreadPoolExecutor(max_workers=...) schon beim Import von
rustmetrics einen ValueError — der Dienst startete gar nicht.
Jeder Fall laeuft in einem eigenen Prozess, weil der Pool beim Import entsteht.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))

# psycopg2 wie in test_rm_bm_ratelimit.py durch Attrappen ersetzen.
_SCRIPT = """
import sys, types
for n in ("psycopg2", "psycopg2.pool", "psycopg2.extras"):
    sys.modules.setdefault(n, types.ModuleType(n))
sys.modules["psycopg2"].pool = sys.modules["psycopg2.pool"]
sys.modules["psycopg2"].extras = sys.modules["psycopg2.extras"]
sys.modules["psycopg2.pool"].ThreadedConnectionPool = object
sys.modules["psycopg2.extras"].RealDictCursor = object
import rustmetrics as rm
print(rm.POLL_WORKERS, rm._poll_pool._max_workers)
"""


def _poll_workers(value):
    env = dict(os.environ)
    env.pop("RM_POLL_WORKERS", None)
    if value is not None:
        env["RM_POLL_WORKERS"] = value
    out = subprocess.run([sys.executable, "-c", _SCRIPT], cwd=HERE, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    workers, pool = out.stdout.split()[-2:]
    return int(workers), int(pool)


def test_poll_workers_default():
    assert _poll_workers(None) == (16, 16)


def test_poll_workers_custom_value_kept():
    assert _poll_workers("4") == (4, 4)


@pytest.mark.parametrize("value", ["0", "-3"])
def test_poll_workers_too_small_is_raised_to_minimum(value):
    assert _poll_workers(value) == (1, 1)
