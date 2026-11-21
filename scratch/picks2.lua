local cap_total = 0
for _,u in ipairs(dfhack.units.getCitizens()) do
  local nm = "?"
  pcall(function() nm = dfhack.units.getProfessionName(u) end)
  local cap = "err"
  pcall(function() cap = dfhack.units.getLaborCapacity(u, df.unit_labor.MINE) end)
  local des = "err"
  pcall(function() des = dfhack.units.hasDesignation(u, df.unit_labor.MINE) end)
  if type(cap) == "number" then cap_total = cap_total + cap end
  print(string.format("%-18s designated=%-6s pick_capacity=%s", nm, tostring(des), tostring(cap)))
end
print("total pick capacity: " .. cap_total)
local picks = 0
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  if it:getType(df.item_type.WEAPON) then
    local wp = it
    local nm = ""
    pcall(function() nm = dfhack.items.getDescription(wp, 0) end)
    if nm:find("[Pp]ick") then picks = picks + 1 end
  end
end
print("pick weapons in play: " .. picks)
