for _,id in ipairs({15,16,17}) do
  local b = nil
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == id then b = sp end end
  if b == nil then print(id .. ": missing") goto continue end
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
    if b.settings.flags[c] == true then on[#on+1] = c end
  end
  print(string.format("pile %d extent=%d..%d,%d..%d z=%d allowed=[%s]", id, b.x1, b.x2, b.y1, b.y2, b.z, table.concat(on, ",")))
  ::continue::
end
