local tally = {}
for _,it in ipairs(df.global.world.items.all) do
  if it._type == df.item_weaponst then
    local nm = "?"
    pcall(function() nm = it.subtype.name end)
    local m = "?"
    pcall(function() m = it.mode end)
    local key = nm .. "|mode" .. tostring(m)
    tally[key] = (tally[key] or 0) + 1
  end
end
local lines = {}
for k,v in pairs(tally) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print(table.concat(lines, "  "))
