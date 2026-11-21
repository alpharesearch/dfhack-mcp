import pathlib

# 1) fix the extras syntax in configure_stockpile (each write is one pcall line, no do/end)
p = pathlib.Path("luaops.py")
s = p.read_text()
a = '''    for field, val in (("max_wheelbarrows", max_wheelbarrows), ("max_barrels", max_barrels),
                       ("max_bins", max_bins)):
        if val is not None:
            extras += ("  do local ok,e = pcall(function() b.storage.%s = %d end)\\n"
                       % (field, int(val)))
            extras += '  if not ok then print("WARN storage.%s: "..tostring(e)) end\\n' % field'''
b = '''    for field, val in (("max_wheelbarrows", max_wheelbarrows), ("max_barrels", max_barrels),
                       ("max_bins", max_bins)):
        if val is not None:
            extras += ("  local ok_%s, err_%s = pcall(function() b.storage.%s = %d end)\\n"
                       % (field, field, field, int(val)))
            extras += ('  if not ok_%s then print("WARN storage.%s: "..tostring(err_%s)) end\\n'
                       % (field, field, field))'''
assert a in s, "extras anchor"
s = s.replace(a, b, 1)

LUA = '''

async def create_stockpile(dec, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create a MULTI-TILE stockpile the way the game builds it: abstract instance ->
    setSize (legal only while id == -1) -> completeBuild.

    Measured on 53.17-r1: constructBuilding{..., w, h, nominal=true} yields a 1x1 stockpile
    (pile 12 came out x1=x2=44, y1=y2=116), and setSize on a committed building answers
    "Invalid argument; expected: bld->id == -1". So size must be set before the commit.
    """
    z = int(elev) + GAME_Z_OFFSET
    expr = """
local bt = 21  -- STOCKPILE building_type
local b = nil
local ok, res = pcall(function()
  return dfhack.buildings.constructAbstract({type=bt, subtype=0, custom=0,
    x=__X__, y=__Y__, z=__Z__})
end)
if not (ok and res) then print("FAIL constructAbstract: "..tostring(res)) return end
b = res
local oks, errs = pcall(function() dfhack.buildings.setSize(b, __W__, __H__) end)
if not oks then print("FAIL setSize: "..tostring(errs)) return end
local okc, errc = pcall(function() dfhack.buildings.completeBuild(b) end)
if not okc then print("FAIL completeBuild: "..tostring(errc)) return end
local at = nil
pcall(function() at = dfhack.buildings.findAtTile(__X__, __Y__, __Z__) end)
if at == nil then print("FAIL built but not found at tile") return end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(at) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2, n))
""".strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z), ("__W__", int(w)), ("__H__", int(h))):
        expr = expr.replace(k, str(v))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line}
'''
p.write_text(s.rstrip() + LUA)

q = pathlib.Path("main.py")
t = q.read_text()
anchor = '@app.post("/resize-building", operation_id="resize_building")'
assert anchor in t
add = '''@app.post("/create-stockpile", operation_id="create_stockpile")
async def create_stockpile(body: dict):
    """Create a multi-tile stockpile (abstract -> setSize -> completeBuild). Use this, not place_building, for any pile bigger than 1 tile; then set its categories with /configure-stockpile."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.create_stockpile(dec, int(body["x"]), int(body["y"]), int(body["elev"]),
                                         int(body["w"]), int(body["h"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


''' + anchor
q.write_text(t.replace(anchor, add, 1))
print("create_stockpile op + endpoint wired; extras syntax fixed")
