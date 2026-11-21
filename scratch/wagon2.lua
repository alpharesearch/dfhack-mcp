local wag = df.global.world.buildings.other.WAGONST[0]
if wag == nil then print("no wagon") return end
print(string.format("wagon x%d..%d y%d..%d z=%d", wag.x1, wag.x2, wag.y1, wag.y2, wag.z-129))
local by = {}
local total = 0
for _,ref in ipairs(wag.general_refs) do
  local item = nil
  pcall(function() item = dfhack.items.makeRef(ref).item end)
  if item ~= nil then
    local tn = "?"
    pcall(function() for k,v in pairs(df) do if v == item._type then tn = k end end end)
    local n = 1
    pcall(function() n = item.stack_size end)
    by[tn] = (by[tn] or 0) + n
    total = total + n
  end
end
local lines = {}
for k,v in pairs(by) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print("wagon inventory (" .. total .. " items): " .. table.concat(lines, " "))
local sp = nil
for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == 15 then sp = s end end
if sp then
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
    if sp.settings.flags[c] == true then on[#on+1] = c end
  end
  print("general pile 15 accepts: " .. table.concat(on, ","))
  local ok = 0
  for _,u in ipairs(dfhack.units.getCitizens()) do
    pcall(function() if u.status.labors[df.unit_labor.HAUL_ITEM] then ok = ok + 1 end end)
  end
  print("citizens with HAUL_ITEM: " .. ok)
end
