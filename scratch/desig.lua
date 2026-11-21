local blk = dfhack.maps.getBlock(3, 7, 10)
if blk == nil then print("no block at block-coords (3,7,10)") return end
print("block found")
local d = blk.designation[1]
if d == nil then d = blk.designation[0] end
print("designation element type: " .. type(d))
local fields = {}
for k,v in pairs(d) do fields[#fields+1] = tostring(k) end
print("keys: " .. table.concat(fields, " "))
local b2 = d.bits2
if b2 ~= nil then
  local keys = {}
  for k,v in pairs(b2) do keys[#keys+1] = tostring(k) end
  print("bits2 keys: " .. table.concat(keys, " "))
end
