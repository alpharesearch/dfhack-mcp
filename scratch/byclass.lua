local by = {}
local picks = {}
for _,it in ipairs(df.global.world.items.all) do
  local tn = "?"
  pcall(function() tn = it._type['getName'] and it._type:getName() or tostring(it._type) end)
  by[tn] = (by[tn] or 0) + 1
  local d = ""
  pcall(function() d = df.items.getDescription(it, 0) end)
  if d:find("[Pp]ick") then
    local m = "?"
    pcall(function() m = it.mode end)
    picks[#picks+1] = d .. " mode=" .. tostring(m)
  end
end
local lines = {}
for k,v in pairs(by) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print("classes: " .. table.concat(lines, " "))
print("picks: " .. #picks)
for i=1,math.min(#picks,8) do print("  " .. picks[i]) end
