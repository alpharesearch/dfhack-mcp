for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  local area = (sp.x2-sp.x1+1)*(sp.y2-sp.y1+1)
  local n = 0
  pcall(function() n = dfhack.buildings.countExtentTiles(sp, sp.x1, sp.z, sp.y1) end)
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
    if sp.settings.flags[c] == true then on[#on+1] = c end
  end
  print(string.format("pile %d rect=%dx%d z=%d usable=%d holes=%d wb=%s active=%s allowed=[%s]",
    sp.id, sp.x2-sp.x1+1, sp.y2-sp.y1+1, sp.z-129, n, area-n, tostring(sp.storage.max_wheelbarrows),
    tostring(dfhack.buildings.isActive(sp)), table.concat(on, ",")))
end
