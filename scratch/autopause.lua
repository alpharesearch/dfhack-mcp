local out = {}
for k,v in pairs(df.global.world.options) do
  local ks = tostring(k)
  if ks:lower():find("pause") then
    if type(v) == "table" then
      local inner = {}
      for kk,vv in pairs(v) do inner[#inner+1] = tostring(kk) .. "=" .. tostring(vv) end
      table.sort(inner)
      out[#out+1] = ks .. "{" .. table.concat(inner, ",") .. "}"
    else
      out[#out+1] = ks .. "=" .. tostring(v)
    end
  end
end
table.sort(out)
print("options pause fields: " .. table.concat(out, "  "))
print("load_pause_state: " .. tostring(df.global.world.options.load_pause_state))
