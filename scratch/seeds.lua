local by = {}
for _,it in ipairs(df.global.world.items.other.SEEDS) do
  local m = "?"
  pcall(function() m = it.matname end)
  local n = 1
  pcall(function() n = it.stack_size end)
  by[m] = (by[m] or 0) + n
end
local lines = {}
for k,v in pairs(by) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print("seeds by material: " .. table.concat(lines, " "))
local buckets = 0
for _,it in ipairs(df.global.world.items.other.BARS) do end
for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
  pcall(function() if it._type == df.item_bucketst and it.flags.on_ground then buckets = buckets + 1 end end)
end
print("loose buckets: " .. buckets)
