local cap = 0
for _,u in ipairs(dfhack.units.getCitizens()) do
  local nm = "?"
  pcall(function() nm = dfhack.units.getProfessionName(u) end)
  local mine = false
  pcall(function() mine = u.status.labors[df.unit_labor.MINE] end)
  print(string.format("%-18s labors.MINE=%s", nm, tostring(mine)))
end
local picks, weapons = 0, 0
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  local t = nil
  pcall(function() t = df.items.findType(it) end)
  if t == df.item_type.WEAPON then
    weapons = weapons + 1
    local d = ""
    pcall(function() d = df.items.getDescription(it, 0) end)
    if d:find("[Pp]ick") then picks = picks + 1 end
  end
end
print(string.format("weapons in play: %d, of which picks: %d", weapons, picks))
