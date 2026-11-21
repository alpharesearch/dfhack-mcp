local m = df.multifilter.new()
if m == nil then print("FAIL multifilter.new") return end
local ks = {}
for k,v in pairs(m) do ks[#ks+1] = tostring(k) .. (type(v)=="table" and "=table" or "=" .. tostring(v)) end
table.sort(ks)
print("multifilter: " .. table.concat(ks, " "))
local hf = {}
for k,v in pairs(df.haulable_flag) do if type(k)=="string" then hf[#hf+1] = k end end
table.sort(hf)
print("haunable_flag names: " .. table.concat(hf, " "))
m.flags:enable(df.haulable_flag.All)
print("flags set ok")
local b = dfhack.buildings.constructBuilding({type=21, subtype=0, custom=0,
  x=36, y=116, z=167, width=6, height=5, filters={m}})
print("constructBuilding=" .. tostring(b))
if b ~= nil then
  local at = dfhack.buildings.findAtTile(36, 116, 167)
  if at ~= nil then
    print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2,
      dfhack.buildings.countExtentTiles(at)))
  end
end
