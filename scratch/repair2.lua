local cats = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","misc","refuse","sheet","stone","weapons","wood"}

local function by_id(id)
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.id == id then return s end end
  return nil
end
local function by_number(n)
  for _,s in ipairs(df.global.world.buildings.other.STOCKPILE) do if s.stockpile_number == n then return s end end
  return nil
end

-- Is this a vector<char>-style list (the material/quality arrays the custom UI walks)?
local function is_charvec(v)
  local n = -1
  local ok = pcall(function() n = #v end)
  if not ok or n < 0 then return false end
  local d = tostring(v)
  return d:find("vector<char>") ~= nil or d:find("bool%[%]") ~= nil
end

local function collect(sp)
  -- category -> field -> size, for every list-shaped field the UI walks
  local out = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    if s ~= nil then
      local fields = {}
      for k,v in pairs(s) do
        if type(v) ~= "string" and type(v) ~= "number" and type(v) ~= "boolean" then
          if is_charvec(v) then fields[tostring(k)] = #v end
        end
      end
      out[c] = fields
    end
  end
  return out
end

local REF_PILE = by_number(6)
if REF_PILE == nil then print("no UI pile number 6 to copy from") return end
local ref = collect(REF_PILE)
local lines = {}
for c, fields in pairs(ref) do
  for k, n in pairs(fields) do lines[#lines+1] = c .. "." .. k .. "=" .. n end
end
table.sort(lines)
print("UI pile 6 (id " .. REF_PILE.id .. ") field sizes: " .. table.concat(lines, " "))

local function repair(id)
  local sp = by_id(id)
  if sp == nil then print("pile " .. id .. ": missing") return end
  local log = {}
  for _,c in ipairs(cats) do
    local s = sp.settings[c]
    local on = false
    pcall(function() on = (sp.settings.flags[c] == true) end)
    if s ~= nil and on then
      local rf = ref[c] or {}
      for k,v in pairs(s) do
        if type(v) ~= "string" and type(v) ~= "number" and type(v) ~= "boolean" and is_charvec(v) then
          local want = rf[tostring(k)]
          if want ~= nil and want > 0 and #v < want then
            local ok, err = pcall(function()
              v:resize(want)
              for i = 0, want - 1 do
                pcall(function() v[i] = 1 end)
              end
            end)
            log[#log+1] = c .. "." .. tostring(k) .. ":" .. #v .. "->" .. (ok and tostring(want) or ("ERR " .. tostring(err)))
          end
        end
      end
    end
  end
  print("pile " .. id .. " repaired: " .. (#log > 0 and table.concat(log, " ") or "nothing to do"))
end

for _,id in ipairs({15,17,18,19}) do repair(id) end
