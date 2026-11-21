# MCP tool plan

Planning doc: which tools the MCP should expose, which should not be in the tool list,
and what is missing. Grounded in measurements from this session (live game, 53.17-r1,
DF 53.17 STEAM) and the API inventory below. It is a plan, not a spec: verdicts are
argued, phases are ordered by how many Quickstart rungs they unblock.

## Principles

1. **Close the loop, do not grow the surface.** Play is perceive → decide → act → verify.
   A tool earns its place by closing part of that loop in a bounded number of tokens.
2. **The model gets typed verbs, never raw APIs.** No Lua, no protobufs, no free-form
   command strings in the tool list. One bad field write to `df.global.world` corrupts a
   save, and a small local model cannot enumerate an API it has never seen.
3. **Token cost is a first-class property.** Measured: one raw z-level of `get_block_list`
   = **196,361 tokens**; the whole-fort briefing `look` = **1,205**. Anything that can emit
   a 100k-token answer must not be a tool.
4. **Verify in the channel that acted.** RemoteFortressReader is not a trustworthy verifier
   of fresh writes (twice measured: a building present via `findAtTile`, absent from
   `GetBlockList`, unchanged by `ResetMapHashes`). Typed ops self-verify.
5. **Console output is a tool output.** `RunCommand` answers EmptyMessage; DFHack's text
   arrives as `RPC_REPLY_TEXT` frames. Since `drain_replies()` the tools return it - that
   single change converted "sent" into feedback (`autochop status`, `buildingplan status`,
   `quickfort orders --dry-run`, and the game's own rejection reasons).
6. **Deny by default.** Anything that reaches the game by string (commands) needs an
   allowlist, not a blocklist.

## API inventory (measured, this build)

| Module | Functions that matter |
|---|---|
| `dfhack.buildings` (32) | `constructAbstract`, `constructBuilding`, `constructWithItems`, `constructWithFilters`, `completeBuild`, `deconstruct`, `findAtTile`, `findCivzonesAt`, `isActivityZone`, `isPenPasture`, `isPitPond`, **`setOwner`**, `getOwner`, `getName`, `getRoomDescription`, `getStockpileContents`, `setSize`, `getSize`, `getCorrectSize`, `checkFreeTiles`, `countExtentTiles`, `hasSupport`, `isActive`, `markedForRemoval`, `notifyCivzoneModified` |
| `dfhack.military` (5) | **`makeSquad`, `addToSquad`, `removeFromSquad`**, `getSquadName`, `updateRoomAssignments` |
| `dfhack.burrows` (10) | **`setAssignedTile`, `setAssignedUnit`, `clearTiles`, `clearUnits`**, `findByName`, `listBlocks`, `isAssigned*` |
| `dfhack.job` (28) | `addWorker`, `assignToWorkshop`, `attachJobItem`, **`checkBuildingsNow`, `checkDesignationsNow`**, `removeJob`, `removePostings`, `getManagerOrderName`, `getName`, `printJobDetails`, `isSuitableItem/Material`, `listNewlyCreated` |
| `dfhack.maps` (35) | `canWalkBetween`, **`getWalkableGroup`**, `setTileAssignment`, `resetTileAssignment`, `hasTileAssignment`, `getTileType`, `getTileFlags`, `isTileVisible`, `isValidTilePos`, `forEachTile`, `getSize`, aquifer ops, `spawnFlow` |
| `dfhack.units` (139) | `isDwarf`, `isCitizen`, `isPet`, `isAlive`, `isMerchant`, `getCitizens`, `getEffectiveSkill`, `getExperience`, `assignTrainer`, caste/profession name helpers (no `isHostile`, no `getUnitName`) |
| `dfhack.items` (39) | `createItem`, `canTrade(WithContents)`, `canMelt`, `getCapacity`, `getOuterContents`, `findType/Subtype` |
| absent | `dfhack.zone`, `dfhack.dfinfo`, `dfhack.squad`, enum constants in the `lua` env, `map_block.map`, `Item.x/y`, `BuildingInstance.x/y/width/height` |

**Corrections to my earlier drivability map** (`SETUP-LINUX.md` §16), because it judged by
CLI commands instead of the Lua API:
- *Military drafting is drivable* (`dfhack.military.makeSquad/addToSquad`), not mouse-only.
- *Zones are drivable* (civzones are buildings: `constructAbstract` + `notifyCivzoneModified`
  + `isActivityZone/isPenPasture/isPitPond`), not UI-only.
- *Room ownership is drivable* (`setOwner`, `getRoomDescription`) → bedrooms/dining/offices.
- *Job posting/removal and hauling routes are drivable* (`dfhack.job.*`, `maps.setTileAssignment`).
Still mouse-only: the interactive **editors** (`gui/stockpiles`, zone drawing UI, build menu
drags) - which the APIs above make unnecessary for placement.

## Current 37 tools: verdicts

### Keep as-is (14)
| Tool | Why |
|---|---|
| `look` | the primary briefing, ~1,200 tokens; hard-capped |
| `world_map` | zoomed glyph view, ~400 tokens at 40x40 |
| `world_objects` | actionable objects: designations, buildings, item wealth, regions |
| `world_delta` | the change feed - what a loop reads between turns |
| `world_minimap` | vertical profile (`span`, `all_levels` opt-in); cheap |
| `fort_progress` | the Quickstart ladder with keys, reasons, coordinates |
| `tile_at` | one-tile truth (tiletype/class/glyph/water/designation/items + building) |
| `unit_counts` | census from game state |
| `labor_list` | the exact labor names (MINE, not MINING) |
| `job_queue` | the jobs queue - RFR exposes none of it |
| `get_reports` | announcements: the game telling you what happened |
| `tile_legend` | read-once glyph alphabet |
| `send_dig_command` | designation over RPC (with the x/y transposition handled) |
| `apply_blueprint` | quickfort bulk dig/build, repo-sourced + copy-deployed |

### Keep, with changes (10)
| Tool | Change |
|---|---|
| `run_command` | **allowlist** of command names; **deny `lua`, `script`, `luap`** so the raw door is not reachable from the tool list (it is reachable today: `{"command":"lua"}`). Deny destructive (`die`, `teleport`, `exterminate`, `strip*`, `remove-*`, `force*`, `resurrect*`, `importlegends`, `export*`, `modtools/*`) |
| `place_building` | add `owner` (via `setOwner`) + `items` accept item *ids* (resolved with `dfhack.items.findItem`); expose `abstract` for material-free buildings |
| `work_order` | extend to `action: create \| list \| cancel` (read orders via `dfhack.job.getManagerOrderName`, cancel via `__reduce_amount`) |
| `set_unit_labors` | keep; add `profession` convenience (set a labor for all citizens of a profession) |
| `pass_keyboard_event` | keep, documented as hotkeys-only (arrow keys do not move the cursor: 8 event variants tested) |
| `copy_screen` | add `max_chars` (screen text is the menu-reading channel and can be noisy) |
| `list_units` | trim to id/name/profession/species/age/squad - it is the id source for labors |
| `get_pause_state` / `set_pause_state` | keep; the loop needs it, and pausing during play is intrusive, so the docstring says "ask before unpausing" |
| `world_cache_reset` | keep; document that it also calls `ResetMapHashes` |
| `get_status` | absorb `get_versions`, `get_world_info`, `get_map_info`, `get_view_info` into one `server_status` with `detail: brief \| full` |

### Drop from the MCP tool list, keep as HTTP (7)
| Tool | Reason |
|---|---|
| `get_block_list` | 196,361 tokens for one z-level - a footgun for a local model. HTTP/debug only |
| `world_raw` | writes a file dump; operator tool, not a model tool |
| `get_versions` | folded into `server_status` |
| `get_world_info` | folded into `server_status` |
| `get_map_info` | folded into `server_status` |
| `get_view_info` | folded into `server_status` (cursor/view state) |
| `get_unit_list` | raw unit list, huge; `list_units` + `unit_counts` cover play |
| `game_lua` | already excluded; stays HTTP debug |
| `world_zone` | subsumed by `look`'s per-level band table - delete, don't move |
| `building_at` | merged into `tile_at` (`at_tile` field); HTTP alias fine |

Target after this pass: **~24 tools** in the MCP surface.

## Missing tools, ranked by ladder rungs unblocked

### P0 - unblocks the mouse-only cluster (6 of the 21 rungs)
1. **`create_zone`** - `kind: meeting \| pasture \| pit \| pond \| dump \| watersource`, rectangle.
   `constructAbstract` civzone + type field + `notifyCivzoneModified`; verify with
   `findCivzonesAt`. Unblocks: meeting area, pasture, refuse pile, garbage dump.
2. **`create_stockpile` + `configure_stockpile` + `stockpile_contents`** - `constructAbstract`
   (verified live: a 5x5 stockpile at 102,10 elev 37 read back as `building_stockpilest`
   with `findAtTile`), then settings via the building's own fields (bins/barrels, accept
   categories, take/give, max), read via `getStockpileContents`. Unblocks: stockpile, and
   makes every "why did nothing get built" question answerable.
3. **`assign_room`** - `owner: unit_id`, `kind: bedroom \| dining \| office`; `setOwner` +
   `getRoomDescription`, and `place_building` for the furniture with `items`. Unblocks:
   bedrooms, dining, offices.
4. **`squad`** - `action: list \| create \| draft \| undraft`; `dfhack.military.makeSquad`,
   `addToSquad`, `removeFromSquad`, `updateRoomAssignments`. Unblocks: military.
5. **`burrow`** - `action: list \| create \| set_tiles \| clear_tiles \| assign_unit`;
   `dfhack.burrows.*`. Feeds pasture/autochop restrictions and hauling limits.
6. **`complete_designations`** - `action: dig \| build` via `dfhack.job.checkDesignationsNow` /
   `checkBuildingsNow` (typed replacement for `run_command dig-now/build-now`, with bounds
   so it cannot touch the whole map).

### P1 - management and cause-finding
7. **`job_postings`** - `action: list \| add_worker \| assign_workshop \| remove`;
   `dfhack.job.addWorker/assignToWorkshop/removeJob/removePostings`.
8. **`hauling_routes`** - `action: set \| clear \| list`; `maps.setTileAssignment` /
   `hasTileAssignment` / `resetTileAssignment`.
9. **`path_report`** - `from: [x,y,elev]`, `require: wagon_path`; combines
   `canWalkBetween` with a width check computed from the map (a 3-wide route to the map
   edge with no stairs/traps), i.e. the real depot rule. Today `fort_progress` claims the
   rung from *presence* only, which is the honest gap I documented.
10. **`fort_facts`** - expose `FortFacts` (food/drink/wood/stone/metal/barrels/miners)
    without the ladder wrapper.
11. **`tool_switch`** - `tool: autochop \| suspendmanager \| autobutcher \| buildingplan \|
    repeat \| autochop.clearcut`, `action: status \| enable \| disable \| set`. Typed
    allowlist over `run_command`, returns the tool's own status text (614 chars of
    `autochop status` proved the channel).
12. **`warnings`** - stranded units / unreachable jobs / blocked construction:
    `warn-stranded` + `suspendmanager` + `job_queue` combined into one report.

### P2 - depth, lower priority
13. `locate_ore` (`locate-ore` script) - where the metal is before you commit a shaft.
14. `caravan` (`caravan_status`, `caravan_accept`) - trade readiness; `items.canTradeWithContents`.
15. `fluid` (`liquids` script) - well/bucket/plumbing; the ladder's water rungs.
16. `population` (`migrants_now`, `embark-*`) - **not** for play; operator/debug only.
17. `save_snapshot` (`quicksave`) - operator HTTP only, never a model tool.

## Deliberately not building
- **Raw Lua as a tool.** Typed wrappers give the same reach with a reviewable surface.
- **A generic "query game state" tool.** Free-form queries make the model write Lua.
- **More token escape hatches.** `world_raw`/`get_block_list` are enough for the operator.
- **GUI automation** (`gui/*` tools, mouse emulation). No mouse RPC exists; cursor keys do
  not move the cursor (8 event variants tested). The APIs above make the UI unnecessary.

## Phasing

| Phase | Content | Result |
|---|---|---|
| **1. Hygiene + safety** | drop the 7 raw reads from the tool list, merge diagnostics into `server_status`, allowlist `run_command` (deny `lua`/`script`/destructive), `max_chars` on `copy_screen` | ~24 tools, raw door closed, token footguns gone |
| **2. Ladder P0** | `create_zone`, `create/configure/contents` for stockpiles, `assign_room`, `squad`, `burrow`, `complete_designations` | the 6 mouse-only rungs become executable by the model |
| **3. Management P1** | `job_postings`, `hauling_routes`, `path_report`, `fort_facts`, `tool_switch`, `warnings` | the model can answer "why is nothing happening" |
| **4. Depth P2** | `locate_ore`, `caravan`, `fluid` | longer-game planning |
| **5. Retrieval (optional)** | facts/items indexed for long-tail questions (sqlite-vec + an embedder, as chosen earlier) | only after 1-4; aggregation, not RAG, is what fixed perception |

## Verification bar for each new tool
1. Composed template has `OK `/`FAIL ` status lines, parsed by prefix (the traceback bug).
2. Idioms verified live, not assumed (the 6 signature misses this session: `MatPair.mat_type`,
   `Item.pos`, `BuildingInstance.pos_*_min/max`, `TileClass` fields, coord tables for
   `canWalkBetween`, item objects for `constructWithItems`).
3. Self-verifies through the acting channel (`findAtTile`), not through RFR.
4. Offline unit test for template composition + a live test gated on RPC **readiness**
   (port-up is not ready: RPC listens before the world loads and answers `CR_FAILURE`).
5. Reply carries the game's own rejection text when it fails.

---

# Addendum: alerts and events (the missing perception channel)

Raised by the player: *the game has an alert system, and events like a caravan arriving or a
monster walking into the fort must be known.* Agreed, and it is a **planning gap in this
document** - my `look`/`get_reports` pair is not that channel.

## Why `get_reports` is not the alert system

`world.status.reports` is the scrolling announcement log: prose lines, no timers, no
forecasts, no severity, and it overwrites. It tells you a caravan arrived; it does not tell
you a caravan is 40 tiles out, that a migrant wave of 12 lands in 3 days, that an attack
wave of 7 goblins is scheduled for autumn, or that a Megabeast is inside the fort and the
dwarves are panicking. Those are **state**, not text - and the alert icons in the UI are
computed from state. So the tool must compute from state too.

## Measured: where the event system lives (53.17-r1, live probes)

| Structure | Contents |
|---|---|
| `world.armies.all` (**532** entries) | every army in the world: `flags` (caravan / ambassador / invader / destroyed / visited-map ...), `race`, `creature_class`, `pos`, `path_x/path_y`, `last_pos`, `travel_count`, `travel_rate`, `watch_time`, `wait_count`, `items` (caravan goods), `members`, `squads`, `controller_id`, `id`, `mat_type/mat_index` (artifact), `tracking_rating`, `sneak_rating`, `block_path_x/y` |
| `world.army_controllers.all` | the wave scheduler: `flag`, `goal`, `number_pop` (wave size), `year`, `year_tick` (arrival time), `pos_x/pos_y`, `site_id`, `subregion_id`, `entity_id`, `commander_hf`, `mission_report`, `assigned_squads`, `activity_id`, `data` |
| `world.attack_chance_info` | the Attack Chances panel data (count 0 on a fresh embark) |
| `world.event` | the current event (attacker / type) |
| `world.status` (21 fields) | `announcement_alert`, `alert_button_announcement_id`, **`popups`** (modal dialogs open - caravan trade, ambassador, attack confirm), `reports`, `announcements`, `flags`, `slots`, `display_timer`, `interrogation_reports`, `mission_reports`, `spoils_reports` |
| `world.squads.all` (**82** world-wide) | squads, rosters, drill/room assignments - military readiness |
| `world.manager_orders` | work orders (read side; 0 now) |
| `world.incidents` / `crimes` | deaths, injuries, crimes - the legal/morale events |
| `list-waves` script | readable wave report computed from **history figures** (`hfdata.year`, `hfdata.seconds`, `hfdata.petitioned`) -> "Wave 0 consisted of 7 unit(s) and arrived in the spring of year 100", with names and professions; migrants vs petitioners separated |
| unit wellbeing | DF53 has **no `unit.status.stress`**. Stress lives in **`unit.status.misc_traits`**; `counters` = pain, dizziness, nausea, winded, stunned, unconscious, suffocation, `soldier_mood(_countdown)`; `counters2` = exhaustion, hunger_timer, thirst_timer, sleepiness_timer, numbness, paralysis, stored_fat, fever; strangemood via `unit.mood`/`moodstage`; curses via `curse_year/curse_time`; were-creature via the `uwss_*` field set; syndromes via `unit.syndromes`; the dwarf's own thought log via `unit.reports` (0 on a fresh dwarf, fills during play) |
| unit flags | enumerated live per unit: `flags1/2/3/4` (bit names readable when set: `move_state`, `calculated_nerves`, `vision_good`, ...) - `panicking` to be confirmed by name |

**Not yet decoded (explicit TODOs, with the probe that will settle each):**
1. `alert_entry` field names - `pairs()` on `world.status.announcement_alert` enumerates nothing,
   `df.new(df.alert_entry)` is not constructible. Path: build the alert set from the state
   predicates below (which is what the UI does), and keep `status.announcement_alert` as an
   extra signal once its fields are found via `devel/query` on the live struct.
2. stress misctrait index (find the "stress" `mat_index` in `unit.status.misc_traits` and its
   flags/timer), the `panicking` flag name, and `popups` screen-type decoding.

## Tools this implies

### `alerts` - P0, the time-critical channel
```
GET /alerts  ->  {
  date: "year 100, Spring, day 4 ~08:00",
  alerts: [ {kind: "caravan_incoming", severity: "opportunity",
             detail: "1 caravan, 40 tiles NE, path marked, 3 goods wagons",
             suggested: "clear a 3-wide depot path; check wagon access",
             source: "armies.all[17] flags=TRADING pos=(...)"},
             {kind: "monster_in_fort", severity: "critical", ...},
             {kind: "migrant_wave", severity: "info", detail: "12 in 3 days", ...},
             {kind: "popup_open", severity: "blocking", detail: "caravan trade dialog"} ],
  new_since: "<cursor>"          # delta cursor, same pattern as world_delta
}
```
Computed from: `armies.all` (flags + position relative to the fort + distance + goods),
`army_controllers.all` (wave ETA from `year`/`year_tick` + `number_pop`),
`attack_chance_info`, `world.event`, `popups`, wild/hostile units **inside** a fort walkable
region (the "monster walked in" case), strangemood units (`unit.mood`), incidents, and the
announcement stream as a text supplement.

`severity` is a first-class field (critical/opportunity/info/blocking) with a `suggested`
action string, because the point of an alert is a decision, not a fact.

### `wellbeing` - P0, the "turns all the dwarfs" case
```
GET /wellbeing -> { panic: N, stressed: N (worst: name, stress value), moods: [...],
                    syndromes/afflictions: [...], wounded: [...], starving/thirsty/exhausted: N,
                    morale_summary: "...", source_units: [...] }
```
From `status.misc_traits` stress, `counters`/`counters2`, `mood/moodstage`, `syndromes`,
`uwss_*`, `curse_year`, `health`, `unit.reports`. This is what makes a monster in the fort
legible: not "a goblin exists" but "6 of 7 dwarves are panicking, 2 are sobbing".

### `waves` - P1, forecasts
`list-waves` (migrants/petitioners by wave with names/professions) + `army_controllers`
(attack waves with ETA/site/pop). Feeds `fort_progress` so the ladder can say
"pop 7, next wave +12 in 3 days -> you need the farm plot NOW".

### `caravan` - P2, the trade interaction
From `armies.all` with the trading flag: `pos`, distance, goods (`items` with values via
`dfhack.items.getItemBaseValue`), merchants present, depot reachability via `path_report`,
and the `popups` state (a trade dialog is modal - a loop that ignores it stalls).

## Loop consequences (why this changes the design, not just adds a tool)

1. **Alerts are the clock.** A play loop must poll `alerts` on every turn, not just
   `world_delta`: terrain changes slowly, events do not.
2. **`popups` can freeze the interface** - modal dialogs (caravan, ambassador, "attack!")
   need `pass_keyboard_event`/dismissal, so the loop needs an explicit "is the UI blocked"
   answer.
3. **Severity, not facts.** A 200M-parameter model needs "critical + suggested action",
   which is why the tool returns decisions-shaped records, matching `fort_progress`'s
   rungs+keys pattern.
4. **New transport fact this session:** `RPC_REPLY_TEXT` fragments carry **bytes** for DF's
   CP1252 output (quoted nicknames like `"Cryptdrove"`, box characters). A plain
   `"".join(fragments)` raised `TypeError: expected str instance, bytes found` and 500'd
   the request - reproduced with `list-waves`. Fixed by `decode_fragments()` (decode per
   fragment with `replace`) in `vendor/dfhack-client-python`, mirrored in the sibling clone.

---

# Field notes from the "dig it like a player" test

Test asked: 3-wide tunnel west ~15 into the mountain, 6x6 chamber, place a trade depot,
**no blueprints, no dig-now** - the dwarves do the labor. (Agreed on the principle:
`dig-now` is a cheat command, not play. The play verbs are designate + labor + time.)

## Result

- Designations sent as `[x, y, elev+128]` **land at x,y** - no transposition. The old swap
  in `send_dig_command` sent an 87-tile tunnel to mirrored coordinates, where unexplored
  tiles rejected it silently. Fixed (default no-swap, `swap:true` opt-in).
- With 4 dwarves on `MINE` (set via `set_unit_labors`, i.e. the `y` menu) and the game
  unpaused, a 6-tile wall designation at x84-89,y160,elev39 was **fully dug in ~30 s**:
  6 -> 5 -> 3 -> 0, read straight from `world_objects` dig clusters.
- My first site was wrong: I read `.` (grass floor) and ` ` (open) as "the mountain".
  Soil floors ARE diggable, so the dwarves dutifully removed ~52 tiles of meadow.
  Diggable is not the same as rock, and the layer's glyph legend says so - I did not use it.

## Two facts for the tool plan

1. **`job_queue` is not a progress signal for digging.** `df.global.world.jobs.list` stayed
   **0** across the whole test while mining happened. In 53.17-r1, map blocks expose no job
   container (`pairs()` on a block lists 24 fields, none job-related), and mining is
   designation-driven labor. The progress channel is the **designation count trend**
   (`world_objects`/`look` dig clusters, `world_delta`), not the queue.
   -> Plan: `job_queue` keeps a documented scope ("manager postings / linked jobs"), and
   progress reporting must read designations.
2. **Explored rock is a shell here, not a massif.** Scans over the fort band found:
   - longest explored westward solid run: **13** tiles (elev 43, y168, x33-45);
   - at the wagon's elevation (39): a **10 x 1** solid band (x84-93, y160) with
     floors/ramps east of it, and **nothing explored below y160** in x84-92;
   - **no 6x6 fully-solid explored box** with an explored entrance anywhere in the band.
   The wagon is a building (`building_wagonst`, id 0) at x65-67, y116-118, elev 39 - and
   `RFR` did not list it in `MapBlock.buildings` while `findAtTile` returned it, again.
   -> Plan: a `survey`-style op that reports, for a requested rectangle, how many tiles the
   game would accept as diggable (explored + rock), BEFORE designating. That is the
   difference between ordering work and ordering a no-op.
