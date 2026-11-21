local pos = xyz2pos(36, 116, 167)
local inst = dfhack.buildings.allocInstance(pos, 29, -1, -1)
if inst == nil then print("FAIL allocInstance") return end
local oka = dfhack.buildings.constructAbstract(inst)
print("constructAbstract=" .. tostring(oka))
local ok = dfhack.buildings.setSize(inst, 6, 5, 8)
print("setSize=" .. tostring(ok))
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(inst) end)
print("tiles=" .. tostring(n) .. " extent x" .. inst.x1 .. ".." .. inst.x2 .. " y" .. inst.y1 .. ".." .. inst.y2 .. " id=" .. tostring(inst.id))
