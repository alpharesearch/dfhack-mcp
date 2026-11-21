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
