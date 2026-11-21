local names = {}
for k,v in pairs(dfhack.buildings) do names[#names+1] = tostring(k) .. "=" .. type(v) end
table.sort(names)
print("dfhack.buildings: " .. table.concat(names, " "))
local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 12 then b = sp end end
if b then print("pile12 x1="..b.x1.." x2="..b.x2.." y1="..b.y1.." y2="..b.y2.." z="..b.z.." room="..tostring(b.room.width).."x"..tostring(b.room.height)) end
