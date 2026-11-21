for _,o in ipairs{{66,112},{68,112},{70,112},{72,112},{66,114},{68,114},{70,114},{72,114},{66,116},{68,116},{74,112},{76,112}} do
  local x, y = o[1], o[2]
  local res, msg = nil, nil
  local ok = pcall(function()
    res, msg = dfhack.buildings.constructBuilding{
      type = 4, x = x, y = y, z = 167, width = 3, height = 3, abstract = true, full_rectangle = true
    }
  end)
  if ok and res ~= nil then
    local b = dfhack.buildings.findAtTile(x, y, 167)
    print(string.format("FARM PLOT placed at x%d..%d,y%d..%d in-game 38, id=%s", x, x+2, y, y+2, tostring(b and b.id)))
    return
  end
  print(string.format("  try x%d,y%d: ok=%s res=%s msg=%s", x, y, tostring(ok), tostring(res), tostring(msg)))
end
print("no farm plot site accepted")
