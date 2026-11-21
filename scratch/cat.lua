local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
local w = b.settings.wood
local keys = {}
for k,v in pairs(w) do
  if type(v) ~= "table" then keys[#keys+1] = tostring(k) .. "=" .. tostring(v) end
end
table.sort(keys)
print("wood category fields: " .. table.concat(keys, " "))
local st = b.settings.stone
local k2 = {}
for k,v in pairs(st) do if type(v) ~= "table" then k2[#k2+1] = tostring(k) .. "=" .. tostring(v) end end
table.sort(k2)
print("stone category fields: " .. table.concat(k2, " "))
local g = {}
for k,v in pairs(b.settings) do if type(v) ~= "table" then g[#g+1] = tostring(k) .. "=" .. tostring(v) end end
table.sort(g)
print("settings scalars: " .. table.concat(g, " "))
