local w = 0
local names = {}
for _,it in ipairs(df.global.world.items.all) do
  if it._type == df.item_weaponst then
    w = w + 1
    local d = "?"
    pcall(function() d = df.items.getDescription(it, 0) end)
    local m = "?"
    pcall(function() m = it.mode end)
    names[#names+1] = d .. "(mode=" .. tostring(m) .. ")"
  end
end
print("weapon count: " .. w)
for i=1,math.min(#names,12) do print("  " .. names[i]) end
local bars = 0
for _,it in ipairs(df.global.world.items.all) do if it._type == df.item_barsst then bars = bars + 1 end end
print("metal bars in world: " .. bars)
