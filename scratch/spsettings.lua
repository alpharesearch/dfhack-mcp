local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  if sp.id == 12 then b = sp end
end
if b == nil then print("stockpile 12 not found") return end
local s = b.settings
local keys = {}
for k,v in pairs(s) do
  if type(v) ~= "table" then keys[#keys+1] = tostring(k) .. "=" .. tostring(v) end
end
table.sort(keys)
print("scalar settings: " .. table.concat(keys, " "))
local cats = {}
for k,v in pairs(s) do
  if type(v) == "string" or type(v) == "boolean" then
    if tostring(k):find("^allow") then cats[#cats+1] = tostring(k) .. "=" .. tostring(v) end
  end
end
table.sort(cats)
print("allow flags: " .. table.concat(cats, " "))
