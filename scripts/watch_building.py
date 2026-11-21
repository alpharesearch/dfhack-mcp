#!/usr/bin/env python3
"""Watch a building until the game reports it active (built), then exit.

Play-faithful completion check: `dfhack.buildings.isActive(bld)` on the tile we placed at.
Reads through the server's internal game-lua channel, so nothing here is a model tool.

Usage:
  ./scripts/watch_building.py --x 104 --y 24 --elev 37 --every 20 --timeout 2700
Exit: 0 active/built, 2 timed out, 1 read failure.
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
Z_OFFSET = 128


def lua(expr, timeout=90):
    req = urllib.request.Request(BASE + "/game-lua", data=json.dumps({"expression": expr}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("output") or d.get("error") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--elev", type=int, required=True)
    ap.add_argument("--every", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=2700)
    args = ap.parse_args()
    z = args.elev + Z_OFFSET
    t0 = time.time()
    last = None
    while True:
        out = lua(f'''
local ok,b = pcall(function() return dfhack.buildings.findAtTile({args.x},{args.y},{z}) end)
if not ok or b == nil then print("OK none") return end
local oka,a = pcall(function() return dfhack.buildings.isActive(b) end)
local okm,m = pcall(function() return dfhack.buildings.markedForRemoval(b) end)
print("OK active="..tostring(oka and a).." type="..tostring(b._type):match("<type: ([_%w%)]+)"))
'''.strip())
        line = next((l.strip() for l in (out or "").splitlines() if l.strip().startswith("OK ")), "")
        val = "active=true" in line
        stamp = int(time.time() - t0)
        if line != last:
            print(f"[{stamp}s] {line}", flush=True)
            last = line
        if val:
            print(f"BUILDING COMPLETE after {stamp}s at x{args.x},y{args.y},elev{args.elev}", flush=True)
            return 0
        if time.time() - t0 > args.timeout:
            print(f"TIMEOUT after {args.timeout}s; last: {line}", flush=True)
            return 2
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
