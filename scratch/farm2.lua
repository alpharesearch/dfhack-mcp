local mats = {}
for _,it in ipairs(df.global.world.items.other.SEEDS) do
  local m = "?"
  pcall(function()
    local nameptr = df.global.world.mat_table.mat_names[it.mat]
    m = dfhack.df2utf(dfhack.readstring(nameptr))
  end)
  local n = 1
  pcall(function() n = it.stack_size end)
  mats[m] = (mats[m] or 0) + n
end
local l = {}
for k,v in pairs(mats) do l[#l+1] = k .. "=" .. v end
table.sort(l)
print("seeds by material: " .. table.concat(l, " "))

local names = {}
for k,v in pairs(df.enums.build_type_key) do
  local ks = tostring(k)
  if ks:find("FARM") or ks:find("Plot") then names[#names+1] = ks .. "=" .. tostring(v) end
end
print("farm-ish building type keys: " .. table.concat(names, " "))
