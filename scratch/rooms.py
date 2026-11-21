import asyncio, sys, json, httpx
sys.path.insert(0,'.')
import worldmodel as WM

ELEV = 36
HALLS = [40, 60]                                     # 1-wide corridors
BANDS = {"A": (34,37), "B": (43,46), "D": (54,57), "E": (63,66)}   # 4-wide, 2-tile walls
ROWS = [60 + 6*k for k in range(16)]                 # 16 rows, 2 wall rows between room rows
WATER_EDGE = 68                                      # measured shore at this elevation

def design():
    t = []
    for hx in HALLS:
        t += [(hx, y) for y in range(60, 160)]
    for (x0, x1) in BANDS.values():
        for y0 in ROWS:
            for xx in range(x0, x1+1):
                for yy in range(y0, y0+4):
                    t.append((xx, yy))
            # 1-wide passage through BOTH wall rows, into the hall
            hall = 40 if x0 < 40 else 60
            gap = sorted(range(min(x0, hall), max(x0, hall)+1))[1:3] if x0 < hall else sorted(range(min(x0, hall), max(x0, hall)+1))[-3:-1]
            for gx in gap:
                t.append((gx, y0+1))
    return list(dict.fromkeys(t))

async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(24, 43); hz = await w.hazard_map(24, 43)
    band, _ = await w._stack(24, 43)
    plan = design()
    good = []; damp = shallow = stuff = shore = 0
    for (x, y) in plan:
        if x >= WATER_EDGE - 1:
            shore += 1; continue
        rec = hz.get((x, y), {"aquifer": [], "water": {}})
        if any(a >= ELEV for a in rec["aquifer"]) or any(a >= ELEV for a in rec["water"]):
            damp += 1; continue
        top = surf.get((x, y))
        if top is not None and top - ELEV < 2:
            shallow += 1; continue
        g = band.get((x, y), {}).get(ELEV)
        if g in (".", "=", "/"):
            stuff += 1; continue                      # never designate over stuff
        good.append((x, y))
    print(f"plan {len(plan)} -> order {len(good)} | skipped: damp={damp} shallow={shallow} over-stuff={stuff} shore={shore}")
    print(f"rooms: {len(BANDS)} bands x {len(ROWS)} rows = {len(BANDS)*len(ROWS)} rooms of 4x4")
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=250) as x:
        sent = 0
        body = [[xx, yy, ELEV+128] for xx, yy in good]
        for i in range(0, len(body), 100):
            sent += x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":body[i:i+100]}).json().get("tiles",0)
        x.get("/world-cache-reset")
        t = x.get("/world-objects", params={"e0":ELEV,"e1":ELEV,"max_tokens":900}).json()["text"]
        print("designated:", sent, "| pending at elev 36 (in-game 35):",
              sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig ")))
        print("game paused:", x.get("/get-pause-state").json())
asyncio.run(main())
