local function rect_count(b)
  local by = {}
  local total = 0
  for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
    local ok, pos = pcall(function() return it.pos end)
    if ok and pos and pos.x >= b.x1 and pos.x <= b.x2 and pos.y >= b.y1 and pos.y <= b.y2 and pos.z == b.z then
      local tn = "?"
      pcall(function()
        local t = tostring(it._type)
        tn = t:match("<type: ([_%w]+)") or t
      end)
      tn = tn:gsub("item_", ""):gsub("st$", "")
      local n = 1
      pcall(function() n = it.stack_size end)
      by[tn] = (by[tn] or 0) + n
      total = total + n
    end
  end
  local lines = {}
  for k,v in pairs(by) do lines[#lines+1] = k .. "=" .. v end
  table.sort(lines)
  return total, table.concat(lines, " ")
end
for _,id in ipairs({15,17,18,19}) do
  local sp = nil
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then sp = s end end
  if sp == nil then print("pile " .. id .. " missing") goto cont end
  local t, l = rect_count(sp)
  print(string.format("pile %d (x%d..%d,y%d..%d,in-game %d): %d items -> %s", id, sp.x1, sp.x2, sp.y1, sp.y2, sp.z-129, t, l))
  ::cont::
end
