import asyncio, sys, json, httpx
sys.path.insert(0,'.')
import worldmodel as WM

# Depot frontage (building id 5) at elev 39 = in-game 38, x45-49, y118-122.
# Wagons need a 3-wide path with NO STAIRS: corridors are flat, level changes are RAMPS.
DEPOT_Y0, DEPOT_Y1 = 119, 121                    # the 3 rows we route through
E0 = 39                                          # depot level
PLAN = [
    ("corridor", 39, 50, 55),                    # flat run east from the depot
    ("ramp",     38, 56, 58),                    # one level down
    ("ramp",     37, 59, 61),                    # one level down
    ("corridor", 37, 62, 78),                    # onto the open plain
]

async def main():
    w = WM.WorldModel()
    surf = await w.surface_map(24, 43); hz = await w.hazard_map(24, 43)
    band, _ = await w._stack(24, 43)
    order = []
    notes = []
    for kind, e, xa, xb in PLAN:
        for x in range(xa, xb+1):
            for y in range(DEPOT_Y0, DEPOT_Y1+1):
                rec = hz.get((x, y), {"aquifer": [], "water": {}})
                if any(a >= e for a in rec["aquifer"]) or any(a >= e for a in rec["water"]):
                    notes.append(f"skip DAMP x{x},y{y},elev{e}"); continue
                top = surf.get((x, y))
                g = band.get((x, y), {}).get(e)
                if g in (".", "=", "/"):
                    notes.append(f"skip stuff x{x},y{y},elev{e} ({g})"); continue
                if top is not None and e - top >= 0 and top - e < 2:
                    notes.append(f"note shallow x{x},y{y},elev{e} depth={top-e}")
                order.append((kind, e, x, y))
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=300) as x:
        # order middle-first per row band so access arrives before the flanks
        for kind in ("corridor", "ramp"):
            for e in sorted({o[1] for o in order if o[0] == kind}, reverse=True):
                tiles = [[o[2], o[3], e+128] for o in order if o[0] == kind and o[1] == e]
                tiles.sort(key=lambda t: abs(t[1] - 120))
                if not tiles: continue
                des = "RAMP_DIG" if kind == "ramp" else "DEFAULT_DIG"
                r = x.post("/send-dig-command", json={"designation": des, "locations": tiles}).json()
                print(f"{kind} elev{e} (ui {e-1}) [{des}]: accepted {r.get('tiles')}/{len(tiles)}")
        x.get("/world-cache-reset")
        for e in (39, 38, 37):
            t = x.get("/world-objects", params={"e0":e,"e1":e,"max_tokens":900}).json()["text"]
            print(f"  elev {e} (ui {e-1}) clusters:", [l.strip() for l in t.splitlines() if l.strip().startswith("dig")])
        print("notes:", *notes[:8], sep="\n  ")
        print("paused:", x.get("/get-pause-state").json())
asyncio.run(main())
