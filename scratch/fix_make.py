import pathlib

p = pathlib.Path("luaops.py")
s = p.read_text()
i = s.index("async def create_stockpile(")
j = s.index("async def make_stockpile(", i)

FN = '''async def create_stockpile(dec, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create a MULTI-TILE stockpile with the game's own building calls.

    Measured on 53.17-r1 (all of this is in the crash/diary history):
    - `constructBuilding{..., w, h}` does nothing for size: the library reads
      `width`/`height`, so w/h produced 1x1 piles.
    - `constructAbstract` requires `!bld->isActual()`, and `allocInstance` for a STOCKPILE
      (type 29) yields an actual building - so abstract is not the commit for piles, and
      calling it on the committed instance is the "expected: !bld->isActual()" failure.
    - `setSize` is legal only before the commit and fails on overlap.
    So: allocInstance -> setSize; if the pile is not live afterwards, try constructAbstract
    in a pcall and treat an isActual refusal as "already live"; verify by finding the
    STOCKPILE whose footprint starts at (x, y) at this z, and report id/extents/tile count.
    """
    z = int(elev) + GAME_Z_OFFSET
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local inst = dfhack.buildings.allocInstance(pos, 29, -1, -1)
if inst == nil then print("ERR|allocInstance failed") return end
if not dfhack.buildings.setSize(inst, __W__, __H__, 8) then
  print("ERR|setSize refused (overlap with a building, walls/ramps in the footprint?)") return
end
local ok_abs, err_abs = pcall(function() return dfhack.buildings.constructAbstract(inst) end)
local found = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  if sp.x1 == __X__ and sp.y1 == __Y__ and sp.z == __Z__ then found = sp break end
end
if found == nil then
  print("ERR|pile not live after construction (constructAbstract said " .. tostring(ok_abs) .. ": " .. tostring(err_abs) .. ")")
  return
end
local tiles = 0
pcall(function() tiles = dfhack.buildings.countExtentTiles(found, found.x1, found.z, found.y1) end)
local area = (found.x2-found.x1+1)*(found.y2-found.y1+1)
print(string.format("OK|%d|%d..%d,%d..%d|%d|%d", found.id, found.x1, found.x2, found.y1, found.y2, tiles, area))
"""
    expr = lua.strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z), ("__W__", int(w)), ("__H__", int(h))):
        expr = expr.replace(k, str(v))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    parts = line.split("|")
    if len(parts) < 6:
        raise LuaOpError("unparsable create result: " + line[:120])
    bid, extent, tiles, area = int(parts[1]), parts[2], int(parts[3]), int(parts[4])
    return {"success": True, "building_id": bid, "extent": extent,
            "usable_tiles": tiles, "rect_tiles": area, "holes": area - tiles}


'''
p.write_text(s[:i] + FN + s[j:])

# make_stockpile: structured, no text parsing of a failure line
a = '''    built = await create_stockpile(dec, x, y, elev, w, h)
    bid = int(str(built.get("line", "")).split("id=")[1].split()[0])'''
b = '''    built = await create_stockpile(dec, x, y, elev, w, h)
    bid = int(built["building_id"])'''
s2 = p.read_text()
assert a in s2, "make_stockpile parse anchor"
s2 = s2.replace(a, b, 1)
a3 = '''    return {"success": True, "building_id": bid, "built": built.get("line"),
            "configured": cfg.get("line")}'''
b3 = '''    return {"success": True, "building_id": bid, "extent": built["extent"],
            "usable_tiles": built["usable_tiles"], "holes": built["holes"],
            "configured": cfg.get("line")}'''
assert a3 in s2
s2 = s2.replace(a3, b3, 1)
p.write_text(s2)
print("create_stockpile made robust; make_stockpile returns structured data")
