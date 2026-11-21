# Descent notes - measured detail for df-dig

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

## Mining output is a logistics problem, not a bonus (measured at elev 36, in-game 35)
A 110-room dig produced, in place on the dig site:
`BOULDER/GYPSUM 15, MAGNETITE 7, LIMESTONE 6, SANDSTONE 5, GALENA 3, BORAX 2, COAL 1,
ROUGH/PINK TOURMALINE 5, ROUGH/SARD 3` and ladder `stone 91`. Consequences:
1. **Boulders lie in the corridors** and must be cleared (mine them) or they block hauling.
   There is no remove-boulder kind in this RPC enum - the fix is a typed op (`remove_boulder`
   designation bit). Until then, treat boulder tiles as part of the dig plan, not scenery.
2. **Stone accumulates where you dig.** A stone stockpile near the dig face (and later
   wheelbarrows, Max wheelbarrows=3) is what keeps miners from hauling half the mountain by
   hand. The general stockpile must EXCLUDE stone/corpses/refuse/wood/gems, and a dedicated
   stone stockpile accepts it.
3. **Vein names are the economy, read from the tile/item names**: `MAGNETITE` = iron ore,
   `COAL_BITUMINOUS` = fuel, `GALENA` = lead, `BORAX`, `GYPSUM` = flux, gem `ROUGH/*` =
   jewelry/cutting. Our shaft passed into the mineral band at elev 36 (`MineralStairUD`) and
   that band is what produced these - the smelter chain (furnace + fuel + flux) becomes
   reachable from here.
4. **Gem rough is loose and gets lost in miasma/hauling** - a gem stockpile is cheap insurance.
5. Digging a 2,075-tile plan with 6 pick-carrying miners is **hours of game time**, and the
   box reads mostly fog until the miners get to it (`walls 29 | fog 944 | floors+stairs 52 |
   pending 2075`). That is normal - the reveal lags the plan. Do not order more dig than the
   crew can finish, and do not panic at a large pending count: it is a queue, not a failure.

## Order space, not tiles: subtract what is already dug (measured)
My "110 room grid" at elev 36 (in-game 35) overlapped **the room and tunnel I had already
dug on that same level**: of 2,240 grid tiles, **51 were already floor/stair** and 2,189 were
rock. Ordering a grid blindly therefore queued 51 useless dig designations (cleared after the
fact with NO_DIG) and inflated the room count.

Rules this adds:
1. **Before ordering a complex, subtract existing dug space** (floor/stair tiles at that
   elevation, from transitions) **and existing buildings** (stockpiles, furniture) - designating
   on a floor is wasted labour and designating on a building tile is meaningless.
2. **Count in rooms, not tiles.** Report "N room slots, M of them overlapping existing space",
   because that is the unit the player sees and the unit the ladder measures.
3. **A level accumulates plans.** Every earlier design on that elevation is part of the
   terrain now - the same lesson as "your copy landed on top of my example", generalized:
   mirroring, extending and gridding must all read the current dug footprint first.

## Diagonal contact is a connection (measured, and it invalidated my separation)
The player's rule: *when tiles diagonally touch, things can move between these rooms*. In DF
units move diagonally, and so do water, vermin, miasma and ambush - a corner touch is an open
door for everything except a wagons' path. Proof: rebuilding the reach graph with the four
diagonal neighbours merged my "separated" components from **63 down to 17** on the same map.

Consequences for design and tools:
1. **Separation requires no corner contact.** Two open areas whose tiles touch diagonally are
   one space. Thick walls buy separation only if the wall is also diagonal-clean (a 1-tile wall
   column between two open rows leaks at the corners where either side is open).
2. **The graph must use 8-neighbour links** (plus stair/ramp z-links). My 4-neighbour version
   under-connected the fort - so `dig_reachable` was too optimistic and "is this room cut off"
   was answered wrong.
3. **Openings must traverse the entire wall thickness.** My 2-tile-wall redesign put the opening
   tile in the outer wall row only, leaving an inner wall tile - sealed rooms. Fixed by ordering
   both rows (32 tiles across four levels), and doors go in the 1-wide passage.
4. Water is the worst case of this rule: a diagonal gap into an aquifer-touched area is a leak,
   not a technicality.

## Verification after a write needs a FRESH read path (measured)
Sent 1,592 `NO_DIG` clears map-wide; my verification read 1,592 designations "remaining" -
because `cache.blocks(lo, hi, gen)` was keyed on a generation that **does not change when
designations are written**. The RFR generation moves for big state changes, not for every
designation, so a cache keyed on it serves pre-write data.

Proof of the fix: after `world-cache-reset` **and a new WorldModel instance**, the full-map
scan read 0 designations, and the server's `world-objects` showed no dig clusters on any
elevation. Rules:
1. After ordering/clearing designations, verify with a fresh model (or a cache keyed on a
   mutation counter the server bumps itself), not the same cached window.
2. A "0 remaining" from a stale cache is as dangerous as a "complete" from designation counts -
   both are absence read from the wrong snapshot.
3. Cheap cross-check: `world-objects` per elevation reports dig clusters; if it lists none,
   the dig queue is empty regardless of what a cached array says.

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
