local want = {"depot", "mason", "craftsdwarf", "furnace", "smelter", "anvil", "stockpile", "bed", "chain", "table", "door", "plot", "well", "still"}
for _,def in ipairs(dfhack.buildings.get_defs()) do
  local nm = def.key.name
  local low = nm:lower()
  for _,w in ipairs(want) do
    if low:find(w) then
      local items = {}
      pcall(function()
        for _,it in ipairs(def.items) do
          local tname = "?"
          for k,v in pairs(df.item_type) do if v == it.first then tname = k end end
          items[#items+1] = tname .. "x" .. tostring(it.second.first)
        end
      end)
      local w, h = "?", "?"
      pcall(function() w = def.nominal_width; h = def.nominal_height end)
      print(string.format("%-22s w=%s h=%s items: %s", nm, tostring(w), tostring(h), table.concat(items, ", ")))
      break
    end
  end
end
