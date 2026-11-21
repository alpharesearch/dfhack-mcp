import asyncio, sys, json, httpx
sys.path.insert(0,'.')
import worldmodel as WM

ELEV_GRID = 36
LEVELS = [37, 32, 31, 30]
Y = 123

def old_rooms(bands, rows):
    out = []
    for (x0, x1) in bands:
        for k in rows:
            y0 = 60 + 5*k if len(rows) > 1 else None
    return out

def level_tiles_old():
    t = []
    for e in LEVELS:
        for rx0 in (36, 41):
            for ry0 in (125, 118):
                t += [(e, x, y) for x in range(rx0, rx0+4) for y in range(ry0, ry0+4)]
                t += [(e, rx0+1, 124 if ry0 == 125 else 122)]
    return t

def level_tiles_new():
    # 2-tile walls: hall y123 (1 wide); rooms 4x4 at y126-129 and y117-120;
    # rows 124-125 and 121-122 stay WALL; x gaps of 2 between rooms (pitch 6).
    t = []
    for e in LEVELS:
        for rx0 in (36, 42):
            for ry0 in (126, 117):
                t += [(e, x, y) for x in range(rx0, rx0+4) for y in range(ry0, ry0+4)]
                t += [(e, rx0+1, 125 if ry0 == 126 else 118)]   # 1-tile opening through the wall
                t += [(e, rx0+1, 124 if ry0 == 126 else 119)]   # and the inner wall tile is kept
    return t

async def main():
    w = WM.WorldModel()
    e0, e1 = 24, 43
    surf = await w.surface_map(e0, e1); hz = await w.hazard_map(e0, e1)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=250) as x:
        # 1) clear the thin-walled rooms I ordered
        old = level_tiles_old()
        cleared = 0
        body = [[xx, yy, e+128] for e, xx, yy in old]
        for i in range(0, len(body), 150):
            cleared += x.post("/send-dig-command", json={"designation":"NO_DIG","locations":body[i:i+150]}).json().get("tiles",0)
        print("cleared thin-walled room designations:", cleared, "of", len(old))
        # 2) re-order with 2-tile walls, gates applied
        new = level_tiles_new()
        ordered = 0
        for e, xx, yy in new:
            rec = hz.get((xx, yy), {"aquifer": [], "water": {}, "magma": []})
            if any(a >= e for a in rec["aquifer"]) or any(a >= e for a in rec["water"]):
                continue
            top = surf.get((xx, yy))
            if top is not None and top - e < 2:
                continue
            r = x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":[[xx,yy,e+128]]}).json()
            ordered += r.get("tiles",0)
        print("re-ordered with 2-tile walls:", ordered, "of", len(new))
        # 3) the elev-36 bedroom grid: 1-tile gaps -> drop the odd room rows (they become wall)
        band_tiles = []
        for (x0,x1) in [(35,38),(41,44),(46,49),(51,54),(56,59),(61,64)]:
            for k in range(20):
                if k % 2 == 1:
                    y0 = 60 + 5*k
                    band_tiles += [[xx, yy, ELEV_GRID+128] for xx in range(x0, x1+1) for yy in range(y0, y0+4)]
                    band_tiles += [[39, y0+1, ELEV_GRID+128]]
        c2 = 0
        for i in range(0, len(band_tiles), 150):
            c2 += x.post("/send-dig-command", json={"designation":"NO_DIG","locations":band_tiles[i:i+150]}).json().get("tiles",0)
        print("bedroom grid: removed the 1-tile-gap room rows:", c2, "tiles become solid wall")
        x.get("/world-cache-reset")
        for e in [37,36,32,31,30]:
            t = x.get("/world-objects", params={"e0":e,"e1":e,"max_tokens":700}).json()["text"]
            print(f"  elev {e} (ui {e-1}) pending:", sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig ")))
asyncio.run(main())
