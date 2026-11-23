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

**Soil is a layer of its own, and it is thin.** Farm plots need a **soil floor**, and the
soil band sits under the surface - so a room dug **one level under the surface floor** lands
on soil, and two levels down you are *sometimes* still in it. The player's own words:
"sometimes soil is available, but that is more luck". Never plan a farm from the elevation
alone: read the tile class where the floor will be (`tile-at` -> `class`, or `survey`'s
`soil`/`floor` counts) and pick the columns where the dig leaves soil underfoot. Measured
2026-11-21 on this fort: at in-game 40 the walls are `SoilWall` (soil floors when dug) along
x44-x51 and `StoneWall` at x52 - one column of difference is a farmable room vs a stone one,
and cover at 40 is depth 2 under the 42 plateau, so the room is legal for both reasons.
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
`z = elev + 129` — the SAME unit as §0, one number, one source — and land **as given** on this build.
(An earlier revision of this line said `+ 128`; the server's own tool docstring repeated it, so an agent following
§2 designated one level UNDER what it aimed at, and nothing downstream could catch it. Measured
2026-11-21: a stair sent at z166 reads as Elevation 37, at z167 as Elevation 38.)

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
- **Do not pre-mine the way for a stairwell: designate the stairs into fog.** Measured
  2026-11-21 on this fort, the player's shaft at x39,y127: `UP_DOWN_STAIR_DIG` was ordered at
  in-game 38, 39, 40 - all fog at the time - and every one of them is dug: 36,37,38,39,40 read
  `MineralStairUD`/`SoilStairUD` and 41 is `SoilStairD`. The game queues stair designations and
  carves them as the tunnel advances underneath. An earlier note in this file claimed up-down
  stairs "need a walkable tile above AND below" so a shaft from below "can never start": that is
  **false for designated wall mining** (it is the rule for *constructed* stair items), and
  believing it costs a round of pointless "fixes" to a shaft that was already working.
  Design the whole shaft in fog in one pass - `DOWN_STAIR` at the top floor, `UP_DOWN_STAIR`
  through every level it crosses, `UP_STAIR` at the bottom - and let §2 (designate in fog,
  the game answers with time) do the ordering.
  alternate; they are never the same tile.
- **A main staircase is 2x2 (or 3x3), never 1-wide.** A dwarf hauling a barrel, a log or a
  stone block is slow and blocks the stair, and every trip in the fort crosses the stair -
  on a 1-wide one the whole fort queues behind him. Order the stair as a **footprint on every
  level it crosses**: this shaft is x43-44, y121-122 = 4 tiles per level, 38 down to 30.
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

### The top tile of a shaft is the one that stalls forts (measured Y100, 20 days idle)

A shaft dug from below needs a stair designation on the tile ABOVE the top stair. Use
**UP_DOWN stairs** there. A plain *down-stair* on the top tile asks the miner to stand on a
tile that does not exist yet, so the job never queues: the adit and stairway finished, `pending`
stayed 0, and the fort ran 20 in-game days with 3 idle miners while the room below the surface
never started. The fix is one of:

- designate the top tile **UP_DOWN_STAIR** (it is diggable from the stair below - measured:
  the four tiles turned into `SoilStairUD` within the day), or
- dig a **helper stair** next to it (the player's method: a second 2-wide column from the
  adit floor up, so the top level becomes reachable, then the room's designations stick).

Symptom to recognise: `to_dig 54, dug 0, pending 0` on a fog face whose stairs are finished.
That is not a queue problem - it is reachability. Adding more designations does nothing.

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

A designation count of 0 proves nothing, and the two ways it happens look identical:
**finished work vanishes** (the tile is now a floor, the designation is gone - so "my
designations disappeared" is often "they completed"), and **unreachable work vanishes** (the
game cancels designations a miner cannot path to). Distinguish them by reading the tiles:
floors/stairs = completed; rock still standing = cancelled by reachability.

A dug floor that becomes a **farm plot** is still dug: its tile type becomes `FurrowedSoil`.
Counting that as lost tile state made the observer shout "dug 54 -> 45, floors destroyed"
nine times over nine plots, and reopened a to-do that was genuinely finished.

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

## 16. Plan in Z: one district per level
Choose each level by stratum (farm at the soil layer, bedrooms above the aquifer, NO-DIG on the aquifer band, industry below it); per level the landing sits on the stair column, halls radiate, rooms along the halls. Strata table, per-level shapes, and the caveats: [references/descent-notes.md](references/descent-notes.md)

## 16b. Put the stairs in the middle - hub and spokes
A level planned as rooms hanging off a corridor that starts at the shaft wastes the shaft: the
stair is an edge, every walk crosses the level. Plan it as a **hub**: a 5x5 landing **with the
stair tiles inside it**, then halls radiating out (N/S/E/W, 1-wide), then rooms **along the
halls** - 4x4 bedrooms, 2x2 storages - each with a 1-wide opening through the full wall
thickness, 2-tile walls between rooms and between rooms and halls. Ordered outward from the hub
(nearest first) so the dug hub is the access point for every spoke, and the spokes are the
access points for the rooms. Level 36 on this fort: hub 25 + halls 51 + eight 4x4 + four 2x2
rooms + 24 opening tiles = 244 tiles, gates clean (damp 0, shallow 0, over-stuff 0).

## 17. A stall is a question about reachability, not about queue size

Measured across three embarks on this site: a face with `pending 0` and `dug` unchanged for
5+ reads is one of exactly three things - (1) it is finished (read the tiles), (2) it is
unreachable (top-stair problem, a wall in the way, a caged path), (3) the crew cannot work
(no picks, no food, no path to the site). Re-survey, read the stair column tile by tile, and
check the crew facts. Ordering more designations on a stalled face is noise: the game cancels
them and the ledger fills up with vanished counts.

**Pause when the crew is idle, unpause the moment work is queued.** A fort running with 0
pending burns in-game days (food drops, seasons pass, animals breed) for nothing; a fort
paused with work queued just wastes no time. The pause is a measurement tool: it freezes a
stall so you can read it, and it is the polite thing to do while you think.

## §19 - what `dig-reachable` does and does not decide

Measured 2026-11-22: the oracle called `(68,116)@37` and `@36` **unreachable** from the dwarves'
surface tile. I read that as "the game will not work it" - and the game built both stairs
(`class=stairs_down`, `class=stairs_ud`) and queued the box (pending 44 -> 41 -> 18, `dug 5 -> 7`).

* `reachable: false` = **no walkable neighbour in the computed graph**. It is a pre-flight warning
  about connectivity, and it does NOT model digging from the tile above (which is how shafts, stair
  wells and room tops get dug). Stair designations under an open tile are workable.
* `pending_dig` read **while the game is paused** proves nothing. A stalled face is proven by
  `dug` not moving **across running reads** on a named box - never by one paused sample.

## §20 - "how many miners do I have" has three answers, and only one digs

Measured 2026-11-22 with the player's Labor screen open:

| layer | tool | answer |
|---|---|---|
| The **profession roster** (the tick marks on the Miners page) | `GET /unit-professions` | 1 Miner (then 2 after `action=set`) |
| The **labor flags** per unit | `GET /unit-labors?labor=MINE` | 4 (then 5) |
| The **plugin's `miners` metric** | `df_status` | profession-based |

Rules:
1. A profession write does **not** grant labors (the new Miner came back `MINE: false`), and a
   labor write does not change the roster. Two writes, two read-backs.
2. `dfhack.units.setProfession` and a profession RPC do not exist on this build; the write is
   `u.profession = pid`, and the pid is taken from a dwarf already in that profession.
3. `/set-unit-labors` answers `applied: N`, which validates **names**, not the game's state -
   read `/unit-labors` after it.

## §21 - never take the only holder of a craft (I did, and it cost the fort its smith)

Measured with the player's screenshot: `/unit-professions action=set unit_id=6491 profession=Miner`
worked - the game's own roster shows `Åblel Rakustàlil, Miner` - and **the fort has no metalsmith
now**. Åblel was the only Metalcrafter. Rung #1 ("two dwarves on Miners") was satisfied; the price
is the craft that makes metal, which is what the well (mechanisms), buckets, anvil and the furnace
chain all need downstream.

Rules:
1. **Read the roster for uniqueness first** (`GET /unit-professions` lists every citizen's
   profession). If a craft has exactly one holder, do not convert them - find a dwarf with no
   load-bearing craft (an expedition leader, a title, not a trade).
2. Restoring a profession the fort no longer has is **not reachable from the tools on this build**:
   the profession table is unreadable (see `tool_missing.md` #32). Use `profession_id` when the id is
   known from a unit that carries it, otherwise the player's UI is the route.
3. Labors are not professions: 5 citizens have `MINE` and the digging proceeds regardless of who is
   labelled Miner - so this rung rarely needed the profession write at all.

## §22 - a reachability tool that builds its graph from the wrong band is worse than no tool

Measured 2026-11-22: `/dig-reachable` answered `reachable: False` for **floors the dwarves
standing on** - the walk graph was empty because `fort_band()` scored unit density over *all*
units, and deep wildlife (~90 units at RPC z 11-26) out-scored the fort's 17 citizens at z 167,
landing the band at elevations **-125..-114**. The band is the map window the graph is built from;
when it misses the fort, every connectivity answer becomes a false negative.

Rules:
1. **An empty graph is NO DATA, never "unreachable".** `reach_map` now reports `tiles` and
   `band_source` (and retries around the player's view); if `tiles == 0`, the answer is a read
   failure - do not plan from it, do not tell the plugin a designation was cancelled.
2. The fort is where **civilian dwarves** stand (non-hostile, non-caged, race contains "dwarf"),
   weighted against the player's view - wildlife density is not fort position.
3. Trust the map over my tool: connectivity claims about walkable floors, soil patches and stair
   links get re-measured after any change to the band logic (measured after the fix: 8 components /
   25,869 tiles, and the soil patch `(90,106)@36` is component 1 - walkable from the fort).
