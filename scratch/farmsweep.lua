local tried = 0
for x = 45, 90, 2 do
  for y = 104, 136, 2 do
    local res, msg = nil, nil
    local ok = pcall(function()
      res, msg = dfhack.buildings.constructBuilding{
        type = 4, x = x, y = y, z = 167, width = 3, height = 3, abstract = true, full_rectangle = true
      }
    end)
    tried = tried + 1
    if ok and res ~= nil then
      local b = dfhack.buildings.findAtTile(x, y, 167)
      print(string.format("FARM PLOT placed x%d..%d,y%d..%d in-game 38 id=%s after %d tries", x, x+2, y, y+2, tostring(b and b.id), tried))
      return
    end
  end
end
print("no farm plot site accepted in x45-90,y104-136 (" .. tried .. " attempts)")
