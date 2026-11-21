local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
local out = {}
for _,name in ipairs({"flags","flags2","flags3"}) do
  local f = b[name]
  if f ~= nil then
    local ks = {}
    for k,v in pairs(f) do ks[#ks+1] = tostring(k) .. "=" .. tostring(v) end
    table.sort(ks)
    out[#out+1] = name .. ": " .. table.concat(ks, " ")
  end
end
print(table.concat(out, " || "))
local L = b.settings.links
local ks = {}
for k,v in pairs(L) do ks[#ks+1] = tostring(k) .. "=" .. (type(v)=="table" and "table" or tostring(v)) end
table.sort(ks)
print("settings.links: " .. table.concat(ks, " "))
