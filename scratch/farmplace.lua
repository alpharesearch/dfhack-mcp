local placed = nil
for _,o in ipairs{{66,112},{68,112},{70,112},{66,114},{68,114},{70,114},{72,112},{74,112},{66,116},{68,116}} do
  local x, y = o[1], o[2]
  local ok, res = pcall(function()
    return dfhack.buildings.constructBuilding{
      building_type = 4, x = x, y = y, z = 167, width = 3, height = 3, items = {}, filters = nil
    }
  end)
  local b = nil
  pcall(function() b = dfhack.buildings.findAtTile(x, y, 167) end)
  if ok and res == true and b ~= nil then
    placed = {x, y, b.id}
    print(string.format("FARM PLOT placed at x%d..%d,y%d..%d in-game 38 id=%s", x, x+2, y, y+2, tostring(b.id)))
    break
  end
  print(string.format("  try x%d,y%d: ok=%s result=%s found=%s", x, y, tostring(ok), tostring(res), tostring(b ~= nil)))
end
if placed == nil then print("no farm plot site accepted in the sweep") end
