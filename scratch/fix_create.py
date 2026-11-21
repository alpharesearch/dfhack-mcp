import pathlib

p = pathlib.Path("luaops.py")
s = p.read_text()
i = s.index("async def create_stockpile(")
# the function runs to the end of the file (it was appended last)
s = s[:i]

FN = '''async def create_stockpile(dec, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create a MULTI-TILE stockpile exactly the way hack/lua/dfhack/buildings.lua does:
    allocInstance -> setSize (legal only on the uncommitted instance) -> constructAbstract.

    Measured on 53.17-r1: constructBuilding{..., w, h, nominal=true} yields a 1x1 stockpile,
    and setSize on a committed building answers "expected: bld->id == -1". The library's own
    order (buildings.lua:506-545) is allocInstance(pos, type, subtype, custom),
    setSize(instance, width, height, direction), then constructAbstract/WithItems/WithFilters.
    """
    z = int(elev) + GAME_Z_OFFSET
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local inst = dfhack.buildings.allocInstance(pos, 21, 0, 0)
if inst == nil then print("FAIL allocInstance") return end
local ok, w2, h2, area, r_area = dfhack.buildings.setSize(inst, __W__, __H__, 8)
if not ok then print("FAIL setSize: cannot place at this position") return end
local oka = dfhack.buildings.constructAbstract(inst)
if not oka then print("FAIL constructAbstract") return end
local at = nil
pcall(function() at = dfhack.buildings.findAtTile(__X__, __Y__, __Z__) end)
if at == nil then print("FAIL built but not found at tile") return end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(at) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2, n))
"""
    expr = lua.strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z), ("__W__", int(w)), ("__H__", int(h))):
        expr = expr.replace(k, str(v))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line}
'''
p.write_text(s + FN)
print("create_stockpile rewritten cleanly")
