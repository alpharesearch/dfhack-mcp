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

## 4. Stairs and shafts
A shaft is **one revealed level at a time**: designate up-down stairs on the current level,
dig it, then the tile below is revealed - designate that, and so on. Designating every z in
one batch works in fog (see §2), but each level's stair must be its own designation kind:
top = down-stairs, middle = up-down, bottom = up-stairs. `scripts/sink_shaft.py` runs this
loop and stops with a reason if a revealed level is damp, open, or fog.

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
