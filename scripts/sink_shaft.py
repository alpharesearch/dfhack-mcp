#!/usr/bin/env python3
"""Sink a stair shaft one revealed level at a time - the fog loop a player runs.

You cannot designate a shaft into fog: unexplored tiles silently reject designations. So the
loop is: designate the stair tile on the current level, wait for the dwarves to dig it,
re-read the column (the dig reveals the level below), check the verdict there (solid / damp /
fog), and only then designate the next level. This script runs that loop to a target
elevation and reports each step, so it can be a background job whose completion is the
notification.

Usage:
  ./scripts/sink_shaft.py --x 50 --y 123 --from 38 --to 25 [--width 2] [--every 15] [--per-level 1200]
Exit: 0 target reached, 2 a level timed out, 3 a level was refused (damp/fog/open), 1 read failure.
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
Z_OFF = 129   # in-game Elevation = z - 129 (the map panel number)

def get(path, params=None, timeout=200):
    return dfw.get(path, params, timeout=timeout)

def post(path, body=None, timeout=200):
    return dfw.post(path, body, timeout=timeout)


def pending_at(elev):
    t = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 900})["text"]
    return sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig"))


def column_water(x, y, e_from, e_to):
    """Water tiles in the shaft column between two elevations (inclusive)."""
    wet = []
    for e in range(e_to, e_from + 1):
        for xx in (x, x + 1):
            ti = get("/tile-at", {"x": xx, "y": y, "elev": e}, timeout=90)
            if ti.get("water", 0):
                wet.append((xx, e, ti["water"]))
    return wet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--from", dest="from_e", type=int, required=True)
    ap.add_argument("--to", dest="to_e", type=int, required=True)
    ap.add_argument("--width", type=int, default=2, help="stair tiles along x")
    ap.add_argument("--depth", type=int, default=2,
                    help="stair tiles along y - a MAIN staircase is 2x2 (or 3x3): a 1-wide "
                         "stair is where a dwarf hauling a barrel, a log or a block stops the fort")
    ap.add_argument("--every", type=int, default=15)
    ap.add_argument("--per-level", type=int, default=1200)
    ap.add_argument("--through-damp", action="store_true",
                    help="keep sinking when a level reads DAMP (the player ordered the shaft "
                         "through the aquifer). The default STOP is the safer rule: water "
                         "follows a shaft down, so a drain must exist before the band is "
                         "opened. With this flag every damp level is logged as a warning.")
    a = ap.parse_args()

    damp_levels = []
    for elev in range(a.from_e, a.to_e - 1, -1):
        # Stair footprint = width x depth on every level (main stairs 2x2, never 1-wide).
        tiles = [[a.x + i, a.y + j, elev + Z_OFF] for i in range(a.width) for j in range(a.depth)]
        sent = post("/send-dig-command", {"designation": "UP_DOWN_STAIR_DIG", "locations": tiles}).get("tiles")
        print(f"[elev {elev}] stair designations accepted: {sent}/{len(tiles)} ({a.width}x{a.depth})", flush=True)
        if not sent:
            print(f"  refused at elev {elev}: fog or invalid site - stop, probe a neighbour", flush=True)
            return 3
        get("/world-cache-reset")
        # The player's rule: JUST STOP WHEN WET. A damp *flag* is a warning we log;
        # actual water in the shaft column is the stop condition - water follows a shaft
        # down, so the moment it appears, the descent ends and the drain becomes the job.
        wet = column_water(a.x, a.y, a.to_e, elev)
        if wet:
            print(f"  WET at elev {elev}: {wet} - STOPPING the stairwell here as instructed",
                  flush=True)
            return 4
        t0 = time.time()
        while pending_at(elev) > 0:
            if time.time() - t0 > a.per_level:
                print(f"  elev {elev}: timed out with designations still pending", flush=True)
                return 2
            time.sleep(a.every)
            get("/world-cache-reset")
        # The dig revealed the level below. Ask the model what that tile is before ordering it.
        wet = column_water(a.x, a.y, a.to_e, elev)
        if wet:
            print(f"  WET after digging elev {elev}: {wet} - STOP, do not open the band "
                  f"further; plan drainage before building below", flush=True)
            return 4
        if elev > a.to_e:
            rows = get("/probe-plan", {"x": a.x, "y": a.y, "elev0": elev - 1, "elev1": elev - 1})["text"]
            line = [l for l in rows.splitlines() if "elev" in l][-1].strip()
            print(f"  elev {elev} dug. next level reads: {line}", flush=True)
            if "not diggable" in line:
                print(f"  elev {elev-1} is open air - stop here", flush=True)
                return 3
            if "avoid" in line:
                if not a.through_damp:
                    print(f"  elev {elev-1} DAMP - stop (plan the drain, or pass --through-damp)",
                          flush=True)
                    return 3
                print(f"  elev {elev-1} DAMP - CONTINUING by request: water follows this "
                      f"shaft down. Log it: {line}", flush=True)
                damp_levels.append(elev - 1)
        else:
            print(f"[elev {elev} / ui {elev-1}] shaft complete at target elevation", flush=True)
    if damp_levels:
        print(f"SHAFT CROSSES THE DAMP BAND at elevations {damp_levels} - expect inflow; "
              f"plan the cistern/pump/sealed-stairwell drainage before building below", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
