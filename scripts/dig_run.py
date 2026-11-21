#!/usr/bin/env python3
"""Designate a whole plan, then watch what the game makes of it.

The player's rule: you can designate anywhere - the game answers LATER whether dwarves can
dig it. Measured: a DEFAULT_DIG on fog tiles at elev 36 (x48,y122-124, hidden=True) is
accepted (tiles:3) and shows up in world-objects as a dig cluster. So do NOT gate a
designation on visibility, reachability, or a probed roof - order the plan, then measure.

Progress is measured by TILE TRANSITIONS (wall tiles remaining in the box), not by the
designation count: designations vanish when dug AND when cancelled, and designations the
dwarves cannot reach simply stay pending. A box that stops shrinking is a blocked plan, and
that is a fact worth reporting, not a bug.

Usage:
  ./scripts/dig_run.py --elev 36 --x0 43 --x1 49 --y0 122 --y1 124 [--band-first middle]
                       [--every 20] [--timeout 2400] [--no-dig-designate]
Exit: 0 box fully dug, 2 stalled (designations left, nothing moving), 1 read failure.
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


def box_state(elev, x0, x1, y0, y1):
    """(walls, fog, floors, other) in the box. Fog is NOT freedom to call it done:
    a box of unexplored rock has zero '#' walls, which is how the first version of this
    script announced DUG COMPLETE at 0s. Completion means FLOORS with no walls and no fog."""
    t = get("/world-map", {"e": elev, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                           "rle": "false", "max_tokens": 3000}, timeout=120)["text"]
    wall = fog = floor = other = 0
    for l in t.splitlines():
        if "|" not in l:
            continue
        y, row = l.split("|", 1)
        try:
            y = int(y.strip())
        except ValueError:
            continue
        if not (y0 <= y <= y1):
            continue
        cells = row[: (x1 - x0 + 1)]
        wall += cells.count("#")
        fog += cells.count("?")
        floor += cells.count(".") + cells.count("=")
        other += sum(1 for c in cells if c not in "#?.=")
    return wall, fog, floor, other


def walls_in(elev, x0, x1, y0, y1):
    t = get("/world-map", {"e": elev, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                           "rle": "false", "max_tokens": 3000}, timeout=120)["text"]
    n = 0
    for l in t.splitlines():
        if "|" not in l:
            continue
        y, row = l.split("|", 1)
        try:
            y = int(y.strip())
        except ValueError:
            continue
        if y0 <= y <= y1:
            n += row[: (x1 - x0 + 1)].count("#")
    return n


def pending_in(elev, x0, x1, y0, y1):
    """Designation tiles pending inside a box on one elevation.

    Line form from the server: "dig 66 tiles x27-42 y121-126 [DEFAULT_DIGx66]" - the x
    range is field 3, NOT field 2 ("tiles"). An earlier version indexed p[2]/p[3], the
    parse threw, and every read said pending 0: a finished plan then looked like
    "STALLED: nothing designated". Parse with a pattern, and treat 'no match' as no data
    rather than as zero.
    """
    import re
    t = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 1200})["text"]
    n = 0
    seen = False
    for l in t.splitlines():
        s = l.strip()
        if not s.startswith("dig "):
            continue
        m = re.search(r"x(\d+)-(\d+) y(\d+)-(\d+)", s)
        if not m:
            continue
        seen = True
        dx0, dx1, dy0, dy1 = map(int, m.groups())
        if dx0 <= x1 and dx1 >= x0 and dy0 <= y1 and dy1 >= y0:
            n += int(s.split()[1])
    return n if seen or not any(l.strip().startswith("dig ") for l in t.splitlines()) else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elev", type=int, required=True)
    ap.add_argument("--x0", type=int, required=True)
    ap.add_argument("--x1", type=int, required=True)
    ap.add_argument("--y0", type=int, required=True)
    ap.add_argument("--y1", type=int, required=True)
    ap.add_argument("--kind", default="DEFAULT_DIG")
    ap.add_argument("--no-designate", action="store_true", help="only watch, do not order")
    ap.add_argument("--every", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()
    box = (a.elev, a.x0, a.x1, a.y0, a.y1)

    if not a.no_designate:
        # Order the whole plan in one pass. Middle rows first so the accessible part of the
        # band gets worked before the flanks - a courtesy to the pathfinder, not a gate.
        mid = (a.y0 + a.y1) // 2
        ys = sorted(range(a.y0, a.y1 + 1), key=lambda y: abs(y - mid))
        tiles = [[x, y, a.elev + Z_OFF] for x in range(a.x0, a.x1 + 1) for y in ys]
        r = post("/send-dig-command", {"designation": a.kind, "locations": tiles})
        print(f"designated {r.get('tiles')}/{len(tiles)} tiles at elev {a.elev} "
              f"(ui {a.elev-1}) box x{a.x0}-{a.x1} y{a.y0}-{a.y1}", flush=True)

    get("/world-cache-reset")
    t0 = time.time()
    last = None
    stall_at = None
    while True:
        w, fog, floor, other = box_state(*box)
        pd = pending_in(*box)
        stamp = int(time.time() - t0)
        if (w, fog, floor, pd) != last:
            print(f"[{stamp}s] walls: {w} | fog: {fog} | floors+stairs: {floor} | "
                  f"pending: {pd}", flush=True)
            last = (w, fog, floor, pd)
            stall_at = stamp
        if w == 0 and fog == 0:
            print(f"DUG COMPLETE after {stamp}s - the box reads as floor/stair, no walls, "
                  f"no fog ({floor} dug tiles)", flush=True)
            return 0
        if time.time() - t0 > a.timeout:
            print(f"TIMEOUT after {a.timeout}s: walls {w}, pending {pd}. "
                  f"Pending designations = the game has not made this reachable.", flush=True)
            return 2
        if stall_at is not None and stamp - stall_at > 40 and w > 0 and pd == 0 and stamp > 90:
            print(f"STALLED at {stamp}s: {w} wall tiles left and nothing designated - "
                  f"the game cancelled the rest (no access, invalid site, or hazard)", flush=True)
            return 2
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
