local names = {}
for k,v in pairs(df) do
  local ks = tostring(k)
  if ks:find("filter") or ks:find("haulable") then names[#names+1] = ks end
end
table.sort(names)
print("df filter-ish types: " .. table.concat(names, " "))
