#!/usr/bin/env python3
"""Watch dig designations until they are done, then exit.

Why this exists: mining completion is not a job-queue event in 53.17-r1
(`df.global.world.jobs.list` stays 0 while dwarves dig, and map blocks expose no job
container). The only reliable progress signal is the **designation count** falling to 0.
A blocking MCP tool cannot wait for that - the DSH tool-call timeout is ~120 s - so the
supported pattern is: run this as a background job, and let the harness deliver the
completion notice when it exits.

Usage:
  ./scripts/watch_dig.py                      # whole fort band, exits when nothing pending
  ./scripts/watch_dig.py --elev 38 --box 84,158,96,160 --every 15 --timeout 1800   # in-game Elevation
  ./scripts/watch_dig.py --markers            # also watch for DSH announcement markers in reports

Exit codes: 0 done, 2 timed out, 1 connection/HTTP failure.
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path, params=None, timeout=30):
    url = BASE + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def post(path, body, timeout=30):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def solid_in_box(elev, box):
    """Wall tiles remaining in the box, counted from the glyph map.

    Designation count -> 0 is NOT proof of digging: the game also cancels invalid
    designations. Tile state (`#` gone) is the proof, so the watcher reports both.
    """
    x0, y0, x1, y1 = box
    import re
    t = get("/world-map", {"e": elev, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                           "rle": "false", "max_tokens": 3000}, timeout=90)["text"]
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


def pending(elev=None, box=None):
    """Designation tiles currently pending, optionally restricted to a box on one level."""
    if elev is None:
        try:
            lk = get("/look", {"max_tokens": 2200}, timeout=90)
        except Exception:
            return None, "look failed"
        total = 0
        for l in lk["text"].splitlines():
            s = l.strip()
            if s.startswith("dig "):
                # "dig 37 tiles x84-96 y158-160 [DEFAULT_DIGx37]"
                total += int(s.split()[1])
        return total, None
    t = get("/world-objects", {"e0": elev, "e1": elev, "max_tokens": 1500}, timeout=90)["text"]
    x0, y0, x1, y1 = box if box else (None, None, None, None)
    total = 0
    for l in t.splitlines():
        s = l.strip()
        if not s.startswith("dig "):
            continue
        n = int(s.split()[1])
        if box:
            import re
            m = re.search(r"x(\d+)-(\d+) y(\d+)-(\d+)", s)
            if m:
                ax0, ax1, ay0, ay1 = map(int, m.groups())
                if ax1 < x0 or ax0 > x1 or ay1 < y0 or ay0 > y1:
                    continue
        total += n
    return total, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elev", type=int, default=None, help="restrict to one elevation")
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1 within --elev")
    ap.add_argument("--every", type=int, default=20, help="poll interval seconds")
    ap.add_argument("--timeout", type=int, default=2400, help="give up after N seconds")
    ap.add_argument("--markers", action="store_true", help="also report DSH: lines from get_reports")
    args = ap.parse_args()
    box = tuple(int(v) for v in args.box.split(",")) if args.box else None
    if box and args.elev is None:
        print("--box needs --elev", file=sys.stderr); return 1
    scope = f"elev {args.elev} box {box}" if args.elev else "fort band"
    t0 = time.time()
    last = None
    while True:
        try:
            get("/world-cache-reset", timeout=60)     # GET endpoint; POST answers 405
        except Exception as e:
            print(f"connection problem: {e}", file=sys.stderr)
        try:
            n, err = pending(args.elev, box)
        except Exception as e:
            print(f"read failed: {e}", file=sys.stderr)
            return 1
        if err:
            n = None
        if args.markers:
            try:
                reps = get("/get-reports", timeout=60)
                for r in reps.get("reports", []):
                    txt = r.get("text") or r.get("report") or json.dumps(r)
                    if "DSH:" in str(txt):
                        print(f"[marker] {txt}")
            except Exception:
                pass
        stamp = int(time.time() - t0)
        walls = None
        if args.elev is not None and box:
            try:
                walls = solid_in_box(args.elev, box)
            except Exception as e:
                print(f"tile proof read failed: {e}", file=sys.stderr)
        line = f"[{stamp}s] pending dig ({scope}): {n}" + (f" | wall tiles left: {walls}" if walls is not None else "")
        if (n, walls) != last:
            print(line, flush=True)
            last = (n, walls)
        if n == 0 and (walls is None or walls == 0):
            print(f"DIG COMPLETE after {stamp}s ({scope}) - designations cleared and no wall "
                  f"tiles left in the box", flush=True)
            return 0
        if n == 0 and walls is not None and walls > 0:
            print(f"DESIGNATIONS GONE but {walls} wall tiles remain in the box: the game "
                  f"cancelled them, it did not dig them", flush=True)
            return 3
        if time.time() - t0 > args.timeout:
            print(f"TIMEOUT after {args.timeout}s; pending dig: {n}", flush=True)
            return 2
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
