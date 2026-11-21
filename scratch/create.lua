local pos = xyz2pos(36, 116, 167)
local inst = dfhack.buildings.allocInstance(pos, 21, 0, 0)
if inst == nil then print("FAIL allocInstance") return end
local ok, w2, h2, area, r_area = dfhack.buildings.setSize(inst, 6, 5, 8)
print("setSize ok=" .. tostring(ok) .. " w=" .. tostring(w2) .. " h=" .. tostring(h2) .. " area=" .. tostring(area) .. " r_area=" .. tostring(r_area))
if not ok then print("FAIL setSize") return end
local oka = dfhack.buildings.constructAbstract(inst)
print("constructAbstract=" .. tostring(oka))
if not oka then print("FAIL constructAbstract") return end
local at = dfhack.buildings.findAtTile(36, 116, 167)
print("at=" .. tostring(at))
if at ~= nil then
  print(string.format("id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2, dfhack.buildings.countExtentTiles(at)))
end
