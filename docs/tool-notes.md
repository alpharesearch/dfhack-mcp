# DFHack MCP Tool Notes & HTTP-Only Diagnostic Reference

This document preserves historical playtest findings, DF 53.17-r1 engine quirks, crash postmortems, and the HTTP-only diagnostic/operator endpoint catalog that were previously embedded in `main.py` MCP tool docstrings. Keeping these notes here rather than in `FastApiMCP` tool schemas keeps the LLM tool surface focused and compact.

**Measured 2026-11-24 pass**: sections 6-13 record this session's measured tool behaviour, each line
carrying a `Measured 2026-11-24:` marker plus a `tool_missing.md` row number or a `git log` commit.
Sections 1-5 text is unchanged.

---

## 1. Digging & Fog Workflow

- **Elevation-to-Z offset**: All map reads and `send_dig_command` box/shaft helpers convert in-game elevation using `z = elevation + 129` (`WM.GAME_Z_OFFSET`).
- **FOG WORKFLOW (verified live on DF 53.17-r1)**: Designating into unexplored fog (`hidden=True`, `?` glyph) **WORKS** and is the intended workflow for extending tunnels, rooms, and stair shafts into the mountain.
  - Pre-flight with `fort_strata` (aquifers/water/magma/soil) and `survey` (do not designate over existing floors/buildings).
  - Note: `tile_at` does not show `dig_designation` on hidden tiles; verify fog orders via `look` / `world_map` or HTTP `GET /world-objects`, and verify completion by positive tile state (`dug > 0 and to_dig == 0` in `survey`), never by designation count dropping to 0.
- **Middle-first ordering**: `send_dig_command` orders box centers before flanks (`middle_first=True`) so miners carve access into fog without stranding tiles.
- **Stair shafts**: `stair_shaft` automatically designates `DOWN_STAIR_DIG` at the top elevation, `UP_DOWN_STAIR_DIG` on intermediate elevations, and `UP_STAIR_DIG` at the bottom elevation.

---

## 2. Trees & Water

- **Tree felling (`find_trees`, `cut_trees`)**:
  - Measured 2026-11-21: As in-game UI selection targets the tree trunk, `cut_trees` designates `DEFAULT_DIG` only on `tree_trunk` (`TreeTrunk`) tiles.
  - Saplings (`sapling`) are counted separately and are *not* felled (clearing a sapling is the "clear vegetation" order, not tree felling).
  - `wood_after` in `cut_trees` reflects the moment of reading; hauling and chopping take in-game time.
- **Water & Wells (`find_water`)**:
  - DF wiki rule: a well is 1x1 on a floor with a clear vertical line to water `>= 3/7` deep, and does not work through a staircase.
  - `find_water` scans map blocks and clusters water tiles per cell with depth range and elevation.

---

## 3. Buildings, Stockpiles & Zones

- **`place_building`**:
  - Multi-tile stockpiles must use `create_stockpile` (`allocInstance -> setSize -> constructAbstract`) rather than `constructBuilding` (which ignores `w/h` and produces a 1x1 pile).
  - Multi-tile workshops route through `constructBuilding` with `width`/`height` + `room.extents` (`constructAbstract()` rejects workshops with `expected !bld->isActual()`).
  - `constructWithItems` rejects an empty item list, and RemoteFortressReader item IDs do not resolve directly—`material="log"|"boulder"|"block"` with `count=N` or crafted furniture lookup resolves real `df.item` objects.
  - Pre-flight site gate rejects footprints containing rock, fog, open air, or water.
- **`create_stockpile` & `configure_stockpile`**:
  - **Mid-air stockpile postmortem (measured 2026-11-21)**: Placing a stockpile over `open` cavern air, unexcavated rock, or fog is accepted by DF and leaves the pile floating in mid-air. The ground site gate reads the footprint first and refuses if any tile is not a dug, walkable floor.
  - **DF 53.x stockpile category flags**: `ammo`, `animals`, `armor`, `bars_blocks`, `cloth`, `coins`, `corpses`, `finished_goods`, `food`, `furniture`, `gems`, `leather`, `misc`, `refuse`, `sheet`, `stone`, `weapons`, `wood`. (Note: `plants` is not a top-level category flag; plants live under `food`.)
  - **General pile**: `all_categories=True` with `deny=["stone", "wood"]`.
  - **Custom-menu `pile_arrays` crash**: Enabling category flags over empty material arrays can crash when opening Custom settings in-game; `configure_stockpile` initializes the underlying arrays via Lua (`POST /pile-arrays` inspects/repairs them over HTTP).
- **`create_zone` & `remove_zone`**:
  - **Zone allocation rule**: Zones are abstract buildings created via `allocInstance(30, subtype=<kind>) -> setSize -> constructAbstract` (never post-construction `z.type = ...` or `notifyCivzoneModified`, which crashed DF in v1/v2 tests).
  - **`remove_zone` crash postmortem (measured 2026-11-21 22:53:41, `crash_2026-11-21-22-53-41.txt`)**: Zone removal via RPC previously caused a SIGSEGV at address 0 inside `Buildings::notifyCivzoneModified`. The current Lua path performs `setOwner(b, nil)`, cleans `assigned_units` and `general_refs` civzone entries in `units.active`, and calls `deconstruct`, and is gated behind `acknowledge_risk=True` with live zone-list read-back verification. Prefer in-game zone removal (`z -> remove`) when practical on a prized save.

---

## 4. Work Details & Labors

- **DF 53.x Labor Menu (`work_details`)**:
  - Measured 2026-11-22: Work details live at `df.global.plotinfo.labor_info.work_details`, each with `name`, `allowed_labors`, `flags.mode` (`1` = `EverybodyDoesThis`, `2` = `NobodyDoesThis`, `3` = `OnlySelectedDoesThis`), and `assigned_units` (a sorted vector of unit IDs).
  - After every mutation (`assign`, `set-mode`, `set-labor`, `create`, `delete`), `work_details` keeps `assigned_units` sorted, switches `flags.mode` to `OnlySelectedDoesThis` (`3`) when adding a unit, and runs `dfhack.units.setAutomaticProfessions(u)` across all citizens so `unit.status.labors` updates immediately even while paused.

---

## 5. HTTP-Only Diagnostic & Operator Endpoints

All 56 endpoints below remain live on the FastAPI HTTP server for `df-observer`, operator scripts, and unit tests, but are excluded from `FastApiMCP` to keep the LLM tool surface focused (~32 tools):

### Vertical / Geology / Reachability Micro-Probes (13)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `overburden` | `GET /overburden` | Non-air levels above `(x, y, elev)` |
| `column` | `GET /column` | Single `(x, y)` tile column top-to-bottom |
| `cross_section` | `GET /cross-section` | Vertical slice across an x/y span |
| `depth_profile` | `GET /depth-profile` | Depth under local surface for a rectangle |
| `magma_depth` | `GET /magma-depth` | Distance down to magma from a dig point |
| `probe_plan` | `GET /probe-plan` | Rank elevations in one column for a probe dig |
| `tunnel_sites` | `GET /tunnel-sites` | Find solid-rock tunnel lines at depth >= 2 |
| `find_soil` | `GET /find-soil` | Soil-floor cluster census (superseded by `fort_strata`) |
| `hazards` | `GET /hazards` | DAMP/HOT check for a dig rectangle |
| `warnings` | `GET /warnings` | Designation-time DAMP/HOT warnings across current digs |
| `reach_map` | `GET /reach-map` | 3D walk-component summary |
| `dig_reachable` | `GET /dig-reachable` | Connectivity check to a target dig tile |
| `walkable_between` | `GET /walkable-between` | `dfhack.maps.canWalkBetween` check between two tiles |

### Legacy / Raw / Debug / UI-Hack Endpoints (11)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `apply_blueprint` | `POST /apply-blueprint` | Write & apply quickfort CSV blueprint |
| `copy_screen` | `GET /copy-screen` | Capture current game screen ASCII text |
| `pass_keyboard_event` | `POST /pass-keyboard-event` | Inject raw SDL1.2 keyboard event |
| `reconnect` | `POST /reconnect` | Drop and reopen DFHack RPC connection |
| `world_cache_reset` | `GET /world-cache-reset` | Drop WorldModel block/decoder caches |
| `world_delta` | `GET /world-delta` | Diff since last world read |
| `world_minimap` | `GET /world-minimap` | 1-char-per-16x16-block map overview |
| `world_objects` | `GET /world-objects` | Dig designations, buildings, and item clusters |
| `transitions` | `GET /transitions` | SQLite TransitionStore excavated tile counts |
| `pile_arrays` | `POST /pile-arrays` | Inspect/repair stockpile Custom-menu arrays |
| `wagon_path` | `GET /wagon-path` | BFS check for 3-wide wagon path to map edge |

### Redundant Read / Lookup Endpoints (16)
| Operation ID | Method & Path | Purpose |
|---|---|---|
| `get_status` | `GET /get-status` | RPC connection, versions, and save identity |
| `get_pause_state` | `GET /get-pause-state` | Current pause boolean |
| `get_reports` | `GET /get-reports` | Recent in-game announcements/reports |
| `tile_at` | `GET /tile-at` | Detailed single-tile inspection |
| `tile_legend` | `GET /tile-legend` | 29-glyph tiletype legend |
| `list_buildings` | `GET /list-buildings` | RPC block building census |
| `buildings_live` | `GET /buildings-live` | Full `df.global.world.buildings.all` census |
| `get_building_def_list` | `GET /building-def-list` | Buildable definition names |
| `labor_list` | `GET /labor-list` | Valid labor enum names |
| `unit_counts` | `GET /unit-counts` | Population counts by category |
| `list_units` | `POST /list-units` | Detailed unit records by ID |
| `unit_labors` | `GET /unit-labors` | Effective citizen `status.labors` |
| `unit_professions` | `GET /unit-professions` | Citizen professions & labor ticks |
| `unit_activity` | `GET /unit-activity` | Current position, move target, and jobs per dwarf |
| `job_queue` | `GET /job-queue` | Active fortress job queue |
| `work_order_link` | `GET /work-order-link` | Link a workshop to a manager work order |

### Previously Excluded Core/Observer/Debug Endpoints (16)
`game_lua`, `observe`, `observe_stats`, `game_paths`, `get_block_list`, `world_raw`, `get_versions`, `get_world_info`, `get_map_info`, `get_view_info`, `get_unit_list`, `world_zone`, `building_at`, `make_stockpile`, `resize_building`, `repair_stockpile_settings`.

---

## 6. Buildings & Def Names

- Measured 2026-11-24: def-name spelling is literal. Accepted names proven live: `Mechanic` →
  Mechanic's Workshop 49 (commit `013ae54`), `Farmer's Workshop` 16, `Wood Furnace` 18,
  `Metalsmith's Forge` 19, `Smelter` 17, `Craftsdwarf's Workshop` 8, `Trade Depot` - the compact
  forms `WoodFurnace`, `MetalsmithsForge`, `FarmersWorkshop`, `TradeDepot` are rejected (#74, #82).
- Measured 2026-11-24: multi-tile shops need the plan route with `nominal=false` and explicit
  `w`/`h` - `nominal=true` snaps a 1x1 workshop request to the canonical 3x3 footprint (#57).
  A 3x3/3x4 shop is built that way (Still 11, Mechanic's 49, Mason 7 / Craftsdwarf 8 plans: #70, #68).
- Measured 2026-11-24: the site gate prints a measured dict and can explain a refusal
  (`{'rock': 3}` at `x70-72,109-111@36`, `{'open': 8}` at `66,119`), while the construct path
  refuses with bare text - `constructBuilding refused: cannot place at this position` /
  `FAIL construct: nil reason=could not construct the building` (#80, #82).
- Measured 2026-11-24: furniture is placed from **crafted items** via
  `material="chair"|"table"|"door"|"bed"|"cabinet"` (Chair 28, Table 29, Door 30, Bed 32/33 - #80);
- Measured 2026-11-24: on DF 50 furniture are building objects, not items - the census is
  `df.global.world.buildings.other.CHAIR/BED/TABLE/...` (commit `26b955b`).
- Measured 2026-11-24: a `complete: true` furniture placement is not durable - one of two placed
  beds vanished from the census (`buildings.BED: 1`, `items.BED: 6`) (#81). Verify furniture by
  `workshop_job(action="stock")` / the building census, never by the write reply.
- Measured 2026-11-24: `get_building_def_list` is HTTP-only (listed in §5), so def names must be
  discovered by trial; the resolver's rejection text points at a tool the agent cannot call (#74).

---

## 7. Zones & Rooms

- Measured 2026-11-24: `create_zone` requires `acknowledge_risk=True`, and an identical active
  footprint answers `already_exists: true` with the existing zone id instead of creating a
  footprint answers `already_exists: true` with the existing zone id instead of creating a
  duplicate (code anchor: `main.py`, the idempotency check whose own comment cites the closed
  zone-cross-talk entry in `tool_missing.md`).
- Measured 2026-11-24: `create_zone` reports no walls/neck/door/enclosure state and accepts a
  room-typed zone stacked inside another owner's zone - offices 34/36 sat inside bedroom 31, three
  owned rooms on one open floor (#85).
- Measured 2026-11-24: room-typed zones must sit in a walkable region, keep >=1 unexcavated
  column/row as separator from other dug rooms, and have a flat 1-wide neck with rock flanks for a
  door. A neck facing a level drop digs as **ramps** and can never be door'ed - Broker's neck came
  out `/` at `70,112`/`71,112`, while Manager (`60,112`) and Bookkeeper (`63,112`) door'ed fine
  (#86; lessons §2, §16, §18).

---

## 8. Dig Designations & Tile State

- Measured 2026-11-24: `DEFAULT_DIG` on a box with `dug > 0` **destroys existing tile state** -
  observer A89 `tile_state_lost`: box `x64-72,112-120@36` went `dug 39 -> 0`, floors and ramps gone,
  whole box rock again, after a write that reported `skipped non_rock 39` (#92). Partial boxes are
  designated per-tile from a fresh rock-only survey, never box-wide.
- Measured 2026-11-24: designations on faces with no walkable route - and in the aquifer band,
  where damp auto-cancels - are cancelled silently: writes answering `tiles: 10/12/9 success` came
  back `designated 0` with no cancel reason in any read (#83; the designation-type read is #69).
- Measured 2026-11-24: a designation reading 0 on one poll can stick and be dug later
  (`x35-39,116-118@40` -> `to_dig 0, dug 15`), so never conclude a mechanism from a single survey
  (lessons §22 correction, commit `6f5ec3a`).
- Measured 2026-11-24: `survey` counts fog as `hidden` - a pure-fog box answers `{"hidden": 49}`
  with **no `rock` key at all**, which is why plugin stall gates must treat an absent key as fog (#47).
- Measured 2026-11-24: `usable_tiles: 0` / `holes: N` on a non-empty footprint is labelled
  UNMEASURED by the tool (`luaops.py`: "countExtentTiles returned 0 ... UNMEASURED (not 0)") -
  never read as "no space" (lessons §12).

---

## 9. Workshop Jobs & Manager Orders

- Measured 2026-11-24: direct shop queues (`workshop_job(action="queue")`) need **no Manager and no
  office** - 7 beds, 6 bins, 3 chairs, tables, doors, buckets, mechanisms and crafts were built
  that way; `repeat_job: true` sets the job's repeat flag for continuous production
  (`deployables/scripts/dfhack_mcp.lua`).
- Measured 2026-11-24: `workshop_job(task="wheelbarrow")` queues **"Make wooden minecart"** jobs
  (ids 241-243) even though the live task table declares
  `tool_id = "ITEM_TOOL_WHEELBARROW"` (`dfhack_mcp.lua:2495`, added in `c96e1fe`) - the declared
  subtype does not survive measurement, and the stone pile's `max_wheelbarrows: 3` rung stays
  unsatisfied (#71).
- Measured 2026-11-24: the real wheelbarrow is reachable only via
  `work_order(action="create", job="MakeTool", item_subtype="ITEM_TOOL_WHEELBARROW")` - order 2
  described "Make wooden wheelbarrow" at the Carpenter - which needs a **validated** Manager (#84).
- Measured 2026-11-24: task->workshop gating is strict in the live map: `chair`/`table`/`door`/
  `cabinet`/`chest`/`coffin` = Carpenters + Masons, `bed`/`barrel`/`bin`/`bucket`/`wheelbarrow` =
  Carpenters only, `pot`/`crafts` = Craftsdwarfs, `mechanisms` = Mechanic's shop,
  `brew` = `CustomReaction` `BREW_DRINK_FROM_PLANT` at the Still, `meal` = Kitchen. Absent from the
  map entirely: `cloth`, `thread`, `charcoal`, `splint`, `crutch`, `bag`, `rope` (#79) and
  `weapon_rack`/`armor_stand`/`training_weapon` (#73).
- Measured 2026-11-24: orders created through `work_order` read `validated: false, active: false`
  until a Manager exists and material resolution lands - the game's validation is not reachable
  from the tool (#50, #84).

---

## 10. Trade & Barter

- Measured 2026-11-24: `accessible` is a boolean with no explanation - it stayed `false` on every
  read with a caravan parked `AtDepot` (`complete: true, build_stage 3/3`, goods listed,
  `time_remaining: 2396`) and after a surface depot + fully dug 3-wide route (#51, #82; the
  `wagon_path` verdict endpoint is HTTP-only, §5).
- Measured 2026-11-24: `trade_depot(action="mark_goods")` filters match the item **description**
  substring: `item_filter="gem"` matches nothing, `"rough"` works - rough gems are type `ROUGH`
  with descriptions like "rough carnelians" (commit `83eb1e8`: carnelians/tigereyes/aventurine and
  a silk bag/wheelbarrow marked, `pending_haul 12`).
- Measured 2026-11-24: the trade screen opens, but both goods lists fill only when a fort trader
  and a merchant are **talking at the depot**; `open` is a boolean, not a function, and a screen
  opened without a talker shows no merchant at all (#18).
- Measured 2026-11-24: `anyone_can_trade: true` removes the need for a trade-qualified Broker -
  measured via `trade_depot(action="request_broker")` read-back (#20, #52).
- Measured 2026-11-24: `barter_status` / `barter_select` work (`selected: true` + `toggled`
  read-back, `selected_value: 20`), but **no tool presses Trade/Confirm**, and the merchant list is
  capped at 40 items with no filter, so "buy food" is only answerable for what the list shows (#91).

---

## 11. Pause & Pause Authority

- Measured 2026-11-24: `set_pause_state(paused=false)` is overridden while a player UI screen
  (e.g. the Nobles/appointments menu) holds the pause - the reply carries `requested` and names
  the mechanism when the game's state differs (`paused: true, requested: false`; code anchor
  `out = {"paused": ..., "requested": want}` at `main.py:944`).
- Measured 2026-11-24: do not re-issue the write; the resume is the player's action from their
  screen (lessons §14).

---

## 12. Nobles, Rooms & Stock Scope

- Measured 2026-11-24: a noble seat needs a **satisfied room** - enclosure + furniture + door +
  no overlap with another owner's zone - not a zone id: offices 34/36 inside bedroom 31 were junk
  rooms (#85, #86).
- Measured 2026-11-24: the Bookkeeper is what makes the game's stock records mean anything:
  `bookkeeper_settings: 4` plus the `UpdateStockpileRecords` job with a satisfied office (Minkot,
  `RECORD_KEEPING 5`, zone 44) (#88; lessons §19).
- Measured 2026-11-24: every stock number in the reads is **pile-scoped** and disagrees with
  item-scan counts by design - `fort_progress` `food 17, drink 0, wood 63, stone 68` vs
  `workshop_job stock` `FOOD: 0` in the same period. Quote the scope with the number; no tool
  reads the game's own fort-wide statistics (#88).
- Measured 2026-11-24: appointments are ranked from `current_soul.skills` for the seat's required
  skills only, and a position exposing no aptitude list is stamped
  `legibility: UNVERIFIED` - the trade qualification the BROKER demands is not readable from any
  accessor on this build (#20, #52, #65).

---

## 13. Verification Rules

- Measured 2026-11-24: a `success: true` flag is not a fact - `applied: N` validated names against
  the labor list, not the game (commit `614d631`), and `prioritize` reported its list back after
  "Ignoring unknown job type" as if nothing failed (#76); `mark_goods`/`request_broker` answered
  success and changed no depot field (#51).
- Measured 2026-11-24: verify removals, placements and digs from live lists - absence from
  `list_zones` for zone removals (#61), the pile list for category acceptance (the
  `known_keys`/read-back contract, `main.py:2981`; refusal text quoted live in #89), per-box `dug`
  movement in `survey` for digs (#46), `workshop_job stock` / the building census for furniture
  (#81).
- Measured 2026-11-24: NO DATA is labelled, never read as zero - `blocks_scanned: 0`, an empty
  walk graph, `total_jobs: 0` and empty item scans are read failures (#30, #34, #46).
- Measured 2026-11-24: after a save swap the observer drops its derived state
  (`save_reloaded` / `resetForNewGame`, `plugin/df-observer/model.js`) and watched boxes must be
  re-armed; seed dedupe keys on the acceptance signature, so the stored ledger has to be reset for
  a new seed kind to land (commits `bc32f46`, `a7e354c`).
- Measured 2026-11-24: MCP-surface caveat - every endpoint listed in §5 is HTTP-only, so "the
  capability exists" is not "the agent can call it": `get_building_def_list` closed gap #2 and the
  32-tool pruning re-opened it as #74 (#2, #74; main.py `exclude_operations`).
