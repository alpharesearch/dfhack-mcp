#!/usr/bin/env python3
"""Watch a stack of levels at once - the Z-dimension of a fort plan.

Each level is a district (landing + hall + rooms) ordered in fog; digging completes them in
whichever order the crew reaches them, so progress must be reported per level, not as one
number. Completion per level uses the same rule as dig_run.py: FLOORS present with no walls
and no fog; pending designations are a queue, not a failure.

Usage:
  ./scripts/dig_levels.py --levels 37:36:118-128,32:36:118-128,31:36:118-128,30:36:118-128
  format per level: ELEV:X0-X1:Y0-Y1
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path, params=None, timeout=120):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def state(elev, x0, x1, y0, y1):
    t = get("/world-map", {"e": elev, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                           "rle": "false", "max_tokens": 2600})["text"]
    wall = fog = floor = 0
    for l in t.splitlines():
        if "|" not in l:
            continue
        y, row = l.split("|", 1)
        try:
            y = int(y.strip())
        except ValueError:
            continue
        if y0 <= y <= y1:
            cells = row[: (x1 - x0 + 1)]
            wall += cells.count("#"); fog += cells.count("?")
            floor += cells.count(".") + cells.count("=") + cells.count("/ ")
    t2 = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 700})["text"]
    pend = sum(int(l.split()[1]) for l in t2.splitlines() if l.strip().startswith("dig "))
    return wall, fog, floor, pend


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", required=True)
    ap.add_argument("--every", type=int, default=45)
    ap.add_argument("--timeout", type=int, default=10800)
    a = ap.parse_args()
    specs = []
    for item in a.levels.split(","):
        e, xs, ys = item.split(":")
        x0, x1 = map(int, xs.split("-")); y0, y1 = map(int, ys.split("-"))
        specs.append((int(e), x0, x1, y0, y1))
    t0 = time.time(); last = {}
    while True:
        done = 0
        for s in specs:
            st = state(*s)
            last[s] = st
            if st[0] == 0 and st[1] == 0:
                done += 1
        stamp = int(time.time() - t0)
        print(f"[{stamp}s] " + " | ".join(
            f"e{s[0]} walls{st[0]} fog{st[1]} floor{st[2]} pend{st[3]}" for s, st in
            ((s, last[s]) for s in specs)), flush=True)
        if done == len(specs):
            print(f"ALL LEVELS DUG after {stamp}s ({done}/{len(specs)})", flush=True)
            return 0
        if time.time() - t0 > a.timeout:
            print(f"TIMEOUT after {a.timeout}s; {done}/{len(specs)} complete", flush=True)
            return 2
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
