"""RustMetrics — A2S (Steam Server Query Protocol).

Reines Protokoll: UDP-Abfrage + Parsing. Kein DB-Zugriff, kein HTTP.
"""
from __future__ import annotations

import socket
import struct
import time

from rm_config import (A2S_CHALLENGE_TYPE, A2S_HEADER, A2S_INFO_REQUEST,
                       A2S_PLAYER_REQHDR, A2S_TIMEOUT)

def _read_cstring(data: bytes, pos: int) -> tuple[str, int]:
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", errors="replace"), end + 1


def query_a2s(host: str, port: int, timeout: float = None, include_players: bool = True) -> dict:
    if timeout is None: timeout = A2S_TIMEOUT
    result = {"host": host, "port": port, "online": False, "queried_at": int(time.time())}
    try:
        addr = (host, port)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            t0 = time.time()
            s.sendto(A2S_INFO_REQUEST, addr)
            data, _ = s.recvfrom(2048)
            if len(data) >= 5 and data[4:5] == A2S_CHALLENGE_TYPE:
                challenge = data[5:9]
                s.sendto(A2S_INFO_REQUEST + challenge, addr)
                data, _ = s.recvfrom(2048)
            result["ping_ms"] = int((time.time() - t0) * 1000)
            info = _parse_a2s_info(data)
            if not info:
                # Antwort ist kein A2S_INFO-Paket → nicht als online werten
                result["error"] = "invalid response"
                return result
            result.update(info)
            result["online"] = True
        if include_players:
            result.update(_query_a2s_players(addr, timeout))
    except TimeoutError:
        result["error"] = "timeout"
    except OSError as e:
        result["error"] = f"OSError: {e}"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def _query_a2s_players(addr: tuple[str, int], timeout: float) -> dict:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(A2S_HEADER + A2S_PLAYER_REQHDR + b"\xFF\xFF\xFF\xFF", addr)
            data, _ = s.recvfrom(4096)
            if len(data) >= 5 and data[4:5] == A2S_CHALLENGE_TYPE:
                challenge = data[5:9]
                s.sendto(A2S_HEADER + A2S_PLAYER_REQHDR + challenge, addr)
                data, _ = s.recvfrom(8192)
            if data[:4] == b"\xFE\xFF\xFF\xFF":
                return {"players_list": []}
            return {"players_list": _parse_a2s_players(data)}
    except Exception as e:
        return {"players_list": [], "players_error": f"{type(e).__name__}: {e}"}


def _parse_a2s_info(data: bytes) -> dict:
    if len(data) < 6 or data[4:5] != b"I":
        return {}
    pos = 5
    protocol = data[pos]; pos += 1
    name, pos    = _read_cstring(data, pos)
    mapname, pos = _read_cstring(data, pos)
    folder, pos  = _read_cstring(data, pos)
    game, pos    = _read_cstring(data, pos)
    app_id = struct.unpack_from("<H", data, pos)[0]; pos += 2
    players = data[pos]; pos += 1
    max_players = data[pos]; pos += 1
    bots = data[pos]; pos += 1
    server_type = chr(data[pos]); pos += 1
    environment = chr(data[pos]); pos += 1
    visibility = data[pos]; pos += 1
    vac = data[pos]; pos += 1
    version, pos = _read_cstring(data, pos)
    keywords, gameid = "", 0
    if pos < len(data):
        edf = data[pos]; pos += 1
        if edf & 0x80: pos += 2
        if edf & 0x10: pos += 8
        if edf & 0x40:
            pos += 2
            _, pos = _read_cstring(data, pos)
        if edf & 0x20: keywords, pos = _read_cstring(data, pos)
        if edf & 0x01:
            gameid = struct.unpack_from("<Q", data, pos)[0]; pos += 8
    return {
        "protocol": protocol, "name": name, "map": mapname,
        "folder": folder, "game": game, "app_id": app_id,
        "players_count": players, "max_players": max_players, "bots": bots,
        "server_type": server_type, "environment": environment,
        "visibility": visibility, "vac": vac, "version": version,
        "keywords": keywords, "gameid": gameid,
    }


def _parse_a2s_players(data: bytes) -> list[dict]:
    if len(data) < 6 or data[4:5] != b"D":
        return []
    count = data[5]; pos = 6
    out = []
    for _ in range(count):
        if pos >= len(data): break
        try:
            _idx = data[pos]; pos += 1
            name, pos = _read_cstring(data, pos)
            score    = struct.unpack_from("<i", data, pos)[0]; pos += 4
            duration = struct.unpack_from("<f", data, pos)[0]; pos += 4
            out.append({"name": name, "score": score, "duration_s": int(duration)})
        except Exception:
            break
    return out
