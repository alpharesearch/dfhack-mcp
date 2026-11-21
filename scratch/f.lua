local blk = dfhack.maps.getBlock(3,7,10)
local d = blk.designation[1]
local cand = {"type","market","smooth","engraving","erase_designation","has_flowing",
              "has_growing","rail_designation","extract_stone","extract_iron","remove_boulder",
              "boulder","road_designation","is_dig_designation","dig","stone"}
for _,k in ipairs(cand) do
  local ok, v = pcall(function() return d[k] end)
  local oks = "read-only"
  if ok then
    local okw = pcall(function() d[k] = v end)
    oks = okw and "WRITABLE" or "read-only"
  end
  print(string.format("%-18s ok=%-5s val=%-6s %s", k, tostring(ok), tostring(ok and v), oks))
end
