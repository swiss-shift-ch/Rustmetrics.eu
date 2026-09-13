"""RustMetrics — Postgres.

Connection-Pool, _Conn-Context-Manager, Schema-Migrationen und Indizes.
Alles was direkt mit der Datenbank redet, aber keine Business-Logik.
"""
from __future__ import annotations

import sys

import psycopg2
import psycopg2.extras
import psycopg2.pool

from rm_config import PG_DSN, _env

_pool: psycopg2.pool.ThreadedConnectionPool = None  # type: ignore

def init_pool():
    global _pool
    # minconn hoch genug, dass nicht bei fast jedem Zugriff eine neue
    # PG-Verbindung aufgebaut wird (psycopg2 schliesst alles ueber minconn
    # beim Zurueckgeben wieder).
    _pool = psycopg2.pool.ThreadedConnectionPool(
        minconn=_env("RM_PG_MINCONN", 8, int),
        maxconn=_env("RM_PG_MAXCONN", 25, int),
        dsn=PG_DSN
    )

class _Conn:
    """Context-Manager: holt Connection aus Pool, gibt sie sicher zurück."""
    def __init__(self, cursor=True):
        self._cursor = cursor
    def __enter__(self):
        self.conn = _pool.getconn()
        try:
            self.cur = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor) if self._cursor else None
        except Exception:
            # Sonst bleibt die Connection fuer immer in _pool._used haengen
            # (z.B. tote Verbindung nach Postgres-Neustart) -> Pool laeuft leer.
            try: _pool.putconn(self.conn, close=True)
            except Exception: pass
            raise
        return (self.conn, self.cur) if self._cursor else self.conn
    def __exit__(self, exc_type, exc, tb):
        if self.cur:
            try: self.cur.close()
            except Exception: pass
        try:
            if exc_type is None: self.conn.commit()
            else: self.conn.rollback()
        finally:
            _pool.putconn(self.conn)


def ensure_migrations():
    """
    Idempotente Schema-Upgrades für bestehende Installationen (ohne init.sql-Rerun).
    Fehlertolerant: Wenn der App-DB-User keine DDL-Rechte hat (z.B. Tabellen-Owner
    ist postgres), loggen wir eine Anleitung statt den Startup zu killen — die
    Statements können dann einmalig als Superuser ausgeführt werden.
    """
    def _column_exists(cur, table, column) -> bool:
        cur.execute("SELECT 1 FROM information_schema.columns "
                    "WHERE table_name=%s AND column_name=%s", (table, column))
        return cur.fetchone() is not None

    def _table_exists(cur, table) -> bool:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        return (cur.fetchone() or [None])[0] is not None

    # (Bedingung, DDL) — DDL wird nur versucht, wenn wirklich nötig. Verhindert,
    # dass bei fremdem Tabellen-Owner bei JEDEM Start eine WARN geloggt wird.
    todo = []
    with _Conn() as (conn, cur):
        # Spalten, die der Code benutzt, die aber in aelteren init.sql-Staenden
        # fehlten — sonst stirbt der Poller auf frischen Installationen still.
        for tbl, col, ddl in (
            ("users", "discord_webhook", "ALTER TABLE users ADD COLUMN discord_webhook TEXT"),
            ("users", "notify_online",
             "ALTER TABLE users ADD COLUMN notify_online BOOLEAN NOT NULL DEFAULT TRUE"),
            ("users", "notify_offline",
             "ALTER TABLE users ADD COLUMN notify_offline BOOLEAN NOT NULL DEFAULT FALSE"),
            ("servers", "is_anonymized",
             "ALTER TABLE servers ADD COLUMN is_anonymized BOOLEAN NOT NULL DEFAULT FALSE"),
            ("watched_players", "bm_player_id",
             "ALTER TABLE watched_players ADD COLUMN bm_player_id BIGINT"),
            ("users", "last_seen_at", "ALTER TABLE users ADD COLUMN last_seen_at BIGINT"),
        ):
            if not _column_exists(cur, tbl, col):
                todo.append(ddl)
        if not _column_exists(cur, "watched_players", "steam_id"):
            todo.append("ALTER TABLE watched_players ADD COLUMN steam_id BIGINT")
        if not _table_exists(cur, "alias_candidates"):
            todo.append("""CREATE TABLE alias_candidates (
                   steam_id   BIGINT  NOT NULL,
                   server_id  INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                   alias      TEXT    NOT NULL,
                   score      INTEGER NOT NULL DEFAULT 1,
                   events     INTEGER NOT NULL DEFAULT 1,
                   first_seen BIGINT  NOT NULL DEFAULT 0,
                   last_seen  BIGINT  NOT NULL DEFAULT 0,
                   PRIMARY KEY (steam_id, server_id, alias)
               )""")
        if not _table_exists(cur, "steam_presence"):
            todo.append("""CREATE TABLE steam_presence (
                   steam_id    BIGINT PRIMARY KEY,
                   persona     TEXT,
                   visible     BOOLEAN,
                   in_rust     BOOLEAN NOT NULL DEFAULT FALSE,
                   server_addr TEXT,
                   updated_at  BIGINT NOT NULL DEFAULT 0
               )""")
        if not _table_exists(cur, "presence_sessions"):
            todo.append("""CREATE TABLE presence_sessions (
                   id          BIGSERIAL PRIMARY KEY,
                   steam_id    BIGINT NOT NULL,
                   server_addr TEXT,
                   start_ts    BIGINT NOT NULL,
                   end_ts      BIGINT
               )""")
        if not _table_exists(cur, "traffic_daily"):
            todo.append("""CREATE TABLE traffic_daily (
                   day            DATE PRIMARY KEY,
                   hits_total     INTEGER NOT NULL DEFAULT 0,
                   unique_ips     INTEGER NOT NULL DEFAULT 0,
                   reddit_hits    INTEGER NOT NULL DEFAULT 0,
                   top_paths      JSONB   NOT NULL DEFAULT '[]',
                   top_referers   JSONB   NOT NULL DEFAULT '[]',
                   updated_at     BIGINT  NOT NULL DEFAULT 0
               )""")
    for stmt in todo:               # leer = Schema aktuell, kein Log-Rauschen
        try:
            with _Conn() as (conn, cur):
                cur.execute(stmt)
            print(f"[migrate] OK: {' '.join(stmt.split())[:70]}", flush=True)
        except Exception as e:
            print(f"[migrate] WARN: {e} — Statement manuell als Superuser ausführen:\n"
                  f"         {' '.join(stmt.split())}", file=sys.stderr, flush=True)
    ensure_indexes()


# Indizes fuer Queries, die sonst Seq-Scans ueber Millionen Zeilen fahren.
# IF NOT EXISTS ist billig; laeuft bei jedem Start, legt aber nur Fehlendes an.
_WANTED_INDEXES = [
    ("idx_watched_players_steam",
     "CREATE INDEX IF NOT EXISTS idx_watched_players_steam "
     "ON watched_players(steam_id) WHERE steam_id IS NOT NULL"),
    # stuendlicher Cleanup-DELETE lief bisher ohne passenden Index
    ("idx_snapshots_ts",
     "CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)"),
    # Online-Rhythmus: Sessions einer SteamID im Zeitfenster
    ("idx_presence_sessions_steam",
     "CREATE INDEX IF NOT EXISTS idx_presence_sessions_steam "
     "ON presence_sessions(steam_id, start_ts)"),
    ("idx_psessions_endts",
     "CREATE INDEX IF NOT EXISTS idx_psessions_endts "
     "ON player_sessions(end_ts) WHERE end_ts IS NOT NULL"),
    # pro Server pro Poll-Runde ausgefuehrt
    ("idx_wp_server",
     "CREATE INDEX IF NOT EXISTS idx_wp_server ON watched_players(server_id)"),
    # pro Spieler-Transition ausgefuehrt (Discord-Notify-Lookup)
    ("idx_wp_server_lname",
     "CREATE INDEX IF NOT EXISTS idx_wp_server_lname "
     "ON watched_players(server_id, lower(name))"),
    ("idx_players_lname",
     "CREATE INDEX IF NOT EXISTS idx_players_lname ON players(lower(current_name))"),
    # players_json wird nur bei Aenderung geschrieben -> "letzte bekannte Liste"
    # muss ohne Scan ueber die NULL-Luecken gefunden werden
    ("idx_snapshots_players",
     "CREATE INDEX IF NOT EXISTS idx_snapshots_players ON snapshots(server_id, ts DESC) "
     "WHERE players_json IS NOT NULL"),
]

def ensure_indexes() -> None:
    created = 0
    for name, ddl in _WANTED_INDEXES:
        try:
            with _Conn() as (conn, cur):
                cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
                if (cur.fetchone() or [None])[0] is not None:
                    continue
                cur.execute(ddl)
            created += 1
            print(f"[migrate] Index angelegt: {name}", flush=True)
        except Exception as e:
            print(f"[migrate] WARN Index {name}: {e}", file=sys.stderr, flush=True)
    if created:
        print(f"[migrate] {created} Index(e) neu angelegt", flush=True)
