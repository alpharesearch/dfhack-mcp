#!/usr/bin/env python3
"""Watch for water where a shaft crossed an aquifer - the flood the game warned about.

Digging through an aquifer band (here: elev 33-34, and a second band at 25) means water
follows the shaft down. This job watches the shaft column and the level below it and exits
the moment water appears, so the notice arrives on its own and nobody has to poll.

Usage:
  ./scripts/watch_water.py --x 50 --y 123 --top 39 --bottom 24 --every 30 --timeout 7200
Exit: 0 water appeared (with the tiles), 2 timed out with none, 1 read failure.
"""
import argparse
import json
import sys
import time
import urllib.request
import sys
sys.path.insert(0, __file__.rsplit('/', 1)[0])
import dfw

BASE = "http://127.0.0.1:8000"

def get(path, params=None, timeout=200):
    return dfw.get(path, params, timeout=timeout)


def wet_tiles(x, y, top, bottom):
    wet = []
    for e in range(bottom, top + 1):
        for xx in (x, x + 1):
            ti = get("/tile-at", {"x": xx, "y": y, "elev": e}, timeout=90)
            w = ti.get("water", 0)
            if w:
                wet.append((xx, e, int(w)))
    return wet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--top", type=int, required=True)
    ap.add_argument("--bottom", type=int, required=True)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args()
    t0 = time.time()
    last = 0
    while True:
        try:
            wet = wet_tiles(a.x, a.y, a.top, a.bottom)
        except Exception as e:
            print(f"read failed: {e}", file=sys.stderr)
            return 1
        stamp = int(time.time() - t0)
        if len(wet) != last:
            print(f"[{stamp}s] wet tiles in the shaft column: {len(wet)} {wet[:6]}", flush=True)
            last = len(wet)
        if wet:
            total = sum(w for _, _, w in wet)
            print(f"WATER IN THE SHAFT after {stamp}s: {len(wet)} tiles, total level {total}. "
                  f"Tiles: {wet[:12]}. Drainage is now the job: seal/redirect the stairwell, "
                  f"cistern above the working level, pumps as the long chain.", flush=True)
            return 0
        if time.time() - t0 > a.timeout:
            print(f"NO INFLOW after {a.timeout}s (shaft dry so far)", flush=True)
            return 2
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
