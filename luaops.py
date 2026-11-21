"""Typed game operations implemented with DFHack Lua INSIDE the server.

Why this file exists
--------------------
`CoreProtocol.RunCommand` reaches DFHack's `lua` command, and `lua` evaluates code
(verified: `lua print(1+1)` -> "2"). Through it the game exposes `dfhack.buildings`,
`dfhack.maps`, `dfhack.units`, `dfhack.items`, `dfhack.gui`, `dfhack.military`,
`reqscript("quickfort")` - i.e. far more than the proto method list suggests.

Exposing raw Lua to the model is the wrong shape: a local model can corrupt a save with
one bad field write, it cannot enumerate the API, and every call would ship a Lua
string through the prompt. So the rule here is: **the model names an operation with
typed parameters; this module composes the Lua, runs it, parses one printed line, and
verifies the result through the same channel.**

Conventions
-----------
* Coordinates are game elevations in/out; RPC z = elev + GAME_Z_OFFSET, converted here.
* Every template prints exactly one status line: `OK ...` on success, `FAIL ...` with a
  reason on failure. Parsing is one split, never a scrape of free-form output.
* Building types are resolved from RemoteFortressReader's own definition list (verified
  live: Trade Depot bt=6, Stockpile 29, Zone 30, Bed 1, Table 2, Chair 0, Door 8, Box 10,
  Workshop 13 - Still 13/15, Farm Plot 4, Furnace 5, Smelter 5/1, Well 18). The Lua
  `lua` environment has NO `dfhack.dfinfo` and no enum constants, so ids must come from
  the server, not from Lua.
* Writes are verified with `dfhack.buildings.findAtTile` after placement, because
  RemoteFortressReader is NOT a reliable verification channel: measured on the live
  save, a depot created via constructBuilding+completeBuild is present in game state
  (findAtTile returns the building) while GetBlockList reports zero buildings on that
  level, and ResetMapHashes + force_reload do not change that.
"""

from __future__ import annotations

import asyncio
import re

from dfhack_client_python.dfhack import RunCommand
from dfhack_client_python.dfhack_remote import DFHackError, DFHackTransportError, drain_replies
from dfhack_client_python.py_export.CoreProtocol_pb2 import CoreRunCommandRequest

from worldmodel import GAME_Z_OFFSET   # 129: in-game Elevation = z - 129 (one unit, one source)

# Friendly names -> DFHack building_type ids, cross-checked against RFR's def list.
# A name not in RFR defs is rejected before any Lua is composed.
_BUILD_ALIASES = {
    "depot": "Trade Depot", "trade depot": "Trade Depot",
    "stockpile": "Stockpile", "chest": "Box", "coffin": "Coffin",
    "bed": "Bed", "table": "Table", "chair": "Chair", "door": "Door",
    "floodgate": "Floodgate", "hatch": "Hatch", "grate": "Wall Grate",
    "well": "Well", "farm plot": "Farm Plot", "plot": "Farm Plot",
    "workshop": "Workshop", "mechanic": "Mechanic's Workshop",
    "carpenter": "Carpenter's Workshop", "stoneworker": "Mason's Workshop",
    "mason": "Mason's Workshop", "jeweler": "Jeweler's Workshop",
    "metalcrafter": "Craftsdwarf's Workshop", "forge": "Metalsmith's Forge",
    "smelter": "Smelter", "kiln": "Kiln", "still": "Still", "loom": "Loom",
    "quern": "Quern", "kitchen": "Kitchen", "butcher": "Butcher's Shop",
    "fishery": "Fishery", "tanner": "Tanner's Shop", "weaver": "Loom",
    "weapon rack": "Weapon Rack", "armor stand": "Armor Stand",
    "statue": "Statue", "slab": "Slab", "seat": "Chair", "cabinet": "Cabinet",
    "track stop": "TrackStop", "trackstop": "TrackStop", "pressure plate": "PressurePlate",
    "lever": "Lever", "trap": "Trap", "cage": "Cage", "chain": "Chain",
    "support": "Support", "water wheel": "Water Wheel", "windmill": "Windmill",
    "axle": "Vertical Axle", "gear assembly": "Gear Assembly",
    "screw pump": "Screw Pump", "bridge": "Bridge", "zone": "Zone",
}


class LuaOpError(Exception):
    pass


async def _lua(expr: str, settle: float = 0.5) -> str:
    """Run one Lua chunk through DFHack's `lua` command and return its printed text."""
    drain_replies()                                  # drop stragglers from earlier calls
    try:
        await RunCommand(input=CoreRunCommandRequest(command="lua", arguments=[expr]))
    except (DFHackError, DFHackTransportError) as e:
        out = "\n".join(drain_replies())
        raise LuaOpError(f"{e}" + (f" | {out}" if out else ""))
    await asyncio.sleep(settle)                       # let RPC_REPLY_TEXT frames arrive
    out = "\n".join(drain_replies()).strip()
    if not out:
        raise LuaOpError("lua produced no output; the chunk printed nothing")
    return out


def _one(out: str) -> str:
    """Return OUR status line.

    DFHack appends a Lua stack traceback after the printed text whenever the chunk
    raised, so the *last* line is a traceback frame - parsing it made a failed
    constructBuilding look like a success with game_type "?" (observed live). The
    status line is found by prefix, and only then do we fall back to the last line.
    """
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    for l in lines:
        if l.startswith("OK ") or l.startswith("FAIL ") or l in ("OK", "FAIL"):
            return l
    return lines[-1] if lines else out


def resolve_building(dec, name: str):
    """name -> (building_type, subtype, custom) using RFR's own definition list."""
    key = _BUILD_ALIASES.get(str(name).strip().lower(), str(name).strip())
    target = key.lower()
    for (bt, sub, cus), nm in dec.building.items():
        if str(nm).strip().lower() == target:
            return bt, sub, cus, nm
    # fall back to the (bt, subtype) fallback maps, which is how real names were kept
    for (bt, sub), nm in dec.building_bts.items():
        if str(nm).strip().lower() == target:
            return bt, sub, -1, nm
    for bt, nm in dec.building_bt.items():
        if str(nm).strip().lower() == target:
            return bt, -1, -1, nm
    raise LuaOpError(f"no building definition named '{key}'; "
                     f"get one from get_building_def_list")


async def place_building(dec, name: str, x: int, y: int, elev: int,
                         w: int | None = None, h: int | None = None,
                         complete: bool = True, abstract: bool | None = None,
                         items: list[int] | None = None, material: str | None = None,
                         count: int = 3) -> dict:
    """Create a building at an exact tile through dfhack.buildings, then verify it."""
    bt, sub, cus, resolved = resolve_building(dec, name)
    z = int(elev) + GAME_Z_OFFSET
    dims = ""
    if w:
        dims += f"w={int(w)}, "
    if h:
        dims += f"h={int(h)}, "
    # Verified against hack/lua/dfhack/buildings.lua:493: constructBuilding refuses to
    # build without one of items / filters / abstract. Stockpiles and zones are ABSTRACT
    # (no construction materials); furniture and doors need real item ids in `items`.
    if abstract is None:
        abstract = resolved.lower() in ("stockpile", "zone")
    extra = ""
    if material:
        kind = str(material).lower().strip()
        extra = f'items=resolve_material("{kind}", {int(count)}, {int(x)}, {int(y)}, {int(elev)}), '
    elif abstract:
        extra = "abstract=true, "
    elif items:
        # Measured: passing raw ids fails in constructWithItems with
        # "Cannot write field vector<item*>.0: incompatible pointer type" - the list
        # must hold item objects, so resolve each id with dfhack.items.findItem.
        ids = ", ".join(str(int(i)) for i in items)
        extra = f"items=resolve_items({{{ids}}}), "
    expr = f'''
local function resolve_items(ids)
  local out = {{}}
  for _, id in ipairs(ids) do
    local ok, it = pcall(function() return dfhack.items.findItem(id) end)
    if ok and it then out[#out + 1] = it end
  end
  return out
end
local function resolve_material(kind, want, nearx, neary, nearz)
  -- Measured: RFR Item.id does NOT resolve via dfhack.items.findItem, and DF53 map-block
  -- items enumerate as numbers. So material items are found by scanning the in-play item
  -- list for item OBJECTS whose description matches, which is what constructWithItems
  -- needs (an empty list is exactly what its argument check rejects).
  --
  -- Early-game rule from the player: SELECT THE CLOSEST MATERIAL. A builder walks to the
  -- item, so distance is the real cost - candidates are collected first, then sorted by
  -- squared 3D distance to the build site, and the nearest `want` are taken.
  local cand = {{}}
  local all = df.global.world.items.other.IN_PLAY
  for _, it in ipairs(all) do
    local okd, d = pcall(function() return dfhack.items.getDescription(it, 0) end)
    if okd and d and tostring(d):lower():find(kind, 1, true) then
      local okp, pos = pcall(function() return it.pos end)
      local dx, dy, dz = 9999, 9999, 9999
      if okp and pos then dx, dy, dz = pos.x - nearx, pos.y - neary, (pos.z - 129) - nearz end
      cand[#cand + 1] = {{it = it, d2 = dx*dx + dy*dy + dz*dz}}
    end
  end
  table.sort(cand, function(a, b) return a.d2 < b.d2 end)
  local out = {{}}
  for i = 1, math.min(want, #cand) do out[#out + 1] = cand[i].it end
  return out
end
local b = nil
local ok, res = pcall(function()
  return dfhack.buildings.constructBuilding{{type={bt}, subtype={sub}, custom={cus},
    x={int(x)}, y={int(y)}, z={z}, {dims}{extra}nominal=true}}
end)
if not (ok and res) then print("FAIL construct: "..tostring(res)) return end
b = res
if {str(bool(complete)).lower()} then
  local okc = pcall(function() dfhack.buildings.completeBuild(b) end)
  if not okc then print("FAIL completeBuild") return end
end
local at = nil
local oka, r = pcall(function() return dfhack.buildings.findAtTile({int(x)}, {int(y)}, {z}) end)
if oka then at = r end
if at == nil then print("FAIL placed but not found at tile") return end
local tn = tostring(at._type)
local dot = tn:match("<type: ([_%w]+)")
print("OK building={resolved!r} bt={bt} type="..(dot or tn).." id="..tostring(at.id))
'''.strip()
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    m = re.search(r"type=(\S+) id=(\S+)", line)
    return {"placed": resolved, "at": [int(x), int(y), elev], "complete": complete,
            "game_type": m.group(1) if m else "?", "building_id": int(m.group(2)) if m and m.group(2).isdigit() else None,
            "verified": "findAtTile"}


async def building_at(x: int, y: int, elev: int) -> dict:
    z = int(elev) + GAME_Z_OFFSET
    out = await _lua(f'''
local ok,b = pcall(function() return dfhack.buildings.findAtTile({int(x)}, {int(y)}, {z}) end)
if not ok then print("FAIL "..tostring(b)) return end
if b == nil then print("OK none") return end
local tn = tostring(b._type)
print("OK type="..(tn:match("<type: ([_%w]+)") or tn).." id="..tostring(b.id))
'''.strip())
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    if line == "OK none":
        return {"building": None}
    m = re.search(r"type=(\S+) id=(\S+)", line)
    return {"building": m.group(1) if m else "?", "building_id": int(m.group(2)) if m and m.group(2).isdigit() else None}


async def remove_building(x: int, y: int, elev: int) -> dict:
    z = int(elev) + GAME_Z_OFFSET
    out = await _lua(f'''
local ok,b = pcall(function() return dfhack.buildings.findAtTile({int(x)}, {int(y)}, {z}) end)
if not ok or b == nil then print("FAIL no building at tile") return end
local okd = pcall(function() dfhack.buildings.deconstruct(b) end)
if okd then print("OK removed id="..tostring(b.id)) else print("FAIL deconstruct") end
'''.strip())
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"removed": True, "at": [int(x), int(y), elev]}


async def walkable_between(x0: int, y0: int, z0_elev: int, x1: int, y1: int, z1_elev: int) -> dict:
    """dfhack.maps.canWalkBetween - the wagon/path question, answered by the game."""
    za, zb = int(z0_elev) + GAME_Z_OFFSET, int(z1_elev) + GAME_Z_OFFSET
    out = await _lua(f'''
local a = {{x={int(x0)}, y={int(y0)}, z={za}}}
local b = {{x={int(x1)}, y={int(y1)}, z={zb}}}
local ok,r = pcall(function() return dfhack.maps.canWalkBetween(a,b) end)
if not ok then print("FAIL "..tostring(r)) return end
print("OK walkable="..tostring(r))
'''.strip())
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"walkable": "walkable=true" in line, "raw": line}


async def list_jobs(limit: int = 40) -> dict:
    """The manager/jobs queue, which RemoteFortressReader does not expose at all."""
    out = await _lua(f'''
local n=0 local lines={{}}
for _,j in ipairs(df.global.world.jobs.list) do
  if n >= {int(limit)} then break end
  n = n + 1
  local desc = "?"
  local okd, d = pcall(function() return dfhack.job.getName(j) end)
  if okd and d then desc = tostring(d) end
  local un = "no unit"
  if j.unit ~= nil and j.unit.id ~= nil then un = "unit #" .. tostring(j.unit.id) end
  lines[#lines+1] = desc .. " by " .. un .. " jtype=" .. tostring(j.job_type)
end
local total = 0
for _ in ipairs(df.global.world.jobs.list) do total = total + 1 end
print("OK jobs="..tostring(total))
for _,l in ipairs(lines) do print("  " .. l) end
'''.strip(), settle=0.8)
    lines = out.splitlines()
    head = lines[0] if lines else ""
    total = int(head.split("=")[1]) if "=" in head else 0
    return {"total_jobs": total, "sample": [l.strip() for l in lines[1:limit + 1]]}


async def unit_counts() -> dict:
    out = await _lua('''
local dwarves=0
local citizens=0
local pets=0
local merchants=0
local living=0
for _,u in ipairs(df.global.world.units.all) do
  local okl, alive = pcall(function() return dfhack.units.isAlive(u) end)
  if okl and alive then
    living = living + 1
    local okd, isd = pcall(function() return dfhack.units.isDwarf(u) end)
    if okd and isd then dwarves = dwarves + 1 end
    local okc, isc = pcall(function() return dfhack.units.isCitizen(u) end)
    if okc and isc then citizens = citizens + 1 end
    local okp, isp = pcall(function() return dfhack.units.isPet(u) end)
    if okp and isp then pets = pets + 1 end
    local okm, ism = pcall(function() return dfhack.units.isMerchant(u) end)
    if okm and ism then merchants = merchants + 1 end
  end
end
print("OK living="..living.." dwarves="..dwarves.." citizens="..citizens.." pets="..pets.." merchants="..merchants)
'''.strip(), settle=0.8)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    vals = dict(re.findall(r"(\w+)=(\d+)", line))
    return {k: int(v) for k, v in vals.items()}

# The 17 flags the game's stockpile_group_set actually exposes (measured: writing
# `settings.flags.misc` throws - "Cannot write field stockpile_group_set.misc: not
# found" - and aborts the loop, so misc is a mats struct, not a category flag).
CATEGORIES = ["ammo", "animals", "armor", "bars_blocks", "cloth", "coins", "corpses",
              "finished_goods", "food", "furniture", "gems", "leather", "refuse",
              "sheet", "stone", "weapons", "wood"]
MATS_ONLY = ["misc"]


async def configure_stockpile(dec, building_id: int, allow: list[str] | None = None,
                              deny: list[str] | None = None, all_categories: bool = False,
                              max_wheelbarrows: int | None = None,
                              max_barrels: int | None = None,
                              max_bins: int | None = None) -> dict:
    """Set a stockpile's accepted categories by writing the game's own flags.

    Measured on this build (53.17-r1): building_stockpilest exposes `settings.flags`
    (stockpile_group_set) with exactly the wiki's category names, and the per-category
    structs carry only `mats` - so the flags ARE the preset. All flags true = takes
    everything; one flag = that preset (a wood pile = wood only); all false = "None".
    """
    cats = [str(c).lower().strip() for c in (allow or [])]
    denied = [str(c).lower().strip() for c in (deny or [])]
    for c in cats + denied:
        if c not in CATEGORIES:
            raise LuaOpError("unknown category '%s'; the game's names are: %s"
                             % (c, ", ".join(CATEGORIES)))
    wanted = CATEGORIES if all_categories else cats
    lines = []
    for c in CATEGORIES:
        on = (c in wanted and c not in denied)
        lines.append("  s.flags.%s = %s" % (c, "true" if on else "false"))
    writes = "\n".join(lines)
    # Measured: the container limits are NOT on the building - they are
    # b.storage.max_barrels / max_bins / max_wheelbarrows, and give/take links are
    # b.links.give_to_pile / take_from_pile / give_to_workshop / take_from_workshop.
    extras = ""
    for field, val in (("max_wheelbarrows", max_wheelbarrows), ("max_barrels", max_barrels),
                       ("max_bins", max_bins)):
        if val is not None:
            extras += ("  local ok_%s, err_%s = pcall(function() b.storage.%s = %d end)\n"
                       % (field, field, field, int(val)))
            extras += ('  if not ok_%s then print("WARN storage.%s: "..tostring(err_%s)) end\n'
                       % (field, field, field))
    catlist = '", "'.join(CATEGORIES)
    expr = """
local b = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  if sp.id == __ID__ then b = sp end
end
if b == nil then print("FAIL stockpile __ID__ not found") return end
local s = b.settings
local before = {}
for _,c in ipairs{"__CATS__"} do before[#before+1] = c .. "=" .. tostring(s.flags[c]) end
__WRITES__
__EXTRAS__local after = {}
for _,c in ipairs{"__CATS__"} do after[#after+1] = c .. "=" .. tostring(s.flags[c]) end
print("OK id=" .. tostring(b.id) .. " | after: " .. table.concat(after, " "))
""".strip().replace("__ID__", str(int(building_id))).replace("__CATS__", catlist)         .replace("__WRITES__", writes).replace("__EXTRAS__", extras)
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line, "allow": wanted, "deny": denied}

async def resize_building(dec, building_id: int, w: int, h: int) -> dict:
    """Resize a building (stockpiles included) through the game's own dfhack.buildings.setSize.

    Measured: constructBuilding with nominal=true makes a 1x1 stockpile even when w/h are
    passed - pile 12 came out x1=x2=44, y1=y2=116 (room 1x1), exactly what the player saw.
    The game's own resize is `dfhack.buildings.setSize(b, w, h)`; extents are read back from
    b.x1..b.x2 / b.y1..b.y2 and the usable tile count from countExtentTiles.
    """
    expr = """
local b = nil
for _,lst in ipairs({df.global.world.buildings.other.STOCKPILE}) do
  for _,sp in ipairs(lst) do if sp.id == __ID__ then b = sp end end
end
if b == nil then print("FAIL building __ID__ not found") return end
local ok, err = pcall(function() dfhack.buildings.setSize(b, __W__, __H__) end)
if not ok then print("FAIL setSize: " .. tostring(err)) return end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(b) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%s", b.id, b.x1, b.x2, b.y1, b.y2, tostring(n)))
""".strip().replace("__ID__", str(int(building_id))).replace("__W__", str(int(w))).replace("__H__", str(int(h)))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line}

async def create_stockpile(dec, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create a MULTI-TILE stockpile exactly the way hack/lua/dfhack/buildings.lua does:
    allocInstance -> setSize (legal only on the uncommitted instance) -> constructAbstract.

    Measured on 53.17-r1: constructBuilding{..., w, h, nominal=true} yields a 1x1 stockpile,
    and setSize on a committed building answers "expected: bld->id == -1". The library's own
    order (buildings.lua:506-545) is allocInstance(pos, type, subtype, custom),
    setSize(instance, width, height, direction), then constructAbstract/WithItems/WithFilters.
    """
    z = int(elev) + GAME_Z_OFFSET
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local inst = dfhack.buildings.allocInstance(pos, 21, 0, 0)
if inst == nil then print("FAIL allocInstance") return end
local ok, w2, h2, area, r_area = dfhack.buildings.setSize(inst, __W__, __H__, 8)
if not ok then print("FAIL setSize: cannot place at this position") return end
local oka = dfhack.buildings.constructAbstract(inst)
if not oka then print("FAIL constructAbstract") return end
local at = nil
pcall(function() at = dfhack.buildings.findAtTile(__X__, __Y__, __Z__) end)
if at == nil then print("FAIL built but not found at tile") return end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(at) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%d", at.id, at.x1, at.x2, at.y1, at.y2, n))
"""
    expr = lua.strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z), ("__W__", int(w)), ("__H__", int(h))):
        expr = expr.replace(k, str(v))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line}
