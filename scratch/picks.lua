local cap_total = 0
for _,u in ipairs(dfhack.units.getCitizens()) do
  local nm = "?"
  pcall(function() nm = dfhack.units.getProfessionName(u) end)
  local cap = nil
  pcall(function() cap = dfhack.units.getLaborCapacity(u, df.unit_labor.MINE) end)
  local has = nil
  pcall(function() has = dfhack.units.hasDesignation(u, df.unit_labors.MINE) end)
  if cap ~= nil then cap_total = cap_total + cap end
  print(string.format("%-18s MINE_designated=%-5s pick_capacity=%s", nm, tostring(has), tostring(cap)))
end
print("total pick capacity across citizens: " .. cap_total)
local picks = 0
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  if it:getType(df.item_type.WEAPON) then
    local w = it
    pcall(function() if w.flags2 and w.flags2.BIT_NAMES then end end)
  end
end
local weapon_count = 0
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  if it:getType(df.item_type.WEAPON) then weapon_count = weapon_count + 1 end
end
print("weapons in play (picks live here): " .. weapon_count)
