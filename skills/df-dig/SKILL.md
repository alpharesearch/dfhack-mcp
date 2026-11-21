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

## 0. One elevation unit: the number in your map panel
The in-game **Elevation** panel number is the unit for everything - tool parameters, band
tables, hazards, depths, dig boxes, script arguments. Internally the layer converts once:
`z = elev + 129` (measured on this save: z167 shows as "Elevation 38"). Never report a second
kind of elevation number; if you see "elev" in this repo it means the panel number.

## 1. The three gates before any designation
| Gate | Tool | Rule |
|---|---|---|
| **Mountain on top** | `depth-profile` / `overburden` | every tile `depth >= 2` under its **local** surface. Depth 0 = trench, 1 = shallow |
| **Damp** | `hazards` | no aquifer or standing water in the column **at/above** the dig level. Aquifers flood the works; this world's band is in-game 32-34 |
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

- A stairwell is a **continuous column, and the TOP is the fort's existing floor**: designate
  `DOWN_STAIR_DIG` on that floor tile (carving stairs out of a floor is allowed), then
  `UP_DOWN_STAIR_DIG` below it, `UP_STAIR_DIG` at the bottom. Start one level under the floor
  and you have dug a cliff - nothing walks down into the shaft.
  stairs, bottom = up-stairs. A gap in the middle is not a shaft, it is two shafts that
  reach nothing. Order per level as the dig advances (measured caveat in references).
- **Dig it down to the level you intend to work on, and finish it, BEFORE ordering the room
  complex on that level.** Access first is not only about a 3-wide band's flanks (§5) - it is
  the order of the whole plan. I got this backwards: I designated a 975-tile, 50-room complex
  at in-game 35 and only then *started* sinking the shaft to it, and the complex had to be
  cleared. A plan whose access is unfinished is not a plan.
- One level at a time when the map is fog (designate, dig, the level below is revealed,
  designate, ...). `scripts/sink_shaft.py` runs exactly that loop and stops with a reason if
  a revealed level is damp, open, or fog - and it must be **allowed to run to the target
  elevation**; killing it mid-shaft leaves a stairwell that ends in the middle of the
  mountain, which is worse than no stairwell.
- Each stair designation is per-level: `UP_DOWN_STAIR_DIG` for the middle levels. Sending
  stairs for one level only (2 tiles at in-game 35) buys nothing until the levels below it are
  dug too.
- Water rule when the shaft crosses the aquifer band (in-game 32-34 here): water follows the
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
Never busy-poll in model turns. `scripts/dig_run.py` designates a box then reports
`walls | fog | floors+stairs | pending` per change: exit `0` = floors with no walls and no
fog, `2` = stalled with reason. Same pattern in `watch_dig.py`, `watch_building.py`,
`watch_water.py`, `dig_levels.py`.

## 8. Geometry of this world (measured)
Surface steps, aquifer band, wagon footprint, reachability caveats: [references/descent-notes.md](references/descent-notes.md)

## 9. Rate and crew (plan time with these, not guesses)
**~0.5-0.6 tiles per miner per minute** (measured: 4 miners/37 tiles = 61 s; 6 miners/66 tiles
= ~120 s; a 2-tile stair level = 15-30 s). The crew is **pick-limited** - the embark ships 3
picks, so more `MINE` designations than picks starve, and picks wear out (forge rungs fix it).
Re-read `miners` every phase - migrants change the pool. Table: references.

## 10-15. Strata, water work, cave-ins, descent learnings, rates
See [references/descent-notes.md](references/descent-notes.md) - the measured detail lives there so this body stays bounded and cheap to load.

## 16. Plan in Z: one district per level, on the strata map
A shaft is not an inconvenience, it is the spine of the fort. Order per **elevation**, chosen
by what the strata allow (measured on this world, in-game numbers):
Strata table for this world: [references/descent-notes.md](references/descent-notes.md)

Per level: `4x4 landing` on the stair column -> `1-wide hall` -> `4x4 rooms` with 1-tile
openings and 2-tile walls. A level without a landing is rock with a hole in it.
Rules:
- The stairwell **head** is designated per level with `UP_DOWN_STAIR_DIG`; a level above the
  current top needs its own stair head (the farm level at 37 was opened that way).
- Gates per level, not per plan: `hazards` + `depth-profile` + `survey` on each elevation
  before ordering it. The same x/y box can be legal at 32 and fatal at 34.
- Watch the stack with `scripts/dig_levels.py` (per-level walls/fog/floor/pend); levels
  complete in whatever order the crew reaches them.

## 17. Walls are the design, not the leftover
2 tiles minimum between room and hall and between rooms; pitch >= 6 (4-tall rooms + 2 wall rows); 2 wall columns between bands; openings 1 tile **through the whole wall thickness** with a door; **diagonal contact is a connection** (dwarves, water, vermin, miasma, ambush) so separation must be corner-clean; count open area, not rooms; NO_DIG tiles you abandon. Full rules and measurements: [references/descent-notes.md](references/descent-notes.md)

## 18. A wagon road follows the surface - the hill *is* the road
Wagons refuse stairs, so a depot needs a 3-wide ramped route to the map edge. My first plan
tunnelled a corridor down through in-game 37/37 - the player's verdict: *not how DF works; a few
ramps outside and the hill connects to the plain that connects to the map edge.* The measured
surface profile east of the depot (in-game 38, y=120) says why:

Measured profile: the hill steps 38 -> 37 -> 36 east of the depot (references has it).


Method and the measured profile: [references/descent-notes.md](references/descent-notes.md).

## 19. Dig needs material; a path in the air is BUILT - and both are allowed
The player's rule: **you can dig a path, build a path, or use both at once**, and
**dig only works where there is material to remove**. My `RAMP_DIG` designations at the x68
and x73 steps were cancelled by the game because those tiles are `OpenSpace` - there was
nothing to dig. Per tile, the decision is:

| Tile at the target level | Action |
|---|---|
| wall / stone / soil (material) | **dig**: `DEFAULT_DIG`, `RAMP_DIG` to carve a ramp through it, `*_STAIR_DIG` for a shaft |
| open air, no material | **build**: `place_building(building="Ramp"/..., material:"log"/"block"/"boulder")` - a construction queued for a builder. Ramps build **on floors**: a ramp in mid-air answers `construct: nil` |
| floor / existing ramp / stairs | **nothing** - and never designate over it |

Measured payoff on this fort: the wagon road needed **zero digging** - the hill already carries a 3-wide ramp chain, proven by `/wagon-path` (1,989 tiles, reaches x=191). Detail in references.
