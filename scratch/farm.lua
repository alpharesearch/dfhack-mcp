-- what are our seeds?
local mats = {}
for _,it in ipairs(df.global.world.items.other.SEEDS) do
  local m = "?"
  pcall(function() m = dfhack.items.getDescription(it, it.mat, true) end)
  local n = 1
  pcall(function() n = it.stack_size end)
  mats[m] = (mats[m] or 0) + n
end
local l = {}
for k,v in pairs(mats) do l[#l+1] = k .. "=" .. v end
table.sort(l)
print("seeds by material: " .. table.concat(l, " "))

-- farm plot type id
local ft = nil
for _,e in ipairs(df.building_type) do
  if e.key:find("FARM") then ft = e.id print("farm plot building_type id=" .. tostring(e.id) .. " key=" .. e.key) end
end

-- build it with the library's full commit path
local ok, err = pcall(function()
  return dfhack.buildings.constructBuilding{
    building_type = ft, designation = nil,
    x = 67, y = 111, z = 167, width = 3, height = 3,
    items = {}, filters = nil
  }
end)
print("constructBuilding farm plot: ok=" .. tostring(ok) .. " result=" .. tostring(err))
local b = dfhack.buildings.findAtTile(67, 111, 167)
if b ~= nil then
  print("placed: id=" .. tostring(b.id) .. " type=" .. tostring(b:getType()))
  local done = false
  pcall(function() done = dfhack.buildings.isComplete(b) end)
  print("complete=" .. tostring(done))
else
  print("no building at 67,111,in-game 38")
end
