local b = dfhack.buildings.constructBuilding({type=21, subtype=0, custom=0,
  x=36, y=116, z=167, width=6, height=5, abstract=true, nominal=true})
if b == nil then print("FAIL constructBuilding") return end
local at = dfhack.buildings.findAtTile(36, 116, 167)
if at == nil then print("FAIL not at tile") return end
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2,
  dfhack.buildings.countExtentTiles(at)))
