local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"}
local function is_charvec(v)
  local n = -1
  local ok = pcall(function() n = #v end)
  if not ok or n < 0 then return false end
  local d = tostring(v)
  return d:find("vector<char>") ~= nil or d:find("bool%[%]") ~= nil
end
for _,id in ipairs({15,18,19}) do
  local sp = nil
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then sp = s end end
  if sp == nil then print("pile " .. id .. " missing") goto cont end
  local parts = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    local on = false
    pcall(function() on = (sp.settings.flags[c] == true) end)
    if s ~= nil and on then
      local fs = {}
      for k,v in pairs(s) do
        if type(v) ~= "string" and type(v) ~= "number" and type(v) ~= "boolean" and is_charvec(v) then
          fs[#fs+1] = tostring(k) .. "=" .. #v
        end
      end
      table.sort(fs)
      parts[#parts+1] = c .. "{" .. table.concat(fs, ",") .. "}"
    end
  end
  print("pile " .. id .. ": " .. table.concat(parts, " "))
  ::cont::
end
