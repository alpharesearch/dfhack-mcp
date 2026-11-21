#!/usr/bin/env python3
"""Shared HTTP transport for the watcher scripts - survives a game restart.

Why this exists: the watchers (dig_run, sink_shaft, dig_levels, watch_*) died with
ConnectionRefusedError / HTTP 500 the moment the player quit Dwarf Fortress - the DFHack RPC
on :5000 disappears with the game, and every read through the MCP server fails. A watcher
that dies when the game is closed is useless for "tell me when the dig is done".

Behaviour: retries with backoff and tells you it is waiting. It gives up only after
`offline_grace` seconds (default 3600 = an hour of game-off time), which is longer than a
typical break between sessions.
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
OFFLINE_GRACE = 3600


class GameOffline(Exception):
    """Raised when the game has been offline longer than the grace period."""


def _call(method, path, params=None, body=None, timeout=200, offline_grace=OFFLINE_GRACE,
          announce_every=120):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    data = None if body is None else json.dumps(body).encode()
    t0 = time.time()
    last_announce = 0.0
    nap = 5
    while True:
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"},
                                         method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, OSError) as e:
            code = getattr(e, "code", None)
            if code is not None and 400 <= code < 500 and code != 429:
                raise                     # our bug, not the game's - surface it
            waited = time.time() - t0
            if waited > offline_grace:
                raise GameOffline(f"game offline {int(waited)}s at {path}: {e}")
            if waited - last_announce > announce_every:
                last_announce = waited
                print(f"[transport] game offline ({int(waited)}s), retrying {path}: {e}",
                      flush=True)
            time.sleep(nap)
            nap = min(nap * 2, 60)


def get(path, params=None, timeout=200, **kw):
    return _call("GET", path, params=params, timeout=timeout, **kw)


def post(path, body=None, timeout=200, **kw):
    return _call("POST", path, body=body if body is not None else {}, timeout=timeout, **kw)
