import asyncio, sys
sys.path.insert(0,'.')
import worldmodel as WM

WAGON = (".", "g", "s", "S", "/")      # floors, grass, smooth, constructed, RAMPS - no stairs '='

async def main():
    w = WM.WorldModel()
    band, _ = await w._stack(30, 43)
    start = (46, 120, 39)                       # depot tiles
    seen = {start}; stack = [start]; path = {start: None}
    edges = []
    while stack:
        x, y, z = stack.pop()
        if x in (0, 191) or y in (0, 191):
            edges.append((x, y, z))
        g = band.get((x, y), {}).get(z, " ")
        if g not in WAGON:
            continue
        nbs = [(x+dx, y+dy, z) for dx, dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))]
        nbs += [(x, y, z+1), (x, y, z-1)]       # ramps link levels; stairs deliberately excluded
        for n in nbs:
            ng = band.get((n[0], n[1]), {}).get(n[2], " ")
            if ng in WAGON and n not in seen:
                seen.add(n); path[n] = (x, y, z); stack.append(n)
    print(f"wagon-reachable tiles from the depot (no stairs): {len(seen)}")
    print("reached map-edge tiles:", edges[:8] if edges else "NONE")
    if start not in WAGON:
        print("(note: depot tile class:", band.get(start[:2],{}).get(start[2]), ")")
    # reconstruct one edge path
    if edges:
        n = edges[0]; steps = []
        while n is not None:
            steps.append(n); n = path.get(n)
        print(f"example route ({len(steps)} hops): depot -> ... -> edge, elevation profile:",
              [s[2] for s in steps[::max(1,len(steps)//12)]])
    # 3-wide check on the key rows at the ramp columns
    for cx, e in [(67,39),(68,38),(72,37),(73,37)]:
        row = [band.get((cx, y), {}).get(e, "?") for y in (119,120,121)]
        print(f"  3-wide at x{cx},elev{e} (in-game {e-1}): {row}")
asyncio.run(main())
