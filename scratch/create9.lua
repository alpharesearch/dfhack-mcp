local function make(x, y, z, w, h, label)
  local inst = dfhack.buildings.allocInstance(xyz2pos(x, y, z), 29, -1, -1)
  if inst == nil then print(label .. ": FAIL allocInstance") return end
  local ok = dfhack.buildings.setSize(inst, w, h, 8)
  if not ok then print(label .. ": FAIL setSize (overlap?)") return end
  local oka = dfhack.buildings.constructAbstract(inst)
  if not oka then print(label .. ": FAIL constructAbstract") return end
  local at = dfhack.buildings.findAtTile(x, y, z)
  if at == nil then print(label .. ": FAIL not at tile") return end
  print(string.format("%s: OK id=%d extent=%d..%d,%d..%d", label, at.id, at.x1, at.x2, at.y1, at.y2))
end
make(52, 116, 167, 6, 5, "general")
make(56, 121, 167, 4, 2, "wood")
make(73, 116, 165, 6, 7, "refuse-outside")
