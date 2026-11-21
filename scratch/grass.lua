local x0, x1, y0, y1, z = 50, 95, 100, 140, 167
local grass = {}
local n = 0
for x = x0, x1 do
  for y = y0, y1 do
    local t = dfhack.maps.getTileAt(x, y, z)
    if t ~= nil then
      local tt = t.typedef == nil and t.tile.type or t.tile.type
      local name = ""
      for _,e in ipairs(df.tiletype) do
        if e.id == tt then name = e.key end
      end
      if name:find("Grass") and not name:find("Wall") then
        grass[#grass+1] = {x, y}
        n = n + 1
      end
    end
  end
end
print("grass floor tiles in x50-95,y100-140 at in-game 38: " .. n)
-- find 3x3 fully-grass top-lefts
local set = {}
for _,g in ipairs(grass) do set[g[1] .. "," .. g[2]] = true end
local blocks = {}
for _,g in ipairs(grass) do
  local ok = true
  for dx = 0, 2 do
    for dy = 0, 2 do
      if set[(g[1]+dx) .. "," .. (g[2]+dy)] ~= true then ok = false end
    end
  end
  if ok then blocks[#blocks+1] = g[1] .. "," .. g[2] end
end
print("3x3 all-grass top-lefts: " .. (#blocks > 0 and table.concat(blocks, " ") .. (" (" .. #blocks .. " total)")) .. " (showing first: " .. (blocks[1] or "none") .. ")")
