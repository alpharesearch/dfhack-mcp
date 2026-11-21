import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM
E = 36; SX, SY = (43, 44), 121

LAND = [(x, y) for x in range(41, 46) for y in range(119, 124)]                     # 5x5 hub on the stairs
HALL_W = [(x, 122) for x in range(30, 41)]
HALL_E = [(x, 122) for x in range(46, 57)]
HALL_N = [(43, y) for y in range(124, 140)]
HALL_S = [(44, y) for y in range(106, 119)]
R4 = ([(x, y) for (x0, y0) in [(30,125),(36,125),(47,125),(53,125),(37,131),(37,136),(38,111),(38,106)]
      for x in range(x0, x0+4) for y in range(y0, y0+4)])
R2 = ([(x, y) for (x0, y0) in [(30,118),(36,118),(47,118),(53,118)]
      for x in range(x0, x0+2) for y in range(y0, y0+2)])
# 1-wide openings THROUGH the full 2-tile wall thickness
OP = ([(31,124),(31,123),(37,124),(37,123),(48,124),(48,123),(54,124),(54,123),      # north rooms off W/E halls
       (30,121),(30,120),(36,121),(36,120),(47,121),(47,120),(53,121),(53,120),      # south 2x2 off W/E halls
       (41,132),(42,132),(41,137),(42,137),                                          # N hall, rooms west
       (42,112),(43,112),(42,107),(43,107)])                                         # S hall, rooms west

async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(28, 43); hz = await w.hazard_map(28, 43)
    band, _ = await w._stack(28, 43)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        old = [[cx, cy, E+129] for cy in range(116, 131) for cx in range(28, 45)]
        cleared = 0
        for i in range(0, len(old), 200):
            cleared += x.post("/send-dig-command", json={"designation":"NO_DIG","locations":old[i:i+200]}).json().get("tiles",0)
        print("cleared the old side-hanging layout:", cleared)
        plan = list(dict.fromkeys(LAND + HALL_W + HALL_E + HALL_N + HALL_S + R4 + R2 + OP))
        good = []; damp = shallow = stuff = 0
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
            good.append((cx, cy))
        good.sort(key=lambda t: abs(t[0]-43.5) + abs(t[1]-121))          # hub outward
        sent = 0
        for i in range(0, len(good), 100):
            sent += x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":[[cx,cy,E+129] for cx,cy in good[i:i+100]]}).json().get("tiles",0)
        print(f"hub layout ordered: {sent}/{len(good)} of {len(plan)} plan tiles (damp {damp}, shallow {shallow}, over-stuff {stuff})")
        print(f"  hub 25, halls {len(HALL_W)+len(HALL_E)+len(HALL_N)+len(HALL_S)}, 4x4 rooms {len(R4)}, 2x2 rooms {len(R2)}, openings {len(OP)}")
        x.get("/world-cache-reset")
        s = x.get("/survey", params={"elev":E,"x0":28,"y0":104,"x1":58,"y1":141}).json()["counts"]
        print("  survey on the new box:", s)
asyncio.run(main())
