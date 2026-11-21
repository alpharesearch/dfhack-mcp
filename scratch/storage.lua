local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
if b == nil then print("12 missing") return end
local ks = {}
for k,v in pairs(b.storage) do
  ks[#ks+1] = tostring(k) .. (type(v)=="table" and "=table" or "=" .. tostring(v))
end
table.sort(ks)
print("storage: " .. table.concat(ks, " "))
local lk = {}
for k,v in pairs(b.links) do lk[#lk+1] = tostring(k) .. (type(v)=="table" and "=table" or "=" .. tostring(v)) end
table.sort(lk)
print("links: " .. table.concat(lk, " "))
local ok, err = pcall(function() b.storage.max_wheelbarrows = b.storage.max_wheelbarrows end)
print("max_wheelbarrows writable: " .. tostring(ok) .. " " .. tostring(err))
print("value: " .. tostring(b.storage.max_wheelbarrows) .. " barrels_max=" .. tostring(b.storage.max_barrels) .. " bins_max=" .. tostring(b.storage.max_bins))
