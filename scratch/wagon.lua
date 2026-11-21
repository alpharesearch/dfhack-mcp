local wag = df.global.world.buildings.other.WAGONST[0]
if wag == nil then print("no wagon") return end
print(string.format("wagon extent x%d..%d y%d..%d z=%d", wag.x1, wag.x2, wag.y1, wag.y2, wag.z-129))
local by = {}
local total = 0
for _,ref in ipairs(wag.general_refs) do
  local it = dfhack.items.findRef(ref) and dfhack.items.makeRef(ref).item end
