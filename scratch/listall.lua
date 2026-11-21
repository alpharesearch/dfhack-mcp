for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
    if sp.settings.flags[c] == true then on[#on+1] = c end
  end
  print(string.format("pile id=%d num=%s rect=%dx%d in-game %d tiles=%d allowed=[%s]", sp.id, tostring(sp.stockpile_number),
    sp.x2-sp.x1+1, sp.y2-sp.y1+1, sp.z-129, (sp.x2-sp.x1+1)*(sp.y2-sp.y1+1), table.concat(on, ",")))
end
