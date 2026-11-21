local pos = xyz2pos(36, 116, 167)
local inst = dfhack.buildings.allocInstance(pos, 21, 0, 0)
local ok = dfhack.buildings.setSize(inst, 6, 5, 8)
print("setSize=" .. tostring(ok) .. " isActual=" .. tostring(inst.flags:is_instance() and "?" or "?"))
local at = dfhack.buildings.findAtTile(36, 116, 167)
print("at=" .. tostring(at))
if at ~= nil then
  local n = dfhack.buildings.countExtentTiles(at)
  print(string.format("id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2, n))
  local okc = pcall(function() dfhack.buildings.completeBuild(at) end)
  print("completeBuild pcall=" .. tostring(okc))
end
