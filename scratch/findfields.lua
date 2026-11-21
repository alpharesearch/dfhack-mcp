local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
if b == nil then print("12 missing") return end
local ks = {}
for k,v in pairs(b) do ks[#ks+1] = tostring(k) end
table.sort(ks)
print("building fields: " .. table.concat(ks, " "))
local sk = {}
for k,v in pairs(b.settings) do sk[#sk+1] = tostring(k) end
table.sort(sk)
print("settings fields: " .. table.concat(sk, " "))
local lk = {}
for k,v in pairs(b.settings.links) do lk[#lk+1] = tostring(k) end
table.sort(lk)
print("links fields: " .. table.concat(lk, " "))
