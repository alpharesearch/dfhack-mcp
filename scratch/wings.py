import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM

def rooms(E):
    """Hall extensions + rooms with 2-tile walls, 1-wide openings through the whole thickness."""
    t = []
    if E == 36:
        t += [(x, 122) for x in range(57, 67)]                              # E hall extension
        for x0 in (57, 62):
            t += [(x, y) for x in range(x0, x0+4) for y in range(125, 129)]   # 4x4 north
            t += [(x0+1, 124), (x0+1, 123)]                                   # opening through both rows
        for x0 in (57, 62):
            t += [(x, y) for x in range(x0, x0+2) for y in range(118, 120)]   # 2x2 south
            t += [(x0, 121), (x0, 120)]                                       # opening through both rows
    if E == 37:
        t += [(x, y) for x in range(41, 46) for y in range(119, 124)]        # landing 5x5 on the stairs
        t += [(x, 122) for x in range(31, 41)] + [(x, 122) for x in range(46, 56)]  # halls W and E
        for x0 in (33, 48):
            t += [(x, y) for x in range(x0, x0+4) for y in range(125, 129)]
            t += [(x0+1, 124), (x0+1, 123)]
        for x0 in (33, 48):
            t += [(x, y) for x in range(x0, x0+2) for y in range(118, 120)]
            t += [(x0, 121), (x0, 120)]
    return list(dict.fromkeys(t))

async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(28, 43); hz = await w.hazard_map(28, 43)
    band, _ = await w._stack(28, 43)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        grand = 0
        for E in (36, 37):
            plan = rooms(E)
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
            good.sort(key=lambda p: abs(p[0]-43.5) + abs(p[1]-121))     # outward from the stair hub
            body = [[cx, cy, E+129] for cx, cy in good]
            sent = 0
            for i in range(0, len(body), 100):
                sent += x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":body[i:i+100]}).json().get("tiles",0)
            grand += sent
            print(f"in-game {E}: ordered {sent}/{len(good)} of {len(plan)} (damp {damp}, shallow {shallow}, over-stuff {stuff})")
        x.get("/world-cache-reset")
        for E in (37, 36):
            s = x.get("/survey", params={"elev":E,"x0":28,"y0":112,"x1":68,"y1":132}).json()["counts"]
            print(f"  in-game {E}: designated={s.get('already_designated',0)} dug={s.get('floor',0)+s.get('stairs',0)+s.get('ramp',0)} hidden={s.get('hidden',0)} rock={s.get('rock',0)}")
        print("total ordered:", grand)
asyncio.run(main())
