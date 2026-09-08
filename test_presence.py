"""RustMetrics — Test-Suite fuer Steam-Presence & Browse.

Auf dem Server laufen lassen:
    cd /opt/rustmetrics && env $(grep -v '^#' .env | tr '\n' ' ') .venv/bin/python test_presence.py
"""
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("rm", "/opt/rustmetrics/rustmetrics.py")
rm = importlib.util.module_from_spec(spec)
try: spec.loader.exec_module(rm)
except SystemExit: pass

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS  %s" % name)
    else:
        FAIL += 1; print("  FAIL  %s %s" % (name, detail))

print("\n== 1. presence_ip(): Game-Port vs Query-Port ==")
check("ip:port -> ip",        rm.presence_ip("1.2.3.4:28012") == "1.2.3.4")
check("ip ohne Port",         rm.presence_ip("1.2.3.4") == "1.2.3.4")
check("None -> None",         rm.presence_ip(None) is None)
check("leer -> None",         rm.presence_ip("") is None)

print("\n== 2. parse_steam_player_input(): Eingabeerkennung ==")
check("reine SteamID64",      rm.parse_steam_player_input("76561198012345678") == 76561198012345678)
check("mit Whitespace",       rm.parse_steam_player_input("  76561198012345678 ") == 76561198012345678)
check("/profiles/-URL",       rm.parse_steam_player_input("https://steamcommunity.com/profiles/76561198012345678") == 76561198012345678)
check("/profiles/ mit Slash", rm.parse_steam_player_input("https://steamcommunity.com/profiles/76561198012345678/") == 76561198012345678)
check("Grossschreibung URL",  rm.parse_steam_player_input("HTTPS://STEAMCOMMUNITY.COM/profiles/76561198012345678") == 76561198012345678)
check("normaler Name -> None",rm.parse_steam_player_input("DIPSY") is None)
check("Name mit Ziffern",     rm.parse_steam_player_input("-40 fps petrucho") is None)
check("zu kurze ID -> None",  rm.parse_steam_player_input("7656119801234567") is None)
check("leer -> None",         rm.parse_steam_player_input("") is None)
check("None -> None",         rm.parse_steam_player_input(None) is None)

print("\n== 3. presence_transition(): Benachrichtigungs-Logik ==")
off = {"in_rust": False, "server_addr": None}
onA = {"in_rust": True,  "server_addr": "1.1.1.1:28012"}
check("Erstbeobachtung meldet nie", rm.presence_transition(None, True, "1.1.1.1:28012") is None)
check("offline -> online",         rm.presence_transition(off, True, "1.1.1.1:28012") == "online")
check("online -> offline",         rm.presence_transition(onA, False, None) == "offline")
check("gleicher Server, kein Event",rm.presence_transition(onA, True, "1.1.1.1:28012") is None)
check("Port aendert sich != Wechsel",rm.presence_transition(onA, True, "1.1.1.1:28999") is None,
      "(Game-Port kann wechseln, IP zaehlt)")
check("echter Serverwechsel",      rm.presence_transition(onA, True, "2.2.2.2:28012") == "switch")
check("offline bleibt offline",    rm.presence_transition(off, False, None) is None)

print("\n== 4. fetch_player_summaries(): API + Batching ==")
check("leere Liste -> {}", rm.fetch_player_summaries([]) == {})
rm.init_pool()
with rm._Conn() as (conn, cur):
    cur.execute("SELECT id FROM users LIMIT 150")
    many = [int(r["id"]) for r in cur.fetchall()]
if len(many) > 100:
    res = rm.fetch_player_summaries(many)
    check("Batching >100 IDs (%d angefragt)" % len(many), len(res) > 100,
          "-> %d Antworten" % len(res))
else:
    print("  SKIP  Batching (nur %d IDs in DB)" % len(many))
bad = rm.fetch_player_summaries([1234567890])
check("ungueltige ID crasht nicht", isinstance(bad, dict))

print("\n== 5. detect_name_anonymization(): Streamer-Mode-Erkennung ==")
try:
    q = rm.query_a2s("64.40.9.132", 28015)
    names = [p["name"] for p in (q.get("players_list") or []) if p.get("name")]
    if len(names) >= 15:
        check("censored Server erkannt (%d Namen)" % len(names),
              rm.detect_name_anonymization(names) is True)
    else:
        # Live-Abhaengigkeit: Server kann Liste blocken/leer sein — dann ist
        # der Detektor gar nicht pruefbar, das ist kein Fehler im Code.
        print("  SKIP  Referenz-Server liefert nur %d Namen" % len(names))
except Exception as e:
    print("  SKIP  A2S nicht erreichbar: %s" % e)
real = ["xX_Sniper_Xx", "Hans", "M4rio", "kebab king", "Ghost", "ProGamer2000",
        "Anna-Lena", "TTV_Dave", "noob", "L33T", "Bär", "Zocker", "M", "qwerty", "Pixel"]
check("echte Namen NICHT als fake erkannt", rm.detect_name_anonymization(real) is False)
check("zu kurze Liste -> False", rm.detect_name_anonymization(["a","b","c"]) is False)

print("\n== 6. Browse-API: Sortierung & Kategorien ==")
def browse(**kw):
    return rm.api_browse(user_id=None, ip="127.0.0.1", **kw)
try:
    b = browse(limit=50)
    check("Browse liefert Server", b["matched"] > 800, "matched=%d" % b["matched"])
    check("mehr als altes 800er-Limit", b["total_online"] > 800, "total=%d" % b["total_online"])
    pd = [s["players_count"] for s in browse(limit=20, sort="pop_desc")["servers"]]
    check("pop_desc absteigend", pd == sorted(pd, reverse=True))
    pa = [s["players_count"] for s in browse(limit=20, sort="pop_asc")["servers"]]
    check("pop_asc aufsteigend", pa == sorted(pa))
    nm = [s["name"].lower() for s in browse(limit=20, sort="name")["servers"]]
    check("name A-Z", nm == sorted(nm))
    nd = [s["name"].lower() for s in browse(limit=20, sort="name_desc")["servers"]]
    check("name Z-A", nd == sorted(nd, reverse=True))
    cats = {}
    for c in ("vanilla", "community", "modded"):
        cats[c] = browse(limit=1, tier=c)["matched"]
    total = browse(limit=1)["matched"]
    check("Kategorien summieren plausibel (%s)" % cats,
          abs(sum(cats.values()) - total) < total * 0.25,
          "summe=%d total=%d" % (sum(cats.values()), total))
    check("jede Kategorie nicht leer", all(v > 0 for v in cats.values()))
    van = browse(limit=5, tier="vanilla", sort="pop_desc")["servers"]
    check("Vanilla = offizielle Server",
          any("facepunch" in s["name"].lower() for s in van),
          [s["name"] for s in van])
except Exception as e:
    print("  FAIL  Browse-Tests: %s: %s" % (type(e).__name__, e)); FAIL += 1

print("\n== 7. Discord-Webhook SSRF-Schutz ==")
for bad_url in ["http://127.0.0.1:9999/x", "http://169.254.169.254/latest/meta-data",
                "https://evil.example.com/webhook", "https://discord.com.evil.com/x"]:
    ok, msg = rm._post_discord_webhook(bad_url, {"content": "test"})
    check("blockiert: %s" % bad_url[:38], ok is False, "-> %s" % msg)

print("\n== 8. api_server_detail(): Streamer-Mode-Flag ==")
with rm._Conn() as (conn, cur):
    cur.execute("SELECT ws.user_id, ws.server_id FROM watched_servers ws LIMIT 1")
    r = cur.fetchone()
if r:
    d = rm.api_server_detail(int(r["user_id"]), int(r["server_id"]))
    check("detail enthaelt name_anonymized", d is not None and "name_anonymized" in d,
          list(d.keys()) if d else None)
else:
    print("  SKIP  kein gewatchter Server in DB")

print("\n== 9. validate_public_host(): SSRF-Schutz ==")
BLOCKED = ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.5", "169.254.169.254",
           "0.0.0.0", "::1", "fc00::1", "224.0.0.1"]
for h in BLOCKED:
    try:
        rm.validate_public_host(h, 28015); check("blockiert %s" % h, False, "-> DURCHGELASSEN!")
    except ValueError:
        check("blockiert %s" % h, True)
    except Exception as e:
        check("blockiert %s" % h, True, "(%s)" % type(e).__name__)
try:
    rm.validate_public_host("195.60.166.128", 28015); check("erlaubt oeffentliche IP", True)
except Exception as e:
    check("erlaubt oeffentliche IP", False, str(e))
for bad_port in [0, -1, 70000]:
    try:
        rm.validate_public_host("8.8.8.8", bad_port); check("Port %s abgelehnt" % bad_port, False)
    except ValueError:
        check("Port %s abgelehnt" % bad_port, True)
try:
    rm.validate_public_host("localhost", 28015); check("Hostname localhost blockiert", False, "-> DURCHGELASSEN!")
except ValueError:
    check("Hostname localhost blockiert", True)
try:
    rm.validate_public_host("this-host-does-not-exist-xyz123.invalid", 28015)
    check("nicht aufloesbarer Host abgelehnt", False)
except ValueError:
    check("nicht aufloesbarer Host abgelehnt", True)

print("\n== 10. Quoten ==")
check("Server-Quote gesetzt", isinstance(rm.MAX_WATCHED_SERVERS, int) and rm.MAX_WATCHED_SERVERS > 0,
      rm.MAX_WATCHED_SERVERS)
check("Spieler-Quote gesetzt", isinstance(rm.MAX_WATCHED_PLAYERS, int) and rm.MAX_WATCHED_PLAYERS > 0,
      rm.MAX_WATCHED_PLAYERS)

print("\n== 11. Poller-Haertung ==")
check("fester Worker-Pool statt Thread-pro-Server", hasattr(rm, "_poll_pool"))
check("_poll_guarded faengt Fehler", hasattr(rm, "_poll_guarded"))
try:
    rm._poll_guarded({"id": -1, "host": "0.0.0.0", "port": 1})
    check("_poll_guarded schluckt Exception", True)
except Exception as e:
    check("_poll_guarded schluckt Exception", False, type(e).__name__)

print("\n== 12. DB-Pool ==")
import rm_db  # Modul-Split: der Pool lebt jetzt in rm_db
check("minconn erhoeht (kein Connect-Churn)", rm_db._pool.minconn >= 8, "minconn=%s" % rm_db._pool.minconn)
check("maxconn >= 25", rm_db._pool.maxconn >= 25, "maxconn=%s" % rm_db._pool.maxconn)

print("\n== 13. Alias-Lernen (Streamer-Mode-Namen zuordnen) ==")
check("Mindest-Ereignisse >= 3 (Falsch-Positiv-Schutz)", rm.ALIAS_MIN_EVENTS >= 3,
      rm.ALIAS_MIN_EVENTS)
check("Kandidaten pro Ereignis gedeckelt", rm.ALIAS_MAX_CANDS <= 12, rm.ALIAS_MAX_CANDS)

# End-zu-End gegen die echte DB: Server + Snapshots bauen, Ereignisse einspielen
TSID, TUID = 999999999997, 999999999996
import time as _t
_now = int(_t.time())
with rm._Conn() as (conn, cur):
    cur.execute("INSERT INTO servers (host, port, name, first_seen) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (host, port) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                ("203.0.113.7", 28015, "ALIAS-TEST", _now))
    _sid = int(cur.fetchone()["id"])
    cur.execute("DELETE FROM alias_candidates WHERE steam_id=%s", (TSID,))
    cur.execute("DELETE FROM snapshots WHERE server_id=%s", (_sid,))
    # 3 Join-Ereignisse: 'ziel' kommt jedes Mal dazu, Rauschen wechselt
    _rounds = [("ziel", "rauschen1"), ("ziel", "rauschen2"), ("ziel", "rauschen3")]
    _events = []
    for _i, (_target, _noise) in enumerate(_rounds):
        _t0 = _now - 3000 + _i * 600
        cur.execute("INSERT INTO snapshots (server_id, ts, online, players_json) VALUES (%s,%s,TRUE,%s)",
                    (_sid, _t0, json.dumps(["anna", "bert", "cara"])))
        cur.execute("INSERT INTO snapshots (server_id, ts, online, players_json) VALUES (%s,%s,TRUE,%s)",
                    (_sid, _t0 + 40, json.dumps(["anna", "bert", "cara", _target, _noise])))
        _events.append(_t0 + 20)
    for _ts in _events:
        rm.learn_alias(cur, TSID, _sid, _ts, "online")
    _alias = rm.confirmed_alias(cur, TSID, _sid)
check("Alias korrekt gelernt (3 Join-Ereignisse)", _alias == "ziel", "-> %r" % _alias)

with rm._Conn() as (conn, cur):
    cur.execute("SELECT alias, score, events FROM alias_candidates WHERE steam_id=%s "
                "ORDER BY score DESC", (TSID,))
    _rows = [(r["alias"], r["score"], r["events"]) for r in cur.fetchall()]
check("Rauschen bekommt weniger Punkte als Ziel",
      _rows and _rows[0][0] == "ziel" and _rows[0][1] > (_rows[1][1] if len(_rows) > 1 else 0),
      _rows[:4])

# Gegenprobe: nur 2 Ereignisse duerfen NICHT bestaetigen
with rm._Conn() as (conn, cur):
    cur.execute("DELETE FROM alias_candidates WHERE steam_id=%s", (TSID,))
    for _ts in _events[:2]:
        rm.learn_alias(cur, TSID, _sid, _ts, "online")
    _a2 = rm.confirmed_alias(cur, TSID, _sid)
check("2 Ereignisse bestaetigen NICHT", _a2 is None, "-> %r" % _a2)

# aufraeumen
with rm._Conn() as (conn, cur):
    cur.execute("DELETE FROM alias_candidates WHERE steam_id=%s", (TSID,))
    cur.execute("DELETE FROM snapshots WHERE server_id=%s", (_sid,))
    cur.execute("DELETE FROM servers WHERE id=%s AND NOT EXISTS "
                "(SELECT 1 FROM watched_servers w WHERE w.server_id=%s)", (_sid, _sid))

print("\n== 14. BattleMetrics-Schutzschalter ==")
check("bm_get existiert", hasattr(rm, "bm_get"))
check("Schutzschalter-Status abfragbar", hasattr(rm, "bm_available"))
check("Token per Env konfigurierbar", hasattr(rm, "BM_TOKEN"))
rm._bm_state["fails"] = 0; rm._bm_state["blocked_until"] = 0; rm._bm_state["logged"] = False
check("startet geschlossen (BM erlaubt)", rm.bm_available() is True)
for _ in range(rm.BM_BREAKER_FAILS):
    rm._bm_note_result(False, 403)
check("oeffnet nach %d x 403" % rm.BM_BREAKER_FAILS, rm.bm_available() is False)
_blocked = False
try:
    rm.bm_get("https://api.battlemetrics.com/servers")
except RuntimeError:
    _blocked = True
except Exception:
    pass
check("bm_get blockt sofort (kein Netz-Timeout)", _blocked)
check("query_battlemetrics liefert leer statt zu haengen",
      rm.query_battlemetrics(region="eu", limit=10) == [])
check("query_bm_server liefert leer", rm.query_bm_server("1.2.3.4", 28015) == (None, []))
rm._bm_state["fails"] = 0; rm._bm_state["blocked_until"] = 0; rm._bm_state["logged"] = False
check("Erfolg setzt Zaehler zurueck", (rm._bm_note_result(True), rm.bm_available())[1] is True)

print("\n== 15. Snapshot-Speicheroptimierung ==")
check("Retention gesetzt", rm.SNAPSHOT_TTL_DAY >= 7, rm.SNAPSHOT_TTL_DAY)
check("Spielerliste wird nach <=48h ausgeduennt", rm.PLAYERS_JSON_KEEP_H <= 48, rm.PLAYERS_JSON_KEEP_H)
_TSRV = None
with rm._Conn() as (conn, cur):
    cur.execute("INSERT INTO servers (host, port, name, first_seen) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (host, port) DO UPDATE SET name=EXCLUDED.name RETURNING id",
                ("203.0.113.9", 28015, "SNAP-TEST", int(_t.time())))
    _TSRV = int(cur.fetchone()["id"])
    cur.execute("DELETE FROM snapshots WHERE server_id=%s", (_TSRV,))
    _pl = [{"name": "alice"}, {"name": "bob"}]
    _base = int(_t.time())
    # gleiche Liste 3x -> nur der erste Snapshot darf players_json haben
    for _i in range(3):
        rm._store_snapshot(cur, _TSRV, {"online": True, "queried_at": _base + _i,
                                        "players_list": _pl, "players_count": 2})
    cur.execute("SELECT count(*) AS n FROM snapshots WHERE server_id=%s AND players_json IS NOT NULL",
                (_TSRV,))
    _withjson = cur.fetchone()["n"]
check("gleiche Liste wird nur 1x gespeichert", _withjson == 1, "-> %s von 3" % _withjson)
with rm._Conn() as (conn, cur):
    rm._store_snapshot(cur, _TSRV, {"online": True, "queried_at": _base + 3,
                                    "players_list": [{"name": "alice"}, {"name": "carol"}],
                                    "players_count": 2})
    cur.execute("SELECT count(*) AS n FROM snapshots WHERE server_id=%s AND players_json IS NOT NULL",
                (_TSRV,))
    _w2 = cur.fetchone()["n"]
check("geaenderte Liste wird gespeichert", _w2 == 2, "-> %s" % _w2)
with rm._Conn() as (conn, cur):
    # Anzeige muss trotz NULL die letzte bekannte Liste liefern
    rm._store_snapshot(cur, _TSRV, {"online": True, "queried_at": _base + 4,
                                    "players_list": [{"name": "alice"}, {"name": "carol"}],
                                    "players_count": 2})
    _snap = rm._latest_snapshot(cur, _TSRV)
    _names = json.loads(_snap.get("players_json") or "[]")
check("Anzeige faellt auf letzte bekannte Liste zurueck",
      sorted(_names) == ["alice", "carol"], _names)
with rm._Conn() as (conn, cur):
    cur.execute("DELETE FROM snapshots WHERE server_id=%s", (_TSRV,))
    cur.execute("DELETE FROM servers WHERE id=%s AND NOT EXISTS "
                "(SELECT 1 FROM watched_servers w WHERE w.server_id=%s)", (_TSRV, _TSRV))

print("\n== 16. Browse: Single-Flight, Cache-Klassifikation, Paging ==")
check("browse_refresh existiert", hasattr(rm, "browse_refresh"))
check("Warmer-Loop existiert", hasattr(rm, "browse_warmer_loop"))
_t0 = _t.time(); _b1 = browse(limit=100); _d1 = _t.time() - _t0
_t0 = _t.time(); _b2 = browse(limit=100); _d2 = _t.time() - _t0
check("2. Aufruf aus dem Cache (<0.3s)", _d2 < 0.3, "%.3fs (1. Aufruf %.3fs)" % (_d2, _d1))
check("Klassifikation im Cache vorbereitet",
      all("category" in x and "tier" in x for x in _b2["servers"][:5]))
_raw = rm._cache_get("browse:all")
check("interne Felder im Cache", _raw and "name_lc" in _raw["servers"][0])
check("interne Felder NICHT in der Antwort", "name_lc" not in _b2["servers"][0])
check("in_watchlist in der Antwort", "in_watchlist" in _b2["servers"][0])

_p1 = browse(limit=50, offset=0, sort="pop_desc")
_p2 = browse(limit=50, offset=50, sort="pop_desc")
check("Seite 1 hat 50 Zeilen", len(_p1["servers"]) == 50, len(_p1["servers"]))
check("Seite 2 hat 50 Zeilen", len(_p2["servers"]) == 50, len(_p2["servers"]))
check("Seiten ueberschneiden sich nicht",
      not ({(x["host"], x["port"]) for x in _p1["servers"]} &
           {(x["host"], x["port"]) for x in _p2["servers"]}))
check("has_more korrekt", _p1.get("has_more") is True)
check("Reihenfolge bleibt ueber Seiten hinweg absteigend",
      _p1["servers"][-1]["players_count"] >= _p2["servers"][0]["players_count"])
_last = browse(limit=50, offset=max(0, _p1["matched"] - 10))
check("letzte Seite meldet has_more=False", _last.get("has_more") is False)
check("Cache wird durch Requests nicht veraendert",
      "in_watchlist" not in rm._cache_get("browse:all")["servers"][0])

print("\n== 17. Eingabe-Normalisierung (echte Nutzereingaben aus den Altdaten) ==")
for _inp, _port, _exp in [
    ("79.137.98.16:28015", 28015, ("79.137.98.16", 28015)),
    ("79.137.98.10:28015", 79,    ("79.137.98.10", 28015)),
    ("connect 2xmonthly.atlasrust.uk", 28010, ("2xmonthly.atlasrust.uk", 28010)),
    ("connect eu.moose.gg:28016", 28015, ("eu.moose.gg", 28016)),
    ("  64.40.9.132  ", 28015, ("64.40.9.132", 28015)),
]:
    try:
        _got = rm.normalize_server_input(_inp, _port)
        check("normalisiert %r" % _inp[:30], _got == _exp, "-> %r" % (_got,))
    except Exception as e:
        check("normalisiert %r" % _inp[:30], False, "Fehler %s" % e)
for _inp in ["76561199091530220", "https://steamid.uk/profile/76561198752317174",
             "https://steamcommunity.com/profiles/76561199348111897"]:
    try:
        rm.normalize_server_input(_inp, 28015)
        check("weist Steam-Eingabe ab: %r" % _inp[:34], False, "-> DURCHGELASSEN")
    except ValueError as e:
        check("weist Steam-Eingabe ab: %r" % _inp[:34], "steam" in str(e).lower(), str(e)[:40])

print("\n== 18. Steam-Freundesliste ==")
check("fetch_friend_list existiert", hasattr(rm, "fetch_friend_list"))
check("api_friends_in_rust existiert", hasattr(rm, "api_friends_in_rust"))
_fl = rm.fetch_friend_list(76561197960287930)   # oeffentliches Profil, Liste privat
check("private Freundesliste -> leere Liste statt Fehler", isinstance(_fl, list))
with rm._Conn() as (conn, cur):
    cur.execute("SELECT id FROM users LIMIT 1")
    _u = cur.fetchone()
if _u:
    _res = rm.api_friends_in_rust(int(_u["id"]))
    check("liefert Struktur mit available/friends",
          isinstance(_res, dict) and "available" in _res and "friends" in _res,
          list(_res.keys()))
    if _res.get("available"):
        check("jeder Eintrag hat Server-Infos",
              all("server_ip" in f and "on_watchlist" in f for f in _res["friends"]))
    else:
        print("  SKIP  (Freundesliste dieses Users privat/leer)")

print("\n== 19. Region-Erkennung & Filter ==")
check("offizieller EU-Tag -> eu", rm.detect_rust_region("[EU] Facepunch 1", ["eu", "vanilla"]) == "eu")
check("offizieller NA-Tag -> na", rm.detect_rust_region("Beliebiger Name", ["na"]) == "na")
check("Name 'US' -> na", rm.detect_rust_region("Rustoria US Long", []) == "na")
check("Name 'EU' -> eu", rm.detect_rust_region("Bloo Lagoon EU Main", []) == "eu")
check("Name 'SEA' -> ea", rm.detect_rust_region("Pickle SEA 5x", []) == "ea")
check("Name 'AU' -> oc", rm.detect_rust_region("OZ Rust AU", []) == "oc")
check("Wort 'for me' matcht NICHT wa", rm.detect_rust_region("chill server for me", []) is None)
check("Wort 'join us' matcht NICHT na", rm.detect_rust_region("Join us now", []) is None)
check("Tag schlaegt Namen (na-Tag trotz 'EU' im Namen)",
      rm.detect_rust_region("EU sounding name", ["na"]) == "na")
check("Alias us-e -> na", rm._REGION_FILTER_ALIASES.get("us-e") == "na")
check("Alias me -> wa", rm._REGION_FILTER_ALIASES.get("me") == "wa")
check("Asia-Gruppe = {wa, ea}", set(rm._REGION_GROUPS.get("asia", set())) == {"wa", "ea"})
# Live-Browse: Region-Filter liefert nur die passende Region.
try:
    _bn = rm.api_browse(user_id=None, region="na", q="rustafied", limit=10)
    check("browse na+rustafied: nur na-Server",
          all(s.get("region") == "na" for s in _bn.get("servers", [])),
          "matched=%s" % _bn.get("matched"))
    _ba = rm.api_browse(user_id=None, region="asia", q="", limit=25)
    check("browse asia: nur wa/ea-Server",
          all(s.get("region") in ("wa", "ea") for s in _ba.get("servers", [])),
          "matched=%s" % _ba.get("matched"))
    _be = rm.api_browse(user_id=None, region="eu", q="facepunch", limit=10)
    check("browse eu+facepunch: nur eu-Server",
          all(s.get("region") == "eu" for s in _be.get("servers", [])),
          "matched=%s" % _be.get("matched"))
except Exception as e:
    check("Live-Browse Region-Filter", False, "Fehler %s" % e)

print("\n== 20. Gruppenlimit-Erkennung & neue Browse-Filter ==")
_gl = rm.detect_rust_group_limit
check("'Rustoria Solo Only' -> 1",        _gl("Rustoria.com - EU Solo Only", []) == 1)
check("'Solo/Duo/Trio' -> 3 (Maximum)",   _gl("Rusty Moose |EU Solo/Duo/Trio|", []) == 3)
check("'Solos & Duos' (Plural) -> 2",     _gl("Warbandits Solos & Duos 2x", []) == 2)
check("'Max Group 4' schlaegt Woerter",   _gl("Vital EU Trio - Max Group 4", []) == 4)
check("'max 5' -> 5",                     _gl("Rustafied.com - EU Main max 5", []) == 5)
check("'team limit 3' -> 3",              _gl("Chill server team limit 3", []) == 3)
check("'5-man' -> 5",                     _gl("Bloo Lagoon 5-man EU", []) == 5)
check("'no group limit' -> 0",            _gl("Mega Clans no group limit", []) == 0)
check("'unlimited clans' -> 0",           _gl("Big Zerg unlimited clans", []) == 0)
check("kein Hinweis -> None",             _gl("Facepunch Amsterdam I", []) is None)
check("'quad' -> 4",                      _gl("Pickle Quad 5x", []) == 4)
check("'squad' matcht NICHT quad",        _gl("Squad up server", []) is None)
check("'3x' ist KEIN Gruppenlimit",       _gl("EU 3x Monthly", []) is None)
check("'duolingo' matcht NICHT duo",      _gl("duolingo fans", []) is None)
check("Tag 'trio' zaehlt auch",           _gl("Some Server", ["trio"]) == 3)
# Fresh-Sort + Browse-Felder (live gegen Cache — nur Feld-Existenz pruefen)
try:
    _bf = rm.api_browse(user_id=None, region="all", sort="fresh", limit=5)
    _rows = _bf.get("servers", [])
    check("browse sort=fresh liefert Zeilen", len(_rows) > 0, "n=%d" % len(_rows))
    check("Zeilen haben max_group-Feld",   all("max_group" in r for r in _rows))
    check("Zeilen haben pop_suspect-Feld", all("pop_suspect" in r for r in _rows))
    _lw = [(r.get("wipe") or {}).get("last_wipe_ts") or 0 for r in _rows]
    check("fresh: neuester Wipe zuerst", _lw == sorted(_lw, reverse=True), str(_lw))
    _bt = rm.api_browse(user_id=None, region="all", group="3", limit=10)
    check("group=3-Filter: nur max_group==3",
          all(r.get("max_group") == 3 for r in _bt.get("servers", [])),
          "matched=%s" % _bt.get("matched"))
    _bw = rm.api_browse(user_id=None, region="all", max_wipe_h=48, limit=10)
    import time as _t
    _now = int(_t.time())
    check("max_wipe_h=48: alle in den letzten 48h gewipet",
          all(0 <= _now - ((r.get("wipe") or {}).get("last_wipe_ts") or 0) <= 48*3600
              for r in _bw.get("servers", [])),
          "matched=%s" % _bw.get("matched"))
except Exception as e:
    check("Live-Browse neue Filter", False, "Fehler %s" % e)

print("\n" + "=" * 46)
print("ERGEBNIS:  %d bestanden, %d fehlgeschlagen" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
