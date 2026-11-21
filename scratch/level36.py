import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM

SHAFT = (43, 44); Y = 121
E = 36
LAND = [(x, y) for x in range(39, 43) for y in range(120, 124)]           # 4x4 landing on the shaft
HALL = [(x, 122) for x in range(28, 39)]                                  # 1-wide hallway west
R4   = [(x, y) for (x0, y0) in [(34, 125), (28, 125)] for x in range(x0, x0+4) for y in range(y0, y0+4)]
R2   = [(x, y) for (x0, y0) in [(36, 118), (30, 118)] for x in range(x0, x0+2) for y in range(y0, y0+2)]
OPEN = [(35, 124), (35, 123), (29, 124), (29, 123),                       # 1-wide through both wall rows
        (36, 121), (36, 120), (30, 121), (30, 120)]

async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(28, 43); hz = await w.hazard_map(28, 43)
    band, _ = await w._stack(28, 43)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        # 1) stairwell down 35 -> 30 (UP_DOWN on the shaft column)
        sent = 0
        for e in (35, 34, 33, 32, 31):
            tiles = [[sx, Y, e+129] for sx in SHAFT]
            r = x.post("/send-dig-command", json={"designation":"UP_DOWN_STAIR_DIG","locations":tiles}).json()
            sent += r.get("tiles", 0)
            print(f"  stairs in-game {e}: {r.get('tiles')}/{len(tiles)}")
        tiles = [[sx, Y, 30+129] for sx in SHAFT]
        r = x.post("/send-dig-command", json={"designation":"UP_STAIR_DIG","locations":tiles}).json()
        print(f"  stairs in-game 30 (bottom, up-stairs): {r.get('tiles')}/{len(tiles)}")
        # 2) level 36 floor: landing, hall, 4x4 rooms, 2x2 rooms, openings
        plan = list(dict.fromkeys(LAND + HALL + R4 + R2 + OPEN))
        good = []
        damp = shallow = stuff = 0
        for (cx, cy) in plan:
            rec = hz.get((cx, cy), {"aquifer": [], "water": {}})
            if any(a >= E for a in rec["aquifer"]) or any(a >= E for a in rec["water"]):
                damp += 1; continue
            top = surf.get((cx, cy))
            if top is not None and top - E < 2:
                shallow += 1; continue
            g = band.get((cx, cy), {}).get(E)
            if g in (".", "=", "/"):
                stuff += 1; continue
            good.append([cx, cy, E+129])
        mid = (118 + 128) // 2
        good.sort(key=lambda t: (abs(t[1]-Y), abs(t[1]-122)))
        r = x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":good}).json()
        print(f"\nlevel 36 floor: ordered {r.get('tiles')}/{len(good)} of {len(plan)} plan tiles "
              f"(damp {damp}, shallow {shallow}, over-stuff {stuff})")
        print(f"  landing 16, hall {len(HALL)}, 4x4 rooms 32, 2x2 rooms 8, openings 8")
        x.get("/world-cache-reset")
        for e in (36, 35, 34, 33, 32, 31, 30):
            s = x.get("/survey", params={"elev":e,"x0":28,"y0":116,"x1":44,"y1":130}).json()["counts"]
            print(f"  in-game {e}: designated={s.get('already_designated',0)} rock={s.get('rock',0)} hidden={s.get('hidden',0)} floor={s.get('floor',0)} stairs={s.get('stairs',0)}")
asyncio.run(main())
