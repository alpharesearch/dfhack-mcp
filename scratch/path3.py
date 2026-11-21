import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM
ROWS = (119,120,121)
async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(24,43); hz = await w.hazard_map(24,43); band,_ = await w._stack(24,43)
    dig = []
    for cx in range(50, 68):
        for cy in ROWS:
            rec = hz.get((cx, cy), {"aquifer": [], "water": {}})
            if any(a >= 39 for a in rec["aquifer"]) or any(a >= 39 for a in rec["water"]):
                continue
            g = band.get((cx, cy), {}).get(39)
            if g in (".", "=", "/"):
                continue
            top = surf.get((cx, cy))
            if top is not None and top - 39 < 2:
                continue
            dig.append([cx, cy, 39+128])
    dig.sort(key=lambda t: abs(t[1]-120))
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as cli:
        r1 = cli.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":dig}).json()
        print(f"road at in-game 38 (elev 39), x50-67: accepted {r1.get('tiles')}/{len(dig)}")
        for cx, e in [(68,38),(73,37)]:
            tiles = [[cx, cy, e+128] for cy in ROWS]
            r = cli.post("/send-dig-command", json={"designation":"RAMP_DIG","locations":tiles}).json()
            print(f"ramp at x{cx} elev {e} (in-game {e-1}): accepted {r.get('tiles')}/{len(tiles)}")
        cli.get("/world-cache-reset")
        for e in (39,38,37):
            t = cli.get("/world-objects", params={"e0":e,"e1":e,"max_tokens":900}).json()["text"]
            print(f"  elev {e} (in-game {e-1}):", [l.strip() for l in t.splitlines() if l.strip().startswith("dig")])
        print("paused:", cli.get("/get-pause-state").json())
asyncio.run(main())
