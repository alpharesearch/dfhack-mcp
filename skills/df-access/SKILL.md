---
name: df-access
description: The access-first build order - staircase, connection tunnel, then room - plus staffing by work detail and the measurements that prove a dig is real, on any map.
whenToUse: Use when a designated room or tunnel makes no progress (dug stays 0), when a fort has no dug floors yet, when staffing "miners/hunters/brewers" has to change, or when a stall alert claims a face is idle. Generic: applies to any embark, any elevation, any world.
metadata:
  source: demonstrated live by the player on DF 53.17-r1 and measured from the game's own reads (see dfhack-mcp tool_missing.md #19-#48)
  tools: send_dig_command, survey, tile_at, hazards, depth_profile, transitions, work_details, unit_labors, reach_map, dig_reachable, create_stockpile, configure_stockpile, place_building, fort_progress
---

# Access first: staircase, connection, room

The rule that decides whether a designation becomes work is **adjacency to ground a dwarf can
stand on** - not the size of the plan. Measure it, and the build order follows by itself.

## 1. Sink the main staircase before designing any room

- **Do not sink a 1-wide shaft.** The player's rule (2026-11-22): the main staircase is **2x2,
  preferably 3x3** on every level it passes. A wide staircase digs faster (more miners work at
  once), carries traffic without blocking, and the stair tiles form a room you can build in;
  a 1-wide shaft is the bottleneck that makes every later face queue at one dwarf's pace.
- Pick the column(s). Designate **every elevation the shaft passes**:
  - top (under the walkable surface): `DOWN_STAIR_DIG`
  - each middle level: `UP_DOWN_STAIR_DIG`
  - the bottom level: `UP_STAIR_DIG`
- Do not skip a level: a designation on a level whose above-neighbour is solid rock is unworkable,
  and the game will queue nothing there.
- Gate as usual: `hazards` (damp / magma-below / obsidian) and `depth_profile` (keep the cover you
  want over the tunnel; the wiki's "mountain on top" for the entrance means >= 2 levels).
- A stair can be dug from above (the miner stands on the walkable tile over it). A **plain dig
  cannot** - under a floor, the only legal continuation is a stair.

## 2. Cut the connection tunnel from the staircase toward the future room

- Designate the run on the target level, **middle-first** from the staircase outward, so each dug
  tile is the access for the next one.
- Extend only where `/survey` shows rock beside existing floor. Rock *beside* dug floor is
  workable; rock under floor is not; rock surrounded by fog is not.
- Verify with `transitions` (fog/wall -> floor) and the box's `dug` count, never with a queue
  number: a "pending designations" figure is a **count of designations**, not a count of jobs.

## 3. Room last

Designate the room once the tunnel reaches it: the room's edge tiles then have a walkable
neighbour, and the whole face queues. Evidence of the order working, on a fresh embark: a room
designated first sat at `dug 0` for 95+ reads, while the staircase moved stone 18 -> 29 -> 70.

## 4. Staff by work detail (the green tick), not by flags

- `work_details action=list` shows each detail's `allowed_labors`, its **mode**, and its
  `assigned_units`.
- Assign with `work_details action=assign detail=<name|index|labor> unit_id=<id> mode=add`. The
  effective flags must follow in the same call's read-back: `unit_labors labor=<LABOR>` is the
  census that proves it (miners, hunters, fishers...).
- Two game facts make this work, and both were measured, not assumed:
  - `assigned_units` is a **sorted** vector - keep it sorted and deduplicated, or the game's
    binary search misses the unit;
  - a detail's **mode** decides membership meaning: everybody / nobody / only-selected. A dwarf
    ticked into a detail that is in "everybody" mode changes nothing; the write must set
    "only-selected" (as the in-game Labour screen does).
  - the flag layer is recomputed by the game's own call (`setAutomaticProfessions`); a write to the
    flags themselves is not durable, because the game recomputes them from the details.
- Never take the only holder of a load-bearing craft (the sole mason, metalcrafter, woodcutter):
  converting that dwarf's role costs the fort the craft. Add labours; do not strip roles.

## 5. Prove the dig, do not narrate it

| Claim | Proof |
|---|---|
| a face is progressing | `survey` `floor` count rising, `transitions` rows, `stone` rising |
| a face is stalled | `dug` unchanged over several reads **and** `already_designated > 0` (if it is 0, the face does not exist - fog counted as work) |
| a designation landed | `tile_at` shows the designation on the tile; `survey` shows it in the box |
| a building landed | the game's own building list, not the cached block census (censuses lag) |
| a pile accepts the right goods | the configure verdict's live `accepts` list |
| dwarves are staffed | the labor census count, not the write's success flag |

## 6. Generic gates that apply on any map

1. Fog is not ground: a site gate must reject `hidden`, `rock`, `wall`, `open`, `water` for
   buildings and piles; drop to a smaller footprint that is fully clean rather than forcing one.
2. A connectivity answer is valid only inside the elevation band the graph was built from: make the
   band cover seed **and** target, and self-check the seed (`dig_reachable` from a tile to itself
   must return reachable; if it does not, the seed is not a measurement).
3. Piles are the fort's circulatory system: place the general pile (all except corpses, refuse,
   stone, gems, wood, blocks) near the entrance, a wood-only pile near the road, corpses+refuse
   outdoors - then, and only then, workshop chains work.
4. Keep the game running while `dug` rises; pause only when no job is being consumed, and pause
   the *panel*, not the world, when you want quiet.

## 7. Workshop orders: an order is not work

Measured 2026-11-22 on the door/charcoal/smelting chain: `work_order action=order` succeeded and the
order count rose, yet every order read `active: false, validated: false, workshop_id: -1` with
"Make unknown material door". Quote the list, not the success flag: an order needs its materials and a
workshop before any dwarf can run it (tool_missing #50). A manager must exist first - `has_manager` in
the same reply tells you, and nobles are appointed through the work-detail/labour machinery, not the
removed flag write.
