---
name: df-dig
description: Dig tunnels, rooms and stair shafts in Dwarf Fortress with real labour - depth under the surface, damp/hot hazards, designating into fog, and proving the dig actually happened.
whenToUse: Use when planning or executing any mining - entrance tunnels, rooms, stairwells, farm/industry halls - and whenever a dig "completed" but nothing appears, a designation vanished, or a plan sits in unexplored rock.
metadata:
  source: measured on DF 53.17-r1 (STEAM) with the dfhack-mcp layer, during play tests on region1
  tools: send_dig_command, set_unit_labors, overburden, depth_profile, column, cross_section, hazards, probe_plan, tunnel_sites, warnings, tile_at, world_map, world_objects, dig_run.py, watch_dig.py, sink_shaft.py, dig_adit.py
---

# Digging: tunnels, rooms, shafts

A tunnel is defined by **what is above it**. Dig where there is no rock over you and you
have cut a trench on the hillside, not a tunnel. Everything below is measured, not folklore.

## 0. Speak the player's elevation
RPC `z`, our `elev = z - 128`, and the in-game panel number **`ui = z - 129`** differ by one.
`elev 36` is **in-game Elevation 35**. Every tool prints both; when talking to a human, use
the in-game number.

## 1. The three gates before any designation
| Gate | Tool | Rule |
|---|---|---|
| **Mountain on top** | `depth-profile` / `overburden` | every tile `depth >= 2` under its **local** surface. Depth 0 = trench, 1 = shallow |
| **Damp** | `hazards` | no aquifer or standing water in the column **at/above** the dig level. Aquifers flood the works; this world's band is elev 33-34 |
| **Hot** | `hazards` + `magma-depth` | magma below. Worldgen first: `getGlobalInitFeature` volcano flags (this map: none). Tile scans see **explored tiles only** - "no magma found" in fog proves nothing, so `magma-depth` reports `scanned_to` |

Roof rule: **fog above is normal** inside a mountain (the player's own room has fog over it).
Refuse only an **explored void directly above** - that is breaching a cavern/pool.

**Gates apply to known data only; unknown is never a veto.** Depth needs a known local
surface, and most columns are fog - a gate that treats "no surface" as shallow cancelled 732
tiles of a 50-room grid. So: order what is known-dry and known-deep, order the fog too (the
game accepts it and it queues, §2), and re-check depth on the revealed tiles afterwards.
`probe-plan` says this explicitly: its verdicts are `probe here` / `shallow` (known surface,
depth < min) / `fog surface: depth unknown ... designation is accepted and queues` /
`avoid: DAMP` (known aquifer/water) / `open air` (explored void).

## 2. Designate in fog; the game answers with time
Unexplored tiles **accept** designations (measured: 3 tiles at `hidden=True` came back
`tiles: 3, success: true`). So order the whole plan - tunnel, room, shaft - and let the
dwarves dig it, revealing the map as they go. Do not gate designations on visibility or
reachability; use those to *explain* a stall.

**Never designate over stuff.** A designation belongs on **rock (or fog)** - not on floors,
stairs, ramps, buildings, items, or tiles already designated. Ordering a floor wastes a
miner; ordering a building tile is meaningless. So every designation pass starts with
`survey?elev&x0&y0&x1&y1`, which counts each tile's category (rock / hidden / floor / stairs /
ramp / building / water / already_designated) and reports `safe_to_designate` and `blocked`.
`scripts/dig_run.py` filters through it before ordering. On this fort 92 tiles were littered
with designations over dug floors and stairs before the rule existed - clean them with NO_DIG.
Related: count plans **in rooms, not tiles**, and subtract the existing dug footprint first
(§14 note in references).

`m`-menu semantics (`send_dig_command`): `DEFAULT_DIG`, `UP_DOWN_STAIR_DIG`, `DOWN_STAIR_DIG`,
`UP_STAIR_DIG`, `RAMP_DIG`, `CHANNEL_DIG`, `NO_DIG` to clear. Locations are `[x, y, z]` with
`z = elev + 128`, and land **as given** on this build.

## 3. Labour before time (no dig-now, no blueprints for play)
`set_unit_labors` with **`MINE`** (not `MINING`; the enum has `MINE`, `CUTWOOD`,
`BUILD_CONSTRUCTION`, `MASON`, `CARPENTER`). The pool drifts as migrants arrive - re-check
`miners` in `fort-progress` before every dig phase, not once. Unpause the game; dig takes
time, and time is the point.

## 4. Stairs and shafts - the stairwell comes FIRST, and it goes ALL the way down
The player's rule, stated twice: designate the stairwell **on every level it crosses**
(`m -> t`: click the room floor, **scroll one level down**, click again, repeat), and the
ladder rung is literally *"Stairs connecting the levels"*. So:

- A stairwell is a **continuous column**: top = down-stairs, every middle level = up-down
  stairs, bottom = up-stairs. A gap in the middle is not a shaft, it is two shafts that
  reach nothing.
- **Dig it down to the level you intend to work on, and finish it, BEFORE ordering the room
  complex on that level.** Access first is not only about a 3-wide band's flanks (§5) - it is
  the order of the whole plan. I got this backwards: I designated a 975-tile, 50-room complex
  at elev 36 and only then *started* sinking the shaft to it, and the complex had to be
  cleared. A plan whose access is unfinished is not a plan.
- One level at a time when the map is fog (designate, dig, the level below is revealed,
  designate, ...). `scripts/sink_shaft.py` runs exactly that loop and stops with a reason if
  a revealed level is damp, open, or fog - and it must be **allowed to run to the target
  elevation**; killing it mid-shaft leaves a stairwell that ends in the middle of the
  mountain, which is worse than no stairwell.
- Each stair designation is per-level: `UP_DOWN_STAIR_DIG` for the middle levels. Sending
  stairs for one level only (2 tiles at elev 36) buys nothing until the levels below it are
  dug too.
- Water rule when the shaft crosses the aquifer band (elev 33-34 here): water follows the
  shaft down. Plan the drain (cistern/channel above the working level, sealed stairwell,
  pumps) **before** the shaft is opened through the band - this is the flood the game's
  "damp" warning is telling you about.

## 5. Band ordering: middle first
A 3-wide designation whose flanks have no walkable neighbour is **cancelled silently**.
Order the band middle-first: the middle touches the access you came from, and each dug tile
becomes the access point for the next. `scripts/dig_run.py` and `dig_adit.py` do this.

## 6. Prove the dig (this is where agents get burned)
| Claim | Not proof | Proof |
|---|---|---|
| Dig finished | designation count → 0 (designations vanish on cancellation too) | **wall → floor transitions** in the plan box |
| Tunnel exists | floor tiles present (a fresh embark has 22,105 grass floors; natural caverns also read as underground floors) | floors at **depth ≥ 2** with **no fog remaining** in the box |
| Building built | a placement call returning success | `building_at` (`findAtTile`) **and** `isActive` on the tile |

`absence of walls != presence of a tunnel`: a box of unexplored rock has zero `#` glyphs.
My watchers announced "DUG COMPLETE at 0s" twice because they measured absence.

## 7. Waits are background jobs
Never busy-poll in model turns. `scripts/dig_run.py --elev E --x0 --x1 --y0 --y1` designates
the box, then reports `walls | fog | floors+stairs | pending` on every change and exits:
`0` dug complete (floors, no walls, no fog), `2` stalled with a reason, `-1`-pending means
unparseable, i.e. unknown. `watch_dig.py` and `watch_building.py` are the same pattern.

## 8. Geometry rules of thumb (measured on this world)
- Surface steps down as a slope: read `cross-section` - at y=120 the surface is elev 43 at
  x40, 41 at x52, 40 at x56, **39 at x61**, 38 at x65, 37 at x69. Digging elev 39 near x61
  is a surface cut; the same elev at x44 has 4 levels of rock over it.
- Aquifer band: elev 33-34 (12,703 tiles at 34). Digging **above** it is fine, **into** it floods.
- Wagon footprint blocks tunnelling: the wagon occupies `x45-49, y118-122`, and the elev-39
  corridor runs through it. Plan the entrance around the wagon, not through it.
- Reachability is ours to compute (`reach-map`, `dig_reachable`): `getWalkableGroup` is
  uncallable over RPC, `canWalkBetween` throws for adjacent stair tiles and lies for
  same-level grass.
## 9. Dig rates measured on this save (plan time with these, not guesses)
| Crew | Work | Wall-clock |
|---|---|---|
| 4 miners | 37 tiles (tunnel band + chamber) | **61 s** |
| 6 miners | 66 tiles (17x3 tunnel extension + 6x6 room) | **~120 s** |
| 6 miners | 2 stair tiles per level, 5 levels (shaft 39 -> 35) | ~2 min |

So ~0.5-0.6 tiles per miner per minute. A 60-tile room is minutes, not hours - **do not
rush it with dig-now; the game is fast enough and the labour is the point**. Migrant waves
change the crew mid-plan, so re-read `miners` each phase.

**Picks are the real gate on miner count**: the embark ships 3 picks, so more than ~3
simultaneous miners get starved of tools, and picks wear out - the ladder's metal/forge
rungs are the long-term fix. Assign `MINE` broadly, expect the effective crew to be
pick-limited.

## 10-15. Strata, water work, cave-ins, descent learnings, rates
See [references/descent-notes.md](references/descent-notes.md) - the measured detail lives there so this body stays bounded and cheap to load.

## 16. Plan in Z: one district per level, on the strata map
A shaft is not an inconvenience, it is the spine of the fort. Order per **elevation**, chosen
by what the strata allow (measured on this world, in-game numbers):
| ui elev | our elev | Stratum / verdict |
|---|---|---|
| 36 | 37 | soil/grass layer, depth 2-3 under surface 39-40 -> **farm level** (5x5 soil room) |
| 35 | 36 | dry stone above the aquifer, mineral vein band -> **bedrooms/dining/offices** (the grid) |
| 34-33 | 35-34 | **aquifer band - NO-DIG.** This is the barrier: 12,703 aquifer tiles at elev 34, water bodies east. Rooms here flood. |
| 31-27 | 32-28 | below the band, dry (damp *flags* only, no water) -> **industry stack**: storage, smelter (magnetite+coal are in the veins), workshops |
| 25 | 26 | deep, and the shaft column reads **aquifer at 25** - keep small, plan drainage |
Per level, the shape that works (94 tiles each):
`4x4 landing` touching the stair column -> `1-wide hall` -> `4x4 rooms` off the hall with a
**1-tile opening** each and a **wall row between every room**. The landing is what makes a
level usable; a level without a landing is rock with a hole in it.
Rules:
- The stairwell **head** is designated per level with `UP_DOWN_STAIR_DIG`; a level above the
  current top needs its own stair head (the farm level at 37 was opened that way).
- Gates per level, not per plan: `hazards` + `depth-profile` + `survey` on each elevation
  before ordering it. The same x/y box can be legal at 32 and fatal at 34.
- Watch the stack as a stack: `scripts/dig_levels.py --levels 37:..,36:..,32:..` reports
  `walls/fog/floor/pend` per level and finishes only when every level reads floors, no walls,
  no fog. Levels complete in whatever order the crew reaches them.

## 17. Walls are the design, not the leftover
My first room plans used the thinnest wall that technically separates two rooms (1 tile), and
the player's verdict was correct: **not enough wall**. The rules that fix it, in the order that
matters:
- **2 tiles of wall minimum** between a room and a hall, and between adjacent rooms. 1-tile
  walls are weak (they read as thin as a doorframe), give no privacy, no support, and no space
  for a door + frame.
- **Wall rows between room rows: use the pitch.** Rooms 4 tall + **2+ wall rows** = pitch >= 6.
  My bedroom grid went from pitch 5 (1 wall row, 1020 tiles) to pitch 10 - 1,020 designated
  tiles were simply given back to the mountain and the level reads better for it.
- **Wall columns between bands**: 2 columns, not 1 (bands at x36-39 and x42-45, hall at y123;
  x40-41 stay wall).
- **Openings stay 1 tile and must reach the hall through the whole wall** - a passage that stops
  one tile short seals the room. Doors are built in them.
- **Diagonal contact IS a connection.** Dwarves move diagonally; so do water, vermin, miasma and
  ambush. My 4-neighbour reach graph called rooms separated and the game said otherwise - with
  diagonals the map's components collapsed from 63 to 17. Separation needs no corner touching.
- **Count the open area, not the room count.** Support/cave-in thresholds are unverified on this
  build (§13), so the conservative reading is: keep single rooms small (4x4), keep walls thick,
  and do not merge rooms into a hall by accident - if two rooms' walls come out, you have dug a
  big unsupported room and paid for it twice.
- Reuse of space: when re-spacing, **NO_DIG the tiles you are giving up** (272 thin-walled room
  tiles cleared, 1,020 grid tiles returned to wall) - a designation you mean to abandon is a
  job you are making dwarves do.
