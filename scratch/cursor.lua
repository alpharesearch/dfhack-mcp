local u = df.global.world.ui
local hits = {}
for k,v in pairs(u) do
  local ks = tostring(k)
  if ks:find("cursor") then hits[#hits+1] = ks .. "=" .. (type(v)=="table" and "table" or tostring(v)) end
end
table.sort(hits)
print("ui cursor fields: " .. table.concat(hits, " "))
local ok, s = pcall(function() return dfhack.gui.getCurViewscreen() end)
print("cur screen: " .. tostring(ok) .. " " .. tostring(s))
