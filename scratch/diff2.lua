local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"}
local function vsize(t)
  if t == nil then return "nil" end
  local n = -1
  pcall(function() n = #t end)
  return tostring(n)
end
local function get(num)
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if s.stockpile_number == num then return s end
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
  print(string.format("%s: building_id=%d number=%s rect=%dx%d z=%d specific=%s wb=%s bins=%s barrels=%s flags_on=[%s]",
    label, sp.id, tostring(sp.stockpile_number), sp.x2-sp.x1+1, sp.y2-sp.y1+1, sp.z-129,
    vsize(sp.specific), tostring(sp.storage.max_wheelbarrows), tostring(sp.storage.max_bins),
    tostring(sp.storage.max_barrels), table.concat(flags_on, ",")))
  print("   mats: " .. table.concat(mats, " "))
end
dump(get(6), "UI pile number 6")
for _,id in ipairs({15,17,18,19}) do
  local sp = nil
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then sp = s end end
  dump(sp, "my pile id " .. id)
end
