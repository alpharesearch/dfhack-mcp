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

## 2. Designate in fog; the game answers with time
Unexplored tiles **accept** designations (measured: 3 tiles at `hidden=True` came back
`tiles: 3, success: true`). So order the whole plan - tunnel, room, shaft - and let the
dwarves dig it, revealing the map as they go. Do not gate designations on visibility or
reachability; use those to *explain* a stall.

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

## 10. Designation kinds, what they are for, and their acceptance facts
| Kind (`m`-menu) | Use | Measured behaviour |
|---|---|---|
| `DEFAULT_DIG` | tunnels, rooms, adits | accepted in fog; 37/57 then 21/21 accepted on this map; unexplored ones land and queue |
| `UP_DOWN_STAIR_DIG` | shafts through a level | accepted 4/4 and 2/2; digs `StoneStairUD` and reveals the level below |
| `DOWN_STAIR_DIG` / `UP_STAIR_DIG` | shaft top / bottom | top = down-stairs, bottom = up-stairs, middle = up-down |
| `RAMP_DIG` | slopes: wagon paths, water work, stair-adjacent floors | needed for any 3-wide wagon route; ramps link z to z+1 at the same column |
| `CHANNEL_DIG` | 3x3 pit: entrance cut, cistern, drop shafts | channel = open above and below; the ladder suggests it for a flat-ground entrance |
| `NO_DIG` | clear a box | the fix for a damp line (cleared 152 tiles) and for designations on the wagon (30) |

There is **no remove-boulder kind** in this RPC enum: `SendDigCommand` exposes only the
seven above. Boulder removal is a separate designation bit (`remove_boulder`), so it needs
a typed op (planned) - meanwhile boulders come to you as **mining output**: digging the
elev-39 band yielded 8 boulders, and `stone` climbed 0 -> 8 -> 17 -> 28 -> 53 as digging
proceeded. Expect a stone stockpile and a mason, not a quarry designation.

## 11. Reading strata from the dig (geology you can trust)
- Wall/floor **names are the stratum**: `StoneWall`/`StoneFloor*` = common stone, `MineralWall`/
  `MineralStairUD` = **mineral vein band** (our shaft passed into minerals at elev 36 - that
  is your future smelting feed), `SoilFloor*`/`GrassDryFloor*` = soil/grass (farm plots),
  `MFloor*`/`MineralFloor*` = mineral floor.
- Material counts after digging tell you the yield; `BOULDER/SANDSTONE`-type rollups in
  `world-objects` are the boulder ledger.
- The **aquifer band** is a stratum fact (elev 33-34 here), the **surface elevation** is a
  per-column fact (`surface_map`), and the two together decide where a legal tunnel level is.

## 12. Water work (the flood you must plan around)
- Rivers/lakes appear as standing `water` at the surface (this map: elev 35-36, x54-186 -
  **east of** our fort; tunnelling west is safe, tunnelling east into it is not).
- `hazards` counts a tile DAMP when its column carries aquifer or water at/above the dig
  level; that is the game's designation-time warning, tile by tile.
- Rules: never breach water from above (channel into it, and the fort floods); a cistern is
  `CHANNEL_DIG` under water; pumps/mechanisms are a much longer chain (metal + mechanics),
  so **route around** first, then under/over deliberately.
- After any water breach, `hazards` and `world-map` show `~` spreading - treat new water
  tiles as an incident, not scenery.

## 13. Cave-ins and support - NOT yet verified here
Large open mined areas can collapse; the usual player mitigation is support (a pillar, a
wall, built floors) inside big halls. **We have not measured the threshold on this build**,
so: keep rooms at the sizes the ladder recommends (6x6, 5x5 soil room), leave a support
tile in larger halls, and test the rule before trusting it. Do not cite a number you have
not observed.

## 14. Sequence for a new fort (the ladder, in dig terms)
1. Entrance: 3-wide tunnel with depth >=2, dry, from the wagon side - `tunnel-sites`, then
   designate in fog.
2. **Stairwell ALL the way down to the level you intend to work, dug complete** - every
   crossed z designated (see §4). No room complex is ordered above a stairwell that does
   not reach it.
3. General stockpile (exclude corpses/refuse/stone/gems/wood) in the first room.
4. Meeting area on the 2nd level below ground (zone, 5x5-7x7) - not at the entrance.
5. Refuse pile OUTSIDE on the surface (miasma rule).
6. Farm: 5x5 room on SOIL (3x3 plot) - find soil by the stratum names, depth >=2.
7. Workshops/furniture need a stone+wood economy: hauling routes, then mason/carpenter.
Each step ends with a **re-survey**: `world-cache-reset` + `depth-profile` + `hazards` on
the new box, because the dig revealed ground you can now plan on.

## 15. Learnings from the 9-level descent (measured this run)
- **Data absence is not open air.** My own layer read `band.get(...).get(e, " ")`, so a tile
  with no cached data reported as a void: the descent stopped at elev 31 announcing a cavern
  that did not exist (the dwarves had dug `MineralStairUD` there). Rule: **unknown is its own
  state**; any default that turns "no data" into a positive fact is a bug that invents world
  features. Same class as the earlier traps (count 0 = dug, zero walls = tunnel).
- **The cached window must cover the range you ask about.** `probe-plan` built its stack from
  the fort band (32-43), so everything below 32 read as fog. Ask for 25, fetch 25.
- **Up-down stairs advance the shaft by one level per designation:** digging an
  `UP_DOWN_STAIR_DIG` at level L yields the stair at L **and** the corresponding stair tile on
  L-1, so the level below often shows stairs before you order it. Tile truth per level:
  `StoneStairUD` / `MineralStairUD` (the vein band at elev 36 gave mineral stairs).
- **Crossing the aquifer (elev 33-34 here) has been dry so far** - water 0 at every stair tile
  after the breach. That is not a promise: `column_water()` checks the shaft column before and
  after each level and **exit 4 = WET, stop** (the player's rule: *just stop when wet*). Log
  the damp *flags*, stop on *water*.
- Descent rates: 2 stair tiles per level, ~15-30 s per level with the crew on it - 9 levels in
  a few minutes. Stairwells are cheap; rooms are the expensive part.
