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
import hashlib
import json
import pathlib
import sys
import uuid
import re

from dfhack_client_python.dfhack import RunCommand
from dfhack_client_python.dfhack_remote import (
    DFHackError, DFHackTransportError, connection_generation, drain_replies
)
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


async def _lua(expr: str, settle: float = 0.0) -> str:
    """Run one Lua chunk through DFHack's `lua` command and return its printed text.

    DFHack streams every RPC_REPLY_TEXT frame before RPC_REPLY_RESULT on the same
    serialized RPC call. dfhack_remote._invoke collects those frames per call into a
    task-local ContextVar while holding _io_lock, so draining immediately after
    RunCommand returns this call's exact output with zero sleep and no cross-talk
    from concurrent polls (/observe, list_zones, list_stockpiles).
    """
    drain_replies()                                  # clear any prior task-local state
    try:
        await RunCommand(input=CoreRunCommandRequest(command="lua", arguments=[expr]))
    except (DFHackError, DFHackTransportError) as e:
        out = "\n".join(getattr(e, "replies", None) or drain_replies())
        raise LuaOpError(f"{e}" + (f" | {out}" if out else ""))
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
    prefixes = ("OK ", "FAIL ", "OK|", "ERR|", "ROW|", "DONE|", "VERDICT|", "REFUSED ")
    for l in lines:
        if l.startswith(prefixes) or l in ("OK", "FAIL"):
            return l
    # The game's own per-tick banner lands in the same reply stream (measured
    # 2026-11-21: "OBS2 PICKS 3 SITE 677 DATE 100-0-22 SEEDS 30 DRINK 12 MEALS 0 RAW 0"),
    # so a bare "last line" read can return the banner and a caller reports
    # "unparsable create result" for a build that SUCCEEDED. Skip banner-shaped lines.
    tail = [l for l in lines if "PICKS" not in l and "SITE" not in l and not l.startswith("OBS")]
    return tail[-1] if tail else out


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
    # Search the WHOLE reply: the chunk prints one line, but the RPC stream can put a banner
    # in front of it. Reading only the first line silently produced
    # {"game_type": "?", "building_id": null} for a farm plot that WAS placed (measured
    # 2026-11-21) - and a null id means /resize-building cannot be called, so the sanctioned
    # 1x1 -> setSize path for multi-tile workshops dies at the reporting step.
    m = re.search(r"type=(\S+) id=(\S+)", out)
    return {"placed": resolved, "at": [int(x), int(y), elev], "complete": complete,
            "game_type": m.group(1) if m else "?",
            "building_id": int(m.group(2)) if m and m.group(2).isdigit() else None,
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
    """Mark a building for deconstruction and REPORT WHAT THE GAME DID.

    Two measured lies in the old version, both on this fort (2026-11-21):
      1. it printed "OK removed" straight after the deconstruct CALL. deconstruct MARKS
         the building; a dwarf has to take it apart. The wagon call reported removed:true
         while the wagon stood there and wood was still 0.
      2. it answered as a failure ("lua produced no output") on the 3x4 still AFTER the
         building existed - a success reported as an error, mid crash-hunt.
    So this reads the tile back and answers in the game's own terms.
    """
    z = int(elev) + GAME_Z_OFFSET
    lua = (
        "local ok,b = pcall(function() return dfhack.buildings.findAtTile("
        + str(int(x)) + ", " + str(int(y)) + ", " + str(z) + ") end)\n"
        'if not ok then print("ERR|findAtTile: "..tostring(b)) return end\n'
        'if b == nil then print("VERDICT|absent|-1|-") return end\n'
        "local id = b.id\n"
        "local okd = pcall(function() dfhack.buildings.deconstruct(b) end)\n"
        'if not okd then print("ERR|deconstruct refused on id "..tostring(id)) return end\n'
        "-- building_flags has NO marked_for_removal on this build (measured); the removal bit\n"
        "-- is marked_for_deconstruction. Probe the names, never assume one.\n"
        'local flagname = "-"\n'
        "local marked = false\n"
        'for _, nm in ipairs({"marked_for_deconstruction", "marked_for_removal"}) do\n'
        "  local okf, v = pcall(function() return b.flags[nm] end)\n'".replace("\n'", "\n") +
        "  if okf and v ~= nil then flagname = nm; marked = (v == true); break end\n"
        "end\n"
        "local after = nil\n"
        "pcall(function() after = dfhack.buildings.findAtTile("
        + str(int(x)) + ", " + str(int(y)) + ", " + str(z) + ") end)\n"
        ''
        "print(string.format(\"VERDICT|%s|%d|%s|%s\",\n"
        '  (after == nil) and "gone" or (marked and "marked" or "unchanged"), id, flagname, tostring(after ~= nil)))'
    )
    out = await _lua(lua)
    line = _one(out)
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    if not line.startswith("VERDICT|"):
        raise LuaOpError(f"removal came back unparsable: {line!r}")
    parts = line.split("|")[1:]
    verdict, bid, flagname, still = parts[0], parts[1], parts[2], (parts[3] if len(parts) > 3 else "?")
    return {"success": True, "removed": verdict == "gone",
            "marked_for_removal": verdict == "marked", "verdict": verdict,
            "building_id": int(bid), "flag_read": flagname, "tile_occupied_after": still == "true",
            "at": [int(x), int(y), elev],
            "note": {"gone": "nothing is on the tile any more",
                     "marked": "the game MARKED it - a dwarf must take it apart; materials drop then",
                     "unchanged": "deconstruct ran and the flag read says nothing changed"}.get(verdict, verdict)}


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
# These are the 17 flag fields the game EXPOSES on a stockpile - enumerated live from
# `pairs(sp.settings.flags)` on 2026-11-21 (pile 4 of fort start): animals, ammo, armor,
# bars_blocks, cloth, coins, corpses, finished_goods, food, furniture, gems, leather,
# refuse, sheet, stone, weapons, wood. There is NO `plants` field on this build, so adding
# "plants" to make room for plump helmets is a name the writer cannot set (measured:
# `sp.settings.flags.plants = true` fails, `flags.wood` reads NOFIELD under that spelling
# while the enumeration shows wood - the flag names must come from the game, never from a
# wiki category list). Plump helmets and their spawn are stored under `food`.
CATEGORIES = ["ammo", "animals", "armor", "bars_blocks", "cloth", "coins", "corpses",
              "finished_goods", "food", "furniture", "gems", "leather", "refuse",
              "sheet", "stone", "weapons", "wood"]
MATS_ONLY = ["misc"]


async def configure_stockpile(dec, building_id: int, allow: list[str] | None = None,
                              deny: list[str] | None = None, all_categories: bool = False,
                              max_wheelbarrows: int | None = None,
                              max_barrels: int | None = None,
                              max_bins: int | None = None,
                              allow_crash_categories: bool = False) -> dict:
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
    # MEASURED TWICE, SAME CLASS: enabling `corpses`/`refuse` through flags is a crash.
    # 2026-11-22 20:02 (the M8 fort's outdoor refuse pile) and 2026-11-22 late (this save:
    # piles 1 general and 2 wood survived the Custom screen, pile 3 corpses+refuse crashed it).
    # Signature: SIGSEGV (SEGV_MAPERR), stack `render_things` -> `overlay.plug.so`, no Lua.
    # Those categories' arrays are indexed by race / item-type, and their sizes are 0 on the
    # reference pile - the UI allocates them when ITS screen opens the page. A programmatic
    # flag (size 0, or 1 from the fallback) leaves the screen walking an array the game never
    # filled. So they are refused here; the player's own screen is the safe path, and a measured
    # reference size is the only thing that unlocks the write.
    CRASH_CATEGORIES = ("corpses", "refuse")
    hit = [c for c in wanted if c in CRASH_CATEGORIES
           and not any(int(v) > 0 for v in SETTINGS_ARRAYS.get(c, {}).values())]
    if hit and not allow_crash_categories:
        raise LuaOpError(
            "categories %s are refused: opening Custom on a pile with these flags set "
            "programmatically crashed this fort twice (crash_2026-11-22-20-02-29.txt and its "
            "repeat on pile 3). Set them in-game (p -> pile -> Custom -> corpses/refuse), where "
            "the game allocates the arrays its screen indexes, or pass "
            "allow_crash_categories=true once /pile-arrays reports measured non-zero reference "
            "sizes for them." % ", ".join(hit))
    lines = []
    for c in CATEGORIES:
        on = (c in wanted and c not in denied)
        lines.append("  s.flags.%s = %s" % (c, "true" if on else "false"))
    writes = "\n".join(lines)
    # Flags alone are NOT a stockpile: the game's custom-settings UI walks per-category
    # material arrays that the UI allocates when a category is switched on. Setting a flag on
    # top of a size-0 vector crashes the game when the player clicks Custom (measured:
    # SIGSEGV at 0xa in render_things with overlay on the stack). So every category we switch
    # on is initialised here, in the same pass - the repair tool is a rescue for piles built
    # before this existed, never part of normal use.
    alloc_lines = []
    for c in CATEGORIES:
        on = (c in wanted and c not in denied)
        sizes = SETTINGS_ARRAYS.get(c, {})
        if not on or not sizes:
            continue
        for field, n in sizes.items():
            if n <= 0:
                continue
            alloc_lines.append(f"  if s.{c}.{field} ~= nil and #s.{c}.{field} < {n} then")
            alloc_lines.append(f"    s.{c}.{field}:resize({n})")
            alloc_lines.append(f"    for i = 0, {n - 1} do pcall(function() s.{c}.{field}[i] = 1 end) end")
            alloc_lines.append("  end")
    writes = writes + "\n" + "\n".join(alloc_lines)
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
local arrays = {}
for _,c in ipairs{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"} do
  if s.flags[c] == true then
    local cat = b.settings[c]
    if cat ~= nil then
      local parts = {}
      for k,v in pairs(cat) do
        local n = -1
        local ok = pcall(function() n = #v end)
        if ok and n >= 0 and tostring(v):find("vector<char>") then parts[#parts+1] = tostring(k) .. "=" .. n end
      end
      table.sort(parts)
      if #parts > 0 then arrays[#arrays+1] = c .. "{" .. table.concat(parts, ",") .. "}" end
    end
  end
end
print("OK id=" .. tostring(b.id) .. " | after: " .. table.concat(after, " ") .. " | arrays: " .. table.concat(arrays, " "))
""".strip().replace("__ID__", str(int(building_id))).replace("__CATS__", catlist)         .replace("__WRITES__", writes).replace("__EXTRAS__", extras)
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line, "allow": wanted, "deny": denied}

async def resize_building(dec, building_id: int, w: int, h: int) -> dict:
    """setSize (w, h) - and it is ONLY legal on an ABSTRACT building.

    Measured on 2026-11-21 with a real fort:
      setSize(pile 4, 11, 11) ->
        "In bool DFHack::Buildings::setSize(df::building*, df::coord2d, int):
         Invalid argument; expected: bld->id == -1"
    A committed building has a real id, so it can never be resized through this call:
    a pile is sized by create_stockpile (which setSize's the ABSTRACT before
    constructAbstract commits it), and a built building keeps its footprint.
    The old version also searched only the STOCKPILE list, so it reported
    "building 19 not found" for a farm plot that exists - the lookup is now by id
    through the game's own findBuilding.
    """
    expr = """
local b = nil
pcall(function() b = dfhack.buildings.findBuilding(__ID__) end)
if b == nil then print("FAIL building __ID__ not found (findBuilding)") return end
local is_abstract = false
pcall(function() is_abstract = (b.id == -1) end)
local ok, err = pcall(function() dfhack.buildings.setSize(b, __W__, __H__) end)
if not ok then
  print(string.format("REFUSED id=%d abstract=%s built=%s extent=%d..%d,%d..%d : %s",
    b.id, tostring(is_abstract), tostring(b.id ~= -1), b.x1, b.x2, b.y1, b.y2, tostring(err)))
  return
end
local n = 0
pcall(function() n = dfhack.buildings.countExtentTiles(b) end)
print(string.format("OK id=%d extent=%d..%d,%d..%d tiles=%s", b.id, b.x1, b.x2, b.y1, b.y2, tostring(n)))
""".strip().replace("__ID__", str(int(building_id))).replace("__W__", str(int(w))).replace("__H__", str(int(h)))
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line[5:])
    if line.startswith("REFUSED"):
        return {"success": False,
                "error": "setSize refuses this building: " + line[9:].strip(),
                "rule": "dfhack.buildings.setSize works on an ABSTRACT building only (bld->id == -1). "
                        "A committed building keeps its footprint: size piles at creation via "
                        "/create-stockpile (allocInstance -> setSize -> constructAbstract)."}
    return {"success": True, "line": line}


_ZONE_HINTS = {
    "meeting": ("meeting",),
    "pasture": ("pasture",),
    "pen": ("pen",),
    "pit": ("pit",),
    "pond": ("pond",),
    "dump": ("dump", "garbage"),
    "refuse": ("refuse", "dump"),
    "watersource": ("water source", "watersource"),
    "granite": ("granite",),
    "trail": ("trail",),
    "gather": ("gather", "gathering"),
}


def resolve_zone_name(dec, kind: str) -> str:
    """kind -> the game's real zone definition NAME, from its own table.

    RFR gives every activity zone its own building type, so the name an agent has to
    pass is the game's string ("Zone Meeting Hall" style), not the wiki's "Meeting
    Area" - a request for "Meeting Area" resolves to nothing here.
    """
    k = str(kind).strip().lower()
    hints = _ZONE_HINTS.get(k, (k,))
    names = sorted({str(nm) for (bt, sub, cus), nm in dec.building.items()
                    if any(h in str(nm).lower() for h in hints)})
    if not names:
        raise LuaOpError(f"no zone definition matches '{kind}' (tried: {', '.join(hints)}); "
                         f"list the buildable names with get_building_def_list?query=zone")
    # A real zone definition, not a workshop that happens to mention the word.
    zones = [n for n in names if "zone" in n.lower()]
    return (zones or names)[0]


_CIVZONE_TYPES = {
    "meeting": "MeetingHall", "bedroom": "Bedroom", "dining": "DiningHall",
    "pen": "Pen", "pasture": "Pen", "pit": "Pond", "pond": "Pond",
    "watersource": "WaterSource", "water": "WaterSource", "dump": "Dump",
    "garbage": "Dump", "refuse": "Dump", "office": "Office", "barracks": "Barracks",
    "dormitory": "Dormitory", "fishing": "FishingArea", "sand": "SandCollection",
    "archery": "ArcheryRange", "tomb": "Tomb", "training": "AnimalTraining",
    "dungeon": "Dungeon",
}


def _usable(tiles: int, area: int) -> tuple[int | None, str]:
    """A tile count the game can return for a legal building.

    countExtentTiles returned 0 for a 130-tile stockpile that the game accepted, the
    categories applied and the ladder rung satisfied. `usable_tiles: 0, holes: 130` is not
    a measurement - it is the probe failing. Report it as unmeasured so nobody plans a fort
    around a phantom hole count.
    """
    if area > 0 and tiles == 0:
        return None, "countExtentTiles returned 0 for a non-empty footprint: usable tiles UNMEASURED (not 0)"
    return tiles, "countExtentTiles"


async def _lua_read(expr: str, attempts: int = 3) -> str:
    """Run a READ-ONLY chunk, retrying transient empty answers.

    READ-ONLY by contract: a write chunk must never be replayed (a doubled build is worse
    than a missing row), so only pure readers call this.
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return await _lua(expr)
        except (LuaOpError, DFHackError, DFHackTransportError) as e:
            last = e
            if i + 1 < max(1, attempts):
                await asyncio.sleep(0.05 * (i + 1))
    assert last is not None
    raise last


class NoData(LuaOpError):
    """The read ran and the game printed NO rows.

    This is NOT "the fort has none of these". Measured 2026-11-21: /stockpiles and /zones
    answered 0 for a fort holding a 10x13 general pile and a 25-tile meeting zone - the RPC
    hiccup swallowed the chunk's print, callers saw `count: 0`, and an agent trusting that
    would dig a second general pile next to the first.
    """


def _rows(out: str) -> list[str]:
    """Scan rows only. An EMPTY fort must read as empty: a scan that prints nothing is
    a legitimate answer (no zones / no piles), not a failure - and `_lua` raises
    "lua produced no output" on it, which made /zones and /stockpiles report errors on a
    fresh embark where the honest answer is count: 0."""
    return [l.strip() for l in out.splitlines() if l.strip()]


async def list_zones(dec) -> list[dict]:
    """Every activity zone with its KIND - the field the ladder's rungs need.

    The building census calls every zone "Zone" (type 30), so a meeting area and an empty
    marked rectangle are indistinguishable there; the kind is `z.type`, an index into
    df.civzone_type. Reading that enum with pairs() yields NO numeric entries on this
    build (measured: type 87 came back name UNKNOWN), so names are resolved by their known
    identifiers, each behind pcall. building_flags has no marked_for_removal here either -
    reading it throws inside the row, and the row silently vanishes (measured: "count: 0"
    with a zone standing).
    """
    kinds = ["Aquifer", "Dump", "FishingArea", "Forest", "GatheringArea", "MeetingHall",
             "Pen", "Pond", "SandCollection", "Tomb", "WaterSource", "Office", "Bedroom",
             "DiningHall", "Barracks", "Dormitory", "ArcheryRange", "AnimalTraining",
             "Dungeon"]
    names_lua = ",".join('"%s"' % k for k in kinds)
    lua = ("local NAMES = {" + names_lua + "}\n"
           "local names = {}\n"
           "for _, nm in ipairs(NAMES) do\n"
           "  local ok, v = pcall(function() return df.civzone_type[nm] end)\n"
           "  if ok and v ~= nil then names[tonumber(v)] = nm end\n"
           "end\n"
           "for _,zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do\n"
           "  local rm = 0\n"
           "  pcall(function() rm = (zz.flags.marked_for_removal and 1) or 0 end)\n"
           '  local tname = "UNKNOWN"\n'
           "  pcall(function() if names[zz.type] ~= nil then tname = names[zz.type] end end)\n"
           '  print(string.format("ROW|%d|%d|%s|%d|%d|%d|%d|%d|%d", zz.id, zz.type,\n'
           '    tostring(tname), zz.x1, zz.x2, zz.y1, zz.y2, zz.z, rm))\n'
           "end\n"
           'print("DONE|" .. tostring(#df.global.world.buildings.other.ANY_ZONE))')
    try:
        out = await _lua_read(lua)
    except LuaOpError as e:
        if 'no output' in str(e):
            raise NoData("the zone scan printed no rows: NO DATA (not a zone-free fort)")
        raise
    rows = []
    for line in _rows(out):
        if not line.startswith("ROW|"):
            continue
        parts = line.split("|")
        if len(parts) < 10:
            continue
        _, bid, tval, tname, x1, x2, y1, y2, z, flag = parts
        # The RPC reply stream cross-talks: a PILE row (whose third field is an extent
        # string like "66..67,112..113") arrived inside the ZONE scan and the int() calls
        # blew up (measured 2026-11-21: "zones unavailable: invalid literal for int() with
        # base 10: '66..67,112..113'"). A zone row has ten numeric-shaped fields; anything
        # else is a foreign row - skip it and say so, never crash the read.
        if not all(tok.lstrip("-").isdigit() for tok in (bid, tval, x1, x2, y1, y2, z)):
            print(f"[list_zones] foreign row skipped: {line[:70]!r}", file=sys.stderr)
            continue
        rows.append({"id": int(bid), "type": int(tval), "type_name": tname,
                     "x0": int(x1), "x1": int(x2), "y0": int(y1), "y1": int(y2),
                     "elev": int(z) - GAME_Z_OFFSET,
                     "tiles": (int(x2) - int(x1) + 1) * (int(y2) - int(y1) + 1),
                     "marked_for_removal": flag == "1"})
    return rows


async def list_stockpiles(dec) -> list[dict]:
    """Every stockpile with its BUILDING ID - what configure_stockpile needs.

    Nothing else in the tool surface gives a pile its id: `building-at` answered null on
    the pile's own anchor tile, `get-block-list` carries no buildings, and `world-objects`
    prints name+dims only. Per-field pcall is mandatory: `pairs(settings.flags)` THROWS on
    this build, container limits live on `sp.storage`, and `building_flags` has no
    `marked_for_removal`.
    """
    lua = """
local CATS = {"ammo","animals","armor","bars_blocks","cloth","coins","corpses",
              "finished_goods","food","furniture","gems","leather","misc","refuse",
              "sheet","stone","weapons","wood"}
-- The in-game panel numbers piles "Pile 1, Pile 2, ..." and that number is NOT the
-- building id (measured 2026-11-21: the player's "pile #3 next to the still" vs my ids
-- 1/4/21/22/23 - we were naming two different objects). ui.main.stockpile.items IS the
-- panel order, so report both.
local panel = {}
pcall(function()
  for i2, entry in ipairs(df.global.world.ui.main.stockpile.items) do
    if entry.build then panel[entry.build.id] = i2 end
  end
end)
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  local ok, line = pcall(function()
    local on = {}
    local okn = 0
    for _, c in ipairs(CATS) do
      local okf, v = pcall(function() return sp.settings.flags[c] end)
      if okf then okn = okn + 1 end
      if okf and v == true then on[#on + 1] = c end
    end
    -- An EMPTY accepts list is two different facts: the pile takes nothing (a rectangle of
    -- paint, worth alerting) and the flag reads FAILED (NO DATA). Measured 2026-11-21: a
    -- pile configured to `food` read as [] three times in a row and the plugin alerted
    -- "pile accepts NOTHING", while a raw probe on the same pile read food=true.
    local acc = (okn == 0) and "UNREAD" or table.concat(on, "+")
    local rm = 0
    pcall(function() rm = (sp.flags.marked_for_removal and 1) or 0 end)
    local bins, wbar, bars = -1, -1, -1
    pcall(function() bins = sp.storage.max_bins end)
    pcall(function() wbar = sp.storage.max_wheelbarrows end)
    pcall(function() bars = sp.storage.max_barrels end)
    return string.format("ROW|%d|%d..%d,%d..%d|%d|%d|%s|%d|%d|%d|%s",
      sp.id, sp.x1, sp.x2, sp.y1, sp.y2, sp.z, rm, acc,
      bins, wbar, bars, tostring(panel[sp.id] or "-"))
  end)
  if ok and line then print(line) end
end
print("DONE|" .. tostring(#df.global.world.buildings.other.STOCKPILE))
""".strip()
    try:
        out = await _lua_read(lua)
    except LuaOpError as e:
        if 'no output' in str(e):
            raise NoData("the pile scan printed no rows: NO DATA (not a pile-free fort)")
        raise
    rows = []
    notes: list[str] = []
    for line in _rows(out):
        if not line.startswith("ROW|"):
            continue
        parts = line.split("|")
        # The pile row is ROW + 9 fields (the extent is ONE field: "72..75,122..124").
        # This guard was written as "< 10" - copied from the zone row, which really has 10 -
        # and silently dropped EVERY pile: /stockpiles answered count 0 for a fort with three
        # piles standing, and the plugin's pile tests could never flip. A row count that
        # mismatches must be reported, not skipped.
        if len(parts) < 10:
            notes.append(f"unparsable pile row skipped: {line[:70]!r}")
            continue
        _, bid, extent, z, rm, on, bins, wbar, barrels, pan = parts[:10]
        # "UNREAD" is the row's own marker: the flag lookups failed for THIS pile, so the
        # category list is unknown - it is not an empty list, and an alert that says the
        # pile accepts nothing on the strength of it is a false alarm.
        unread = (on == "UNREAD")
        bits = extent.split(",")
        if len(bits) != 2:
            notes.append(f"pile {bid}: unparsable extent {extent!r} (row skipped, not guessed)")
            continue
        xs, ys = bits
        x_lo, x_hi = xs.split("..")
        y_lo, y_hi = ys.split("..")
        rows.append({"id": int(bid), "extent": extent,
                     "x0": int(x_lo), "x1": int(x_hi), "y0": int(y_lo), "y1": int(y_hi),
                     "elev": int(z) - GAME_Z_OFFSET,
                     "marked_for_removal": rm == "1",
                     "accepts": (None if unread else (sorted(on.split("+")) if on else [])),
                     "categories_measured": (not unread),
                     "max_bins": int(bins), "max_wheelbarrows": int(wbar),
                     "max_barrels": int(barrels),
                     "panel_number": (None if pan == "-" else int(pan))})
    if notes:
        print("[list_stockpiles] " + "; ".join(notes), file=sys.stderr)
    return rows


async def create_zone(dec, kind: str, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create an activity zone with the SAME three calls that make a stockpile work.

    The comparison the player asked for ("stock piles and zones should be very similar,
    compare the tools and see where the error is") is exactly the diagnosis:

        create_stockpile  allocInstance -> setSize -> constructAbstract        (12+ piles, no crash)
        create_zone  v1   allocInstance -> setSize -> constructAbstract
                               then  found.type = <kind>                       (FORT CRASHED)
        create_zone  v2   constructBuilding{type=Civzone, subtype=<kind>, ...}  (FORT CRASHED)

    The stockpile path never touches a zone's type after creation, and quickfort's call
    shows the kind belongs in the SUBTYPE at construction. v3 does both: the kind is
    supplied to allocInstance as the subtype, and nothing is written afterwards.
    `constructAbstract` is the same commit the piles use.
    """
    k = str(kind).strip().lower()
    ztype = _CIVZONE_TYPES.get(k)
    if ztype is None:
        raise LuaOpError(f"no zone kind '{kind}'; kinds: {', '.join(sorted(_CIVZONE_TYPES))}")
    z = int(elev) + GAME_Z_OFFSET
    w, h = max(1, int(w)), max(1, int(h))
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local kind = df.civzone_type.__TYPE__
if kind == nil then print("ERR|df.civzone_type.__TYPE__ missing in this build") return end
local before = {}
for _,zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do before[zz.id] = true end
-- THE KIND IS THE SUBTYPE, AT ALLOCATION. v1 allocated -1 and wrote found.type
-- afterwards; that raw write on a zone built outside the game's creation path is the
-- prime suspect for the crash (the game's zone bookkeeping is never told).
local inst = dfhack.buildings.allocInstance(pos, 30, kind, -1)
if inst == nil then print("ERR|allocInstance(zone) failed") return end
if not dfhack.buildings.setSize(inst, __W__, __H__, 8) then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- never leave an orphan
  print("ERR|setSize refused (overlap with buildings/walls/ramps in the footprint?)") return
end
local ok_abs, err_abs = pcall(function() return dfhack.buildings.constructAbstract(inst) end)
local found = nil
for _,zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do
  if before[zz.id] == nil and zz.z == __Z__ then found = zz break end
end
if found == nil then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- never leave an orphan
  print("ERR|zone not live after constructAbstract ("..tostring(ok_abs)..": "..tostring(err_abs)..")") return
end
local tiles = 0
-- countExtentTiles takes the BUILDING ALONE (see the proven call in resize_building).
-- Measured 2026-11-21: passing (b, x1, z, y1) returned the z value - a 2x2 wood pile
-- reported usable_tiles 167, rect_tiles 0, holes -167.
pcall(function() tiles = dfhack.buildings.countExtentTiles(found) end)
local area = (found.x2-found.x1+1)*(found.y2-found.y1+1)
print(string.format("OK|%d|%d|%d..%d,%d..%d|%d|%d",
    found.id, found.type, found.x1, found.x2, found.y1, found.y2, tiles, area))
""".strip()
    for key, val in (("__X__", x), ("__Y__", y), ("__Z__", z), ("__W__", w), ("__H__", h),
                     ("__TYPE__", ztype)):
        lua = lua.replace(key, str(val))
    out = await _lua(lua)
    line = _one(out)
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    # "OK|a|b|c|d|e" -> the row is everything AFTER the leading "OK" token. Slicing the
    # two letters off leaves the pipe in place and splits into 6 fields (measured: the
    # write succeeded, three zones went live, and the endpoint answered 500 on its own
    # parse - a success reported as a server error is worse than the bug it replaced).
    parts = line.split("|")[1:] if line.startswith("OK|") else line[2:].split("|")
    if len(parts) < 5:
        raise LuaOpError(f"zone row came back malformed: {line!r}")
    bid, tval, extent, tiles, area = parts
    _u, _src = _usable(int(tiles), int(area))
    return {"success": True, "kind": k, "zone_type": ztype, "zone_type_id": int(tval),
            "building_id": int(bid), "extent": extent,
            "usable_tiles": _u, "usable_tiles_source": _src, "rect_tiles": int(area),
            "holes": (int(area) - _u) if _u is not None else None,
            "route": "allocInstance(30, subtype=<kind>) -> setSize -> constructAbstract "
                     "(the create_stockpile path; the kind is never written afterwards)"}


async def remove_zone(dec, building_id: int) -> dict:
    """Remove a zone by id - findAtTile cannot see zones, so the ordinary remove-building
    path cannot reach one.

    THIS FUNCTION HAS A MEASURED CRASH HISTORY - treat it as a loaded gun. On 2026-11-21 the
    chunk ended with `pcall(function() dfhack.buildings.notifyCivzoneModified() end)` - with
    NO building argument. The game died with:

        crash_2026-11-21-22-53-41.txt: SIGSEGV (SEGV_MAPERR) at address 0
        1> libdfhack.so DFHack::Buildings::notifyCivzoneModified(df::building*)
        stack traceback: [C]: in function 'dfhack.buildings.notifyCivzoneModified'
                         (lua command):10: in function <(lua command):10>

    A native segfault is NOT catchable by pcall, so "it's wrapped, it's safe" was false and
    the call is GONE: deconstruct disposes the zone on its own. Zone removal is only ever
    issued with the caller's explicit acknowledgement, and never twice in a row on the same
    id without reading the zone list first.
    """
    lua = """
local b = nil
for _,zz in ipairs(df.global.world.buildings.other.ANY_ZONE) do
  if zz.id == __ID__ then b = zz break end
end
if b == nil then print("ERR|no zone with id __ID__") return end
local zid = tonumber(b.id)
-- Cleanly detach any assigned pasture/pit animals and room owner before deconstruct
pcall(function() dfhack.buildings.setOwner(b, nil) end)
pcall(function()
  for idx = #b.assigned_units - 1, 0, -1 do
    b.assigned_units:erase(idx)
  end
end)
pcall(function()
  for _, u in ipairs(df.global.world.units.active) do
    for idx = #u.general_refs - 1, 0, -1 do
      local r = u.general_refs[idx]
      if df.general_ref_building_civzone_assignedst:is_instance(r) and r.building_id == zid then
        u.general_refs:erase(idx)
        pcall(function() r:delete() end)
      end
    end
  end
end)
-- building_flags has no marked_for_removal on this build, so this write threw;
-- the API's own removal is the supported path (it also disposes cleanly).
local ok_rm, err_rm = pcall(function() dfhack.buildings.deconstruct(b) end)
if not ok_rm then print("ERR|deconstruct failed: "..tostring(err_rm)) return end
print("OK|" .. tostring(zid))
""".strip().replace("__ID__", str(int(building_id)))
    out = await _lua(lua)
    line = _one(out)
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    return {"success": True, "removed_zone_id": int(line.split("|")[1])}


async def place_building_plan(dec, name: str, x: int, y: int, elev: int,
                              w: int = 1, h: int = 1) -> dict:
    """Place a multi-tile workshop as a BUILD PLAN - quickfort's own call.

    Why this exists: `constructAbstract` rejects workshops outright
    ("expected !bld->isActual()") - measured placing a 3x4 Still. Workshops are not
    abstract: the game creates a plan and a builder finishes it. Quickfort's
    internal/quickfort/build.lua:1259 `create_building` is the field-tested path, and it
    is a single call:

        constructBuilding{type, subtype, custom, pos, width, height, direction, fields}

    with NO `abstract` flag, and `fields.room.extents` for the footprint. That is this
    function. The 1x1 route stays where it already works (stockpiles, farm plots, zones).
    """
    bt, sub, cus, resolved = resolve_building(dec, name)
    z = int(elev) + GAME_Z_OFFSET
    w, h = max(1, int(w)), max(1, int(h))
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local W, H = __W__, __H__
local area = W * H
-- building_extents is a 256-BIT field indexed (x - x1) + 16*(y - y1). Measured
-- 2026-11-21: allocating W*H BYTES and setting the first W*H bits made the "3x4"
-- still a 12-tile strip in ONE row (tile (42,127) read back as null), and a legal
-- 3x3 farm plot was refused ("cannot place at this position") because the mask
-- claimed tiles the site does not have. 32 bytes = 256 bits, stride 16.
local ext = df.reinterpret_cast(df.building_extents_type, df.new('uint8_t', 32))
for i = 0, 31 do ext[i] = 0 end
for dy = 0, H - 1 do
  for dx = 0, W - 1 do ext[dy * 16 + dx] = 1 end
end
local before = {}
for _,bb in ipairs(df.global.world.buildings.all) do before[bb.id] = true end
local bld, err = dfhack.buildings.constructBuilding{
    type = __TYPE__, subtype = __SUB__, custom = __CUS__,
    pos = pos, width = W, height = H,
    fields = { room = {x = 0, y = 0, width = W, height = H, extents = ext} },
}
if not bld then print("ERR|constructBuilding refused: "..tostring(err)) return end
local tiles = 0
pcall(function() tiles = dfhack.buildings.countExtentTiles(bld) end)
local area2 = (bld.x2 - bld.x1 + 1) * (bld.y2 - bld.y1 + 1)
local ready = false
pcall(function() ready = bld.is_ready end)
print(string.format("OK|%d|%d..%d,%d..%d|%d|%d|%s",
    bld.id, bld.x1, bld.x2, bld.y1, bld.y2, tiles, area2, tostring(ready)))
""".strip()
    for key, val in (("__X__", x), ("__Y__", y), ("__Z__", z), ("__W__", w), ("__H__", h),
                     ("__TYPE__", int(bt)), ("__SUB__", int(sub)), ("__CUS__", int(cus))):
        lua = lua.replace(key, str(val))
    out = await _lua(lua)
    try:
        line = _one(out)
    except LuaOpError:
        # No reply row at all. That is not "refused" and not "success": measured placing a
        # 3x4 Still into a spot a player had already built, the chunk printed nothing.
        # Answer with what a read can prove - is there a building on the origin tile?
        probe = await _lua(f'local b = dfhack.buildings.findAtTile({int(x)}, {int(y)}, {z})\n'
                           'print("PROBE|"..tostring(b ~= nil).."|"..tostring(b and b.id or -1))')
        row = [l for l in probe.splitlines() if l.startswith("PROBE|")]
        live = row[0].split("|")[1] == "true" if row else False
        bid = int(row[0].split("|")[2]) if row and live else -1
        raise LuaOpError(
            f"the game printed nothing for this plan; read-back says a building IS on the "
            f"origin tile (id {bid})" if live else
            f"the game printed nothing and nothing was placed at {int(x)},{int(y)} elev "
            f"{int(elev)} - the site is rejected, not the tool")
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    parts = line.split("|")[1:]   # strip the OK token, not two letters (the pipe stays otherwise)
    if len(parts) < 5:
        raise LuaOpError(f"plan row came back malformed: {line!r}")
    bid, extent, tiles, area2, ready = parts[:5]
    _u, _src = _usable(int(tiles), int(area2))
    return {"success": True, "building": resolved, "building_id": int(bid),
            "extent": extent, "usable_tiles": _u, "usable_tiles_source": _src,
            "rect_tiles": int(area2),
            "holes": (int(area2) - _u) if _u is not None else None,
            "ready_for_construction": ready == "true",
            "route": "constructBuilding(type, subtype, custom, width, height, room.extents) "
                     "- quickfort's workshop plan path (no constructAbstract)"}


async def create_building_multi(dec, name: str, x: int, y: int, elev: int,
                                w: int, h: int) -> dict:
    """Create a MULTI-TILE building of ANY type with the game's own calls.

    Why this exists: `constructBuilding` reads `width`/`height`, so passing w/h to it
    changes nothing - measured twice on this fort (2026-11-21): a 4x2 Stockpile came
    back 1x1 (x66-66,128-128) and a 3x2 Farm Plot came back 1x1, both reported as
    `success: true` by the endpoint. A 1x1 general pile clogs hauling and a 1x1 farm
    plot starves the fort, and the caller has no idea, so the size has to be set
    before the commit and read back from the game afterwards:

        allocInstance(pos, type) -> setSize(w, h) -> constructAbstract (piles/zones)
                                    ... or completeBuild (farm plots, workshops)
        verify with findAtTile + countExtentTiles, report id/extents/usable/rect.
    """
    bt, sub, cus, resolved = resolve_building(dec, name)
    z = int(elev) + GAME_Z_OFFSET
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local inst = dfhack.buildings.allocInstance(pos, __TYPE__, __SUB__, __CUS__)
if inst == nil then print("ERR|allocInstance failed (type __TYPE__)") return end
if not dfhack.buildings.setSize(inst, __W__, __H__, 8) then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- no orphan on the failure path
  print("ERR|setSize refused (overlap with a building, walls/ramps in the footprint?)") return
end
local ok_abs, err_abs = pcall(function() return dfhack.buildings.constructAbstract(inst) end)
if not ok_abs then
  pcall(function() dfhack.buildings.completeBuild(inst) end)
end
local b = dfhack.buildings.findAtTile(pos)
if b == nil then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- no orphan on the failure path
  print("ERR|nothing live at the tile (abstract: " .. tostring(ok_abs) .. " " .. tostring(err_abs) .. ")")
  return
end
local tiles = 0
pcall(function() tiles = dfhack.buildings.countExtentTiles(b) end)
local area = (b.x2 - b.x1 + 1) * (b.y2 - b.y1 + 1)
local desc = "?"
pcall(function() desc = dfhack.buildings.getGeneralBuildDescription(b) end)
print(string.format("OK|%d|%s|%d..%d,%d..%d|%d|%d", b.id, desc, b.x1, b.x2, b.y1, b.y2, tiles, area))
""".strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z),
                 ("__W__", max(1, int(w))), ("__H__", max(1, int(h))),
                 ("__TYPE__", int(bt)), ("__SUB__", int(sub)), ("__CUS__", int(cus)),
                 ("__NAME__", f'"{resolved}"')):
        lua = lua.replace(k, str(v))
    out = await _lua(lua)
    line = _one(out)
    if line.startswith("ERR|"):
        raise LuaOpError(line[4:])
    parts = line.split("|")[1:]
    bid, desc, extent, tiles, area = parts
    _u, _src = _usable(int(tiles), int(area))
    return {"success": True, "building_id": int(bid), "game_description": desc,
            "extent": extent, "usable_tiles": _u, "usable_tiles_source": _src,
            "rect_tiles": int(area),
            "holes": (int(area) - _u) if _u is not None else None}


async def create_stockpile(dec, x: int, y: int, elev: int, w: int, h: int) -> dict:
    """Create a MULTI-TILE stockpile with the game's own building calls.

    Measured on 53.17-r1 (all of this is in the crash/diary history):
    - `constructBuilding{..., w, h}` does nothing for size: the library reads
      `width`/`height`, so w/h produced 1x1 piles.
    - `constructAbstract` requires `!bld->isActual()`, and `allocInstance` for a STOCKPILE
      (type 29) yields an actual building - so abstract is not the commit for piles, and
      calling it on the committed instance is the "expected: !bld->isActual()" failure.
    - `setSize` is legal only before the commit and fails on overlap.
    So: allocInstance -> setSize; if the pile is not live afterwards, try constructAbstract
    in a pcall and treat an isActual refusal as "already live"; verify by finding the
    STOCKPILE whose footprint starts at (x, y) at this z, and report id/extents/tile count.
    """
    z = int(elev) + GAME_Z_OFFSET
    lua = """
local pos = xyz2pos(__X__, __Y__, __Z__)
local inst = dfhack.buildings.allocInstance(pos, 29, -1, -1)
if inst == nil then print("ERR|allocInstance failed") return end
if not dfhack.buildings.setSize(inst, __W__, __H__, 8) then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- no orphan on the failure path
  print("ERR|setSize refused (overlap with a building, walls/ramps in the footprint?)") return
end
local ok_abs, err_abs = pcall(function() return dfhack.buildings.constructAbstract(inst) end)
local found = nil
for _,sp in ipairs(df.global.world.buildings.other.STOCKPILE) do
  if sp.x1 == __X__ and sp.y1 == __Y__ and sp.z == __Z__ then found = sp break end
end
if found == nil then
  pcall(function() dfhack.buildings.deconstruct(inst) end)   -- no orphan on the failure path
  print("ERR|pile not live after construction (constructAbstract said " .. tostring(ok_abs) .. ": " .. tostring(err_abs) .. ")")
  return
end
local tiles = 0
-- countExtentTiles takes the BUILDING ALONE (see the proven call in resize_building).
-- Measured 2026-11-21: passing (b, x1, z, y1) returned the z value - a 2x2 wood pile
-- reported usable_tiles 167, rect_tiles 0, holes -167.
pcall(function() tiles = dfhack.buildings.countExtentTiles(found) end)
local area = (found.x2-found.x1+1)*(found.y2-found.y1+1)
print(string.format("OK|__N__|%d|%d..%d,%d..%d|%d|%d", found.id, found.x1, found.x2, found.y1, found.y2, tiles, area))
"""
    # The reply stream cross-talks, and a create is a WRITE: answering with a line from an
    # EARLIER call hands the caller a building it never made. Measured 2026-11-21:
    # /create-stockpile at 42 x41..45 y120..124 returned {"building_id": 1,
    # "extent": "66..67,112..113"} - a stale OK| line naming the wood pile at 38 - and the
    # follow-up /configure-stockpile then rewrote the player's wood pile to corpses+refuse.
    # Every create answer now carries this request's nonce, and a reply without it is
    # reported as NO DATA instead of a plausible-looking wrong id.
    nonce = uuid.uuid4().hex[:10]
    expr = lua.strip()
    for k, v in (("__X__", int(x)), ("__Y__", int(y)), ("__Z__", z),
                 ("__W__", int(w)), ("__H__", int(h)), ("__N__", nonce)):
        expr = expr.replace(k, str(v))
    out = await _lua(expr)
    line = ""
    for cand in _rows(out):
        if cand.startswith(f"OK|{nonce}|"):
            line = cand
            break
    for cand in _rows(out):
        if cand.startswith("ERR|"):
            raise LuaOpError(cand[4:])
    if not line:
        raise LuaOpError("create reply carried no line for this request (cross-talk): the pile "
                         "may exist - read /stockpiles for the real id before configuring anything")
    parts = line.split("|")
    if len(parts) < 6:
        raise LuaOpError("unparsable create result: " + line[:120])
    bid, extent, tiles, area = int(parts[2]), parts[3], int(parts[4]), int(parts[5])
    return {"success": True, "building_id": bid, "extent": extent, "nonce": nonce,
            "usable_tiles": tiles, "rect_tiles": area, "holes": area - tiles}


async def make_stockpile(dec, x: int, y: int, elev: int, w: int, h: int,
                         allow: list[str] | None = None, deny: list[str] | None = None,
                         all_categories: bool = False, max_wheelbarrows: int | None = None,
                         max_barrels: int | None = None, max_bins: int | None = None) -> dict:
    """Create a multi-tile stockpile AND set it up correctly, in one call.

    Order: allocInstance -> setSize -> constructAbstract, then the category flags plus the
    material arrays the custom-settings UI walks, then container limits - all before we
    report. There is no follow-up step to forget: a pile made here is openable in the UI.
    """
    built = await create_stockpile(dec, x, y, elev, w, h)
    bid = int(built["building_id"])
    cfg = await configure_stockpile(dec, bid, allow=allow, deny=deny,
                                   all_categories=all_categories,
                                   max_wheelbarrows=max_wheelbarrows,
                                   max_barrels=max_barrels, max_bins=max_bins)
    return {"success": True, "building_id": bid, "extent": built["extent"],
            "usable_tiles": built["usable_tiles"], "holes": built["holes"],
            "configured": cfg.get("line")}

# The per-category material arrays the custom-settings UI walks, as allocated by the game
# itself (measured against a UI-made pile, stockpile number 6, on this world). Writing only
# settings.flags leaves these vectors at size 0 while the category flag is true, and the
# custom-settings UI then dereferences past an empty vector: SIGSEGV at address 0xa in
# render_things with overlay.plug.so on the stack (crash logs 23:48:06 and 23:56:23).
SETTINGS_ARRAYS = {
    "ammo": {"mats": 319, "other_mats": 2, "quality_core": 7, "quality_total": 7, "type": 10},
    "animals": {"enabled": 1327},
    "armor": {"body": 20, "color": 136, "feet": 14, "hands": 11, "head": 16, "legs": 17,
              "mats": 319, "other_mats": 10, "quality_core": 7, "quality_total": 7, "shield": 6},
    "bars_blocks": {"bars_mats": 319, "bars_other_mats": 5, "blocks_mats": 319, "blocks_other_mats": 4},
    "cloth": {"cloth_metal": 1, "cloth_plant": 8, "cloth_silk": 49, "cloth_yarn": 8, "color": 136,
              "thread_metal": 1, "thread_plant": 8, "thread_silk": 49, "thread_yarn": 8},
    "coins": {"mats": 319},
    # MEASURED on the player's UI-made pile (2026-11-22, pile 1 at 75-81,119-124 @36):
    # corpses.corpses = vector<char>[1327] (indexed by RACE), refuse.* = [1327] each and
    # refuse.type = [113] (indexed by ITEM TYPE). My old table said 0 for these - I had read a
    # TOOL-made pile, which is the crash, not the reference. wood.mats = [0] IS legal: a UI pile
    # with wood enabled has an empty mats array and opens fine.
    "corpses": {"corpses": 1327},
    "finished_goods": {"color": 136, "mats": 319, "other_mats": 16, "quality_core": 7,
                       "quality_total": 7, "type": 113},
    "food": {"cheese_animal": 26, "cheese_plant": 0, "drink_animal": 2, "drink_plant": 76,
             "egg": 307, "fish": 81, "glob": 2420, "glob_paste": 13, "glob_pressed": 7,
             "leaves": 95, "liquid_animal": 265, "liquid_misc": 2, "liquid_plant": 9,
             "meat": 14476, "plants": 225, "powder_creature": 0, "powder_plant": 101,
             "seeds": 157, "unprepared_fish": 81},
    "furniture": {"mats": 319, "other_mats": 15, "quality_core": 7, "quality_total": 7, "type": 35},
    "gems": {"cut_mats": 319, "cut_other_mats": 659, "rough_mats": 319, "rough_other_mats": 659},
    "leather": {"color": 136, "mats": 892},
    "refuse": {"body_parts": 1327, "bones": 1327, "corpses": 1327, "hair": 1327, "horns": 1327,
               "shells": 1327, "skulls": 1327, "teeth": 1327, "type": 113},
    "sheet": {"paper": 9, "parchment": 892},
    "stone": {"mats": 319},
    "weapons": {"mats": 319, "other_mats": 10, "quality_core": 7, "quality_total": 7,
                "trapcomp_type": 5, "weapon_type": 45},
    "wood": {"mats": 319},
}


def _array_alloc_lua(category: str, sizes: dict) -> str:
    """Lua that resizes and fills one category's UI-walked arrays (allow everything).

    JOIN WITH REAL NEWLINES. This function joined with the literal two-character `\\n`, so
    every chunk it built reached Lua as one line of backslashes - the allocation NEVER ran,
    and the repair tool answered `CR_FAILURE: unexpected symbol near '\\'`. The consequence is
    the crash we measured on 2026-11-22: `crash_2026-11-22-20-02-29.txt`, SIGSEGV at 0x5 in
    `render_things` via `overlay.plug.so`, "no Lua involvement" - the player clicked Custom on
    a tool-made pile whose category flags were ON over unallocated material arrays.
    """
    lines = []
    for field, n in sizes.items():
        if n <= 0:
            # Zero in SETTINGS_ARRAYS means "the reference pile's array is empty", NOT "no
            # array is needed": the UI still walks the field. Allocate one slot so the
            # pointer is never nil.
            n = 1
        lines.append(f"  if s.{field} ~= nil and #s.{field} < {n} then")
        lines.append(f"    s.{field}:resize({n})")
        lines.append(f"    for i = 0, {n - 1} do pcall(function() s.{field}[i] = 1 end) end")
        lines.append("  end")
    return chr(10).join(lines)


def _json_balanced(raw: str) -> bool:
    """True when `raw` is a complete JSON value: braces/brackets close outside strings.

    Used by the MCP_JSON reader because a module print is not guaranteed to arrive as one
    console line - a control byte inside a DF string splits it.
    """
    depth = 0
    in_str = False
    esc = False
    for ch in raw:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                return True
    return depth == 0 and not in_str


async def repair_stockpile_settings(dec, building_id: int) -> dict:
    """Allocate the per-category material arrays the game's custom-settings UI walks.

    Flags-only configuration is not enough: enabling a category with an unallocated
    `mats`/`type`/sub-vector makes the custom-settings UI crash the game. Sizes here are the
    game's own, measured from a UI-made pile; a UI pile is also re-read at runtime so a
    different world's counts win over these constants.
    """
    per_cat = []
    for cat, sizes in SETTINGS_ARRAYS.items():
        block = _array_alloc_lua(cat, sizes)
        if block:
            # Real newlines, not the literal two-character sequence \n: emitted as escaped
            # text it reached Lua as `local s = sp.settings.amo\nif ...`, and the game answered
            # "unexpected symbol near '\'" (CR_FAILURE, measured 2026-11-22 on /repair-stockpile-settings).
            per_cat.append(chr(10).join([
                "local s = sp.settings.%s" % cat,
                "if s ~= nil then",
                block,
                "end",
            ]))
    body = chr(10).join(per_cat)
    expr = f"""
local sp = nil
for _,x in ipairs(df.global.world.buildings.other.STOCKPILE) do if x.id == {int(building_id)} then sp = x end end
if sp == nil then print("FAIL pile {int(building_id)} not found") return end
local fixed = {{}}
{body}
for _,c in ipairs{{"ammo","animals","armor","bars_blocks","cloth","coins","corpses","finished_goods","food","furniture","gems","leather","refuse","sheet","stone","weapons","wood"}} do
  local s = sp.settings[c]
  local on = false
  pcall(function() on = (sp.settings.flags[c] == true) end)
  if s ~= nil and on then
    local parts = {{}}
    for k,v in pairs(s) do
      local n = -1
      local ok = pcall(function() n = #v end)
      if ok and n >= 0 and tostring(v):find("vector<char>") then parts[#parts+1] = tostring(k) .. "=" .. n end
    end
    table.sort(parts)
    fixed[#fixed+1] = c .. "{{" .. table.concat(parts, ",") .. "}}"
  end
end
print("OK id=" .. tostring(sp.id) .. " " .. table.concat(fixed, " "))
""".strip()
    out = await _lua(expr)
    line = _one(out)
    if line.startswith("FAIL"):
        raise LuaOpError(line)
    return {"success": True, "line": line[:900]}


# ---------------------------------------------------------------------------
# Server-side RPC Lua module bridge (deployables/scripts/dfhack_mcp.lua)
#
# Why: instead of interpolating parameters into ad-hoc multi-line Lua strings and
# parsing pipe-delimited stdout, new operations live in a real git-tracked Lua
# module (`deployables/scripts/dfhack_mcp.lua`), deployed to `<gamedir>/hack/scripts/`
# via `./scripts/deploy.sh deploy` and auto-bootstrapped into `_G.__dfhack_mcp` per
# connection generation if not yet deployed or updated in-repo.
# Every call passes a JSON argument table and returns one `MCP_JSON|{...}` line.
# ---------------------------------------------------------------------------

LUA_MODULE_PATH = pathlib.Path(__file__).resolve().parent / "deployables" / "scripts" / "dfhack_mcp.lua"
_module_gen: int = -1
_module_sha: str = ""


def _parse_mcp_json(out: str) -> dict:
    """Extract and parse the `MCP_JSON|{...}` payload printed by dfhack_mcp.lua."""
    lines = out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("MCP_JSON|"):
            raw = s[len("MCP_JSON|"):]
            # A module print can arrive as SEVERAL text fragments; a raw control byte in a
            # DF string (trader names) splits the payload mid-string, so a "single line"
            # read is not a safe assumption. Keep appending following lines until the JSON
            # balances - only then is it parsed. (Measured 2026-11-22 on /trade-depot.)
            j = i + 1
            while not _json_balanced(raw) and j < len(lines):
                raw += lines[j].strip()
                j += 1
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                raise LuaOpError(f"malformed MCP_JSON payload: {e}: {raw[:160]!r}") from e
            if not data.get("ok", False):
                raise LuaOpError(str(data.get("error") or f"dfhack_mcp {data.get('op')} failed"))
            res = data.get("result")
            return res if isinstance(res, dict) else {"value": res}
    raise LuaOpError(f"dfhack_mcp produced no MCP_JSON line: {out[:200]!r}")


async def _ensure_lua_module(force: bool = False) -> None:
    """Ensure `_G.__dfhack_mcp` is loaded in the running game for the current connection
    generation and matches `deployables/scripts/dfhack_mcp.lua`."""
    global _module_gen, _module_sha
    src = LUA_MODULE_PATH.read_text(encoding="utf-8")
    sha = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
    gen = connection_generation()
    if not force and _module_gen == gen and _module_sha == sha:
        return
    # Load the module chunk into _G.__dfhack_mcp and print a confirmation line
    bootstrap = (
        f"local fn, err = load({json.dumps(src)}, 'dfhack_mcp.lua')\n"
        "if not fn then print('MCP_JSON|{\"ok\":false,\"error\":' .. string.format('%q', tostring(err)) .. '}') return end\n"
        "local m = fn()\n"
        "_G.__dfhack_mcp = m\n"
        "print('MCP_JSON|{\"ok\":true,\"result\":{\"bootstrapped\":true,\"version\":' .. string.format('%q', tostring(m and m.VERSION or '?')) .. '}}')"
    )
    out = await _lua(bootstrap)
    _parse_mcp_json(out)
    _module_gen = connection_generation()
    _module_sha = sha


async def call_module(op: str, args: dict | None = None) -> dict:
    """Invoke `OPS.<op>(args)` in `deployables/scripts/dfhack_mcp.lua` with JSON arguments
    and parse its `MCP_JSON|{...}` response. Auto-bootstraps `_G.__dfhack_mcp` if needed."""
    await _ensure_lua_module()
    op_lit = json.dumps(str(op))
    args_lit = json.dumps(json.dumps(args or {}))
    invoke = (
        "if _G.__dfhack_mcp == nil then\n"
        "  local ok, m = pcall(reqscript, 'dfhack_mcp')\n"
        "  if ok and m then _G.__dfhack_mcp = m end\n"
        "end\n"
        "if _G.__dfhack_mcp == nil then print('MCP_JSON|{\"ok\":false,\"error\":\"MODULE_NOT_LOADED\"}') return end\n"
        f"_G.__dfhack_mcp.dispatch({op_lit}, {args_lit})"
    )
    try:
        out = await _lua(invoke)
        return _parse_mcp_json(out)
    except LuaOpError as e:
        if "MODULE_NOT_LOADED" in str(e):
            await _ensure_lua_module(force=True)
            out = await _lua(invoke)
            return _parse_mcp_json(out)
        raise


async def game_paths() -> dict:
    """Discover the live game's DF directory, save path, crashlogs, blueprints, and scripts
    via `dfhack_mcp.lua` (closes tool_missing.md #5)."""
    return await call_module("game_paths", {})


async def building_status(building_id: int | None = None,
                          only_incomplete: bool = True,
                          limit: int = 25) -> dict:
    """Inspect unfinished/planned (or specific) buildings, build stages, attached items,
    and missing job item filters via `dfhack_mcp.lua` (closes tool_missing.md #15)."""
    payload: dict = {"only_incomplete": bool(only_incomplete), "limit": int(limit)}
    if building_id is not None:
        payload["building_id"] = int(building_id)
    return await call_module("building_status", payload)


async def configure_farmplot(building_id: int | None = None,
                             crop: str | None = None,
                             all_seasons: bool = True,
                             spring: bool = False,
                             summer: bool = False,
                             autumn: bool = False,
                             winter: bool = False) -> dict:
    """Read or set a farm plot's seasonal crops with read-back via `dfhack_mcp.lua`
    (closes tool_missing.md #6)."""
    payload: dict = {
        "all_seasons": bool(all_seasons),
        "spring": bool(spring),
        "summer": bool(summer),
        "autumn": bool(autumn),
        "winter": bool(winter),
    }
    if building_id is not None:
        payload["building_id"] = int(building_id)
    if crop is not None:
        payload["crop"] = str(crop)
    return await call_module("configure_farmplot", payload)


async def observe_status_module() -> dict:
    """Structured status-bar probe (picks, site_id, date, seeds, alcohol, meals, raw_food)
    via `dfhack_mcp.lua`."""
    return await call_module("observe_status", {})


async def assign_zone_owner(building_id: int, unit_id: int | None = None) -> dict:
    """Set or read the owner of an activity zone (Office, Bedroom, DiningHall, Tomb)
    via `dfhack_mcp.lua` (`dfhack.buildings.setOwner` / `getOwner` / `getRoomDescription`)."""
    payload: dict = {"building_id": int(building_id)}
    if unit_id is not None:
        payload["unit_id"] = int(unit_id)
    return await call_module("assign_zone_owner", payload)


async def assign_pasture(building_id: int | None = None,
                         unit_ids: list[int] | None = None,
                         all_grazers: bool = True,
                         species: str | None = None,
                         action: str = "assign") -> dict:
    """Assign, unassign, or clear tame grazing animals (or specific `unit_ids` / `species`)
    on a Pen/Pasture zone via `dfhack_mcp.lua`."""
    payload: dict = {"action": str(action), "all_grazers": bool(all_grazers)}
    if building_id is not None:
        payload["building_id"] = int(building_id)
    if unit_ids is not None:
        payload["unit_ids"] = [int(i) for i in unit_ids]
    if species is not None:
        payload["species"] = str(species)
    return await call_module("assign_pasture", payload)


async def manage_nobles(action: str = "list",
                        position: str | None = None,
                        unit_id: int | None = None,
                        bookkeeper_settings: int | None = None) -> dict:
    """List, assign, or auto-assign fortress noble positions (`MANAGER`, `BOOKKEEPER`,
    `BROKER`, `CHIEF_MEDICAL_DWARF`, etc.) and bookkeeper precision via `dfhack_mcp.lua`."""
    payload: dict = {"action": str(action)}
    if position is not None:
        payload["position"] = str(position)
    if unit_id is not None:
        payload["unit_id"] = int(unit_id)
    if bookkeeper_settings is not None:
        payload["bookkeeper_settings"] = int(bookkeeper_settings)
    return await call_module("manage_nobles", payload)


async def manage_work_orders(action: str = "list",
                             order_id: int | None = None,
                             job: str | None = None) -> dict:
    """List or cancel active manager work orders and inspect manager appointment status
    via `dfhack_mcp.lua`."""
    payload: dict = {"action": str(action)}
    if order_id is not None:
        payload["order_id"] = int(order_id)
    if job is not None:
        payload["job"] = str(job)
    return await call_module("manage_work_orders", payload)


async def alerts_and_wellbeing() -> dict:
    """Structured threat/opportunity radar (popups, caravans, hostiles, unpastured grazers,
    strange moods) and citizen wellbeing metrics (thirsty, hungry, drowsy, stressed, wounded)
    via `dfhack_mcp.lua`."""
    return await call_module("alerts_and_wellbeing", {})


async def gather_and_items(action: str = "gather_plants",
                           x0: int | None = None,
                           y0: int | None = None,
                           x1: int | None = None,
                           y1: int | None = None,
                           elev: int | None = None,
                           item_filter: str | None = None,
                           ban_brew: bool = True) -> dict:
    """Designate surface shrubs for plant gathering, mark ground items for dump/undump/forbid/reclaim,
    or protect seed/brewable plant stocks from cooking via `dfhack_mcp.lua`."""
    payload: dict = {"action": str(action), "ban_brew": bool(ban_brew)}
    for k, v in (("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1), ("elev", elev)):
        if v is not None:
            payload[k] = int(v)
    if item_filter is not None:
        payload["item_filter"] = str(item_filter)
    return await call_module("gather_and_items", payload)


async def buildings_live(elev0: int | None = None, elev1: int | None = None,
                         type: str | None = None) -> dict:
    """Every building the GAME holds (`df.global.world.buildings.all`), not just what the RPC publishes.

    Measured 2026-11-22: the RPC block census reported the wagon and a chair, and never the siege
    **artery** the game announced - RemoteFortressReader only publishes its own building enum.
    """
    payload: dict = {}
    if elev0 is not None:
        payload["elev0"] = int(elev0)
    if elev1 is not None:
        payload["elev1"] = int(elev1)
    if type:
        payload["type"] = str(type)
    return await call_module("buildings_live", payload)


async def unit_profession(action: str = "list", unit_id: int | None = None,
                          profession: str | None = None,
                          profession_id: int | None = None) -> dict:
    """Who is a miner in the game's own terms - the profession layer the Labor screen shows.

    Measured 2026-11-22: the player's Miners page ticked one dwarf while my `u.status.labors`
    census said 4. On this build labors are driven by PROFESSION assignment, so the profession is
    the layer to set when the question is "how many miners do I have".
    """
    payload: dict = {"action": str(action)}
    if unit_id is not None:
        payload["unit_id"] = int(unit_id)
    if profession:
        payload["profession"] = str(profession)
    if profession_id is not None:
        payload["profession_id"] = int(profession_id)
    return await call_module("unit_profession", payload)


async def work_details(action: str = "list", detail: str | int | None = None,
                       unit_id: int | None = None, mode: str | None = None,
                       labor: str | None = None, value: bool | None = None) -> dict:
    """The Labor menu's work details - `assigned_units` IS the green tick column.

    Route (found in the game's own scripts - autofish.lua, gui/manipulator.lua,
    gui/settings-manager.lua - and measured live):
    `df.global.plotinfo.labor_info.work_details[i] = { name, icon, flags, allowed_labors,
    assigned_units }`, where `assigned_units` is a vector of UNIT IDS. Built-in details carry
    `cannot_be_everybody` / `no_modify`. This is the layer that OWNS `unit.status.labors`: the game
    recomputes those flags from the details, which is exactly why my labor writes kept lying.
    """
    payload: dict = {"action": str(action)}
    if detail is not None:
        payload["detail"] = detail
    if unit_id is not None:
        payload["unit_id"] = int(unit_id)
    if mode:
        payload["mode"] = str(mode)
    if labor:
        payload["labor"] = str(labor)
    if value is not None:
        payload["value"] = bool(value)
    return await call_module("work_details", payload)


async def unit_labors(unit_ids: list[int] | None = None, labor: str | None = None) -> dict:
    """Read the labors the GAME holds per citizen - the read-back for labor writes.

    Field names are DFHack's (`MINE`, not `MINING`); `u.status.labors.MINING` does not exist on
    this build and a `pcall` probe reads it as a silent false, which is how a raw-Lua labor census
    can lie to you.
    """
    payload: dict = {}
    if unit_ids:
        payload["unit_ids"] = [int(i) for i in unit_ids]
    if labor:
        payload["labor"] = str(labor)
    return await call_module("unit_labors", payload)


async def pile_arrays(action: str = "check",
                       building_id: int | None = None) -> dict:
    """Check/fix the per-category material arrays the game's Custom-settings UI walks.

    The player's crash of 2026-11-22 20:02:29 (`crash_2026-11-22-20-02-29.txt`, SIGSEGV at 0x5,
    `render_things` -> `overlay.plug.so`, no Lua) is a tool-made pile whose category flags are ON
    over unallocated arrays: the settings screen dereferences nil. `action='check'` lists the empty
    fields per pile; `action='fix'` resizes them to a reference pile's measured sizes (a pile the
    game's UI built), never to invented numbers.
    """
    payload = {"action": str(action)}
    if building_id is not None:
        payload["building_id"] = int(building_id)
    return await call_module("pile_arrays", payload)


async def trade_depot(action: str = "status",
                      building_id: int | None = None,
                      item_ids: list[int] | None = None,
                      item_filter: str | None = None,
                      min_value: int = 0,
                      max_items: int = 25,
                      trader_requested: bool | None = None,
                      anyone_can_trade: bool | None = None,
                      side: str = "fortress",
                      selected: bool = True) -> dict:
    """Inspect Trade Depot status, list tradeable goods/bins, mark/unmark goods for hauling
    to the depot (`dfhack.items.markForTrade`), summon the Broker, and inspect/select items
    on the barter screen via `dfhack_mcp.lua`."""
    payload: dict = {
        "action": str(action),
        "min_value": int(min_value),
        "max_items": int(max_items),
        "side": str(side),
        "selected": bool(selected),
    }
    if building_id is not None:
        payload["building_id"] = int(building_id)
    if item_ids is not None:
        payload["item_ids"] = [int(i) for i in item_ids]
    if item_filter is not None:
        payload["item_filter"] = str(item_filter)
    if trader_requested is not None:
        payload["trader_requested"] = bool(trader_requested)
    if anyone_can_trade is not None:
        payload["anyone_can_trade"] = bool(anyone_can_trade)
    return await call_module("trade_depot", payload)


