local inst = dfhack.buildings.allocInstance(xyz2pos(30, 126, 165), 29, -1, -1)
local ok = dfhack.buildings.setSize(inst, 4, 3, 8)
print("setSize=" .. tostring(ok))
local oka = dfhack.buildings.constructAbstract(inst)
print("constructAbstract=" .. tostring(oka))
local at = dfhack.buildings.findAtTile(30, 126, 165)
if at == nil then print("FAIL findAtTile") return end
print(string.format("OK stone pile id=%d extent=%d..%d,%d..%d z=%d", at.id, at.x1, at.x2, at.y1, at.y2, at.z-129))
