local fresh = df.new(df.stockpile_settings)
if fresh == nil then print("FAIL fresh stockpile_settings") return end
local probe = {}
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"} do
  local s = fresh[c]
  if s ~= nil then
    local n = -1
    pcall(function() n = #s.mats end)
    local om = -1
    pcall(function() om = #s.other_mats end)
    probe[#probe+1] = c .. ":mats=" .. n .. " other=" .. om
  end
end
print("FRESH settings: " .. table.concat(probe, " "))
