#!/usr/bin/env python3
"""Sink a stair shaft one revealed level at a time - the fog loop a player runs.

You cannot designate a shaft into fog: unexplored tiles silently reject designations. So the
loop is: designate the stair tile on the current level, wait for the dwarves to dig it,
re-read the column (the dig reveals the level below), check the verdict there (solid / damp /
fog), and only then designate the next level. This script runs that loop to a target
elevation and reports each step, so it can be a background job whose completion is the
notification.

Usage:
  ./scripts/sink_shaft.py --x 50 --y 123 --from 39 --to 36 [--width 2] [--every 15] [--per-level 1200]
Exit: 0 target reached, 2 a level timed out, 3 a level was refused (damp/fog/open), 1 read failure.
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
Z_OFF = 128


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


def pending_at(elev):
    t = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 900})["text"]
    return sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--from", dest="from_e", type=int, required=True)
    ap.add_argument("--to", dest="to_e", type=int, required=True)
    ap.add_argument("--width", type=int, default=2, help="stair tiles along x")
    ap.add_argument("--every", type=int, default=15)
    ap.add_argument("--per-level", type=int, default=1200)
    a = ap.parse_args()

    for elev in range(a.from_e, a.to_e - 1, -1):
        tiles = [[a.x + i, a.y, elev + Z_OFF] for i in range(a.width)]
        sent = post("/send-dig-command", {"designation": "UP_DOWN_STAIR_DIG", "locations": tiles}).get("tiles")
        print(f"[elev {elev} / ui {elev-1}] stair designations accepted: {sent}/{a.width}", flush=True)
        if not sent:
            print(f"  refused at elev {elev}: fog or invalid site - stop, probe a neighbour", flush=True)
            return 3
        get("/world-cache-reset")
        t0 = time.time()
        while pending_at(elev) > 0:
            if time.time() - t0 > a.per_level:
                print(f"  elev {elev}: timed out with designations still pending", flush=True)
                return 2
            time.sleep(a.every)
            get("/world-cache-reset")
        # The dig revealed the level below. Ask the model what that tile is before ordering it.
        if elev > a.to_e:
            rows = get("/probe-plan", {"x": a.x, "y": a.y, "elev0": elev - 1, "elev1": elev - 1})["text"]
            line = [l for l in rows.splitlines() if "elev" in l][-1].strip()
            print(f"  elev {elev} dug. next level reads: {line}", flush=True)
            if "avoid" in line or "not diggable" in line:
                print(f"  elev {elev-1} refused by the hazard/open check - stop here", flush=True)
                return 3
        else:
            print(f"[elev {elev} / ui {elev-1}] shaft complete at target elevation", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
