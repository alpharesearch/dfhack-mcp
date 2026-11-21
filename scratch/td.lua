local names = {}
for k,v in pairs(df.tile_designation) do
  if type(k) == "string" then names[#names+1] = k .. ":" .. tostring(v) end
end
print("tile_designation members: " .. table.concat(names, " | "))
local blk = dfhack.maps.getBlock(3,7,10)
local d = blk.designation[1]
local vals = {}
for i=0,15 do vals[#vals+1] = i .. "=" .. tostring(d[i]) end
print("values at idx1: " .. table.concat(vals, " "))
