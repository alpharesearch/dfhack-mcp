local function make(x, y, z, w, h, label)
  local inst = dfhack.buildings.allocInstance(xyz2pos(x, y, z), 29, -1, -1)
  if inst == nil then print(label .. ": FAIL allocInstance") return nil end
  if not dfhack.buildings.setSize(inst, w, h, 8) then print(label .. ": FAIL setSize") return nil end
  if not dfhack.buildings.constructAbstract(inst) then print(label .. ": FAIL constructAbstract") return nil end
  local at = dfhack.buildings.findAtTile(x, y, z)
  if at == nil then print(label .. ": FAIL findAtTile") return nil end
  local n = 0
  pcall(function() n = dfhack.buildings.countExtentTiles(at, at.x1, at.z, at.y1) end)
  local area = (at.x2-at.x1+1)*(at.y2-at.y1+1)
  print(string.format("%s: OK id=%d rect=%dx%d usable=%d holes=%d", label, at.id, at.x2-at.x1+1, at.y2-at.y1+1, n, area-n))
  return at.id
end
print("--- remove the holed wood pile 16")
local old = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do if sp.id == 16 then old = sp end end
if old then print("deconstruct=" .. tostring(dfhack.buildings.deconstruct(old))) end
local wood = make(58, 116, 167, 6, 4, "wood")
local stone = make(30, 126, 165, 4, 3, "stone")
if wood then
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if sp.id == wood then
      sp.storage.max_barrels = 0
      sp.storage.max_bins = 0
      for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
        sp.settings.flags[c] = (c == "wood")
      end
      print("wood pile filters set")
    end
  end
end
if stone then
  for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
    if sp.id == stone then
      sp.storage.max_wheelbarrows = 3
      sp.storage.max_barrels = 0
      sp.storage.max_bins = 0
      for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
        sp.settings.flags[c] = (c == "stone")
      end
      print("stone pile filters + 3 wheelbarrows set")
    end
  end
end
