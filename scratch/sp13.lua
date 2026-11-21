local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 13 then b = sp end end
if b == nil then print("13 missing") return end
print("found pile 13")
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
  local ok, err = pcall(function() b.settings.flags[c] = (c == "wood") end)
  print(string.format("%-15s write_ok=%s %s", c, tostring(ok), ok and "" or tostring(err)))
end
for _,f in ipairs({"max_barrels","max_bins","max_wheelbarrows"}) do
  local ok, err = pcall(function() return b[f] end)
  local okw = "n/a"
  if ok then okw = pcall(function() b[f] = b[f] end) and "writable" or "read-only" end
  print(string.format("%-16s read_ok=%s write=%s", f, tostring(ok), tostring(okw)))
end
local on = {}
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
  if b.settings.flags[c] == true then on[#on+1] = c end
end
print("pile 13 allowed now: [" .. table.concat(on, ",") .. "]")
