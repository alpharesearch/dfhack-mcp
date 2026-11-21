local by_type = {}
local picks = {}
for _,it in ipairs(df.global.world.items.all) do
  local t = nil
  pcall(function() t = df.items.findType(it) end)
  local tn = "nil"
  if t ~= nil then
    for k,v in pairs(df.item_type) do if v == t then tn = k end end
  end
  by_type[tn] = (by_type[tn] or 0) + 1
  local d = ""
  pcall(function() d = df.items.getDescription(it, 0) end)
  if d:find("[Pp]ick") then
    local mode = "?"
    pcall(function() mode = it.mode end)
    picks[#picks+1] = d .. " mode=" .. tostring(mode)
  end
end
local lines = {}
for k,v in pairs(by_type) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print("items by type: " .. table.concat(lines, " "))
print("picks found: " .. #picks)
for i=1,math.min(#picks,6) do print("  " .. picks[i]) end
