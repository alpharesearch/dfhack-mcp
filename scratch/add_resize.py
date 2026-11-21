import pathlib

LUA = '''

async def resize_building(dec, building_id: int, w: int, h: int) -> dict:
    """Resize a building (stockpiles included) through the game's own dfhack.buildings.setSize.

    Measured: constructBuilding with nominal=true makes a 1x1 stockpile even when w/h are
    passed - pile 12 came out x1=x2=44, y1=y2=116 (room 1x1), exactly what the player saw.
    The game's own resize is `dfhack.buildings.setSize(b, w, h)`; extents are read back from
    b.x1..b.x2 / b.y1..b.y2 and the usable tile count from countExtentTiles.
    """
    expr = """
local b = nil
for _,lst in ipairs({df.global.world.buildings.other.STOCKPILE}) do
  for _,sp in ipairs(lst) do if sp.id == __ID__ then b = sp end end
end
if b == nil then print("FAIL building __ID__ not found") return end
local ok, err = pcall(function() dfhack.buildings.setSize(b, __W__, __H__) end)
if not ok then print("FAIL setSize: " .. tostring(err)) return end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(b) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%s", b.id, b.x1, b.x2, b.y1, b.y2, tostring(n)))
""".strip().replace("__ID__", str(int(building_id))).replace("__W__", str(int(w))).replace("__H__", str(int(h)))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line}
'''

p = pathlib.Path("luaops.py")
p.write_text(p.read_text().rstrip() + LUA)

q = pathlib.Path("main.py")
s = q.read_text()
anchor = '@app.post("/configure-stockpile", operation_id="configure_stockpile")'
assert anchor in s
add = '''@app.post("/resize-building", operation_id="resize_building")
async def resize_building(body: dict):
    """Resize a building - stockpiles included - with the game's own setSize. constructBuilding with nominal=true makes a 1x1 stockpile no matter what w/h you pass (measured), so big piles need this call afterwards."""
    try:
        dec = await world._dec(await world._gen())
        return await LO.resize_building(dec, int(body["building_id"]), int(body["w"]), int(body["h"]))
    except LO.LuaOpError as e:
        return {"success": False, "error": str(e)}


''' + anchor
q.write_text(s.replace(anchor, add, 1))
print("resize op + endpoint added")
