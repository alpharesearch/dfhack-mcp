import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM
LEVELS=[37,32,31,30]
async def main():
    w=WM.WorldModel()
    surf=await w.surface_map(24,43); hz=await w.hazard_map(24,43)
    tiles=[]
    for e in LEVELS:
        for rx0 in (36,42):
            # north rooms (y126-129): passage through wall rows 125 and 124
            tiles += [(e, rx0+1, 125), (e, rx0+1, 124)]
            # south rooms (y117-120): passage through wall rows 118 and 119
            tiles += [(e, rx0+1, 118), (e, rx0+1, 119)]
    good=[]
    for e,x,y in tiles:
        rec=hz.get((x,y),{"aquifer":[],"water":{}})
        if any(a>=e for a in rec["aquifer"]) or any(a>=e for a in rec["water"]): continue
        top=surf.get((x,y))
        if top is not None and top-e<2: continue
        good.append((e,x,y))
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=250) as x:
        sent=0
        for e,xx,yy in good:
            sent += x.post("/send-dig-command", json={"designation":"DEFAULT_DIG","locations":[[xx,yy,e+128]]}).json().get("tiles",0)
        print("openings extended through both wall rows:", sent, "of", len(tiles))
        x.get("/world-cache-reset")
        for e in LEVELS:
            t=x.get("/world-objects",params={"e0":e,"e1":e,"max_tokens":700}).json()["text"]
            print(f"  elev {e} (ui {e-1}) pending:", sum(int(l.split()[1]) for l in t.splitlines() if l.strip().startswith("dig ")))
asyncio.run(main())
