---
name: df-dig
description: Dig tunnels, rooms and stair shafts in Dwarf Fortress with real labour - depth under the surface, damp/hot hazards, designating into fog, and proving the dig actually happened.
whenToUse: Use when planning or executing any mining - entrance tunnels, rooms, stairwells, farm/industry halls - and whenever a dig "completed" but nothing appears, a designation vanished, or a plan sits in unexplored rock.
metadata:
  source: measured on DF 53.17-r1 (STEAM) with the dfhack-mcp layer, during play tests on region1
  tools: fort_strata, fort_plan, send_dig_command, survey, world_map, work_details, building_status
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
`/work-details action=assign detail=Miners unit_id=... mode=add` grants **`MINE`** through the detail (not `MINING`; the enum has `MINE`, `CUTWOOD`,
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
1. A profession write does **not** grant labors (the new Miner came back `MINE: false`), and detail
   membership does not by itself rebuild the flags - the game recomputes them on its own path. Read
   `/unit-labors` after every staffing change, and expect the census to be the authority.
2. `dfhack.units.setProfession` and a profession RPC do not exist on this build; the write is
   `u.profession = pid`, and the pid is taken from a dwarf already in that profession.
3. The labor-flag write (`/set-unit-labors`) is **removed**: `applied: N` validated names, not the
   game's state, and the game recomputes the flags from the details. Staff with `/work-details`,
   verify with `/unit-labors`.

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

## §23 - digging one level below the surface CREATES soil (the player's rule; my census missed it)

A dug floor takes the material of the layer above it. So on a map whose only soil is the surface
layer, **a room dug one level under the soil surface has a soil floor** - and that is where a farm
plot belongs. My census reported *existing* soil tiles, concluded "there is no underground soil on
this world", and put the farm on the bare surface; the conclusion was an artifact of asking the
wrong question. Existing soil is not the farmable set; **soil-coverable space** is.

Rules:
1. The farmable set at elevation `e` = tiles at `e` whose tile at `e+1` is soil/earth. `GET /find-soil`
   reports this as `potential_soil_clusters` (`soil_tiles` alone is the surface, which is not the answer).
2. Depth math: surface at 36 → farm room at **35**, with cover ≥1 from the soil layer and the ≥2 rule
   satisfied by the soil above plus the layer over it - gate with `/depth-profile` and `/hazards`
   before designating, and keep the box middle-first.
3. Do not place a farm plot on open-sky surface soil as a design choice. It grows (measured: plot id 6
   at (90,106)@36 came up complete with plump helmets in all four seasons), but the quickstart's room
   is underground, and a surface plot is exposed to frost and visitors.

## §24 - before believing a reachability answer, check the seed and the band

Measured 2026-11-22 (the forest at elev 43, the fort at 36-38):

1. **The band must cover the target.** `/dig-reachable` used to build its graph on the default fort
   band (12 levels, elev 28-39), so elev-43 tiles answered `reachable: False` by construction
   (tool_missing #42, fixed: the band now spans seed and target).
2. **The seed must be a tile the graph accepts.** `tile-at` and the graph disagree about some
   classes (`open` at (70,118)@38 is `walkable=True` to the decoder and "not walkable" to the
   graph; `ramp_top` likewise). Self-test the seed: `dig-reachable(x,y,elev,from=x,y,elev)` must
   return True. `self-reachable=False` means the answer is not a measurement (tool_missing #43).
3. **`/reach-map` takes `e0`/`e1`** - pass `elev0`/`elev1` and you silently get the default band.
4. A high plain is its own component (measured: 4,674 tiles at elev 42 as comp 12). Trees up there
   need a ramp/stair route from the fort's ground before `cut_trees` designations can be worked -
   which is exactly why wood stayed 0 with 3-5 trunks designated.

## §25 - the designation summary is not the designation truth

Measured 2026-11-22: a 63-tile strip I queued at elev 35 reads back as `already_designated: 63` in
`/survey` and `DEFAULT_DIG` in every `/tile-at` probe, while the `/observe` summary in the same window
said `designated_total: 4` (tool_missing #45). So:

- After a designation write, verify with `/survey` on the box (and `/tile-at` on a few tiles), not
  with the summary count.
- A stall alert quoting `pending 0` with a suspiciously small `designated_total` is a *scan* question
  first: check the box, then the access (stair vs plain dig from above, §19), then the labour census
  (§20) - in that order.

## §26 - a stall alert on a box you never designated is about fog, not your plan

`to_dig` in the observer's box row counts **unexplored tiles** as well as designated ones (tool_missing #47:
`x57-63 y113-119 elev36` reported `to_dig 49, dug 0` six times, and `/survey` on that exact box answered
`{"hidden": 49}` with nothing designated). So before acting on a stall:

1. `/survey` the box. If `already_designated` is 0, nothing is stalled - the face simply does not exist yet.
2. If it is designated, check the access rule (§19): plain digs need a walkable neighbour; under a floor it
   must be a stair. Rock *beside* dug floor is workable - that is how a tunnel extends (measured: queuing
   `x74-76 y114-118` + `x68-69 y114-118 @36` beside the dug band took `pending` 66 → 75 and floors 26 → 35).

## §27 - the player's staircase-then-tunnel pattern (demonstrated live 2026-11-22)

Watched the player designate it in-game, measured from the tile reads:

1. **Main staircase first, over many levels.** One column, `UP_DOWN_STAIR_DIG` on **every** level the
   shaft passes (measured at (36,119): elev 37,36,35,34,33) and **`UP_STAIR_DIG` at the bottom**
   (elev 32). The top tile stays undesignated while it is solid wall above the surface line.
2. **Then the connection tunnel** - a horizontal run off the staircase into the fort, designated
   beside dug/standing ground so every tile has a walkable neighbour (§19).
3. Why this beats my first attempt: I designated a wide room first (63 tiles at elev 35) and a stair
   under a floor. A room in fog has no walkable neighbour, so the game queued nothing - `dug 0` over
   95 reads. The staircase creates the connection; the tunnel carries it; the room gets dug last.

Measured proof that it works: with the staircase running, `stone 18 -> 29` and designations were
consumed (`pending` moving), where the fog room alone moved nothing for 12+ minutes.

## §28 - Underground CAD & Quickfort workflow (32-tool MCP surface)

- **`fort_strata` replaces the pruned micro-probes** (`overburden`, `depth_profile`, `column`, `cross_section`, `hazards`, `magma_depth`, `find_soil` remain HTTP-only; see `docs/tool-notes.md`): scan once to get `soil_walls`, `potential_soil`, `stone_walls`, `aquifer_tiles`, `water_tiles`, `magma_tiles`, and covered underground (`open_space == 0`) recommendations for `farm_elev`, `workshop_elev`, and `bedroom_elev`.
- **Plan and commit with `fort_plan`**:
  - `set_hub` (`radius=1`) creates a 2x2 central shaft inside a 3x3 hub.
  - Start small in early game with `add_template(template="bootstrap_camp")` (~47 tiles) so miners finish quickly instead of queueing an 800-tile Quickfort dig on day 1.
  - `commit(phase="auto")` runs `shaft` -> `hub_and_corridors` (hub-attached corridors/halls only, zero disconnected child doorways) -> `rooms` (topological parent-before-child).
  - Use `action="find_site"` to locate clean, dry, already-dug footprints (`pending_dig_tiles` tells you if miners are still excavating).
  - Use `qf_status`, `qf_list`, `qf_run`, and `qf_orders` for DFHack Quickfort blueprints once the bootstrap fort (workshops + Chair + Office + Manager + `buildingplan`) is ready.
