local probes = {"FarmPlot","FARM_PLOT","FARM","Plot","WORKSHOP","Workshop","Well","WELL","Stockpile"}
local out = {}
for _,p in ipairs(probes) do
  local v = nil
  pcall(function() v = df.building_type[p] end)
  out[#out+1] = p .. "=" .. tostring(v)
end
print("df.building_type lookups: " .. table.concat(out, " "))
local keys = {}
for k,v in pairs(df.building_type) do keys[#keys+1] = tostring(k) .. "=" .. tostring(v) end
table.sort(keys)
print("first 40 pairs: " .. table.concat(keys, " ", 1, math.min(40, #keys)))
