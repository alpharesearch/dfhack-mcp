#!/usr/bin/env python3
"""Prove a stockpile by counting what is ON it - the wiki's own definition of a pile.

A stockpile is a hauling contract: dwarves with hauling labours carry items to a pile that
accepts them. So the proof is the item count inside the pile's extent and its trend, not the
building's existence. This watcher counts items per class inside a rectangle at an in-game
Elevation, and can watch a pile by id (its extents are read from the game).

Usage:
  ./scripts/watch_pile.py --id 15 --every 30 --timeout 1800
  ./scripts/watch_pile.py --x0 52 --x1 57 --y0 116 --y1 120 --elev 38
Exit codes: 0 finished (timeout), 2 server/script error.
"""
import argparse
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"


def lua(expr, timeout=150):
    req = urllib.request.Request(
        BASE + "/game-lua", data=json.dumps({"expression": expr}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d.get("output") or d.get("error") or ""


COUNT = '''
local x0, x1, y0, y1, z = __X0__, __X1__, __Y0__, __Y1__, __Z__
local tally = {}
local total = 0
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  local ok, pos = pcall(function() return it.pos end)
  if ok and pos and pos.x >= x0 and pos.x <= x1 and pos.y >= y0 and pos.y <= y1 and (pos.z - 129) == z then
    local tn = "?"
    pcall(function() for k,v in pairs(df) do if v == it._type then tn = k end end end)
    tally[tn] = (tally[tn] or 0) + 1
    total = total + 1
  end
end
local lines = {}
for k,v in pairs(tally) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print("COUNT " .. total .. " " .. table.concat(lines, " "))
'''

EXTENTS = '''
local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == __ID__ then b = sp end end
if b == nil then print("FAIL pile __ID__ not found") return end
local on = {}
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
  if b.settings.flags[c] == true then on[#on+1] = c end
end
print("EXT " .. b.x1 .. " " .. b.x2 .. " " .. b.y1 .. " " .. b.y2 .. " " .. (b.z - 129) .. " " .. table.concat(on, ","))
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int)
    ap.add_argument("--x0", type=int)
    ap.add_argument("--x1", type=int)
    ap.add_argument("--y0", type=int)
    ap.add_argument("--y1", type=int)
    ap.add_argument("--elev", type=int)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    if a.id is not None:
        out = lua(EXTENTS.replace("__ID__", str(a.id)))
        line = [l for l in out.splitlines() if l.startswith("EXT ")]
        if not line:
            print("no extents:", out[:200])
            return 2
        f = line[0].split()
        x0, x1, y0, y1, elev = map(int, f[1:6])
        print(f"pile {a.id}: extent x{x0}-{x1} y{y0}-{y1} in-game {elev} accepts [{f[6]}]", flush=True)
    else:
        x0, x1, y0, y1, elev = a.x0, a.x1, a.y0, a.y1, a.elev

    t0 = time.time()
    last = None
    while True:
        code = (COUNT.replace("__X0__", str(x0)).replace("__X1__", str(x1))
                .replace("__Y0__", str(y0)).replace("__Y1__", str(y1))
                .replace("__Z__", str(elev)))
        out = lua(code)
        row = [l for l in out.splitlines() if l.startswith("COUNT ")]
        stamp = int(time.time() - t0)
        text = row[0][6:] if row else "ERR " + out[:120]
        if text != last:
            print(f"[{stamp}s] x{x0}-{x1} y{y0}-{y1} in-game {elev}: {text}", flush=True)
            last = text
        if time.time() - t0 > a.timeout:
            print(f"done after {a.timeout}s (count was {last})", flush=True)
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
