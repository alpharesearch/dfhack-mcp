local inst = dfhack.buildings.allocInstance(xyz2pos(59, 116, 167), 29, -1, -1)
local ok = dfhack.buildings.setSize(inst, 6, 4, 8)
print("setSize=" .. tostring(ok))
local oka = dfhack.buildings.constructAbstract(inst)
print("constructAbstract=" .. tostring(oka))
local at = dfhack.buildings.findAtTile(59, 116, 167)
if at ~= nil then
  print(string.format("OK wood pile id=%d extent=%d..%d,%d..%d", at.id, at.x1, at.x2, at.y1, at.y2))
end
