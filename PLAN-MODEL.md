# PLAN-MODEL.md — how the agent should hold a 3D model of a fogged dwarf fortress

Motivated by the player's question: *"the whole mountain is unexplored — are there
techniques that would help you better with building your own 3D model of the game world?"*
Everything below is driven by what we measured on 53.17-r1, not by how the API is
documented to work.

## The core constraint (measured, not assumed)
- **Fog is total underground.** At elev 36 (in-game 35) the only explored tiles are the
  shaft and its immediate neighbours; `x48` is `?` and a designation there is **silently
  rejected**. You cannot plan a tunnel shape you cannot see.
- **There is no connectivity oracle.** `dfhack.maps.getWalkableGroup` is **uncallable** from
  the RPC lua channel (`Cannot write field ... complex object`), `dfcoord` does not exist,
  and `canWalkBetween` **throws** for two adjacent stair tiles and answers *false* for two
  grass tiles 15 apart on the same level. Reachability is ours to compute.
- **Unexplored ≠ blocked.** Planning must distinguish "solid rock, unexplored" from
  "impassable". Confusing them is what makes agents dig in the air.
- **Counts lie, tiles don't.** Designation count → 0 happens on fulfilment *and* on
  cancellation; RFR reports explored tiles only, so "no magma found" from fog proves
  nothing; natural cavern floors satisfy "underground floors". Only tile state is evidence.

## Technique 1 — a persistent tile store (the model itself)
RFR gives windows, not a world. Mirror it into a local store (sqlite is enough, `sqlite-vec`
later for retrieval):

```
tile(x, y, z, class, glyph, water, magma, aquifer, hidden, designation,
     construction, building_id, item_count, generation,
     first_seen, last_changed)
tile_event(x, y, z, from_class, to_class, at, kind)   -- 'dug', 'built', 'revealed', 'flooded'
facts(key, value, at)                                   -- FortFacts history
```

What the store buys, and none of it costs an RPC sweep:
- **Transitions are the ground truth of play**: `wall → floor` = dug (this is the
  `dug_floors` the ladder needs, and the only honest proof a dig completed);
  `wall → stairs` = shaft built; appearance of water where none was = flood event.
- **History per elevation** — the ladder, the progress reports and "what changed since
  dinner" all become queries.
- **Cheap 3D questions**: overburden, depth, roof, column, cross-section, hazard-in-column,
  adjacency, all indexed lookups on `(x,y,z)`.

## Technique 2 — delta-driven ingestion (never re-read the world)
- Poll on a schedule (background job, not model turns) and apply **only changed tiles**:
  diff the cached band stack, log transitions, update the store.
- Bound the reads: 12-level windows (the RPC reply-size limit), band-scoped, cache keyed by
  generation; `world_cache_reset` only when the game changed materially (a save reload).
- The game-side announcer (planned: `repeat` + lua → `showAnnouncement`) pushes events —
  caravan, wave, birth, death, flood — into `get_reports`, so the model receives news
  instead of hunting for it.

## Technique 3 — fog as a state, with inference, not as absence
- Every tile is `explored | fog`. Fog tiles are **never** asserted in plans, and every
  derived report carries `unexplored` counts (implemented in `depth-profile`,
  `tunnel-sites`, `probe-plan`).
- **Learn geology from dug tiles**: a histogram of material class per elevation, the
  aquifer band elevation, observed vein/mineral tiles, the local surface elevation. Then
  extrapolate with a confidence tag: "predicted solid rock at elev 34-36 with depth ≥2,
  confidence = learned from 12 dug tiles at these elevations". A player does exactly this.
- **Worldgen knowledge is available in fog** and must be used: region biome flags and
  `getGlobalInitFeature` (volcano/magma pipes) answer "is there a hot hazard here"
  without visibility. On this map: no volcano, aquifer band at elev 33-34.
- **The reveal frontier**: a dig reveals the tiles its miners see. Model it — after
  digging column `x`, `x-1` becomes designatable *when a dwarf has looked that way*.
  `dig_adit.py` waits for the reveal (`--reveal-timeout`) instead of treating lag as
  failure, because the reveal lags the dig by ticks.

## Technique 4 — our own reachability layer (built, measured)
- 3D flood fill over explored walkable glyphs: `.` floors, `=` stairs, `/` ramps,
  `g/s/S` surfaces; 4-neighbour links per level, `z±1` links on stairs and ramps.
  Result on the live map: **34,034 walkable tiles in 63 components, computed in 0.9 s**.
- `dig_reachable(x,y,elev, from_*)`: a dig is workable iff a walkable neighbour (same level,
  or via a stair/ramp link) lies in the dwarves' component. This is what catches the
  failure mode we hit twice: **a 3-wide designation whose flanks have no access is
  cancelled silently.**
- Consequence for ordering: **dig the middle of a band first**, then the flanks — each dug
  tile becomes the access point for the next. Implemented in `dig_adit.py`.
- Component reports (`reach-map`) expose cut-off pockets: a stockpile/depot in a component
  the dwarves cannot reach is decoration, which is exactly how my first depot failed.

## Technique 5 — multi-resolution, always
1. **Block summary** (16×16×1) for the whole map: what exists where, at ~1 glyph per block.
2. **Band grid** (the fort's elevations): per-tile glyphs, regions, dig clusters.
3. **Tile detail** only where explored and active.
4. **Derived maps** (precomputed, cached per generation): surface elevation, depth,
   hazards (aquifer/water/magma), reachability components, dug transitions.
Every projection prints the in-game elevation (`ui` = z−129) next to ours, plus
`unexplored` counts, so agent and player never talk past each other.

## Technique 6 — the discipline that makes the model trustworthy
- **Plans carry fallbacks** (2-3 ranked sites, `probe-plan` + `tunnel-sites`), never one
  shape designed inside fog.
- **A dig is a loop, not a batch**: probe → designate → (labour) → watch → re-survey →
  extend. `sink_shaft.py` and `dig_adit.py` are that loop, per level/column.
- **Verify by transition, report by evidence**: dug = `wall→floor`; built = `findAtTile`
  + `isActive`; never "the count went to zero".
- **Labour before time**: check `miners`/`CUTWOOD`/`BUILD_CONSTRUCTION` each phase (the
  pool drifts as migrants arrive — miners fell to 2 of 14).
- **Waits are background jobs**, never model turns (the watcher scripts).

## Next build order
1. `worldstore.py` (sqlite) + transition logging in the refresh path → `dug_floors`,
   `dug_stairs`, flood detection, honest ladder rungs.
2. `mirror_structure` (read a dug footprint, re-place it with clearance from existing
   structures, validate per part, order designations middle-first).
3. Geology inference + `survey` (accepted/rejected counts with reasons: fog, damp, hot,
   no-access, water, slope) as the gate before any designation.
4. Game-side announcer script in `deployables/scripts/` feeding `get_reports`.
5. Then the deferred RAG phase — with the store, embedding tiles/events is trivial.

## Correction from the player (measured right after): designations are not gated by fog
The player's rule: **"you should be able to designate anywhere; whether the dwarves can dig
or build is answered by the game when time comes."** Tested: `DEFAULT_DIG` on
`x48,y122-124,elev36` - all three tiles `hidden=True` - came back `tiles: 3, success: true`,
and `world-objects` shows `dig 3 tiles x48-48 y122-124`. So designation in fog **works**.

Consequences, all implemented in `scripts/dig_run.py`:
1. **Order the plan, then measure.** No visibility gate, no reachability gate, no probed-roof
   gate before designating. `dig_adit.py`'s fog refusal (`exit 3: unexplored`) was an
   *agent-side* obstacle - the tunnel stopped at x48 for no game reason.
2. **Progress = wall tiles remaining** in the box (tile transitions). Designation count is a
   *queue*, not progress: pending designations mean "the game has not made this reachable
   yet", and a box that stops shrinking with nothing pending means the game cancelled the
   rest (no access / invalid site / hazard). Both are reported as facts.
3. Middle rows first in a band (courtesy to pathfinding), flanks after - ordering, not gating.
4. Reachability (`dig_reachable`, `reach-map`) stays in the model as an **explanation** tool
   for why a plan stalls - not as a precondition that blocks design.
5. `tile_at` does not surface `dig_designation` on hidden tiles (the designation is recorded -
   `world-objects` reports it), so designation state on fog tiles must be read from
   `world-objects` clusters, not per tile. Fix the tile-level read in the store design.

### Absence of walls is not the presence of a tunnel (second fog trap)
`dig_run.py` first version announced `DUG COMPLETE after 0s` for the elev-36 box: it counted
`#` glyphs, and fog tiles are `?` - **a box of unexplored rock has zero walls**. Completion
now requires `walls == 0 AND fog == 0`, and reports the four counts
`walls | fog | floors+stairs | pending` every change.

General rule for the model: every "is it done?" question is answered by a **positive tile
state** (floors/stairs present), never by the absence of the pre-dig state. The same trap
caught me twice (designation count → 0; wall count → 0), and both times the fog was the
thing that made absence look like success.
