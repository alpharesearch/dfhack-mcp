local cats = {"ammo","armor","coins","finished_goods","furniture","leather","weapons","wood","stone","gems","cloth","sheet","bars_blocks"}
local SIZES = {ammo=319, armor=319, coins=319, finished_goods=319, furniture=319, weapons=319,
               bars_blocks=319, sheet=319, cloth=319, gems=319, stone=319, wood=319, leather=892}
local function fix(id)
  local sp = nil
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then sp = s end end
  if sp == nil then print("pile " .. id .. ": missing") return end
  local log = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    if s ~= nil and sp.settings.flags[c] == true then
      local n = 0
      pcall(function() n = #s.mats end)
      local want = SIZES[c] or 319
      if n < want then
        local ok, err = pcall(function()
          s.mats:resize(want)
          for i = 0, want - 1 do s.mats[i] = 1 end
        end)
        log[#log+1] = c .. ":" .. n .. "->" .. (ok and tostring(want) or ("ERR " .. tostring(err)))
      else
        log[#log+1] = c .. ":ok(" .. n .. ")"
      end
    end
  end
  print("pile " .. id .. " repaired: " .. table.concat(log, " "))
end
for _,id in ipairs({15,17,18,19}) do fix(id) end
