-- Count wall tile families per elevation in the fort area, via block cache access.
local names = {}
for _,b in ipairs(df.global.world.map.all_blocks) do
  if b.map == nil then goto cont end
  local z = b.z
  if z < 161 or z > 170 then goto cont end
  local x0, y0 = b.x*16, b.y*16
  if x0 < 3008/16*16 then end
  for sx=0,15 do
    for sy=0,15 do
      local t = b.map[sx][sy]
      if t ~= nil then
        local tt = t.tile.type
        local nm = df.tiletype.attrs[tt] and df.tilename[tt] or nil
        local key = nil
        pcall(function() key = df.tiletype_key[tt] end)
        local s = key and dfhack.df2utf(dfhack.readstring(df.tiletype_key[tt])) or nil
        local base = tostring(tt)
        if s ~= nil and (s:find("SOIL") or s:find("SOIL") or s:lower():find("soil")) then
          local k = (z-129) .. ":" .. s
          names[k] = (names[k] or 0) + 1
        end
      end
    end
  end
  ::cont::
end
local l = {}
for k,v in pairs(names) do l[#l+1] = k .. "=" .. v end
table.sort(l)
print("soil tiletypes by elev: " .. table.concat(l, " "))
