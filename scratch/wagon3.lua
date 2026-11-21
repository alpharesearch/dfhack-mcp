local wag = nil
if wag == nil then
  -- fall back: scan the wagon area with findAtTile
  for x = 44, 66 do
    for y = 112, 126 do
      local b = dfhack.buildings.findAtTile(x, y, 167)
      if b ~= nil and tostring(b._type):find("wagon") then wag = b break end
    end
    if wag then break end
  end
end
if wag == nil then print("no wagon found") return end
print(string.format("wagon id=%d x%d..%d y%d..%d z=%d", wag.id, wag.x1, wag.x2, wag.y1, wag.y2, wag.z-129))
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
end
local ok = 0
for _,u in ipairs(dfhack.units.getCitizens()) do
  pcall(function() if u.status.labors[df.unit_labor.HAUL_ITEM] then ok = ok + 1 end end)
end
print("citizens with HAUL_ITEM: " .. ok)
