local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"}
local function get_by_number(num)
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if s.stockpile_number == num then return s end
  end
  return nil
end
local function get_by_id(id)
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if s.id == id then return s end
  end
  return nil
end
local function dump(sp, label)
  if sp == nil then print(label .. ": missing") return end
  local flags_on = {}
  for _,c in ipairs(cats) do
    local v = "x"
    pcall(function() v = tostring(sp.settings.flags[c]) end)
    if v == "true" then flags_on[#flags_on+1] = c end
  end
  local mats = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    if s ~= nil then
      local n = "no-mats"
      pcall(function() n = tostring(#s.mats) end)
      mats[#mats+1] = c .. ":" .. n
    end
  end
  local wb = "x"; pcall(function() wb = tostring(sp.storage.max_wheelbarrows) end)
  local bins = "x"; pcall(function() bins = tostring(sp.storage.max_bins) end)
  local bar = "x"; pcall(function() bar = tostring(sp.storage.max_barrels) end)
  local num = "x"; pcall(function() num = tostring(sp.stockpile_number) end)
  print(string.format("%s: building_id=%d number=%s rect=%dx%d z=%d wb=%s bins=%s barrels=%s",
    label, sp.id, num, sp.x2-sp.x1+1, sp.y2-sp.y1+1, sp.z-129, wb, bins, bar))
  print("   flags_on: [" .. table.concat(flags_on, ",") .. "]")
  print("   mats: " .. table.concat(mats, " "))
end
dump(get_by_number(6), "UI pile number 6")
for _,id in ipairs({15,17,18,19}) do
  dump(get_by_id(id), "my pile id " .. id)
end
