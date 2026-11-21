local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
if b == nil then print("no 12") return end
local f = b.settings.flags
local keys = {}
for k,v in pairs(f) do keys[#keys+1] = tostring(k) .. "=" .. tostring(v) end
table.sort(keys)
print("flags: " .. table.concat(keys, " "))
local w = {}
for k,v in pairs(b) do
  if type(v) ~= "table" then w[#w+1] = tostring(k) .. "=" .. tostring(v) end
end
table.sort(w)
print("building scalars: " .. table.concat(w, " "))
