local pos = xyz2pos(36, 116, 167)
local inst = dfhack.buildings.allocInstance(pos, 29, -1, -1)
if inst == nil then print("FAIL allocInstance") return end
local ok = dfhack.buildings.setSize(inst, 6, 5, 8)
print("setSize=" .. tostring(ok))
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(inst) end)
print("extent tiles=" .. tostring(n) .. " x" .. inst.x1 .. ".." .. inst.x2 .. " y" .. inst.y1 .. ".." .. inst.y2)
dfhack.buildings.setOwner(inst, true, false)
print("done")
