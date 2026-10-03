"""pytest-Konfiguration: nur die echten Unit-Tests einsammeln.

Die folgenden Dateien heissen zwar test_*.py, sind aber Probe-Skripte fuer den
Live-Server (Netzwerkzugriffe, Steam-Key, fester Pfad /opt/rustmetrics). Beim
Import laufen sie sofort los und brechen `pytest` ab — direkt mit
`python3 <datei>` starten.
"""

collect_ignore = [
    "test_a2s_atlas.py",
    "test_a2s_many.py",
    "test_master.py",
    "test_presence.py",
    "test_udp.py",
    "test_webapi_limit.py",
]
