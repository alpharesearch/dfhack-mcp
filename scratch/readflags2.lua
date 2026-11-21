for _,id in ipairs({12,13}) do
  local b = nil
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == id then b = sp end end
  if b == nil then print(id .. ": missing") goto continue end
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
    if b.settings.flags[c] == true then on[#on+1] = c end
  end
  print("pile " .. id .. " allowed: [" .. table.concat(on, ",") .. "]")
  ::continue::
end
