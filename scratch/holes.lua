for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  local area = (sp.x2-sp.x1+1)*(sp.y2-sp.y1+1)
  local n = 0
  pcall(function() n = dfhack.buildings.countExtentTiles(sp, sp.x1, sp.z, sp.y1) end)
  print(string.format("pile %d rect=%dx%d area=%d usable=%d holes=%d active=%s",
    sp.id, sp.x2-sp.x1+1, sp.y2-sp.y1+1, area, n, area-n, tostring(dfhack.buildings.isActive(sp))))
end
