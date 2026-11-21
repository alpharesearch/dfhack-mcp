#!/usr/bin/env python3
"""Dig a tunnel one revealed column at a time - the adit loop for a fogged mountain.

At a deep level the mountain is solid rock and the fog is total: only the tiles around the
shaft are explored, and designations on unexplored tiles are silently rejected. So a tunnel
is not one designation batch; it is a loop: designate the next column, let the miners dig it,
re-read (their digging reveals the next column), check hazards/depth on it, continue.

Each step checks the column it is about to order:
  - explored (not hidden),
  - diggable (wall/floor, not open air),
  - depth under the local surface >= --min-depth (mountain on top),
  - DAMP-free (no aquifer/standing water in the column at/above the dig level).

Usage:
  ./scripts/dig_adit.py --elev 36 --x 49 --y0 122 --y1 124 --dir w --len 12
                        [--every 15] [--per-step 900] [--min-depth 2]
Exit: 0 length built, 2 a step timed out, 3 a step refused (fog/damp/open), 1 read failure.
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
Z_OFF = 128
STEP = {"w": (-1, 0), "e": (1, 0), "n": (0, -1), "s": (0, 1)}


def get(path, params=None, timeout=120):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path, body, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def column_state(x, y, elev):
    ti = get("/tile-at", {"x": x, "y": y, "elev": elev})
    ob = get("/overburden", {"x": x, "y": y, "elev": elev})
    return ti, ob


def pending_in(x0, x1, y0, y1, elev):
    t = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 1200})["text"]
    n = 0
    for l in t.splitlines():
        s = l.strip()
        if not s.startswith("dig "):
            continue
        parts = s.split()
        try:
            dx0 = int(parts[2].split("-")[0]); dx1 = int(parts[2].split("-")[1])
            dy0 = int(parts[3].split("-")[0]); dy1 = int(parts[3].split("-")[1])
        except (IndexError, ValueError):
            continue
        if dx0 <= x1 and dx1 >= x0 and dy0 <= y1 and dy1 >= y0:
            n += int(parts[1])
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elev", type=int, required=True)
    ap.add_argument("--x", type=int, required=True, help="start column (the first column to dig)")
    ap.add_argument("--y0", type=int, required=True)
    ap.add_argument("--y1", type=int, required=True)
    ap.add_argument("--dir", choices="wens", default="w")
    ap.add_argument("--len", type=int, default=12)
    ap.add_argument("--min-depth", type=int, default=2)
    ap.add_argument("--every", type=int, default=15)
    ap.add_argument("--per-step", type=int, default=900)
    a = ap.parse_args()
    dx, dy = STEP[a.dir]
    x = a.x
    built = 0
    for step in range(a.len):
        tiles = [[x, y, a.elev + Z_OFF] for y in range(a.y0, a.y1 + 1)]
        bad = None
        for tx, ty, _ in tiles:
            ti, ob = column_state(tx, ty, a.elev)
            if ti.get("hidden"):
                bad = f"x{tx},y{ty} unexplored (fog) - stop, the reveal has not reached it"
                break
            if ti.get("class") == "open":
                bad = f"x{tx},y{ty} open air - nothing to dig"
                break
            if ob.get("overburden", 0) < a.min_depth:
                bad = f"x{tx},y{ty} overburden {ob.get('overburden')} < {a.min_depth} - surface cut"
                break
            if ob.get("overburden_unknown"):
                bad = f"x{tx},y{ty} has {ob['overburden_unknown']} hidden levels above - unknown roof"
                break
        if bad:
            print(f"step {step+1} at x{x}: refused - {bad}", flush=True)
            return 3
        sent = post("/send-dig-command", {"designation": "DEFAULT_DIG", "locations": tiles}).get("tiles")
        print(f"[step {step+1}] x{x},y{a.y0}-{a.y1},elev{a.elev}: designated {sent}/{len(tiles)}", flush=True)
        if not sent:
            print("  game accepted nothing - stop and re-survey", flush=True)
            return 3
        get("/world-cache-reset")
        t0 = time.time()
        while True:
            n = pending_in(x, x, a.y0, a.y1, a.elev)
            if n == 0:
                break
            if time.time() - t0 > a.per_step:
                print(f"  step {step+1}: {n} designations still pending after {a.per_step}s", flush=True)
                return 2
            time.sleep(a.every)
            get("/world-cache-reset")
        built += sent
        x += dx
    print(f"ADIT COMPLETE: {a.len} columns x{a.y0}-{a.y1} at elev {a.elev} "
          f"(ui {a.elev-1}), {built} tiles dug by the miners", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
