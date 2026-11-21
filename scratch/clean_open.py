import asyncio, sys, json, httpx
sys.path.insert(0,'.')
import worldmodel as WM
ELEV=36
async def main():
    w=WM.WorldModel()
    band,_=await w._stack(24,43)
    open_tiles=[(x,y) for y in range(60,160) for x in range(34,67) if band.get((x,y),{}).get(ELEV)==" "]
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=250) as x:
        body=[[xx,yy,ELEV+128] for xx,yy in open_tiles]
        cleared=0
        for i in range(0,len(body),200):
            cleared += x.post("/send-dig-command", json={"designation":"NO_DIG","locations":body[i:i+200]}).json().get("tiles",0)
        print("open-air tiles cleared of designation:", cleared, "of", len(open_tiles))
        x.get("/world-cache-reset")
        tot=0
        for y0,y1 in [(60,95),(96,130),(131,159)]:
            s=x.get("/survey",params={"elev":ELEV,"x0":34,"y0":y0,"x1":67,"y1":y1}).json()
            tot += s["counts"].get("already_designated",0)
            print(f"  y{y0}-{y1}: {json.dumps(s['counts'])}" if False else f"  y{y0}-{y1}: designated={s['counts'].get('already_designated',0)} open={s['counts'].get('open',0)} rock={s['counts'].get('rock',0)} stuff={s['counts'].get('floor',0)+s['counts'].get('stairs',0)}")
        print("total designated at elev 36:", tot)
        print("paused:", x.get("/get-pause-state").json())
asyncio.run(main())
