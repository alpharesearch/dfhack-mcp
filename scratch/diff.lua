local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"}
local function vsize(t)
  if t == nil then return "nil" end
  local n = -1
  pcall(function() n = #t end)
  return tostring(n)
end
local function dump(id)
  local sp = nil
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then sp = s end end
  if sp == nil then print("pile " .. id .. ": missing") return end
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
      mats[#mats+1] = c .. ":" .. vsize(s.mats)
    end
  end
  print(string.format(
    "pile %d rect=%dx%d z=%d stockpile_flag=%s stockpile_number=%s specific=%s storage{bar=%s bin=%s wb=%s} container_item_id=%s flags_on=[%s]",
    sp.id, sp.x2-sp.x1+1, sp.y2-sp.y1+1, sp.z-129, tostring(sp.stockpile_flag), tostring(sp.stockpile_number),
    vsize(sp.specific), vsize(sp.storage.max_barrels)=="-1" and tostring(sp.storage.max_barrels) or tostring(sp.storage.max_barrels),
    tostring(sp.storage.max_bins), tostring(sp.storage.max_wheelbarrows), vsize(sp.storage.container_item_id),
    table.concat(flags_on, ",")))
  print("   mats: " .. table.concat(mats, " "))
end
for _,id in ipairs({6,15,17,18,19}) do dump(id) end
