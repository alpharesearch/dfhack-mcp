import asyncio, sys, json, httpx
sys.path.insert(0,'.')
import worldmodel as WM

Y = 123
LEVELS = {37: "farm", 32: "industry-upper", 31: "smelter", 30: "workshops"}

def layout():
    t = []
    for e in LEVELS:
        t += [(x, y) for x in range(46, 50) for y in range(122, 126)]        # 4x4 landing
        t += [(x, Y) for x in range(36, 46)]                                 # 1-wide hall
        for rx0 in (36, 41):
            for ry0 in (125, 118):
                t += [(x, y) for x in range(rx0, rx0+4) for y in range(ry0, ry0+4)]
                t += [(rx0+1, 124 if ry0 == 125 else 122)]                  # 1-tile opening
    return list(dict.fromkeys(t))

async def main():
    w = WM.WorldModel()
    e0, e1 = 24, 43
    surf = await w.surface_map(e0, e1); hz = await w.hazard_map(e0, e1)
    plan = layout()
    stair = [(50, Y), (51, Y)]
    order = {e: [] for e in LEVELS}
    for (x, y) in plan:
        for e in LEVELS:
            if (x, y) in stair and e != 37:
                continue                       # the shaft column belongs to the stairwell
            rec = hz.get((x, y), {"aquifer": [], "water": {}, "magma": []})
            if any(a >= e for a in rec["aquifer"]) or any(a >= e for a in rec["water"]):
                continue                       # DAMP: never
            top = surf.get((x, y))
            if top is not None and top - e < 2:
                continue                       # known surface cut
            order[e].append((x, y))
    # the farm level needs its own stair head from 36 up to 37
    order[37] = sorted(set(order[37]) | set(stair), key=lambda p: (p[0], p[1]))
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=250) as x:
        total = 0
        for e in sorted(LEVELS, reverse=True):
            tiles = order[e]
            kind = "UP_DOWN_STAIR_DIG"
            body = [[xx, yy, e+128] for xx, yy in tiles]
            # stairs only on the shaft column, plain dig elsewhere
            sent = 0
            stair_tiles = [[xx, yy, e+128] for xx, yy in tiles if (xx, yy) in stair]
            dig_tiles = [[xx, yy, e+128] for xx, yy in tiles if (xx, yy) not in stair]
            if stair_tiles:
                sent += x.post("/send-dig-command", json={"designation": kind, "locations": stair_tiles}).json().get("tiles", 0)
            for i in range(0, len(dig_tiles), 100):
                sent += x.post("/send-dig-command", json={"designation": "DEFAULT_DIG", "locations": dig_tiles[i:i+100]}).json().get("tiles", 0)
            total += sent
            sv = x.get("/survey", params={"elev": e, "x0": 36, "y0": 118, "x1": 51, "y1": 128}).json()
            blocked = {k: v for k, v in sv["counts"].items() if k in ("floor","stairs","ramp","building","water","already_designated")}
            print(f"elev {e} (ui {e-1}) [{LEVELS[e]}]: ordered {sent} tiles | pre-existing: {json.dumps(blocked) if blocked else 'clean'}")
        print("total ordered:", total)
        x.get("/world-cache-reset")
        for e in sorted(LEVELS, reverse=True):
            t = x.get("/world-objects", params={"e0":e,"e1":e,"max_tokens":700}).json()["text"]
            print(f"  elev {e} pending:", sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig ")))
asyncio.run(main())
