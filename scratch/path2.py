import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM
ROWS = (119,120,121)
async def main():
    w=WM.WorldModel()
    surf=await w.surface_map(24,43); hz=await w.hazard_map(24,43); band,_=await w._stack(24,43)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        # 1) clear my underground plan
        bad = [[xx,yy,e+128] for e,(a,b) in {38:(56,58),37:(59,71)}.items()
               for xx in range(a,b+1) for yy in ROWS]
        cleared=0
        for i in range(0,len(bad),100):
            cleared += x.post("/send-dig-command", json={"designation":"NO_DIG","locations":bad[i:i+100]}).json().get("tiles",0)
        print("cleared the underground corridor/ramp plan:", cleared, "tiles")
        # 2) the road: flat at 39 from the depot to x67, ramp at the x68 step, ramp at the x73 step
        order = {"dig": [], "ramp": []}
        for x in range(50,68):
            for y in ROWS:
                rec = hz.get((x,y),{"aquifer":[],"water":{}})
                if any(a>=39 for a in rec["aquifer"]) or any(a>=39 for a in rec["water"]): continue
                top = surf.get((x,y))
                g = band.get((x,y),{}).get(39)
                if g in (".","=","/"): continue          # already open: nothing to order
                if top is not None and top-39 < 2: continue
                order["dig"].append([x,y,39+128])
        for (xx,e) in [(68,38),(73,37)]:
            for y in ROWS:
                order["ramp"].append([xx,y,e+128])
        # middle-first so access arrives before the flanks
        order["dig"].sort(key=lambda t: abs(t[1]-120))
        r1 = x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":order["dig"]}).json()
        print(f"road at in-game 38 (elev 39), x50-67: accepted {r1.get('tiles')}/{len(order['dig'])}")
        for xx,e in [(68,38),(73,37)]:
            tiles=[[xx,yy,e+128] for yy in ROWS]
            r = x.post("/send-dig-command", json={"designation":"RAMP_DIG","locations":tiles}).json()
            print(f"ramp at x{xx} elev{e} (in-game {e-1}): accepted {r.get('tiles')}/{len(tiles)}")
        x.get("/world-cache-reset")
        for e in (39,38,37):
            t=x.get("/world-objects",params={"e0":e,"e1":e,"max_tokens":900}).json()["text"]
            print(f"  elev {e} (in-game {e-1}):", [l.strip() for l in t.splitlines() if l.strip().startswith("dig")])
        print("paused:", x.get("/get-pause-state").json())
asyncio.run(main())
