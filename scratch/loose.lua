local function count(x0,x1,y0,y1,z,label)
  local by = {}
  local total = 0
  for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
    local ok, pos = pcall(function() return it.pos end)
    if ok and pos and pos.x >= x0 and pos.x <= x1 and pos.y >= y0 and pos.y <= y1 and (pos.z-129) == z then
      local tn = "?"
      pcall(function() for k,v in pairs(df) do if v == it._type then tn = k end end end)
      local n = 1
      pcall(function() n = it.stack_size end)
      by[tn] = (by[tn] or 0) + n
      total = total + n
    end
  end
  local lines = {}
  for k,v in pairs(by) do lines[#lines+1] = k .. "=" .. v end
  table.sort(lines)
  print(label .. ": " .. total .. " items -> " .. table.concat(lines, " "))
end
count(60, 72, 110, 126, 38, "loose near the wagon (x60-72,y110-126,38)")
count(52, 57, 116, 120, 38, "ON general pile 15 (x52-57,y116-120,38)")
