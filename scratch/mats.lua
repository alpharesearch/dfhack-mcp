local want = {"item_blockst","item_boulderst","item_rockst","item_barsst","item_anvilst","item_mechanismst","item_weaponst","item_bagst","item_tradegoodst","item_glassst","item_roughst"}
local tally = {}
for _,it in ipairs(df.global.world.items.all) do
  local tn = "?"
  pcall(function()
    for k,v in pairs(df) do
      if v == it._type then tn = k end
    end
  end)
  tally[tn] = (tally[tn] or 0) + 1
end
local lines = {}
for k,v in pairs(tally) do lines[#lines+1] = k .. "=" .. v end
table.sort(lines)
print(table.concat(lines, " "))
