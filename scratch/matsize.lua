local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"}
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  local parts = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    if s ~= nil then
      local n = -1
      pcall(function() n = #s.mats end)
      local other = {}
      for k,v in pairs(s) do
        if k ~= "mats" and type(v) ~= "table" then other[#other+1] = k .. "=" .. tostring(v) end
      end
      table.sort(other)
      parts[#parts+1] = c .. ":mats=" .. n .. (other[1] and ("{" .. table.concat(other, ",") .. "}") or "")
    end
  end
  print("pile " .. sp.id .. " | " .. table.concat(parts, " "))
end
