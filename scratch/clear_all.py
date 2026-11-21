import asyncio, sys, httpx
sys.path.insert(0,'.')
import worldmodel as WM

async def main():
    w = WM.WorldModel()
    gen = await w._gen()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        x.get("/world-cache-reset")
        found = []
        for lo in range(0, 192, 12):
            hi = min(lo + 12, 192)
            try:
                blocks = await w.cache.blocks(lo, hi, gen)
            except Exception as e:
                print(f"  window z{lo}-{hi} failed: {e}"); continue
            for b in blocks:
                des = list(b.tile_dig_designation)
                for i, d in enumerate(des):
                    if int(d):
                        found.append((b.map_x + (i % 16), b.map_y + (i // 16), b.map_z))
        print("designated tiles found (full map):", len(found))
        sent = 0
        for i in range(0, len(found), 200):
            r = x.post("/send-dig-command", json={"designation":"NO_DIG","locations":[list(t) for t in found[i:i+200]]}).json()
            sent += r.get("tiles", 0)
        print("NO_DIG sent:", sent)
        # verify from a clean cache
        x.get("/world-cache-reset")
        gen = await w._gen()
        left = 0
        for lo in range(0, 192, 12):
            hi = min(lo + 12, 192)
            try:
                blocks = await w.cache.blocks(lo, hi, gen)
            except Exception:
                continue
            for b in blocks:
                left += sum(1 for d in b.tile_dig_designation if int(d))
        print("designated tiles remaining:", left)
        print("paused:", x.get("/get-pause-state").json())
asyncio.run(main())
