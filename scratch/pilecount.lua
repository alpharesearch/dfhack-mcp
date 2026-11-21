local function count(x0, x1, y0, y1, z, label)
  local tally = {}
  local total = 0
  for _,it in ipairs(df.global.world.items.other.IN_PLAY) do
    local ok, pos = pcall(function() return it.pos end)
    if ok and pos and pos.x >= x0 and pos.x <= x1 and pos.y >= y0 and pos.y <= y1 and (pos.z - 129) == z then
      local tn = "?"
      pcall(function()
        for k,v in pairs(df) do if v == it._type then tn = k end end
      end)
      tally[tn] = (tally[tn] or 0) + 1
      total = total + 1
    end
  end
  local lines = {}
  for k,v in pairs(tally) do lines[#lines+1] = k .. "=" .. v end
  table.sort(lines)
  print(label .. " items on tiles: " .. total .. " (" .. table.concat(lines, " ") .. ")")
end
count(44, 48, 116, 120, 38, "general pile 12 x44-48 y116-120")
count(46, 49, 116, 117, 38, "wood pile 13 x46-49 y116-117")
local function setflags(id, mode)
  local b = nil
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == id then b = sp end end
  if b == nil then print("pile " .. id .. " missing") return end
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"} do
    if mode == "all" then
      b.settings.flags[c] = true
    elseif mode == "general" then
      b.settings.flags[c] = (c ~= "stone" and c ~= "wood" and c ~= "corpses" and c ~= "refuse" and c ~= "gems")
    end
  end
  local on = {}
  for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"} do
    if b.settings.flags[c] == true then on[#on+1] = c end
  end
  print("pile " .. id .. " set to " .. mode .. " -> allowed: [" .. table.concat(on, ",") .. "]")
end
setflags(12, "general")
setflags(13, "all")
count(44, 48, 116, 120, 38, "AFTER: general pile 12")
